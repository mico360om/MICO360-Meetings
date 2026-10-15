# MICO360 Meetings for Android — Plan 3: Screens and End-to-End

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The app a person uses: tabs for Meetings, Action Items, Insights and Settings; the new-meeting flow (record / import / paste → details → transcript → minutes); the minutes editor with preview; export and share; settings pages; and an emulator end-to-end run against a local fake MICO360 Connect.

**Architecture:** Screens (`src/app/`, Expo Router) are thin: they call the services from Plan 2 through a small set of hooks (`src/ui/`). Every decision a screen makes that can go wrong — which template a meeting uses, what a new meeting inherits from its type, how a generation result is saved, how reminders are refreshed — lives in `src/services/meetingFlow.ts` and is unit-tested with `node:sqlite`. End-to-end runs use adb + uiautomator (Maestro is not installed) and a Node fake Connect server reached from the emulator at `10.0.2.2`.

**Tech Stack:** Expo Router tabs/stack, `expo-sqlite` `SQLiteProvider`, `@siteed/audio-studio` `AudioRecorderProvider`, `react-native-webview` (preview), Plan 2 services and adapters.

**Spec:** `docs/superpowers/specs/2026-10-15-android-app-design.md` §3.4, §4, §6, §8 · **Builds on:** Plans 1–2.

## Global Constraints

- Everything in Plans 1–2's Global Constraints holds.
- Screens contain no business rules: anything testable goes to `src/services/` with a vitest test.
- Every interactive control has an `accessibilityLabel` (used by the E2E flows and screen readers).
- Light and dark follow the phone; colours come from `src/ui/theme.ts` (desktop palette: accent `#8B1E1E`).
- Arabic text is shown right-to-left (`writingDirection` from `hasArabic`).
- Edits autosave (debounced 800 ms) so a kill never loses minutes (§3.3).
- No key: *Generate minutes* is disabled with "Minutes need MICO360 Cloud, which isn't enabled in this build." (§5).
- The plain-HTTP consent dialog appears once per install before the first Cloud request while unencrypted (§5).

## Review Focus

1. Leaving the app mid-generation or mid-transcription and coming back: the meeting must still be there with whatever finished (transcript saved before generation starts) — tested in Task 1 (`saveTranscript` before `generate`).
2. A meeting whose type points to a deleted prompt or company: it must fall back to the built-in prompt / no branding, never crash — tested in Task 1.
3. Generate pressed twice quickly: one request only — guarded by a busy flag in the screen and `generateForMeeting` rejecting while a run for that meeting is active (Task 1).
4. Action-item edits after the minutes are regenerated: rows whose task text is unchanged keep their edits (stable keys from Plan 1) — tested in Task 1.
5. Reminders after status changes: completing an item removes its reminders — tested in Task 1 (`refreshReminders` plan).

---

### Task 1: Meeting flow service

**Files:** Create `android/src/services/meetingFlow.ts`, `android/test/meetingFlow.test.ts`.

**Interfaces:**
- Consumes: Plan 2 `Db`, `createMeeting/getMeeting/updateMeeting`, `listMeetingTypes`, `listPrompts`, `listProfiles`, `getProfile`, `getSettings`, `listActionItems`, `generateMinutesFor`, `MinutesClient`, `planReminders`, `ReminderPlan`, `Transcript`, `speakerText`; Plan 1 `BUILTIN_PROMPTS`, `DEFAULT_STYLE`.
- Produces:
  - `newMeetingDefaults(db, typeName?): Promise<Partial<Meeting>>` — prompt, style and company from the meeting type (company matched by name), else the settings defaults.
  - `templateFor(db, meeting): Promise<{ text: string; style: string }>` — the meeting's prompt text by name; a missing prompt falls back to the built-in of that name, then to the default prompt.
  - `saveTranscript(db, id, t: Transcript, source): Promise<Meeting>` — stores text, segments, language, duration.
  - `generateForMeeting(db, id, client, opts): Promise<{ meeting: Meeting; warnings: string[] }>` — one run per meeting at a time (`Error("Minutes are already being generated for this meeting.")`), saves minutes + model.
  - `renameSpeakers(db, id, names: Record<string,string>): Promise<Meeting>` — rewrites the transcript text with the names.
  - `reminderPlans(db, now): Promise<ReminderPlan[]>` — from all open items when `settings.reminders`, else `[]`.

- [ ] Step 1: failing tests — defaults from a type and from settings; template fallbacks (deleted prompt, deleted company → no profile); transcript saved before generation and kept when generation throws; concurrent `generateForMeeting` rejected; regenerated minutes keep edits of unchanged rows; reminders disappear after an item is completed and when `reminders` is off; speaker renaming.
- [ ] Step 2: run → FAIL (module missing). Step 3: implement. Step 4: `npm test && npm run typecheck` → PASS. Step 5: commit "Android: meeting flow service".

### Task 2: App shell, theme and data providers

**Files:** Modify `android/src/app/_layout.tsx`; create `android/src/app/(tabs)/_layout.tsx`, `android/src/ui/theme.ts`, `android/src/ui/db.tsx` (`DbProvider` wrapping `SQLiteProvider` with `onInit` = `migrate` + `seed`, `useDb(): Db`), `android/src/ui/components.tsx` (Screen, Card, Button, Field, Chip, ProgressBar, Empty, Banner — all themed, RTL-aware text), `android/src/ui/hooks.ts` (`useSettings`, `useRefreshOnFocus`), delete `android/src/app/index.tsx`.

- Root layout: `setRandomSource(getRandomBytes)`, `DbProvider`, `AudioRecorderProvider`, Stack; on start `probeCloud()` and `recoverInterruptedRecordings` (offered as drafts on the Meetings tab).
- Tabs: Meetings (`index`), Action Items (`actions`), Insights (`insights`), Settings (`settings`), with labels and icons from `@expo/vector-icons`.
- Verify: typecheck; mirror debug build (`-Abis x86_64`); app opens on Meetings with four tabs (uiautomator dump contains the four tab labels).
- Commit "Android: app shell, theme and data providers".

### Task 3: Meetings list and new-meeting flow

**Files:** `android/src/app/(tabs)/index.tsx` (search, draft/final filter, newest first, long-press delete with confirm, recovered-recording banner, FAB "New meeting"), `android/src/app/meeting/new.tsx` (Source step: Record / Import file / Paste text; Details step: title, date, attendees, meeting type, company), `android/src/app/meeting/record.tsx` (timer, level bar, pause/resume, stop; muted-mic banner; permission explanation with *Open settings*), `android/src/app/meeting/[id]/transcribe.tsx` (model check → `ModelMissing` → link to Settings; progress, cancel; result saved with `saveTranscript`).

- Import uses `expo-document-picker` (`audio/*`, `video/*`), copying into `recordings/`.
- Verify: typecheck; on the emulator, paste-text flow creates a meeting that appears in the list.
- Commit "Android: meetings list and new-meeting flow".

### Task 4: Meeting screen — transcript, minutes, editor, preview

**Files:** `android/src/app/meeting/[id]/index.tsx` with segmented views **Transcript · Minutes · Preview**: transcript editing (and speaker names when segments have speakers); *Generate minutes* (disabled without key; consent dialog while unencrypted; progress per part; cancel; warnings banner; retry); minutes Markdown editor with toolbar (H2, bold, italic, bullet, table, undo) and 800 ms autosave; Preview = `WebView` of `renderPreviewHtml` with the meeting's company; *Mark final*; *Export*.
- Verify: typecheck; E2E paste → generate against the fake server shows the minutes.
- Commit "Android: meeting screen with editor and preview".

### Task 5: Export and share

**Files:** `android/src/app/meeting/[id]/export.tsx` — format (PDF / Word / Text / Markdown), company (meeting's / any / none), design override for PDF (preview card per design), *Share* (share sheet) — using `exportMinutes` + `nativeExportDeps` + `shareFile`.
- Verify: on the emulator a PDF export opens the share sheet (`com.android.intentresolver` in the window dump); the PDF in `cache/exports` has the meeting title (pulled with `adb exec-out run-as`).
- Commit "Android: export and share".

### Task 6: Action Items and Insights tabs

**Files:** `android/src/app/(tabs)/actions.tsx` (search; filters status/owner/overdue; tap → edit sheet with task, owner, deadline, status, priority, notes, reset; CSV and calendar export via share; reminders refreshed with `reminderPlans` + `scheduleReminders` after every change), `android/src/app/(tabs)/insights.tsx` (cards: meetings, open, overdue, completion rate; bars for status, owners, cadence; keyword chips).
- Commit "Android: action items and insights".

### Task 7: Settings

**Files:** `android/src/app/(tabs)/settings.tsx` (sections) and `android/src/app/settings/{transcription,companies,company,prompts,prompt,types,transfer,cloud,about}.tsx`:
- Transcription: models with size, download (progress, cancel), delete; language; speaker identification; remove fillers.
- Companies: list, add/edit (all profile fields, logo from gallery via `expo-image-picker` into `logos/`, PDF design with preview), duplicate, delete.
- Prompts: list by category, favourites, edit, restore built-in, add, delete.
- Meeting types: list, edit (prompt, style, company), add, delete.
- Transfer: import/export prompts and profiles (desktop format) with the summary message; export via share.
- Cloud: status (`CloudClient.status()`), model picker, encrypted or not, consent reset; Local-only notice when no key.
- About: version, privacy text, licences.
- Commit "Android: settings".

### Task 8: End-to-end on the emulator

**Files:** `android/e2e/fakeConnect.mjs` (GET `/v1/models`, POST `/v1/chat/completions` returning fixed minutes with an action item; a `307` route to another port whose listener records whether `Authorization` arrived — Plan 2 Task 7 Step 5), `android/e2e/run.mjs` (adb + uiautomator helpers: `tap(label)`, `type(label, text)`, `waitFor(text)`; flows: paste → generate → minutes visible → export PDF → share sheet; action item visible on the Action Items tab; profile import from a desktop export file pushed to Downloads), `android/e2e/README.md`.
- Build: `MICO360_E2E_CONNECT_URL=http://10.0.2.2:8787/v1 MICO360_MOBILE_CONNECT_KEY=e2e-local-test powershell -File tools/build-mirror.ps1 -Variant release -Abis x86_64`.
- Expected: every flow prints `PASS`; the redirect probe result is written into spec §5.1.
- Commit "Android: emulator end-to-end flows".

## Self-review

- Spec §3.4 screens 1–7 → Tasks 3–7; §4 data flow → Tasks 3–5; §5 consent/Local-only → Task 4/7; §6 errors → Tasks 3 (permission, recovery), 4 (cloud errors, retry), 7 (model download); §8 E2E → Task 8 (adb/uiautomator instead of Maestro — not installed; same flows).
- Release workflow, keystore script and in-app updater: Plan 4.
