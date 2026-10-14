# MICO360 Meetings for Android — Plan 4: Release, Signing and In-App Updates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed APK published by GitHub Actions on an `android-v*` tag (never becoming the desktop's "latest" release), a script for the owner to create the signing keystore, and an in-app update check that downloads, verifies and installs a newer APK.

**Architecture:** Update decisions are pure (`src/services/updates.ts`, vitest with a fake fetch); the download/verify/install adapter is `src/native/updates.ts` (expo-file-system download, SHA256 in chunks, `expo-intent-launcher` `ACTION_VIEW` on a content URI). Signing is injected at build time by a config plugin that reads environment variables, so no keystore or password ever touches the repository.

**Tech Stack:** GitHub Actions (ubuntu, Node 24, JDK 17, Android SDK), `softprops/action-gh-release@v2`, `reactivecircus/android-emulator-runner@v2`, `expo-intent-launcher`, `keytool`.

**Spec:** `docs/superpowers/specs/2026-10-15-android-app-design.md` §5 (key injection, Local-only), §7 (releases and updates) · **Builds on:** Plans 1–3.

## Global Constraints

- Tag `android-vX.Y.Z` must equal `APP_VERSION` in `android/app.config.ts`; the desktop workflow (tags `v*`) is not triggered.
- Published assets: `MICO360Meetings-Android-X.Y.Z.apk`, `MICO360Meetings-Android-X.Y.Z.apk.sha256`; notes from `build/RELEASE_NOTES_android-vX.Y.Z.md`; **`make_latest: false`**.
- Secrets used only by the steps that need them: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `MICO360_MOBILE_CONNECT_KEY` (else `MICO360_CONNECT_API_KEY`). A release without a Connect key must say "Local-only" in its notes or the workflow fails. A release without the keystore fails (never publish a debug-signed APK).
- The keystore is created by the owner with `android/tools/make-keystore.ps1`; Claude never creates, holds or reads it.
- An update APK whose SHA256 doesn't match is deleted and never installed.

## Review Focus

1. A release published for desktop (`v*`) or a draft/pre-release must never be offered to phones — covered by Plan 1 `pickAndroidRelease` tests and Task 1.
2. GitHub API rate limiting / offline: the check fails quietly at start-up and with a clear message from Settings — Task 1.
3. A truncated or altered download is never installed — Task 1 (`verifyDownload`).
4. A tag that doesn't match the app version is refused by the workflow — Task 3.
5. A missing keystore secret fails the release instead of shipping a debug-signed APK — Task 3.

---

### Task 1: Update check service and adapter

**Files:** Create `android/src/services/updates.ts`, `android/test/updates.test.ts`, `android/src/native/updates.ts`, `android/src/app/settings/updates.tsx`; modify `android/src/app/(tabs)/settings.tsx` (Updates row), `android/src/app/_layout.tsx` (daily quiet check when `autoCheckUpdates`).

**Interfaces:**
- `RELEASES_URL = "https://api.github.com/repos/mico360om/MICO360-Meetings/releases?per_page=30"` (the desktop's `DEFAULT_REPO`).
- `checkForUpdate(fetchJson, current): Promise<{ update: AndroidUpdate | null; error: string }>` — never throws.
- `expectedChecksum(update, fetchText): Promise<string>` — per-file `.sha256` (bare hash allowed) or `SHA256SUMS.txt` (line for the APK only).
- `verifyDownload(size, digest, update, expected): "ok" | "size" | "checksum" | "no-checksum"`.
- `shouldAutoCheck(settings, now): boolean` — at most once per 24 h.

- [ ] Step 1: failing tests — newer android release found; same version → none; network error → message; sums file vs own `.sha256`; missing checksum refuses install; wrong size/digest refused; auto-check spacing.
- [ ] Step 2: run → FAIL. Step 3: implement service; adapter downloads to cache, hashes with `sha256Chunks`, deletes on mismatch, opens the installer via `expo-intent-launcher` with a `content://` URI (`expo-file-system/legacy` `getContentUriAsync`) and `FLAG_GRANT_READ_URI_PERMISSION`. Step 4: tests + typecheck PASS. Step 5: commit.

### Task 2: Signing plugin and keystore script

**Files:** Create `android/plugins/withReleaseSigning.js` (when `MICO360_KEYSTORE_PATH` is set, adds `signingConfigs.release` reading the path/passwords/alias from environment variables at Gradle time and uses it for `buildTypes.release`), `android/tools/make-keystore.ps1` (wraps `keytool -genkeypair -v -keystore … -alias mico360-meetings -keyalg RSA -keysize 4096 -validity 10000`, then prints the `gh secret set` commands the owner runs; never echoes passwords), register the plugin in `app.config.ts`.
- Verify: unit test that the plugin's Gradle text contains `System.getenv("MICO360_KEYSTORE_PASSWORD")` and no literal secrets; local build without the variable still produces a (debug-signed) APK.
- Commit.

### Task 3: Release workflow

**Files:** Create `.github/workflows/android-release.yml`; modify `.github/workflows/android-ci.yml` (no change needed beyond paths).
- Steps: checkout → tag/version check → Local-only notes rule → keystore present check → Node 24 + `npm ci` + typecheck + tests → JDK 17 → decode keystore to `$RUNNER_TEMP` → `npx expo prebuild --platform android` with the Connect key env on that step only → `./gradlew assembleRelease` with signing env on that step only (`reactNativeArchitectures=arm64-v8a,armeabi-v7a,x86_64`) → `apksigner verify` → rename + `sha256sum` → emulator smoke (install, launch, app title visible) → `softprops/action-gh-release@v2` with `make_latest: false`, `prerelease: false`, files + body_path.
- Verify: `actionlint`-style review by reading; a dry run is the first tag push by the owner.
- Commit.

### Task 4: Docs and release notes

**Files:** Create `android/README.md` (develop, test, build locally, release checklist, secrets), `build/RELEASE_NOTES_android-v1.0.0.md`; update `android/AGENTS.md` if needed.
- Commit.
