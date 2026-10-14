/** Meeting recording: 16 kHz mono 16-bit WAV with a microphone foreground
 *  service, a live level, and the muted-microphone warning (M37). */
import { type AudioDataEvent, type RecordingConfig, type RecordingInterruptionReason, useSharedAudioRecorder } from "@siteed/audio-studio";
import { Directory, File, Paths } from "expo-file-system";
import { useCallback, useRef, useState } from "react";
import { SilenceWatch } from "../core/silence";
import { fixBytes, wavHeaderFixes } from "../core/wav";

export const recordingsDir = () => new Directory(Paths.document, "recordings");

export function recordingConfig(filename: string, onStream: (e: AudioDataEvent) => Promise<void>,
  onInterrupted?: (reason: RecordingInterruptionReason, isPaused: boolean) => void): RecordingConfig {
  const dir = recordingsDir();
  if (!dir.exists) dir.create({ intermediates: true });
  return {
    sampleRate: 16000, channels: 1, encoding: "pcm_16bit", interval: 500, keepAwake: true, streamFormat: "float32",
    showNotification: true, notification: { title: "MICO360 Meetings", text: "Recording a meeting" },
    autoResumeAfterInterruption: false, outputDirectory: dir.uri, filename, onAudioStream: onStream,
    onRecordingInterrupted: (e) => onInterrupted?.(e.reason, e.isPaused),
  };
}

export function useMeetingRecorder() {
  const rec = useSharedAudioRecorder();
  const watch = useRef(new SilenceWatch());
  const [level, setLevel] = useState(0);

  const start = useCallback(async (onSilence: () => void,
    onInterrupted?: (reason: RecordingInterruptionReason, isPaused: boolean) => void) => {
    watch.current = new SilenceWatch();
    await rec.startRecording(recordingConfig(`meeting-${Date.now()}`, async (e) => {
      const d = e.data;
      if (typeof d === "string") return;
      const samples = d instanceof Int16Array ? Float32Array.from(d, (x) => x / 32768) : d;
      let peak = 0;
      for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
      setLevel(peak);
      if (watch.current.feed(samples, 16000)) onSilence();
    }, onInterrupted));
  }, [rec]);

  const stop = useCallback(async () => {
    const r = await rec.stopRecording();
    // audio-studio reports "file:/data/..."; expo-file-system lists "file:///data/...": store one form.
    return r ? { uri: new File(r.fileUri).uri, durationMs: r.durationMs, size: r.size } : null;
  }, [rec]);

  return { start, stop, pause: rec.pauseRecording, resume: rec.resumeRecording,
    isRecording: rec.isRecording, isPaused: rec.isPaused, durationMs: rec.durationMs, size: rec.size, level };
}

const fileName = (uri: string) => decodeURIComponent(uri.split("/").pop() ?? "");

/** Recordings not yet attached to a meeting (the app was killed while recording):
 *  their WAV header is patched in place — never the whole file in memory — so
 *  every captured second can be transcribed. `known` holds the meetings' audio URIs. */
export async function recoverInterruptedRecordings(known: Set<string>): Promise<string[]> {
  const dir = recordingsDir();
  if (!dir.exists) return [];
  const names = new Set([...known].map(fileName));
  const out: string[] = [];
  for (const f of dir.list()) {
    if (!(f instanceof File) || !f.uri.endsWith(".wav") || names.has(fileName(f.uri))) continue;
    const h = f.open();
    try {
      const head = h.readBytes(Math.min(4096, f.size));
      const fixes = wavHeaderFixes(head, f.size);
      if (!fixes) continue;
      for (const fix of fixes) { h.offset = fix.offset; h.writeBytes(fixBytes(fix)); }
    } finally { h.close(); }
    out.push(f.uri);
  }
  return out;
}
