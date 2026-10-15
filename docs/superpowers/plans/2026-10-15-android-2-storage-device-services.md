# MICO360 Meetings for Android — Plan 2: Storage, Device Features and Services

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything below the screens: native dependencies and build tooling, the SQLite data layer, the recorder with the muted-microphone warning, windowed on-device transcription with speaker identification, Whisper model management, exports and sharing, MICO360 Cloud generation with the built-in key and the plain-HTTP consent, template/profile import-export, and reminders — each with a pure, unit-tested core and a thin native adapter.

**Architecture:** Logic lives in `src/core/` (pure, vitest) and `src/services/` (pure orchestration over small adapter interfaces, vitest with fakes). `src/data/` holds the SQLite schema and repositories behind a `Db` interface that `expo-sqlite` satisfies structurally and that the tests back with Node's built-in `node:sqlite`. `src/native/` holds the thin adapters to Expo and native modules; they are typechecked here and exercised on the emulator in Plan 3. Native builds run in a mirror outside Dropbox.

**Tech Stack:** Expo SDK 57 modules (`expo-router`, `expo-sqlite`, `expo-file-system` File/Directory/Paths API, `expo-print`, `expo-sharing`, `expo-document-picker`, `expo-image-picker`, `expo-notifications`, `expo-constants`, `expo-build-properties`), `react-native-webview`, `@siteed/audio-studio` 3.2 (16 kHz PCM WAV recording with a microphone foreground service; `streamAudioData` decoding), `whisper.rn` 0.7.4 (`initWhisper`, `transcribeData`), `@noble/hashes`.

**Spec:** `docs/superpowers/specs/2026-10-15-android-app-design.md` · **Builds on:** Plan 1 (`src/core/*`).

## Global Constraints

- Everything in Plan 1's Global Constraints still holds (pure `src/core`, no `\p{}`, no `\uXXXX` written as literal characters, `HttpTransport` for network).
- `src/services/**` and `src/data/**` must not import native modules either; they receive adapters (enforced by extending `test/purity.test.ts`).
- Add native/Expo packages with `npx expo install <pkg>` (SDK-compatible versions); add plain JS packages with `npm install`.
- Never build Gradle inside Dropbox: native builds use `tools/build-mirror.ps1` (mirror at `%USERPROFILE%\mico360-build\app`).
- Audio for Whisper is 16 kHz mono float32; long audio is processed in windows of at most 600 s (never the whole recording in memory).
- Whisper models are downloaded on demand, pinned by size and SHA256: tiny-q5_1, base-q5_1 (default), small-q5_1.
- The Cloud key comes only from the build environment (`MICO360_MOBILE_CONNECT_KEY`, else `MICO360_CONNECT_API_KEY`), XOR-hidden in `extra.connect`; it is never logged, displayed or stored.
- Cleartext HTTP is allowed only for `ai.mico360.com` (and the emulator/Metro loopback hosts); sending meeting text over plain HTTP needs the user's recorded consent (`settings.plainHttpConsent`).
- PDF pages are A4: `printToFileAsync({ html, width: 595, height: 842 })`.

## Review Focus

1. Recording killed mid-meeting or a phone call during recording: the WAV written so far must survive and be usable — tested in Task 4 (header repair) and on the emulator in Plan 3.
2. A 2-hour import must never hold more than one 600 s window of samples, and timestamps must stay continuous across windows — tested in Task 5.
3. A model download interrupted or corrupted must never be used — tested in Task 6.
4. Exports with titles that are empty, reserved (`CON`), Arabic or full of `/:*?` must produce valid file names — tested in Task 7.
5. Importing the same profile file twice must not duplicate profiles or logos — tested in Task 9.

---

### Task 1: Native dependencies, app config, key box, cleartext host and the build mirror

**Files:**
- Modify: `android/package.json`, `android/app.config.ts`, `android/test/appConfig.test.ts`
- Create: `android/src/core/keyBox.ts`, `android/test/keyBox.test.ts`, `android/plugins/withCleartextHosts.js`, `android/tools/build-mirror.ps1`, `android/tools/check-native.mjs`
- Delete: `android/App.tsx`, `android/index.ts` (expo-router provides the entry); Create: `android/src/app/_layout.tsx`, `android/src/app/index.tsx` (placeholder screen, replaced in Plan 3)

**Interfaces:**
- Produces: `interface KeyBox { s: string; d: string }`, `hideKey(key, salt?): KeyBox`, `revealKey(box): string`; `app.config.ts` `extra.connect: KeyBox | null`, `extra.e2eConnectUrl: string`; config plugin `withCleartextHosts(config, { hosts: string[] })`.

- [ ] **Step 1: Install native dependencies**

```bash
cd android
npx expo install expo-router react-native-safe-area-context react-native-screens expo-linking expo-constants expo-sqlite expo-file-system expo-print expo-sharing expo-document-picker expo-image-picker expo-notifications expo-build-properties expo-system-ui expo-crypto react-native-webview
npm install @siteed/audio-studio@^3.2.1 whisper.rn@^0.7.4
```

Set `"main": "expo-router/entry"` in `package.json`; delete `App.tsx` and `index.ts`. Rename `vitest.config.ts` to `vitest.config.mts` (removes Vite's CJS warning; do not add `"type": "module"`, which affects Metro). Replace `android/AGENTS.md` (Expo template text) with a short note: routes in `src/app/`, pure logic in `src/core|data|services` (purity test), adapters in `src/native`, `npx expo install` for native packages, builds via `tools/build-mirror.ps1`, no EAS/Expo cloud services.

- [ ] **Step 2: Write the failing tests** — `android/test/keyBox.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { hideKey, revealKey } from "../src/core/keyBox";

describe("key box", () => {
  it("round-trips a key and never stores it in clear", () => {
    const box = hideKey("mico_" + "ab".repeat(24));
    expect(revealKey(box)).toBe("mico_" + "ab".repeat(24));
    expect(JSON.stringify(box)).not.toContain("mico_");
    expect(box.s).toMatch(/^[0-9a-f]{64}$/);
  });
  it("a different salt gives different data", () => {
    expect(hideKey("k", new Uint8Array(32).fill(1)).d).not.toBe(hideKey("k", new Uint8Array(32).fill(2)).d);
  });
  it("missing or broken boxes reveal nothing", () => {
    expect(revealKey(null)).toBe("");
    expect(revealKey({ s: "zz", d: "00" })).toBe("");
  });
});
```

Extend `android/test/appConfig.test.ts`:

```ts
import { revealKey } from "../src/core/keyBox";

describe("build-time configuration", () => {
  it("hides the Connect key from the environment and enables the native plugins", () => {
    process.env.MICO360_MOBILE_CONNECT_KEY = "mico_test_key";
    const c = config();
    delete process.env.MICO360_MOBILE_CONNECT_KEY;
    expect(revealKey((c.extra as { connect: { s: string; d: string } }).connect)).toBe("mico_test_key");
    const names = (c.plugins ?? []).map((p) => (Array.isArray(p) ? p[0] : p));
    expect(names).toEqual(expect.arrayContaining(["expo-router", "expo-sqlite", "@siteed/audio-studio",
      "expo-notifications", "expo-build-properties", "./plugins/withCleartextHosts.js"]));
  });
  it("no key in the environment: a Local-only build", () => {
    delete process.env.MICO360_MOBILE_CONNECT_KEY;
    delete process.env.MICO360_CONNECT_API_KEY;
    expect((config().extra as { connect: unknown }).connect).toBeNull();
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd android && npx vitest run test/keyBox.test.ts test/appConfig.test.ts`
Expected: FAIL — `keyBox` missing; plugins/extra absent.

- [ ] **Step 4: `android/src/core/keyBox.ts`**

```ts
/** Light obfuscation for the build-time Cloud key: XOR with a per-build salt.
 *  Deters casual extraction from the APK only (spec §5) — never logged. */
import { randomBytes } from "./random";

export interface KeyBox { s: string; d: string; }

const toHex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
function fromHex(h: string): Uint8Array | null {
  if (!/^(?:[0-9a-f]{2})*$/.test(h)) return null;
  return Uint8Array.from(h.match(/../g) ?? [], (x) => parseInt(x, 16));
}

export function hideKey(key: string, salt: Uint8Array = randomBytes(32)): KeyBox {
  const bytes = new TextEncoder().encode(key);
  const out = bytes.map((b, i) => b ^ salt[i % salt.length]);
  return { s: toHex(salt), d: toHex(out) };
}

export function revealKey(box: KeyBox | null | undefined): string {
  if (!box || typeof box.s !== "string" || typeof box.d !== "string") return "";
  const salt = fromHex(box.s);
  const data = fromHex(box.d);
  if (!salt || !data || !salt.length) return "";
  return new TextDecoder().decode(data.map((b, i) => b ^ salt[i % salt.length]));
}
```

- [ ] **Step 5: `android/plugins/withCleartextHosts.js`**

```js
// Allow plain HTTP only for the named hosts (Android blocks cleartext by default).
const fs = require("fs");
const path = require("path");
const { AndroidConfig, withAndroidManifest, withDangerousMod } = require("expo/config-plugins");

module.exports = function withCleartextHosts(config, { hosts = [] } = {}) {
  config = withDangerousMod(config, ["android", async (c) => {
    const dir = path.join(c.modRequest.platformProjectRoot, "app", "src", "main", "res", "xml");
    fs.mkdirSync(dir, { recursive: true });
    const domains = hosts.map((h) => `    <domain includeSubdomains="false">${h}</domain>`).join("\n");
    fs.writeFileSync(path.join(dir, "network_security_config.xml"),
      `<?xml version="1.0" encoding="utf-8"?>\n<network-security-config>\n` +
      `  <base-config cleartextTrafficPermitted="false" />\n` +
      `  <domain-config cleartextTrafficPermitted="true">\n${domains}\n  </domain-config>\n</network-security-config>\n`);
    return c;
  }]);
  return withAndroidManifest(config, (c) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(c.modResults);
    app.$["android:networkSecurityConfig"] = "@xml/network_security_config";
    delete app.$["android:usesCleartextTraffic"];
    return c;
  });
};
```

- [ ] **Step 6: `android/app.config.ts`**

```ts
import type { ExpoConfig } from "expo/config";
import { hideKey } from "./src/core/keyBox";

export const APP_VERSION = "1.0.0";
export const VERSION_CODE = 1;

export default function config(): ExpoConfig {
  const key = (process.env.MICO360_MOBILE_CONNECT_KEY || process.env.MICO360_CONNECT_API_KEY || "").trim();
  return {
    name: "MICO360 Meetings",
    slug: "mico360-meetings",
    scheme: "mico360meetings",
    version: APP_VERSION,
    orientation: "portrait",
    icon: "./assets/icon.png",
    userInterfaceStyle: "automatic",
    android: {
      package: "com.mico360.meetings",
      versionCode: VERSION_CODE,
      adaptiveIcon: {
        backgroundColor: "#8B1E1E",
        foregroundImage: "./assets/android-icon-foreground.png",
        backgroundImage: "./assets/android-icon-background.png",
        monochromeImage: "./assets/android-icon-monochrome.png",
      },
      predictiveBackGestureEnabled: false,
      permissions: ["RECORD_AUDIO", "POST_NOTIFICATIONS", "REQUEST_INSTALL_PACKAGES"],
    },
    plugins: [
      "expo-router",
      "expo-sqlite",
      ["@siteed/audio-studio", { enablePhoneStateHandling: true, enableNotifications: true, enableBackgroundAudio: true, enableDeviceDetection: false }],
      ["expo-notifications", { color: "#8B1E1E" }],
      ["expo-build-properties", { android: { minSdkVersion: 26 } }],
      ["./plugins/withCleartextHosts.js", { hosts: ["ai.mico360.com", "localhost", "10.0.2.2", "127.0.0.1"] }],
    ],
    experiments: { typedRoutes: false },
    extra: {
      connect: key ? hideKey(key) : null,
      e2eConnectUrl: process.env.MICO360_E2E_CONNECT_URL ?? "",
    },
  };
}
```

- [ ] **Step 7: Placeholder routes** — `android/src/app/_layout.tsx` and `android/src/app/index.tsx`

```tsx
// _layout.tsx
import { getRandomBytes } from "expo-crypto";
import { Stack } from "expo-router";
import { setRandomSource } from "../core/random";

setRandomSource(getRandomBytes);   // Hermes has no WebCrypto: request ids and record ids need a secure source

export default function RootLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
```

```tsx
// index.tsx
import { Text, View } from "react-native";

export default function Home() {
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
      <Text accessibilityLabel="app-title">MICO360 Meetings</Text>
    </View>
  );
}
```

- [ ] **Step 8: Build mirror** — `android/tools/build-mirror.ps1`

```powershell
<# Build the Android app OUTSIDE Dropbox: mirror the sources, install, prebuild, gradle.
     powershell -File android\tools\build-mirror.ps1 [-Variant debug|release] #>
param([ValidateSet("debug", "release")][string]$Variant = "release",
      [string]$BuildDir = "$env:USERPROFILE\mico360-build\app")
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot
robocopy $src $BuildDir /MIR /XD node_modules android .expo .claude /XF .lockhash /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
Push-Location $BuildDir
try {
  $lock = (Get-FileHash package-lock.json).Hash
  if (-not (Test-Path node_modules) -or -not (Test-Path .lockhash) -or (Get-Content .lockhash) -ne $lock) {
    npm ci; if ($LASTEXITCODE) { throw "npm ci failed" }
    Set-Content .lockhash $lock
  }
  $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
  npx expo prebuild --platform android --no-install; if ($LASTEXITCODE) { throw "prebuild failed" }
  Push-Location android
  try {
    .\gradlew.bat ("assemble" + (Get-Culture).TextInfo.ToTitleCase($Variant)); if ($LASTEXITCODE) { throw "gradle failed" }
  } finally { Pop-Location }
  $apk = Get-ChildItem "android\app\build\outputs\apk\$Variant\*.apk" | Select-Object -First 1
  Write-Output "APK: $($apk.FullName)"
} finally { Pop-Location }
```

`android/tools/check-native.mjs` — verifies the generated native project after prebuild:

```js
// node android/tools/check-native.mjs <buildDir>
import { readFileSync } from "node:fs";
import { join } from "node:path";
const root = process.argv[2];
const manifest = readFileSync(join(root, "android/app/src/main/AndroidManifest.xml"), "utf8");
const nsc = readFileSync(join(root, "android/app/src/main/res/xml/network_security_config.xml"), "utf8");
const checks = [
  ["networkSecurityConfig wired", manifest.includes('android:networkSecurityConfig="@xml/network_security_config"')],
  ["cleartext only for listed hosts", nsc.includes('<base-config cleartextTrafficPermitted="false" />') && nsc.includes("ai.mico360.com")],
  ["microphone permission", manifest.includes("android.permission.RECORD_AUDIO")],
  ["microphone foreground service", /foregroundServiceType="microphone"/.test(manifest)],
  ["install packages permission", manifest.includes("android.permission.REQUEST_INSTALL_PACKAGES")],
];
let ok = true;
for (const [name, pass] of checks) { console.log(`${pass ? "PASS" : "FAIL"} ${name}`); ok &&= pass; }
process.exit(ok ? 0 : 1);
```

- [ ] **Step 9: Run tests, typecheck, build and check the native project**

```bash
cd android && npm test && npm run typecheck
powershell -File tools/build-mirror.ps1 -Variant release
node tools/check-native.mjs "$USERPROFILE/mico360-build/app"
```

Expected: all vitest suites pass; tsc 0; `APK: …app-release.apk`; five `PASS` lines.

- [ ] **Step 10: Launch on the emulator**

```bash
SDK="$LOCALAPPDATA/Android/Sdk"
"$SDK/cmdline-tools/latest/bin/avdmanager.bat" create avd -n mico360_meetings_test -k "system-images;android-34;google_apis;x86_64" -d pixel_6 --force
"$SDK/emulator/emulator.exe" -avd mico360_meetings_test -no-snapshot -no-boot-anim -gpu swiftshader_indirect &
"$SDK/platform-tools/adb.exe" wait-for-device shell 'while [ "$(getprop sys.boot_completed)" != 1 ]; do sleep 2; done'
"$SDK/platform-tools/adb.exe" install -r "$USERPROFILE/mico360-build/app/android/app/build/outputs/apk/release/app-release.apk"
"$SDK/platform-tools/adb.exe" shell am start -n com.mico360.meetings/.MainActivity && sleep 8
"$SDK/platform-tools/adb.exe" exec-out uiautomator dump /dev/tty | grep -o "MICO360 Meetings" | head -1
```

Expected: `MICO360 Meetings`.

- [ ] **Step 11: Commit**

```bash
git add android
git commit -m "Android: native modules, config plugins, key box, build mirror"
```

---

### Task 2: SQLite data layer

**Files:**
- Create: `android/src/data/db.ts`, `android/src/data/schema.ts`, `android/src/data/meetings.ts`, `android/src/data/actions.ts`, `android/src/data/library.ts`, `android/src/data/settings.ts`, `android/test/nodeDb.ts`, `android/test/data.test.ts`
- Modify: `android/test/purity.test.ts` (scan `src/data` and `src/services` too)

**Interfaces:**
- Consumes: `itemsForMeeting`, `applyOverride`, `normalizeStatus`, `normalizePriority`, `ActionItem`, `ItemOverride` (Plan 1 T8); `BUILTIN_PROMPTS`, `SavedPrompt`, `promptId`, `PromptImportPlan` (T5); `BUILTIN_MEETING_TYPES`, `MeetingType` (T5); `CompanyProfile`, `sanitizeProfile` (T10).
- Produces (`db.ts`): `type SqlParam = string | number | null`, `interface Db {execAsync, runAsync, getAllAsync, getFirstAsync, withTransactionAsync}`, `newId(): string`.
- Produces (`schema.ts`): `SCHEMA_VERSION`, `migrate(db)`, `seed(db)`.
- Produces (`meetings.ts`): `interface TranscriptSegment {start, end, text, speaker?}`, `interface Meeting`, `createMeeting(db, init)`, `getMeeting(db, id)`, `updateMeeting(db, id, patch)`, `listMeetings(db, {search?, status?})`, `deleteMeeting(db, id)`.
- Produces (`actions.ts`): `listActionItems(db, search?)`, `saveItemEdits(db, item, ov)`, `setItemStatus(db, item, status)`, `resetItem(db, item)`.
- Produces (`library.ts`): `listProfiles`, `getProfile`, `saveProfile`, `deleteProfile`; `listPrompts`, `getPrompt`, `addPrompt`, `updatePrompt`, `deletePrompt`, `setPromptFavorite`, `restorePrompt`, `applyPromptImport`; `listMeetingTypes`, `saveMeetingType`, `deleteMeetingType`.
- Produces (`settings.ts`): `interface AppSettings`, `DEFAULT_SETTINGS`, `getSettings(db)`, `setSetting(db, key, value)`.

- [ ] **Step 1: Test adapter** — `android/test/nodeDb.ts`

```ts
import { DatabaseSync } from "node:sqlite";
import type { Db, SqlParam } from "../src/data/db";

/** The Db interface over Node's built-in SQLite (rows copied to plain objects). */
export function nodeDb(): Db {
  const db = new DatabaseSync(":memory:");
  const plain = <T>(r: unknown) => (r ? ({ ...(r as object) } as T) : null);
  return {
    async execAsync(sql) { db.exec(sql); },
    async runAsync(sql, params: SqlParam[] = []) {
      const r = db.prepare(sql).run(...params);
      return { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
    },
    async getAllAsync<T>(sql: string, params: SqlParam[] = []) { return db.prepare(sql).all(...params).map((r) => plain<T>(r)!); },
    async getFirstAsync<T>(sql: string, params: SqlParam[] = []) { return plain<T>(db.prepare(sql).get(...params)); },
    async withTransactionAsync(fn) {
      db.exec("BEGIN");
      try { await fn(); db.exec("COMMIT"); } catch (e) { db.exec("ROLLBACK"); throw e; }
    },
  };
}
```

- [ ] **Step 2: Write the failing test** — `android/test/data.test.ts`

```ts
import { beforeEach, describe, expect, it } from "vitest";
import { nodeDb } from "./nodeDb";
import type { Db } from "../src/data/db";
import { migrate, seed } from "../src/data/schema";
import { createMeeting, deleteMeeting, getMeeting, listMeetings, updateMeeting } from "../src/data/meetings";
import { listActionItems, resetItem, saveItemEdits, setItemStatus } from "../src/data/actions";
import * as L from "../src/data/library";
import { getSettings, setSetting } from "../src/data/settings";
import { DEFAULT_PROFILE } from "../src/core/profiles";

const MINUTES = "## Action Items\n| Task | Owner | Deadline | Status |\n|---|---|---|---|\n| Send report | Ali | 2026-10-01 | Pending |\n| Send report | Bob | 2026-10-30 | Done |";
let db: Db;

beforeEach(async () => { db = nodeDb(); await migrate(db); await seed(db); });

describe("schema", () => {
  it("migrates once and seeds the built-ins once", async () => {
    await migrate(db); await seed(db);
    expect((await db.getFirstAsync<{ user_version: number }>("PRAGMA user_version"))!.user_version).toBe(1);
    expect(await L.listPrompts(db)).toHaveLength(25);
    expect(await L.listMeetingTypes(db)).toHaveLength(6);
  });
});

describe("meetings", () => {
  it("create, read, update (updated_at moves), search, delete", async () => {
    const m = await createMeeting(db, { title: "Budget review", transcript: "We discussed the budget." });
    expect(m.id).toMatch(/^[0-9a-f]{16}$/);
    expect(m.status).toBe("draft");
    const u = await updateMeeting(db, m.id, { minutes: MINUTES, segments: [{ start: 0, end: 1, text: "Hi", speaker: "Speaker 1" }] });
    expect(u.updatedAt).toBeGreaterThanOrEqual(m.updatedAt);
    expect((await getMeeting(db, m.id))!.segments[0].speaker).toBe("Speaker 1");
    await createMeeting(db, { title: "Other" });
    expect((await listMeetings(db, { search: "budget" })).map((x) => x.title)).toEqual(["Budget review"]);
    expect(await listMeetings(db, { search: "100%_" })).toEqual([]);
    await deleteMeeting(db, m.id);
    expect(await getMeeting(db, m.id)).toBeNull();
  });
});

describe("action items", () => {
  it("come from the minutes and keep their own edits per row", async () => {
    const m = await createMeeting(db, { title: "Budget", minutes: MINUTES, createdAt: Date.UTC(2026, 9, 5, 12) });
    let items = await listActionItems(db);
    expect(items.map((i) => [i.owner, i.status])).toEqual([["Ali", "Pending"], ["Bob", "Completed"]]);
    await setItemStatus(db, items[0], "In Progress");
    await saveItemEdits(db, items[1], { owner: "Sara", notes: "call first" });
    items = await listActionItems(db);
    expect(items.map((i) => [i.owner, i.status, i.notes])).toEqual([["Ali", "In Progress", ""], ["Sara", "Completed", "call first"]]);
    expect(await listActionItems(db, "sara")).toHaveLength(1);
    await resetItem(db, items[1]);
    expect((await listActionItems(db))[1].owner).toBe("Bob");
    await deleteMeeting(db, m.id);
    expect(await db.getAllAsync("SELECT * FROM action_overrides")).toEqual([]);
  });
});

describe("library", () => {
  it("profiles are sanitised and sorted", async () => {
    await L.saveProfile(db, { ...DEFAULT_PROFILE, name: "beta", accent_color: "javascript:x" });
    const a = await L.saveProfile(db, { ...DEFAULT_PROFILE, name: "Alpha" });
    const list = await L.listProfiles(db);
    expect(list.map((p) => p.name)).toEqual(["Alpha", "beta"]);
    expect(list[1].accent_color).toBe("#8B1E1E");
    expect((await L.getProfile(db, a.id))!.name).toBe("Alpha");
    await L.deleteProfile(db, a.id);
    expect(await L.getProfile(db, a.id)).toBeNull();
  });
  it("a renamed built-in prompt becomes the user's own; restore puts the text back", async () => {
    const formal = (await L.listPrompts(db)).find((p) => p.name === "Formal Minutes")!;
    await L.updatePrompt(db, formal.id, { text: "edited" });
    expect((await L.getPrompt(db, formal.id))!.builtin).toBe(true);
    await L.restorePrompt(db, formal.id);
    expect((await L.getPrompt(db, formal.id))!.text).toContain("[TRANSCRIPT_HERE]");
    await L.updatePrompt(db, formal.id, { name: "My formal" });
    expect((await L.getPrompt(db, formal.id))!.builtin).toBe(false);
    await seed(db);
    expect((await L.listPrompts(db)).some((p) => p.name === "Formal Minutes" && p.builtin)).toBe(true);
  });
  it("meeting types upsert by name and keep their order", async () => {
    await L.saveMeetingType(db, { name: "Weekly", prompt_name: "Short Summary", style: "Short Summary", profile_name: "" });
    await L.saveMeetingType(db, { name: "Weekly", prompt_name: "Decision Log", style: "", profile_name: "" });
    const types = await L.listMeetingTypes(db);
    expect(types).toHaveLength(7);
    expect(types[6]).toMatchObject({ name: "Weekly", prompt_name: "Decision Log" });
    await L.deleteMeetingType(db, "Weekly");
    expect(await L.listMeetingTypes(db)).toHaveLength(6);
  });
});

describe("settings", () => {
  it("defaults, typed writes, unknown junk ignored", async () => {
    expect((await getSettings(db)).whisperModel).toBe("base");
    await setSetting(db, "diarize", true);
    await db.runAsync("INSERT INTO settings (key, value) VALUES ('chunkChars', 'not json')");
    const s = await getSettings(db);
    expect(s.diarize).toBe(true);
    expect(s.chunkChars).toBe(6000);
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd android && npx vitest run test/data.test.ts`
Expected: FAIL — `src/data/*` missing.

- [ ] **Step 4: `android/src/data/db.ts` and `android/src/data/schema.ts`**

```ts
// db.ts
import { randomHex } from "../core/random";

/** The subset of expo-sqlite's SQLiteDatabase the app uses (tests back it with node:sqlite). */
export type SqlParam = string | number | null;

export interface Db {
  execAsync(sql: string): Promise<void>;
  runAsync(sql: string, params?: SqlParam[]): Promise<{ changes: number; lastInsertRowId: number }>;
  getAllAsync<T>(sql: string, params?: SqlParam[]): Promise<T[]>;
  getFirstAsync<T>(sql: string, params?: SqlParam[]): Promise<T | null>;
  withTransactionAsync(fn: () => Promise<void>): Promise<void>;
}

export function newId(): string {
  return randomHex(8);
}
```

```ts
// schema.ts
import { BUILTIN_MEETING_TYPES } from "../core/meetingTypes";
import { BUILTIN_PROMPTS, promptId } from "../core/prompts";
import type { Db } from "./db";

export const SCHEMA_VERSION = 1;

const V1 = `
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  meeting_date TEXT NOT NULL DEFAULT '', attendees TEXT NOT NULL DEFAULT '', meeting_type TEXT NOT NULL DEFAULT '',
  profile_id TEXT NOT NULL DEFAULT '', prompt_name TEXT NOT NULL DEFAULT '', style TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '', audio_path TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '', segments_json TEXT NOT NULL DEFAULT '[]',
  minutes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft', model TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS meetings_created ON meetings (created_at DESC);
CREATE TABLE IF NOT EXISTS action_overrides (okey TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompts (id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, text TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meeting_types (name TEXT PRIMARY KEY, prompt_name TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT '', profile_name TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);`;

export async function migrate(db: Db): Promise<void> {
  const row = await db.getFirstAsync<{ user_version: number }>("PRAGMA user_version");
  let v = row?.user_version ?? 0;
  if (v >= SCHEMA_VERSION) return;
  if (v < 1) { await db.execAsync(V1); v = 1; }
  await db.execAsync(`PRAGMA user_version = ${SCHEMA_VERSION}`);
}

/** Add any built-in prompt (by name) or, on first run, the meeting types. */
export async function seed(db: Db): Promise<void> {
  const names = new Set((await db.getAllAsync<{ name: string }>("SELECT name FROM prompts")).map((r) => r.name));
  for (const p of BUILTIN_PROMPTS) {
    if (names.has(p.name)) continue;
    await db.runAsync("INSERT INTO prompts (id, name, category, text, builtin, favorite) VALUES (?, ?, ?, ?, 1, 0)",
      [promptId(p.name), p.name, p.category, p.text]);
  }
  const types = await db.getFirstAsync<{ n: number }>("SELECT COUNT(*) AS n FROM meeting_types");
  if (!types?.n) {
    let pos = 0;
    for (const t of BUILTIN_MEETING_TYPES) {
      await db.runAsync("INSERT INTO meeting_types (name, prompt_name, style, profile_name, position) VALUES (?, ?, ?, ?, ?)",
        [t.name, t.prompt_name, t.style, t.profile_name, pos++]);
    }
  }
}
```

- [ ] **Step 5: `android/src/data/meetings.ts`**

```ts
import { type Db, newId, type SqlParam } from "./db";

export interface TranscriptSegment { start: number; end: number; text: string; speaker?: string; }
export type MeetingSource = "record" | "import" | "paste" | "";

export interface Meeting {
  id: string; title: string; createdAt: number; updatedAt: number; meetingDate: string; attendees: string;
  meetingType: string; profileId: string; promptName: string; style: string; source: MeetingSource;
  audioPath: string; durationMs: number; language: string; transcript: string; segments: TranscriptSegment[];
  minutes: string; status: "draft" | "final"; model: string;
}

interface Row {
  id: string; title: string; created_at: number; updated_at: number; meeting_date: string; attendees: string;
  meeting_type: string; profile_id: string; prompt_name: string; style: string; source: string; audio_path: string;
  duration_ms: number; language: string; transcript: string; segments_json: string; minutes: string; status: string; model: string;
}

const COLUMNS: Array<[keyof Meeting, keyof Row]> = [
  ["title", "title"], ["createdAt", "created_at"], ["updatedAt", "updated_at"], ["meetingDate", "meeting_date"],
  ["attendees", "attendees"], ["meetingType", "meeting_type"], ["profileId", "profile_id"], ["promptName", "prompt_name"],
  ["style", "style"], ["source", "source"], ["audioPath", "audio_path"], ["durationMs", "duration_ms"],
  ["language", "language"], ["transcript", "transcript"], ["minutes", "minutes"], ["status", "status"], ["model", "model"],
];

function parseSegments(json: string): TranscriptSegment[] {
  try { const v = JSON.parse(json); return Array.isArray(v) ? v : []; } catch { return []; }
}

function fromRow(r: Row): Meeting {
  return {
    id: r.id, title: r.title, createdAt: Number(r.created_at), updatedAt: Number(r.updated_at), meetingDate: r.meeting_date,
    attendees: r.attendees, meetingType: r.meeting_type, profileId: r.profile_id, promptName: r.prompt_name, style: r.style,
    source: (r.source as MeetingSource) || "", audioPath: r.audio_path, durationMs: Number(r.duration_ms), language: r.language,
    transcript: r.transcript, segments: parseSegments(r.segments_json), minutes: r.minutes,
    status: r.status === "final" ? "final" : "draft", model: r.model,
  };
}

const value = (m: Partial<Meeting>, k: keyof Meeting): SqlParam => {
  const v = m[k];
  return typeof v === "number" || typeof v === "string" ? v : v === undefined || v === null ? null : String(v);
};

export async function createMeeting(db: Db, init: Partial<Meeting>): Promise<Meeting> {
  const now = Date.now();
  const m: Meeting = {
    id: newId(), title: "", createdAt: now, updatedAt: now, meetingDate: "", attendees: "", meetingType: "", profileId: "",
    promptName: "", style: "", source: "", audioPath: "", durationMs: 0, language: "", transcript: "", segments: [],
    minutes: "", status: "draft", model: "", ...init,
  };
  m.updatedAt = Math.max(m.updatedAt, m.createdAt);
  await db.runAsync(
    `INSERT INTO meetings (id, ${COLUMNS.map(([, c]) => c).join(", ")}, segments_json) VALUES (?, ${COLUMNS.map(() => "?").join(", ")}, ?)`,
    [m.id, ...COLUMNS.map(([k]) => value(m, k)), JSON.stringify(m.segments)]);
  return m;
}

export async function getMeeting(db: Db, id: string): Promise<Meeting | null> {
  const r = await db.getFirstAsync<Row>("SELECT * FROM meetings WHERE id = ?", [id]);
  return r ? fromRow(r) : null;
}

export async function updateMeeting(db: Db, id: string, patch: Partial<Omit<Meeting, "id" | "createdAt">>): Promise<Meeting> {
  const cur = await getMeeting(db, id);
  if (!cur) throw new Error("This meeting no longer exists.");
  const next: Meeting = { ...cur, ...patch, id, updatedAt: Math.max(Date.now(), cur.updatedAt) };
  await db.runAsync(
    `UPDATE meetings SET ${COLUMNS.map(([, c]) => `${c} = ?`).join(", ")}, segments_json = ? WHERE id = ?`,
    [...COLUMNS.map(([k]) => value(next, k)), JSON.stringify(next.segments), id]);
  return next;
}

const likeEscape = (s: string) => s.replace(/[\\%_]/g, (c) => "\\" + c);

export async function listMeetings(db: Db, f: { search?: string; status?: "draft" | "final" } = {}): Promise<Meeting[]> {
  const where: string[] = [];
  const params: SqlParam[] = [];
  const q = (f.search ?? "").trim();
  if (q) {
    where.push("(title LIKE ? ESCAPE '\\' OR transcript LIKE ? ESCAPE '\\' OR minutes LIKE ? ESCAPE '\\' OR attendees LIKE ? ESCAPE '\\')");
    const p = `%${likeEscape(q)}%`;
    params.push(p, p, p, p);
  }
  if (f.status) { where.push("status = ?"); params.push(f.status); }
  const rows = await db.getAllAsync<Row>(
    `SELECT * FROM meetings ${where.length ? "WHERE " + where.join(" AND ") : ""} ORDER BY created_at DESC`, params);
  return rows.map(fromRow);
}

export async function deleteMeeting(db: Db, id: string): Promise<void> {
  await db.withTransactionAsync(async () => {
    await db.runAsync("DELETE FROM action_overrides WHERE meeting_id = ?", [id]);
    await db.runAsync("DELETE FROM meetings WHERE id = ?", [id]);
  });
}
```

- [ ] **Step 6: `android/src/data/actions.ts`**

```ts
import { type ActionItem, applyOverride, itemsForMeeting, type ItemOverride, normalizePriority, normalizeStatus } from "../core/actionItems";
import type { Db } from "./db";

interface Lite { id: string; title: string; created_at: number; minutes: string; }

export async function listActionItems(db: Db, search = ""): Promise<ActionItem[]> {
  const meetings = await db.getAllAsync<Lite>("SELECT id, title, created_at, minutes FROM meetings ORDER BY created_at DESC");
  const ovRows = await db.getAllAsync<{ okey: string; data: string }>("SELECT okey, data FROM action_overrides");
  const overrides = new Map(ovRows.map((r) => [r.okey, safeJson(r.data)]));
  const out: ActionItem[] = [];
  for (const m of meetings) {
    for (const it of itemsForMeeting({ id: m.id, title: m.title, createdAt: Number(m.created_at), minutes: m.minutes })) {
      applyOverride(it, overrides.get(it.okey) as ItemOverride | undefined);
      it.status = normalizeStatus(it.status);
      it.priority = normalizePriority(it.priority);
      out.push(it);
    }
  }
  const q = search.trim().toLowerCase();
  if (!q) return out;
  return out.filter((i) => [i.task, i.owner, i.meetingTitle, i.status, i.notes].some((v) => (v ?? "").toLowerCase().includes(q)));
}

function safeJson(s: string): unknown {
  try { return JSON.parse(s); } catch { return undefined; }
}

async function currentOverride(db: Db, okey: string): Promise<ItemOverride> {
  const r = await db.getFirstAsync<{ data: string }>("SELECT data FROM action_overrides WHERE okey = ?", [okey]);
  const v = r ? safeJson(r.data) : undefined;
  return v && typeof v === "object" ? (v as ItemOverride) : {};
}

export async function saveItemEdits(db: Db, item: ActionItem, ov: ItemOverride): Promise<void> {
  const merged = { ...(await currentOverride(db, item.okey)), ...ov };
  if (merged.priority !== undefined) merged.priority = normalizePriority(merged.priority);
  await db.runAsync("INSERT OR REPLACE INTO action_overrides (okey, meeting_id, data) VALUES (?, ?, ?)",
    [item.okey, item.meetingId, JSON.stringify(merged)]);
}

export async function setItemStatus(db: Db, item: ActionItem, status: string): Promise<void> {
  await saveItemEdits(db, item, { status });
}

export async function resetItem(db: Db, item: ActionItem): Promise<void> {
  await db.runAsync("DELETE FROM action_overrides WHERE okey = ?", [item.okey]);
}
```

- [ ] **Step 7: `android/src/data/library.ts`**

```ts
import type { MeetingType } from "../core/meetingTypes";
import { type CompanyProfile, sanitizeProfile } from "../core/profiles";
import { builtinText, BUILTIN_PROMPTS, type PromptImportPlan, promptId, type SavedPrompt } from "../core/prompts";
import { type Db, newId } from "./db";

// ---- profiles ------------------------------------------------------------------
export async function listProfiles(db: Db): Promise<CompanyProfile[]> {
  const rows = await db.getAllAsync<{ data: string }>("SELECT data FROM profiles ORDER BY lower(name)");
  return rows.map((r) => sanitizeProfile(JSON.parse(r.data) as CompanyProfile));
}

export async function getProfile(db: Db, id: string): Promise<CompanyProfile | null> {
  if (!id) return null;
  const r = await db.getFirstAsync<{ data: string }>("SELECT data FROM profiles WHERE id = ?", [id]);
  try { return r ? sanitizeProfile(JSON.parse(r.data) as CompanyProfile) : null; } catch { return null; }
}

export async function saveProfile(db: Db, profile: CompanyProfile): Promise<CompanyProfile> {
  const p = sanitizeProfile({ ...profile, id: profile.id || newId().slice(0, 10) });
  await db.runAsync("INSERT OR REPLACE INTO profiles (id, name, data) VALUES (?, ?, ?)", [p.id, p.name, JSON.stringify(p)]);
  return p;
}

export async function deleteProfile(db: Db, id: string): Promise<void> {
  await db.runAsync("DELETE FROM profiles WHERE id = ?", [id]);
}

// ---- prompts -------------------------------------------------------------------
interface PromptRow { id: string; name: string; category: string; text: string; builtin: number; favorite: number; }
const toPrompt = (r: PromptRow): SavedPrompt => ({ id: r.id, name: r.name, category: r.category, text: r.text,
  builtin: !!r.builtin, favorite: !!r.favorite });

export async function listPrompts(db: Db): Promise<SavedPrompt[]> {
  return (await db.getAllAsync<PromptRow>("SELECT * FROM prompts ORDER BY lower(category), lower(name)")).map(toPrompt);
}

export async function getPrompt(db: Db, id: string): Promise<SavedPrompt | null> {
  const r = await db.getFirstAsync<PromptRow>("SELECT * FROM prompts WHERE id = ?", [id]);
  return r ? toPrompt(r) : null;
}

export async function addPrompt(db: Db, p: { name: string; text: string; category?: string; favorite?: boolean }): Promise<SavedPrompt> {
  const name = p.name.trim() || "Untitled";
  const out: SavedPrompt = { id: promptId(name), name, text: p.text, category: p.category?.trim() || "General", builtin: false, favorite: !!p.favorite };
  await db.runAsync("INSERT INTO prompts (id, name, category, text, builtin, favorite) VALUES (?, ?, ?, ?, 0, ?)",
    [out.id, out.name, out.category, out.text, out.favorite ? 1 : 0]);
  return out;
}

/** A renamed built-in becomes the user's own prompt (the original is re-seeded). */
export async function updatePrompt(db: Db, id: string, patch: { name?: string; text?: string; category?: string }): Promise<SavedPrompt | null> {
  const cur = await getPrompt(db, id);
  if (!cur) return null;
  const name = patch.name?.trim() || cur.name;
  const next: SavedPrompt = { ...cur, name, text: patch.text ?? cur.text, category: patch.category ?? cur.category,
    builtin: cur.builtin && name === cur.name };
  await db.runAsync("UPDATE prompts SET name = ?, category = ?, text = ?, builtin = ? WHERE id = ?",
    [next.name, next.category, next.text, next.builtin ? 1 : 0, id]);
  return next;
}

export async function deletePrompt(db: Db, id: string): Promise<void> {
  await db.runAsync("DELETE FROM prompts WHERE id = ?", [id]);
}

export async function setPromptFavorite(db: Db, id: string, favorite: boolean): Promise<void> {
  await db.runAsync("UPDATE prompts SET favorite = ? WHERE id = ?", [favorite ? 1 : 0, id]);
}

export async function restorePrompt(db: Db, id: string): Promise<SavedPrompt | null> {
  const cur = await getPrompt(db, id);
  if (!cur || !cur.builtin) return null;
  const shipped = BUILTIN_PROMPTS.find((p) => p.name === cur.name);
  if (!shipped || builtinText(cur.name) === null) return null;
  await db.runAsync("UPDATE prompts SET text = ?, category = ? WHERE id = ?", [shipped.text, shipped.category, id]);
  return { ...cur, text: shipped.text, category: shipped.category };
}

export async function applyPromptImport(db: Db, plan: PromptImportPlan): Promise<void> {
  await db.withTransactionAsync(async () => {
    for (const p of plan.toAdd) {
      await db.runAsync("INSERT INTO prompts (id, name, category, text, builtin, favorite) VALUES (?, ?, ?, ?, 0, ?)",
        [p.id, p.name, p.category, p.text, p.favorite ? 1 : 0]);
    }
  });
}

// ---- meeting types ---------------------------------------------------------------
export async function listMeetingTypes(db: Db): Promise<MeetingType[]> {
  return db.getAllAsync<MeetingType>("SELECT name, prompt_name, style, profile_name FROM meeting_types ORDER BY position, name");
}

export async function saveMeetingType(db: Db, t: MeetingType): Promise<void> {
  const name = t.name.trim();
  if (!name) throw new Error("A meeting type needs a name.");
  const pos = await db.getFirstAsync<{ p: number | null }>("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM meeting_types");
  await db.runAsync(
    `INSERT INTO meeting_types (name, prompt_name, style, profile_name, position) VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(name) DO UPDATE SET prompt_name = excluded.prompt_name, style = excluded.style, profile_name = excluded.profile_name`,
    [name, t.prompt_name, t.style, t.profile_name, pos?.p ?? 0]);
}

export async function deleteMeetingType(db: Db, name: string): Promise<void> {
  await db.runAsync("DELETE FROM meeting_types WHERE name = ?", [name]);
}
```

- [ ] **Step 8: `android/src/data/settings.ts`**

```ts
import type { Db } from "./db";

export interface AppSettings {
  whisperModel: "tiny" | "base" | "small";
  language: "auto" | "en" | "ar";
  diarize: boolean;
  removeFillers: boolean;
  chunkChars: number;
  cloudModel: string;
  plainHttpConsent: boolean;
  reminders: boolean;
  defaultProfileId: string;
  defaultPromptName: string;
  defaultStyle: string;
  lastUpdateCheck: number;
  autoCheckUpdates: boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  whisperModel: "base", language: "auto", diarize: false, removeFillers: true, chunkChars: 6000, cloudModel: "",
  plainHttpConsent: false, reminders: true, defaultProfileId: "", defaultPromptName: "Formal Minutes",
  defaultStyle: "Formal Minutes", lastUpdateCheck: 0, autoCheckUpdates: true,
};

export async function getSettings(db: Db): Promise<AppSettings> {
  const rows = await db.getAllAsync<{ key: string; value: string }>("SELECT key, value FROM settings");
  const out: AppSettings = { ...DEFAULT_SETTINGS };
  for (const r of rows) {
    if (!(r.key in DEFAULT_SETTINGS)) continue;
    try {
      const v = JSON.parse(r.value);
      const key = r.key as keyof AppSettings;
      if (typeof v === typeof DEFAULT_SETTINGS[key]) (out as unknown as Record<string, unknown>)[key] = v;
    } catch { /* keep the default */ }
  }
  return out;
}

export async function setSetting<K extends keyof AppSettings>(db: Db, key: K, value: AppSettings[K]): Promise<void> {
  await db.runAsync("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", [key, JSON.stringify(value)]);
}
```

- [ ] **Step 9: Extend the purity test** — in `android/test/purity.test.ts` change both loops to scan `["core", "data", "services"]` under `src` (skip missing folders):

```ts
const PURE = ["core", "data", "services"].map((d) => join(__dirname, "..", "src", d)).filter((d) => existsSync(d));
// replace files(join(__dirname, "..", "src", "core")) with PURE.flatMap(files)
```

(Import `existsSync` from `node:fs`.)

- [ ] **Step 10: Run the tests**

Run: `cd android && npx vitest run test/data.test.ts test/purity.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add android/src/data android/test/nodeDb.ts android/test/data.test.ts android/test/purity.test.ts
git commit -m "Android: SQLite schema and repositories"
```

---

### Task 3: Streaming speaker statistics and windowed transcription

**Files:**
- Create: `android/src/core/transcribeLong.ts`, `android/test/transcribeLong.test.ts`

**Interfaces:**
- Consumes (`diarization.ts`, added by the Plan 1 review fix 4fc7280): `SegmentStats`, `segmentStats(pcm, startSec, endSec)`, `diarizeStats(stats, durations, opts?)`, `yieldToUi(signal?)`.
- Produces (`transcribeLong.ts`): `interface Recognizer {transcribe(samples: Float32Array, language: string, signal?): Promise<{language: string; segments: Array<{text: string; t0: number; t1: number}>}>}` (t0/t1 in hundredths of a second, whisper.cpp), `interface AudioSource {stream(onChunk: (samples: Float32Array) => Promise<void>, signal?): Promise<{durationMs: number}>}` (16 kHz mono float32), `WINDOW_SECONDS = 600`, `interface Transcript {text, language, segments: TranscriptSegment[], speakers, durationMs}`, `transcribeAudio(source, recognizer, opts): Promise<Transcript>`, `speakerText(segments, names?)`, `timestampedText(segments, names?)`.

- [ ] **Step 1: Write the failing test** — `android/test/transcribeLong.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/diarization.json";
import { diarize, diarizeStats, segmentStats } from "../src/core/diarization";
import { type AudioSource, type Recognizer, speakerText, timestampedText, transcribeAudio } from "../src/core/transcribeLong";

function pcmOf(b64: string): Float32Array {
  const bytes = Buffer.from(b64, "base64");
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Float32Array.from({ length: bytes.length / 2 }, (_x, i) => v.getInt16(i * 2, true) / 32768);
}

/** A source that hands out `pcm` in chunks of `chunk` samples, recording the largest buffer it saw. */
function source(pcm: Float32Array, chunk = 16000): AudioSource {
  return { async stream(onChunk) {
    for (let i = 0; i < pcm.length; i += chunk) await onChunk(pcm.subarray(i, Math.min(pcm.length, i + chunk)));
    return { durationMs: (pcm.length / 16000) * 1000 };
  } };
}

/** A recogniser that emits one 2-second segment per 2 seconds of audio it is given. */
function fakeRecognizer(log: number[]): Recognizer {
  return { async transcribe(samples) {
    log.push(samples.length);
    const secs = samples.length / 16000;
    const segments = [];
    for (let t = 0; t + 0.5 < secs; t += 2) segments.push({ text: ` word${Math.round(t)} `, t0: Math.round(t * 100), t1: Math.round(Math.min(secs, t + 2) * 100) });
    return { language: "en", segments };
  } };
}

describe("streaming speaker statistics", () => {
  it("statistics per segment give the same speakers as the whole-file path", async () => {
    const c = fx.two;
    const pcm = pcmOf(c.pcm16);
    const stats = c.segments.map(([a, b]) => segmentStats(pcm, a, b));
    expect(diarizeStats(stats, c.segments.map(([a, b]) => b - a))).toEqual(await diarize(pcm, c.segments.map(([start, end]) => ({ start, end }))));
  });
});

describe("windowed transcription", () => {
  it("never hands the recogniser more than one window, and keeps time continuous", async () => {
    const log: number[] = [];
    const pcm = new Float32Array(16000 * 25);                 // 25 s
    const t = await transcribeAudio(source(pcm), fakeRecognizer(log), { windowSeconds: 10 });
    expect(Math.max(...log)).toBeLessThanOrEqual(16000 * 10);
    expect(log).toHaveLength(3);
    const starts = t.segments.map((s) => s.start);
    expect(starts).toEqual([...starts].sort((a, b) => a - b));
    expect(t.segments.find((s) => s.text === "word0" && s.start === 10)).toBeTruthy();
    expect(t.durationMs).toBe(25000);
    expect(t.text.startsWith("word0 word2")).toBe(true);
  });
  it("identifies speakers across windows", async () => {
    const c = fx.two;
    const pcm = pcmOf(c.pcm16);
    const rec: Recognizer = { async transcribe(samples) {
      return { language: "en", segments: c.segments.map(([a, b], i) => ({ text: `turn ${i}`, t0: Math.round(a * 100), t1: Math.round(b * 100) }))
        .filter((s) => s.t1 / 100 <= samples.length / 16000 + 0.01) };
    } };
    const t = await transcribeAudio(source(pcm), rec, { windowSeconds: 600, diarize: true });
    expect(t.speakers).toBe(2);
    expect(t.segments.map((s) => s.speaker)).toEqual(c.labels.map((l: number) => `Speaker ${l + 1}`));
    expect(speakerText(t.segments).split("\n")[0]).toBe("[Speaker 1] turn 0");
    expect(speakerText(t.segments, { "Speaker 1": "Ali" }).split("\n")[0]).toBe("[Ali] turn 0");
    expect(timestampedText(t.segments).split("\n")[1]).toMatch(/^\[00:00:0\d\] Speaker 2: turn 1$/);
  });
  it("cancel stops between windows", async () => {
    const ctl = new AbortController();
    const rec: Recognizer = { async transcribe() { ctl.abort(); return { language: "en", segments: [] }; } };
    await expect(transcribeAudio(source(new Float32Array(16000 * 30)), rec, { windowSeconds: 10, signal: ctl.signal })).rejects.toThrow(/cancelled/i);
  });
  it("silence-only audio gives an empty transcript, not an error", async () => {
    const t = await transcribeAudio(source(new Float32Array(16000 * 3)), { async transcribe() { return { language: "ar", segments: [] }; } }, {});
    expect(t.text).toBe("");
    expect(t.language).toBe("ar");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/transcribeLong.test.ts`
Expected: FAIL — `transcribeLong` missing.

- [ ] **Step 3: `android/src/core/transcribeLong.ts`**

```ts
/** On-device transcription of any length: 16 kHz mono audio is processed in
 *  windows (never the whole recording in memory), timestamps are made
 *  continuous, and speakers are identified across windows. */
import { diarizeStats, segmentStats, type SegmentStats, yieldToUi } from "./diarization";
import { CancelledError, type ProgressFn } from "./pipeline";

export const SAMPLE_RATE = 16000;
export const WINDOW_SECONDS = 600;

export interface Recognizer {
  transcribe(samples: Float32Array, language: string, signal?: AbortSignal):
    Promise<{ language: string; segments: Array<{ text: string; t0: number; t1: number }> }>;
}
export interface AudioSource {
  stream(onChunk: (samples: Float32Array) => Promise<void>, signal?: AbortSignal): Promise<{ durationMs: number }>;
}
export interface TranscriptSegment { start: number; end: number; text: string; speaker?: string; }
export interface Transcript { text: string; language: string; segments: TranscriptSegment[]; speakers: number; durationMs: number; }

export interface TranscribeOptions {
  language?: string; windowSeconds?: number; diarize?: boolean; progress?: ProgressFn; totalMs?: number; signal?: AbortSignal;
}

export async function transcribeAudio(source: AudioSource, recognizer: Recognizer, o: TranscribeOptions = {}): Promise<Transcript> {
  const windowSamples = Math.max(1, Math.round((o.windowSeconds ?? WINDOW_SECONDS) * SAMPLE_RATE));
  let buf = new Float32Array(windowSamples);
  let fill = 0;
  let offsetSec = 0;
  let language = o.language && o.language !== "auto" ? o.language : "";
  const segments: TranscriptSegment[] = [];
  const stats: SegmentStats[] = [];
  const check = () => { if (o.signal?.aborted) throw new CancelledError("Transcription cancelled."); };

  const flush = async () => {
    if (fill < SAMPLE_RATE / 2) { offsetSec += fill / SAMPLE_RATE; fill = 0; return; }   // < 0.5 s: nothing to hear
    check();
    const window = buf.subarray(0, fill);
    const res = await recognizer.transcribe(window, language || o.language || "auto", o.signal);
    if (!language && res.language) language = res.language;
    let n = 0;
    for (const s of res.segments) {
      const text = (s.text ?? "").trim();
      if (!text) continue;
      const a = s.t0 / 100, b = Math.max(s.t0, s.t1) / 100;
      segments.push({ start: offsetSec + a, end: offsetSec + b, text });
      if (o.diarize) {
        if (++n % 25 === 0) await yieldToUi(o.signal);      // MFCCs are slow on Hermes: keep the UI alive
        stats.push(segmentStats(window, a, b));
      }
    }
    offsetSec += fill / SAMPLE_RATE;
    fill = 0;
    buf = new Float32Array(windowSamples);
    if (o.totalMs) o.progress?.(Math.min(0.99, (offsetSec * 1000) / o.totalMs), `Transcribed ${formatTime(offsetSec)}`);
    check();
  };

  const result = await source.stream(async (chunk) => {
    check();
    let i = 0;
    while (i < chunk.length) {
      const n = Math.min(chunk.length - i, windowSamples - fill);
      buf.set(chunk.subarray(i, i + n), fill);
      fill += n;
      i += n;
      if (fill === windowSamples) await flush();
    }
  }, o.signal);
  await flush();

  let speakers = 0;
  if (o.diarize && segments.length > 1) {
    const labels = diarizeStats(stats, segments.map((s) => s.end - s.start), { signal: o.signal });
    labels.forEach((l, i) => { segments[i].speaker = `Speaker ${l + 1}`; });
    speakers = Math.max(...labels) + 1;
  }
  o.progress?.(1, "Transcription finished.");
  return {
    text: speakers > 1 ? speakerText(segments) : segments.map((s) => s.text).join(" ").trim(),
    language, segments, speakers, durationMs: result.durationMs || Math.round(offsetSec * 1000),
  };
}

const named = (s: TranscriptSegment, names: Record<string, string>) =>
  (s.speaker && names[s.speaker]?.trim()) || s.speaker || "Speaker ?";

/** "[Speaker 1] …" per change of speaker (desktop as_speaker_text). */
export function speakerText(segments: TranscriptSegment[], names: Record<string, string> = {}): string {
  if (!segments.some((s) => s.speaker)) return segments.map((s) => s.text).join(" ").trim();
  const lines: string[] = [];
  let cur = "";
  let buf: string[] = [];
  for (const s of segments) {
    const who = named(s, names);
    if (who !== cur) { if (buf.length) lines.push(`[${cur}] ${buf.join(" ").trim()}`); cur = who; buf = []; }
    buf.push(s.text.trim());
  }
  if (buf.length) lines.push(`[${cur}] ${buf.join(" ").trim()}`);
  return lines.join("\n");
}

export function formatTime(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return [h, m, s].map((x) => String(x).padStart(2, "0")).join(":");
}

/** "[hh:mm:ss] Speaker: text" per segment (desktop as_timestamped). */
export function timestampedText(segments: TranscriptSegment[], names: Record<string, string> = {}): string {
  return segments.map((s) => `[${formatTime(s.start)}] ${s.speaker ? named(s, names) + ": " : ""}${s.text.trim()}`).join("\n");
}
```

- [ ] **Step 4: Run the tests (the Plan 1 diarization parity must still pass)**

Run: `cd android && npx vitest run test/transcribeLong.test.ts test/diarization.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/transcribeLong.ts android/test/transcribeLong.test.ts
git commit -m "Android core: windowed transcription with speakers across windows"
```

---

### Task 4: Recording support — muted-mic watch and WAV repair

**Files:**
- Create: `android/src/core/silence.ts`, `android/src/core/wav.ts`, `android/test/recording.test.ts`, `android/src/native/recorder.ts`

**Interfaces:**
- Produces (`silence.ts`): `MUTED_MIC_SECONDS = 8`, `MUTED_MIC_MESSAGE`, `class SilenceWatch {feed(samples: ArrayLike<number>, sampleRate: number): boolean; reset(): void}`.
- Produces (`wav.ts`): `repairWavHeader(bytes: Uint8Array): Uint8Array | null` (fixes RIFF/data sizes of a WAV whose writer was killed; null when not a PCM WAV).
- Produces (`native/recorder.ts`): `RECORDING_CONFIG`, `useMeetingRecorder(): {start(onSilence): Promise<void>; pause(); resume(); stop(): Promise<{uri, durationMs, size} | null>; isRecording; isPaused; durationMs; size; level}`, `recoverInterruptedRecordings(dir): Promise<string[]>`.

- [ ] **Step 1: Write the failing test** — `android/test/recording.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { MUTED_MIC_SECONDS, SilenceWatch } from "../src/core/silence";
import { repairWavHeader } from "../src/core/wav";
import { decodeWav16 } from "../src/core/diarization";

describe("muted microphone", () => {
  it("warns once after 8 s of digital silence", () => {
    const w = new SilenceWatch();
    const zeros = new Float32Array(16000);
    const hits = Array.from({ length: MUTED_MIC_SECONDS + 3 }, () => w.feed(zeros, 16000));
    expect(hits.filter(Boolean)).toHaveLength(1);
    expect(hits.indexOf(true)).toBe(MUTED_MIC_SECONDS - 1);
  });
  it("never warns once the mic has picked up any sound", () => {
    const w = new SilenceWatch(1);
    expect(w.feed(Float32Array.of(0, 0.0001, 0), 16000)).toBe(false);
    expect(w.feed(new Float32Array(32000), 16000)).toBe(false);
  });
  it("works on 16-bit samples too", () => {
    expect(new SilenceWatch(1).feed(new Int16Array(16000), 16000)).toBe(true);
  });
});

function wav(samples: number, dataSizeField: number, riffSizeField: number): Uint8Array {
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, riffSizeField, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, dataSizeField, true);
  for (let i = 0; i < samples; i++) v.setInt16(44 + i * 2, 1000, true);
  return new Uint8Array(buf);
}

describe("WAV repair after the app was killed while recording", () => {
  it("fixes zero sizes so every recorded sample is readable", () => {
    const fixed = repairWavHeader(wav(16000, 0, 0))!;
    expect(decodeWav16(fixed).pcm.length).toBe(16000);
    const v = new DataView(fixed.buffer);
    expect(v.getUint32(4, true)).toBe(36 + 32000);
    expect(v.getUint32(40, true)).toBe(32000);
  });
  it("leaves a healthy file alone and refuses non-WAV bytes", () => {
    const ok = wav(10, 20, 56);
    expect(repairWavHeader(ok)).toEqual(ok);
    expect(repairWavHeader(new Uint8Array([1, 2, 3]))).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/recording.test.ts`
Expected: FAIL — modules missing.

- [ ] **Step 3: `android/src/core/silence.ts`**

```ts
/** M37 on the phone: a working microphone always picks up some noise; a stream
 *  of exact zeros means it is muted (or the audio engine is wedged). */
export const MUTED_MIC_SECONDS = 8;
export const MUTED_MIC_MESSAGE =
  "The microphone is sending complete silence — it may be muted (on the phone, the headset or by another app). Check it, or stop and try again.";

export class SilenceWatch {
  private silent = 0;
  private done = false;
  constructor(private readonly seconds = MUTED_MIC_SECONDS) {}

  /** Feed captured samples; true exactly once, when only silence has arrived for `seconds`. */
  feed(samples: ArrayLike<number>, sampleRate: number): boolean {
    if (this.done) return false;
    for (let i = 0; i < samples.length; i++) {
      if (samples[i] !== 0 && Math.abs(samples[i]) > 1e-6) { this.done = true; return false; }
    }
    this.silent += samples.length;
    if (this.silent / sampleRate >= this.seconds) { this.done = true; return true; }
    return false;
  }

  reset(): void { this.silent = 0; this.done = false; }
}
```

- [ ] **Step 4: `android/src/core/wav.ts`**

```ts
/** Repair the size fields of a PCM WAV whose writer stopped before finalising
 *  (app killed mid-recording). Returns the bytes with correct sizes, or null. */
export function repairWavHeader(bytes: Uint8Array): Uint8Array | null {
  if (bytes.length < 44) return null;
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  let o = 12;
  while (o + 8 <= bytes.length) {
    const id = tag(o);
    const size = v.getUint32(o + 4, true);
    if (id === "data") {
      const actual = bytes.length - o - 8;
      if (size === actual && v.getUint32(4, true) === bytes.length - 8) return bytes;
      const out = bytes.slice();
      const w = new DataView(out.buffer);
      w.setUint32(o + 4, actual - (actual % 2), true);
      w.setUint32(4, out.length - 8, true);
      return out;
    }
    if (o + 8 + size > bytes.length) return null;
    o += 8 + size + (size % 2);
  }
  return null;
}
```

- [ ] **Step 5: `android/src/native/recorder.ts`** (adapter; exercised on the emulator in Plan 3)

```ts
import { type AudioDataEvent, type RecordingConfig, useSharedAudioRecorder } from "@siteed/audio-studio";
import { Directory, File, Paths } from "expo-file-system";
import { useCallback, useRef, useState } from "react";
import { SilenceWatch } from "../core/silence";
import { repairWavHeader } from "../core/wav";

export const recordingsDir = () => new Directory(Paths.document, "recordings");

export function RECORDING_CONFIG(filename: string, onStream: (e: AudioDataEvent) => Promise<void>): RecordingConfig {
  const dir = recordingsDir();
  if (!dir.exists) dir.create({ intermediates: true });
  return {
    sampleRate: 16000, channels: 1, encoding: "pcm_16bit", interval: 500, keepAwake: true,
    showNotification: true, notification: { title: "MICO360 Meetings", text: "Recording a meeting" },
    autoResumeAfterInterruption: false, outputDirectory: dir.uri, filename, onAudioStream: onStream,
  };
}

export function useMeetingRecorder() {
  const rec = useSharedAudioRecorder();
  const watch = useRef(new SilenceWatch());
  const [level, setLevel] = useState(0);

  const start = useCallback(async (onSilence: () => void) => {
    watch.current = new SilenceWatch();
    const name = `meeting-${Date.now()}`;
    await rec.startRecording(RECORDING_CONFIG(name, async (e) => {
      const d = e.data;
      if (typeof d === "string") return;
      const samples = d instanceof Int16Array ? Float32Array.from(d, (x) => x / 32768) : d;
      let peak = 0;
      for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
      setLevel(peak);
      if (watch.current.feed(samples, 16000)) onSilence();
    }));
  }, [rec]);

  const stop = useCallback(async () => {
    const r = await rec.stopRecording();
    return r ? { uri: r.fileUri, durationMs: r.durationMs, size: r.size } : null;
  }, [rec]);

  return { start, stop, pause: rec.pauseRecording, resume: rec.resumeRecording,
    isRecording: rec.isRecording, isPaused: rec.isPaused, durationMs: rec.durationMs, size: rec.size, level };
}

/** Recordings whose writer was killed get their WAV header fixed; returns their URIs. */
export async function recoverInterruptedRecordings(known: Set<string>): Promise<string[]> {
  const dir = recordingsDir();
  if (!dir.exists) return [];
  const out: string[] = [];
  for (const f of dir.list()) {
    if (!(f instanceof File) || !f.uri.endsWith(".wav") || known.has(f.uri)) continue;
    const bytes = await f.bytes();
    const fixed = repairWavHeader(bytes);
    if (!fixed) continue;
    if (fixed !== bytes) f.write(fixed);
    out.push(f.uri);
  }
  return out;
}
```

- [ ] **Step 6: Run tests and typecheck**

Run: `cd android && npx vitest run test/recording.test.ts && npm run typecheck`
Expected: PASS; tsc 0. If `@siteed/audio-studio` names differ from the above (`useSharedAudioRecorder`, `AudioDataEvent`, `RecordingConfig` fields), correct them from `node_modules/@siteed/audio-studio/src/AudioStudio.types.ts` and record a ruling.

- [ ] **Step 7: Commit**

```bash
git add android/src/core/silence.ts android/src/core/wav.ts android/src/native/recorder.ts android/test/recording.test.ts
git commit -m "Android: muted-mic watch, WAV repair, recorder adapter"
```

---

### Task 5: Whisper models and the transcription service

**Files:**
- Create: `android/src/core/models.ts`, `android/src/services/transcription.ts`, `android/test/models.test.ts`, `android/src/native/whisper.ts`, `android/src/native/modelFiles.ts`, `android/src/native/decode.ts`

**Interfaces:**
- Produces (`models.ts`): `type ModelId = "tiny" | "base" | "small"`, `interface WhisperModel {id, label, file, url, sizeBytes, sha256}`, `WHISPER_MODELS`, `modelById(id)`, `sha256Chunks(read: (offset: number, length: number) => Promise<Uint8Array>, size: number, chunk?: number): Promise<string>`, `checkModel(model, size, digest): "ok" | "size" | "checksum"`.
- Produces (`services/transcription.ts`): `interface TranscriptionDeps {modelPath(id): Promise<string | null>; recognizer(path): Promise<Recognizer>; source(uri): AudioSource; durationMs(uri): Promise<number>}`, `class ModelMissing extends Error {model}`, `transcribeMeetingAudio(uri, settings, deps, opts): Promise<Transcript>`.
- Produces (`native/*`): `whisperRecognizer(modelPath)`, `fileSource(uri)`, `fileDurationMs(uri)`, `modelPath(id)`, `downloadModel(model, onProgress, signal)`, `deleteModel(model)`, `installedModels()`.

- [ ] **Step 1: Write the failing test** — `android/test/models.test.ts`

```ts
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { describe, expect, it } from "vitest";
import { checkModel, modelById, sha256Chunks, WHISPER_MODELS } from "../src/core/models";
import { ModelMissing, transcribeMeetingAudio } from "../src/services/transcription";
import { DEFAULT_SETTINGS } from "../src/data/settings";

describe("model catalogue", () => {
  it("pins three quantised models from the whisper.cpp repository", () => {
    expect(WHISPER_MODELS.map((m) => m.id)).toEqual(["tiny", "base", "small"]);
    for (const m of WHISPER_MODELS) {
      expect(m.url).toBe(`https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${m.file}`);
      expect(m.sha256).toMatch(/^[0-9a-f]{64}$/);
    }
    expect(modelById("base").file).toBe("ggml-base-q5_1.bin");
  });
  it("hashes a file in chunks exactly like a one-shot hash", async () => {
    const data = Uint8Array.from({ length: 100_003 }, (_x, i) => (i * 31) % 251);
    const digest = await sha256Chunks(async (o, n) => data.subarray(o, o + n), data.length, 4096);
    expect(digest).toBe(bytesToHex(sha256(data)));
  });
  it("a short or altered download is never accepted", () => {
    const m = modelById("tiny");
    expect(checkModel(m, m.sizeBytes - 1, m.sha256)).toBe("size");
    expect(checkModel(m, m.sizeBytes, "0".repeat(64))).toBe("checksum");
    expect(checkModel(m, m.sizeBytes, m.sha256)).toBe("ok");
  });
});

describe("transcription service", () => {
  it("asks for the model when it is not downloaded", async () => {
    await expect(transcribeMeetingAudio("file:///a.wav", DEFAULT_SETTINGS, {
      modelPath: async () => null, recognizer: async () => { throw new Error("unused"); },
      source: () => ({ stream: async () => ({ durationMs: 0 }) }), durationMs: async () => 0,
    })).rejects.toBeInstanceOf(ModelMissing);
  });
  it("uses the chosen model, language and speaker setting", async () => {
    const seen: string[] = [];
    const t = await transcribeMeetingAudio("file:///a.wav", { ...DEFAULT_SETTINGS, whisperModel: "small", language: "ar", diarize: false }, {
      modelPath: async (id) => { seen.push(id); return "/models/" + id; },
      recognizer: async (p) => { seen.push(p); return { transcribe: async (_s, lang) => { seen.push(lang); return { language: lang, segments: [{ text: "مرحبا", t0: 0, t1: 100 }] }; } }; },
      source: () => ({ stream: async (on) => { await on(new Float32Array(16000)); return { durationMs: 1000 }; } }),
      durationMs: async () => 1000,
    });
    expect(seen).toEqual(["small", "/models/small", "ar"]);
    expect(t.text).toBe("مرحبا");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/models.test.ts`
Expected: FAIL — modules missing.

- [ ] **Step 3: `android/src/core/models.ts`**

```ts
/** On-device Whisper models: quantised ggml files from the whisper.cpp
 *  repository, pinned by size and SHA256 (Hugging Face LFS metadata). */
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export type ModelId = "tiny" | "base" | "small";
export interface WhisperModel { id: ModelId; label: string; file: string; url: string; sizeBytes: number; sha256: string; }

const BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/";
const model = (id: ModelId, label: string, file: string, sizeBytes: number, digest: string): WhisperModel =>
  ({ id, label, file, url: BASE + file, sizeBytes, sha256: digest });

export const WHISPER_MODELS: WhisperModel[] = [
  model("tiny", "Tiny — fastest, least accurate (32 MB)", "ggml-tiny-q5_1.bin", 32152673,
    "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7"),
  model("base", "Base — recommended (60 MB)", "ggml-base-q5_1.bin", 59707625,
    "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"),
  model("small", "Small — most accurate, slower (190 MB)", "ggml-small-q5_1.bin", 190085487,
    "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"),
];

export const modelById = (id: ModelId) => WHISPER_MODELS.find((m) => m.id === id) ?? WHISPER_MODELS[1];

/** SHA256 of a file read in chunks (never the whole file in memory). */
export async function sha256Chunks(read: (offset: number, length: number) => Promise<Uint8Array>, size: number, chunk = 4 << 20): Promise<string> {
  const h = sha256.create();
  for (let o = 0; o < size; o += chunk) h.update(await read(o, Math.min(chunk, size - o)));
  return bytesToHex(h.digest());
}

export function checkModel(m: WhisperModel, size: number, digest: string): "ok" | "size" | "checksum" {
  if (size !== m.sizeBytes) return "size";
  return digest.toLowerCase() === m.sha256 ? "ok" : "checksum";
}
```

- [ ] **Step 4: `android/src/services/transcription.ts`**

```ts
import { type ModelId, modelById } from "../core/models";
import type { ProgressFn } from "../core/pipeline";
import { type AudioSource, type Recognizer, type Transcript, transcribeAudio } from "../core/transcribeLong";
import type { AppSettings } from "../data/settings";

export interface TranscriptionDeps {
  modelPath(id: ModelId): Promise<string | null>;
  recognizer(path: string): Promise<Recognizer>;
  source(uri: string): AudioSource;
  durationMs(uri: string): Promise<number>;
}

export class ModelMissing extends Error {
  constructor(readonly model: ModelId) {
    super(`The ${modelById(model).label.split(" —")[0]} speech model isn't downloaded yet. Download it in Settings → Transcription.`);
    this.name = "ModelMissing";
  }
}

export async function transcribeMeetingAudio(uri: string, settings: AppSettings, deps: TranscriptionDeps,
  opts: { progress?: ProgressFn; signal?: AbortSignal } = {}): Promise<Transcript> {
  const path = await deps.modelPath(settings.whisperModel);
  if (!path) throw new ModelMissing(settings.whisperModel);
  opts.progress?.(0.01, "Loading the speech model…");
  const recognizer = await deps.recognizer(path);
  const totalMs = await deps.durationMs(uri).catch(() => 0);
  return transcribeAudio(deps.source(uri), recognizer, {
    language: settings.language, diarize: settings.diarize, totalMs, progress: opts.progress, signal: opts.signal,
  });
}
```

- [ ] **Step 5: Native adapters** — `android/src/native/whisper.ts`, `android/src/native/decode.ts`, `android/src/native/modelFiles.ts`

```ts
// whisper.ts
import { initWhisper, type WhisperContext } from "whisper.rn";
import type { Recognizer } from "../core/transcribeLong";

let cached: { path: string; ctx: WhisperContext } | null = null;

export async function whisperRecognizer(modelPath: string): Promise<Recognizer> {
  if (!cached || cached.path !== modelPath) {
    if (cached) await cached.ctx.release();
    cached = { path: modelPath, ctx: await initWhisper({ filePath: modelPath }) };
  }
  const ctx = cached.ctx;
  return {
    async transcribe(samples, language, signal) {
      const data = samples.slice().buffer as ArrayBuffer;
      const task = ctx.transcribeData(data, { language, maxLen: 0, tokenTimestamps: false });
      const onAbort = () => { void task.stop(); };
      signal?.addEventListener("abort", onAbort);
      try {
        const r = await task.promise;
        return { language: r.language, segments: r.segments };
      } finally { signal?.removeEventListener("abort", onAbort); }
    },
  };
}
```

```ts
// decode.ts
import { streamAudioData, extractAudioData } from "@siteed/audio-studio";
import type { AudioSource } from "../core/transcribeLong";

/** Any recorded or imported audio/video file as 16 kHz mono float32 chunks. */
export function fileSource(uri: string): AudioSource {
  return {
    async stream(onChunk) {
      const r = await streamAudioData({ fileUri: uri, targetSampleRate: 16000, channels: 1, chunkDurationMs: 30_000 },
        { onChunk: async (c) => { await onChunk(c.samples); } });
      return { durationMs: r.durationMs };
    },
  };
}

export async function fileDurationMs(uri: string): Promise<number> {
  const head = await extractAudioData({ fileUri: uri, startTimeMs: 0, endTimeMs: 10 });
  return head.durationMs;
}
```

```ts
// modelFiles.ts
import { Directory, File, Paths } from "expo-file-system";
import { checkModel, type ModelId, modelById, sha256Chunks, type WhisperModel, WHISPER_MODELS } from "../core/models";

const dir = () => { const d = new Directory(Paths.document, "models"); if (!d.exists) d.create({ intermediates: true }); return d; };
const fileFor = (m: WhisperModel) => new File(dir(), m.file);

export async function modelPath(id: ModelId): Promise<string | null> {
  const f = fileFor(modelById(id));
  return f.exists && f.size === modelById(id).sizeBytes ? f.uri : null;
}

export async function installedModels(): Promise<ModelId[]> {
  const out: ModelId[] = [];
  for (const m of WHISPER_MODELS) if (await modelPath(m.id)) out.push(m.id);
  return out;
}

async function digestOf(f: File): Promise<string> {
  const h = f.open();
  try {
    return await sha256Chunks(async (offset, length) => { h.offset = offset; return h.readBytes(length); }, f.size);
  } finally { h.close(); }
}

/** Download, verify size + SHA256, then move into place; a bad file is deleted. */
export async function downloadModel(m: WhisperModel, onProgress: (fraction: number) => void, signal?: AbortSignal): Promise<void> {
  const part = new File(dir(), m.file + ".part");
  if (part.exists) part.delete();
  const task = File.createDownloadTask(m.url, part, {
    onProgress: ({ bytesWritten, totalBytes }) => onProgress(totalBytes ? bytesWritten / totalBytes : 0),
  });
  const onAbort = () => { void task.pauseAsync(); };
  signal?.addEventListener("abort", onAbort);
  try { await task.downloadAsync(); } finally { signal?.removeEventListener("abort", onAbort); }
  if (signal?.aborted) { if (part.exists) part.delete(); throw new Error("Download cancelled."); }
  const verdict = checkModel(m, part.size, await digestOf(part));
  if (verdict !== "ok") {
    part.delete();
    throw new Error(verdict === "size" ? "The download was incomplete. Try again." : "The downloaded model failed its integrity check and was deleted.");
  }
  const target = fileFor(m);
  if (target.exists) target.delete();
  await part.move(target);
}

export function deleteModel(m: WhisperModel): void {
  const f = fileFor(m);
  if (f.exists) f.delete();
}
```

- [ ] **Step 6: Run tests and typecheck**

Run: `cd android && npx vitest run test/models.test.ts && npm run typecheck`
Expected: PASS; tsc 0. Correct any native API spelling that tsc rejects (e.g. `FileHandle.offset`, `createDownloadTask` options) from the installed type definitions, and ledger the ruling.

- [ ] **Step 7: Commit**

```bash
git add android/src/core/models.ts android/src/services/transcription.ts android/src/native/whisper.ts android/src/native/decode.ts android/src/native/modelFiles.ts android/test/models.test.ts
git commit -m "Android: Whisper models (pinned, verified) and the transcription service"
```

---

### Task 6: Exports and sharing

**Files:**
- Create: `android/src/core/fileNames.ts`, `android/src/services/exports.ts`, `android/test/exports.test.ts`, `android/src/native/exportFiles.ts`

**Interfaces:**
- Consumes: `renderMinutesHtml` (Plan 1 T11), `buildDocxBase64` (T12), `buildTxt`, `buildMd` (T12), `buildActionItemsCsv` (T8), `buildIcs`, `datedItems` (T9), `Meeting` (Task 2), `CompanyProfile`.
- Produces (`fileNames.ts`): `safeFilename(name, fallback?, maxLen?)`.
- Produces (`services/exports.ts`): `type ExportFormat = "pdf" | "docx" | "txt" | "md"`, `MIME`, `interface ExportDeps {pdfFromHtml(html): Promise<string>; writeText(name, text): Promise<string>; writeBase64(name, b64): Promise<string>; moveTo(uri, name): Promise<string>; logo(profile): Promise<{dataUri: string; base64: string; type: "png"|"jpg"|"gif"|"bmp"; widthPx: number; heightPx: number} | null>}`, `exportMinutes(meeting, profile, format, deps, design?): Promise<{uri, mime, name}>`, `exportActionItems(items, kind: "csv" | "ics", deps): Promise<{uri, mime, name, count}>`.
- Produces (`native/exportFiles.ts`): `nativeExportDeps`, `shareFile(uri, mime, title)`.

- [ ] **Step 1: Write the failing test** — `android/test/exports.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { safeFilename } from "../src/core/fileNames";
import { type ExportDeps, exportActionItems, exportMinutes } from "../src/services/exports";
import { DEFAULT_PROFILE } from "../src/core/profiles";
import type { Meeting } from "../src/data/meetings";

const meeting = { id: "m1", title: "Q4 / Budget: review?", minutes: "# Meeting Minutes\n**Meeting Title:** Budget\n## Notes\n- one" } as Meeting;

function fakeDeps() {
  const writes: Array<[string, string]> = [];
  const deps: ExportDeps = {
    pdfFromHtml: async (html) => { writes.push(["pdf-html", html]); return "file:///cache/print.pdf"; },
    writeText: async (name, text) => { writes.push([name, text]); return "file:///exports/" + name; },
    writeBase64: async (name, b64) => { writes.push([name, b64]); return "file:///exports/" + name; },
    moveTo: async (_uri, name) => "file:///exports/" + name,
    logo: async () => null,
  };
  return { deps, writes };
}

describe("file names", () => {
  it("are safe on every platform", () => {
    expect(safeFilename("Q4 / Budget: review?")).toBe("Q4 Budget review");
    expect(safeFilename("")).toBe("Meeting-Minutes");
    expect(safeFilename("CON")).toBe("_CON");
    expect(safeFilename("محضر الاجتماع")).toBe("محضر الاجتماع");
    expect(safeFilename("a".repeat(150)).length).toBe(100);
    expect(safeFilename("trailing dots... ")).toBe("trailing dots");
  });
});

describe("minutes exports", () => {
  it("PDF goes through the branded HTML in the company's design and is named after the meeting", async () => {
    const { deps, writes } = fakeDeps();
    const r = await exportMinutes(meeting, { ...DEFAULT_PROFILE, id: "p", name: "Acme", pdf_design: "formal" }, "pdf", deps);
    expect(r).toEqual({ uri: "file:///exports/Q4 Budget review.pdf", mime: "application/pdf", name: "Q4 Budget review.pdf" });
    expect(writes[0][1]).toContain('class="design-formal"');
  });
  it("Word, text and Markdown", async () => {
    const { deps, writes } = fakeDeps();
    expect((await exportMinutes(meeting, null, "docx", deps)).mime).toBe("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    expect((await exportMinutes(meeting, null, "txt", deps)).name).toBe("Q4 Budget review.txt");
    expect((await exportMinutes(meeting, null, "md", deps)).mime).toBe("text/markdown");
    expect(writes.find(([n]) => n.endsWith(".txt"))![1]).not.toContain("**");
  });
  it("empty minutes are refused with a clear message", async () => {
    await expect(exportMinutes({ ...meeting, minutes: "  " }, null, "pdf", fakeDeps().deps)).rejects.toThrow(/no minutes/i);
  });
});

describe("action item exports", () => {
  it("CSV and calendar", async () => {
    const { deps } = fakeDeps();
    const items = [{ task: "Send", owner: "Ali", deadline: "2026-10-20", status: "Pending", priority: "", notes: "", meetingId: "m1",
      meetingTitle: "Budget", meetingDate: "2026-10-05", okey: "m1:abc:0", occ: 0 }];
    expect(await exportActionItems(items, "csv", deps)).toMatchObject({ name: "Action-Items.csv", mime: "text/csv", count: 1 });
    expect(await exportActionItems(items, "ics", deps)).toMatchObject({ name: "Action-Items.ics", mime: "text/calendar", count: 1 });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/exports.test.ts`
Expected: FAIL — modules missing.

- [ ] **Step 3: `android/src/core/fileNames.ts`**

```ts
const RESERVED = new Set(["CON", "PRN", "AUX", "NUL", ...Array.from({ length: 9 }, (_x, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_x, i) => `LPT${i + 1}`)]);

/** A file name (no extension) safe on Android, Windows and in e-mail clients. */
export function safeFilename(name: string, fallback = "Meeting-Minutes", maxLen = 100): string {
  let s = (name ?? "").replace(/[<>:"/\\|?*\x00-\x1f]/g, " ");
  s = s.replace(/\s+/g, " ").trim().replace(/[. ]+$/, "");
  s = s.slice(0, maxLen).replace(/[. ]+$/, "");
  if (RESERVED.has(s.split(".")[0].toUpperCase())) s = "_" + s;
  return s || fallback;
}
```

- [ ] **Step 4: `android/src/services/exports.ts`**

```ts
import { type ActionItem, buildActionItemsCsv } from "../core/actionItems";
import { buildDocxBase64 } from "../core/exportDocx";
import { renderMinutesHtml } from "../core/exportHtml";
import { buildMd, buildTxt } from "../core/exportText";
import { safeFilename } from "../core/fileNames";
import { buildIcs, datedItems } from "../core/ics";
import type { CompanyProfile } from "../core/profiles";
import type { Meeting } from "../data/meetings";

export type ExportFormat = "pdf" | "docx" | "txt" | "md";
export const MIME: Record<ExportFormat | "csv" | "ics", string> = {
  pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  txt: "text/plain", md: "text/markdown", csv: "text/csv", ics: "text/calendar",
};

export interface LogoData { dataUri: string; base64: string; type: "png" | "jpg" | "gif" | "bmp"; widthPx: number; heightPx: number; }
export interface ExportDeps {
  pdfFromHtml(html: string): Promise<string>;
  writeText(name: string, text: string): Promise<string>;
  writeBase64(name: string, base64: string): Promise<string>;
  moveTo(uri: string, name: string): Promise<string>;
  logo(profile: CompanyProfile): Promise<LogoData | null>;
}

export async function exportMinutes(meeting: Meeting, profile: CompanyProfile | null, format: ExportFormat,
  deps: ExportDeps, design?: string | null): Promise<{ uri: string; mime: string; name: string }> {
  if (!meeting.minutes.trim()) throw new Error("This meeting has no minutes yet — generate or write them first.");
  const name = `${safeFilename(meeting.title)}.${format}`;
  const logo = profile ? await deps.logo(profile) : null;
  let uri: string;
  if (format === "pdf") {
    const html = renderMinutesHtml(meeting.minutes, { profile, design, logoDataUri: logo?.dataUri });
    uri = await deps.moveTo(await deps.pdfFromHtml(html), name);
  } else if (format === "docx") {
    uri = await deps.writeBase64(name, await buildDocxBase64(meeting.minutes, profile, logo));
  } else {
    uri = await deps.writeText(name, format === "txt" ? buildTxt(meeting.minutes, profile) : buildMd(meeting.minutes, profile));
  }
  return { uri, mime: MIME[format], name };
}

export async function exportActionItems(items: ActionItem[], kind: "csv" | "ics", deps: ExportDeps):
  Promise<{ uri: string; mime: string; name: string; count: number }> {
  const name = `Action-Items.${kind}`;
  if (kind === "csv") return { uri: await deps.writeText(name, buildActionItemsCsv(items)), mime: MIME.csv, name, count: items.length };
  const dated = datedItems(items);
  if (!dated.length) throw new Error("None of these action items has a date the calendar can use.");
  return { uri: await deps.writeText(name, buildIcs(dated)), mime: MIME.ics, name, count: dated.length };
}
```

- [ ] **Step 5: `android/src/native/exportFiles.ts`**

```ts
import { Directory, File, Paths } from "expo-file-system";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Image } from "react-native";
import type { CompanyProfile } from "../core/profiles";
import type { ExportDeps, LogoData } from "../services/exports";

const exportsDir = () => { const d = new Directory(Paths.cache, "exports"); if (!d.exists) d.create({ intermediates: true }); return d; };
const fresh = (name: string) => { const f = new File(exportsDir(), name); if (f.exists) f.delete(); return f; };

function sizeOf(uri: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => Image.getSize(uri, (width, height) => resolve({ width, height }), () => resolve({ width: 0, height: 0 })));
}

export async function logoData(profile: CompanyProfile): Promise<LogoData | null> {
  if (!profile.logo_path) return null;
  const f = new File(profile.logo_path);
  if (!f.exists) return null;
  const ext = f.uri.split(".").pop()!.toLowerCase();
  const type = ext === "jpeg" || ext === "jpg" ? "jpg" : ext === "gif" ? "gif" : ext === "bmp" ? "bmp" : "png";
  const base64 = await f.base64();
  const { width, height } = await sizeOf(f.uri);
  return { dataUri: `data:image/${type === "jpg" ? "jpeg" : type};base64,${base64}`, base64, type, widthPx: width, heightPx: height };
}

export const nativeExportDeps: ExportDeps = {
  async pdfFromHtml(html) { return (await Print.printToFileAsync({ html, width: 595, height: 842 })).uri; },
  async writeText(name, text) { const f = fresh(name); f.write(text); return f.uri; },
  async writeBase64(name, b64) { const f = fresh(name); f.write(b64, { encoding: "base64" }); return f.uri; },
  async moveTo(uri, name) { const target = fresh(name); await new File(uri).move(target); return target.uri; },
  logo: logoData,
};

export async function shareFile(uri: string, mimeType: string, dialogTitle: string): Promise<void> {
  if (!(await Sharing.isAvailableAsync())) throw new Error("Sharing isn't available on this device.");
  await Sharing.shareAsync(uri, { mimeType, dialogTitle });
}
```

- [ ] **Step 6: Run tests and typecheck**

Run: `cd android && npx vitest run test/exports.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add android/src/core/fileNames.ts android/src/services/exports.ts android/src/native/exportFiles.ts android/test/exports.test.ts
git commit -m "Android: exports (PDF/Word/text/Markdown/CSV/calendar) and sharing"
```

---

### Task 7: MICO360 Cloud generation service

**Files:**
- Create: `android/src/services/generation.ts`, `android/test/generation.test.ts`, `android/src/native/cloud.ts`

**Interfaces:**
- Consumes: `CloudClient`, `CloudError`, `defaultEndpoint` (Plan 1 T7); `revealKey` (Task 1); `Meeting`, `AppSettings`.
- Produces (`generation.ts`): `class ConsentRequired extends Error`, `meetingPreamble({title, meetingDate, attendees}): string`, `interface MinutesClient {model; warnings; isEncrypted(); generateMinutes(transcript, template, opts)}`, `generateMinutesFor(meeting, template, style, client, settings, opts): Promise<{minutes, warnings, model}>`.
- Produces (`native/cloud.ts`): `cloudKey(): string`, `makeCloudClient(settings): CloudClient`, `probeCloud(): Promise<void>`.

- [ ] **Step 1: Write the failing test** — `android/test/generation.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { ConsentRequired, generateMinutesFor, meetingPreamble, type MinutesClient } from "../src/services/generation";
import { DEFAULT_SETTINGS } from "../src/data/settings";
import type { Meeting } from "../src/data/meetings";

const m = { id: "m", title: "Budget", meetingDate: "5 Oct 2026", attendees: "Ali, Sara, ", transcript: "We agreed." } as Meeting;

function client(encrypted: boolean, seen: Array<[string, string]>): MinutesClient {
  return { model: "llama3.1:latest", warnings: ["processed on qwen instead"], isEncrypted: () => encrypted,
    async generateMinutes(t, tpl) { seen.push([t, tpl]); return "# Minutes"; } };
}

describe("generation", () => {
  it("known meeting details guide the model (desktop format_preamble)", () => {
    expect(meetingPreamble(m)).toBe("Known meeting details (use these exactly in the minutes header):\n" +
      "Meeting Title: Budget\nDate and Time: 5 Oct 2026\nAttendees: Ali, Sara\n\n");
    expect(meetingPreamble({ title: "", meetingDate: "", attendees: "" })).toBe("");
  });
  it("plain HTTP needs the user's consent first", async () => {
    await expect(generateMinutesFor(m, "T", "Formal Minutes", client(false, []), DEFAULT_SETTINGS)).rejects.toBeInstanceOf(ConsentRequired);
  });
  it("sends preamble + transcript and returns the minutes, warnings and model", async () => {
    const seen: Array<[string, string]> = [];
    const r = await generateMinutesFor(m, "T", "Formal Minutes", client(false, seen), { ...DEFAULT_SETTINGS, plainHttpConsent: true });
    expect(seen[0][0]).toMatch(/^Known meeting details[\s\S]*\n\nWe agreed\.$/);
    expect(r).toEqual({ minutes: "# Minutes", warnings: ["processed on qwen instead"], model: "llama3.1:latest" });
  });
  it("an empty transcript is refused before any request", async () => {
    await expect(generateMinutesFor({ ...m, transcript: " " }, "T", "x", client(true, []), DEFAULT_SETTINGS)).rejects.toThrow(/transcript/i);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/generation.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: `android/src/services/generation.ts`**

```ts
import type { GenerateOptions } from "../core/cloudClient";
import type { Meeting } from "../data/meetings";
import type { AppSettings } from "../data/settings";

export class ConsentRequired extends Error {
  constructor() {
    super("MICO360 Cloud is reached over an unencrypted connection. Your transcript would be sent unencrypted.");
    this.name = "ConsentRequired";
  }
}

export interface MinutesClient {
  model: string;
  warnings: string[];
  isEncrypted(): boolean;
  generateMinutes(transcript: string, template: string, o?: GenerateOptions): Promise<string>;
}

/** Desktop calendar_import.format_preamble: only real values. */
export function meetingPreamble(m: Pick<Meeting, "title" | "meetingDate" | "attendees">): string {
  const parts: string[] = [];
  if (m.title.trim()) parts.push(`Meeting Title: ${m.title.trim()}`);
  if (m.meetingDate.trim()) parts.push(`Date and Time: ${m.meetingDate.trim()}`);
  const att = m.attendees.split(",").map((a) => a.trim()).filter(Boolean);
  if (att.length) parts.push("Attendees: " + att.join(", "));
  if (!parts.length) return "";
  return "Known meeting details (use these exactly in the minutes header):\n" + parts.join("\n") + "\n\n";
}

export async function generateMinutesFor(meeting: Meeting, template: string, style: string, client: MinutesClient,
  settings: AppSettings, opts: Pick<GenerateOptions, "progress" | "signal"> = {}): Promise<{ minutes: string; warnings: string[]; model: string }> {
  if (!meeting.transcript.trim()) throw new Error("This meeting has no transcript yet — record, import or paste one first.");
  if (!client.isEncrypted() && !settings.plainHttpConsent) throw new ConsentRequired();
  const minutes = await client.generateMinutes(meetingPreamble(meeting) + meeting.transcript, template, {
    style, removeFillers: settings.removeFillers, chunkChars: settings.chunkChars, progress: opts.progress, signal: opts.signal,
  });
  return { minutes, warnings: [...client.warnings], model: client.model };
}
```

- [ ] **Step 4: `android/src/native/cloud.ts`**

```ts
import Constants from "expo-constants";
import { CloudClient, defaultEndpoint } from "../core/cloudClient";
import { type KeyBox, revealKey } from "../core/keyBox";
import type { AppSettings } from "../data/settings";

const extra = () => (Constants.expoConfig?.extra ?? {}) as { connect?: KeyBox | null; e2eConnectUrl?: string };

export function cloudKey(): string {
  return revealKey(extra().connect ?? null);
}

export function cloudEnabled(): boolean {
  return cloudKey().length > 0;
}

export function makeCloudClient(settings: AppSettings): CloudClient {
  return new CloudClient({ key: cloudKey(), model: settings.cloudModel || undefined, baseUrl: extra().e2eConnectUrl || undefined,
    userAgent: `MICO360 Meetings Android/${Constants.expoConfig?.version ?? ""}` });
}

/** Switch to HTTPS as soon as the server offers it (spec §5.1, guide §9). */
export async function probeCloud(): Promise<void> {
  if (!extra().e2eConnectUrl) await defaultEndpoint.probeHttps();
}
```

- [ ] **Step 5: Redirect behaviour of the phone's fetch (Plan 1 review, finding 5)**

React Native's `fetch` follows redirects itself, so `followRedirect` in `cloudClient.ts` only sees the final response. Record what actually happens: in Plan 3's E2E fake Connect server, add a route that answers `307 Location: http://10.0.2.2:<other-port>/v1/chat/completions` and a second listener on the other port that logs whether an `Authorization` header arrived. Add the observed result to the spec's §5.1 table (row "Redirects") and a ledger ruling. If the header arrives, switch `makeCloudClient` to a transport built on `expo/fetch` with `redirect: "manual"` when that option is honoured, otherwise document the residual limitation in §5.1.

- [ ] **Step 6: Run tests and typecheck**

Run: `cd android && npx vitest run test/generation.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add android/src/services/generation.ts android/src/native/cloud.ts android/test/generation.test.ts
git commit -m "Android: Cloud minutes generation with consent and meeting details"
```

---

### Task 8: Template and profile import/export

**Files:**
- Create: `android/src/services/library.ts`, `android/test/libraryService.test.ts`, `android/src/native/libraryFiles.ts`

**Interfaces:**
- Consumes: `parseProfileFile`, `contentKey`, `uniqueProfileName`, `copyProfileName`, `buildProfilesJson`, `CompanyProfile` (Plan 1 T10); `parsePromptFile`, `planPromptImport`, `buildPromptExport` (T5); `Db`, `listProfiles`, `saveProfile`, `getProfile`, `listPrompts`, `applyPromptImport` (Task 2).
- Produces: `interface LogoStore {save(base64, ext): Promise<string>; read(uri): Promise<string | null>; copy(uri): Promise<string>; remove(uri): Promise<void>}`, `importProfiles(db, fileName, text, logos): Promise<ImportSummary>`, `exportProfiles(db, ids, logos): Promise<string>`, `duplicateProfile(db, id, logos): Promise<CompanyProfile | null>`, `importPrompts(db, fileName, text): Promise<ImportSummary>`, `exportPrompts(db, ids): Promise<string>`, `interface ImportSummary {imported, skipped, renamed, invalid, missingLogos, message}`.

- [ ] **Step 1: Write the failing test** — `android/test/libraryService.test.ts`

```ts
import { beforeEach, describe, expect, it } from "vitest";
import { nodeDb } from "./nodeDb";
import type { Db } from "../src/data/db";
import { migrate, seed } from "../src/data/schema";
import { listProfiles, listPrompts } from "../src/data/library";
import * as S from "../src/services/library";

let db: Db;
let files: Map<string, string>;
const logos: S.LogoStore = {
  async save(b64, ext) { const uri = `file:///logos/${files.size}.${ext}`; files.set(uri, b64); return uri; },
  async read(uri) { return files.get(uri) ?? null; },
  async copy(uri) { const uri2 = `file:///logos/${files.size}.png`; files.set(uri2, files.get(uri) ?? ""); return uri2; },
  async remove(uri) { files.delete(uri); },
};
beforeEach(async () => { db = nodeDb(); await migrate(db); await seed(db); files = new Map(); });

const DESKTOP_EXPORT = JSON.stringify([{ id: "x1", name: "Acme", phone: 123, pdf_design: "Modern", logo_path: "C:/a/logo.png",
  logo_name: "logo.png", logo_data: "iVBORw0KGgo=" }]);

describe("profiles", () => {
  it("imports a desktop export with its logo; importing it again changes nothing", async () => {
    const first = await S.importProfiles(db, "profiles.json", DESKTOP_EXPORT, logos);
    expect(first.imported).toBe(1);
    const [p] = await listProfiles(db);
    expect(p).toMatchObject({ name: "Acme", phone: "123", pdf_design: "modern" });
    expect(files.get(p.logo_path)).toBe("iVBORw0KGgo=");
    const again = await S.importProfiles(db, "profiles.json", DESKTOP_EXPORT, logos);
    expect(again).toMatchObject({ imported: 0, skipped: 1 });
    expect(await listProfiles(db)).toHaveLength(1);
    expect(files.size).toBe(1);
  });
  it("a different profile with a taken name is renamed, never overwritten", async () => {
    await S.importProfiles(db, "a.json", DESKTOP_EXPORT, logos);
    const r = await S.importProfiles(db, "b.json", JSON.stringify([{ name: "Acme", phone: "999" }]), logos);
    expect(r.renamed).toEqual([["Acme", "Acme (2)"]]);
  });
  it("export embeds logos; duplicate copies the logo file", async () => {
    await S.importProfiles(db, "a.json", DESKTOP_EXPORT, logos);
    const [p] = await listProfiles(db);
    const out = JSON.parse(await S.exportProfiles(db, [p.id], logos));
    expect(out[0]).toMatchObject({ name: "Acme", logo_data: "iVBORw0KGgo=" });
    const copy = (await S.duplicateProfile(db, p.id, logos))!;
    expect(copy.name).toBe("Acme (copy)");
    expect(copy.logo_path).not.toBe(p.logo_path);
  });
});

describe("prompts", () => {
  it("round-trip through the desktop format", async () => {
    const r = await S.importPrompts(db, "p.json", JSON.stringify({ mico360_prompts: 1, prompts: [{ name: "Weekly", category: "Summary", text: "Do [TRANSCRIPT_HERE]" }] }));
    expect(r.message).toMatch(/^Imported 1 prompt/);
    const weekly = (await listPrompts(db)).find((p) => p.name === "Weekly")!;
    expect(JSON.parse(await S.exportPrompts(db, [weekly.id])).prompts[0].text).toBe("Do [TRANSCRIPT_HERE]");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/libraryService.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: `android/src/services/library.ts`**

```ts
import { sha1 } from "@noble/hashes/legacy.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";
import { buildProfilesJson, type CompanyProfile, contentKey, copyProfileName, parseProfileFile, uniqueProfileName } from "../core/profiles";
import { buildPromptExport, parsePromptFile, planPromptImport } from "../core/prompts";
import type { Db } from "../data/db";
import { applyPromptImport, getProfile, listProfiles, listPrompts, saveProfile } from "../data/library";

export interface LogoStore {
  save(base64: string, ext: string): Promise<string>;
  read(uri: string): Promise<string | null>;
  copy(uri: string): Promise<string>;
  remove(uri: string): Promise<void>;
}

export interface ImportSummary {
  imported: number; skipped: number; renamed: Array<[string, string]>; invalid: number; missingLogos: string[]; message: string;
}

const digest = (b64: string | null) => (b64 ? bytesToHex(sha1(utf8ToBytes(b64))) : "");
const LOGO_EXTS = new Set(["png", "jpg", "jpeg", "gif", "bmp", "webp"]);
const BASE64_RE = /^[A-Za-z0-9+/]+={0,2}$/;

function summary(kind: string, s: Omit<ImportSummary, "message">): ImportSummary {
  const parts = [`Imported ${s.imported} ${kind}(s)`];
  if (s.skipped) parts.push(`skipped ${s.skipped} already here`);
  if (s.renamed.length) parts.push("renamed " + s.renamed.slice(0, 3).map(([a, b]) => `"${a}" to "${b}"`).join(", "));
  if (s.missingLogos.length) parts.push(`logo not found for ${s.missingLogos.slice(0, 3).join(", ")}`);
  if (s.invalid) parts.push(`${s.invalid} unreadable entr${s.invalid === 1 ? "y" : "ies"} ignored`);
  return { ...s, message: parts.join("; ") + "." };
}

export async function importProfiles(db: Db, fileName: string, text: string, logos: LogoStore): Promise<ImportSummary> {
  const { records, invalid } = parseProfileFile(fileName, text);
  const existing = await listProfiles(db);
  const keys = new Set(await Promise.all(existing.map(async (p) => contentKey(p, digest(p.logo_path ? await logos.read(p.logo_path) : null)))));
  const names = existing.map((p) => p.name);
  const out = { imported: 0, skipped: 0, renamed: [] as Array<[string, string]>, invalid, missingLogos: [] as string[] };
  for (const r of records) {
    const data = r.logoData && BASE64_RE.test(r.logoData.replace(/\s+/g, "")) ? r.logoData.replace(/\s+/g, "") : "";
    const key = contentKey(r.profile, digest(data || null));
    if (keys.has(key)) { out.skipped += 1; continue; }
    keys.add(key);
    const name = uniqueProfileName(r.profile.name, names);
    if (name !== r.profile.name) out.renamed.push([r.profile.name, name]);
    let logoPath = "";
    if (data) {
      const ext = (r.logoName.split(".").pop() ?? "png").toLowerCase();
      logoPath = await logos.save(data, LOGO_EXTS.has(ext) ? ext : "png");
    } else if (r.logoPath) {
      out.missingLogos.push(name);
    }
    await saveProfile(db, { ...r.profile, id: "", name, logo_path: logoPath });
    names.push(name);
    out.imported += 1;
  }
  return summary("profile", out);
}

export async function exportProfiles(db: Db, ids: string[], logos: LogoStore): Promise<string> {
  const chosen = (await listProfiles(db)).filter((p) => !ids.length || ids.includes(p.id));
  const files: Record<string, { name: string; base64: string }> = {};
  for (const p of chosen) {
    const b64 = p.logo_path ? await logos.read(p.logo_path) : null;
    if (b64) files[p.id] = { name: p.logo_path.split("/").pop() ?? "logo.png", base64: b64 };
  }
  return buildProfilesJson(chosen, files);
}

export async function duplicateProfile(db: Db, id: string, logos: LogoStore): Promise<CompanyProfile | null> {
  const src = await getProfile(db, id);
  if (!src) return null;
  const name = copyProfileName(src.name, (await listProfiles(db)).map((p) => p.name));
  const logo = src.logo_path ? await logos.copy(src.logo_path) : "";
  return saveProfile(db, { ...src, id: "", name, logo_path: logo });
}

export async function importPrompts(db: Db, fileName: string, text: string): Promise<ImportSummary> {
  const { records } = parsePromptFile(fileName, text);
  const plan = planPromptImport(await listPrompts(db), records);
  await applyPromptImport(db, plan);
  return summary("prompt", { imported: plan.toAdd.length, skipped: plan.skipped.length, renamed: plan.renamed, invalid: plan.invalid, missingLogos: [] });
}

export async function exportPrompts(db: Db, ids: string[]): Promise<string> {
  return buildPromptExport((await listPrompts(db)).filter((p) => !ids.length || ids.includes(p.id)));
}
```

- [ ] **Step 4: `android/src/native/libraryFiles.ts`**

```ts
import * as DocumentPicker from "expo-document-picker";
import { Directory, File, Paths } from "expo-file-system";
import type { LogoStore } from "../services/library";

const logosDir = () => { const d = new Directory(Paths.document, "logos"); if (!d.exists) d.create({ intermediates: true }); return d; };
const uid = () => `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`;

export const logoStore: LogoStore = {
  async save(base64, ext) { const f = new File(logosDir(), `${uid()}.${ext}`); f.write(base64, { encoding: "base64" }); return f.uri; },
  async read(uri) { const f = new File(uri); return f.exists ? f.base64() : null; },
  async copy(uri) { const src = new File(uri); const f = new File(logosDir(), `${uid()}.${uri.split(".").pop() ?? "png"}`); await src.copy(f); return f.uri; },
  async remove(uri) { const f = new File(uri); if (f.exists) f.delete(); },
};

/** Let the user pick a file; returns its name and text, or null when cancelled. */
export async function pickTextFile(types: string[]): Promise<{ name: string; text: string } | null> {
  const r = await DocumentPicker.getDocumentAsync({ type: types, copyToCacheDirectory: true, multiple: false });
  if (r.canceled || !r.assets?.length) return null;
  const a = r.assets[0];
  return { name: a.name, text: await new File(a.uri).text() };
}
```

- [ ] **Step 5: Run tests and typecheck**

Run: `cd android && npx vitest run test/libraryService.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add android/src/services/library.ts android/src/native/libraryFiles.ts android/test/libraryService.test.ts
git commit -m "Android: desktop-compatible template and profile import/export"
```

---

### Task 9: Reminders

**Files:**
- Create: `android/src/core/reminders.ts`, `android/test/reminders.test.ts`, `android/src/native/notify.ts`

**Interfaces:**
- Consumes: `ActionItem`, `deadlineDate`, `normalizeStatus` (Plan 1 T8).
- Produces: `interface ReminderPlan {id: string; at: Date; title: string; body: string}`, `REMINDER_HOUR = 9`, `MAX_REMINDERS = 50`, `planReminders(items, now): ReminderPlan[]`; native `scheduleReminders(plans): Promise<number>`, `ensureNotificationPermission(): Promise<boolean>`.

- [ ] **Step 1: Write the failing test** — `android/test/reminders.test.ts`

```ts
import { expect, it } from "vitest";
import { MAX_REMINDERS, planReminders } from "../src/core/reminders";
import type { ActionItem } from "../src/core/actionItems";

const item = (task: string, deadline: string, status = "Pending", okey = task): ActionItem => ({ task, owner: "Ali", deadline, status,
  priority: "", notes: "", meetingId: "m", meetingTitle: "Budget", meetingDate: "2026-10-05", okey, occ: 0 });

it("reminds the day before and on the day at 09:00, only for open items in the future", () => {
  const now = new Date(2026, 9, 14, 10, 0);
  const plans = planReminders([item("Send report", "2026-10-16"), item("Done one", "2026-10-16", "Completed"),
    item("Past", "2026-10-01"), item("Tomorrow only", "2026-10-15")], now);
  // 10:00 on the 14th: "Tomorrow only"'s day-before reminder (09:00 on the 14th) is already past.
  expect(plans.map((p) => [p.title, p.at.getDate(), p.at.getHours()])).toEqual([
    ["Due tomorrow: Send report", 15, 9], ["Due today: Tomorrow only", 15, 9], ["Due today: Send report", 16, 9]]);
  expect(plans.every((p) => p.at > now)).toBe(true);
  expect(plans.find((p) => p.title.includes("Done one"))).toBeUndefined();
  expect(plans[0].body).toBe("Ali · Budget");
});

it("never schedules more than the limit, nearest first", () => {
  const now = new Date(2026, 0, 1, 8, 0);
  const many = Array.from({ length: 80 }, (_x, i) => item(`T${i}`, `2026-03-${String((i % 28) + 1).padStart(2, "0")}`, "Pending", `k${i}`));
  const plans = planReminders(many, now);
  expect(plans).toHaveLength(MAX_REMINDERS);
  expect(plans.map((p) => p.at.getTime())).toEqual([...plans.map((p) => p.at.getTime())].sort((a, b) => a - b));
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/reminders.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: `android/src/core/reminders.ts`**

```ts
import { type ActionItem, deadlineDate, normalizeStatus } from "./actionItems";

export interface ReminderPlan { id: string; at: Date; title: string; body: string; }
export const REMINDER_HOUR = 9;
export const MAX_REMINDERS = 50;

function at(iso: string, dayOffset: number): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d + dayOffset, REMINDER_HOUR, 0, 0, 0);
}

/** Local notifications for open action items: the day before and the day itself, 09:00. */
export function planReminders(items: ActionItem[], now: Date): ReminderPlan[] {
  const out: ReminderPlan[] = [];
  for (const it of items) {
    if (["Completed", "Cancelled"].includes(normalizeStatus(it.status))) continue;
    const d = deadlineDate(it);
    if (!d) continue;
    const body = [it.owner, it.meetingTitle].filter((x) => x && x.trim()).join(" · ");
    for (const [offset, label] of [[-1, "Due tomorrow"], [0, "Due today"]] as const) {
      const when = at(d, offset);
      if (when > now) out.push({ id: `${it.okey}:${offset}`, at: when, title: `${label}: ${it.task}`, body });
    }
  }
  return out.sort((a, b) => a.at.getTime() - b.at.getTime()).slice(0, MAX_REMINDERS);
}
```

- [ ] **Step 4: `android/src/native/notify.ts`**

```ts
import * as Notifications from "expo-notifications";
import type { ReminderPlan } from "../core/reminders";

const CHANNEL = "reminders";

export async function ensureNotificationPermission(): Promise<boolean> {
  await Notifications.setNotificationChannelAsync(CHANNEL, { name: "Action item reminders", importance: Notifications.AndroidImportance.DEFAULT });
  const cur = await Notifications.getPermissionsAsync();
  if (cur.granted) return true;
  return (await Notifications.requestPermissionsAsync()).granted;
}

/** Replace every scheduled reminder with `plans`; returns how many were scheduled. */
export async function scheduleReminders(plans: ReminderPlan[]): Promise<number> {
  await Notifications.cancelAllScheduledNotificationsAsync();
  for (const p of plans) {
    await Notifications.scheduleNotificationAsync({
      identifier: p.id,
      content: { title: p.title, body: p.body },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: p.at, channelId: CHANNEL },
    });
  }
  return plans.length;
}
```

- [ ] **Step 5: Run all tests and typecheck**

Run: `cd android && npm test && npm run typecheck`
Expected: every suite PASS; tsc 0.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/reminders.ts android/src/native/notify.ts android/test/reminders.test.ts
git commit -m "Android: action item reminders"
```

---

## Self-review

- **Spec coverage:** §3.2 wrappers — recorder (T4), whisper (T5), models (T5), media/decode (T5), print/share (T6), notify (T9), install (Plan 4); §3.3 storage (T2); §5 key injection, cleartext, consent (T1, T7); §6 errors: mic silence (T4), killed app (T4), model checksum (T5), cloud consent (T7), long meetings (T3), import clashes (T8); exports (T6). Screens: Plan 3. Release: Plan 4.
- **Placeholders:** none.
- **Type consistency:** `Meeting`/`TranscriptSegment` (T2) are used by T6/T7; `Transcript.segments` from T3 matches `TranscriptSegment` shape; `AppSettings` (T2) is used by T5/T7; `LogoData` (T6) matches `DocxLogo`.
