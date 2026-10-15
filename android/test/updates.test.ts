import { describe, expect, it } from "vitest";
import type { GithubRelease } from "../src/core/updater";
import { DEFAULT_SETTINGS } from "../src/data/settings";
import { checkForUpdate, expectedChecksum, RELEASES_URL, shouldAutoCheck, verifyDownload } from "../src/services/updates";

const HASH = "a".repeat(64);
const rel = (tag: string, assets: string[], extra: Partial<GithubRelease> = {}): GithubRelease => ({
  tag_name: tag, prerelease: false, draft: false, html_url: `https://x/${tag}`, body: "notes", published_at: "2026-11-01T00:00:00Z",
  assets: assets.map((name) => ({ name, browser_download_url: `https://dl/${tag}/${name}`, size: 1000 })), ...extra });
const RELEASES = [rel("v1.2.4", ["MICO360Meetings-Setup-1.2.4.exe"]),
  rel("android-v1.1.0", ["MICO360Meetings-Android-1.1.0.apk", "MICO360Meetings-Android-1.1.0.apk.sha256"])];

describe("update check", () => {
  it("offers a newer Android release from this repository", async () => {
    const seen: string[] = [];
    const r = await checkForUpdate(async (url) => { seen.push(url); return RELEASES; }, "1.0.0");
    expect(seen).toEqual([`${RELEASES_URL}&page=1`]);
    expect(RELEASES_URL).toContain("/repos/mico360om/MICO360-Meetings/releases");
    expect(r).toMatchObject({ error: "", update: { version: "1.1.0", apkName: "MICO360Meetings-Android-1.1.0.apk" } });
  });
  it("nothing newer: no update; offline or rate-limited: a message, never an exception", async () => {
    expect((await checkForUpdate(async () => RELEASES, "1.1.0")).update).toBeNull();
    const r = await checkForUpdate(async () => { throw new Error("HTTP 403"); }, "1.0.0");
    expect(r.update).toBeNull();
    expect(r.error).toMatch(/Couldn't check for updates.*403/);
    expect((await checkForUpdate(async () => ({ message: "API rate limit exceeded" }), "1.0.0")).error).toMatch(/rate limit/);
  });
});

describe("verifying the download", () => {
  it("takes the APK's own .sha256, or its line in SHA256SUMS.txt", async () => {
    const own = (await checkForUpdate(async () => RELEASES, "1.0.0")).update!;
    expect(await expectedChecksum(own, async () => `${HASH}\n`)).toBe(HASH);
    const sums = { ...own, checksumUrl: "https://dl/SHA256SUMS.txt", checksumIsSums: true };
    expect(await expectedChecksum(sums, async () => `${"b".repeat(64)}  other.exe\n${HASH} *MICO360Meetings-Android-1.1.0.apk\n`)).toBe(HASH);
    expect(await expectedChecksum({ ...own, checksumUrl: "" }, async () => HASH)).toBe("");
  });
  it("never installs without a matching checksum and size", () => {
    const u = { size: 1000 } as Parameters<typeof verifyDownload>[2];
    expect(verifyDownload(1000, HASH, u, "")).toBe("no-checksum");
    expect(verifyDownload(999, HASH, u, HASH)).toBe("size");
    expect(verifyDownload(1000, "c".repeat(64), u, HASH)).toBe("checksum");
    expect(verifyDownload(1000, HASH.toUpperCase(), u, HASH)).toBe("ok");
  });
});

it("checks automatically at most once a day, and only when allowed", () => {
  const now = Date.UTC(2026, 10, 2);
  expect(shouldAutoCheck({ ...DEFAULT_SETTINGS, lastUpdateCheck: now - 25 * 3600_000 }, now)).toBe(true);
  expect(shouldAutoCheck({ ...DEFAULT_SETTINGS, lastUpdateCheck: now - 3600_000 }, now)).toBe(false);
  expect(shouldAutoCheck({ ...DEFAULT_SETTINGS, autoCheckUpdates: false }, now)).toBe(false);
});

it("looks past a page full of desktop releases to find the Android one", async () => {
  const desktop = Array.from({ length: 100 }, (_x, i) => rel(`v1.${i}.0`, ["Setup.exe"]));
  const pages: Record<number, GithubRelease[]> = { 1: desktop, 2: [rel("android-v1.3.0", ["MICO360Meetings-Android-1.3.0.apk"])] };
  const seen: string[] = [];
  const r = await checkForUpdate(async (url) => { seen.push(url); return pages[Number(/[?&]page=(\d+)/.exec(url)![1])] ?? []; }, "1.0.0");
  expect(r.update?.version).toBe("1.3.0");
  expect(seen).toHaveLength(2);
  expect(seen[0]).toContain("per_page=100");
});
