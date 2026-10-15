import { type ModelId, modelById } from "../core/models";
import type { ProgressFn } from "../core/pipeline";
import { type AudioSource, type Recognizer, type Transcript, transcribeAudio } from "../core/transcribeLong";
import type { AppSettings } from "../data/settings";

export interface TranscriptionDeps {
  modelPath(id: ModelId): Promise<string | null>;
  recognizer(path: string): Promise<Recognizer>;
  source(uri: string): AudioSource;
  durationMs(uri: string): Promise<number>;
}

export class ModelMissing extends Error {
  constructor(readonly model: ModelId) {
    super(`The ${modelById(model).label.split(" —")[0]} speech model isn't downloaded yet. Download it in Settings → Transcription.`);
    this.name = "ModelMissing";
  }
}

/** Transcribe a recorded or imported file with the user's model, language and speaker settings. */
export async function transcribeMeetingAudio(uri: string, settings: AppSettings, deps: TranscriptionDeps,
  opts: { progress?: ProgressFn; signal?: AbortSignal } = {}): Promise<Transcript> {
  const path = await deps.modelPath(settings.whisperModel);
  if (!path) throw new ModelMissing(settings.whisperModel);
  opts.progress?.(0.01, "Loading the speech model…");
  const recognizer = await deps.recognizer(path);
  const totalMs = await deps.durationMs(uri).catch(() => 0);
  return transcribeAudio(deps.source(uri), recognizer, {
    language: settings.language, diarize: settings.diarize, totalMs, progress: opts.progress, signal: opts.signal,
  });
}
