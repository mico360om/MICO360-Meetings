/** Choosing and verifying Android updates from GitHub Releases (version and
 *  checksum rules ported from desktop core/updater.py). */
export const ANDROID_TAG_PREFIX = "android-v";

/** Comparable key: up to 4 numeric parts; a pre-release ranks below the final. */
export function parseVersion(v: string): number[] {
  const s = (v ?? "").trim().replace(/^[vV]+/, "");
  const dash = s.indexOf("-");
  const core = dash >= 0 ? s.slice(0, dash) : s;
  const nums = (core.match(/\d+/g) ?? []).slice(0, 4).map(Number);
  const key = nums.length ? nums : [0];
  while (key.length < 4) key.push(0);
  return [...key, dash >= 0 ? 0 : 1];
}

export function isNewer(latest: string, current: string): boolean {
  const a = parseVersion(latest), b = parseVersion(current);
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}

const SHA256_RE = /(?<![A-Za-z0-9_])([0-9a-fA-F]{64})(?![A-Za-z0-9_])/;
const SHA256_ALL = new RegExp(SHA256_RE.source, "g");
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The SHA256 for `filename` in a checksums text; only a line naming that file
 *  counts. `allowBare` accepts a lone hash (a per-file .sha256 asset). */
export function parseChecksum(text: string, filename = "", allowBare = false): string {
  if (!text) return "";
  const fn = filename.toLowerCase();
  if (fn) {
    const nameRe = new RegExp(`(?<![A-Za-z0-9_.-])\\*?${escapeRe(fn)}(?![A-Za-z0-9_.-])`, "i");
    for (const line of text.split(/\r\n|\n|\r/)) {
      if (nameRe.test(line)) {
        const m = SHA256_RE.exec(line);
        if (m) return m[1].toLowerCase();
      }
    }
    if (!allowBare) return "";
    const hashes = new Set([...text.matchAll(SHA256_ALL)].map((m) => m[1].toLowerCase()));
    return hashes.size === 1 ? [...hashes][0] : "";
  }
  const m = SHA256_RE.exec(text);
  return m ? m[1].toLowerCase() : "";
}

export interface GithubAsset { name: string; browser_download_url: string; size: number; }
export interface GithubRelease {
  tag_name: string; prerelease: boolean; draft: boolean; html_url: string; body: string; published_at: string; assets: GithubAsset[];
}
export interface AndroidUpdate {
  version: string; apkUrl: string; apkName: string; size: number; checksumUrl: string; checksumIsSums: boolean;
  notes: string; publishedAt: string; pageUrl: string;
}

/** The newest published Android release that carries an APK, or null. */
export function pickAndroidRelease(releases: GithubRelease[]): AndroidUpdate | null {
  let best: AndroidUpdate | null = null;
  for (const r of releases ?? []) {
    if (!r || r.draft || r.prerelease || !String(r.tag_name ?? "").startsWith(ANDROID_TAG_PREFIX)) continue;
    const apk = (r.assets ?? []).find((a) => a.name.toLowerCase().endsWith(".apk"));
    if (!apk) continue;
    const version = r.tag_name.slice(ANDROID_TAG_PREFIX.length);
    if (best && !isNewer(version, best.version)) continue;
    const own = r.assets.find((a) => a.name.toLowerCase() === `${apk.name.toLowerCase()}.sha256`);
    const sums = r.assets.find((a) => a.name.toLowerCase() === "sha256sums.txt");
    best = { version, apkUrl: apk.browser_download_url, apkName: apk.name, size: apk.size,
      checksumUrl: (own ?? sums)?.browser_download_url ?? "", checksumIsSums: !own && !!sums,
      notes: r.body ?? "", publishedAt: r.published_at ?? "", pageUrl: r.html_url ?? "" };
  }
  return best;
}
