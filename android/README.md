# MICO360 Meetings for Android

Record or import a meeting on the phone, transcribe it **on the phone** (whisper.cpp), turn it into
minutes with **MICO360 Cloud**, edit them, and share a PDF in the company's design — plus action items,
reminders, insights, and desktop-compatible prompt and company files.

Design: `../docs/superpowers/specs/2026-10-15-android-app-design.md` · Plans: `../docs/superpowers/plans/2026-10-15-android-*.md`

## Develop

```bash
cd android
npm ci
npm test            # vitest: core, data, services (no device needed)
npm run typecheck
```

Layout: `src/app` screens (Expo Router) · `src/ui` theme, components, hooks · `src/core` pure logic ported
from the desktop (parity-tested) · `src/data` SQLite repositories · `src/services` the app's decisions ·
`src/native` thin device adapters · `plugins` config plugins · `e2e` emulator flows.

Desktop text data is generated, never hand-edited: `tools/export_desktop_data.py` and
`tools/make_parity_fixtures.py` (run with the desktop's Python).

## Build an APK locally (Windows)

Gradle must not run inside Dropbox. `tools/build-mirror.ps1` mirrors the sources to
`%USERPROFILE%\mico360-build\app`, installs, runs `expo prebuild` and Gradle there:

```powershell
powershell -File android\tools\build-mirror.ps1 -Variant release -Abis x86_64    # quick emulator build
powershell -File android\tools\build-mirror.ps1 -Variant release                  # arm64-v8a, armeabi-v7a, x86_64
```

It uses Visual Studio Build Tools' ninja 1.12 when present (the SDK's ninja 1.10 fails on paths over
260 characters); set `MICO360_NINJA` to use another long-path-aware ninja. Without
`MICO360_KEYSTORE_PATH` a local release build is debug-signed. Without a Connect key in
`MICO360_MOBILE_CONNECT_KEY` it is a Local-only build (minutes generation disabled).

## End-to-end tests on the emulator

```bash
node android/e2e/fakeConnect.mjs 8787          # a local stand-in for MICO360 Connect
MICO360_E2E_CONNECT_URL=http://10.0.2.2:8787/v1 MICO360_MOBILE_CONNECT_KEY=e2e-local-test \
  powershell -File android/tools/build-mirror.ps1 -Variant release -Abis x86_64
node android/e2e/run.mjs emulator-5554 "%USERPROFILE%/mico360-build/app/android/app/build/outputs/apk/release/app-release.apk"
```

An E2E build points at the fake server and must never be published.

## Release (owner)

1. Once: create the signing key — `powershell -File android\tools\make-keystore.ps1` — and set the
   secrets it prints (`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`,
   `ANDROID_KEY_PASSWORD`). Back up the `.jks` and its passwords: every update must use the same key.
2. Once: set `MICO360_MOBILE_CONNECT_KEY` (a dedicated mobile application key from the MICO360 Connect
   console). Without it the release is Local-only and its notes must say so.
3. Bump `APP_VERSION` and `VERSION_CODE` in `android/app.config.ts`; write
   `build/RELEASE_NOTES_android-vX.Y.Z.md`.
4. `git tag android-vX.Y.Z && git push origin android-vX.Y.Z`. The *Android Release* workflow tests,
   builds, signs, smoke-tests on an emulator and publishes `MICO360Meetings-Android-X.Y.Z.apk` with its
   `.sha256` — not marked as the latest release, so desktop updates are unaffected.

Phones find the release through Settings → Updates (or the daily check), verify the SHA256, and open
Android's installer.

## Before the first release: check on a real phone

- 30-minute recording with the screen off; a phone call during a recording (paused, banner, Resume).
- Kill the app while recording, reopen: the recording is offered as a draft and transcribes.
- A real two-person meeting with speaker identification; Arabic and English minutes.
- Download the *small* model over a flaky connection (resume), then transcribe a 1-hour import.
- Share a PDF to WhatsApp and Gmail; open the Word export.
