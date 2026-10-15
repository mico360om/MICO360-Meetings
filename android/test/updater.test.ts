import { describe, expect, it } from "vitest";
import fx from "./fixtures/updater.json";
import { isNewer, parseChecksum, pickAndroidRelease, type GithubRelease } from "../src/core/updater";

describe("parity with desktop updater.py", () => {
  it("version comparison", () => { for (const c of fx.newer) expect([c.latest, c.current, isNewer(c.latest, c.current)]).toEqual([c.latest, c.current, c.output]); });
  it("checksums", () => { for (const c of fx.checksum) expect(parseChecksum(c.text, c.file, c.bare)).toBe(c.output); });
});

const rel = (tag: string, assets: string[], extra: Partial<GithubRelease> = {}): GithubRelease => ({
  tag_name: tag, prerelease: false, draft: false, html_url: `https://x/${tag}`, body: "notes", published_at: "2026-10-20T00:00:00Z",
  assets: assets.map((name) => ({ name, browser_download_url: `https://dl/${tag}/${name}`, size: 1000 })), ...extra });

describe("picking the Android release", () => {
  it("ignores desktop, draft and pre-releases; takes the highest android version", () => {
    const pick = pickAndroidRelease([
      rel("v1.2.4", ["MICO360Meetings-Setup.exe"]),
      rel("android-v1.1.0", ["MICO360Meetings-Android-1.1.0.apk", "MICO360Meetings-Android-1.1.0.apk.sha256"]),
      rel("android-v1.2.0-rc1", ["a.apk"], { prerelease: true }),
      rel("android-v1.3.0", ["a.apk"], { draft: true }),
      rel("android-v1.0.5", ["b.apk"]),
    ])!;
    expect(pick.version).toBe("1.1.0");
    expect(pick.apkUrl).toBe("https://dl/android-v1.1.0/MICO360Meetings-Android-1.1.0.apk");
    expect(pick.checksumUrl).toBe("https://dl/android-v1.1.0/MICO360Meetings-Android-1.1.0.apk.sha256");
  });
  it("falls back to SHA256SUMS.txt; no APK means no update", () => {
    expect(pickAndroidRelease([rel("android-v2.0.0", ["x.apk", "SHA256SUMS.txt"])])!.checksumIsSums).toBe(true);
    expect(pickAndroidRelease([rel("android-v2.0.0", ["notes.txt"])])).toBeNull();
  });
});
