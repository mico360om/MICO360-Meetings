/** Offline speaker identification (port of desktop core/diarization.py, M38):
 *  each segment's voice is its mean MFCC without c0 (loudness), normalised per
 *  coefficient over the whole recording; segments are compared relative to
 *  their standard error and grouped by average-linkage clustering. */
import { CancelledError } from "./pipeline";

const SR = 16000;
const N_MFCC = 13;
const N_MELS = 26;
const FRAME = 400;
const HOP = 160;
const FFT = 512;
const FRAMES_PER_SECOND = SR / HOP;
const MAX_CLUSTER_ITEMS = 1200;
export const VOICE_THRESHOLD = 0.05;

export interface Segment { start: number; end: number; }

// ---- WAV -------------------------------------------------------------------------
export function decodeWav16(bytes: Uint8Array): { pcm: Float32Array; rate: number } {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
  if (bytes.length < 12 || tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("Not a WAV file.");
  let o = 12;
  let rate = 0, channels = 1, bits = 16;
  while (o + 8 <= bytes.length) {
    const id = tag(o);
    const size = v.getUint32(o + 4, true);
    if (id === "fmt ") { channels = v.getUint16(o + 10, true) || 1; rate = v.getUint32(o + 12, true); bits = v.getUint16(o + 22, true); }
    if (id === "data") {
      if (bits !== 16) throw new Error("Only 16-bit WAV is supported.");
      const len = Math.min(size, bytes.length - o - 8);
      const frames = Math.floor(len / 2 / channels);
      const pcm = new Float32Array(frames);
      for (let i = 0; i < frames; i++) {
        let s = 0;
        for (let c = 0; c < channels; c++) s += v.getInt16(o + 8 + (i * channels + c) * 2, true);
        pcm[i] = s / channels / 32768;
      }
      return { pcm, rate };
    }
    o += 8 + size + (size % 2);
  }
  throw new Error("The WAV file has no audio data.");
}

// ---- MFCC ------------------------------------------------------------------------
const WIN = Float64Array.from({ length: FRAME }, (_x, n) => 0.54 - 0.46 * Math.cos((2 * Math.PI * n) / (FRAME - 1)));

const MEL_FB: Float64Array[] = (() => {
  const hz2mel = (f: number) => 2595 * Math.log10(1 + f / 700);
  const mel2hz = (m: number) => 700 * (10 ** (m / 2595) - 1);
  const low = hz2mel(0), high = hz2mel(SR / 2);
  const pts = Array.from({ length: N_MELS + 2 }, (_x, i) => mel2hz(low + ((high - low) * i) / (N_MELS + 1)));
  const bins = pts.map((p) => Math.floor(((FFT + 1) * p) / SR));
  const fb: Float64Array[] = [];
  for (let m = 1; m <= N_MELS; m++) {
    const row = new Float64Array(FFT / 2 + 1);
    const a = bins[m - 1], b = bins[m], c = bins[m + 1];
    for (let k = a; k < b; k++) if (b > a) row[k] = (k - a) / (b - a);
    for (let k = b; k < c; k++) if (c > b) row[k] = (c - k) / (c - b);
    fb.push(row);
  }
  return fb;
})();

const DCT: Float64Array[] = Array.from({ length: N_MFCC }, (_x, k) =>
  Float64Array.from({ length: N_MELS }, (_y, n) => Math.cos(((Math.PI * k) / N_MELS) * (n + 0.5))));

// radix-2 FFT tables for 512 points
const BITREV = (() => {
  const r = new Uint16Array(FFT);
  const nBits = Math.log2(FFT);
  for (let i = 0; i < FFT; i++) {
    let x = i, y = 0;
    for (let b = 0; b < nBits; b++) { y = (y << 1) | (x & 1); x >>= 1; }
    r[i] = y;
  }
  return r;
})();
const COS = Float64Array.from({ length: FFT / 2 }, (_x, i) => Math.cos((-2 * Math.PI * i) / FFT));
const SIN = Float64Array.from({ length: FFT / 2 }, (_x, i) => Math.sin((-2 * Math.PI * i) / FFT));
const RE = new Float64Array(FFT);
const IM = new Float64Array(FFT);
const POWER = new Float64Array(FFT / 2 + 1);
const MEL = new Float64Array(N_MELS);

function powerSpectrum(frame: Float64Array): Float64Array {
  RE.fill(0);
  IM.fill(0);
  for (let i = 0; i < FRAME; i++) RE[BITREV[i]] = frame[i];
  for (let size = 2; size <= FFT; size <<= 1) {
    const half = size >> 1, step = FFT / size;
    for (let s = 0; s < FFT; s += size) {
      for (let k = 0; k < half; k++) {
        const wr = COS[k * step], wi = SIN[k * step];
        const a = s + k, b = a + half;
        const tr = RE[b] * wr - IM[b] * wi, ti = RE[b] * wi + IM[b] * wr;
        RE[b] = RE[a] - tr; IM[b] = IM[a] - ti; RE[a] += tr; IM[a] += ti;
      }
    }
  }
  for (let k = 0; k <= FFT / 2; k++) POWER[k] = (RE[k] * RE[k] + IM[k] * IM[k]) / FFT;
  return POWER;
}

/** Add c1..c12 of every frame of a segment to `sum` / `sumSq`; returns the frame count. */
function segmentMfcc(sig: Float32Array, sum: Float64Array, sumSq: Float64Array): number {
  if (sig.length < FRAME) return 0;
  const pre = new Float64Array(sig.length);
  pre[0] = sig[0];
  for (let i = 1; i < sig.length; i++) pre[i] = sig[i] - 0.97 * sig[i - 1];
  const nFrames = 1 + Math.floor((sig.length - FRAME) / HOP);
  const frame = new Float64Array(FRAME);
  for (let f = 0; f < nFrames; f++) {
    const off = f * HOP;
    for (let i = 0; i < FRAME; i++) frame[i] = pre[off + i] * WIN[i];
    const p = powerSpectrum(frame);
    for (let m = 0; m < N_MELS; m++) {
      let s = 0;
      const row = MEL_FB[m];
      for (let k = 0; k <= FFT / 2; k++) s += p[k] * row[k];
      MEL[m] = Math.log(Math.max(s, 1e-10));
    }
    for (let k = 1; k < N_MFCC; k++) {
      let c = 0;
      const row = DCT[k];
      for (let m = 0; m < N_MELS; m++) c += MEL[m] * row[m];
      sum[k - 1] += c;
      sumSq[k - 1] += c * c;
    }
  }
  return nFrames;
}

export function voiceStats(pcm: Float32Array, segments: Segment[]): { M: Float64Array[]; N: number[] } {
  const D = N_MFCC - 1;
  const means: Float64Array[] = [];
  const N: number[] = [];
  const s1 = new Float64Array(D), s2 = new Float64Array(D);
  let total = 0;
  for (const seg of segments) {
    const a = Math.trunc(Math.max(0, seg.start) * SR);
    const b = Math.trunc(Math.max(seg.start, seg.end) * SR);
    const sum = new Float64Array(D), sq = new Float64Array(D);
    const n = segmentMfcc(pcm.subarray(Math.min(a, pcm.length), Math.min(b, pcm.length)), sum, sq);
    means.push(n ? sum.map((x) => x / n) : new Float64Array(D));
    N.push(n);
    for (let j = 0; j < D; j++) { s1[j] += sum[j]; s2[j] += sq[j]; }
    total += n;
  }
  if (!total) return { M: segments.map(() => new Float64Array(D)), N: segments.map(() => 0) };
  const mu = s1.map((x) => x / total);
  const sd = s2.map((x, j) => Math.sqrt(Math.max(x / total - mu[j] * mu[j], 1e-12)));
  return { M: means.map((m) => m.map((x, j) => (x - mu[j]) / sd[j])), N };
}

/** RMS difference of normalised means divided by its standard error, per second of speech. */
function distances(M: Float64Array[], N: number[]): Float64Array[] {
  const n = M.length, D = M[0]?.length ?? 1;
  const sq = M.map((r) => r.reduce((s, x) => s + x * x, 0));
  return Array.from({ length: n }, (_x, i) => {
    const row = new Float64Array(n);
    for (let j = 0; j < n; j++) {
      let dot = 0;
      for (let k = 0; k < D; k++) dot += M[i][k] * M[j][k];
      const diff = Math.sqrt(Math.max(sq[i] + sq[j] - 2 * dot, 0) / D);
      row[j] = diff / Math.sqrt(1 / N[i] + 1 / N[j]) / FRAMES_PER_SECOND;
    }
    return row;
  });
}

/** Average-linkage agglomerative clustering on a distance matrix (Lance–Williams). */
function agglomerative(Dm: Float64Array[], threshold: number, maxK: number, signal?: AbortSignal): number[] {
  const n = Dm.length;
  if (n === 0) return [];
  if (n === 1) return [0];
  const D = Dm.map((r) => Float64Array.from(r));
  for (let i = 0; i < n; i++) D[i][i] = Infinity;
  const sizes = new Float64Array(n).fill(1);
  const members: number[][] = Array.from({ length: n }, (_x, i) => [i]);
  let k = n;
  while (k > 1) {
    if (signal?.aborted) throw new CancelledError("Transcription cancelled.");
    let bi = 0, bj = 1, best = Infinity;
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (D[i][j] < best) { best = D[i][j]; bi = i; bj = j; }
    const i = Math.min(bi, bj), j = Math.max(bi, bj);
    if (best > threshold && k <= maxK) break;
    const si = sizes[i], sj = sizes[j];
    for (let c = 0; c < n; c++) { const v = (si * D[i][c] + sj * D[j][c]) / (si + sj); D[i][c] = v; D[c][i] = v; }
    D[i][i] = Infinity;
    for (let c = 0; c < n; c++) { D[j][c] = Infinity; D[c][j] = Infinity; }
    sizes[i] = si + sj;
    members[i].push(...members[j]);
    members[j] = [];
    k -= 1;
    if (best > threshold && k <= maxK) break;
  }
  const labels = new Array<number>(n).fill(0);
  let cid = 0;
  for (const g of members) {
    if (!g.length) continue;
    for (const idx of g) labels[idx] = cid;
    cid += 1;
  }
  return labels;
}

function clusterVoices(M: Float64Array[], N: number[], durations: number[], threshold: number, maxK: number, signal?: AbortSignal): number[] {
  const n = M.length;
  const labels = new Array<number>(n).fill(0);
  const real = N.map((x, i) => (x > 0 ? i : -1)).filter((i) => i >= 0);
  if (real.length <= 1) return labels;
  let keep = real;
  if (real.length > MAX_CLUSTER_ITEMS) {
    keep = [...real].sort((a, b) => durations[b] - durations[a] || a - b).slice(0, MAX_CLUSTER_ITEMS).sort((a, b) => a - b);
  }
  const sub = agglomerative(distances(keep.map((i) => M[i]), keep.map((i) => N[i])), threshold, maxK, signal);
  keep.forEach((idx, k) => { labels[idx] = sub[k]; });
  if (keep.length < real.length) {
    const kset = new Set(keep);
    const K = Math.max(...sub) + 1;
    const D = M[0].length;
    const cent: Float64Array[] = [];
    const cn: number[] = [];
    for (let c = 0; c < K; c++) {
      const acc = new Float64Array(D);
      let w = 0;
      keep.forEach((idx, k) => {
        if (sub[k] !== c) return;
        for (let j = 0; j < D; j++) acc[j] += M[idx][j] * N[idx];
        w += N[idx];
      });
      cent.push(acc.map((x) => x / w));
      cn.push(w);
    }
    for (const idx of real) {
      if (kset.has(idx)) continue;
      const d = distances([M[idx], ...cent], [N[idx], ...cn])[0].slice(1);
      let best = 0;
      for (let c = 1; c < d.length; c++) if (d[c] < d[best]) best = c;
      labels[idx] = best;
    }
  }
  let last = labels[real[0]];
  for (let i = 0; i < n; i++) { if (N[i] > 0) last = labels[i]; else labels[i] = last; }
  return labels;
}

/** Speaker index (0-based, in order of first appearance) for each segment. */
export async function diarize(pcm: Float32Array, segments: Segment[],
  opts: { maxSpeakers?: number; threshold?: number; signal?: AbortSignal } = {}): Promise<number[]> {
  if (segments.length <= 1 || !pcm.length) return segments.map(() => 0);
  await Promise.resolve();                               // let the UI breathe before a long run
  const { M, N } = voiceStats(pcm, segments);
  if (opts.signal?.aborted) throw new CancelledError("Transcription cancelled.");
  const durations = segments.map((s) => Math.max(0, s.end - s.start));
  const labels = clusterVoices(M, N, durations, opts.threshold ?? VOICE_THRESHOLD, opts.maxSpeakers ?? 4, opts.signal);
  const remap = new Map<number, number>();
  return labels.map((l) => { if (!remap.has(l)) remap.set(l, remap.size); return remap.get(l)!; });
}

/** Write "Speaker N" onto each segment; returns the number of speakers. */
export function applySpeakers(segments: Array<{ speaker?: string }>, labels: number[]): number {
  segments.forEach((s, i) => { s.speaker = `Speaker ${(labels[i] ?? 0) + 1}`; });
  return labels.length ? Math.max(...labels) + 1 : 0;
}
