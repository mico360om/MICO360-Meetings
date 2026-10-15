/** Whisper model files: downloaded on demand, verified by size and SHA256
 *  before use; a bad or interrupted download is deleted, never used. */
import { Directory, File, Paths } from "expo-file-system";
import { checkModel, type ModelId, modelById, resumePlan, sha256Chunks, type WhisperModel, WHISPER_MODELS } from "../core/models";

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

/** Fetch `url` into `to`, optionally asking only for the bytes from `from` on. */
async function fetchInto(url: string, to: File, from: number, onBytes: (n: number) => void, signal?: AbortSignal): Promise<void> {
  if (to.exists) to.delete();
  const task = File.createDownloadTask(url, to, {
    signal, headers: from > 0 ? { Range: `bytes=${from}-` } : undefined,
    onProgress: ({ bytesWritten }) => onBytes(bytesWritten),
  });
  await task.downloadAsync();
}

/** Append `rest` to `part` in 4 MiB chunks (never the whole file in memory). */
function appendFile(part: File, rest: File): void {
  const src = rest.open(), dst = part.open();
  try {
    dst.offset = part.size;
    for (let o = 0; o < rest.size; o += 4 << 20) { src.offset = o; dst.writeBytes(src.readBytes(Math.min(4 << 20, rest.size - o))); }
  } finally { src.close(); dst.close(); }
}

/** Download (resuming a partial file from an earlier attempt), verify size + SHA256,
 *  then move into place. A failed attempt keeps the partial file for the next try;
 *  a file that fails verification is deleted. */
export async function downloadModel(m: WhisperModel, onProgress: (fraction: number) => void, signal?: AbortSignal): Promise<void> {
  const part = new File(dir(), m.file + ".part");
  const rest = new File(dir(), m.file + ".rest");
  if (part.exists && part.size >= m.sizeBytes) part.delete();
  const have = part.exists ? part.size : 0;
  try {
    if (have > 0) {
      await fetchInto(m.url, rest, have, (n) => onProgress((have + n) / m.sizeBytes), signal);
      const plan = resumePlan(have, rest.size, m.sizeBytes);
      if (plan === "append") { appendFile(part, rest); rest.delete(); }
      else if (plan === "replace") { part.delete(); await rest.move(part); }
      else { rest.delete(); part.delete(); await fetchInto(m.url, part, 0, (n) => onProgress(n / m.sizeBytes), signal); }
    } else {
      await fetchInto(m.url, part, 0, (n) => onProgress(n / m.sizeBytes), signal);
    }
  } catch (e) {
    if (rest.exists) rest.delete();
    throw signal?.aborted ? new Error("Download paused. Tap Download to continue where it stopped.") : e;
  }
  if (signal?.aborted || !part.exists) throw new Error("Download paused. Tap Download to continue where it stopped.");
  const verdict = checkModel(m, part.size, part.size === m.sizeBytes ? await digestOf(part) : "");
  if (verdict !== "ok") {
    if (verdict === "checksum") part.delete();
    throw new Error(verdict === "size" ? "The download stopped early. Tap Download to continue." : "The downloaded model failed its integrity check and was deleted.");
  }
  const target = fileFor(m);
  if (target.exists) target.delete();
  await part.move(target);
}

export function deleteModel(m: WhisperModel): void {
  for (const f of [fileFor(m), new File(dir(), m.file + ".part"), new File(dir(), m.file + ".rest")]) if (f.exists) f.delete();
}
