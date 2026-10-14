/** In-app updates from this repository's GitHub Releases: find a newer
 *  `android-v*` release, and refuse any APK whose size or SHA256 doesn't match. */
import { type AndroidUpdate, type GithubRelease, isNewer, parseChecksum, pickAndroidRelease } from "../core/updater";
import type { AppSettings } from "../data/settings";

export const RELEASES_URL = "https://api.github.com/repos/mico360om/MICO360-Meetings/releases?per_page=30";
export type FetchJson = (url: string) => Promise<unknown>;
export type FetchText = (url: string) => Promise<string>;

export async function checkForUpdate(fetchJson: FetchJson, current: string): Promise<{ update: AndroidUpdate | null; error: string }> {
  try {
    const data = await fetchJson(RELEASES_URL);
    if (!Array.isArray(data)) {
      const msg = (data as { message?: unknown } | null)?.message;
      return { update: null, error: `Couldn't check for updates (${typeof msg === "string" ? msg : "unexpected answer from GitHub"}).` };
    }
    const u = pickAndroidRelease(data as GithubRelease[]);
    return { update: u && isNewer(u.version, current) ? u : null, error: "" };
  } catch (e) {
    return { update: null, error: `Couldn't check for updates (${e instanceof Error ? e.message : String(e)}). Check your connection and try again.` };
  }
}

/** The SHA256 the release publishes for its APK ("" when it publishes none). */
export async function expectedChecksum(u: AndroidUpdate, fetchText: FetchText): Promise<string> {
  if (!u.checksumUrl) return "";
  return parseChecksum(await fetchText(u.checksumUrl), u.apkName, !u.checksumIsSums);
}

export function verifyDownload(size: number, digest: string, u: Pick<AndroidUpdate, "size">, expected: string): "ok" | "size" | "checksum" | "no-checksum" {
  if (!expected) return "no-checksum";
  if (u.size && size !== u.size) return "size";
  return digest.toLowerCase() === expected.toLowerCase() ? "ok" : "checksum";
}

const DAY_MS = 24 * 3600_000;
export function shouldAutoCheck(s: AppSettings, now: number): boolean {
  return s.autoCheckUpdates && now - s.lastUpdateCheck >= DAY_MS;
}
