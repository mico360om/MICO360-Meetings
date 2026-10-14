# MICO360 Meetings for Android — design

Date: 2026-10-15 · Status: approved design, awaiting spec review · Target: v1.0.0 (`android-v1.0.0`)

## 1. Goal and success criteria

An Android version of MICO360 Meetings for the same users as the desktop app: record or import a
meeting on the phone, transcribe it, turn it into minutes with the company's templates and branding,
and share a professional PDF — plus action items, reminders and insights.

Done means:

- A signed APK published on GitHub Releases that installs on Android 8.0+ (API 26) phones.
- The full flow works on a real phone: record (screen off) → transcribe on the phone → minutes from
  MICO360 Cloud → edit → PDF in the company's design → shared to WhatsApp / e-mail.
- Templates and company profiles exported from the desktop app import unchanged, and the reverse.
- Unit, parity and emulator end-to-end tests pass in CI; the release workflow refuses to publish otherwise.

## 2. Decisions (from the brainstorm)

| Topic | Decision |
|---|---|
| Where AI runs | Transcription **on the phone** (whisper.rn). Minutes from **MICO360 Connect** (OpenAI-compatible `POST /v1/chat/completions`). No local LLM. |
| Cloud key | **One shared key built into the APK** at build time from a GitHub secret. Never in source. A separate Connect application key for mobile is recommended so it can be revoked without touching desktop. |
| Scope | Core flow, branded exports, action items & reminders, extras (speaker identification, insights, template/profile import-export, in-app updates). |
| Not possible on Android | Live-meeting auto-detect; recording other apps' audio (Teams/Zoom block playback capture). Left out. |
| Distribution | Signed **APK on GitHub Releases** of this repo; in-app update check. No Play Store in v1. |
| Stack | **Expo (SDK 57) + React Native + TypeScript**, as in MICO360 Tasks. Expo dev build / prebuild with local Gradle; no Expo cloud account. |

## 3. Architecture

The app lives in `android/` of this repository (an Expo project; `npx expo prebuild` generates the
native `android/android/` folder, which is not committed). Four layers, each testable on its own:

```
android/
  app.config.ts            app id com.mico360.meetings, version, injected build constants
  src/core/                pure TypeScript — no React Native imports; unit-tested with vitest
  src/native/              thin wrappers over device APIs (one file per capability)
  src/store/               SQLite schema, repositories, file locations
  src/ui/                  screens, components, theme
  test/                    vitest unit + parity tests, fixtures
  .maestro/                emulator end-to-end flows
  tools/                   make-keystore script, parity fixture generator (Python, desktop side)
```

### 3.1 Core (`src/core/`, pure TypeScript)

Ported from the desktop modules named in brackets, keeping their behaviour; each port has parity tests
against fixtures produced by the Python code (§8).

| Module | Responsibility |
|---|---|
| `mdBlocks` [`export/md_blocks.py`] | Markdown → blocks (headings, key/value lines, bullets, numbered lists, tables with pipes in cells). Shared by preview and all exporters. |
| `prompts` [`core/prompts.py`] | Built-in prompt library (14 prompts, 6 categories), `build_generation_prompt`, reduce/merge prompts, custom prompts, restore built-in. |
| `meetingTypes` [`core/meeting_types.py`] | Meeting-type presets (prompt, style, branding). |
| `pipeline` [`core/generation.py`] | Minutes pipeline: single pass for short transcripts; chunk → partial notes → hierarchical merge for long ones; cancel; per-part cache. |
| `cloudClient` [`core/cloud_client.py`] | `GET /v1/models`, `POST /v1/chat/completions`, following the Connect API guide exactly as the desktop client does since 2026-10-15 — see §5.1. |
| `actionItems` [`core/tasks.py`] | Extract action items from minutes; statuses, priorities, deadline parsing (dates without a year), overdue and reminder counts. |
| `ics` [`core/calendar_ics.py`] | Calendar export with stable UIDs, CRLF, local times. |
| `profiles` [`core/profiles.py`] | Company profile model, tolerant import (`_coerce`/`_choice`), JSON export with embedded base64 logos — the desktop's format. |
| `diarization` [`core/diarization.py`] | Speaker identification (MFCC without c0, per-recording normalisation, standard-error distance, average linkage, threshold 0.05) on 16 kHz PCM. |
| `insights` [`core/insights.py`] | Trends, recurring topics (incl. Arabic), action-item health. |
| `updater` [`core/updater.py`] | Version compare, pick the APK + its `.sha256` from a release, verify. |
| `rtl` [`export/rtl.py`] | Arabic/Hebrew detection for direction. |
| `exportHtml` [`export/pdf_designs.py`, `pdf_export.py`] | One HTML/CSS document per design — classic, modern, formal, compact — with letterhead, logo, details panel, attendee grid, coloured action-item statuses, page-break rules (`break-inside: avoid` for table rows and heading+first block), footer with page numbers. |
| `docx` [`export/docx_export.py`] | Word export with the `docx` library from the same blocks. |
| `txt`, `md` | Plain text and Markdown exports. |

### 3.2 Device wrappers (`src/native/`)

| Wrapper | Library | Notes |
|---|---|---|
| `recorder` | `expo-audio` + a small Expo module for an Android **foreground service** (microphone type) | 16 kHz mono WAV; pause/resume; level meter; keeps recording with the screen off; pauses on an incoming call / audio-focus loss and tells the user; **muted-mic warning** after 8 s of digital silence (desktop M37). |
| `whisper` | `whisper.rn` | Transcribe a WAV with progress and cancel; language auto / English / Arabic; returns timed segments. |
| `models` | `@kesha-antonov/react-native-background-downloader` | Download ggml Whisper models (tiny / base / small; default **base**) from pinned URLs, resume, SHA256 check against pinned hashes, delete. |
| `media` | `expo-document-picker`, `expo-file-system`, FFmpeg-free decode via `expo-audio` | Import audio/video files; convert to 16 kHz WAV for Whisper. Unsupported formats give a clear message. |
| `print` | `expo-print` | HTML → PDF with Android's print engine; fonts embedded by the WebView (Noto incl. Arabic). |
| `share` | `expo-sharing` | Android share sheet (WhatsApp, Gmail, Drive…). |
| `notify` | `expo-notifications` | Reminder notifications for due / overdue action items; recording notification. |
| `install` | small Expo module | `ACTION_VIEW` on the downloaded APK (`REQUEST_INSTALL_PACKAGES`). |

### 3.3 Storage (`src/store/`)

- `expo-sqlite`: tables `meetings` (id, title, date, attendees, type, profile_id, transcript, minutes_md,
  status draft/final, audio_path, created/updated), `action_items` (meeting_id, text, owner, deadline,
  status, priority, stable key), `profiles`, `prompts`, `meeting_types`, `settings`. Schema versioned
  with migrations from v1.
- Files in the app's private storage: `recordings/`, `exports/`, `models/`, `logos/`. Nothing is written
  to shared storage except through the share sheet or the system file picker.
- Edits autosave (debounced) so a crash or kill never loses minutes.

### 3.4 Screens (`src/ui/`)

Bottom tabs: **Meetings · Action Items · Insights · Settings**; a floating **New meeting** button.

1. **Meetings** — history with search, sort and filter (draft/final); swipe to delete with undo.
2. **New meeting** — a stepper that fits one screen per step:
   *Source* (Record / Import file / Paste text) → *Details* (title, date, attendees, meeting type,
   company) → *Transcript* (progress, cancel, edit, speaker names) → *Minutes* (Generate via Cloud,
   progress per part, retry) → *Edit & share*.
3. **Minutes editor** — Markdown text with a toolbar (heading, bold, italic, list, table, undo) and a
   live preview tab; RTL when the text is Arabic.
4. **Export** — format (PDF / Word / Text / Markdown), company branding (the meeting's company by
   default, or none), design preview from the company's chosen design; then Share or Save to files.
5. **Action Items** — across meetings; filters (status, owner, overdue); edit in place; CSV export;
   calendar (.ics) export.
6. **Insights** — the desktop dashboard's figures as cards and simple charts.
7. **Settings** — Transcription (model download/delete, language, speaker identification), Companies
   (profiles, logo from gallery, PDF design per company with preview), Prompts, Meeting types,
   Import/Export templates & profiles (desktop-compatible files), Cloud (status, model, encrypted or
   not), Updates, About & help, Privacy.

Theme follows the desktop design system's colours and type; light and dark follow the phone.

## 4. Data flow

```
Record (WAV 16k) ─┐
Import file ──────┼─▶ 16 kHz WAV ─▶ whisper.rn ─▶ segments ─▶ [diarization] ─▶ transcript
Paste text ───────┘                                                              │
                                    prompt + meeting type + transcript ◀─────────┘
                                                 │
                                   pipeline (single or chunk→merge)
                                                 │  POST /v1/chat/completions (MICO360 Connect)
                                                 ▼
                                    minutes (Markdown) ─▶ action items ─▶ SQLite
                                                 │
                         editor ◀────────────────┘
                            │
                            ▼
       mdBlocks ─▶ HTML (design + profile) ─▶ expo-print ─▶ PDF ─▶ share sheet
                └▶ docx / txt / md ───────────────────────────────▶ share sheet
```

## 5. MICO360 Cloud connection and the built-in key

- **Endpoint**: the desktop's `MICO360_CONNECT_BASE_URL` and HTTPS candidates, copied into
  `src/core/config.ts`. On start the app probes the HTTPS candidates (as desktop `_probe_https`) and
  switches to the first verified one.
- **Plain HTTP**: Android blocks cleartext by default. A network-security config allows cleartext for
  the Connect host **only**. While the connection is unencrypted the app asks once per install before
  sending meeting text ("Your transcript will be sent unencrypted … Continue?"), as the desktop does.
- **Key injection**: the release workflow reads the secret `MICO360_MOBILE_CONNECT_KEY` (falls back to
  `MICO360_CONNECT_API_KEY`), and `app.config.ts` places it, XOR-split with a per-build salt, into
  `extra`; `src/core/config.ts` reassembles it at run time. This deters casual extraction only — anyone
  with the APK can recover it. Mitigations: a dedicated mobile application key in the Connect console
  with its own quota; revoke and rotate by publishing a new APK.
- **No key in the build** ("Local-only" build): recording, transcription, editing, exports and action
  items work; *Generate minutes* is disabled with "Minutes need MICO360 Cloud, which isn't enabled in
  this build." The release notes must say so (same rule as desktop).
- The key is never logged, never shown, and never written to SQLite or files.

### 5.1 Conformance with the MICO360 Connect API guide (v2.6.7, `MICO360 Connect/docs/API-Guide.md`)

The mobile client implements the same rules as the desktop `cloud_client.py` and ports its tests
(`tests/fixes_ai.py`, *API* section) to vitest against a fake server.

| Guide | Rule |
|---|---|
| §1, §5 | `POST {base}/chat/completions` with `Authorization: Bearer <key>`, `Content-Type: application/json`, `Accept: application/json`; body `model`, `messages` (one `user` message), `stream: false`. Fields the platform ignores (`temperature`, …) are not sent. |
| §11 | Every logical request gets an `Idempotency-Key` (`mico360-meetings-<32 hex>`) and an `X-Mico-Correlation-Id` (`MM-<24 hex>`); retries reuse both. |
| §5, §11 | Requests over **32,000 characters** are refused locally with a clear message; the pipeline sizes parts and merged notes so every request fits, template included. |
| §7 | Client timeout 330 s (the OpenAI surface waits 300 s, then answers 504). |
| §8 | Retry only 429, 502, 503 and transport errors (≤ 4 attempts). 429/503 wait exactly `Retry-After`; a `Retry-After` longer than 30 s (quota, fleet down) is reported with the wait, never retried early; a 503 without `Retry-After` (model unavailable, no alternative) is final. |
| §5, §8 | A 504 is **not resubmitted**: the job id in the message is collected with `GET /api/v1/jobs/{id}` using the native `X-Mico-Api-Key` header, polled every 3 s up to 10 min; `Completed` → its output; `Cancelled` (never started) → sent again; `Failed`/`TimedOut` → its error. |
| §6, §8 | `X-Mico-Model-Warning`, the body's `warning` and `X-Mico-Warning` are shown to the user after generation. |
| §8 | Error messages use the server's text from either envelope, a hint when the body is empty, and the correlation id as "Reference for support". |
| §9 | Encrypted endpoint: `https://ai.mico360.com:5311/v1` (the documented TLS port), then `:443`; never `https` on 5310. A `307/308` to HTTPS on the **same host** switches to it; any other redirect is not followed. |
| §9 (measured) | React Native's `fetch` (OkHttp) follows redirects itself, so the client sees only the final response and refuses it when it came from another address. Measured on the API 34 emulator (2026-10-15, `android/e2e`): a `307` to another port was followed **without** the `Authorization` header (OkHttp drops it when scheme, host or port change), and the app showed "redirected the request to another address, which is not followed". The key therefore never reaches a different server. |

## 6. Errors and edge cases

| Situation | Behaviour |
|---|---|
| Microphone permission denied | Explain and offer *Open settings*; recording disabled until granted. |
| Mic sends digital silence | Warning after 8 s; recording continues. |
| Phone call / another app takes audio focus | Pause, notification, resume button. |
| App killed while recording | The WAV written so far is kept (header fixed on next start) and offered as a draft. |
| Storage low (< 200 MB) | Warn before recording / model download. |
| Model download fails or checksum mismatch | Resume / retry; a bad file is deleted, never used. |
| Cloud unreachable / 401 / quota | Clear message per case; retries for transient errors; the transcript is kept; *Retry* resumes from completed parts (part cache). |
| Very long meeting (2 h+) | Chunked transcription with progress; chunked minutes pipeline; diarization capped as desktop. |
| PDF with Arabic / long tables | Noto Arabic font; RTL blocks; rows never split across pages; wide tables shrink font. |
| Update APK checksum mismatch | Never installed; message. |
| Imported template/profile clashes | Never overwrites; renamed copy, as desktop `unique_name`. |

## 7. Releases and updates

- Version in `android/app.config.ts` (`version` + `android.versionCode`); tag **`android-vX.Y.Z`** must
  match (the desktop release workflow listens to `v*` and is not triggered).
- `.github/workflows/android-release.yml` on `android-v*`: install Node + JDK 17 + Android SDK, `npm ci`,
  unit + parity tests, `expo prebuild`, Gradle `assembleRelease` signed with the keystore from secrets
  (`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`),
  emulator smoke test, then publish `MICO360Meetings-Android-X.Y.Z.apk`, its `.sha256` and
  `RELEASE_NOTES_android-vX.Y.Z.md` with **`make_latest: false`** so the desktop updater's
  `/releases/latest` keeps returning desktop releases.
- `.github/workflows/android-ci.yml` on pushes touching `android/`: typecheck, lint, unit + parity tests.
- The keystore is created by the user with `android/tools/make-keystore.ps1` (wraps `keytool`) and
  stored only in GitHub secrets and the user's own backup; it is never committed and Claude never holds it.
- In-app updates: on start (and from Settings) list releases, take the newest `android-v*` that is not a
  pre-release, compare with the app version, download the APK, verify its SHA256, open the installer.

## 8. Testing

- **Unit tests** (vitest) for every core module.
- **Parity tests**: `android/tools/make_parity_fixtures.py` runs the desktop Python code (md_blocks,
  action items, deadlines, ics, prompts, diarization labels on synthetic voices, version compare) over
  a shared input set and writes expected outputs to `android/test/fixtures/`; the TypeScript ports must
  match them.
- **Export checks**: PDFs printed on the emulator are read back (text extraction) for every design ×
  Arabic/English × long-table fixture: no missing words, rows not split.
- **End-to-end** (Maestro on the API 34 emulator) against a local fake Connect server: paste text →
  generate → edit → export PDF → share intent; import audio → transcribe with the tiny model →
  minutes; action item → reminder; profile import from a desktop export.
- **Manual on a real phone**: background recording with the screen off for 30 min, a phone call during
  recording, a real two-person meeting, WhatsApp share.

## 9. Out of scope for v1

Live-meeting auto-detect; recording other apps' audio; local (on-phone) LLM; Play Store; iOS;
syncing meetings between phone and desktop (only template/profile files move); e-mail via SMTP (the
share sheet covers e-mail).

## 10. Risks

| Risk | Mitigation |
|---|---|
| Built-in key extracted from the APK | Dedicated mobile key with quota; rotate by new release (accepted by the owner). |
| Whisper speed on low-end phones | Default base model, tiny offered; progress + cancel; 2 h audio may take long — shown before starting. |
| Background recording killed by aggressive OEMs | Foreground service + battery-optimisation exemption prompt; partial WAV recovery. |
| expo-print layout differs from desktop reportlab PDFs | Designs re-created in HTML/CSS and checked by the export tests; visual parity is close, not pixel-identical. |
| Unencrypted Connect endpoint | HTTPS probe; explicit consent while unencrypted; cleartext allowed for that host only. |
