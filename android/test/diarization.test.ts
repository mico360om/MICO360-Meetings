import { describe, expect, it } from "vitest";
import fx from "./fixtures/diarization.json";
import { decodeWav16, diarize, voiceStats } from "../src/core/diarization";

function pcmOf(b64: string): Float32Array {
  const bytes = Buffer.from(b64, "base64");
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(bytes.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = v.getInt16(i * 2, true) / 32768;
  return out;
}
const segs = (s: number[][]) => s.map(([start, end]) => ({ start, end }));

describe("parity with desktop diarization.py", () => {
  for (const name of ["two", "one"] as const) {
    it(`${name}: same labels and voice statistics`, async () => {
      const c = fx[name];
      const pcm = pcmOf(c.pcm16);
      expect(await diarize(pcm, segs(c.segments))).toEqual(c.labels);
      const { M, N } = voiceStats(pcm, segs(c.segments));
      expect(N).toEqual(c.N);
      M.forEach((row, i) => row.forEach((v, j) => expect(Math.abs(v - c.M[i][j])).toBeLessThan(0.02)));
    });
  }
  it("one segment or none: one speaker, nothing to compute", async () => {
    expect(await diarize(new Float32Array(16000), [{ start: 0, end: 1 }])).toEqual([0]);
    expect(await diarize(new Float32Array(0), [])).toEqual([]);
  });
});

it("reads a 16-bit mono WAV", () => {
  const n = 4;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, n * 2, true);
  v.setInt16(44, 16384, true);
  const { pcm, rate } = decodeWav16(new Uint8Array(buf));
  expect(rate).toBe(16000);
  expect(pcm[0]).toBeCloseTo(0.5);
});
