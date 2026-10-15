/** Transcript cleaning and chunking (port of desktop core/cleaning.py). */
import data from "./data/desktop.json";
import { WORD } from "./unicode";

const FILLERS: string[] = data.fillers;
const STUTTER: string[] = data.stutterWords;
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const F = `(?<![${WORD}'-])(?:${FILLERS.map(esc).join("|")})(?![${WORD}'-])`;
const COMMA_WRAPPED_RE = new RegExp(`,[ \\t]*${F}[ \\t]*,`, "giu");
const FILLER_RE = new RegExp(`${F}(?<p>[ \\t]*(?:\\.\\.\\.|…|[,.!?]))?(?<ws>[ \\t]*)`, "giu");
const REPEAT_WORD_RE = new RegExp(`(?<![${WORD}])(${STUTTER.join("|")})(?:[ \\t]+\\1(?![${WORD}]))+`, "giu");
const CAP = "\u0000";

/** Python str.splitlines(): every line-break kind; no trailing empty line. */
export function splitlines(text: string): string[] {
  if (!text) return [];
  const parts = text.split(new RegExp("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85" + String.fromCharCode(0x2028, 0x2029) + "]"));
  if (parts[parts.length - 1] === "") parts.pop();
  return parts;
}

function replaceMatches(text: string, re: RegExp, fn: (m: RegExpMatchArray) => string): string {
  let out = "";
  let pos = 0;
  for (const m of text.matchAll(re)) {
    out += text.slice(pos, m.index) + fn(m);
    pos = m.index! + m[0].length;
  }
  return out + text.slice(pos);
}

/** Remove hesitation sounds only and collapse function-word stutters. */
export function removeFillers(input: string): string {
  const src = input.replace(COMMA_WRAPPED_RE, " ");
  let text = replaceMatches(src, FILLER_RE, (m) => {
    const before = src.slice(0, m.index).replace(/[ \t]+$/, "");
    if (!before || "\n.!?…".includes(before[before.length - 1])) return CAP;
    const p = (m.groups?.p ?? "").trim();
    const keep = p === "." || p === "!" || p === "?" ? p : "";
    return keep + (m.groups?.ws ?? "");
  });
  text = text.replace(/\u0000[ \t]*([a-z])/g, (_m, c: string) => c.toUpperCase());
  text = text.split(CAP).join("");
  text = text.replace(REPEAT_WORD_RE, (_m, w: string) => w);
  text = text.replace(/[ \t]+([,.!?;:])/g, (_m, p: string) => p);
  text = text.replace(/,([.!?])/g, (_m, p: string) => p);
  text = text.replace(/[ \t]{2,}/g, " ");
  text = text.replace(/[ \t]+\n/g, "\n");
  text = text.replace(/^[ \t]+(?=\S)/gm, "");
  return text;
}

export function dedupLines(text: string): string {
  const out: string[] = [];
  let prev: string | null = null;
  for (const line of splitlines(text)) {
    const norm = line.trim().toLowerCase();
    if (norm && norm === prev) continue;
    out.push(line);
    prev = norm;
  }
  return out.join("\n");
}

export function cleanTranscript(input: string, removeFillerWords = true): string {
  let text = input.split("\r\n").join("\n").trim();
  text = dedupLines(text);
  if (removeFillerWords) text = removeFillers(text);
  text = text.replace(/\n{3,}/g, "\n\n");
  return text.trim();
}

// Sentence ends (Latin, Arabic ؟, Urdu ۔, Devanagari ।, CJK) and newlines.
const BOUNDARY_RE = /(?<=[.!?…؟۔।。！？])[ \t]+|[ \t]*\n\s*|(?<=[。！？])(?=\S)/g;

function splitSentences(text: string): string[] {
  const segs: string[] = [];
  let start = 0;
  for (const m of text.matchAll(BOUNDARY_RE)) {
    const end = m.index! + m[0].length;
    if (end <= start) continue;
    segs.push(text.slice(start, end));
    start = end;
  }
  if (start < text.length) segs.push(text.slice(start));
  return segs.filter((s) => s.trim());
}

function splitLong(seg: string, step: number, limit: number): string[] {
  const pieces: string[] = [];
  let pos = 0;
  const n = seg.length;
  while (pos < n) {
    if (n - pos <= step) { pieces.push(seg.slice(pos)); break; }
    const window = seg.slice(pos, pos + step + 1);
    const cut = Math.max(window.lastIndexOf(" "), window.lastIndexOf("\t"), window.lastIndexOf("\n"));
    let end: number;
    if (cut > 0) {
      end = pos + cut;
    } else {
      const m = /\s/.exec(seg.slice(pos + step, pos + limit + 1));
      end = m ? pos + step + m.index : pos + limit;
    }
    pieces.push(seg.slice(pos, end));
    pos = end;
  }
  return pieces;
}

/** Pack text into chunks <= maxChars on sentence boundaries, each seeded with
 *  ~overlapChars of the previous chunk's tail. */
export function chunkText(input: string, maxChars = 6000, overlapChars = 400): string[] {
  const text = input.trim();
  if (text.length <= maxChars) return text ? [text] : [];
  maxChars = Math.max(1, Math.trunc(maxChars));
  overlapChars = Math.max(0, Math.min(Math.trunc(overlapChars), Math.floor(maxChars / 2)));
  const step = Math.max(1, Math.min(maxChars, overlapChars || maxChars));
  const segments: string[] = [];
  for (const seg of splitSentences(text)) {
    if (seg.length > maxChars) segments.push(...splitLong(seg, step, maxChars));
    else segments.push(seg);
  }
  const chunks: string[] = [];
  let cur: string[] = [];
  let size = 0;
  let fresh = 0;
  for (const seg of segments) {
    const sLen = seg.length;
    if (size + sLen > maxChars && cur.length) {
      if (fresh) {
        chunks.push(cur.join("").trim());
        const tail: string[] = [];
        let tlen = 0;
        for (let k = cur.length - 1; k >= 0; k--) {
          if (tlen + cur[k].length > overlapChars) break;
          tail.unshift(cur[k]);
          tlen += cur[k].length;
        }
        cur = tail;
        size = tlen;
      }
      if (size + sLen > maxChars) { cur = []; size = 0; }
      fresh = 0;
    }
    cur.push(seg);
    size += sLen;
    fresh += 1;
  }
  if (cur.length && fresh) chunks.push(cur.join("").trim());
  return chunks.filter((c) => c);
}
