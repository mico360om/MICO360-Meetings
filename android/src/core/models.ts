/** On-device Whisper models: quantised ggml files from the whisper.cpp
 *  repository, pinned by size and SHA256 (Hugging Face LFS metadata). */
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export type ModelId = "tiny" | "base" | "small";
export interface WhisperModel { id: ModelId; label: string; file: string; url: string; sizeBytes: number; sha256: string; }

const BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/";
const model = (id: ModelId, label: string, file: string, sizeBytes: number, digest: string): WhisperModel =>
  ({ id, label, file, url: BASE + file, sizeBytes, sha256: digest });

export const WHISPER_MODELS: WhisperModel[] = [
  model("tiny", "Tiny — fastest, least accurate (32 MB)", "ggml-tiny-q5_1.bin", 32152673,
    "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7"),
  model("base", "Base — recommended (60 MB)", "ggml-base-q5_1.bin", 59707625,
    "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"),
  model("small", "Small — most accurate, slower (190 MB)", "ggml-small-q5_1.bin", 190085487,
    "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"),
];

export const modelById = (id: ModelId): WhisperModel => WHISPER_MODELS.find((m) => m.id === id) ?? WHISPER_MODELS[1];

/** SHA256 of a file read in chunks (never the whole file in memory). */
export async function sha256Chunks(read: (offset: number, length: number) => Promise<Uint8Array>, size: number,
  chunk = 4 << 20): Promise<string> {
  const h = sha256.create();
  for (let o = 0; o < size; o += chunk) h.update(await read(o, Math.min(chunk, size - o)));
  return bytesToHex(h.digest());
}

export function checkModel(m: WhisperModel, size: number, digest: string): "ok" | "size" | "checksum" {
  if (size !== m.sizeBytes) return "size";
  return digest.toLowerCase() === m.sha256 ? "ok" : "checksum";
}

/** After fetching the rest of a partial download (`restSize` bytes, asked for with a
 *  Range header): append it, replace the part (the server sent the whole file), or restart. */
export function resumePlan(partSize: number, restSize: number, total: number): "append" | "replace" | "restart" {
  if (partSize > 0 && partSize + restSize === total) return "append";
  if (restSize === total) return "replace";
  return "restart";
}
