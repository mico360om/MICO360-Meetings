# MICO360 Meetings v1.2.1

A big usability, design and reliability release. Everything still runs **fully
offline by default** — audio is transcribed locally with Whisper and minutes are
written by a local Ollama model.

## New features

- **Step-by-step New Meeting wizard** — Source → Transcript → Setup → Review →
  Minutes, with a progress stepper, Back/Next, per-step validation, a review
  summary, and a clear **Create meeting** action.
- **One-click "Transcribe & generate minutes"** for the common path.
- **MICO360 Cloud AI mode** — generate minutes on the company AI server
  (OpenAI-compatible) as an alternative to local Ollama; pick the mode in
  Settings. Transcription always stays local.
- **"AI not ready" banner** on New Meeting with one-click fixes (install a model,
  open Settings, switch to Local).
- **Editable meeting titles** so History is easy to scan.
- **Empty states** on History and Action Items.
- **Inline status dropdown** for action items (instead of double-click-to-cycle).
- **Persistent "Saved · just now" indicator** near the minutes.
- **Keyboard-shortcut cheat sheet** in Help.
- **Text-size / scale setting** (accessibility) — grow or shrink all text.
- **Settings grouped into tabs** — AI · Transcription · Email · Updates · Data.
- **Minutes open on the formatted Preview** first (not raw Markdown).
- **Git-based auto-update** for source checkouts (fast-forward only).

## Fixes

- **Data loss:** starting a new meeting could overwrite the previous one — fixed.
- **Duplicate action:** re-clicking Transcribe mid-run could start a second,
  interleaved run — fixed.
- **App hang:** a malformed table in generated minutes could freeze the Action
  Items / Preview / export — fixed (and small models' no-leading-pipe tables are
  now parsed).
- **Action Items:** cancelled items were counted as "open"; deleting a meeting
  left orphaned status overrides — both fixed.
- Editing a meeting opened from History no longer forks a duplicate record.

## Security & branding

- **Verified updates:** downloaded installers are checked against a published
  SHA256 (and Authenticode signature) before they run.
- Applied the **MICO360 brand identity** (logo + maroon/charcoal theme) across the
  whole app and removed leftover off-brand colours.

## Reliability

Reworked test suites (module, integration, parser, and a real end-to-end
workflow) run through a one-command release gate — all green for this build.
