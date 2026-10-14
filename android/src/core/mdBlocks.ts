/** A small Markdown block parser — the subset the minutes use (port of desktop
 *  export/md_blocks.py): headings, **key:** value lines, bullets, numbered
 *  lists, pipe tables and paragraphs. */

export type BlockKind = "h1" | "h2" | "h3" | "kv" | "bullet" | "olist" | "table" | "para";

export interface Block {
  kind: BlockKind;
  text: string;
  key: string;
  items: string[];
  headers: string[];
  rows: string[][];
  level: number;
  start: number;
}

function block(kind: BlockKind, p: Partial<Block> = {}): Block {
  return { kind, text: "", key: "", items: [], headers: [], rows: [], level: 0, start: 1, ...p };
}

// C0 controls except \t \n \r, DEL, U+FFFE/U+FFFF and LONE surrogates (a valid
// pair is an emoji and must survive).
const CTRL_RE =
  /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;

export function cleanText(s: unknown): string {
  return (s === null || s === undefined ? "" : String(s)).replace(CTRL_RE, "");
}

const BOLD_RE = /\*\*([^\n]+?)\*\*/g;
const KV_RE = /^\*\*([^\n]+?):\*\*\s*([^\n]*)$/;
const HEADING_RE = /^(#{1,6})\s+([^\n]*?)(?:\s+#+)?\s*$/;
// Python's \d also matches other decimal digits; ASCII, Arabic-Indic,
// Extended Arabic-Indic and Devanagari cover the app's languages.
const DIGIT = "[0-9٠-٩۰-۹०-९]";
const OLIST_RE = new RegExp(`^(${DIGIT}{1,4})[.)]\\s+([^\\n]*)$`);
const BULLETS = ["- ", "* ", "• ", "+ "];
const HR_RE = /^(?:-{3,}|\*{3,}|_{3,})$/;
const TABLE_SEP_RE = /^\s*\|?[\s:|-]+\|?\s*$/;
const INDEX_HEADERS = new Set(["#", "no", "no.", "nr", "s/n", "sn", "sr", "sr.", "م", "رقم", "الرقم"]);

function toInt(digits: string): number {
  let n = 0;
  for (const ch of digits) {
    const cp = ch.codePointAt(0)!;
    const zero = cp >= 0x0966 ? 0x0966 : cp >= 0x06f0 ? 0x06f0 : cp >= 0x0660 ? 0x0660 : 0x30;
    n = n * 10 + (cp - zero);
  }
  return n;
}

const startsWithAny = (s: string, prefixes: string[]) => prefixes.some((p) => s.startsWith(p));

export function runs(text: string): Array<[string, boolean]> {
  const out: Array<[string, boolean]> = [];
  let pos = 0;
  for (const m of text.matchAll(BOLD_RE)) {
    const start = m.index!;
    if (start > pos) out.push([text.slice(pos, start), false]);
    out.push([m[1], true]);
    pos = start + m[0].length;
  }
  if (pos < text.length) out.push([text.slice(pos), false]);
  return out.length ? out : [["", false]];
}

export function plain(text: string): string {
  return runs(text).map(([t]) => t).join("");
}

function isTableSep(line: string): boolean {
  return TABLE_SEP_RE.test(line) && line.includes("-");
}

/** Split a row on unescaped pipes outside `code` spans; "\|" is a literal pipe. */
function splitCells(line: string): string[] {
  const s = line.trim();
  let cells: string[] = [];
  let buf = "";
  let inCode = false;
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && s[i + 1] === "|") {
      buf += "|";
      i += 2;
      continue;
    }
    if (ch === "`") {
      // only a PAIRED backtick opens a code span ("Don`t" is a literal)
      if (inCode) inCode = false;
      else if (s.indexOf("`", i + 1) !== -1) inCode = true;
    }
    if (ch === "|" && !inCode) {
      cells.push(buf);
      buf = "";
    } else {
      buf += ch;
    }
    i += 1;
  }
  cells.push(buf);
  if (s.startsWith("|") && cells.length) cells = cells.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|") && cells.length) cells = cells.slice(0, -1);
  return cells.map((c) => c.trim());
}

/** Keep a row aligned with its headers when it has EXTRA cells. */
function fitRow(row: string[], headers: string[]): string[] {
  const n = headers.length;
  if (n === 0 || row.length <= n) return row;
  const r = [...row];
  while (r.length > n && !r[r.length - 1]) r.pop();
  const extra = r.length - n;
  if (extra <= 0) return r;
  const target = n > 1 && INDEX_HEADERS.has(headers[0].trim().toLowerCase()) ? 1 : 0;
  const merged = r.slice(target, target + extra + 1).join(" | ");
  return [...r.slice(0, target), merged, ...r.slice(target + extra + 1)];
}

function isTableStart(lines: string[], i: number): boolean {
  const s = lines[i].trim();
  return (
    s.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]) &&
    !startsWithAny(s, ["#", ...BULLETS]) && !isTableSep(s)
  );
}

function isSpecial(lines: string[], i: number): boolean {
  const s = lines[i].trim();
  return (
    !s || startsWithAny(s, ["#", "|", ...BULLETS]) || KV_RE.test(s) || OLIST_RE.test(s) ||
    HR_RE.test(s) || isTableStart(lines, i)
  );
}

export function parse(markdown: string): Block[] {
  const lines = cleanText(markdown ?? "").split("\r\n").join("\n").split("\r").join("\n").split("\n");
  const blocks: Block[] = [];
  const n = lines.length;
  let i = 0;
  while (i < n) {
    const stripped = lines[i].trim();
    if (!stripped) { i += 1; continue; }

    if (isTableStart(lines, i)) {
      const sp = splitCells(stripped);
      const headers = sp.length ? sp : [""];
      const rows: string[][] = [];
      i += 2;
      while (i < n && lines[i].includes("|") && !isTableSep(lines[i])) {
        rows.push(fitRow(splitCells(lines[i]), headers));
        i += 1;
      }
      blocks.push(block("table", { headers, rows }));
      continue;
    }

    const hm = HEADING_RE.exec(stripped);
    if (hm) {
      const level = hm[1].length;
      const kind: BlockKind = level === 1 ? "h1" : level === 2 ? "h2" : "h3";
      blocks.push(block(kind, { text: hm[2].trim(), level }));
      i += 1;
      continue;
    }

    if (HR_RE.test(stripped)) { i += 1; continue; }

    const kv = KV_RE.exec(stripped);
    if (kv) {
      blocks.push(block("kv", { key: kv[1].trim(), text: kv[2].trim() }));
      i += 1;
      continue;
    }

    if (startsWithAny(stripped, BULLETS)) {
      const items: string[] = [];
      while (i < n && startsWithAny(lines[i].trim(), BULLETS)) {
        items.push(lines[i].trim().slice(2).trim());
        i += 1;
      }
      blocks.push(block("bullet", { items }));
      continue;
    }

    const om = OLIST_RE.exec(stripped);
    if (om) {
      const start = toInt(om[1]);
      const items: string[] = [];
      while (i < n) {
        const m2 = OLIST_RE.exec(lines[i].trim());
        if (!m2) break;
        items.push(m2[2].trim());
        i += 1;
      }
      blocks.push(block("olist", { items, start }));
      continue;
    }

    // Paragraph: consume the current line first (a stray '|' line can't hang
    // the loop), then gather lines until a blank line or another block.
    const para = [stripped];
    i += 1;
    while (i < n && !isSpecial(lines, i)) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push(block("para", { text: para.join(" ") }));
  }
  return blocks;
}
