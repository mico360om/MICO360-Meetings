/** Minutes generation pipeline (port of desktop core/generation.py):
 *  short transcripts in one request; long ones condensed per part, merged
 *  hierarchically when the notes are too long, then reduced with the SAME
 *  template. Finished parts are cached so a retry resumes where it stopped. */
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";
import { chunkText, cleanTranscript } from "./cleaning";
import { DEFAULT_STYLE, buildChunkPrompt, buildGenerationPrompt, buildMergeNotesPrompt, buildReducePrompt } from "./prompts";

export type ChatFn = (prompt: string, signal?: AbortSignal) => Promise<string>;
export type ProgressFn = (fraction: number, message: string) => void;

export class CancelledError extends Error {
  constructor(message = "Generation cancelled.") {
    super(message);
    this.name = "CancelledError";
  }
}

export function throwIfCancelled(signal?: AbortSignal): void {
  if (signal?.aborted) throw new CancelledError();
}

export const REDUCE_NOTES_BUDGET = 12000;
const MAX_MERGE_ROUNDS = 4;
const PART_CACHE_MAX = 256;
const partCache = new Map<string, string>();

function cacheGet(key: string): string | undefined {
  const v = partCache.get(key);
  if (v !== undefined) { partCache.delete(key); partCache.set(key, v); }
  return v;
}

function cachePut(key: string, value: string): void {
  partCache.delete(key);
  partCache.set(key, value);
  while (partCache.size > PART_CACHE_MAX) partCache.delete(partCache.keys().next().value as string);
}

export function clearPartCache(): void {
  partCache.clear();
}

/** Pack consecutive notes into groups whose joined size stays <= budget. */
export function groupNotes(notes: string[], budget: number): string[][] {
  let groups: string[][] = [];
  let cur: string[] = [];
  let size = 0;
  for (const n of notes) {
    const add = n.length + 2;
    if (cur.length && size + add > budget) { groups.push(cur); cur = []; size = 0; }
    cur.push(n);
    size += add;
  }
  if (cur.length) groups.push(cur);
  if (groups.length === notes.length && notes.length > 1) {
    groups = [];
    for (let i = 0; i < notes.length; i += 2) groups.push(notes.slice(i, i + 2));
  }
  return groups;
}

export async function mergeNotesHierarchically(
  chat: ChatFn, notes: string[], totalParts: number,
  opts: { budget?: number; progress?: ProgressFn; signal?: AbortSignal; span?: [number, number] } = {},
): Promise<string[]> {
  const budget = opts.budget ?? REDUCE_NOTES_BUDGET;
  const [lo, hi] = opts.span ?? [0.75, 0.85];
  const total = (xs: string[]) => xs.reduce((s, n) => s + n.length + 2, 0);
  let level = [...notes];
  let ranges: Array<[number, number]> = notes.map((_n, i) => [i + 1, i + 1]);
  let rounds = 0;
  while (total(level) > budget && level.length > 1 && rounds < MAX_MERGE_ROUNDS) {
    rounds += 1;
    const groups = groupNotes(level, budget);
    const newLevel: string[] = [];
    const newRanges: Array<[number, number]> = [];
    let idx = 0;
    for (let gi = 1; gi <= groups.length; gi++) {
      const g = groups[gi - 1];
      const first = ranges[idx][0];
      const last = ranges[idx + g.length - 1][1];
      idx += g.length;
      if (g.length === 1) { newLevel.push(g[0]); newRanges.push([first, last]); continue; }
      throwIfCancelled(opts.signal);
      opts.progress?.(lo + ((hi - lo) * (gi - 1)) / Math.max(1, groups.length),
        `Combining notes for parts ${first}–${last} of ${totalParts}…`);
      const merged = await chat(buildMergeNotesPrompt(g.join("\n\n"), first, last, totalParts), opts.signal);
      newLevel.push(`### Parts ${first}–${last}\n${merged}`);
      newRanges.push([first, last]);
    }
    const noProgress = newLevel.length >= level.length;
    level = newLevel;
    ranges = newRanges;
    if (noProgress) break;
  }
  return level;
}

export interface PipelineOptions {
  model: string;
  transcript: string;
  template: string;
  style?: string;
  removeFillers?: boolean;
  chunkChars?: number;
  reduceBudget?: number;
  progress?: ProgressFn;
  signal?: AbortSignal;
}

export async function runMinutesPipeline(chat: ChatFn, o: PipelineOptions): Promise<string> {
  if (!o.model) throw new Error("No AI model selected.");
  const style = o.style ?? DEFAULT_STYLE;
  const chunkChars = o.chunkChars ?? 6000;
  o.progress?.(0.03, "Cleaning transcript…");
  const cleaned = cleanTranscript(o.transcript, o.removeFillers ?? true);
  if (!cleaned.trim()) throw new Error("Transcript is empty after cleaning.");
  const chunks = chunkText(cleaned, chunkChars);

  if (chunks.length <= 1) {
    o.progress?.(0.15, `Generating ${style} with ${o.model} — the first request can take up to a minute while the model loads…`);
    const result = await chat(buildGenerationPrompt(o.template, style, cleaned), o.signal);
    o.progress?.(1, "Minutes generated.");
    return result;
  }

  const notes: string[] = [];
  const n = chunks.length;
  for (let i = 1; i <= n; i++) {
    throwIfCancelled(o.signal);
    const note = i === 1 ? " (the first request can take up to a minute)" : "";
    o.progress?.(0.1 + (0.65 * (i - 1)) / n, `Analyzing part ${i} of ${n}…${note}`);
    const p = buildChunkPrompt(chunks[i - 1], i, n);
    const key = bytesToHex(sha256(utf8ToBytes(o.model + "\x00" + p)));   // 64 chars, not the ~30 KB prompt (desktop keys by SHA-256 too)
    let part = cacheGet(key);
    if (part === undefined) {
      part = await chat(p, o.signal);
      if ((part ?? "").trim()) cachePut(key, part);   // never cache an empty answer
    }
    notes.push(`### Part ${i}\n` + part);
  }

  const budget = o.reduceBudget ?? Math.max(REDUCE_NOTES_BUDGET, 2 * chunkChars);
  const merged = await mergeNotesHierarchically(chat, notes, n, { budget, progress: o.progress, signal: o.signal });
  throwIfCancelled(o.signal);
  o.progress?.(0.85, `Composing ${style}…`);
  const result = await chat(buildReducePrompt(o.template, style, merged.join("\n\n")), o.signal);
  o.progress?.(1, "Minutes generated.");
  return result;
}
