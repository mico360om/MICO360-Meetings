# MICO360 Meetings v1.2.0

A big feature release for the offline Python edition. Download
**MICO360Meetings-Setup.exe** below and run it.

## ✨ New
- **Action Items tracker** — every task from every meeting in one filterable
  table (owner · deadline · status), editable status, CSV export, drill-through
  to the source meeting.
- **Meeting-type presets** — one click applies a prompt + output style + company
  profile (Board, Standup, Client Call, Internal, Action Items, Executive).
- **Calendar (.ics) import** — pre-fills meeting title, date and attendees into
  the minutes header so they aren't left as "Not specified".
- **Email minutes** — send the minutes straight from the app over SMTP
  (Mailjet-ready) with PDF / Word attachments. Credentials stay in your local
  settings.
- **In-place auto-update** — download → silent install → relaunch, from GitHub
  Releases.
- **Autosave** (never lose a transcript) + native undo/redo.

## Also in the 1.1.x line (now included)
- System / speaker **loopback audio** capture (record what you hear), plus
  mic + system mixed.
- Offline **speaker identification** (beta), **Speed/Quality presets**,
  rendered **minutes preview**, and **Markdown/HTML** export.
- Recording reliability fixes (host-API fallback; no more Stereo-Mix requirement).

## Testing
74 integration + 30 module + 22 cross-screen consistency checks pass; live mic /
system / email verified on real hardware.

> Note: this Python edition uses a different installer from the older 1.0.x
> Electron builds, so those don't auto-update to it — install the setup below.
> The previous Electron source remains on the `electron-legacy` branch.
