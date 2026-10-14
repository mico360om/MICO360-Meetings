/** Prompt templates, output styles and the prompt library (port of desktop
 *  core/prompts.py). All text comes from data/desktop.json. */
import data from "./data/desktop.json";

export const TRANSCRIPT_TOKEN: string = data.transcriptToken;
const STYLES = new Map<string, string>(data.outputStyles as Array<[string, string]>);
export const STYLE_NAMES: string[] = [...STYLES.keys()];
export const DEFAULT_STYLE: string = data.defaultStyle;
export const PROMPT_CATEGORIES: string[] = data.promptCategories;
export const BUILTIN_PROMPTS = (data.builtinPrompts as Array<[string, string, string]>).map(
  ([name, category, text]) => ({ name, category, text }));

/** Replace every occurrence literally ($& and $1 in user text stay as typed). */
const sub = (s: string, token: string, value: string) => s.split(token).join(value);
const SEP = "\x00";

export function styleHint(style: string): string {
  return STYLES.get(style) ?? "";
}

export function buildGenerationPrompt(template: string, style: string, transcript: string): string {
  const hint = styleHint(style);
  let base = template.includes(TRANSCRIPT_TOKEN) ? template : template + "\n\nTranscript:\n" + TRANSCRIPT_TOKEN;
  base = sub(base, TRANSCRIPT_TOKEN, transcript);
  return hint ? `${base}\n\nStyle instruction: ${hint}` : base;
}

export function buildReducePrompt(template: string, style: string, partialNotes: string): string {
  return buildGenerationPrompt(template, style, data.reduceNotesPrefix + partialNotes);
}

export function buildMergeNotesPrompt(notes: string, first: number, last: number, total: number): string {
  const t = sub(sub(sub(data.mergeNotesPrompt, "{first}", String(first)), "{last}", String(last)), "{total}", String(total));
  return sub(t, TRANSCRIPT_TOKEN, notes);
}

export function buildChunkPrompt(chunk: string, idx: number, total: number): string {
  const t = sub(sub(data.chunkSummaryPrompt, "{idx}", String(idx)), "{total}", String(total));
  return sub(t, TRANSCRIPT_TOKEN, chunk);
}

// ---- library ---------------------------------------------------------------
export interface SavedPrompt {
  id: string;
  name: string;
  text: string;
  builtin: boolean;
  category: string;
  favorite: boolean;
}

function randomHex(n: number): string {
  let s = "";
  while (s.length < n) s += Math.floor(Math.random() * 16).toString(16);
  return s;
}

export function promptId(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "prompt";
  return `${slug}-${randomHex(6)}`;
}

export function builtinText(name: string): string | null {
  return BUILTIN_PROMPTS.find((p) => p.name === name)?.text ?? null;
}

export function isModified(p: SavedPrompt): boolean {
  const shipped = p.builtin ? builtinText(p.name) : null;
  return shipped !== null && shipped !== p.text;
}

export function uniqueName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  const base = (name ?? "").trim() || "Untitled";
  if (!t.has(base.toLowerCase())) return base;
  let n = 2;
  while (t.has(`${base} (${n})`.toLowerCase())) n += 1;
  return `${base} (${n})`;
}

export function copyName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  let cand = `${name} (copy)`;
  let n = 2;
  while (t.has(cand.toLowerCase())) { cand = `${name} (copy ${n})`; n += 1; }
  return cand;
}

/** Read a prompt export (.json) or one prompt (.txt/.md, named after the file). */
export function parsePromptFile(fileName: string, raw: string): { records: unknown[]; invalid: number } {
  const dot = fileName.lastIndexOf(".");
  const ext = dot >= 0 ? fileName.slice(dot).toLowerCase() : "";
  const text = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw;
  if (ext === ".json") {
    const d: unknown = JSON.parse(text);
    if (d && typeof d === "object" && !Array.isArray(d) && Array.isArray((d as { prompts?: unknown }).prompts))
      return { records: (d as { prompts: unknown[] }).prompts, invalid: 0 };
    if (Array.isArray(d)) return { records: d, invalid: 0 };
    if (d && typeof d === "object") return { records: [d], invalid: 0 };
    throw new Error("This file doesn't contain prompts.");
  }
  if (ext === ".txt" || ext === ".md") {
    const base = fileName.slice(Math.max(fileName.lastIndexOf("/"), fileName.lastIndexOf("\\")) + 1, dot);
    return { records: [{ name: base.split("_").join(" ").trim(), text }], invalid: 0 };
  }
  throw new Error(`Unsupported file type: ${ext || "(no extension)"} - use a .json prompt export, or a .txt / .md file`);
}

export interface PromptImportPlan { toAdd: SavedPrompt[]; skipped: string[]; renamed: Array<[string, string]>; invalid: number; }

/** Decide what an import adds: identical prompts are skipped, a different one
 *  with a taken name becomes "Name (2)"; imported prompts are never built-in. */
export function planPromptImport(existing: SavedPrompt[], records: unknown[]): PromptImportPlan {
  const plan: PromptImportPlan = { toAdd: [], skipped: [], renamed: [], invalid: 0 };
  const keyOf = (name: string, category: string, text: string) =>
    name.trim().toLowerCase() + SEP + category.trim().toLowerCase() + SEP + text;
  const have = new Set(existing.map((p) => keyOf(p.name, p.category, p.text)));
  const names = existing.map((p) => p.name);
  for (const rec of records) {
    if (!rec || typeof rec !== "object" || Array.isArray(rec)) { plan.invalid += 1; continue; }
    const r = rec as Record<string, unknown>;
    if (typeof r.text !== "string" || !r.text.trim()) { plan.invalid += 1; continue; }
    const text = r.text.split("\r\n").join("\n").split("\r").join("\n");
    const name = String(r.name ?? "").trim() || "Imported prompt";
    const category = String(r.category ?? "").trim() || "General";
    if (have.has(keyOf(name, category, text))) { plan.skipped.push(name); continue; }
    const newName = uniqueName(name, names);
    if (newName !== name) plan.renamed.push([name, newName]);
    plan.toAdd.push({ id: promptId(newName), name: newName, text, builtin: false, category, favorite: r.favorite === true });
    have.add(keyOf(newName, category, text));
    names.push(newName);
  }
  return plan;
}

export function buildPromptExport(prompts: SavedPrompt[]): string {
  return JSON.stringify({
    mico360_prompts: 1,
    prompts: prompts.map((p) => ({ name: p.name, category: p.category, favorite: p.favorite, text: p.text })),
  }, null, 2);
}
