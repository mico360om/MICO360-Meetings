/** Download an update APK, verify it, and open Android's installer. */
import Constants from "expo-constants";
import { Directory, File, Paths } from "expo-file-system";
import { getContentUriAsync } from "expo-file-system/legacy";
import * as IntentLauncher from "expo-intent-launcher";
import { sha256Chunks } from "../core/models";
import type { AndroidUpdate } from "../core/updater";
import { expectedChecksum, verifyDownload } from "../services/updates";

const UA = { "User-Agent": "MICO360-Meetings-Android", Accept: "application/vnd.github+json" };

export const currentVersion = () => Constants.expoConfig?.version ?? "0.0.0";

export async function fetchJson(url: string): Promise<unknown> {
  const r = await fetch(url, { headers: UA });
  if (!r.ok && r.status !== 403) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchText(url: string): Promise<string> {
  const r = await fetch(url, { headers: { "User-Agent": UA["User-Agent"] } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}

async function digestOf(f: File): Promise<string> {
  const h = f.open();
  try { return await sha256Chunks(async (offset, length) => { h.offset = offset; return h.readBytes(length); }, f.size); }
  finally { h.close(); }
}

/** Download and verify; returns the APK file. A file that fails verification is deleted. */
export async function downloadUpdate(u: AndroidUpdate, onProgress: (fraction: number) => void, signal?: AbortSignal): Promise<File> {
  const expected = await expectedChecksum(u, fetchText);
  const dir = new Directory(Paths.cache, "updates");
  if (!dir.exists) dir.create({ intermediates: true });
  const apk = new File(dir, u.apkName.replace(/[^A-Za-z0-9._-]/g, "_"));
  if (apk.exists) apk.delete();
  const task = File.createDownloadTask(u.apkUrl, apk, {
    signal, onProgress: ({ bytesWritten, totalBytes }) => onProgress(totalBytes > 0 ? bytesWritten / totalBytes : bytesWritten / (u.size || 1)),
  });
  await task.downloadAsync();
  if (signal?.aborted || !apk.exists) throw new Error("Download cancelled.");
  const verdict = verifyDownload(apk.size, await digestOf(apk), u, expected);
  if (verdict !== "ok") {
    apk.delete();
    throw new Error(verdict === "no-checksum" ? "This release has no checksum, so it can't be verified and won't be installed."
      : verdict === "size" ? "The download was incomplete. Try again." : "The download failed its integrity check and was deleted.");
  }
  return apk;
}

/** Hand the verified APK to Android's package installer. */
export async function openInstaller(apk: File): Promise<void> {
  const contentUri = await getContentUriAsync(apk.uri);
  await IntentLauncher.startActivityAsync("android.intent.action.VIEW", {
    data: contentUri, type: "application/vnd.android.package-archive", flags: 1,   // FLAG_GRANT_READ_URI_PERMISSION
  });
}
