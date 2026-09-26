# MICO360 Meetings — Bug Report

**Version reviewed:** 1.2.3 (commit `77e5411`) · **Date:** 26 September 2026
**Scope:** all 55 source modules (~12,150 lines), every screen and workflow, integrations (Whisper, Ollama, MICO360 Cloud, SMTP, GitHub updater, Outlook/Teams detection), exports, the Inno Setup installer, the PyInstaller build, CI/release workflows and the test suites.

## How this was produced

Six reviewers each took one area of the codebase in parallel. They read it end to end and reproduced suspected defects with throwaway scripts against a scratch data folder, so the real app data and the repo were untouched. Every Critical finding, and most High ones, was then re-checked against the source by the lead reviewer; these are marked **✔ verified**. Findings are listed only with a file:line location and a concrete failure scenario.

- **Confidence:** *Confirmed* means reproduced, or the full code path was traced. *Likely* means strong evidence but not executed (usually needs real hardware or a real network).
- **Severity:** *Critical* = data loss, a crash in a common path, or a security exposure. *High* = a feature is broken in a realistic scenario. *Medium* = edge case or degraded behaviour. *Low* = minor or cosmetic.

## Summary

| Severity | Count |
|---|---|
| Critical | 5 |
| High | 26 |
| Medium | 44 |
| Low | 15 |
| **Total** | **90** |

Why the release gate still says **READY 10/10**: the suites exercise happy paths in one session on the developer's machine. None of the Critical bugs is reachable that way: they involve switching meetings mid-run, a second auto-recorded call, remote participants, worker-thread exceptions, and network interception. Some gate checks also cannot fail (see H26, M44).

### Recommendation for the pending v1.2.3 release

**Do not publish v1.2.3 until at least C1–C5 and H23 are fixed.**
- C2 and C3 break the release's headline feature, auto-record: it overwrites the previous meeting and leaves out remote participants.
- H23 means a CI-built installer, which is the publish path now wired up, would ship **without** live-meeting detection at all.
- C5 means Cloud mode sends transcripts and the shared key unencrypted.

### Defects introduced during this work session (disclosed for transparency)

- **H20, H21** — Arabic PDF line order and bold-run order come from the Arabic export added this session (`export/rtl.py`, `pdf_export.py`).
- **M40** — the `release.yml` fix that moved secrets to job-level `env:` now exposes them to every step.
- **M12** — the updated Privacy Policy and Help text still contain claims that don't match behaviour: updates are checked automatically at launch, recordings are auto-deleted, Cloud traffic is unencrypted, and the Arabic editor alignment differs from what Help describes.

---

## Critical

### C1 — A worker that finishes after you switch meetings writes into, and overwrites, the other meeting ✔ verified
- **Area:** New Meeting workflow · **Confidence:** Confirmed (reproduced offscreen)
- **Location:** `ui/pages.py:984-1003` (`_on_generated`), `918-923` (`_on_transcribed`), `1352-1372` (`load_meeting`), `1112-1130` (`_new_meeting`); `ui/main_window.py:331-334`
- **Problem:** Generate and Transcribe results are applied to whatever meeting the page shows when they finish. `_on_generated` then calls `_save_history()` with the *current* `_current_id`. Nothing cancels or blocks a running worker when a meeting is opened from History, from Action Items, or with Ctrl+N.
- **Scenario:** Paste transcript A and click Create meeting. While it runs, open meeting B from History. When generation finishes, B's History record holds B's transcript with A's minutes: B's minutes are overwritten and A is never saved. With Ctrl+N instead, the minutes land in a new record that has an empty transcript.
- **Fix:** Capture the target meeting id and a token when a worker starts, and discard or redirect results whose token no longer matches. Alternatively, confirm and cancel the worker in `load_meeting`/`_new_meeting`.
- **Status: ✅ FIXED.** Each Transcribe/Generate run is bound to the meeting it was started for (job token + snapshot). Switching meetings autosaves first; an in-flight generation finishes into *its own* meeting's record, a running transcription is stopped after confirmation, and per-meeting setup fields/queue/preview are reset. Regression suite `tests/workflow_isolation.py` (part of the release gate): old code 6/16, fixed 19/19.

### C2 — Auto-record appends the next meeting to whatever is open and overwrites that record ✔ verified
- **Area:** Auto-record · **Confidence:** Confirmed (reproduced offscreen)
- **Location:** `ui/main_window.py:132-148` (`_begin_auto_record`); `ui/pages.py:468-479`, `995-999`, `1044-1058`
- **Problem:** `_begin_auto_record` never starts a fresh meeting. The live or queued transcript is appended to the current transcript, minutes are regenerated, and the result is saved under the old `_current_id` with the old title.
- **Scenario:** Generate minutes for meeting 1 and leave the app open. The next Teams call is auto-recorded. History then has one record, titled as meeting 1, with a merged transcript and minutes for meeting 2. Meeting 1's minutes are lost and meeting 2 has no record of its own.
- **Fix:** In `_begin_auto_record`, call `new_page._new_meeting()` (which autosaves first) before starting the recorder.
- **Status: ✅ FIXED.** `_begin_auto_record` saves the open meeting and starts a fresh one before recording; a generation still running for the previous meeting completes into that meeting's record. Covered by `tests/workflow_isolation.py`.

### C3 — Auto-recorded minutes contain only the local microphone; remote participants are dropped ✔ verified
- **Area:** Recording / auto-record · **Confidence:** Confirmed
- **Location:** `core/recording.py:445`, `590` (the only `_live_push` call sites; `_capture_system` at `469` never pushes); `ui/recording_panel.py:508-518`; `ui/pages.py:468-479`
- **Problem:** Auto-record starts with source "both" and live transcription on. The live buffer is fed only from the microphone, never from the WASAPI loopback that carries the other participants. When any live text exists, the finished recording is **not** queued for full transcription, and minutes are generated from the live text immediately.
- **Scenario:** A Teams call is detected and the user accepts recording. Minutes are generated only from what the user said. Everything the other participants said (present in the WAV) is silently left out.
- **Fix:** Feed the mixed or loopback signal into the live buffer, or in auto mode always transcribe the final file rather than generating from the live draft.
- **Status: ✅ FIXED.** In auto mode the full mic+system recording is always queued and transcribed (verified: the saved file carries both the microphone and the system-audio signal); the mic-only live draft is no longer used for auto-generated minutes. Manual recordings still offer the live draft, and the toast explains it is microphone-only; "Use for transcription" now *replaces* the draft instead of duplicating it. `tests/release_fixes.py` (release gate): old code 2/10, fixed 25/25.

### C4 — The crash reporter opens a Qt dialog on a worker thread, so one stray exception crashes the app ✔ verified
- **Area:** Startup / reliability · **Confidence:** Confirmed (segfault, rc=139, reproduced)
- **Location:** `crash_reporter.py:45-49`, `55-71`, `125-139`
- **Problem:** `threading.excepthook` and `sys.excepthook` (which PySide6 also calls for `QThread.run` exceptions) both call `_excepthook`, which builds and `exec()`s a `CrashDialog` on the raising thread.
- **Scenario:** Any unhandled exception in a worker crashes the whole process and kills any recording in progress. Workers that can raise here include `UpdateCheckWorker.run` (`ui/workers.py:313-315`), `LiveTranscribeWorker._flush` (`69-78`) and the capture threads.
- **Fix:** Off the main thread, only log and write the report, then post the dialog to the GUI thread (a queued signal or `QTimer.singleShot(0, …)` on `qApp`).
- **Status: ✅ FIXED.** Worker-thread exceptions are logged and written to disk, and the dialog is queued to the GUI thread through a main-thread bridge object; exceptions from a Python thread and a QThread no longer crash the process. `tests/release_fixes.py` (release gate): old code 2/10, fixed 25/25.

### C5 — Cloud mode sends transcripts and the shared API key over plain HTTP ✔ verified
- **Area:** Security / privacy · **Confidence:** Confirmed
- **Location:** `config.py:104` (`http://ai.mico360.com:5310/v1`); `core/cloud_client.py:44-50`, `67-70`, `108-112`; `.github/workflows/release.yml` / `build/build_all.ps1` (key baked into every installer)
- **Problem:** Every Cloud request, including the full transcript and the `Authorization: Bearer <key>` header, is sent unencrypted. The key is one shared credential embedded in every installer, where it can be extracted from the PyInstaller bundle.
- **Scenario:** On hotel or café Wi-Fi or behind a corporate proxy, anyone on the network path can read confidential meetings, tamper with the returned minutes, or harvest the key and use the quota.
- **Fix:** Serve the endpoint over HTTPS with default certificate verification, and move to per-install or revocable tokens. Correct the README ("no data ever leaves your computer") and the Privacy Policy (see M12).

---

## High

### Workflow, UI and shell

**H1 — Global shortcuts bypass the busy guards; Ctrl+G during generation aborts the process; shortcuts act on the hidden New Meeting page ✔ verified** · Confirmed
`ui/main_window.py:253-257`; `ui/pages.py:926-982` (`_generate` has no `isRunning()` check), `1140-1163` (`_export`)
Ctrl+G calls `_generate()` directly. A second press replaces `self._worker` while the first QThread is still running, and the process aborts (exit 127). Ctrl+E behaves the same during an export. The shortcuts are window-wide, so Ctrl+S on Settings saves the *meeting* rather than the settings, and Ctrl+G ignores queued media even though its tooltip says it transcribes.
*Fix:* route Ctrl+G through `_create_meeting` with a guard, and enable these shortcuts only while New Meeting is the current page.

**H2 — Opening a meeting from History discards unsaved work without asking ✔ verified** · Confirmed
`ui/pages.py:1352-1362`; `ui/main_window.py:331-334`
`load_meeting` replaces the transcript and minutes without calling `_autosave()` or checking `_is_dirty()`. Autosave runs every 20 s, so any edits since the last tick are lost, and a brand-new unsaved meeting is lost completely.
*Fix:* call `_autosave()` (or prompt) at the start of `load_meeting`.

**H3 — Dropping the last reference to a running QThread aborts the app (several places, including window close)** · Confirmed (reproduced: exit 127 / 0xC0000409)
`ui/recording_panel.py:388`, `487`, `508-510`, `586`; `ui/main_window.py:129-130` (watcher `wait(3000)` then `None`), `375-389` (`closeEvent` never cancels or waits for Transcribe, Generate, ModelPull, UpdateDownload or Email workers); `ui/settings_page.py:236-239`, `475-497` (double-clicking "Send test email"); `ui/pages_extra.py:794`
Workers are parentless QThreads. When a timed `wait()` expires and the reference is cleared anyway, a new worker replaces a running one, or the window closes mid-task, the process aborts and in-flight minutes are lost. `ModelPullWorker.cancel()` and `UpdateDownloadWorker.cancel()` exist but no button calls them.
*Fix:* never drop a running worker (keep it until `finished`, then `deleteLater`), confirm and cancel+wait in `closeEvent`, and add Cancel buttons for the model pull and the update download.

**H4 — A failed or cancelled transcription drops the file from the queue, and Retry does nothing** · Confirmed
`ui/pages.py:904` (popped before start), `842-853`, `1289-1291`, `1301-1305`
The file is popped from `_media_queue` before the worker runs and is never put back. The list still says "queued" but Transcribe stays disabled, and Retry reports "Add media or paste a transcript first."
*Fix:* pop the file in `_on_transcribed`, or re-insert it on failure or cancel.

**H5 — History shows every draft as "Empty", and the Draft filter never matches** · Confirmed
`ui/history_page.py:150-155`, `174-176`, `183`; `core/history.py:32-34`, `94-114`
`history.list()` leaves out the transcript column, so `_status_of()` sees `""` for every row.
*Fix:* return a cheap `has_transcript` flag from `list()` and use it for the status.

### Recording and transcription

**H6 — Recordings are kept only in the temp folder and auto-deleted after 7 days; closing mid-recording hides the file ✔ verified** · Confirmed
`core/recording.py:324`, `695`; `core/maintenance.py:21-22`; `main.py:48-49`; `config.py:162`; `ui/recording_panel.py:546`, `560-568`, `582-594`
Final recordings go to `TMP_DIR/recording_*`. `purge_tmp` deletes them at startup once they are older than `tmp_retention_days`, which defaults to 7 and is not shown anywhere in the UI. Meanwhile the panel says "Recording saved" and shows that folder. Closing the window during a recording stops it silently, with no summary and no queueing.
*Fix:* move finished recordings to a persistent folder (for example `DATA_DIR/recordings`), purge only the intermediates, and confirm before closing while recording.
**Status: ✅ FIXED.** Finished recordings are saved to `%LOCALAPPDATA%\MICO360Meetings\recordings` and are never auto-deleted; start-up clean-up removes only intermediate files; recordings older versions left in `tmp` are moved into `recordings` at start-up; quitting during a recording asks first. `tests/release_fixes.py` (release gate): old code 2/10, fixed 25/25.

**H7 — A recorder in ERROR state is never cleaned up** · Confirmed
`ui/recording_panel.py:445-450`, `359`; `core/recording.py:833-840`, `856-857`; `ui/main_window.py:140-145`
On ERROR, the panel stops its timers but never calls `rec.stop()`, stops the live worker, or resets `_auto_mode` and `_auto_generate_pending`.
*Scenarios:* the mic stays open for the whole session and writes `_aud_*.wav` at about 115 MB/h; Start again aborts the app (H3); the window title stays "● Recording"; after a failed auto-start, the next manual recording triggers generation on its own.
*Fix:* fully tear down on ERROR.

**H8 — A video recording with zero frames discards the good audio but reports "Recording saved"** · Confirmed
`core/recording.py:891-894` (also `752-755`, `788-791`)
When the camera is busy or flaky, `_mux` returns the empty video file. The full audio stays hidden in `_aud_*.wav` and is purged after 7 days.
*Fix:* when there are no frames, return the WAV (or report an error).

**H9 — Screen and Camera recordings ignore the "System audio" / "Mic + System" source and always record the mic** · Confirmed
`core/recording.py:714-730`, `822-857`; `ui/recording_panel.py:372-379`
`VideoRecorder` never reads `cfg.source`, so remote participants are missing from screen recordings of online meetings.
*Fix:* reuse the `AudioRecorder` capture and mix path, or disable system sources for video.

**H10 — Video timing follows the frame count, not the clock, so audio and video drift apart** · Confirmed (12 frames over 2.42 s produced a 1.0 s video)
`core/recording.py:725-767`
When capture runs below the target fps, the video comes out shorter than the audio. A 60-minute recording can end up with about 40 minutes of video.
*Fix:* set `frame.pts` from wall-clock time (time_base 1/1000), and start the audio clock at the first frame.

**H11 — Speaker diarization is O(n³) in pure Python and can't be cancelled** · Confirmed (200 segments took 5.8 s; about 12 min extrapolated for 1-hour meetings)
`core/diarization.py:117-139`; `core/transcription.py:187-192`
A 2-hour meeting can sit at "Identifying speakers…" for over an hour, and Cancel does nothing.
*Fix:* vectorise with Lance–Williams average-linkage updates, cap the number of segments, and check `cancel`.

**H12 — Switching browser tabs during a Google Meet call stops auto-record** · Likely
`core/meeting_watch.py:42-47`, `66-86`; `ui/workers.py:122-128`; `ui/main_window.py:164-168`
Detection depends on the *active* tab's window title. Two missed polls (about 16 s) emit `meetingEnded` and stop the recording mid-meeting.
*Fix:* don't auto-stop browser meetings on a title miss; use a long grace period or check audio or process activity instead.

**H13 — A device unplugged mid-recording isn't detected; the file is silently cut short** · Likely
`core/recording.py:646-652` (watchdog only runs while `_frames_captured == 0`), `257-264`, `309`, `442`, `587`
If a USB or Bluetooth headset drops at minute 10 of a 60-minute meeting, the summary still shows 01:00:00, but the WAV contains 10 minutes.
*Fix:* track the time of the last callback and set ERROR or reopen the device; report duration as frames ÷ rate.

### AI generation and integrations

**H14 — Network status checks block the UI thread, and the Ollama client has no timeout** · Confirmed / Likely
`ui/context.py:40-52`, `64-65`; `core/ollama_client.py:26-28`, `113-126`; `core/cloud_client.py:70`; called from `ui/pages.py:62`, `217`, `718`, `932`, `ui/main_window.py:90`, `327-329`, `ui/settings_page.py:319-369`
In Cloud mode on a network that drops traffic, the window freezes for about 15 s at startup and on every visit to New Meeting. With an unreachable remote Ollama host there is no timeout at all. A stalled model load also can't be cancelled.
*Fix:* run status checks in a worker, and give the httpx client a timeout (for example connect 3 s, read 600 s).

**H15 — Long meetings overflow the Ollama context and the start is silently truncated** · Likely
`core/ollama_client.py:116-121` (no `num_ctx`); `core/generation.py:60-75`; `core/prompts.py:109-119`
With the default 2k–4k context, the reduce step for a 90-minute meeting (about 15–20k characters of notes plus the template, which contains `MINUTES_STRUCTURE` twice) exceeds the window. Ollama drops the front of the prompt, including the instructions and the notes for the start of the meeting.
*Fix:* size `num_ctx` to the prompt, and reduce the notes hierarchically.

**H16 — The reduce prompt breaks non-minutes templates and drops the user's instructions ✔ verified** · Confirmed
`core/prompts.py:109-119`
The reduce prompt keeps only the template text before `"Transcript:"` and always adds `MINUTES_STRUCTURE`.
*Scenarios:* "TL;DR" and "Follow-up Email" templates return formal minutes for any meeting over ~6000 characters (most meetings). "Write the output in Arabic" placed after the token is lost. A template without a "Transcript:" label sends the literal `[TRANSCRIPT_HERE]` token.
*Fix:* substitute the notes at the transcript token and keep the template text on both sides of it.

**H17 — SMTP login can be sent in cleartext ✔ verified** · Confirmed
`core/emailer.py:49-60`
On ports other than 465, STARTTLS is used only if the server advertises it, but `login()` always runs. A STARTTLS-stripping middlebox, or a relay without TLS, exposes the Mailjet key and secret. The connection is also leaked if `login` raises.
*Fix:* require STARTTLS (and fail clearly if it isn't offered), and close the connection on error.

### Data and exports

**H18 — Exported `.ics` files have `\r\r\n` line endings on Windows ✔ verified (reproduced)** · Confirmed
`core/calendar_ics.py:69`, `76`
`build_ics` already joins lines with CRLF, and `write_text` then converts each `\n` to CRLF again. Folded lines break, and re-importing the app's own file truncates titles. The output is not RFC 5545 compliant, so strict clients may reject it.
*Fix:* use `write_bytes(s.encode("utf-8"))` or `newline=""`.

**H19 — A numeric field in an imported profile (for example a phone number) crashes every export ✔ verified (reproduced)** · Confirmed
`core/profiles.py:170-180` (`_coerce` passes ints through), `138-141`; crash sites `export/html_export.py:59`, `89`, `pdf_export.py:194-195`, `docx_export.py:75-77`, `txt_export.py:14-16`
An Excel phone cell stored as a number is kept as an `int`. HTML export then raises `AttributeError`, and PDF/DOCX/TXT/MD raise `TypeError`.
*Fix:* coerce every text field with `str(value).strip()`.

**H20 — Wrapped Arabic paragraphs in PDF read bottom-to-top** · Confirmed · *introduced this session*
`export/pdf_export.py:59-62`; `export/rtl.py:48`
The whole paragraph is bidi-reordered into a single visual line, and reportlab then wraps that line left-to-right. Any Arabic paragraph or table cell longer than one line comes out with its lines in reverse order.
*Fix:* wrap the reshaped logical text first, then reorder each line, or use a shaping-capable renderer.

**H21 — Arabic PDF lines that mix bold and plain text come out in the wrong order** · Confirmed · *introduced this session*
`export/pdf_export.py:59-65`, `110-113`
Each `**bold**` run is shaped and reordered on its own, then the runs are placed left-to-right. `**المسؤول:** أحمد` draws the key on the left, so the value is read first.
*Fix:* for RTL paragraphs, reverse the run order, or bidi the whole line while tracking the bold spans.

**H22 — PDF export fails when a single table cell is long (about 200+ words)** · Confirmed
`export/pdf_export.py:121-125`
The table has no `colWidths` and rows can't split across pages, so reportlab raises `LayoutError` and the user gets no PDF.
*Fix:* set explicit column widths and `splitInRow=1`, or fall back to paragraphs for oversized rows.

### Build, release and CI

**H23 — `pywin32` is missing from `requirements.txt`, so CI-built installers ship without meeting detection or Outlook integration ✔ verified** · Confirmed
`requirements.txt`; `core/meeting_watch.py:66-71`, `172-178`
`win32gui`, `pythoncom` and `win32com` are imported inside `try/except ImportError`, which returns `[]`. The CI install log confirms pywin32 isn't installed. Local builds include it only because the developer's global Python happens to have it. A v1.2.3 built by `release.yml` would silently never detect a meeting, and that is the headline feature.
*Fix:* add `pywin32>=306; sys_platform == "win32"`, and add a test that asserts `import win32gui` works.
**Status: ✅ FIXED.** `pywin32` added to requirements.txt (verified in a clean virtualenv: meeting detection then works); the release workflow fails the build if the win32 modules are missing; the app logs a warning instead of silently disabling detection. `tests/release_fixes.py` (release gate): old code 2/10, fixed 25/25.

**H24 — The v1.2.2 release has two different installers, and its notes and checksum point at different files ✔ verified** · Confirmed
GitHub release v1.2.2 assets; `.github/workflows/release.yml:79-86`; `core/updater.py:114-130`, `273-283`
There is a hand-uploaded `MICO360Meetings-Setup-1.2.2.exe` (169 MB, Cloud key) and a CI-built `MICO360Meetings-Setup.exe` (139 MB, *no* Cloud key, no pywin32). The release body tells users to download the second file but lists the first file's SHA256. The updater works today only because the API happens to list the first asset first.
*Fix:* publish exactly one CI-built installer per release, and drop the updater's "first 64-hex string in body" checksum fallback.
**Status: ✅ FIXED (code) · ⚠ one manual step left.** The updater now picks the release's own installer regardless of asset order (the one named with the version, then one with its own `.sha256`) and verifies it only against that file's own checksum — a hash naming a different file is never used (checked against the real v1.2.2 data in both asset orders). The release workflow publishes one CI installer with the maintained release notes. The live v1.2.2 notes were corrected to name the right installer (original saved). **Remaining:** the stray CI assets (`MICO360Meetings-Setup.exe`, its `.sha256`, `SHA256SUMS.txt`) are still attached — deleting release files must be done by the repo owner. `tests/release_fixes.py` (release gate): old code 2/10, fixed 25/25.

**H25 — Upgrades never remove old runtime files** · Likely
`build/installer.iss:47-51` (no `[InstallDelete]`); `ctranslate2/__init__.py` loads every `*.dll` in its folder
When a release renames or drops a DLL, the stale copy stays behind and is force-loaded. Transcription then crashes only on upgraded machines, never on clean installs.
*Fix:* add `[InstallDelete] Type: filesandordirs; Name: "{app}\_internal"`.

**H26 — CI has failed on every push, from three causes** · Confirmed (CI logs)
`.gitignore:23` (`samples/spoken_test.wav` is ignored); `tests/qa_check.py:57`, `240`, `346`; `tests/module_tests.py:107`, `126`, `151`, `202`; `tests/consistency_audit.py:61-75`
1. The WAV fixture is missing in CI.
2. The suites need a running Ollama.
3. They also download Whisper "tiny" from Hugging Face on each run.

`release_check` prints only the last 5 lines of each suite, which hides the real failing assertions. As a result, CI currently gives no signal on regressions.
*Fix:* commit a small CC0 WAV, mark Ollama- and network-dependent checks as SKIP rather than FAIL, and print the FAIL lines.

---

## Medium

### Workflow and UI
| ID | Finding | Location | Conf. |
|---|---|---|---|
| M1 | Opening a meeting keeps the *previous* meeting's Setup fields, media queue and rendered Preview. A later "Create meeting" transcribes the old file into the new meeting and prepends the old title and attendees. | `ui/pages.py:1352-1372` vs `1120-1128` | Confirmed |
| M2 | Re-saving an opened meeting overwrites its model, profile and source with whatever the UI shows, including the text "⚠ Ollama not running". | `ui/pages.py:729-735`, `1050-1057` | Confirmed |
| M3 | Changing pages resets the chosen model, prompt and the custom "edit prompt for this generation" text. | `ui/pages.py:717-750`; `ui/main_window.py:69`, `327-328` | Confirmed |
| M4 | Dropping several files adds only the first, although the caption says "multiple files supported". Browse also picks only one file. | `ui/components.py:297-303`; `ui/pages.py:339` | Confirmed |
| M5 | Following the "full re-transcribe" advice after a live recording appends the full transcript to the live draft, so the AI sees the meeting twice. | `ui/pages.py:480-482`, `918-922` | Confirmed |
| M6 | Insights: "Recurring themes" is always empty for Arabic or other non-Latin minutes (`[^a-z]` is stripped). Cadence and "Meeting date" use `updated_at`, so editing an old meeting moves it to this week. Aggregation is silently capped at 500 meetings. | `core/insights.py:91` (keyword regex); `core/tasks.py:216-220`; `core/history.py:94` | Confirmed |
| M7 | An export filename containing a dot ("Minutes v1.2") fails with "Unsupported export type: .2". | `ui/pages.py:1150-1154` | Likely |
| M8 | In the dark theme on light-mode Windows, alternate Action Items rows are near-white with near-white text, and the overdue tint never renders. | `ui/pages_extra.py:482`, `605-621`; `ui/theme.py:87`, `191-202` | Confirmed |
| M9 | The Language setting is free text: "Arabic", "AR", "ar-SA" and "Auto" all break transcription; live transcription then fails silently. | `ui/settings_page.py:170-174`, `455`; `core/transcription.py:164`, `201` | Confirmed |
| M10 | A new Ollama host isn't used by Refresh or Install until Save, and even after Save the model list, environment line and 4 s cache stay stale. | `ui/settings_page.py:318-393`, `445-451`; `ui/context.py:40-65` | Confirmed |
| M11 | Settings changes are dropped when you navigate away, with no warning, while theme, scale, AI mode and preset apply immediately (inconsistent). | `ui/settings_page.py:278-282` | Confirmed |

### Documentation and legal accuracy
| ID | Finding | Location | Conf. |
|---|---|---|---|
| M12 | The Privacy Policy, README and build docs contradict actual behaviour. (a) Updates are checked automatically at every launch (`auto_check_updates=True`), yet the policy says "only when you click" and "nothing is transmitted automatically". (b) Recordings are auto-deleted after 7 days, yet the policy says they are stored and "you can delete them at any time". (c) Cloud traffic is unencrypted and this is not disclosed. (d) README says "no data ever leaves your computer" and doesn't mention Cloud mode. (e) README_BUILD claims uninstall leaves "no leftover files" and that auto-update is "not yet wired". *Partly from this session's Privacy/Help update.* | `ui/pages_extra.py:995-1010`; `config.py:156`, `162`; `README.md:5`; `build/README_BUILD.md:27-28`, `76`, `80-85` | Confirmed |

### Data, exports and integrations
| ID | Finding | Location | Conf. |
|---|---|---|---|
| M13 | Identical task text within one meeting shares a single saved override: completing Alice's "Send report" also completes Bob's, and editing one copies its owner and date onto the other. | `core/tasks.py:132-134`, `241-261` | Confirmed |
| M14 | Deadlines without a year take the current year, so "10 Jan" written on 20 Dec is immediately overdue. "29 Feb" returns None, and "Oct 5" does not parse. | `core/tasks.py:44`, `63-68` | Confirmed |
| M15 | `.ics` export generates a new UID on every export, so re-importing duplicates events. Lines are folded by character count, not by octet (an Arabic line was 128 octets). | `core/calendar_ics.py:23-29`, `55` | Confirmed |
| M16 | `.ics` import doesn't unescape `\,` or `\;`, shows UTC ("Z") times without converting to local time, and takes SUMMARY/ATTENDEE values from VALARM blocks (the title became "Reminder" and the alarm bot became an attendee). | `core/calendar_import.py:30-39`, `59-62` | Confirmed |
| M17 | The markdown parser mangles common LLM output: a `\|` inside a cell shifts columns (and corrupts extracted owner and deadline), `###` headings merge into the body, numbered lists collapse into one paragraph, and a table directly after text is lost. | `export/md_blocks.py:44-46`, `78-85`, `104-111` | Confirmed |
| M18 | HTML export puts the profile accent colour and logo path into attributes unescaped. An imported profile can inject `<script>`, and paths containing `'` or `#` break the logo. | `export/html_export.py:28`, `30`, `47`, `62-64` | Confirmed |
| M19 | DOCX shows literal `**` in headings, key/value lines and table cells, and crashes on control characters such as `\x0b` from pasted text ("All strings must be XML compatible"). | `export/docx_export.py:126-154` | Confirmed |
| M20 | Deleting an imported copy of a profile deletes the *original* profile's logo file. | `core/profiles.py:103-104`, `138-141` | Confirmed |
| M21 | One bad entry in `meeting_types.json` resets the file to the built-ins, wiping the user's custom types. | `core/meeting_types.py:44-46`, `53-56` | Confirmed |
| M22 | Non-atomic writes (truncate then write) for `settings.json`, `action_status.json`, profiles and meeting types. If a crash or power loss interrupts a write, the next load silently falls back to empty or defaults, and the next save permanently overwrites everything (SMTP credentials, status edits). | `config.py:185-193`, `206-210`; `core/tasks.py:183-195`; `core/profiles.py:94-96`; `core/meeting_types.py:60` | Confirmed / Likely |
| M23 | Choosing a logo in the profile dialog overwrites the saved logo immediately, even if you then Cancel. | `ui/dialogs.py:221-228`; `core/profiles.py:109-119` | Confirmed |
| M24 | Semicolon-separated recipients ("a@x.com; b@y.com") are sent to the first address only, but the app reports "Minutes emailed." | `ui/dialogs.py:452-453`; `core/emailer.py:69-79` | Confirmed |
| M25 | Filler removal (on by default) changes meaning: "Do you know if…" becomes "Do if…", "What kind of contract" becomes "What contract", and "that that plan" becomes "that plan". The punctuated fillers never match. | `core/cleaning.py:12-31` | Confirmed |
| M26 | The chunker splits only on `.!?`: Arabic "؟" and newlines are ignored, and unpunctuated text is hard-sliced mid-word with no overlap. | `core/cleaning.py:56-59`, `87-91` | Confirmed |
| M27 | Cloud generation has no retry for 429/5xx/timeouts and loses all finished chunks; some errors surface as a bare "timed out". | `core/cloud_client.py:111-119`; `core/generation.py:62-69` | Confirmed |
| M28 | The single-instance lock is a machine-wide loopback port, so a second Windows user (RDS or fast user switching) is told the app is "already running", and any bind error is treated the same way. | `single_instance.py:12`, `21-31` | Confirmed |
| M29 | If the checksum file can't be fetched, the updater skips SHA256 verification and still offers to install as admin. | `core/updater.py:139-151`, `179-189`; `ui/pages_extra.py:354-364` | Confirmed |
| M30 | Action Items CSV has no BOM, so Excel garbles Arabic. Cells starting with `= + - @` also become formulas. | `core/tasks.py:287-296` | Confirmed |

### Recording
| ID | Finding | Location | Conf. |
|---|---|---|---|
| M31 | Long mic+system recordings are mixed in RAM (about 4.5 GB per hour). A MemoryError is swallowed, so no output file is written, yet the UI still says "Recording saved". | `core/recording.py:497-542`, `416-420`, `680-682` | Likely |
| M32 | Stop runs `rec.stop()` and the whole remux/AAC encode on the GUI thread, so long recordings freeze the window ("Not Responding"). | `ui/recording_panel.py:501-505`; `core/recording.py:880-925` | Confirmed |
| M33 | After a lazy CUDA→CPU fallback, the engine is rebuilt on CUDA for *every* file, and live transcription has no fallback at all. | `core/transcription.py:175-204`; `ui/context.py:30-32` | Confirmed |
| M34 | In "Mic + System" mode (the auto-record default), a mic that fails to open is silently dropped: there is no device or rate fallback and no error. | `core/recording.py:422-456` | Likely |
| M35 | The Outlook calendar lookup uses a hard-coded US date format, so it fails or picks the wrong window on en-GB and ar-OM locales. Each poll also re-launches Outlook. | `core/meeting_watch.py:185-187` | Likely |
| M36 | Teams false positives: "Chat \| Jane Doe \| Microsoft Teams" is classified as a meeting and can then mask real meetings. | `core/meeting_watch.py:37-41` | Likely |

### Build, installer and CI
| ID | Finding | Location | Conf. |
|---|---|---|---|
| M37 | Version comparison ranks `1.3.0-rc1` above `1.3.0`, and the workflow publishes any `v*` tag as latest. There is no check that the tag matches `__version__`, and a missing key silently produces a Local-only release. | `core/updater.py:74-80`; `release.yml:4-7`, `36-37`, `79-86` | Confirmed |
| M38 | The in-app update says "the app will reopen", but the silent install never relaunches it (`skipifsilent`). | `ui/pages_extra.py:369-378`; `build/installer.iss:68` | Confirmed ✔ |
| M39 | Ollama and model setup runs hidden and elevated, and its exit code is ignored. It installs into the *admin* profile under over-the-shoulder elevation, and an ICMP ping skips setup on networks that block ping. | `build/installer.iss:37`, `60-64`; `build/smart_setup.ps1:45-48`, `185-214` | Confirmed |
| M40 | Job-level secrets in `release.yml` expose the Cloud key and signing cert to every step, including unpinned `pip install`, PyInstaller and third-party actions. *Introduced this session.* | `.github/workflows/release.yml:16-19`, `28-31`, `71-86` | Confirmed |
| M41 | `build_all.ps1` can hash and report a *stale* installer: `build\Output` isn't cleaned, ISCC and pip exit codes aren't checked, and pip output is discarded. | `build/build_all.ps1:14-18`, `51-61` | Confirmed |
| M42 | Dependencies are unpinned. CI resolves reportlab 5, OpenCV 5, av 18 and numpy 2.5, none of which have been tested. Local builds come from a polluted global Python 3.14. | `requirements.txt`; `release.yml:31`; `build/build_all.ps1:14-15` | Confirmed |
| M43 | The test suites write into the developer's real app-data folder: they change theme and preset, add speakers, and can leave "QA" records behind. | `tests/module_tests.py:46-62`, `244-273`, `778-797`, `1045-1057`; `tests/qa_check.py:40-41`, `95-116`, `330-332` | Confirmed |
| M44 | The gate can pass while the product is broken. The launch check passes if the exe simply stays alive (including when it is stuck on the "already running" or crash dialog). The Arabic PDF glyph check is skipped because pypdf isn't in the requirements. Skipped LLM checks are recorded as PASS. The secrets regex misses `mico_` keys. | `tests/release_check.py:82-84`, `105-119`; `tests/module_tests.py:342-348`; `tests/qa_check.py:91`, `262-275`, `302` | Confirmed |

---

## Low
| ID | Finding | Location |
|---|---|---|
| L1 | Arabic lines in the Transcript and Minutes editors are left-aligned (Preview and History are correct). | `ui/pages.py:508`, `677` |
| L2 | The email subject uses the guessed title rather than the edited one, and PDF/DOCX attachments are built on the UI thread. | `ui/pages.py:1188`, `1200-1204` |
| L3 | Clearing both editors and then pressing Undo creates a duplicate History record. | `ui/pages.py:1098-1107` |
| L4 | The Help → Shortcuts list omits Insights, so Ctrl+4…Ctrl+8 are documented wrong (Ctrl+8 is Help, Settings is Ctrl+9). ✔ verified | `ui/pages_extra.py:882-884` vs `ui/main_window.py:26-36` |
| L5 | The Speed ⇄ Quality preset never switches to "Custom" when the fields are changed. | `ui/settings_page.py:141-147`, `431-462` |
| L6 | The SMTP password is stored in plaintext in `settings.json`, and leading or trailing spaces are stripped from it. | `ui/settings_page.py:230-235`, `471`, `480` |
| L7 | Pasting a full GitHub URL into the repo field breaks update checks with the message "No releases found". | `ui/settings_page.py:244`, `464`; `core/updater.py:83-89` |
| L8 | Blank or formatted-empty xlsx rows become junk "Company" profiles, and headers are case- and space-sensitive. | `core/profiles.py:193-197`, `138-141` |
| L9 | An invalid accent colour (for example `#GG0000`) aborts PDF export. | `export/pdf_export.py:28` |
| L10 | The crash-report email shows `+` between every word (`quote_plus` in a mailto link). | `crash_reporter.py:118-121` |
| L11 | `_build_key.py` is never deleted after a build and sits in the Dropbox-synced repo; a later build without the env var ships a stale key. | `build/build_all.ps1:23-29`; `build/mico360.spec:36-37` |
| L12 | The release body is auto-generated, so the Updates page shows no feature or fix lists. | `release.yml:86` |
| L13 | Uninstall silently leaves the meeting history and the plaintext SMTP password on disk, and there is no `AppMutex`. | `build/installer.iss:70-71` |
| L14 | The Python 3.14 shutdown crash is masked in the tests with `os._exit` but is still present in locally built installers. | `tests/*` teardown; `ci.yml:17` |
| L15 | README_BUILD is out of date, and the spec's `except: pass` around `collect_all` hides missing packages. | `build/README_BUILD.md`; `build/mico360.spec:21-28` |

---

## Suggested fix order

1. **Data safety:** C1, C2, H2, M1 (switching meetings and auto-record). Guard workers, autosave before any load, start a fresh meeting for auto-record.
2. **Security:** C5 (HTTPS), H17 (STARTTLS), M29 (checksum), M40 (secrets scope), M18 (HTML escaping).
3. **Crash hardening:** C4, H1, H3, H7 (crash reporter off-thread, shortcut guards, QThread lifetime, recorder ERROR teardown).
4. **Auto-record correctness:** C3, H9, H12, H23 (remote audio in the transcript, pywin32 in builds, Meet tab handling).
5. **Recording durability:** H6, H8, H13, M31 (persistent recordings folder, zero-frame fallback, stall detection, streaming mix).
6. **Generation quality:** H15, H16, M25, M26 (context size, reduce prompt, filler rules, Arabic chunking).
7. **Export and Arabic:** H18–H22, M15–M19.
8. **Release pipeline:** H24, H25, H26, M37, M41–M44, then re-publish with a single CI-built, keyed installer.
9. **Docs and legal text:** M12, L4. Correct the Privacy Policy, README and Help to match actual behaviour.

## What was checked and held up

**Storage and data**
- History uses parameterised SQLite, is touched only from the GUI thread, and AUTOINCREMENT prevents id reuse.
- Overdue logic uses local dates consistently and ignores undated or closed items.
- Insights guards against divide-by-zero.

**Exports**
- The PDF body is escaped correctly, and 400-row tables paginate correctly.
- The two-pass page numbering and the DOCX PAGE/NUMPAGES fields are correct.
- HTML escapes all user text in element content.

**Updates and email**
- The update integrity chain (SHA256 checked before install and again at install, Authenticode rejection) is sound when the checksum is available.
- Numeric version comparison handles "1.10" > "1.9".
- Email headers refuse CR/LF, and Arabic subjects are encoded correctly.

**Security, templates and lifecycle**
- No secrets appear in logs or crash reports.
- `[TRANSCRIPT_HERE]` is replaced in a single pass, and braces in transcripts are safe.
- The single-instance lock can't go stale after a crash.
- Logging does not duplicate handlers.

**New Meeting workflow and pages**
- Step 1 and Step 2 reject empty or whitespace-only transcripts.
- Generation handles "Ollama down", "no models" and "Cloud unreachable/no key" with clear messages.
- Page signals are not double-connected when you revisit a page.

**Settings, profiles and prompts**
- History and profile deletes ask for confirmation, and onboarding runs exactly once.
- Built-in prompts can be restored.

**Recording and transcription**
- MP3 recording paths produce files of the correct duration.
- Loopback silence doesn't trip the watchdog.
- Pause and resume time accounting is correct, as are the double-stop guards.
- The load-time CUDA→CPU fallback works.
- Meeting-watch debounce and calendar de-duplication work.

**Build and packaging**
- Version strings are coherent across the code, installer and release notes.
- Bundled assets resolve through `_MEIPASS`.
- `arabic_reshaper` and `bidi` are included in the frozen build.

## Not covered by this review

- Real hardware (microphones, speakers, GPU), live Teams/Meet/Zoom calls, a real MICO360 Cloud server, real SMTP delivery, and a clean-machine installer run. These still need the manual pass in `docs/PRERELEASE_CHECKLIST.md`.
- *Environment note:* during the review, Windows' Python install manager auto-updated the local interpreter to 3.14.7 (site-packages were kept). This was not triggered deliberately.
