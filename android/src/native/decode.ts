/** Any recorded or imported audio/video file as 16 kHz mono float32 chunks. */
import { streamAudioData } from "@siteed/audio-studio";
import { File } from "expo-file-system";
import { CancelledError } from "../core/pipeline";
import type { AudioSource } from "../core/transcribeLong";

export function fileSource(uri: string): AudioSource {
  return {
    async stream(onChunk, signal) {
      const r = await streamAudioData(
        { fileUri: uri, targetSampleRate: 16000, channels: 1, chunkDurationMs: 30_000, streamFormat: "float32", signal },
        { onChunk: async (c) => { await onChunk(c.samples); } });
      // streamAudioData resolves (it does not reject) when its signal aborts.
      if (r.cancelled || signal?.aborted) throw new CancelledError("Transcription cancelled.");
      return { durationMs: r.durationMs };
    },
  };
}

/** Our own recordings are 16 kHz mono 16-bit WAV: the length follows from the size.
 *  Other files report 0 (progress is shown without a percentage). */
export async function fileDurationMs(uri: string): Promise<number> {
  if (!uri.toLowerCase().endsWith(".wav")) return 0;
  const f = new File(uri);
  return f.exists ? Math.max(0, Math.round(((f.size - 44) / 32000) * 1000)) : 0;
}
