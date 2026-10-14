/** Whisper model files: downloaded on demand, verified by size and SHA256
 *  before use; a bad or interrupted download is deleted, never used. */
import { Directory, File, Paths } from "expo-file-system";
import { checkModel, type ModelId, modelById, sha256Chunks, type WhisperModel, WHISPER_MODELS } from "../core/models";

const dir = () => { const d = new Directory(Paths.document, "models"); if (!d.exists) d.create({ intermediates: true }); return d; };
const fileFor = (m: WhisperModel) => new File(dir(), m.file);

export async function modelPath(id: ModelId): Promise<string | null> {
  const m = modelById(id);
  const f = fileFor(m);
  return f.exists && f.size === m.sizeBytes ? f.uri : null;
}

export async function installedModels(): Promise<ModelId[]> {
  const out: ModelId[] = [];
  for (const m of WHISPER_MODELS) if (await modelPath(m.id)) out.push(m.id);
  return out;
}

async function digestOf(f: File): Promise<string> {
  const h = f.open();
  try {
    return await sha256Chunks(async (offset, length) => { h.offset = offset; return h.readBytes(length); }, f.size);
  } finally { h.close(); }
}

/** Download, verify size + SHA256, then move into place. */
export async function downloadModel(m: WhisperModel, onProgress: (fraction: number) => void, signal?: AbortSignal): Promise<void> {
  const part = new File(dir(), m.file + ".part");
  if (part.exists) part.delete();
  const task = File.createDownloadTask(m.url, part, {
    signal,
    onProgress: ({ bytesWritten, totalBytes }) => onProgress(totalBytes > 0 ? bytesWritten / totalBytes : bytesWritten / m.sizeBytes),
  });
  try {
    await task.downloadAsync();
  } catch (e) {
    if (part.exists) part.delete();
    throw signal?.aborted ? new Error("Download cancelled.") : e;
  }
  if (signal?.aborted || !part.exists) { if (part.exists) part.delete(); throw new Error("Download cancelled."); }
  const verdict = checkModel(m, part.size, part.size === m.sizeBytes ? await digestOf(part) : "");
  if (verdict !== "ok") {
    part.delete();
    throw new Error(verdict === "size" ? "The download was incomplete. Try again." : "The downloaded model failed its integrity check and was deleted.");
  }
  const target = fileFor(m);
  if (target.exists) target.delete();
  await part.move(target);
}

export function deleteModel(m: WhisperModel): void {
  const f = fileFor(m);
  if (f.exists) f.delete();
}
