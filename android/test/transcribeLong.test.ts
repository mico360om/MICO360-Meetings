import { describe, expect, it } from "vitest";
import { CancelledError } from "../src/core/pipeline";
import fx from "./fixtures/diarization.json";
import { diarize, diarizeStats, segmentStats } from "../src/core/diarization";
import { type AudioSource, type Recognizer, speakerText, timestampedText, transcribeAudio } from "../src/core/transcribeLong";

function pcmOf(b64: string): Float32Array {
  const bytes = Buffer.from(b64, "base64");
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Float32Array.from({ length: bytes.length / 2 }, (_x, i) => v.getInt16(i * 2, true) / 32768);
}

/** A source that hands out `pcm` in chunks of `chunk` samples. */
function source(pcm: Float32Array, chunk = 16000): AudioSource {
  return { async stream(onChunk) {
    for (let i = 0; i < pcm.length; i += chunk) await onChunk(pcm.subarray(i, Math.min(pcm.length, i + chunk)));
    return { durationMs: (pcm.length / 16000) * 1000 };
  } };
}

/** A recogniser that emits one 2-second segment per 2 seconds of audio it is given. */
function fakeRecognizer(log: number[]): Recognizer {
  return { async transcribe(samples) {
    log.push(samples.length);
    const secs = samples.length / 16000;
    const segments = [];
    for (let t = 0; t + 0.5 < secs; t += 2) segments.push({ text: ` word${Math.round(t)} `, t0: Math.round(t * 100), t1: Math.round(Math.min(secs, t + 2) * 100) });
    return { language: "en", segments };
  } };
}

describe("streaming speaker statistics", () => {
  it("statistics per segment give the same speakers as the whole-file path", async () => {
    const c = fx.two;
    const pcm = pcmOf(c.pcm16);
    const stats = c.segments.map(([a, b]) => segmentStats(pcm, a, b));
    expect(diarizeStats(stats, c.segments.map(([a, b]) => b - a))).toEqual(await diarize(pcm, c.segments.map(([start, end]) => ({ start, end }))));
  });
});

describe("windowed transcription", () => {
  it("never hands the recogniser more than one window, and keeps time continuous", async () => {
    const log: number[] = [];
    const pcm = new Float32Array(16000 * 25);
    const t = await transcribeAudio(source(pcm), fakeRecognizer(log), { windowSeconds: 10 });
    expect(Math.max(...log)).toBeLessThanOrEqual(16000 * 10);
    expect(log).toHaveLength(3);
    const starts = t.segments.map((s) => s.start);
    expect(starts).toEqual([...starts].sort((a, b) => a - b));
    expect(t.segments.find((s) => s.text === "word0" && s.start === 10)).toBeTruthy();
    expect(t.durationMs).toBe(25000);
    expect(t.text.startsWith("word0 word2")).toBe(true);
  });
  it("identifies speakers across windows", async () => {
    const c = fx.two;
    const pcm = pcmOf(c.pcm16);
    const rec: Recognizer = { async transcribe(samples) {
      return { language: "en", segments: c.segments.map(([a, b], i) => ({ text: `turn ${i}`, t0: Math.round(a * 100), t1: Math.round(b * 100) }))
        .filter((s) => s.t1 / 100 <= samples.length / 16000 + 0.01) };
    } };
    const t = await transcribeAudio(source(pcm), rec, { windowSeconds: 600, diarize: true });
    expect(t.speakers).toBe(2);
    expect(t.segments.map((s) => s.speaker)).toEqual(c.labels.map((l: number) => `Speaker ${l + 1}`));
    expect(speakerText(t.segments).split("\n")[0]).toBe("[Speaker 1] turn 0");
    expect(speakerText(t.segments, { "Speaker 1": "Ali" }).split("\n")[0]).toBe("[Ali] turn 0");
    expect(timestampedText(t.segments).split("\n")[1]).toMatch(/^\[00:00:0\d\] Speaker 2: turn 1$/);
  });
  it("cancel stops between windows", async () => {
    const ctl = new AbortController();
    const rec: Recognizer = { async transcribe() { ctl.abort(); return { language: "en", segments: [] }; } };
    await expect(transcribeAudio(source(new Float32Array(16000 * 30)), rec, { windowSeconds: 10, signal: ctl.signal })).rejects.toThrow(/cancelled/i);
  });
  it("silence-only audio gives an empty transcript, not an error", async () => {
    const t = await transcribeAudio(source(new Float32Array(16000 * 3)), { async transcribe() { return { language: "ar", segments: [] }; } }, {});
    expect(t.text).toBe("");
    expect(t.language).toBe("ar");
  });
});

describe("cancel and progress on a phone", () => {
  it("a source that finishes normally after cancel still ends as cancelled, never a partial transcript", async () => {
    const ctl = new AbortController();
    const src: AudioSource = { async stream(onChunk) {
      await onChunk(new Float32Array(16000 * 12));
      ctl.abort();
      await onChunk(new Float32Array(1000));          // the decoder delivers one more small chunk, then resolves
      return { durationMs: 12000 };
    } };
    const rec: Recognizer = { async transcribe() { return { language: "en", segments: [{ text: "hi", t0: 0, t1: 100 }] }; } };
    await expect(transcribeAudio(src, rec, { windowSeconds: 10, signal: ctl.signal })).rejects.toBeInstanceOf(CancelledError);
  });
  it("a decoder that wraps callback errors still reports a cancel as a cancel", async () => {
    const ctl = new AbortController();
    const src: AudioSource = { async stream(onChunk) {
      try { await onChunk(new Float32Array(16000)); ctl.abort(); await onChunk(new Float32Array(16000)); }
      catch (e) { throw new Error(`Audio stream failed: ${(e as Error).message}`); }
      return { durationMs: 2000 };
    } };
    const rec: Recognizer = { async transcribe() { return { language: "en", segments: [] }; } };
    await expect(transcribeAudio(src, rec, { signal: ctl.signal })).rejects.toBeInstanceOf(CancelledError);
  });
  it("progress moves while a window is being recognised", async () => {
    const seen: number[] = [];
    const rec: Recognizer = { async transcribe(_s, _l, _sig, onProgress) { onProgress?.(0.5); return { language: "en", segments: [] }; } };
    await transcribeAudio(source(new Float32Array(16000 * 20)), rec, { windowSeconds: 10, totalMs: 20000, progress: (f) => seen.push(f) });
    expect(seen.slice(0, 2)).toEqual([0.25, 0.5]);
  });
});
