/** MICO360 Connect client (OpenAI-compatible surface), following
 *  MICO360 Connect/docs/API-Guide.md 2.6.7 exactly — see spec §5.1. */
import data from "./data/desktop.json";
import { CancelledError, type ProgressFn, REDUCE_NOTES_BUDGET, runMinutesPipeline } from "./pipeline";
import { randomHex } from "./random";

export const CONNECT_BASE_URL: string = data.connect.baseUrl;
export const CONNECT_HTTPS_CANDIDATES: string[] = data.connect.httpsCandidates;
export const CONNECT_DEFAULT_MODEL: string = data.connect.defaultModel;

export const MAX_PROMPT_CHARS = 32_000;            // §5/§11: the conversation as a whole
export const PROMPT_OVERHEAD = 1_500;
export const CHAT_TIMEOUT_MS = 330_000;            // §7: the server waits 300 s, then 504
export const STATUS_TIMEOUT_MS = 4_000;

export interface RetryPolicy {
  maxAttempts: number; backoffBaseMs: number; maxSingleWaitMs: number; maxTotalWaitMs: number;
  jobPollIntervalMs: number; jobPollDeadlineMs: number;
}
export const DEFAULT_RETRY: RetryPolicy = {
  maxAttempts: 4, backoffBaseMs: 2000, maxSingleWaitMs: 30_000, maxTotalWaitMs: 60_000,
  jobPollIntervalMs: 3000, jobPollDeadlineMs: 600_000,
};

export interface HttpRequest {
  url: string; method: "GET" | "POST"; headers: Record<string, string>; body?: string;
  timeoutMs: number; signal?: AbortSignal;
}
export interface HttpResponse { status: number; headers: Record<string, string>; text: string; url: string; }
export type HttpTransport = (req: HttpRequest) => Promise<HttpResponse>;

export class TransportError extends Error {
  constructor(message: string, readonly timeout: boolean) { super(message); this.name = "TransportError"; }
}
export class CloudError extends Error {
  constructor(message: string) { super(message); this.name = "CloudError"; }
}

/** 32000 → "32,000" without depending on Intl (Hermes builds may lack it). */
const grouped = (n: number) => String(Math.trunc(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

export const fetchTransport: HttpTransport = async (req) => {
  if (req.signal?.aborted) throw new CancelledError();
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, req.timeoutMs);
  const onAbort = () => ctl.abort();
  req.signal?.addEventListener("abort", onAbort);
  try {
    const r = await fetch(req.url, { method: req.method, headers: req.headers, body: req.body, signal: ctl.signal });
    const text = await r.text();
    const headers: Record<string, string> = {};
    r.headers.forEach((v, k) => { headers[k.toLowerCase()] = v; });
    return { status: r.status, headers, text, url: r.url || req.url };
  } catch (e) {
    if (req.signal?.aborted) throw new CancelledError();
    if (timedOut) throw new TransportError("the server did not respond in time", true);
    throw new TransportError(e instanceof Error && e.message ? e.message : "the connection failed", false);
  } finally {
    clearTimeout(timer);
    req.signal?.removeEventListener("abort", onAbort);
  }
};

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new CancelledError()); return; }
    const onAbort = () => { clearTimeout(t); reject(new CancelledError()); };
    const t = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

const trimSlash = (u: string) => u.replace(/\/+$/, "");

/** Scheme, host and path of an http(s) URL — RN's URL class lacks most properties. */
function splitUrl(u: string): { scheme: string; host: string; hostname: string; path: string } | null {
  const m = /^(https?):\/\/([^/?#]+)([^?#]*)/i.exec(u);
  if (!m) return null;
  return { scheme: m[1].toLowerCase(), host: m[2], hostname: m[2].replace(/:\d+$/, "").toLowerCase(), path: m[3] || "/" };
}

export function looksLikeConnect(status: number, text: string): boolean {
  let d: unknown;
  try { d = JSON.parse(text || ""); } catch { return false; }
  if (!d || typeof d !== "object" || Array.isArray(d)) return false;
  const o = d as Record<string, unknown>;
  if (status === 200) return Array.isArray(o.data);
  if (status === 401 || status === 403) return typeof o.error === "string" || (!!o.error && typeof o.error === "object");
  return false;
}

/** The plain-HTTP endpoint until the server offers TLS (§9); shared by clients. */
export class ConnectEndpoint {
  secureBase: string | null = null;
  constructor(readonly plainBase: string = CONNECT_BASE_URL, readonly candidates: string[] = CONNECT_HTTPS_CANDIDATES) {}
  base(): string { return trimSlash(this.secureBase ?? this.plainBase); }
  isEncrypted(): boolean { return this.base().startsWith("https://"); }
  async probeHttps(transport: HttpTransport = fetchTransport, timeoutMs = 4000): Promise<boolean> {
    for (const cand of this.candidates) {
      if (!cand.toLowerCase().startsWith("https://")) continue;
      try {
        const r = await transport({ url: trimSlash(cand) + "/models", method: "GET", headers: { Accept: "application/json" }, timeoutMs });
        if (looksLikeConnect(r.status, r.text)) { this.secureBase = trimSlash(cand); return true; }
      } catch { /* no TLS, unreachable, invalid certificate */ }
    }
    return false;
  }
}
export const defaultEndpoint = new ConnectEndpoint();

export function newRequestIds(): { idempotencyKey: string; correlationId: string } {
  const u = randomHex(16);
  return { idempotencyKey: `mico360-meetings-${u}`, correlationId: `MM-${u.slice(0, 24)}` };
}

export function originOf(base: string): string {
  const b = trimSlash(base);
  for (const suffix of ["/openai/v1", "/v1"]) if (b.endsWith(suffix)) return b.slice(0, -suffix.length);
  return b;
}

const header = (r: HttpResponse, name: string) => (r.headers[name.toLowerCase()] ?? "").trim();

function retryAfterMs(r: HttpResponse): number | null {
  const raw = header(r, "retry-after");
  if (!raw) return null;
  const n = Number(raw);
  if (Number.isFinite(n)) return Math.max(0, n * 1000);
  const t = Date.parse(raw);
  return Number.isNaN(t) ? null : Math.max(0, t - Date.now());
}

function errorDetail(r: HttpResponse): string {
  try {
    const e = (JSON.parse(r.text) as { error?: unknown })?.error;
    if (typeof e === "string") return e;
    if (e && typeof e === "object" && typeof (e as { message?: unknown }).message === "string") return (e as { message: string }).message;
  } catch { /* empty or non-JSON body */ }
  return "";
}

export function waitWords(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 90) return `${s} seconds`;
  if (s < 90 * 60) return `${Math.round(s / 60)} minutes`;
  return `${Math.round(s / 3600)} hours`;
}

const HINTS: Record<number, string> = {
  400: "the request was not accepted",
  401: "the API key was rejected (missing, disabled, expired or deleted)",
  403: "this key is not permitted to use that capability or model",
  404: "the address is not the MICO360 AI API — check the base URL ends in /v1",
  405: "the request used the wrong method",
  413: "the request is too large — lower the chunk size in Settings or shorten the template",
  415: "the request was not sent as JSON",
  429: "rate limit or quota exceeded — try again shortly",
  500: "the server had an internal error",
  502: "the AI node could not complete the request — try again shortly",
  503: "no AI node can serve this request right now",
  504: "the server timed out waiting for an AI node",
};
const RETRY_STATUSES = new Set([429, 502, 503]);
const FINAL_JOB_STATES = new Set(["Completed", "Failed", "TimedOut", "Cancelled"]);
const JOB_ID_RE = /\bjob\s+(JOB-[A-Za-z0-9_\-:.]+?)[,)\s]/;
const REDIRECTS = new Set([301, 302, 303, 307, 308]);
const REDIRECT_REFUSED = "MICO360 Connect redirected the request to another address, which is not followed (the API key would go with it).";

export interface CloudStatus { running: boolean; models: string[]; error: string; }

export interface CloudClientOptions {
  key: string;
  model?: string;
  baseUrl?: string;
  endpoint?: ConnectEndpoint;
  transport?: HttpTransport;
  retry?: Partial<RetryPolicy>;
  userAgent?: string;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

export interface GenerateOptions {
  style?: string; removeFillers?: boolean; chunkChars?: number; progress?: ProgressFn; signal?: AbortSignal;
}

function quote(r: HttpResponse, corr: string): string {
  const cid = header(r, "x-mico-correlation-id") || corr;
  return cid ? ` Reference for support: ${cid}.` : "";
}

function transportMessage(e: unknown, attempts: number): string {
  const tried = attempts > 1 ? ` (tried ${attempts} times)` : "";
  if (e instanceof TransportError && e.timeout) {
    return `MICO360 Connect did not respond in time${tried}. The service may be busy or your connection slow — try again shortly.`;
  }
  const reason = e instanceof Error ? e.message : String(e);
  return `Could not reach MICO360 Connect (${reason})${tried}. Check your network connection.`;
}

export class CloudClient {
  readonly key: string;
  model: string;
  warnings: string[] = [];
  private explicitBase: string;
  private readonly endpoint: ConnectEndpoint;
  private readonly transport: HttpTransport;
  private readonly retry: RetryPolicy;
  private readonly userAgent: string;
  private readonly wait: (ms: number, signal?: AbortSignal) => Promise<void>;

  constructor(o: CloudClientOptions) {
    this.key = o.key ?? "";
    this.model = o.model || CONNECT_DEFAULT_MODEL;
    this.explicitBase = trimSlash(o.baseUrl ?? "");
    this.endpoint = o.endpoint ?? defaultEndpoint;
    this.transport = o.transport ?? fetchTransport;
    this.retry = { ...DEFAULT_RETRY, ...o.retry };
    this.userAgent = o.userAgent ?? "MICO360 Meetings (Android)";
    this.wait = o.sleep ?? sleep;
  }

  get baseUrl(): string { return this.explicitBase || this.endpoint.base(); }
  isEncrypted(): boolean { return this.baseUrl.startsWith("https://"); }

  async status(signal?: AbortSignal): Promise<CloudStatus> {
    if (!this.key) return { running: false, models: [], error: "MICO360 Cloud isn't enabled in this build." };
    try {
      const r = await this.transport({ url: this.baseUrl + "/models", method: "GET", timeoutMs: STATUS_TIMEOUT_MS, signal,
        headers: { Authorization: `Bearer ${this.key}`, Accept: "application/json", "User-Agent": this.userAgent } });
      if (r.status === 200) {
        const d = JSON.parse(r.text) as { data?: Array<{ id?: unknown }> };
        const models = (Array.isArray(d?.data) ? d.data : []).map((m) => m?.id)
          .filter((x): x is string => typeof x === "string" && x.length > 0).sort();
        return { running: true, models, error: "" };
      }
      const error = r.status === 401 ? "MICO360 Connect rejected the API key (401)."
        : r.status === 403 ? "This key is not permitted to list models (403)."
        : `MICO360 Connect returned HTTP ${r.status}.`;
      return { running: false, models: [], error };
    } catch (e) {
      if (e instanceof CancelledError) throw e;
      return { running: false, models: [], error: `Could not reach MICO360 Connect: ${e instanceof Error ? e.message : String(e)}` };
    }
  }

  async chat(prompt: string, signal?: AbortSignal): Promise<string> {
    if (signal?.aborted) throw new CancelledError();
    if (!this.key) throw new CloudError("Minutes need MICO360 Cloud, which isn't enabled in this build.");
    if (prompt.length > MAX_PROMPT_CHARS) {
      throw new CloudError(`This request is ${grouped(prompt.length)} characters; MICO360 Cloud accepts at most ` +
        `${grouped(MAX_PROMPT_CHARS)}. Shorten the prompt template, or lower the chunk size in Settings so long meetings are split into smaller parts.`);
    }
    const body = JSON.stringify({ model: this.model, messages: [{ role: "user", content: prompt }], stream: false });
    const ids = newRequestIds();                      // the same for every retry of this call (§11)
    let waited = 0;
    let attempt = 0;
    let redirects = 0;
    for (;;) {
      attempt += 1;
      const url = this.baseUrl + "/chat/completions";
      let r: HttpResponse;
      try {
        r = await this.transport({ url, method: "POST", body, timeoutMs: CHAT_TIMEOUT_MS, signal, headers: {
          Authorization: `Bearer ${this.key}`, "Content-Type": "application/json", Accept: "application/json",
          "User-Agent": this.userAgent, "Idempotency-Key": ids.idempotencyKey, "X-Mico-Correlation-Id": ids.correlationId } });
      } catch (e) {
        if (e instanceof CancelledError) throw e;
        const delay = Math.min(this.retry.backoffBaseMs * 2 ** (attempt - 1), this.retry.maxSingleWaitMs);
        if (attempt >= this.retry.maxAttempts || waited + delay > this.retry.maxTotalWaitMs) throw new CloudError(transportMessage(e, attempt));
        await this.wait(delay, signal);
        waited += delay;
        continue;
      }

      if (this.followRedirect(r, url)) {
        if (++redirects > 2) throw new CloudError(REDIRECT_REFUSED);
        attempt -= 1;
        continue;
      }
      if (r.status === 200) return this.answer(r);
      if (r.status === 504) {
        const m = JOB_ID_RE.exec(errorDetail(r) + " ");
        if (!m) throw new CloudError(this.errorMessage(r, attempt, ids.correlationId));
        const job = await this.collectJob(m[1], ids.correlationId, signal);
        if (job.state === "Completed") return job.text.trim();
        if (job.state === "Cancelled" && attempt < this.retry.maxAttempts) continue;   // never started: safe to send again
        throw new CloudError(`MICO360 Connect could not finish this part (${m[1]}: ${job.text}).` + quote(r, ids.correlationId));
      }
      const delay = this.retryDelay(r, attempt, waited, ids.correlationId);
      await this.wait(delay, signal);
      waited += delay;
    }
  }

  async generateMinutes(transcript: string, template: string, o: GenerateOptions = {}): Promise<string> {
    this.warnings = [];
    const room = MAX_PROMPT_CHARS - (template ?? "").length - PROMPT_OVERHEAD;
    if (room < 1500) {
      throw new CloudError(`The prompt template is ${grouped((template ?? "").length)} characters — too long for MICO360 Cloud, ` +
        `which accepts ${grouped(MAX_PROMPT_CHARS)} characters per request including the meeting text. Shorten the template.`);
    }
    const chunk = Math.max(1500, Math.min(o.chunkChars || 6000, room));    // 0 / unset → 6,000, as desktop int(x or 6000)
    const budget = Math.min(Math.max(REDUCE_NOTES_BUDGET, 2 * chunk), room);
    return runMinutesPipeline((p, s) => this.chat(p, s), {
      model: this.model, transcript, template, style: o.style, removeFillers: o.removeFillers,
      chunkChars: chunk, reduceBudget: budget, progress: o.progress, signal: o.signal,
    });
  }

  private answer(r: HttpResponse): string {
    let d: { choices?: Array<{ message?: { content?: unknown } }>; warning?: unknown };
    try { d = JSON.parse(r.text); } catch { throw new CloudError("MICO360 Connect returned an unexpected response."); }
    for (const w of [header(r, "x-mico-model-warning"), typeof d?.warning === "string" ? d.warning : "", header(r, "x-mico-warning")]) {
      const t = w.trim();
      if (t && !this.warnings.includes(t)) this.warnings.push(t);
    }
    const msg = d?.choices?.[0]?.message;
    if (!msg || typeof msg !== "object" || !("content" in msg)) throw new CloudError("MICO360 Connect returned an unexpected response.");
    return String(msg.content ?? "").trim();
  }

  /** §9: plain HTTP is redirected to HTTPS once TLS is on. Adopt it only for
   *  HTTPS on the SAME host; refuse anything else. Returns true when the
   *  request must be sent again (a redirect the HTTP stack did not follow). */
  private followRedirect(r: HttpResponse, requested: string): boolean {
    const isRedirect = REDIRECTS.has(r.status);
    const target = isRedirect ? header(r, "location") : (r.url && r.url !== requested ? r.url : "");
    if (!target) {
      if (isRedirect) throw new CloudError(REDIRECT_REFUSED);
      return false;
    }
    const cur = splitUrl(requested);
    const next = splitUrl(target);
    if (!cur || !next || next.scheme !== "https" || next.hostname !== cur.hostname || !next.path.endsWith("/chat/completions")) {
      throw new CloudError(REDIRECT_REFUSED);
    }
    const base = trimSlash(`https://${next.host}${next.path.slice(0, -"/chat/completions".length)}`);
    if (this.explicitBase) this.explicitBase = base;
    else this.endpoint.secureBase = base;
    return isRedirect;
  }

  private retryDelay(r: HttpResponse, attempt: number, waited: number, corr: string): number {
    if (!RETRY_STATUSES.has(r.status) || attempt >= this.retry.maxAttempts) throw new CloudError(this.errorMessage(r, attempt, corr));
    const ra = retryAfterMs(r);
    if (r.status === 503 && ra === null) throw new CloudError(this.errorMessage(r, attempt, corr));
    if (ra !== null && (ra > this.retry.maxSingleWaitMs || waited + ra > this.retry.maxTotalWaitMs)) {
      throw new CloudError(this.errorMessage(r, attempt, corr) + ` Try again in about ${waitWords(ra)}.`);
    }
    const delay = ra ?? Math.min(this.retry.backoffBaseMs * 2 ** (attempt - 1), this.retry.maxSingleWaitMs);
    if (waited + delay > this.retry.maxTotalWaitMs) throw new CloudError(this.errorMessage(r, attempt, corr));
    return delay;
  }

  private errorMessage(r: HttpResponse, attempts: number, corr: string): string {
    const tried = attempts > 1 ? ` (tried ${attempts} times)` : "";
    return `MICO360 Connect error (${r.status}): ${errorDetail(r) || HINTS[r.status] || `HTTP ${r.status}`}${tried}` + quote(r, corr);
  }

  private async collectJob(jobId: string, corr: string, signal?: AbortSignal): Promise<{ state: string; text: string }> {
    const url = originOf(this.baseUrl) + `/api/v1/jobs/${encodeURIComponent(jobId)}`;
    const deadline = Date.now() + this.retry.jobPollDeadlineMs;
    let last = "the job could not be read";
    while (Date.now() < deadline) {
      try {
        const r = await this.transport({ url, method: "GET", timeoutMs: 30_000, signal, headers: {
          "X-Mico-Api-Key": this.key, Accept: "application/json", "User-Agent": this.userAgent, "X-Mico-Correlation-Id": corr } });
        if (r.status === 200) {
          const job = JSON.parse(r.text) as { state?: unknown; output?: unknown; error?: unknown };
          const state = String(job.state ?? "");
          if (state === "Completed") return { state, text: String(job.output ?? "") };
          if (FINAL_JOB_STATES.has(state)) return { state, text: String(job.error || state) };
        } else if (r.status === 401 || r.status === 403 || r.status === 404) {
          return { state: "Unknown", text: `the job could not be read (HTTP ${r.status})` };
        } else {
          last = `HTTP ${r.status}`;
        }
      } catch (e) {
        if (e instanceof CancelledError) throw e;
        last = e instanceof Error ? e.message : String(e);
      }
      await this.wait(this.retry.jobPollIntervalMs, signal);
    }
    return { state: "Unknown", text: `no answer within ${waitWords(this.retry.jobPollDeadlineMs)} (${last})` };
  }
}
