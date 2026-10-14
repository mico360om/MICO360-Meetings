/** Repair the size fields of a PCM WAV whose writer stopped before finalising
 *  (app killed mid-recording). Returns the same bytes when they are already
 *  right, corrected bytes otherwise, or null when this is not a WAV. */
export function repairWavHeader(bytes: Uint8Array): Uint8Array | null {
  if (bytes.length < 44) return null;
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  let o = 12;
  while (o + 8 <= bytes.length) {
    const id = tag(o);
    const size = v.getUint32(o + 4, true);
    if (id === "data") {
      const actual = bytes.length - o - 8;
      const whole = actual - (actual % 2);
      if (size === whole && v.getUint32(4, true) === bytes.length - 8) return bytes;
      const out = bytes.slice();
      const w = new DataView(out.buffer);
      w.setUint32(o + 4, whole, true);
      w.setUint32(4, out.length - 8, true);
      return out;
    }
    if (o + 8 + size > bytes.length) return null;
    o += 8 + size + (size % 2);
  }
  return null;
}
