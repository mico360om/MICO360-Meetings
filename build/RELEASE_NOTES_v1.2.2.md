# MICO360 Meetings v1.2.2

A polish-and-reliability release on top of v1.2.1. Still fully **offline by
default** — audio is transcribed locally with Whisper and minutes are written by
a local Ollama model (MICO360 Cloud stays an optional mode).

## Fixes

- **Auto-update now runs.** Older installs had a blank update repo saved, which
  silently disabled the startup update check. Settings now heal that on launch,
  so update checks work again.
- **Incompatible AI models no longer break generation.** Vision and embedding
  models (e.g. `llama3.2-vision`, architecture `mllama`) are filtered out of the
  model picker, and if one still fails to load you get a clear message ("pick a
  text model like llama3.1 / update Ollama") instead of a raw server error.
- **New Meeting:** removed a redundant "Transcribe & generate minutes" button
  that duplicated — and could short-circuit — the wizard's "Create meeting"
  action. Step 1 now has a single clear **Transcribe** action.

## Branding

- **New app icon** — a proper MICO360 mark (maroon squircle with the 360 swoosh)
  replaces the old placeholder, crisp from 16px to 256px, in the taskbar, window,
  installer and shortcuts.

## Under the hood

- All test suites (module, integration, parser, end-to-end) pass through the
  one-command release gate.
