# MICO360 Meetings v1.2.3

The biggest release yet. Still **offline by default** — audio is transcribed
locally with Whisper and minutes are written by a local Ollama model, with
MICO360 Cloud available as an optional mode.

## New features

- **Auto-record live meetings, hands-free.** Detects a live Teams, Google Meet,
  Zoom, or Webex meeting and records it automatically — no manual start needed.
- **Live transcription while recording (beta).** Watch the transcript appear as
  the meeting happens.
- **Speaker identification & naming.** Segments are attributed to speakers you
  can label, and the names flow through into the minutes.
- **Cross-meeting Insights dashboard.** See trends, recurring topics, and action
  items across all your meetings in one place.
- **Reminders, follow-ups & calendar sync.** Overdue reminders, per-owner
  follow-ups, and calendar (`.ics`) export for action items.
- **Paste text as a source.** Start a new meeting from pasted notes or a
  transcript — a first-class option alongside audio and file uploads.

## Experience overhaul

- **Unified design system** across every page.
- **New Meeting** redesigned as a proper progress stepper; Step 1 fits on one
  screen; single clear **Transcribe** action.
- **Action Items** overhauled: statuses, filters, overdue highlighting, edit
  every field, and quick actions in place.
- **Meeting History** with sort, filter, preview, and status.
- **Prompt Library** redesigned with search, favourites, and duplicate.
- **Company Profiles** as cards with safer actions.
- **Navigation:** grouped sidebar with synced active state; responsive layout
  that uses wide screens while keeping tables readable.

## Fixes & hardening

- **Low-resource hardening:** lazy transcripts, a lighter task table, and temp
  file cleanup keep memory and disk use down on modest machines.
- **UI audit fixes:** status/priority dropdowns are visible again, chart labels
  are readable in dark theme, the toast no longer covers the header, and the
  Task column is readable.
- **Reliable release gate:** no longer crashes on a legacy console code page or a
  flaky test-runner exit.

## Under the hood

- All test suites (module, QA, consistency, parser, end-to-end) pass through the
  one-command release gate.
- Installer is SHA256-verified for the in-app updater.
