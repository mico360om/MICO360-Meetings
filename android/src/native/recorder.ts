/** Meeting recording: 16 kHz mono 16-bit WAV with a microphone foreground
 *  service, a live level, and the muted-microphone warning (M37). */
import { type AudioDataEvent, type RecordingConfig, useSharedAudioRecorder } from "@siteed/audio-studio";
import { Directory, File, Paths } from "expo-file-system";
import { useCallback, useRef, useState } from "react";
import { SilenceWatch } from "../core/silence";
import { repairWavHeader } from "../core/wav";

export const recordingsDir = () => new Directory(Paths.document, "recordings");

export function recordingConfig(filename: string, onStream: (e: AudioDataEvent) => Promise<void>): RecordingConfig {
  const dir = recordingsDir();
  if (!dir.exists) dir.create({ intermediates: true });
  return {
    sampleRate: 16000, channels: 1, encoding: "pcm_16bit", interval: 500, keepAwake: true, streamFormat: "float32",
    showNotification: true, notification: { title: "MICO360 Meetings", text: "Recording a meeting" },
    autoResumeAfterInterruption: false, outputDirectory: dir.uri, filename, onAudioStream: onStream,
  };
}

export function useMeetingRecorder() {
  const rec = useSharedAudioRecorder();
  const watch = useRef(new SilenceWatch());
  const [level, setLevel] = useState(0);

  const start = useCallback(async (onSilence: () => void) => {
    watch.current = new SilenceWatch();
    await rec.startRecording(recordingConfig(`meeting-${Date.now()}`, async (e) => {
      const d = e.data;
      if (typeof d === "string") return;
      const samples = d instanceof Int16Array ? Float32Array.from(d, (x) => x / 32768) : d;
      let peak = 0;
      for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
      setLevel(peak);
      if (watch.current.feed(samples, 16000)) onSilence();
    }));
  }, [rec]);

  const stop = useCallback(async () => {
    const r = await rec.stopRecording();
    return r ? { uri: r.fileUri, durationMs: r.durationMs, size: r.size } : null;
  }, [rec]);

  return { start, stop, pause: rec.pauseRecording, resume: rec.resumeRecording,
    isRecording: rec.isRecording, isPaused: rec.isPaused, durationMs: rec.durationMs, size: rec.size, level };
}

/** Recordings not yet attached to a meeting (the app was killed while recording):
 *  their WAV header is repaired so every captured second can be transcribed. */
export async function recoverInterruptedRecordings(known: Set<string>): Promise<string[]> {
  const dir = recordingsDir();
  if (!dir.exists) return [];
  const out: string[] = [];
  for (const f of dir.list()) {
    if (!(f instanceof File) || !f.uri.endsWith(".wav") || known.has(f.uri)) continue;
    const bytes = await f.bytes();
    const fixed = repairWavHeader(bytes);
    if (!fixed) continue;
    if (fixed !== bytes) f.write(fixed);
    out.push(f.uri);
  }
  return out;
}
