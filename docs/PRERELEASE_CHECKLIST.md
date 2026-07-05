# Pre-release hardware smoke checklist

The automated suites (`tests/qa_check.py`, `tests/module_tests.py`,
`tests/consistency_audit.py`) run **headless** and can't touch a real
microphone, speakers, GPU, or the installer. Run this **manual pass on real
hardware before tagging a release** — the two recording bugs in v1.1.0 slipped
through precisely because CI couldn't exercise the audio devices.

> Tick every box on a machine with a working mic + speakers. Ideally also test
> once on a machine **with** an NVIDIA GPU and once **without**.

## 0. Build & launch
- [ ] `powershell -File build\build_all.ps1` produces `build\Output\MICO360Meetings-Setup.exe`.
- [ ] Installer runs on a clean machine; app launches; window icon + title correct.
- [ ] Version in the sidebar / About / Updates all match the tag.

## 1. Recording (the CI blind spot)
- [ ] **Mic**: record 5s speaking → Stop & Save → summary shows a non-zero size + duration.
- [ ] **System audio**: play a YouTube clip, record "System audio" → captured audio is audible (not silent).
- [ ] **Both**: record "Mic + System" while audio plays → both voices present.
- [ ] **Screen + audio**: record 5s → MP4 is not black and has sound.
- [ ] **No-mic machine / muted mic**: shows a clear "no audio" message, not a crash or hang.
- [ ] Pause / Resume / Stop / Cancel all behave; visualizer moves; timer counts.

## 2. Transcription (GPU vs CPU)
- [ ] On a GPU machine: transcription completes (no `cublas64_12.dll` error — must fall back to CPU).
- [ ] On a CPU machine: transcription completes; text is accurate for clear speech.
- [ ] Speaker identification (if enabled) labels Speaker 1/2 for a two-person clip.

## 3. Minutes & data
- [ ] Generate minutes with a real Ollama model; header, decisions, action items populated.
- [ ] `.ics` import fills Meeting title/date/attendees; they appear in the minutes.
- [ ] Action Items page aggregates items from ≥2 meetings; status double-click cycles; CSV exports.
- [ ] Edit transcript + minutes; close & reopen app → autosaved content is restored from History.
- [ ] Export DOCX / PDF / TXT / MD / HTML with a company profile — letterhead + page numbers correct.

## 4. UI / responsiveness
- [ ] Resize the window small→large and set Windows zoom to 125% / 150% — no overlap or cut-off.
- [ ] Dark and light themes both render correctly.
- [ ] Every nav page opens; keyboard shortcuts (Ctrl+1..8, Ctrl+G/E/S, F1) work.

## 5. Updates & lifecycle
- [ ] Updates page shows current vs latest correctly against GitHub.
- [ ] (If a newer release exists) Download → Install & restart replaces the app and reopens.
- [ ] Second launch is blocked (single-instance).
- [ ] App closes cleanly; no MICO360Meetings.exe left in Task Manager.

## 6. Sign-off
- [ ] All boxes ticked → bump version, `git tag vX.Y.Z`, push → CI builds & publishes.
