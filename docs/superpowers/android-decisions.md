# MICO360 Meetings for Android — decisions and deferred items

Compiled from the execution ledgers of the four Android plans (2026-10-15). Each ruling: what was decided — why — what it costs if wrong.

## Plan 1: foundation-and-core

### Rulings
- Task 1: Ruling: template adaptive-icon files are android-icon-*.png, not adaptive-icon.png - kept the template files in app.config.ts - cost if wrong: an icon path edit.
- Task 1: Ruling: deleted the template LICENSE (Expo's MIT notice, not this app's licence) - cost if wrong: restore one file.
- Task 4: Ruling: the file-writing tool decodes \uXXXX escapes into literal characters (U+2028 broke a regex literal) - split regex built with String.fromCharCode; mdBlocks U+FFFE/FFFF restored to escapes; purity test now bans literal U+2028/2029/FEFF/FFFE/FFFF/NUL in src/core - cost if wrong: none (behaviour identical).
- Task 5: Ruling: parsePromptFile returns unknown[] records, so the .txt test casts records[0] to {name}; key separator written as \x00 (not \u0000) because the write tool decodes \u escapes - cost if wrong: none.
- Task 6: Ruling: retry test rewritten to assert directly (part 2 fails -> 2 calls; rerun skips part 1, asks part 2 again) instead of the plan's counter juggling - same behaviour pinned - cost if wrong: none.
- Task 7: Ruling: errorMessage follows the desktop wording exactly (no extra period + '..' cleanup as in the plan) - cost if wrong: punctuation only.
- Task 8: Ruling: test maps fixture items through the same toPy picker (typed) instead of a Record<string,string> cast; BOM built with String.fromCharCode - cost if wrong: none.
- Task 10: Ruling: profile test also pins logoPath (the original path, for the import flow to find a CSV's logo file); normalizeRecord test casts fixture input to Record<string,unknown> - cost if wrong: none.
- Task 11: Ruling: 'No minutes content.' shown when nothing at all was rendered (title/details count as content), simpler than the plan's separate block counter - cost if wrong: a details-only document skips that line, which is correct.
- Task 12: Ruling: para() typed with docx's IParagraphOptions instead of the plan's ConstructorParameters juggling; olist separator written as \xa0 - cost if wrong: none.
- Final: Ruling: RN fetch follows redirects itself (finding 5) - not fixable in pure core; Plan 2 builds the native transport and tests a cross-host 307 on the emulator, recording the result in spec 5.1 - cost if wrong: on a misconfigured server the key could follow a redirect (base is plain HTTP today, so exposure is no worse than now).
- Final: Ruling: whole-recording Float32Array for diarization (finding 3, memory) - Plan 2 transcribes in 600 s windows and gathers per-segment stats per window (diarizeStats) - cost if wrong: memory pressure on 2 h imports.
- Final: Ruling: declined-to-judge items (key injection, consent, network config, status() probe, docx on Hermes, hierarchical merge over budget, transport retries, OLIST_RE) stand as reviewer described; key/consent/network/docx-on-device belong to Plan 2/3 - cost if wrong: found in Plan 2 device testing.

### Review findings fixed
- Idempotency-Key Math.random fallback on Hermes - "request ids on a phone" (2 tests) RED->GREEN, suite 126/126
- unescaped logo_position/footer_alignment/page_number_position in HTML - "profile choices that bypassed validation" RED->GREEN, suite 126/126
- attendee splitting divergence from _split_names - "attendee names split like the desktop" RED->GREEN, suite 126/126
- diarization UI freeze / no cancel / O(n^3) clustering - "fast clustering gives exactly the desktop's clusters" + "yields to the UI ... cancelled part-way" RED->GREEN, suite 126/126

### Deferred minors
- part cache keyed by full prompt (~8 MB worst case) - key by sha256.
- purity test misses require()/dynamic import()/node built-ins.
- no emoji in export tests; no test for data:text/html logo refusal.
- fetchTransport doesn't check an already-aborted signal before fetch.
- decodeWav16 doesn't enforce 16 kHz.
- insights uses toLowerCase where desktop uses casefold.
- chunkChars 0 gives 1500 (desktop 6000).
- vitest.config.ts ESM warning (rename to .mts).
- android/AGENTS.md is Expo template boilerplate.
- minSdk 26 not in app.config (Plan 2 Task 1 adds expo-build-properties).
- toLocaleString relies on Hermes Intl - verify on device.

## Plan 2: storage-device-services

### Rulings
- Task 1: Ruling: react-dom pinned to 19.2.3 via `npx expo install react-dom` - npm install of audio-studio/whisper.rn failed ERESOLVE on react-dom 19.3.1 vs react 19.2.3 - cost if wrong: none (web only).
- Task 1: Ruling: "expo-sharing" added to plugins (expo install asked for it) - cost if wrong: one plugin line.
- Task 1: Ruling: app.config.ts hides the key itself with node:crypto (Expo's config loader cannot require other .ts files; prebuild failed "Cannot find module ./src/core/keyBox") - keyBox.ts keeps revealKey/hideKey; the appConfig test proves the round trip - cost if wrong: none.
- Task 1: Ruling: added plugins/withNinja.js + build-mirror auto-detects VS Build Tools ninja 1.12 (SDK ninja 1.10 failed: "Filename longer than 260 characters" for gesture-handler codegen objects, LongPathsEnabled=1 already) - cost if wrong: builds on a machine without VS need MICO360_NINJA set.
- Task 1: Ruling: build-mirror -Abis (default arm64-v8a,armeabi-v7a,x86_64) passed as ORG_GRADLE_PROJECT_reactNativeArchitectures (the -P form was not applied) - x86 32-bit dropped - cost if wrong: no 32-bit x86 emulator support.
- Task 2: Ruling: promptId's private Math.random hex replaced by core/random randomHex(3) (same format) - cost if wrong: none.
- Task 2: Ruling: LIKE escaping escapes the escape character first (plan escaped \ % _ together, fine) and listProfiles also orders by id for stable ties - cost if wrong: none.
- Task 4: Ruling: recorder requests streamFormat "float32" - on native the stream data is otherwise base64 and the muted-mic watch would never see samples - cost if wrong: watch silently inactive.
- Task 4: Ruling: SilenceWatch treats any non-zero sample as sound (plan also ignored |x|<=1e-6) - matches desktop M37 'exact zeros' - cost if wrong: dithered-silence mics not flagged.
- Task 5: Ruling: whisper.rn decodes an ArrayBuffer as 16-bit PCM (cpp decodePcm16), not float32 as planned - adapter converts float32 -> int16 - cost if wrong: Whisper would transcribe noise.
- Task 5: Ruling: import "whisper.rn/index" - the package's exports map has no "." entry, so the bare specifier fails tsc - cost if wrong: one import path.
- Task 5: Ruling: fileDurationMs = WAV size based for our recordings, 0 (indeterminate progress) for imports - audio-studio has no metadata API - cost if wrong: imports show no percentage.
- Task 5: Ruling: model download cancellation uses DownloadTaskOptions.signal (SDK 57 supports it) instead of pauseAsync; File.move/copy are async in SDK 57 and are awaited - cost if wrong: none.
- Task 6: Ruling: LogoData = DocxLogo + dataUri (extends the Plan 1 type rather than restating it) - cost if wrong: none.
- Task 7: Ruling: Step 5 (cross-host 307 probe on the emulator) moves to Plan 3's E2E fake server, as the step itself says - cost if wrong: redirect behaviour unverified until Plan 3.
- Final: Ruling: declined-to-judge items (action_overrides vs action_items table, audio-studio/expo download instead of expo-audio/background-downloader, low-storage warning, SilenceWatch latch, Whisper unload on background, 600 s word split, cache/exports) stand as plan decisions; low-storage warning is a gap carried to Plan 3 polish - cost if wrong: a model download or long recording can fail on a full phone with the OS error.

### Review findings fixed
- C1 recovery loads whole WAV into memory - "WAV repair ... (header only, in place)" (3 tests) RED->GREEN, suite 194/194
- I1 file:/ vs file:/// URIs never matched - recovery now compares by file name and stop() normalises the URI (native; covered on device)
- I2 cancel during import returned partial transcript / wrapped error - "a decoder that wraps callback errors still reports a cancel" RED->GREEN (+ guard test), suite 194/194
- I3 progress only per 600 s window - "progress moves while a window is being recognised" RED->GREEN, suite 194/194
- I4 no download resume - resumePlan test RED->GREEN; adapter keeps .part and fetches the rest with Range (device check pending)
- I5 phone-call pause invisible - onRecordingInterrupted forwarded; record screen banner (native)
- M3 (re-graded Important: autosave vs generation lost fields) - "an autosave and a finishing generation never overwrite each other's fields" RED->GREEN
- M4 (re-graded Important: Review Focus 5) - "a logo stored on the phone in different base64 formatting" RED->GREEN
- M6 (re-graded Important: release config carried test hosts) - "a release build allows plain HTTP only to the Connect host" RED->GREEN
- M1 $Abis param missing (my earlier edit silently failed) - build script

### Deferred minors
- fileDurationMs assumes 16 kHz mono for imported .wav (progress only).
- hashing 190 MB after download blocks JS ~5-10 s without a "Verifying" message.
- built-in prompts are re-seeded on launch (deleting one is temporary; UI says so).
- reminders inexact on Android 12+ without SCHEDULE_EXACT_ALARM.

## Plan 3: screens-and-e2e

### Rulings
- Ruling: Tasks 2-7 verified with one native build and the Task 8 emulator run instead of a build per task - a full Gradle build takes 6-15 min - cost if wrong: a failure is harder to localise (none occurred beyond the bundling fix below).
- Ruling: whisper.rn needs the Node 'buffer' module under Metro - added buffer@6 - cost if wrong: none.
- Ruling: expo-sqlite's runAsync requires params, so the app wraps it in asDb() instead of passing SQLiteDatabase as Db - cost if wrong: none.
- Ruling: e2e uses adb + uiautomator (Maestro not installed); profile import via the system file picker is not driven in E2E (unit-tested in Plan 2), a company is created in Settings instead; the PDF's content isn't read back (release builds aren't debuggable for run-as) - the share sheet opening proves printToFileAsync produced the file - cost if wrong: a PDF layout problem on device is found only by the manual checklist.
- Ruling: editing.test.ts written together with editing.ts (not watched failing first) - small pure helpers - cost if wrong: a test that passes for the wrong reason.
- Ruling: build-mirror uses `npm install` on an existing mirror and `--no-daemon` (a Gradle daemon held node_modules files, npm ci failed EBUSY); stopped only the daemon whose log showed it served this mirror - cost if wrong: slower builds.
- Final: Ruling: declined-to-judge items (Plan 2 internals, PDF fidelity, reminders after reboot, lastUpdateCheck written on failure, runner Gradle, Hermes whisper speed) stand; reminders-after-reboot goes on the real-phone checklist - cost if wrong: reminders lost after a phone restart until the app is opened.

### Review findings fixed
- I1 Android back during recording left recorder running - useLeaveGuard on record screen (native/UI; verified on emulator after rebuild)
- I2 discarded recordings resurfaced as interrupted - discard deletes the WAV; banner offers delete; abandoned import copy deleted
- I3 Generate twice broke progress/cancel - synchronous busy ref
- I4 returning mid-generation showed nothing - "the running generation can be awaited, and is gone once finished" RED->GREEN; screen resumes it, editor read-only meanwhile
- I5 leaving Transcribe silently cancelled - leave guard with confirm
- I6 signing plugin never compiled - local build with the plugin applied and MICO360_KEYSTORE_PATH=project debug.keystore (public test key) - see build result line
- I7 apksigner failure masked by tee - set -o pipefail
- I8 update check limited to 30 newest releases - "looks past a page full of desktop releases" RED->GREEN (per_page=100, up to 5 pages)
- I9 apply names discarded transcript edits - confirm when transcript differs from the segments
- I10 no low-storage warning (spec 6) - lowStorageWarning before recording and model download
- minor (re-graded Important: crash on every launch) DbProvider onError alert
- minor (re-graded Important: first release would fail) PKCS12 single password noted in script and README
- minor error banner "Try again" only for generation errors; export flushes details
- minor mic "blocked" banner gains Try again

### Deferred minors
- preview HTML rebuilt on every keystroke (useMemo).
- toolbar Undo only reverts toolbar edits.
- defaultProfileId pointing at a deleted company leaves no chip selected.
- prompt favourite toggle resets unsaved edits; meeting type delete has no confirm, name not trimmed before clash.
- company remove/duplicate lack try/catch; accent colour unvalidated (sanitizeProfile falls back on save).
- updates check() lacks try/catch; release notes shown as raw Markdown.
- spec deviations - delete button+confirm instead of swipe/undo; Share only (no Save to files); no owner filter (search covers); e2e docs live in android/README.md.
- accessibility of the action-item meeting link and Switch roles.
- workflow NDK time / 20 s smoke wait unverified until the first tag.

## Plan 4: release-and-updates

### Rulings
- Ruling: the Connect key must be in the Gradle step's environment (expo-constants re-evaluates app.config.ts while bundling), so the workflow passes it there, not at prebuild - cost if wrong: a keyed release would ship Local-only.
- Ruling: the release workflow cannot be run here (needs a pushed tag and the owner's secrets); verified by YAML parse, the version-extraction command, the signing plugin's unit test, and the local build of the same steps - cost if wrong: the first tag run fails and needs a fix.
- Ruling: a release without the keystore fails rather than publishing a debug-signed APK; release notes for 1.0.0 do not say Local-only, so tagging without a Connect key secret fails by design - cost if wrong: none.
- Final: Ruling: declined-to-judge items (Plan 2 internals, PDF fidelity, reminders after reboot, lastUpdateCheck written on failure, runner Gradle, Hermes whisper speed) stand; reminders-after-reboot goes on the real-phone checklist - cost if wrong: reminders lost after a phone restart until the app is opened.

### Review findings fixed
- I1 Android back during recording left recorder running - useLeaveGuard on record screen (native/UI; verified on emulator after rebuild)
- I2 discarded recordings resurfaced as interrupted - discard deletes the WAV; banner offers delete; abandoned import copy deleted
- I3 Generate twice broke progress/cancel - synchronous busy ref
- I4 returning mid-generation showed nothing - "the running generation can be awaited, and is gone once finished" RED->GREEN; screen resumes it, editor read-only meanwhile
- I5 leaving Transcribe silently cancelled - leave guard with confirm
- I6 signing plugin never compiled - local build with the plugin applied and MICO360_KEYSTORE_PATH=project debug.keystore (public test key) - see build result line
- I7 apksigner failure masked by tee - set -o pipefail
- I8 update check limited to 30 newest releases - "looks past a page full of desktop releases" RED->GREEN (per_page=100, up to 5 pages)
- I9 apply names discarded transcript edits - confirm when transcript differs from the segments
- I10 no low-storage warning (spec 6) - lowStorageWarning before recording and model download
- minor (re-graded Important: crash on every launch) DbProvider onError alert
- minor (re-graded Important: first release would fail) PKCS12 single password noted in script and README
- minor error banner "Try again" only for generation errors; export flushes details
- minor mic "blocked" banner gains Try again

### Deferred minors
- preview HTML rebuilt on every keystroke (useMemo).
- toolbar Undo only reverts toolbar edits.
- defaultProfileId pointing at a deleted company leaves no chip selected.
- prompt favourite toggle resets unsaved edits; meeting type delete has no confirm, name not trimmed before clash.
- company remove/duplicate lack try/catch; accent colour unvalidated (sanitizeProfile falls back on save).
- updates check() lacks try/catch; release notes shown as raw Markdown.
- spec deviations - delete button+confirm instead of swipe/undo; Share only (no Save to files); no owner filter (search covers); e2e docs live in android/README.md.
- accessibility of the action-item meeting link and Switch roles.
- workflow NDK time / 20 s smoke wait unverified until the first tag.
