/** Decode base64 text to bytes, ignoring whitespace and missing padding. */
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const INDEX = new Map([...ALPHABET].map((ch, i) => [ch, i]));

export function base64Bytes(text: string): Uint8Array {
  const out: number[] = [];
  let acc = 0, bits = 0;
  for (const ch of text) {
    const v = INDEX.get(ch === "-" ? "+" : ch === "_" ? "/" : ch);
    if (v === undefined) continue;          // whitespace, "=" padding, anything else
    acc = (acc << 6) | v;
    bits += 6;
    if (bits >= 8) { bits -= 8; out.push((acc >> bits) & 0xff); }
  }
  return Uint8Array.from(out);
}
