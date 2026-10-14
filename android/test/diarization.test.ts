import { describe, expect, it } from "vitest";
import fx from "./fixtures/diarization.json";
import { agglomerative, decodeWav16, diarize, voiceStats } from "../src/core/diarization";

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

/** The straightforward O(n^3) average-linkage clustering the desktop performs. */
function naive(Dm: Float64Array[], threshold: number, maxK: number): number[] {
  const n = Dm.length;
  if (n <= 1) return n ? [0] : [];
  const D = Dm.map((r) => Float64Array.from(r));
  for (let i = 0; i < n; i++) D[i][i] = Infinity;
  const sizes = new Float64Array(n).fill(1);
  const members: number[][] = Array.from({ length: n }, (_x, i) => [i]);
  let k = n;
  while (k > 1) {
    let bi = 0, bj = 1, best = Infinity;
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (D[i][j] < best) { best = D[i][j]; bi = i; bj = j; }
    const i = Math.min(bi, bj), j = Math.max(bi, bj);
    if (best > threshold && k <= maxK) break;
    const si = sizes[i], sj = sizes[j];
    for (let c = 0; c < n; c++) { const v = (si * D[i][c] + sj * D[j][c]) / (si + sj); D[i][c] = v; D[c][i] = v; }
    D[i][i] = Infinity;
    for (let c = 0; c < n; c++) { D[j][c] = Infinity; D[c][j] = Infinity; }
    sizes[i] = si + sj; members[i].push(...members[j]); members[j] = []; k -= 1;
    if (best > threshold && k <= maxK) break;
  }
  const labels = new Array<number>(n).fill(0);
  let cid = 0;
  for (const g of members) { if (!g.length) continue; for (const idx of g) labels[idx] = cid; cid += 1; }
  return labels;
}

describe("long recordings", () => {
  it("fast clustering gives exactly the desktop's clusters, ties included", () => {
    let seed = 7;
    const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    for (let trial = 0; trial < 40; trial++) {
      const n = 2 + Math.floor(rnd() * 60);
      const D = Array.from({ length: n }, () => new Float64Array(n));
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
        const v = trial % 3 === 0 ? Math.floor(rnd() * 4) / 4 : rnd();
        D[i][j] = v; D[j][i] = v;
      }
      const threshold = [0.05, 0.3, 0.6][trial % 3], maxK = 1 + (trial % 5);
      expect(agglomerative(D, threshold, maxK)).toEqual(naive(D, threshold, maxK));
    }
  });
  it("yields to the UI while it works and can be cancelled part-way", async () => {
    const pcm = new Float32Array(16000 * 60).map((_x, i) => Math.sin(i / 7) * 0.1 + ((i * 7919) % 13) / 1000);
    const segments = Array.from({ length: 60 }, (_x, i) => ({ start: i, end: i + 1 }));
    const ctl = new AbortController();
    setTimeout(() => ctl.abort(), 0);
    await expect(diarize(pcm, segments, { signal: ctl.signal })).rejects.toThrow(/cancelled/i);
  });
});
