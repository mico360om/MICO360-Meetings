/** Markdown toolbar edits on a text + selection (pure, so they are unit-tested). */
export interface Sel { start: number; end: number; }
export interface Edit { text: string; sel: Sel; }

const lineStart = (t: string, i: number) => t.lastIndexOf("\n", Math.max(0, i - 1)) + 1;

export function wrap(text: string, sel: Sel, mark: string, placeholder: string): Edit {
  const inner = text.slice(sel.start, sel.end) || placeholder;
  const out = text.slice(0, sel.start) + mark + inner + mark + text.slice(sel.end);
  return { text: out, sel: { start: sel.start + mark.length, end: sel.start + mark.length + inner.length } };
}

/** Put `prefix` at the start of every selected line (once). */
export function prefixLines(text: string, sel: Sel, prefix: string): Edit {
  const a = lineStart(text, sel.start);
  const block = text.slice(a, sel.end);
  const lines = block.split("\n").map((l) => (l.startsWith(prefix) ? l : prefix + l.replace(/^(#+ |- )/, "")));
  const changed = lines.join("\n");
  return { text: text.slice(0, a) + changed + text.slice(sel.end), sel: { start: sel.start + prefix.length, end: a + changed.length } };
}

export const TABLE_TEMPLATE = "| Task | Owner | Deadline | Status |\n| --- | --- | --- | --- |\n|  |  |  | Pending |\n";

export function insertBlock(text: string, sel: Sel, block: string): Edit {
  const before = text.slice(0, sel.start);
  const pad = before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : "";
  const out = before + pad + block + text.slice(sel.end);
  const at = before.length + pad.length + block.length;
  return { text: out, sel: { start: at, end: at } };
}
