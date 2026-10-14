/** Repair the size fields of a PCM WAV whose writer stopped before finalising
 *  (app killed mid-recording). Works from the header bytes and the file size
 *  only, so a long recording is patched in place, never loaded into memory. */
export interface WavFix { offset: number; value: number; }

/** The 32-bit little-endian fields to rewrite ([] when the file is already
 *  right), or null when `head` (the first bytes of the file) is not a WAV. */
export function wavHeaderFixes(head: Uint8Array, fileSize: number): WavFix[] | null {
  if (head.length < 12 || fileSize < 44) return null;
  const v = new DataView(head.buffer, head.byteOffset, head.byteLength);
  const tag = (o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  let o = 12;
  while (o + 8 <= head.length) {
    const size = v.getUint32(o + 4, true);
    if (tag(o) === "data") {
      const actual = fileSize - o - 8;
      const whole = actual - (actual % 2);
      const fixes: WavFix[] = [];
      if (v.getUint32(4, true) !== fileSize - 8) fixes.push({ offset: 4, value: fileSize - 8 });
      if (size !== whole) fixes.push({ offset: o + 4, value: whole });
      return fixes;
    }
    if (o + 8 + size > fileSize) return null;
    o += 8 + size + (size % 2);
  }
  return null;
}

/** Apply fixes to bytes in memory (tests, small files). */
export function applyWavFixes(bytes: Uint8Array, fixes: WavFix[]): Uint8Array {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (const f of fixes) v.setUint32(f.offset, f.value, true);
  return bytes;
}

/** A fix as the four bytes to write at its offset. */
export function fixBytes(f: WavFix): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, f.value, true);
  return out;
}
