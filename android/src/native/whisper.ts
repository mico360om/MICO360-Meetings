/** whisper.cpp on the phone (whisper.rn). One model stays loaded between calls. */
import { initWhisper, type WhisperContext } from "whisper.rn/index";   // the package exports only "./*"
import type { Recognizer } from "../core/transcribeLong";

let cached: { path: string; ctx: WhisperContext } | null = null;

/** whisper.rn reads an ArrayBuffer as 16-bit little-endian PCM (16 kHz mono). */
function toPcm16(samples: Float32Array): ArrayBuffer {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
  }
  return out.buffer;
}

export async function whisperRecognizer(modelPath: string): Promise<Recognizer> {
  if (!cached || cached.path !== modelPath) {
    if (cached) await cached.ctx.release();
    cached = null;
    cached = { path: modelPath, ctx: await initWhisper({ filePath: modelPath }) };
  }
  const ctx = cached.ctx;
  return {
    async transcribe(samples, language, signal, onProgress) {
      const task = ctx.transcribeData(toPcm16(samples), { language, onProgress: (p: number) => onProgress?.(p / 100) });
      const onAbort = () => { void task.stop(); };
      signal?.addEventListener("abort", onAbort);
      try {
        const r = await task.promise;
        return { language: r.language, segments: r.segments };
      } finally { signal?.removeEventListener("abort", onAbort); }
    },
  };
}
