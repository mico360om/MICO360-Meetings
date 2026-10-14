import { describe, expect, it, vi } from "vitest";
import { setRandomSource } from "../src/core/random";
import { CancelledError } from "../src/core/pipeline";
import {
  CloudClient, CloudError, type CloudClientOptions, newRequestIds, ConnectEndpoint, type HttpRequest, type HttpResponse, TransportError,
} from "../src/core/cloudClient";

type Step = HttpResponse | Error | ((r: HttpRequest) => HttpResponse);
const OK: HttpResponse = { status: 200, headers: {}, url: "", text: JSON.stringify({ choices: [{ message: { content: " Minutes OK. " } }] }) };
const res = (status: number, body: unknown, headers: Record<string, string> = {}): HttpResponse =>
  ({ status, headers, url: "", text: typeof body === "string" ? body : JSON.stringify(body) });

function server(steps: Step[]) {
  const requests: HttpRequest[] = [];
  const queue = [...steps];
  const transport = async (r: HttpRequest) => {
    requests.push(r);
    const s = queue.length > 1 ? queue.shift()! : queue[0];
    if (s instanceof Error) throw s;
    const out = typeof s === "function" ? s(r) : s;
    return { ...out, url: out.url || r.url };
  };
  return { requests, transport, posts: () => requests.filter((r) => r.method === "POST") };
}

function client(t: ReturnType<typeof server>, extra: Partial<CloudClientOptions> = {}) {
  const waits: number[] = [];
  const c = new CloudClient({
    key: "mico_test", model: "m", baseUrl: "http://srv:5310/v1", transport: t.transport,
    retry: { backoffBaseMs: 5, jobPollIntervalMs: 1 },
    sleep: async (ms) => { waits.push(ms); },
    ...extra,
  });
  return { c, waits };
}

const JOB_504 = res(504, { error: { message: "The request did not complete within 300 seconds (job JOB-GW1-20261015-000042, correlation id REQ-ABC123).", type: "timeout", code: null } });

describe("request shape (§1, §5, §11)", () => {
  it("POSTs JSON with the Bearer key, ids, and only the fields the platform reads", async () => {
    const s = server([OK]);
    expect(await client(s).c.chat("hello")).toBe("Minutes OK.");
    const r = s.posts()[0];
    expect(r.url).toBe("http://srv:5310/v1/chat/completions");
    expect(r.headers.Authorization).toBe("Bearer mico_test");
    expect(r.headers["Content-Type"]).toBe("application/json");
    expect(r.headers.Accept).toBe("application/json");
    expect(r.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_\-:.]{1,128}$/);
    expect(r.headers["X-Mico-Correlation-Id"]).toMatch(/^[A-Za-z0-9_\-:.]{1,64}$/);
    expect(JSON.parse(r.body!)).toEqual({ model: "m", messages: [{ role: "user", content: "hello" }], stream: false });
    expect(r.timeoutMs).toBeGreaterThan(300_000);
  });

  it("a retry repeats the same ids; a new call gets new ones", async () => {
    const s = server([res(503, { error: { message: "No connected node provides the 'Chat' capability." } }, { "retry-after": "1" }), OK]);
    const { c, waits } = client(s);
    await c.chat("one");
    await c.chat("two");
    const keys = s.posts().map((r) => r.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
    expect(waits).toEqual([1000]);
  });
});

describe("refusals (§8)", () => {
  it("503 without Retry-After is final and shows the server's reason", async () => {
    const s = server([res(503, { error: { message: "Requested model 'm' is currently unavailable. No alternative model is available." } })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/No alternative model/);
    expect(s.posts()).toHaveLength(1);
  });
  it("a long Retry-After (quota) is reported with the wait, never retried early", async () => {
    const s = server([res(429, { error: { message: "Quota exceeded: tokens per day." } }, { "retry-after": "7200" })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/Quota exceeded.*2 hours/s);
    expect(s.posts()).toHaveLength(1);
  });
  it("errors quote the correlation id", async () => {
    const s = server([res(400, { error: { message: "n must be 1." } }, { "x-mico-correlation-id": "REQ-77AA" })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/n must be 1\..*REQ-77AA/s);
  });
  it("empty-body statuses get a hint", async () => {
    const s = server([res(415, "")]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/415.*JSON/s);
  });
  it("a timeout is retried, then reported in plain words", async () => {
    const s = server([new TransportError("the server did not respond in time", true)]);
    await expect(client(s, { retry: { maxAttempts: 2, backoffBaseMs: 1 } }).c.chat("hi")).rejects.toThrow(/did not respond in time/);
    expect(s.posts()).toHaveLength(2);
  });
  it("cancel while waiting to retry", async () => {
    const ctl = new AbortController();
    const s = server([res(503, {}, { "retry-after": "20" })]);
    const c = new CloudClient({ key: "k", baseUrl: "http://srv/v1", transport: s.transport,
      sleep: async () => { ctl.abort(); throw new CancelledError(); } });
    await expect(c.chat("hi", ctl.signal)).rejects.toBeInstanceOf(CancelledError);
  });
  it("no key in this build", async () => {
    await expect(new CloudClient({ key: "" }).chat("hi")).rejects.toThrow(/isn't enabled in this build/);
  });
});

describe("504: collect the job, never resubmit (§5, §8)", () => {
  it("collects the answer with the native header", async () => {
    const s = server([JOB_504,
      res(200, { jobId: "JOB-GW1-20261015-000042", state: "Running", output: null }),
      res(200, { jobId: "JOB-GW1-20261015-000042", state: "Completed", output: " Collected minutes. ", error: null })]);
    expect(await client(s).c.chat("hi")).toBe("Collected minutes.");
    const polls = s.requests.filter((r) => r.method === "GET");
    expect(s.posts()).toHaveLength(1);
    expect(polls.map((r) => r.url)).toEqual(Array(2).fill("http://srv:5310/api/v1/jobs/JOB-GW1-20261015-000042"));
    expect(polls[0].headers["X-Mico-Api-Key"]).toBe("mico_test");
    expect(polls[0].headers.Authorization).toBeUndefined();
  });
  it("a failed job reports its error", async () => {
    const s = server([JOB_504, res(200, { state: "Failed", error: "Model ran out of memory." })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/ran out of memory/);
    expect(s.posts()).toHaveLength(1);
  });
  it("a job that never started is sent again", async () => {
    const s = server([JOB_504, res(200, { state: "Cancelled", error: "Cancelled before a node started it." }), OK]);
    expect(await client(s).c.chat("hi")).toBe("Minutes OK.");
    expect(s.posts()).toHaveLength(2);
  });
});

describe("limits and warnings (§5, §6, §11)", () => {
  it("refuses a prompt over 32,000 characters before sending", async () => {
    const s = server([OK]);
    await expect(client(s).c.chat("x".repeat(32_001))).rejects.toThrow(/32,000/);
    expect(s.requests).toHaveLength(0);
  });
  it("every pipeline request fits the limit, even a 2-hour meeting with a long template", async () => {
    const s = server([res(200, { choices: [{ message: { content: "Notes." } }] })]);
    const transcript = Array.from({ length: 3000 }, (_, i) => `Sentence ${i} about the budget and the plan.`).join(" ");
    await client(s).c.generateMinutes(transcript, "Write minutes. ".repeat(300) + "[TRANSCRIPT_HERE]", { chunkChars: 20000 });
    const sizes = s.posts().map((r) => JSON.parse(r.body!).messages[0].content.length);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(32_000);
    expect(sizes.length).toBeGreaterThan(3);
  });
  it("a model substitution becomes one warning", async () => {
    const s = server([res(200, { model: "qwen2.5:0.5b", warning: "Requested model 'm' is unavailable; processing on 'qwen2.5:0.5b' instead.",
      choices: [{ message: { content: "OK" } }] }, { "x-mico-model-warning": "Requested model 'm' is unavailable; processing on 'qwen2.5:0.5b' instead." })]);
    const { c } = client(s);
    await c.chat("hi");
    expect(c.warnings).toEqual(["Requested model 'm' is unavailable; processing on 'qwen2.5:0.5b' instead."]);
  });
});

describe("TLS (§9)", () => {
  it("a 307 to HTTPS on the same host switches the endpoint", async () => {
    const ep = new ConnectEndpoint("http://ai.mico360.com:5310/v1", []);
    const s = server([(r) => r.url.startsWith("http://")
      ? res(307, "", { location: "https://ai.mico360.com:5311/v1/chat/completions" }) : OK]);
    const c = new CloudClient({ key: "k", endpoint: ep, transport: s.transport, sleep: async () => {} });
    expect(await c.chat("hi")).toBe("Minutes OK.");
    expect(s.requests[s.requests.length - 1].url).toBe("https://ai.mico360.com:5311/v1/chat/completions");
    expect(ep.isEncrypted()).toBe(true);
  });
  it("an HTTP stack that followed the redirect is noticed from the final URL", async () => {
    const ep = new ConnectEndpoint("http://ai.mico360.com:5310/v1", []);
    const s = server([{ ...OK, url: "https://ai.mico360.com:5311/v1/chat/completions" }]);
    await new CloudClient({ key: "k", endpoint: ep, transport: s.transport }).chat("hi");
    expect(ep.isEncrypted()).toBe(true);
  });
  it("a redirect to another host is refused", async () => {
    const s = server([res(307, "", { location: "https://other.example/v1/chat/completions" })]);
    await expect(client(s).c.chat("hi")).rejects.toBeInstanceOf(CloudError);
    expect(s.requests.every((r) => !r.url.includes("other.example"))).toBe(true);
  });
  it("the HTTPS probe accepts only the real API", async () => {
    const ep = new ConnectEndpoint("http://h:5310/v1", ["https://site.example/v1", "https://h:5311/v1"]);
    const ok = await ep.probeHttps(async (r) => r.url.startsWith("https://site")
      ? res(200, "<html>website</html>") : res(401, { error: { message: "Invalid or missing API key." } }));
    expect(ok).toBe(true);
    expect(ep.base()).toBe("https://h:5311/v1");
  });
});

describe("status", () => {
  it("lists models sorted, and explains a rejected key", async () => {
    const s = server([res(200, { object: "list", data: [{ id: "b" }, { id: "a" }] })]);
    expect(await client(s).c.status()).toEqual({ running: true, models: ["a", "b"], error: "" });
    const s2 = server([res(401, { error: { message: "x" } })]);
    expect((await client(s2).c.status()).error).toMatch(/rejected the API key/);
  });
});

describe("request ids on a phone (Hermes has no WebCrypto)", () => {
  it("come from the app's secure random source when one is installed", () => {
    setRandomSource((n) => new Uint8Array(n).fill(0xab));
    try {
      expect(newRequestIds().idempotencyKey).toBe("mico360-meetings-" + "ab".repeat(16));
    } finally { setRandomSource(null); }
  });
  it("without any secure source, two calls never repeat even when Math.random does", () => {
    vi.stubGlobal("crypto", undefined);
    const spy = vi.spyOn(Math, "random").mockReturnValue(0.5);
    try {
      const a = newRequestIds().idempotencyKey, b = newRequestIds().idempotencyKey;
      expect(a).not.toBe(b);
      expect(a).toMatch(/^[A-Za-z0-9_\-:.]{1,128}$/);
    } finally { spy.mockRestore(); vi.unstubAllGlobals(); }
  });
});
