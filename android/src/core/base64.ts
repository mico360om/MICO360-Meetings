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

/** Little-endian 16-bit PCM sent as base64 (audio-studio's native stream format) as floats in [-1, 1). */
export function pcm16FromBase64(text: string): Float32Array {
  const bytes = base64Bytes(text);
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(Math.floor(bytes.length / 2));
  for (let i = 0; i < out.length; i++) out[i] = v.getInt16(i * 2, true) / 32768;
  return out;
}
