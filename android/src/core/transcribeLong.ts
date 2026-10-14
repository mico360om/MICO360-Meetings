/** On-device transcription of any length: 16 kHz mono audio is processed in
 *  windows (never the whole recording in memory), timestamps are made
 *  continuous, and speakers are identified across windows. */
import { diarizeStats, segmentStats, type SegmentStats, yieldToUi } from "./diarization";
import { CancelledError, type ProgressFn } from "./pipeline";

export const SAMPLE_RATE = 16000;
export const WINDOW_SECONDS = 600;

export interface Recognizer {
  /** t0/t1 in hundredths of a second from the start of `samples` (whisper.cpp). */
  transcribe(samples: Float32Array, language: string, signal?: AbortSignal):
    Promise<{ language: string; segments: Array<{ text: string; t0: number; t1: number }> }>;
}
export interface AudioSource {
  /** Deliver the audio as 16 kHz mono float32 chunks, in order. */
  stream(onChunk: (samples: Float32Array) => Promise<void>, signal?: AbortSignal): Promise<{ durationMs: number }>;
}
export interface TranscriptSegment { start: number; end: number; text: string; speaker?: string; }
export interface Transcript { text: string; language: string; segments: TranscriptSegment[]; speakers: number; durationMs: number; }

export interface TranscribeOptions {
  language?: string; windowSeconds?: number; diarize?: boolean; progress?: ProgressFn; totalMs?: number; signal?: AbortSignal;
}

export async function transcribeAudio(source: AudioSource, recognizer: Recognizer, o: TranscribeOptions = {}): Promise<Transcript> {
  const windowSamples = Math.max(1, Math.round((o.windowSeconds ?? WINDOW_SECONDS) * SAMPLE_RATE));
  let buf = new Float32Array(windowSamples);
  let fill = 0;
  let offsetSec = 0;
  let language = o.language && o.language !== "auto" ? o.language : "";
  const segments: TranscriptSegment[] = [];
  const stats: SegmentStats[] = [];
  const check = () => { if (o.signal?.aborted) throw new CancelledError("Transcription cancelled."); };

  const flush = async () => {
    if (fill < SAMPLE_RATE / 2) { offsetSec += fill / SAMPLE_RATE; fill = 0; return; }   // < 0.5 s: nothing to hear
    check();
    const window = buf.subarray(0, fill);
    const res = await recognizer.transcribe(window, language || o.language || "auto", o.signal);
    if (!language && res.language) language = res.language;
    let n = 0;
    for (const s of res.segments) {
      const text = (s.text ?? "").trim();
      if (!text) continue;
      const a = s.t0 / 100, b = Math.max(s.t0, s.t1) / 100;
      segments.push({ start: offsetSec + a, end: offsetSec + b, text });
      if (o.diarize) {
        if (++n % 25 === 0) await yieldToUi(o.signal);      // MFCCs are slow on Hermes: keep the UI alive
        stats.push(segmentStats(window, a, b));
      }
    }
    offsetSec += fill / SAMPLE_RATE;
    fill = 0;
    buf = new Float32Array(windowSamples);
    if (o.totalMs) o.progress?.(Math.min(0.99, (offsetSec * 1000) / o.totalMs), `Transcribed ${formatTime(offsetSec)}`);
    check();
  };

  const result = await source.stream(async (chunk) => {
    check();
    let i = 0;
    while (i < chunk.length) {
      const n = Math.min(chunk.length - i, windowSamples - fill);
      buf.set(chunk.subarray(i, i + n), fill);
      fill += n;
      i += n;
      if (fill === windowSamples) await flush();
    }
  }, o.signal);
  await flush();

  let speakers = 0;
  if (o.diarize && segments.length > 1) {
    const labels = diarizeStats(stats, segments.map((s) => s.end - s.start), { signal: o.signal });
    labels.forEach((l, i) => { segments[i].speaker = `Speaker ${l + 1}`; });
    speakers = Math.max(...labels) + 1;
  }
  o.progress?.(1, "Transcription finished.");
  return {
    text: speakers > 1 ? speakerText(segments) : segments.map((s) => s.text).join(" ").trim(),
    language, segments, speakers, durationMs: result.durationMs || Math.round(offsetSec * 1000),
  };
}

const named = (s: TranscriptSegment, names: Record<string, string>) =>
  (s.speaker && names[s.speaker]?.trim()) || s.speaker || "Speaker ?";

/** "[Speaker 1] …" per change of speaker (desktop as_speaker_text). */
export function speakerText(segments: TranscriptSegment[], names: Record<string, string> = {}): string {
  if (!segments.some((s) => s.speaker)) return segments.map((s) => s.text).join(" ").trim();
  const lines: string[] = [];
  let cur = "";
  let buf: string[] = [];
  for (const s of segments) {
    const who = named(s, names);
    if (who !== cur) { if (buf.length) lines.push(`[${cur}] ${buf.join(" ").trim()}`); cur = who; buf = []; }
    buf.push(s.text.trim());
  }
  if (buf.length) lines.push(`[${cur}] ${buf.join(" ").trim()}`);
  return lines.join("\n");
}

export function formatTime(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return [h, m, s].map((x) => String(x).padStart(2, "0")).join(":");
}

/** "[hh:mm:ss] Speaker: text" per segment (desktop as_timestamped). */
export function timestampedText(segments: TranscriptSegment[], names: Record<string, string> = {}): string {
  return segments.map((s) => `[${formatTime(s.start)}] ${s.speaker ? named(s, names) + ": " : ""}${s.text.trim()}`).join("\n");
}
