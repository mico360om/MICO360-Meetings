# MICO360 Meetings v1.2.3

The biggest release yet — new hands-free recording features plus a full code review with 90 fixes. Still offline by default: audio is transcribed on your PC with Whisper and minutes are written by a local Ollama model; MICO360 Cloud remains optional.

## New features

- **Auto-record live meetings:** detects a live Teams, Google Meet, Zoom or Webex meeting and records it — every detected meeting gets its own record, and the minutes include everyone on the call (microphone + system audio).
- **Live transcription while recording (beta):** watch the transcript appear as the meeting happens.
- **Speaker identification & naming:** segments are attributed to speakers you can rename; names flow into the minutes.
- **Insights dashboard:** trends, recurring topics (now in any language, including Arabic) and action-item health across all meetings.
- **Reminders, follow-ups & calendar:** overdue reminders, per-owner follow-ups and calendar (.ics) export that updates events instead of duplicating them.
- **Paste text as a source:** start a meeting from pasted notes or a transcript.
- **Professional PDF minutes:** company letterhead, a meeting-details panel, attendees as a name grid, colour-coded action-item statuses, and clean page breaks — no split table rows, no stranded headings, nothing cut off.
- **Arabic / right-to-left:** Arabic minutes display and export correctly in PDF, Word and HTML.
- **Per-user install:** `Setup.exe /CURRENTUSER` installs without administrator rights.

## Experience overhaul

- Unified design system; New Meeting is a clear step-by-step wizard.
- Action Items: statuses, filters, overdue highlighting (readable in dark theme), edit-in-place, Excel-friendly CSV.
- Meeting History with sort, filter, preview and correct Draft status.
- Settings: language picker, unsaved-changes prompt, Cancel buttons for model and update downloads.

## Fixes

- Switching meetings while minutes are generating no longer writes them into the wrong meeting; your unsaved edits are saved first.
- Auto-record no longer merges a new meeting into the one that was open.
- Recordings are kept in your Recordings folder and are never deleted automatically.
- An error in a background task no longer closes the app.
- Screen and camera recordings capture the selected audio source; video and audio stay in sync; unplugged microphones are detected.
- Long meetings: the AI keeps the whole meeting in context, keeps your template's instructions, and a retry resumes from where it stopped.
- Filler-word removal no longer changes the meaning of sentences; Arabic transcripts are split cleanly.
- Exports handle tables with long cells, pipes inside cells, numbered lists, sub-headings and numeric profile fields.
- Calendar import/export: correct line endings, local times, stable events, no alarm text in titles.
- Action items with identical text are tracked separately; dates without a year resolve correctly.
- Settings, profiles and task edits are saved atomically — a crash mid-save can no longer wipe them.
- The app stays responsive when Ollama or the Cloud server is slow or unreachable.
- Updates install the right file even when a release carries more than one installer, and the app reopens after an in-app update.

## Security

- The email (SMTP) password is stored encrypted with your Windows account.
- Email login is refused unless the connection is encrypted (STARTTLS).
- Updates that can't be verified against their published checksum are never installed.
- MICO360 Cloud switches to an encrypted connection automatically as soon as the server offers one, and asks for confirmation while it doesn't.

## Under the hood

- New release gate: 11 test suites plus a self-test that runs inside the built app (`MICO360Meetings.exe --self-test`).
- Pinned, tested dependency versions; upgrades replace the whole runtime folder.
