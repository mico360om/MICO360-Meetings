# MICO360 Meetings for Android — Plan 1: Foundation and Core Logic

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Expo (SDK 57) project in `android/` whose pure-TypeScript core reproduces the desktop app's logic — Markdown parsing, transcript cleaning and chunking, prompts, the minutes pipeline, the MICO360 Connect client, action items, calendar export, company profiles, branded HTML/Word/text exports, speaker identification, insights and update selection — proven by unit tests and by parity tests against output from the desktop Python code.

**Architecture:** `android/src/core/` is plain TypeScript with no React Native or Expo imports, so all of it runs under vitest in Node and on the phone unchanged. Text data shared with the desktop (prompts, styles, meeting types, PDF designs, Connect endpoints) is exported from the Python modules into `src/core/data/desktop.json` by a script, never copied by hand. Parity fixtures are produced by a second script that runs the desktop functions on a fixed input set.

**Tech Stack:** Node 24, Expo SDK 57 (blank-typescript template), TypeScript strict, vitest, `@noble/hashes` 2.x (md5/sha1/sha256), `docx` 9.x, `jszip` (tests only). Desktop side: the repo's Python 3.14 (`C:/Users/Khurram/AppData/Local/Python/pythoncore-3.14-64/python.exe`).

**Spec:** `docs/superpowers/specs/2026-10-15-android-app-design.md`

**Plans in this series:** 1 (this) foundation + core logic · 2 storage, device features and screens · 3 release, signing and in-app updates. Each ends in working, tested software.

## Global Constraints

- App id `com.mico360.meetings`, name "MICO360 Meetings", version `1.0.0`, `versionCode` 1, Android 8.0+ (API 26).
- Stack: Expo SDK 57 + React Native + TypeScript (strict); tests with vitest; no Expo cloud services.
- `src/core/**` must not import `react-native`, `expo*` or any native module (enforced by a test).
- Desktop text data comes only from `src/core/data/desktop.json` (regenerate with `tools/export_desktop_data.py`).
- Dates are ISO strings `YYYY-MM-DD`; timestamps are milliseconds since the epoch.
- String lengths are UTF-16 code units (JavaScript `length`) — the Connect server counts the same way.
- No `\p{…}` in regular expressions (Hermes support is uncertain); use the generated ranges in `src/core/unicode.ts`. Lookbehind is allowed.
- Every network call goes through an injectable `HttpTransport`; the Connect client follows spec §5.1 exactly.
- String replacement of user text never uses `String.replace` with a string replacement (it interprets `$&`, `$1`); use `split/join` or a function replacement.
- Profile files: JSON and CSV import on the phone; an `.xlsx` profile file is refused with "Export the profiles as JSON from the desktop app." (Clarifies spec §1 "imports unchanged": JSON is the desktop's portable format with embedded logos.)
- The real user data folder of the desktop app is never touched: the Python tools set `LOCALAPPDATA` to a temp folder before importing `mico360`.

## Review Focus

1. A transcript or template containing `$&`, `$1`, `{idx}` or `[TRANSCRIPT_HERE]` text must come out literally in prompts (JS replace pitfalls) — tested in Task 5.
2. Emoji, astral characters and lone surrogates in minutes must not crash parsing or exports, and emoji must survive — tested in Task 3.
3. Arabic everywhere: parsing, sentence boundaries (؟ ۔), RTL direction in HTML and Word — tested in Tasks 3, 4, 11, 12.
4. A two-hour transcript (~150,000 characters) with a long template must produce only requests of ≤ 32,000 characters — tested in Task 7.
5. Hostile imported profile values (`accent_color` with markup, a non-image `logo_data`, a phone stored as a number) must be neutralised — tested in Tasks 10 and 11.

---

### Task 1: Expo project, test runner and CI

**Files:**
- Create: `android/` (from the Expo template), `android/app.config.ts`, `android/vitest.config.ts`, `android/test/appConfig.test.ts`, `.github/workflows/android-ci.yml`
- Modify: `android/package.json` (scripts), `android/tsconfig.json`
- Delete: `android/app.json`

**Interfaces:**
- Produces: `npm test` (vitest), `npm run typecheck` (tsc); `app.config.ts` default export `config(): ExpoConfig`, named `APP_VERSION`, `VERSION_CODE`.

- [ ] **Step 1: Create the project**

```bash
cd "C:/Users/Khurram/Dropbox/0 AI/Claude/MICO360-Softwares/MICO360-Meeting"
npx create-expo-app@latest android --template blank-typescript --no-install
cd android && npm install && npm install @noble/hashes@^2.4.0 docx@^9.11.0 && npm install -D vitest jszip
```

- [ ] **Step 2: Write the failing test** — `android/test/appConfig.test.ts`

```ts
import { describe, expect, it } from "vitest";
import config, { APP_VERSION, VERSION_CODE } from "../app.config";

describe("app config", () => {
  it("identifies the Android app", () => {
    const c = config();
    expect(c.name).toBe("MICO360 Meetings");
    expect(c.android?.package).toBe("com.mico360.meetings");
    expect(c.version).toBe(APP_VERSION);
    expect(APP_VERSION).toBe("1.0.0");
    expect(c.android?.versionCode).toBe(VERSION_CODE);
  });
});
```

- [ ] **Step 3: Add the test runner** — `android/vitest.config.ts`

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { include: ["test/**/*.test.ts"], environment: "node", testTimeout: 30000 },
});
```

In `android/package.json` add to `"scripts"`: `"test": "vitest run"`, `"typecheck": "tsc --noEmit"`. In `android/tsconfig.json` set `"compilerOptions": { "strict": true, "resolveJsonModule": true }` (keep the template's `extends`).

- [ ] **Step 4: Run it to verify it fails**

Run: `cd android && npx vitest run test/appConfig.test.ts`
Expected: FAIL — cannot find module `../app.config`.

- [ ] **Step 5: Replace `app.json` with `app.config.ts`**

Read `android/app.json` first and carry over its icon/splash asset paths, then delete it.

```ts
import type { ExpoConfig } from "expo/config";

export const APP_VERSION = "1.0.0";
export const VERSION_CODE = 1;

export default function config(): ExpoConfig {
  return {
    name: "MICO360 Meetings",
    slug: "mico360-meetings",
    version: APP_VERSION,
    orientation: "portrait",
    icon: "./assets/icon.png",
    userInterfaceStyle: "automatic",
    android: {
      package: "com.mico360.meetings",
      versionCode: VERSION_CODE,
      adaptiveIcon: { foregroundImage: "./assets/adaptive-icon.png", backgroundColor: "#8B1E1E" },
    },
    extra: {},
  };
}
```

- [ ] **Step 6: Run the tests and typecheck**

Run: `cd android && npm test && npm run typecheck`
Expected: 1 passed; tsc exits 0.

- [ ] **Step 7: CI** — `.github/workflows/android-ci.yml`

```yaml
name: Android CI
on:
  push:
    paths: ["android/**", ".github/workflows/android-ci.yml"]
  pull_request:
    paths: ["android/**"]
jobs:
  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: android
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "24"
          cache: npm
          cache-dependency-path: android/package-lock.json
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
```

- [ ] **Step 8: Commit**

```bash
git add android .github/workflows/android-ci.yml
git commit -m "Android: Expo project, vitest, CI"
```

---

### Task 2: Desktop data, Unicode classes and parity fixtures

**Files:**
- Create: `android/tools/export_desktop_data.py`, `android/tools/make_parity_fixtures.py`, `android/tools/make_unicode_classes.mjs`
- Create (generated): `android/src/core/data/desktop.json`, `android/src/core/data/unicode.json`, `android/test/fixtures/*.json`
- Create: `android/src/core/unicode.ts`, `android/test/desktopData.test.ts`, `android/test/purity.test.ts`

**Interfaces:**
- Produces: `desktop.json` keys `transcriptToken, minutesStructure, baseTemplate, outputStyles: [name, hint][], defaultStyle, chunkSummaryPrompt, reduceNotesPrefix, mergeNotesPrompt, builtinPrompts: [name, category, text][], promptCategories, meetingTypes: {name, prompt_name, style, profile_name}[], fillers, stutterWords, statusCycle, statusSynonyms, prioritySynonyms, placeholders, insightsStopwords, arabicMarks: number[], statusOrder, pdfDesigns: Design[], defaultPdfDesign, profileDefaults, statusColors, pdfTitleKeys, pdfPeopleKeys, pdfStatusHeaders, pdfPlaceholders, signaturesEn, signaturesAr, connect: {baseUrl, httpsCandidates, defaultModel}`.
- Produces: `unicode.ts` exports `WORD` (class body for letters+digits+underscore), `LETTER` (letters + non-decimal numbers), both for use inside `[...]` with the `u` flag.

- [ ] **Step 1: Write the failing tests** — `android/test/desktopData.test.ts`

```ts
import { describe, expect, it } from "vitest";
import data from "../src/core/data/desktop.json";
import { LETTER, WORD } from "../src/core/unicode";

describe("desktop data", () => {
  it("has the desktop's shared text", () => {
    expect(data.transcriptToken).toBe("[TRANSCRIPT_HERE]");
    expect(data.builtinPrompts.length).toBe(25);
    expect(data.outputStyles.length).toBe(5);
    expect(data.meetingTypes.length).toBe(6);
    expect(data.pdfDesigns.map((d) => d.key)).toEqual(["classic", "modern", "formal", "compact"]);
    expect(data.connect.baseUrl).toBe("http://ai.mico360.com:5310/v1");
    expect(data.connect.httpsCandidates[0]).toBe("https://ai.mico360.com:5311/v1");
  });
});

describe("unicode classes", () => {
  const word = new RegExp(`^[${WORD}]+$`, "u");
  const letter = new RegExp(`^[${LETTER}]+$`, "u");
  it("match letters in any script and digits like Python's \\w", () => {
    for (const s of ["abc", "Ünïcödé", "مرحبا", "שלום", "日本語", "x_1", "٣٤"]) expect(word.test(s)).toBe(true);
    for (const s of [" ", "-", "'", ".", "،", "😀"]) expect(word.test(s)).toBe(false);
  });
  it("letters exclude decimal digits and underscore", () => {
    expect(letter.test("اجتماع")).toBe(true);
    expect(letter.test("a1")).toBe(false);
    expect(letter.test("a_b")).toBe(false);
  });
});
```

`android/test/purity.test.ts`

```ts
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : p.endsWith(".ts") ? [p] : [];
  });
}

it("src/core imports no React Native, Expo or native module", () => {
  for (const f of files(join(__dirname, "..", "src", "core"))) {
    const src = readFileSync(f, "utf8");
    expect(src, f).not.toMatch(/from\s+["'](react-native|expo[^"']*|react)["']/);
    expect(src, f).not.toMatch(/\\p\{/);
  }
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd android && npx vitest run test/desktopData.test.ts test/purity.test.ts`
Expected: FAIL — `desktop.json` and `unicode.ts` do not exist.

- [ ] **Step 3: The desktop data exporter** — `android/tools/export_desktop_data.py`

```python
"""Export the desktop app's shared text data for the Android app.

    <desktop python> android/tools/export_desktop_data.py

Writes android/src/core/data/desktop.json from the desktop modules, so prompts,
styles, meeting types, PDF designs and the Cloud endpoint are never copied by hand.
LOCALAPPDATA points at a temp folder first: the real user data is never touched.
"""
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="mico360_export_")
sys.path.insert(0, str(REPO))

from mico360 import config  # noqa: E402
from mico360.core import cleaning, insights, meeting_types, profiles, prompts, tasks  # noqa: E402
from mico360.export import pdf_designs, pdf_export  # noqa: E402

data = {
    "transcriptToken": prompts.TRANSCRIPT_TOKEN,
    "minutesStructure": prompts.MINUTES_STRUCTURE,
    "baseTemplate": prompts.BASE_TEMPLATE,
    "outputStyles": [[k, v] for k, v in prompts.OUTPUT_STYLES.items()],
    "defaultStyle": prompts.DEFAULT_STYLE,
    "chunkSummaryPrompt": prompts.CHUNK_SUMMARY_PROMPT,
    "reduceNotesPrefix": prompts.REDUCE_NOTES_PREFIX,
    "mergeNotesPrompt": prompts.MERGE_NOTES_PROMPT,
    "builtinPrompts": [list(t) for t in prompts.BUILTIN_PROMPTS],
    "promptCategories": prompts.PROMPT_CATEGORIES,
    "meetingTypes": [asdict(t) for t in meeting_types.BUILTIN_TYPES],
    "fillers": cleaning.FILLERS,
    "stutterWords": cleaning._STUTTER_WORDS,
    "statusCycle": tasks.STATUS_CYCLE,
    "statusSynonyms": tasks._STATUS_SYNONYMS,
    "prioritySynonyms": tasks._PRIORITY_SYNONYMS,
    "placeholders": sorted(tasks._PLACEHOLDER),
    "insightsStopwords": sorted(insights._STOPWORDS),
    "arabicMarks": [cp for cp in range(0x0600, 0x0700) if insights._AR_MARKS_RE.match(chr(cp))],
    "statusOrder": insights.STATUS_ORDER,
    "pdfDesigns": [asdict(d) for d in pdf_designs.choices()],
    "defaultPdfDesign": profiles.DEFAULT_PDF_DESIGN,
    "profileDefaults": asdict(profiles.CompanyProfile()),
    "statusColors": pdf_export.STATUS_COLORS,
    "pdfTitleKeys": sorted(pdf_export._TITLE_KEYS),
    "pdfPeopleKeys": sorted(pdf_export._PEOPLE_KEYS),
    "pdfStatusHeaders": sorted(pdf_export._STATUS_HEADERS),
    "pdfPlaceholders": sorted(pdf_export._PLACEHOLDERS),
    "signaturesEn": list(pdf_export._SIGN_EN),
    "signaturesAr": list(pdf_export._SIGN_AR),
    "connect": {
        "baseUrl": config.MICO360_CONNECT_BASE_URL,
        "httpsCandidates": list(config.MICO360_CONNECT_HTTPS_CANDIDATES),
        "defaultModel": config.MICO360_CONNECT_DEFAULT_MODEL,
    },
}
out = HERE.parent / "src" / "core" / "data" / "desktop.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out}")
```

- [ ] **Step 4: The Unicode class generator** — `android/tools/make_unicode_classes.mjs`

```js
// Build explicit character ranges for Python's \w (letters + numbers + "_") and
// for letters (+ non-decimal numbers), so src/core never needs \p{...} (Hermes).
//   node android/tools/make_unicode_classes.mjs
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function ranges(test) {
  const out = [];
  let start = -1;
  for (let cp = 0; cp <= 0x110000; cp++) {
    const ok = cp < 0x110000 && !(cp >= 0xd800 && cp <= 0xdfff) && test(String.fromCodePoint(cp));
    if (ok && start < 0) start = cp;
    if (!ok && start >= 0) { out.push([start, cp - 1]); start = -1; }
  }
  return out;
}
const word = ranges((c) => /[\p{L}\p{N}_]/u.test(c));
const letter = ranges((c) => /[\p{L}\p{Nl}\p{No}]/u.test(c));
const url = new URL("../src/core/data/unicode.json", import.meta.url);
writeFileSync(fileURLToPath(url), JSON.stringify({ word, letter }) + "\n");
console.log(`word ranges ${word.length}, letter ranges ${letter.length}`);
```

- [ ] **Step 5: `android/src/core/unicode.ts`**

```ts
import data from "./data/unicode.json";

/** Character-class bodies (use inside [...] with the "u" flag). Generated by
 *  tools/make_unicode_classes.mjs; they stand in for \p{...}, which Hermes may
 *  not support. */
function cls(r: number[][]): string {
  const hex = (n: number) => `\\u{${n.toString(16)}}`;
  return r.map(([a, b]) => (a === b ? hex(a) : `${hex(a)}-${hex(b)}`)).join("");
}

/** Python's \w: letters, numbers and underscore in any script. */
export const WORD = cls(data.word);
/** Letters in any script, plus non-decimal numbers (Python's [^\W\d_]). */
export const LETTER = cls(data.letter);
```

- [ ] **Step 6: The parity fixture generator** — `android/tools/make_parity_fixtures.py`

```python
"""Run the desktop Python code on a fixed input set and write the expected
results to android/test/fixtures/*.json for the TypeScript parity tests.

    <desktop python> android/tools/make_parity_fixtures.py
"""
import base64
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="mico360_fixtures_")
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
from mico360.core import calendar_ics, cleaning, diarization, insights, profiles, prompts, tasks, updater  # noqa: E402
from mico360.export import md_blocks, rtl  # noqa: E402

OUT = HERE.parent / "test" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)


def dump(name, obj):
    (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


MD = [
    "# Meeting Minutes\n**Meeting Title:** Budget review\n**Date and Time:** 5 Oct 2026\n**Attendees:** Ali, Sara\n\n## Purpose of Meeting\nPlan Q4.\nSecond line.",
    "## Action Items\n| Task | Responsible Person | Deadline | Status |\n| --- | --- | --- | --- |\n| Send report | Ali | 10 Oct | Pending |\n| Fix `a|b` pipe | Sara | 2026-10-12 | Done |\n| Escaped \\| pipe | Omar | | In progress |",
    "Task | Owner\n---|---\nNo leading pipe | Me\n\nAfter table",
    "- one\n- **two** bold\n* three\n\n1. first\n2) second\n7. seventh\n\n---\n\nPara with **bold** and `code`.",
    "### Deep ###\n#### Deeper\n###### Six\n####### Seven is text",
    "# محضر الاجتماع\n**عنوان الاجتماع:** الميزانية\n- بند أول\n| المهمة | المسؤول | الحالة |\n| --- | --- | --- |\n| إرسال التقرير | علي | قيد التنفيذ |",
    "Line with \x0b vertical tab and \x07 bell and emoji 😀 kept\n| Header only |\n| --- |",
    "| # | Task | Owner |\n| --- | --- | --- |\n| 1 | Extra | cell | Bob |",
    "Don`t | split\n| A | B |\n|---|---|\n| x | y |",
    "",
]
dump("mdBlocks", {
    "parse": [{"input": m, "blocks": [asdict(b) for b in md_blocks.parse(m)]} for m in MD],
    "runs": [{"input": s, "runs": [[t, b] for t, b in md_blocks.runs(s)]}
             for s in ["plain", "**bold** start", "mid **b** end", "**a****b**", "un**closed"]],
    "clean": [{"input": s, "output": md_blocks.clean_text(s)} for s in ["a\x00b\x0bc\td\ne\x7ff", "ok \ufffe"]],
})
dump("rtl", [{"input": s, "arabic": rtl.has_arabic(s)} for s in ["hello", "مرحبا", "שלום", "x ي", ""]])

LONG = " ".join(f"Sentence {i} is about the budget, um, and the plan." for i in range(120))
AR = "هذا اجتماع مهم؟ نعم۔ " * 80
dump("cleaning", {
    "clean": [{"input": s, "fillers": f, "output": cleaning.clean_transcript(s, remove_filler_words=f)}
              for s in ["Um, we should, uh, start. Uh the the plan is ready.\nUm\nUm\nok so so what",
                        "Do you know if he, um, said hmm? I mean kind of.", "Hmm. yes. Erm, maybe.",
                        "Line one\r\nLine one\r\n\r\n\r\n\r\nLine two", "We, uh, agreed, umm, to go."]
              for f in (True, False)],
    "chunk": [{"input": t, "max": m, "overlap": o, "chunks": cleaning.chunk_text(t, m, o)}
              for t, m, o in [(LONG, 500, 100), (LONG, 2000, 400), (AR, 300, 60),
                              ("x" * 1200, 500, 100), ("word " * 400, 300, 80), ("short", 6000, 400)]],
})

dump("prompts", {
    "generation": [{"template": t, "style": s, "transcript": tr,
                    "output": prompts.build_generation_prompt(t, s, tr)}
                   for t in [prompts.BASE_TEMPLATE, "No token template", "A [TRANSCRIPT_HERE] B [TRANSCRIPT_HERE]"]
                   for s in ["Formal Minutes", "Unknown style"]
                   for tr in ["Plain text", "Cost $& and $1 and {idx} [TRANSCRIPT_HERE]"]],
    "reduce": prompts.build_reduce_prompt("T [TRANSCRIPT_HERE]", "Short Summary", "notes $&"),
    "merge": prompts.build_merge_notes_prompt("n {first}", 2, 5, 9),
    "chunk": prompts.CHUNK_SUMMARY_PROMPT.format(idx=3, total=7).replace(prompts.TRANSCRIPT_TOKEN, "part $1"),
})

REF = date(2026, 10, 5)
DEADLINES = ["2026-10-12", "20261012", "12/10/2026", "10/25/2026", "12-10-2026", "12.10.2026",
             "Oct 12, 2026", "October 12, 2026", "12 Oct 2026", "12 October 2026", "Oct 12 2026",
             "12 Oct, 2026", "12th October 2026", "1st Nov", "10 Jan", "Feb 29", "5 Oct", "3 Jul",
             "Not specified", "tbd", "next week", "31/02/2026", "  12 oct  2026. ", "2026-10-12T10:00"]
dump("actionItems", {
    "deadlines": [{"input": s, "ref": REF.isoformat(),
                   "output": (d.isoformat() if (d := tasks.parse_deadline(s, REF)) else None)} for s in DEADLINES],
    "status": [{"input": s, "output": tasks.normalize_status(s)}
               for s in ["done", "In-Progress", "WIP", "", "open", "Blocked", "Cancelled"]],
    "priority": [{"input": s, "output": tasks.normalize_priority(s)}
                 for s in ["urgent", "P2", "low", "", "n/a", "Whenever"]],
    "extract": [{"input": m, "items": [asdict(i) for i in tasks.extract_action_items(m)]}
                for m in MD + ["| Action | Owner | Due | State | Priority |\n|---|---|---|---|---|\n"
                               "| **Ship** it | Ana | 1st Nov | done | urgent |\n| ... | x | | | |"]],
    "csvSafe": [{"input": s, "output": tasks.csv_safe(s)} for s in ["=SUM(A1)", "+1", "-x", "@a", "\tt", "ok"]],
})


def item(task, owner, deadline, status, mid, title, mdate, prio=""):
    it = tasks.ActionItem(task=task, owner=owner, deadline=deadline, status=status, meeting_id=mid,
                          meeting_title=title, meeting_date=mdate, priority=prio)
    it.okey = it.key()
    return it


ITEMS = [item("Send report, final", "Ali", "10 Oct", "Pending", 1, "Budget; Q4", "2026-10-05", "High"),
         item("Send report, final", "Ali", "10 Oct", "Pending", 1, "Budget; Q4", "2026-10-05"),
         item("إرسال التقرير النهائي إلى الإدارة العليا قبل نهاية الأسبوع القادم", "علي", "2026-10-20",
              "in progress", 2, "اجتماع", "2026-10-06"),
         item("No date", "", "tbd", "Pending", 3, "", "2026-10-06")]
ics = calendar_ics.build_ics(ITEMS, today=REF)
dump("ics", {"items": [asdict(i) for i in ITEMS],
             "ics": "\r\n".join(ln for ln in ics.split("\r\n") if not ln.startswith("DTSTAMP:")),
             "uids": [calendar_ics.event_uid(i) for i in ITEMS]})

COERCE = [("logo_position", "Centre"), ("logo_position", "middle"), ("page_number_position", "Bottom Right"),
          ("page_number_position", "top_center"), ("pdf_design", "MODERN"), ("pdf_design", "fancy"),
          ("accent_color", "#abc"), ("accent_color", "red;}</style><script>"), ("show_page_numbers", "no"),
          ("show_page_numbers", ""), ("show_page_numbers", True), ("logo_width_mm", "120"),
          ("logo_width_mm", "abc"), ("logo_width_mm", 5), ("phone", 96824123456.0), ("phone", None),
          ("name", "  Acme  "), ("page_number_format", "  "), ("footer_alignment", "RIGHT")]
dump("profiles", {
    "coerce": [{"key": k, "value": v, "output": profiles._coerce(k, v)} for k, v in COERCE],
    "normHeader": [{"input": h, "output": profiles._norm_header(h)}
                   for h in ["Company Name", " Phone ", "Logo-File", "Accent Colour", "design", "unknown col"]],
    "normalize": [{"input": r, "output": profiles._normalize_record(r)}
                  for r in [{"Company": "Acme", "Tel": "1", "junk": 2}, {"name": "", "phone": None}, {"id": "x"}]],
})

def synth_voice(seconds, f0, tract, seed, gain=0.3):
    vowels = [(730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480),
              (570, 840, 2410), (440, 1020, 2240), (660, 1720, 2410), (490, 1350, 1690)]
    rng = np.random.default_rng(seed)
    sr, out, total = 16000, [], 0
    while total < seconds * sr:
        n = int(0.16 * sr)
        t = np.arange(n) / sr
        pitch = f0 * (1 + 0.04 * np.sin(2 * np.pi * rng.uniform(2, 5) * t))
        phase = 2 * np.pi * np.cumsum(pitch) / sr
        formants = [f * tract for f in vowels[rng.integers(len(vowels))]]
        k = np.arange(1, int(3800 / f0) + 1)
        amp = sum(1.0 / (1 + ((k * f0 - fc) / (60 + 0.06 * fc)) ** 2) for fc in formants) / k ** 0.5
        v = (amp[:, None] * np.sin(k[:, None] * phase[None, :])).sum(0)
        v *= np.hanning(n) * gain / (np.abs(v).max() + 1e-9)
        out.append(v)
        if rng.random() < 0.25:
            out.append(np.zeros(int(rng.uniform(0.05, 0.2) * sr)))
        total += sum(len(o) for o in out[-2:])
    sig = np.concatenate(out)[: int(seconds * sr)]
    return (sig + rng.normal(0, 0.002, sig.size)).astype("float32")


class Seg:
    def __init__(self, a, b):
        self.start, self.end, self.speaker = a, b, None


def clip(parts):
    audio, segs, t = [], [], 0.0
    for a in parts:
        segs.append((t, t + a.size / 16000))
        audio += [a, np.zeros(4000, "float32")]
        t += a.size / 16000 + 0.25
    pcm = np.clip(np.concatenate(audio) * 32768, -32768, 32767).astype("<i2")
    wav = Path(tempfile.mkdtemp()) / "c.wav"
    import soundfile as sf
    sf.write(str(wav), pcm.astype("float32") / 32768.0, 16000, subtype="PCM_16")
    labels = diarization.diarize(str(wav), [Seg(a, b) for a, b in segs])
    stats_m, stats_n = diarization._voice_stats(pcm.astype("float32") / 32768.0, [Seg(a, b) for a, b in segs])
    return {"pcm16": base64.b64encode(pcm.tobytes()).decode("ascii"), "segments": segs, "labels": labels,
            "M": stats_m.tolist(), "N": stats_n.tolist()}


lo, hi = dict(f0=115.0, tract=1.0), dict(f0=205.0, tract=1.17)
dump("diarization", {
    "two": clip([synth_voice(3.0, seed=i, gain=(0.3, 0.12)[i % 2], **v)
                 for i, v in enumerate([lo, hi, lo, hi, lo, hi])]),
    "one": clip([synth_voice(2.5, seed=40 + i, gain=g, **lo) for i, g in enumerate((0.3, 0.05, 0.2, 0.1))]),
})


class M:
    def __init__(self, mid, title, ts, minutes):
        self.id, self.title, self.created_at, self.updated_at, self.minutes = mid, title, ts, ts, minutes


class H:
    def __init__(self, ms):
        self.ms = ms

    def list_all(self):
        return self.ms


class S:
    def __init__(self, items):
        self.items = items

    def all_items(self):
        return self.items


def noon(y, m, d):
    return datetime(y, m, d, 12, 0).timestamp()


MEETINGS = [M(1, "Budget", noon(2026, 10, 5), MD[0] + "\nbudget budget forecast forecast forecast"),
            M(2, "اجتماع", noon(2026, 9, 28), "# محضر\nالميزانية الميزانية التسويق"),
            M(3, "Ops", noon(2026, 8, 3), "Logistics warehouse logistics")]
INS_ITEMS = [item("A", "Ali", "2026-10-01", "Pending", 1, "Budget", "2026-10-05"),
             item("B", "Ali", "2026-10-30", "In Progress", 1, "Budget", "2026-10-05"),
             item("C", "Sara", "", "Completed", 2, "x", "2026-09-28"),
             item("D", "tbd", "", "Cancelled", 2, "x", "2026-09-28"),
             item("E", "Sara", "1 Oct", "Pending", 3, "Ops", "2026-08-03")]
ins = insights.compute_insights(H(MEETINGS), S(INS_ITEMS), today=date(2026, 10, 15))
dump("insights", {
    "meetings": [{"id": str(m.id), "title": m.title, "createdAt": int(m.created_at * 1000), "minutes": m.minutes}
                 for m in MEETINGS],
    "items": [asdict(i) for i in INS_ITEMS], "today": "2026-10-15",
    "result": {"totalMeetings": ins.total_meetings, "totalItems": ins.total_items, "openItems": ins.open_items,
               "overdueItems": ins.overdue_items, "completionRate": ins.completion_rate,
               "statusCounts": ins.status_counts, "byOwner": [list(x) for x in ins.by_owner],
               "cadence": [list(x) for x in ins.cadence], "keywords": [list(x) for x in ins.keywords]},
})

VERS = [("1.10", "1.9"), ("v1.2.3", "1.2.3"), ("1.3.0", "1.3.0-rc1"), ("1.3.0-rc1", "1.3.0"),
        ("junk", "1.0.0"), ("2", "1.9.9"), ("1.2", "1.2.0"), ("1.0.1", "1.0.0.9")]
H64 = "ab" * 32
dump("updater", {
    "newer": [{"latest": a, "current": b, "output": updater.is_newer(a, b)} for a, b in VERS],
    "checksum": [{"text": t, "file": f, "bare": b, "output": updater.parse_checksum(t, f, allow_bare=b)}
                 for t, f, b in [(f"{H64}  app.apk", "app.apk", False), (f"{H64} *app.apk", "APP.apk", False),
                                 (f"{H64}  other.apk", "app.apk", False), (H64, "app.apk", True),
                                 (H64, "app.apk", False), (f"x {H64} y", "", False), ("", "a", True)]],
})
print(f"fixtures written to {OUT}")
```

- [ ] **Step 7: Generate the data and fixtures**

```bash
cd "C:/Users/Khurram/Dropbox/0 AI/Claude/MICO360-Softwares/MICO360-Meeting"
PY=C:/Users/Khurram/AppData/Local/Python/pythoncore-3.14-64/python.exe
$PY android/tools/export_desktop_data.py && $PY android/tools/make_parity_fixtures.py
node android/tools/make_unicode_classes.mjs
```

Expected: `wrote …desktop.json`, `fixtures written to …`, `word ranges N, letter ranges M`.

- [ ] **Step 8: Run the tests**

Run: `cd android && npx vitest run test/desktopData.test.ts test/purity.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add android/tools android/src/core android/test
git commit -m "Android: desktop data export, Unicode classes, parity fixtures"
```

---

### Task 3: Markdown blocks and right-to-left detection

**Files:**
- Create: `android/src/core/mdBlocks.ts`, `android/src/core/rtl.ts`, `android/test/mdBlocks.test.ts`

**Interfaces:**
- Produces: `type BlockKind`, `interface Block {kind,text,key,items,headers,rows,level,start}`, `cleanText(s: unknown): string`, `runs(text): Array<[string, boolean]>`, `plain(text): string`, `parse(markdown): Block[]`; `hasArabic(text): boolean`.

- [ ] **Step 1: Write the failing test** — `android/test/mdBlocks.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/mdBlocks.json";
import rtlFx from "./fixtures/rtl.json";
import { cleanText, parse, plain, runs } from "../src/core/mdBlocks";
import { hasArabic } from "../src/core/rtl";

describe("mdBlocks parity with desktop md_blocks.py", () => {
  it.each(fx.parse.map((c, i) => [i, c] as const))("parse case %i", (_i, c) => {
    expect(parse(c.input)).toEqual(c.blocks);
  });
  it("runs", () => { for (const c of fx.runs) expect(runs(c.input)).toEqual(c.runs); });
  it("cleanText", () => { for (const c of fx.clean) expect(cleanText(c.input)).toEqual(c.output); });
  it("rtl", () => { for (const c of rtlFx) expect(hasArabic(c.input)).toBe(c.arabic); });
});

describe("edge cases", () => {
  it("keeps emoji and drops only lone surrogates", () => {
    expect(cleanText("a😀b\ud800c\udc00d")).toBe("a😀bcd");
  });
  it("plain strips bold markers", () => expect(plain("**Key:** value")).toBe("Key: value"));
  it("never hangs on a stray pipe line", () => {
    expect(parse("|\n|\n|").length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/mdBlocks.test.ts`
Expected: FAIL — cannot find `../src/core/mdBlocks`.

- [ ] **Step 3: `android/src/core/rtl.ts`**

```ts
// Right-to-left scripts: Arabic (+ Supplement, Extended-A, presentation forms
// A/B) and Hebrew (+ presentation forms), as desktop export/rtl.py.
const RANGES: Array<[number, number]> = [
  [0x0600, 0x06ff], [0x0750, 0x077f], [0x08a0, 0x08ff],
  [0xfb50, 0xfdff], [0xfe70, 0xfeff], [0x0590, 0x05ff], [0xfb1d, 0xfb4f],
];

export function hasArabic(text: string): boolean {
  for (const ch of text ?? "") {
    const cp = ch.codePointAt(0)!;
    for (const [lo, hi] of RANGES) if (cp >= lo && cp <= hi) return true;
  }
  return false;
}
```

- [ ] **Step 4: `android/src/core/mdBlocks.ts`**

```ts
/** A small Markdown block parser — the subset the minutes use (port of desktop
 *  export/md_blocks.py): headings, **key:** value lines, bullets, numbered
 *  lists, pipe tables and paragraphs. */

export type BlockKind = "h1" | "h2" | "h3" | "kv" | "bullet" | "olist" | "table" | "para";

export interface Block {
  kind: BlockKind;
  text: string;
  key: string;
  items: string[];
  headers: string[];
  rows: string[][];
  level: number;
  start: number;
}

function block(kind: BlockKind, p: Partial<Block> = {}): Block {
  return { kind, text: "", key: "", items: [], headers: [], rows: [], level: 0, start: 1, ...p };
}

// C0 controls except \t \n \r, DEL, U+FFFE/U+FFFF and LONE surrogates (a valid
// pair is an emoji and must survive).
const CTRL_RE =
  /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;

export function cleanText(s: unknown): string {
  return (s === null || s === undefined ? "" : String(s)).replace(CTRL_RE, "");
}

const BOLD_RE = /\*\*([^\n]+?)\*\*/g;
const KV_RE = /^\*\*([^\n]+?):\*\*\s*([^\n]*)$/;
const HEADING_RE = /^(#{1,6})\s+([^\n]*?)(?:\s+#+)?\s*$/;
// Python's \d also matches other decimal digits; ASCII, Arabic-Indic,
// Extended Arabic-Indic and Devanagari cover the app's languages.
const DIGIT = "[0-9\u0660-\u0669\u06f0-\u06f9\u0966-\u096f]";
const OLIST_RE = new RegExp(`^(${DIGIT}{1,4})[.)]\\s+([^\\n]*)$`);
const BULLETS = ["- ", "* ", "• ", "+ "];
const HR_RE = /^(?:-{3,}|\*{3,}|_{3,})$/;
const TABLE_SEP_RE = /^\s*\|?[\s:|-]+\|?\s*$/;
const INDEX_HEADERS = new Set(["#", "no", "no.", "nr", "s/n", "sn", "sr", "sr.", "م", "رقم", "الرقم"]);

function toInt(digits: string): number {
  let n = 0;
  for (const ch of digits) {
    const cp = ch.codePointAt(0)!;
    const zero = cp >= 0x0966 ? 0x0966 : cp >= 0x06f0 ? 0x06f0 : cp >= 0x0660 ? 0x0660 : 0x30;
    n = n * 10 + (cp - zero);
  }
  return n;
}

const startsWithAny = (s: string, prefixes: string[]) => prefixes.some((p) => s.startsWith(p));

export function runs(text: string): Array<[string, boolean]> {
  const out: Array<[string, boolean]> = [];
  let pos = 0;
  for (const m of text.matchAll(BOLD_RE)) {
    const start = m.index!;
    if (start > pos) out.push([text.slice(pos, start), false]);
    out.push([m[1], true]);
    pos = start + m[0].length;
  }
  if (pos < text.length) out.push([text.slice(pos), false]);
  return out.length ? out : [["", false]];
}

export function plain(text: string): string {
  return runs(text).map(([t]) => t).join("");
}

function isTableSep(line: string): boolean {
  return TABLE_SEP_RE.test(line) && line.includes("-");
}

/** Split a row on unescaped pipes outside `code` spans; "\|" is a literal pipe. */
function splitCells(line: string): string[] {
  const s = line.trim();
  let cells: string[] = [];
  let buf = "";
  let inCode = false;
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && s[i + 1] === "|") {
      buf += "|";
      i += 2;
      continue;
    }
    if (ch === "`") {
      // only a PAIRED backtick opens a code span ("Don`t" is a literal)
      if (inCode) inCode = false;
      else if (s.indexOf("`", i + 1) !== -1) inCode = true;
    }
    if (ch === "|" && !inCode) {
      cells.push(buf);
      buf = "";
    } else {
      buf += ch;
    }
    i += 1;
  }
  cells.push(buf);
  if (s.startsWith("|") && cells.length) cells = cells.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|") && cells.length) cells = cells.slice(0, -1);
  return cells.map((c) => c.trim());
}

/** Keep a row aligned with its headers when it has EXTRA cells. */
function fitRow(row: string[], headers: string[]): string[] {
  const n = headers.length;
  if (n === 0 || row.length <= n) return row;
  const r = [...row];
  while (r.length > n && !r[r.length - 1]) r.pop();
  const extra = r.length - n;
  if (extra <= 0) return r;
  const target = n > 1 && INDEX_HEADERS.has(headers[0].trim().toLowerCase()) ? 1 : 0;
  const merged = r.slice(target, target + extra + 1).join(" | ");
  return [...r.slice(0, target), merged, ...r.slice(target + extra + 1)];
}

function isTableStart(lines: string[], i: number): boolean {
  const s = lines[i].trim();
  return (
    s.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]) &&
    !startsWithAny(s, ["#", ...BULLETS]) && !isTableSep(s)
  );
}

function isSpecial(lines: string[], i: number): boolean {
  const s = lines[i].trim();
  return (
    !s || startsWithAny(s, ["#", "|", ...BULLETS]) || KV_RE.test(s) || OLIST_RE.test(s) ||
    HR_RE.test(s) || isTableStart(lines, i)
  );
}

export function parse(markdown: string): Block[] {
  const lines = cleanText(markdown ?? "").split("\r\n").join("\n").split("\r").join("\n").split("\n");
  const blocks: Block[] = [];
  const n = lines.length;
  let i = 0;
  while (i < n) {
    const stripped = lines[i].trim();
    if (!stripped) { i += 1; continue; }

    if (isTableStart(lines, i)) {
      const sp = splitCells(stripped);
      const headers = sp.length ? sp : [""];
      const rows: string[][] = [];
      i += 2;
      while (i < n && lines[i].includes("|") && !isTableSep(lines[i])) {
        rows.push(fitRow(splitCells(lines[i]), headers));
        i += 1;
      }
      blocks.push(block("table", { headers, rows }));
      continue;
    }

    const hm = HEADING_RE.exec(stripped);
    if (hm) {
      const level = hm[1].length;
      const kind: BlockKind = level === 1 ? "h1" : level === 2 ? "h2" : "h3";
      blocks.push(block(kind, { text: hm[2].trim(), level }));
      i += 1;
      continue;
    }

    if (HR_RE.test(stripped)) { i += 1; continue; }

    const kv = KV_RE.exec(stripped);
    if (kv) {
      blocks.push(block("kv", { key: kv[1].trim(), text: kv[2].trim() }));
      i += 1;
      continue;
    }

    if (startsWithAny(stripped, BULLETS)) {
      const items: string[] = [];
      while (i < n && startsWithAny(lines[i].trim(), BULLETS)) {
        items.push(lines[i].trim().slice(2).trim());
        i += 1;
      }
      blocks.push(block("bullet", { items }));
      continue;
    }

    const om = OLIST_RE.exec(stripped);
    if (om) {
      const start = toInt(om[1]);
      const items: string[] = [];
      while (i < n) {
        const m2 = OLIST_RE.exec(lines[i].trim());
        if (!m2) break;
        items.push(m2[2].trim());
        i += 1;
      }
      blocks.push(block("olist", { items, start }));
      continue;
    }

    // Paragraph: consume the current line first (a stray '|' line can't hang
    // the loop), then gather lines until a blank line or another block.
    const para = [stripped];
    i += 1;
    while (i < n && !isSpecial(lines, i)) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push(block("para", { text: para.join(" ") }));
  }
  return blocks;
}
```

- [ ] **Step 5: Run the tests**

Run: `cd android && npx vitest run test/mdBlocks.test.ts`
Expected: PASS (every fixture case). If a case differs, compare with `md_blocks.py` line by line before changing anything — the fixture is the truth.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/mdBlocks.ts android/src/core/rtl.ts android/test/mdBlocks.test.ts
git commit -m "Android core: Markdown blocks and RTL detection (parity with desktop)"
```

---

### Task 4: Transcript cleaning and chunking

**Files:**
- Create: `android/src/core/cleaning.ts`, `android/test/cleaning.test.ts`

**Interfaces:**
- Consumes: `WORD` from `unicode.ts`; `desktop.json` `fillers`, `stutterWords`.
- Produces: `removeFillers(text)`, `dedupLines(text)`, `cleanTranscript(text, removeFillerWords = true)`, `chunkText(text, maxChars = 6000, overlapChars = 400): string[]`, `splitlines(text): string[]`.

- [ ] **Step 1: Write the failing test** — `android/test/cleaning.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/cleaning.json";
import { chunkText, cleanTranscript, splitlines } from "../src/core/cleaning";

describe("cleaning parity with desktop cleaning.py", () => {
  it.each(fx.clean.map((c, i) => [i, c] as const))("clean case %i", (_i, c) => {
    expect(cleanTranscript(c.input, c.fillers)).toBe(c.output);
  });
  it.each(fx.chunk.map((c, i) => [i, c] as const))("chunk case %i", (_i, c) => {
    expect(chunkText(c.input, c.max, c.overlap)).toEqual(c.chunks);
  });
});

describe("splitlines like Python", () => {
  it("drops one trailing empty line and splits on every line break", () => {
    expect(splitlines("a\nb\n")).toEqual(["a", "b"]);
    expect(splitlines("a\r\nb\rc\u2028d")).toEqual(["a", "b", "c", "d"]);
    expect(splitlines("")).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/cleaning.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/cleaning.ts`**

```ts
/** Transcript cleaning and chunking (port of desktop core/cleaning.py). */
import data from "./data/desktop.json";
import { WORD } from "./unicode";

const FILLERS: string[] = data.fillers;
const STUTTER: string[] = data.stutterWords;
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const F = `(?<![${WORD}'-])(?:${FILLERS.map(esc).join("|")})(?![${WORD}'-])`;
const COMMA_WRAPPED_RE = new RegExp(`,[ \\t]*${F}[ \\t]*,`, "giu");
const FILLER_RE = new RegExp(`${F}(?<p>[ \\t]*(?:\\.\\.\\.|…|[,.!?]))?(?<ws>[ \\t]*)`, "giu");
const REPEAT_WORD_RE = new RegExp(`(?<![${WORD}])(${STUTTER.join("|")})(?:[ \\t]+\\1(?![${WORD}]))+`, "giu");
const CAP = "\u0000";

/** Python str.splitlines(): every line-break kind; no trailing empty line. */
export function splitlines(text: string): string[] {
  if (!text) return [];
  const parts = text.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/);
  if (parts[parts.length - 1] === "") parts.pop();
  return parts;
}

function replaceMatches(text: string, re: RegExp, fn: (m: RegExpMatchArray) => string): string {
  let out = "";
  let pos = 0;
  for (const m of text.matchAll(re)) {
    out += text.slice(pos, m.index) + fn(m);
    pos = m.index! + m[0].length;
  }
  return out + text.slice(pos);
}

/** Remove hesitation sounds only and collapse function-word stutters. */
export function removeFillers(input: string): string {
  let text = input.replace(COMMA_WRAPPED_RE, " ");
  const src = text;
  text = replaceMatches(src, FILLER_RE, (m) => {
    const before = src.slice(0, m.index).replace(/[ \t]+$/, "");
    if (!before || "\n.!?…".includes(before[before.length - 1])) return CAP;
    const p = (m.groups?.p ?? "").trim();
    const keep = p === "." || p === "!" || p === "?" ? p : "";
    return keep + (m.groups?.ws ?? "");
  });
  text = text.replace(/\u0000[ \t]*([a-z])/g, (_m, c: string) => c.toUpperCase());
  text = text.split(CAP).join("");
  text = text.replace(REPEAT_WORD_RE, (_m, w: string) => w);
  text = text.replace(/[ \t]+([,.!?;:])/g, (_m, p: string) => p);
  text = text.replace(/,([.!?])/g, (_m, p: string) => p);
  text = text.replace(/[ \t]{2,}/g, " ");
  text = text.replace(/[ \t]+\n/g, "\n");
  text = text.replace(/^[ \t]+(?=\S)/gm, "");
  return text;
}

export function dedupLines(text: string): string {
  const out: string[] = [];
  let prev: string | null = null;
  for (const line of splitlines(text)) {
    const norm = line.trim().toLowerCase();
    if (norm && norm === prev) continue;
    out.push(line);
    prev = norm;
  }
  return out.join("\n");
}

export function cleanTranscript(input: string, removeFillerWords = true): string {
  let text = input.split("\r\n").join("\n").trim();
  text = dedupLines(text);
  if (removeFillerWords) text = removeFillers(text);
  text = text.replace(/\n{3,}/g, "\n\n");
  return text.trim();
}

// Sentence ends (Latin, Arabic ؟, Urdu ۔, Devanagari ।, CJK) and newlines.
const BOUNDARY_RE = /(?<=[.!?…؟۔।。！？])[ \t]+|[ \t]*\n\s*|(?<=[。！？])(?=\S)/g;

function splitSentences(text: string): string[] {
  const segs: string[] = [];
  let start = 0;
  for (const m of text.matchAll(BOUNDARY_RE)) {
    const end = m.index! + m[0].length;
    if (end <= start) continue;
    segs.push(text.slice(start, end));
    start = end;
  }
  if (start < text.length) segs.push(text.slice(start));
  return segs.filter((s) => s.trim());
}

function splitLong(seg: string, step: number, limit: number): string[] {
  const pieces: string[] = [];
  let pos = 0;
  const n = seg.length;
  while (pos < n) {
    if (n - pos <= step) { pieces.push(seg.slice(pos)); break; }
    const window = seg.slice(pos, pos + step + 1);
    const cut = Math.max(window.lastIndexOf(" "), window.lastIndexOf("\t"), window.lastIndexOf("\n"));
    let end: number;
    if (cut > 0) {
      end = pos + cut;
    } else {
      const m = /\s/.exec(seg.slice(pos + step, pos + limit + 1));
      end = m ? pos + step + m.index : pos + limit;
    }
    pieces.push(seg.slice(pos, end));
    pos = end;
  }
  return pieces;
}

/** Pack text into chunks <= maxChars on sentence boundaries, each seeded with
 *  ~overlapChars of the previous chunk's tail. */
export function chunkText(input: string, maxChars = 6000, overlapChars = 400): string[] {
  const text = input.trim();
  if (text.length <= maxChars) return text ? [text] : [];
  maxChars = Math.max(1, Math.trunc(maxChars));
  overlapChars = Math.max(0, Math.min(Math.trunc(overlapChars), Math.floor(maxChars / 2)));
  const step = Math.max(1, Math.min(maxChars, overlapChars || maxChars));
  const segments: string[] = [];
  for (const seg of splitSentences(text)) {
    if (seg.length > maxChars) segments.push(...splitLong(seg, step, maxChars));
    else segments.push(seg);
  }
  const chunks: string[] = [];
  let cur: string[] = [];
  let size = 0;
  let fresh = 0;
  for (const seg of segments) {
    const sLen = seg.length;
    if (size + sLen > maxChars && cur.length) {
      if (fresh) {
        chunks.push(cur.join("").trim());
        const tail: string[] = [];
        let tlen = 0;
        for (let k = cur.length - 1; k >= 0; k--) {
          if (tlen + cur[k].length > overlapChars) break;
          tail.unshift(cur[k]);
          tlen += cur[k].length;
        }
        cur = tail;
        size = tlen;
      }
      if (size + sLen > maxChars) { cur = []; size = 0; }
      fresh = 0;
    }
    cur.push(seg);
    size += sLen;
    fresh += 1;
  }
  if (cur.length && fresh) chunks.push(cur.join("").trim());
  return chunks.filter((c) => c);
}
```

- [ ] **Step 4: Run the tests**

Run: `cd android && npx vitest run test/cleaning.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/cleaning.ts android/test/cleaning.test.ts
git commit -m "Android core: transcript cleaning and chunking (parity with desktop)"
```

---

### Task 5: Prompts, prompt library and meeting types

**Files:**
- Create: `android/src/core/prompts.ts`, `android/src/core/meetingTypes.ts`, `android/test/prompts.test.ts`

**Interfaces:**
- Produces (`prompts.ts`): `TRANSCRIPT_TOKEN`, `STYLE_NAMES: string[]`, `DEFAULT_STYLE`, `styleHint(style): string`, `buildGenerationPrompt(template, style, transcript)`, `buildReducePrompt(template, style, notes)`, `buildMergeNotesPrompt(notes, first, last, total)`, `buildChunkPrompt(chunk, idx, total)`, `interface SavedPrompt {id,name,text,builtin,category,favorite}`, `BUILTIN_PROMPTS: Array<{name,category,text}>`, `PROMPT_CATEGORIES`, `promptId(name)`, `builtinText(name)`, `isModified(p)`, `uniqueName(name, taken)`, `copyName(name, taken)`, `parsePromptFile(fileName, raw): {records: PromptRecord[]; invalid: number}`, `planPromptImport(existing: SavedPrompt[], records): PromptImportPlan`, `buildPromptExport(prompts): string`.
- Produces (`meetingTypes.ts`): `interface MeetingType {name, prompt_name, style, profile_name}`, `BUILTIN_MEETING_TYPES`, `sanitizeMeetingTypes(raw: unknown): MeetingType[]`.

- [ ] **Step 1: Write the failing test** — `android/test/prompts.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/prompts.json";
import * as P from "../src/core/prompts";
import { BUILTIN_MEETING_TYPES, sanitizeMeetingTypes } from "../src/core/meetingTypes";

describe("prompt parity with desktop prompts.py", () => {
  it("generation prompts (incl. $&, $1, {idx} and the token inside the transcript)", () => {
    for (const c of fx.generation) expect(P.buildGenerationPrompt(c.template, c.style, c.transcript)).toBe(c.output);
  });
  it("reduce, merge and chunk prompts", () => {
    expect(P.buildReducePrompt("T [TRANSCRIPT_HERE]", "Short Summary", "notes $&")).toBe(fx.reduce);
    expect(P.buildMergeNotesPrompt("n {first}", 2, 5, 9)).toBe(fx.merge);
    expect(P.buildChunkPrompt("part $1", 3, 7)).toBe(fx.chunk);
  });
});

describe("prompt library", () => {
  it("has the 25 built-ins in 6 categories", () => {
    expect(P.BUILTIN_PROMPTS).toHaveLength(25);
    expect(P.PROMPT_CATEGORIES).toHaveLength(6);
    expect(P.builtinText("Formal Minutes")).toContain(P.TRANSCRIPT_TOKEN);
  });
  it("names stay unique", () => {
    expect(P.uniqueName("Notes", ["notes", "Notes (2)"])).toBe("Notes (3)");
    expect(P.copyName("Notes", ["Notes (copy)"])).toBe("Notes (copy 2)");
  });
  it("imports desktop exports without overwriting anything", () => {
    const raw = JSON.stringify({ mico360_prompts: 1, prompts: [
      { name: "Mine", category: "Summary", favorite: true, text: "A\r\nB" },
      { name: "Mine", category: "Summary", text: "Other" },
      { name: "Empty", text: "  " }, 5,
    ] });
    const { records, invalid } = P.parsePromptFile("export.json", raw);
    const plan = P.planPromptImport(
      [{ id: "x", name: "Mine", text: "A\nB", builtin: false, category: "Summary", favorite: false }], records);
    expect(invalid).toBe(0);
    expect(plan.skipped).toEqual(["Mine"]);
    expect(plan.toAdd.map((p) => p.name)).toEqual(["Mine (2)"]);
    expect(plan.invalid).toBe(2);
    const back = JSON.parse(P.buildPromptExport(plan.toAdd));
    expect(back.mico360_prompts).toBe(1);
  });
  it("reads a .txt prompt named after the file", () => {
    const { records } = P.parsePromptFile("Weekly_sync.txt", "Write notes [TRANSCRIPT_HERE]");
    expect(records[0].name).toBe("Weekly sync");
  });
  it("refuses other file types", () => {
    expect(() => P.parsePromptFile("x.docx", "")).toThrow(/Unsupported file type/);
  });
});

describe("meeting types", () => {
  it("ships the six desktop presets and survives bad data", () => {
    expect(BUILTIN_MEETING_TYPES).toHaveLength(6);
    expect(sanitizeMeetingTypes([{ name: "A", style: 3 }, { name: "" }, "x", null])).toEqual([
      { name: "A", prompt_name: "", style: "3", profile_name: "" },
    ]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/prompts.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/prompts.ts`**

```ts
/** Prompt templates, output styles and the prompt library (port of desktop
 *  core/prompts.py). All text comes from data/desktop.json. */
import data from "./data/desktop.json";

export const TRANSCRIPT_TOKEN: string = data.transcriptToken;
const STYLES = new Map<string, string>(data.outputStyles as Array<[string, string]>);
export const STYLE_NAMES: string[] = [...STYLES.keys()];
export const DEFAULT_STYLE: string = data.defaultStyle;
export const PROMPT_CATEGORIES: string[] = data.promptCategories;
export const BUILTIN_PROMPTS = (data.builtinPrompts as Array<[string, string, string]>).map(
  ([name, category, text]) => ({ name, category, text }));

/** Replace every occurrence literally ($& and $1 in user text stay as typed). */
const sub = (s: string, token: string, value: string) => s.split(token).join(value);

export function styleHint(style: string): string {
  return STYLES.get(style) ?? "";
}

export function buildGenerationPrompt(template: string, style: string, transcript: string): string {
  const hint = styleHint(style);
  let base = template.includes(TRANSCRIPT_TOKEN) ? template : template + "\n\nTranscript:\n" + TRANSCRIPT_TOKEN;
  base = sub(base, TRANSCRIPT_TOKEN, transcript);
  return hint ? `${base}\n\nStyle instruction: ${hint}` : base;
}

export function buildReducePrompt(template: string, style: string, partialNotes: string): string {
  return buildGenerationPrompt(template, style, data.reduceNotesPrefix + partialNotes);
}

export function buildMergeNotesPrompt(notes: string, first: number, last: number, total: number): string {
  const t = sub(sub(sub(data.mergeNotesPrompt, "{first}", String(first)), "{last}", String(last)), "{total}", String(total));
  return sub(t, TRANSCRIPT_TOKEN, notes);
}

export function buildChunkPrompt(chunk: string, idx: number, total: number): string {
  const t = sub(sub(data.chunkSummaryPrompt, "{idx}", String(idx)), "{total}", String(total));
  return sub(t, TRANSCRIPT_TOKEN, chunk);
}

// ---- library ---------------------------------------------------------------
export interface SavedPrompt {
  id: string;
  name: string;
  text: string;
  builtin: boolean;
  category: string;
  favorite: boolean;
}

function randomHex(n: number): string {
  let s = "";
  while (s.length < n) s += Math.floor(Math.random() * 16).toString(16);
  return s;
}

export function promptId(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "prompt";
  return `${slug}-${randomHex(6)}`;
}

export function builtinText(name: string): string | null {
  return BUILTIN_PROMPTS.find((p) => p.name === name)?.text ?? null;
}

export function isModified(p: SavedPrompt): boolean {
  const shipped = p.builtin ? builtinText(p.name) : null;
  return shipped !== null && shipped !== p.text;
}

export function uniqueName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  const base = (name ?? "").trim() || "Untitled";
  if (!t.has(base.toLowerCase())) return base;
  let n = 2;
  while (t.has(`${base} (${n})`.toLowerCase())) n += 1;
  return `${base} (${n})`;
}

export function copyName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  let cand = `${name} (copy)`;
  let n = 2;
  while (t.has(cand.toLowerCase())) { cand = `${name} (copy ${n})`; n += 1; }
  return cand;
}

export interface PromptRecord { name: string; category: string; text: string; favorite: boolean; }

/** Read a prompt export (.json) or one prompt (.txt/.md, named after the file). */
export function parsePromptFile(fileName: string, raw: string): { records: unknown[]; invalid: number } {
  const dot = fileName.lastIndexOf(".");
  const ext = dot >= 0 ? fileName.slice(dot).toLowerCase() : "";
  const text = raw.replace(/^\ufeff/, "");
  if (ext === ".json") {
    const d: unknown = JSON.parse(text);
    if (d && typeof d === "object" && !Array.isArray(d) && Array.isArray((d as { prompts?: unknown }).prompts))
      return { records: (d as { prompts: unknown[] }).prompts, invalid: 0 };
    if (Array.isArray(d)) return { records: d, invalid: 0 };
    if (d && typeof d === "object") return { records: [d], invalid: 0 };
    throw new Error("This file doesn't contain prompts.");
  }
  if (ext === ".txt" || ext === ".md") {
    const base = fileName.slice(fileName.lastIndexOf("/") + 1, dot);
    return { records: [{ name: base.split("_").join(" ").trim(), text }], invalid: 0 };
  }
  throw new Error(`Unsupported file type: ${ext || "(no extension)"} - use a .json prompt export, or a .txt / .md file`);
}

export interface PromptImportPlan { toAdd: SavedPrompt[]; skipped: string[]; renamed: Array<[string, string]>; invalid: number; }

/** Decide what an import adds: identical prompts are skipped, a different one
 *  with a taken name becomes "Name (2)"; imported prompts are never built-in. */
export function planPromptImport(existing: SavedPrompt[], records: unknown[]): PromptImportPlan {
  const plan: PromptImportPlan = { toAdd: [], skipped: [], renamed: [], invalid: 0 };
  const have = new Set(existing.map((p) => `${p.name.trim().toLowerCase()}\u0000${p.category.trim().toLowerCase()}\u0000${p.text}`));
  const names = existing.map((p) => p.name);
  for (const rec of records) {
    if (!rec || typeof rec !== "object" || Array.isArray(rec)) { plan.invalid += 1; continue; }
    const r = rec as Record<string, unknown>;
    if (typeof r.text !== "string" || !r.text.trim()) { plan.invalid += 1; continue; }
    const text = r.text.split("\r\n").join("\n").split("\r").join("\n");
    const name = String(r.name ?? "").trim() || "Imported prompt";
    const category = String(r.category ?? "").trim() || "General";
    const key = `${name.toLowerCase()}\u0000${category.toLowerCase()}\u0000${text}`;
    if (have.has(key)) { plan.skipped.push(name); continue; }
    const newName = uniqueName(name, names);
    if (newName !== name) plan.renamed.push([name, newName]);
    plan.toAdd.push({ id: promptId(newName), name: newName, text, builtin: false, category, favorite: r.favorite === true });
    have.add(`${newName.toLowerCase()}\u0000${category.toLowerCase()}\u0000${text}`);
    names.push(newName);
  }
  return plan;
}

export function buildPromptExport(prompts: SavedPrompt[]): string {
  return JSON.stringify({
    mico360_prompts: 1,
    prompts: prompts.map((p) => ({ name: p.name, category: p.category, favorite: p.favorite, text: p.text })),
  }, null, 2);
}
```

- [ ] **Step 4: `android/src/core/meetingTypes.ts`**

```ts
/** Meeting-type presets: a prompt + output style + optional company, by name
 *  (port of desktop core/meeting_types.py). */
import data from "./data/desktop.json";

export interface MeetingType { name: string; prompt_name: string; style: string; profile_name: string; }

export const BUILTIN_MEETING_TYPES: MeetingType[] = data.meetingTypes.map((t) => ({ ...t }));

/** Keep only well-formed entries (a bad one is skipped, not the whole list). */
export function sanitizeMeetingTypes(raw: unknown): MeetingType[] {
  if (!Array.isArray(raw)) return [];
  const out: MeetingType[] = [];
  for (const d of raw) {
    if (!d || typeof d !== "object" || Array.isArray(d)) continue;
    const r = d as Record<string, unknown>;
    const s = (k: string) => (r[k] === null || r[k] === undefined ? "" : String(r[k]));
    if (!s("name").trim()) continue;
    out.push({ name: s("name"), prompt_name: s("prompt_name"), style: s("style"), profile_name: s("profile_name") });
  }
  return out;
}
```

- [ ] **Step 5: Run the tests**

Run: `cd android && npx vitest run test/prompts.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/prompts.ts android/src/core/meetingTypes.ts android/test/prompts.test.ts
git commit -m "Android core: prompts, prompt library and meeting types"
```

---

### Task 6: Minutes pipeline

**Files:**
- Create: `android/src/core/pipeline.ts`, `android/test/pipeline.test.ts`

**Interfaces:**
- Consumes: `cleanTranscript`, `chunkText` (Task 4); `buildGenerationPrompt`, `buildReducePrompt`, `buildMergeNotesPrompt`, `buildChunkPrompt`, `DEFAULT_STYLE` (Task 5).
- Produces: `type ChatFn = (prompt: string, signal?: AbortSignal) => Promise<string>`, `type ProgressFn = (fraction: number, message: string) => void`, `class CancelledError`, `throwIfCancelled(signal?)`, `REDUCE_NOTES_BUDGET = 12000`, `groupNotes(notes, budget)`, `mergeNotesHierarchically(chat, notes, totalParts, opts)`, `interface PipelineOptions`, `runMinutesPipeline(chat, opts): Promise<string>`, `clearPartCache()`.

- [ ] **Step 1: Write the failing test** — `android/test/pipeline.test.ts`

```ts
import { beforeEach, describe, expect, it } from "vitest";
import { CancelledError, clearPartCache, groupNotes, runMinutesPipeline } from "../src/core/pipeline";

const longText = Array.from({ length: 400 }, (_, i) => `Point ${i} about the plan.`).join(" ");

describe("minutes pipeline", () => {
  beforeEach(() => clearPartCache());

  it("short transcripts are one request with the template and style", async () => {
    const sent: string[] = [];
    const out = await runMinutesPipeline(async (p) => { sent.push(p); return "MINUTES"; },
      { model: "m", transcript: "Hello team.", template: "T [TRANSCRIPT_HERE]", style: "Short Summary" });
    expect(out).toBe("MINUTES");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toContain("T Hello team.");
    expect(sent[0]).toContain("Style instruction:");
  });

  it("long transcripts: one request per part, then the reduce with the same template", async () => {
    const sent: string[] = [];
    await runMinutesPipeline(async (p) => { sent.push(p); return "notes"; },
      { model: "m", transcript: longText, template: "TEMPLATE [TRANSCRIPT_HERE]", chunkChars: 2000 });
    const parts = sent.filter((p) => p.includes("Transcript part"));
    expect(parts.length).toBeGreaterThan(2);
    expect(sent[sent.length - 1]).toMatch(/^TEMPLATE \[This meeting was long/);
  });

  it("a retry reuses finished parts (only the failed part is asked again)", async () => {
    let calls = 0;
    const flaky = async (p: string) => {
      calls += 1;
      if (p.includes("Transcript part 2") && calls < 10) throw new Error("boom");
      return "notes";
    };
    await expect(runMinutesPipeline(flaky, { model: "m", transcript: longText, template: "T", chunkChars: 2000 })).rejects.toThrow("boom");
    const before = calls;
    calls = 100;
    const sent: string[] = [];
    await runMinutesPipeline(async (p) => { sent.push(p); return "notes"; },
      { model: "m", transcript: longText, template: "T", chunkChars: 2000 });
    expect(sent.some((p) => p.includes("Transcript part 1 of"))).toBe(false);
    expect(before).toBeGreaterThan(1);
  });

  it("notes too long for one reduce are merged in groups first", async () => {
    const sent: string[] = [];
    await runMinutesPipeline(async (p) => { sent.push(p); return "n".repeat(900); },
      { model: "m", transcript: longText, template: "T", chunkChars: 1500, reduceBudget: 2500 });
    expect(sent.some((p) => p.startsWith("You are condensing notes"))).toBe(true);
  });

  it("cancel stops between requests", async () => {
    const ctl = new AbortController();
    const run = runMinutesPipeline(async () => { ctl.abort(); return "notes"; },
      { model: "m", transcript: longText, template: "T", chunkChars: 2000, signal: ctl.signal });
    await expect(run).rejects.toBeInstanceOf(CancelledError);
  });

  it("empty transcripts and missing models are refused", async () => {
    await expect(runMinutesPipeline(async () => "", { model: "", transcript: "x", template: "T" })).rejects.toThrow("No AI model");
    await expect(runMinutesPipeline(async () => "", { model: "m", transcript: "  um  ", template: "T" })).rejects.toThrow("empty");
  });

  it("groupNotes pairs neighbours when nothing fits", () => {
    expect(groupNotes(["aaaa", "bbbb", "cccc"], 3)).toEqual([["aaaa", "bbbb"], ["cccc"]]);
    expect(groupNotes(["a", "b", "c"], 100)).toEqual([["a", "b", "c"]]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/pipeline.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/pipeline.ts`**

```ts
/** Minutes generation pipeline (port of desktop core/generation.py):
 *  short transcripts in one request; long ones condensed per part, merged
 *  hierarchically when the notes are too long, then reduced with the SAME
 *  template. Finished parts are cached so a retry resumes where it stopped. */
import { chunkText, cleanTranscript } from "./cleaning";
import { DEFAULT_STYLE, buildChunkPrompt, buildGenerationPrompt, buildMergeNotesPrompt, buildReducePrompt } from "./prompts";

export type ChatFn = (prompt: string, signal?: AbortSignal) => Promise<string>;
export type ProgressFn = (fraction: number, message: string) => void;

export class CancelledError extends Error {
  constructor(message = "Generation cancelled.") {
    super(message);
    this.name = "CancelledError";
  }
}

export function throwIfCancelled(signal?: AbortSignal): void {
  if (signal?.aborted) throw new CancelledError();
}

export const REDUCE_NOTES_BUDGET = 12000;
const MAX_MERGE_ROUNDS = 4;
const PART_CACHE_MAX = 256;
const partCache = new Map<string, string>();

function cacheGet(key: string): string | undefined {
  const v = partCache.get(key);
  if (v !== undefined) { partCache.delete(key); partCache.set(key, v); }
  return v;
}

function cachePut(key: string, value: string): void {
  partCache.delete(key);
  partCache.set(key, value);
  while (partCache.size > PART_CACHE_MAX) partCache.delete(partCache.keys().next().value as string);
}

export function clearPartCache(): void {
  partCache.clear();
}

/** Pack consecutive notes into groups whose joined size stays <= budget. */
export function groupNotes(notes: string[], budget: number): string[][] {
  let groups: string[][] = [];
  let cur: string[] = [];
  let size = 0;
  for (const n of notes) {
    const add = n.length + 2;
    if (cur.length && size + add > budget) { groups.push(cur); cur = []; size = 0; }
    cur.push(n);
    size += add;
  }
  if (cur.length) groups.push(cur);
  if (groups.length === notes.length && notes.length > 1) {
    groups = [];
    for (let i = 0; i < notes.length; i += 2) groups.push(notes.slice(i, i + 2));
  }
  return groups;
}

export async function mergeNotesHierarchically(
  chat: ChatFn, notes: string[], totalParts: number,
  opts: { budget?: number; progress?: ProgressFn; signal?: AbortSignal; span?: [number, number] } = {},
): Promise<string[]> {
  const budget = opts.budget ?? REDUCE_NOTES_BUDGET;
  const [lo, hi] = opts.span ?? [0.75, 0.85];
  const total = (xs: string[]) => xs.reduce((s, n) => s + n.length + 2, 0);
  let level = [...notes];
  let ranges: Array<[number, number]> = notes.map((_n, i) => [i + 1, i + 1]);
  let rounds = 0;
  while (total(level) > budget && level.length > 1 && rounds < MAX_MERGE_ROUNDS) {
    rounds += 1;
    const groups = groupNotes(level, budget);
    const newLevel: string[] = [];
    const newRanges: Array<[number, number]> = [];
    let idx = 0;
    for (let gi = 1; gi <= groups.length; gi++) {
      const g = groups[gi - 1];
      const first = ranges[idx][0];
      const last = ranges[idx + g.length - 1][1];
      idx += g.length;
      if (g.length === 1) { newLevel.push(g[0]); newRanges.push([first, last]); continue; }
      throwIfCancelled(opts.signal);
      opts.progress?.(lo + ((hi - lo) * (gi - 1)) / Math.max(1, groups.length),
        `Combining notes for parts ${first}–${last} of ${totalParts}…`);
      const merged = await chat(buildMergeNotesPrompt(g.join("\n\n"), first, last, totalParts), opts.signal);
      newLevel.push(`### Parts ${first}–${last}\n${merged}`);
      newRanges.push([first, last]);
    }
    const noProgress = newLevel.length >= level.length;
    level = newLevel;
    ranges = newRanges;
    if (noProgress) break;
  }
  return level;
}

export interface PipelineOptions {
  model: string;
  transcript: string;
  template: string;
  style?: string;
  removeFillers?: boolean;
  chunkChars?: number;
  reduceBudget?: number;
  progress?: ProgressFn;
  signal?: AbortSignal;
}

export async function runMinutesPipeline(chat: ChatFn, o: PipelineOptions): Promise<string> {
  if (!o.model) throw new Error("No AI model selected.");
  const style = o.style ?? DEFAULT_STYLE;
  const chunkChars = o.chunkChars ?? 6000;
  o.progress?.(0.03, "Cleaning transcript…");
  const cleaned = cleanTranscript(o.transcript, o.removeFillers ?? true);
  if (!cleaned.trim()) throw new Error("Transcript is empty after cleaning.");
  const chunks = chunkText(cleaned, chunkChars);

  if (chunks.length <= 1) {
    o.progress?.(0.15, `Generating ${style} with ${o.model} — the first request can take up to a minute while the model loads…`);
    const result = await chat(buildGenerationPrompt(o.template, style, cleaned), o.signal);
    o.progress?.(1, "Minutes generated.");
    return result;
  }

  const notes: string[] = [];
  const n = chunks.length;
  for (let i = 1; i <= n; i++) {
    throwIfCancelled(o.signal);
    const note = i === 1 ? " (the first request can take up to a minute)" : "";
    o.progress?.(0.1 + (0.65 * (i - 1)) / n, `Analyzing part ${i} of ${n}…${note}`);
    const p = buildChunkPrompt(chunks[i - 1], i, n);
    const key = `${o.model}\u0000${p}`;
    let part = cacheGet(key);
    if (part === undefined) {
      part = await chat(p, o.signal);
      if ((part ?? "").trim()) cachePut(key, part);   // never cache an empty answer
    }
    notes.push(`### Part ${i}\n` + part);
  }

  const budget = o.reduceBudget ?? Math.max(REDUCE_NOTES_BUDGET, 2 * chunkChars);
  const merged = await mergeNotesHierarchically(chat, notes, n, { budget, progress: o.progress, signal: o.signal });
  throwIfCancelled(o.signal);
  o.progress?.(0.85, `Composing ${style}…`);
  const result = await chat(buildReducePrompt(o.template, style, merged.join("\n\n")), o.signal);
  o.progress?.(1, "Minutes generated.");
  return result;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd android && npx vitest run test/pipeline.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/pipeline.ts android/test/pipeline.test.ts
git commit -m "Android core: minutes pipeline with part cache and hierarchical merge"
```

---

### Task 7: MICO360 Connect client (API guide 2.6.7)

**Files:**
- Create: `android/src/core/cloudClient.ts`, `android/test/cloudClient.test.ts`

**Interfaces:**
- Consumes: `runMinutesPipeline`, `REDUCE_NOTES_BUDGET`, `CancelledError`, `ProgressFn` (Task 6).
- Produces: `CONNECT_BASE_URL`, `CONNECT_DEFAULT_MODEL`, `MAX_PROMPT_CHARS = 32000`, `interface HttpRequest {url, method, headers, body?, timeoutMs, signal?}`, `interface HttpResponse {status, headers (lower-case keys), text, url}`, `type HttpTransport`, `class TransportError {timeout: boolean}`, `class CloudError`, `fetchTransport`, `sleep(ms, signal?)`, `class ConnectEndpoint {secureBase, base(), isEncrypted(), probeHttps(transport?, timeoutMs?)}`, `defaultEndpoint`, `looksLikeConnect(status, text)`, `interface CloudStatus {running, models, error}`, `class CloudClient {constructor(opts: CloudClientOptions); warnings: string[]; baseUrl; isEncrypted(); status(signal?); chat(prompt, signal?); generateMinutes(transcript, template, opts)}`.

- [ ] **Step 1: Write the failing test** — `android/test/cloudClient.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { CancelledError } from "../src/core/pipeline";
import {
  CloudClient, CloudError, ConnectEndpoint, type HttpRequest, type HttpResponse, TransportError,
} from "../src/core/cloudClient";

type Step = HttpResponse | Error | ((r: HttpRequest) => HttpResponse);
const OK: HttpResponse = { status: 200, headers: {}, url: "", text: JSON.stringify({ choices: [{ message: { content: " Minutes OK. " } }] }) };
const res = (status: number, body: unknown, headers: Record<string, string> = {}): HttpResponse =>
  ({ status, headers, url: "", text: typeof body === "string" ? body : JSON.stringify(body) });

function server(steps: Step[]) {
  const requests: HttpRequest[] = [];
  const queue = [...steps];
  const transport = async (r: HttpRequest) => {
    requests.push(r);
    const s = queue.length > 1 ? queue.shift()! : queue[0];
    if (s instanceof Error) throw s;
    const out = typeof s === "function" ? s(r) : s;
    return { ...out, url: out.url || r.url };
  };
  return { requests, transport, posts: () => requests.filter((r) => r.method === "POST") };
}

function client(t: ReturnType<typeof server>, extra: Partial<ConstructorParameters<typeof CloudClient>[0]> = {}) {
  const waits: number[] = [];
  const c = new CloudClient({
    key: "mico_test", model: "m", baseUrl: "http://srv:5310/v1", transport: t.transport,
    retry: { backoffBaseMs: 5, jobPollIntervalMs: 1 },
    sleep: async (ms) => { waits.push(ms); },
    ...extra,
  });
  return { c, waits };
}

const JOB_504 = res(504, { error: { message: "The request did not complete within 300 seconds (job JOB-GW1-20261015-000042, correlation id REQ-ABC123).", type: "timeout", code: null } });

describe("request shape (§1, §5, §11)", () => {
  it("POSTs JSON with the Bearer key, ids, and only the fields the platform reads", async () => {
    const s = server([OK]);
    expect(await client(s).c.chat("hello")).toBe("Minutes OK.");
    const r = s.posts()[0];
    expect(r.url).toBe("http://srv:5310/v1/chat/completions");
    expect(r.headers.Authorization).toBe("Bearer mico_test");
    expect(r.headers["Content-Type"]).toBe("application/json");
    expect(r.headers.Accept).toBe("application/json");
    expect(r.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_\-:.]{1,128}$/);
    expect(r.headers["X-Mico-Correlation-Id"]).toMatch(/^[A-Za-z0-9_\-:.]{1,64}$/);
    expect(JSON.parse(r.body!)).toEqual({ model: "m", messages: [{ role: "user", content: "hello" }], stream: false });
    expect(r.timeoutMs).toBeGreaterThan(300_000);
  });

  it("a retry repeats the same ids; a new call gets new ones", async () => {
    const s = server([res(503, { error: { message: "No connected node provides the 'Chat' capability." } }, { "retry-after": "1" }), OK]);
    const { c, waits } = client(s);
    await c.chat("one");
    await c.chat("two");
    const keys = s.posts().map((r) => r.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
    expect(waits).toEqual([1000]);
  });
});

describe("refusals (§8)", () => {
  it("503 without Retry-After is final and shows the server's reason", async () => {
    const s = server([res(503, { error: { message: "Requested model 'm' is currently unavailable. No alternative model is available." } })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/No alternative model/);
    expect(s.posts()).toHaveLength(1);
  });
  it("a long Retry-After (quota) is reported with the wait, never retried early", async () => {
    const s = server([res(429, { error: { message: "Quota exceeded: tokens per day." } }, { "retry-after": "7200" })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/Quota exceeded.*2 hours/s);
    expect(s.posts()).toHaveLength(1);
  });
  it("errors quote the correlation id", async () => {
    const s = server([res(400, { error: { message: "n must be 1." } }, { "x-mico-correlation-id": "REQ-77AA" })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/n must be 1\..*REQ-77AA/s);
  });
  it("empty-body statuses get a hint", async () => {
    const s = server([res(415, "")]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/415.*JSON/s);
  });
  it("a timeout is retried, then reported in plain words", async () => {
    const s = server([new TransportError("the server did not respond in time", true)]);
    await expect(client(s, { retry: { maxAttempts: 2, backoffBaseMs: 1 } }).c.chat("hi")).rejects.toThrow(/did not respond in time/);
    expect(s.posts()).toHaveLength(2);
  });
  it("cancel while waiting to retry", async () => {
    const ctl = new AbortController();
    const s = server([res(503, {}, { "retry-after": "20" })]);
    const c = new CloudClient({ key: "k", baseUrl: "http://srv/v1", transport: s.transport,
      sleep: async () => { ctl.abort(); throw new CancelledError(); } });
    await expect(c.chat("hi", ctl.signal)).rejects.toBeInstanceOf(CancelledError);
  });
  it("no key in this build", async () => {
    await expect(new CloudClient({ key: "" }).chat("hi")).rejects.toThrow(/isn't enabled in this build/);
  });
});

describe("504: collect the job, never resubmit (§5, §8)", () => {
  it("collects the answer with the native header", async () => {
    const s = server([JOB_504,
      res(200, { jobId: "JOB-GW1-20261015-000042", state: "Running", output: null }),
      res(200, { jobId: "JOB-GW1-20261015-000042", state: "Completed", output: " Collected minutes. ", error: null })]);
    expect(await client(s).c.chat("hi")).toBe("Collected minutes.");
    const polls = s.requests.filter((r) => r.method === "GET");
    expect(s.posts()).toHaveLength(1);
    expect(polls.map((r) => r.url)).toEqual(Array(2).fill("http://srv:5310/api/v1/jobs/JOB-GW1-20261015-000042"));
    expect(polls[0].headers["X-Mico-Api-Key"]).toBe("mico_test");
    expect(polls[0].headers.Authorization).toBeUndefined();
  });
  it("a failed job reports its error", async () => {
    const s = server([JOB_504, res(200, { state: "Failed", error: "Model ran out of memory." })]);
    await expect(client(s).c.chat("hi")).rejects.toThrow(/ran out of memory/);
    expect(s.posts()).toHaveLength(1);
  });
  it("a job that never started is sent again", async () => {
    const s = server([JOB_504, res(200, { state: "Cancelled", error: "Cancelled before a node started it." }), OK]);
    expect(await client(s).c.chat("hi")).toBe("Minutes OK.");
    expect(s.posts()).toHaveLength(2);
  });
});

describe("limits and warnings (§5, §6, §11)", () => {
  it("refuses a prompt over 32,000 characters before sending", async () => {
    const s = server([OK]);
    await expect(client(s).c.chat("x".repeat(32_001))).rejects.toThrow(/32,000/);
    expect(s.requests).toHaveLength(0);
  });
  it("every pipeline request fits the limit, even a 2-hour meeting with a long template", async () => {
    const s = server([res(200, { choices: [{ message: { content: "Notes." } }] })]);
    const transcript = Array.from({ length: 3000 }, (_, i) => `Sentence ${i} about the budget and the plan.`).join(" ");
    await client(s).c.generateMinutes(transcript, "Write minutes. ".repeat(300) + "[TRANSCRIPT_HERE]", { chunkChars: 20000 });
    const sizes = s.posts().map((r) => JSON.parse(r.body!).messages[0].content.length);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(32_000);
    expect(sizes.length).toBeGreaterThan(3);
  });
  it("a model substitution becomes one warning", async () => {
    const s = server([res(200, { model: "qwen2.5:0.5b", warning: "Requested model 'm' is unavailable; processing on 'qwen2.5:0.5b' instead.",
      choices: [{ message: { content: "OK" } }] }, { "x-mico-model-warning": "Requested model 'm' is unavailable; processing on 'qwen2.5:0.5b' instead." })]);
    const { c } = client(s);
    await c.chat("hi");
    expect(c.warnings).toEqual(["Requested model 'm' is unavailable; processing on 'qwen2.5:0.5b' instead."]);
  });
});

describe("TLS (§9)", () => {
  it("a 307 to HTTPS on the same host switches the endpoint", async () => {
    const ep = new ConnectEndpoint("http://ai.mico360.com:5310/v1", []);
    const s = server([(r) => r.url.startsWith("http://")
      ? res(307, "", { location: "https://ai.mico360.com:5311/v1/chat/completions" }) : OK]);
    const c = new CloudClient({ key: "k", endpoint: ep, transport: s.transport, sleep: async () => {} });
    expect(await c.chat("hi")).toBe("Minutes OK.");
    expect(s.requests[s.requests.length - 1].url).toBe("https://ai.mico360.com:5311/v1/chat/completions");
    expect(ep.isEncrypted()).toBe(true);
  });
  it("an HTTP stack that followed the redirect is noticed from the final URL", async () => {
    const ep = new ConnectEndpoint("http://ai.mico360.com:5310/v1", []);
    const s = server([{ ...OK, url: "https://ai.mico360.com:5311/v1/chat/completions" }]);
    await new CloudClient({ key: "k", endpoint: ep, transport: s.transport }).chat("hi");
    expect(ep.isEncrypted()).toBe(true);
  });
  it("a redirect to another host is refused", async () => {
    const s = server([res(307, "", { location: "https://other.example/v1/chat/completions" })]);
    await expect(client(s).c.chat("hi")).rejects.toBeInstanceOf(CloudError);
    expect(s.requests.every((r) => !r.url.includes("other.example"))).toBe(true);
  });
  it("the HTTPS probe accepts only the real API", async () => {
    const ep = new ConnectEndpoint("http://h:5310/v1", ["https://site.example/v1", "https://h:5311/v1"]);
    const ok = await ep.probeHttps(async (r) => r.url.startsWith("https://site")
      ? res(200, "<html>website</html>") : res(401, { error: { message: "Invalid or missing API key." } }));
    expect(ok).toBe(true);
    expect(ep.base()).toBe("https://h:5311/v1");
  });
});

describe("status", () => {
  it("lists models sorted, and explains a rejected key", async () => {
    const s = server([res(200, { object: "list", data: [{ id: "b" }, { id: "a" }] })]);
    expect(await client(s).c.status()).toEqual({ running: true, models: ["a", "b"], error: "" });
    const s2 = server([res(401, { error: { message: "x" } })]);
    expect((await client(s2).c.status()).error).toMatch(/rejected the API key/);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/cloudClient.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/cloudClient.ts`**

```ts
/** MICO360 Connect client (OpenAI-compatible surface), following
 *  MICO360 Connect/docs/API-Guide.md 2.6.7 exactly — see spec §5.1. */
import data from "./data/desktop.json";
import { CancelledError, type ProgressFn, REDUCE_NOTES_BUDGET, runMinutesPipeline } from "./pipeline";

export const CONNECT_BASE_URL: string = data.connect.baseUrl;
export const CONNECT_HTTPS_CANDIDATES: string[] = data.connect.httpsCandidates;
export const CONNECT_DEFAULT_MODEL: string = data.connect.defaultModel;

export const MAX_PROMPT_CHARS = 32_000;            // §5/§11: the conversation as a whole
export const PROMPT_OVERHEAD = 1_500;
export const CHAT_TIMEOUT_MS = 330_000;            // §7: the server waits 300 s, then 504
export const STATUS_TIMEOUT_MS = 4_000;

export interface RetryPolicy {
  maxAttempts: number; backoffBaseMs: number; maxSingleWaitMs: number; maxTotalWaitMs: number;
  jobPollIntervalMs: number; jobPollDeadlineMs: number;
}
export const DEFAULT_RETRY: RetryPolicy = {
  maxAttempts: 4, backoffBaseMs: 2000, maxSingleWaitMs: 30_000, maxTotalWaitMs: 60_000,
  jobPollIntervalMs: 3000, jobPollDeadlineMs: 600_000,
};

export interface HttpRequest {
  url: string; method: "GET" | "POST"; headers: Record<string, string>; body?: string;
  timeoutMs: number; signal?: AbortSignal;
}
export interface HttpResponse { status: number; headers: Record<string, string>; text: string; url: string; }
export type HttpTransport = (req: HttpRequest) => Promise<HttpResponse>;

export class TransportError extends Error {
  constructor(message: string, readonly timeout: boolean) { super(message); this.name = "TransportError"; }
}
export class CloudError extends Error {
  constructor(message: string) { super(message); this.name = "CloudError"; }
}

export const fetchTransport: HttpTransport = async (req) => {
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, req.timeoutMs);
  const onAbort = () => ctl.abort();
  req.signal?.addEventListener("abort", onAbort);
  try {
    const r = await fetch(req.url, { method: req.method, headers: req.headers, body: req.body, signal: ctl.signal });
    const text = await r.text();
    const headers: Record<string, string> = {};
    r.headers.forEach((v, k) => { headers[k.toLowerCase()] = v; });
    return { status: r.status, headers, text, url: r.url || req.url };
  } catch (e) {
    if (req.signal?.aborted) throw new CancelledError();
    if (timedOut) throw new TransportError("the server did not respond in time", true);
    throw new TransportError(e instanceof Error && e.message ? e.message : "the connection failed", false);
  } finally {
    clearTimeout(timer);
    req.signal?.removeEventListener("abort", onAbort);
  }
};

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new CancelledError()); return; }
    const onAbort = () => { clearTimeout(t); reject(new CancelledError()); };
    const t = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

const trimSlash = (u: string) => u.replace(/\/+$/, "");

/** host and path of an http(s) URL — RN's URL class lacks most properties. */
function splitUrl(u: string): { scheme: string; host: string; hostname: string; path: string } | null {
  const m = /^(https?):\/\/([^/?#]+)([^?#]*)/i.exec(u);
  if (!m) return null;
  return { scheme: m[1].toLowerCase(), host: m[2], hostname: m[2].replace(/:\d+$/, "").toLowerCase(), path: m[3] || "/" };
}

export function looksLikeConnect(status: number, text: string): boolean {
  let d: unknown;
  try { d = JSON.parse(text || ""); } catch { return false; }
  if (!d || typeof d !== "object" || Array.isArray(d)) return false;
  const o = d as Record<string, unknown>;
  if (status === 200) return Array.isArray(o.data);
  if (status === 401 || status === 403) return typeof o.error === "string" || (!!o.error && typeof o.error === "object");
  return false;
}

/** The plain-HTTP endpoint until the server offers TLS (§9); shared by clients. */
export class ConnectEndpoint {
  secureBase: string | null = null;
  constructor(readonly plainBase: string = CONNECT_BASE_URL, readonly candidates: string[] = CONNECT_HTTPS_CANDIDATES) {}
  base(): string { return trimSlash(this.secureBase ?? this.plainBase); }
  isEncrypted(): boolean { return this.base().startsWith("https://"); }
  async probeHttps(transport: HttpTransport = fetchTransport, timeoutMs = 4000): Promise<boolean> {
    for (const cand of this.candidates) {
      if (!cand.toLowerCase().startsWith("https://")) continue;
      try {
        const r = await transport({ url: trimSlash(cand) + "/models", method: "GET", headers: { Accept: "application/json" }, timeoutMs });
        if (looksLikeConnect(r.status, r.text)) { this.secureBase = trimSlash(cand); return true; }
      } catch { /* no TLS, unreachable, invalid certificate */ }
    }
    return false;
  }
}
export const defaultEndpoint = new ConnectEndpoint();

function randomHex(n: number): string {
  const bytes = new Uint8Array(n / 2);
  const c = (globalThis as { crypto?: { getRandomValues?: (a: Uint8Array) => Uint8Array } }).crypto;
  if (c?.getRandomValues) c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function newRequestIds(): { idempotencyKey: string; correlationId: string } {
  const u = randomHex(32);
  return { idempotencyKey: `mico360-meetings-${u}`, correlationId: `MM-${u.slice(0, 24)}` };
}

export function originOf(base: string): string {
  const b = trimSlash(base);
  for (const suffix of ["/openai/v1", "/v1"]) if (b.endsWith(suffix)) return b.slice(0, -suffix.length);
  return b;
}

const header = (r: HttpResponse, name: string) => (r.headers[name.toLowerCase()] ?? "").trim();

function retryAfterMs(r: HttpResponse): number | null {
  const raw = header(r, "retry-after");
  if (!raw) return null;
  const n = Number(raw);
  if (Number.isFinite(n)) return Math.max(0, n * 1000);
  const t = Date.parse(raw);
  return Number.isNaN(t) ? null : Math.max(0, t - Date.now());
}

function errorDetail(r: HttpResponse): string {
  try {
    const e = (JSON.parse(r.text) as { error?: unknown })?.error;
    if (typeof e === "string") return e;
    if (e && typeof e === "object" && typeof (e as { message?: unknown }).message === "string") return (e as { message: string }).message;
  } catch { /* empty or non-JSON body */ }
  return "";
}

export function waitWords(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 90) return `${s} seconds`;
  if (s < 90 * 60) return `${Math.round(s / 60)} minutes`;
  return `${Math.round(s / 3600)} hours`;
}

const HINTS: Record<number, string> = {
  400: "the request was not accepted",
  401: "the API key was rejected (missing, disabled, expired or deleted)",
  403: "this key is not permitted to use that capability or model",
  404: "the address is not the MICO360 AI API — check the base URL ends in /v1",
  405: "the request used the wrong method",
  413: "the request is too large — lower the chunk size in Settings or shorten the template",
  415: "the request was not sent as JSON",
  429: "rate limit or quota exceeded — try again shortly",
  500: "the server had an internal error",
  502: "the AI node could not complete the request — try again shortly",
  503: "no AI node can serve this request right now",
  504: "the server timed out waiting for an AI node",
};
const RETRY_STATUSES = new Set([429, 502, 503]);
const FINAL_JOB_STATES = new Set(["Completed", "Failed", "TimedOut", "Cancelled"]);
const JOB_ID_RE = /\bjob\s+(JOB-[A-Za-z0-9_\-:.]+?)[,)\s]/;
const REDIRECTS = new Set([301, 302, 303, 307, 308]);
const REDIRECT_REFUSED = "MICO360 Connect redirected the request to another address, which is not followed (the API key would go with it).";

export interface CloudStatus { running: boolean; models: string[]; error: string; }

export interface CloudClientOptions {
  key: string;
  model?: string;
  baseUrl?: string;
  endpoint?: ConnectEndpoint;
  transport?: HttpTransport;
  retry?: Partial<RetryPolicy>;
  userAgent?: string;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

export interface GenerateOptions {
  style?: string; removeFillers?: boolean; chunkChars?: number; progress?: ProgressFn; signal?: AbortSignal;
}

export class CloudClient {
  readonly key: string;
  model: string;
  warnings: string[] = [];
  private explicitBase: string;
  private readonly endpoint: ConnectEndpoint;
  private readonly transport: HttpTransport;
  private readonly retry: RetryPolicy;
  private readonly userAgent: string;
  private readonly wait: (ms: number, signal?: AbortSignal) => Promise<void>;

  constructor(o: CloudClientOptions) {
    this.key = o.key ?? "";
    this.model = o.model || CONNECT_DEFAULT_MODEL;
    this.explicitBase = trimSlash(o.baseUrl ?? "");
    this.endpoint = o.endpoint ?? defaultEndpoint;
    this.transport = o.transport ?? fetchTransport;
    this.retry = { ...DEFAULT_RETRY, ...o.retry };
    this.userAgent = o.userAgent ?? "MICO360 Meetings (Android)";
    this.wait = o.sleep ?? sleep;
  }

  get baseUrl(): string { return this.explicitBase || this.endpoint.base(); }
  isEncrypted(): boolean { return this.baseUrl.startsWith("https://"); }

  async status(signal?: AbortSignal): Promise<CloudStatus> {
    if (!this.key) return { running: false, models: [], error: "MICO360 Cloud isn't enabled in this build." };
    try {
      const r = await this.transport({ url: this.baseUrl + "/models", method: "GET", timeoutMs: STATUS_TIMEOUT_MS, signal,
        headers: { Authorization: `Bearer ${this.key}`, Accept: "application/json", "User-Agent": this.userAgent } });
      if (r.status === 200) {
        const d = JSON.parse(r.text) as { data?: Array<{ id?: unknown }> };
        const models = (Array.isArray(d?.data) ? d.data : []).map((m) => m?.id)
          .filter((x): x is string => typeof x === "string" && x.length > 0).sort();
        return { running: true, models, error: "" };
      }
      const error = r.status === 401 ? "MICO360 Connect rejected the API key (401)."
        : r.status === 403 ? "This key is not permitted to list models (403)."
        : `MICO360 Connect returned HTTP ${r.status}.`;
      return { running: false, models: [], error };
    } catch (e) {
      if (e instanceof CancelledError) throw e;
      return { running: false, models: [], error: `Could not reach MICO360 Connect: ${e instanceof Error ? e.message : String(e)}` };
    }
  }

  async chat(prompt: string, signal?: AbortSignal): Promise<string> {
    if (signal?.aborted) throw new CancelledError();
    if (!this.key) throw new CloudError("Minutes need MICO360 Cloud, which isn't enabled in this build.");
    if (prompt.length > MAX_PROMPT_CHARS) {
      throw new CloudError(`This request is ${prompt.length.toLocaleString("en-US")} characters; MICO360 Cloud accepts at most ` +
        `${MAX_PROMPT_CHARS.toLocaleString("en-US")}. Shorten the prompt template, or lower the chunk size in Settings so long meetings are split into smaller parts.`);
    }
    const body = JSON.stringify({ model: this.model, messages: [{ role: "user", content: prompt }], stream: false });
    const ids = newRequestIds();                      // the same for every retry of this call (§11)
    let waited = 0;
    let attempt = 0;
    let redirects = 0;
    for (;;) {
      attempt += 1;
      const url = this.baseUrl + "/chat/completions";
      let r: HttpResponse;
      try {
        r = await this.transport({ url, method: "POST", body, timeoutMs: CHAT_TIMEOUT_MS, signal, headers: {
          Authorization: `Bearer ${this.key}`, "Content-Type": "application/json", Accept: "application/json",
          "User-Agent": this.userAgent, "Idempotency-Key": ids.idempotencyKey, "X-Mico-Correlation-Id": ids.correlationId } });
      } catch (e) {
        if (e instanceof CancelledError) throw e;
        const delay = Math.min(this.retry.backoffBaseMs * 2 ** (attempt - 1), this.retry.maxSingleWaitMs);
        if (attempt >= this.retry.maxAttempts || waited + delay > this.retry.maxTotalWaitMs) throw new CloudError(transportMessage(e, attempt));
        await this.wait(delay, signal);
        waited += delay;
        continue;
      }

      if (this.followRedirect(r, url)) {
        if (++redirects > 2) throw new CloudError(REDIRECT_REFUSED);
        attempt -= 1;
        continue;
      }
      if (r.status === 200) return this.answer(r);
      if (r.status === 504) {
        const m = JOB_ID_RE.exec(errorDetail(r) + " ");
        if (!m) throw new CloudError(this.errorMessage(r, attempt, ids.correlationId));
        const job = await this.collectJob(m[1], ids.correlationId, signal);
        if (job.state === "Completed") return job.text.trim();
        if (job.state === "Cancelled" && attempt < this.retry.maxAttempts) continue;   // never started: safe to send again
        throw new CloudError(`MICO360 Connect could not finish this part (${m[1]}: ${job.text}).` + quote(r, ids.correlationId));
      }
      const delay = this.retryDelay(r, attempt, waited, ids.correlationId);
      await this.wait(delay, signal);
      waited += delay;
    }
  }

  async generateMinutes(transcript: string, template: string, o: GenerateOptions = {}): Promise<string> {
    this.warnings = [];
    const room = MAX_PROMPT_CHARS - (template ?? "").length - PROMPT_OVERHEAD;
    if (room < 1500) {
      throw new CloudError(`The prompt template is ${(template ?? "").length.toLocaleString("en-US")} characters — too long for MICO360 Cloud, ` +
        `which accepts ${MAX_PROMPT_CHARS.toLocaleString("en-US")} characters per request including the meeting text. Shorten the template.`);
    }
    const chunk = Math.max(1500, Math.min(o.chunkChars ?? 6000, room));
    const budget = Math.min(Math.max(REDUCE_NOTES_BUDGET, 2 * chunk), room);
    return runMinutesPipeline((p, s) => this.chat(p, s), {
      model: this.model, transcript, template, style: o.style, removeFillers: o.removeFillers,
      chunkChars: chunk, reduceBudget: budget, progress: o.progress, signal: o.signal,
    });
  }

  private answer(r: HttpResponse): string {
    let d: { choices?: Array<{ message?: { content?: unknown } }>; warning?: unknown };
    try { d = JSON.parse(r.text); } catch { throw new CloudError("MICO360 Connect returned an unexpected response."); }
    for (const w of [header(r, "x-mico-model-warning"), typeof d?.warning === "string" ? d.warning : "", header(r, "x-mico-warning")]) {
      const t = w.trim();
      if (t && !this.warnings.includes(t)) this.warnings.push(t);
    }
    const msg = d?.choices?.[0]?.message;
    if (!msg || typeof msg !== "object" || !("content" in msg)) throw new CloudError("MICO360 Connect returned an unexpected response.");
    return String(msg.content ?? "").trim();
  }

  /** §9: plain HTTP is redirected to HTTPS once TLS is on. Adopt it only for
   *  HTTPS on the SAME host; refuse anything else. Returns true when the
   *  request must be sent again (a redirect the HTTP stack did not follow). */
  private followRedirect(r: HttpResponse, requested: string): boolean {
    const isRedirect = REDIRECTS.has(r.status);
    const target = isRedirect ? header(r, "location") : (r.url && r.url !== requested ? r.url : "");
    if (!target) {
      if (isRedirect) throw new CloudError(REDIRECT_REFUSED);
      return false;
    }
    const cur = splitUrl(requested);
    const next = splitUrl(target);
    if (!cur || !next || next.scheme !== "https" || next.hostname !== cur.hostname || !next.path.endsWith("/chat/completions")) {
      throw new CloudError(REDIRECT_REFUSED);
    }
    const base = trimSlash(`https://${next.host}${next.path.slice(0, -"/chat/completions".length)}`);
    if (this.explicitBase) this.explicitBase = base;
    else this.endpoint.secureBase = base;
    return isRedirect;
  }

  private retryDelay(r: HttpResponse, attempt: number, waited: number, corr: string): number {
    if (!RETRY_STATUSES.has(r.status) || attempt >= this.retry.maxAttempts) throw new CloudError(this.errorMessage(r, attempt, corr));
    const ra = retryAfterMs(r);
    if (r.status === 503 && ra === null) throw new CloudError(this.errorMessage(r, attempt, corr));
    if (ra !== null && (ra > this.retry.maxSingleWaitMs || waited + ra > this.retry.maxTotalWaitMs)) {
      throw new CloudError(this.errorMessage(r, attempt, corr) + ` Try again in about ${waitWords(ra)}.`);
    }
    const delay = ra ?? Math.min(this.retry.backoffBaseMs * 2 ** (attempt - 1), this.retry.maxSingleWaitMs);
    if (waited + delay > this.retry.maxTotalWaitMs) throw new CloudError(this.errorMessage(r, attempt, corr));
    return delay;
  }

  private errorMessage(r: HttpResponse, attempts: number, corr: string): string {
    const tried = attempts > 1 ? ` (tried ${attempts} times)` : "";
    return `MICO360 Connect error (${r.status}): ${errorDetail(r) || HINTS[r.status] || `HTTP ${r.status}`}${tried}.`
      .replace(/\.\.$/, ".") + quote(r, corr);
  }

  private async collectJob(jobId: string, corr: string, signal?: AbortSignal): Promise<{ state: string; text: string }> {
    const url = originOf(this.baseUrl) + `/api/v1/jobs/${encodeURIComponent(jobId)}`;
    const deadline = Date.now() + this.retry.jobPollDeadlineMs;
    let last = "the job could not be read";
    while (Date.now() < deadline) {
      try {
        const r = await this.transport({ url, method: "GET", timeoutMs: 30_000, signal, headers: {
          "X-Mico-Api-Key": this.key, Accept: "application/json", "User-Agent": this.userAgent, "X-Mico-Correlation-Id": corr } });
        if (r.status === 200) {
          const job = JSON.parse(r.text) as { state?: unknown; output?: unknown; error?: unknown };
          const state = String(job.state ?? "");
          if (state === "Completed") return { state, text: String(job.output ?? "") };
          if (FINAL_JOB_STATES.has(state)) return { state, text: String(job.error || state) };
        } else if (r.status === 401 || r.status === 403 || r.status === 404) {
          return { state: "Unknown", text: `the job could not be read (HTTP ${r.status})` };
        } else {
          last = `HTTP ${r.status}`;
        }
      } catch (e) {
        if (e instanceof CancelledError) throw e;
        last = e instanceof Error ? e.message : String(e);
      }
      await this.wait(this.retry.jobPollIntervalMs, signal);
    }
    return { state: "Unknown", text: `no answer within ${waitWords(this.retry.jobPollDeadlineMs)} (${last})` };
  }
}

function quote(r: HttpResponse, corr: string): string {
  const cid = header(r, "x-mico-correlation-id") || corr;
  return cid ? ` Reference for support: ${cid}.` : "";
}

function transportMessage(e: unknown, attempts: number): string {
  const tried = attempts > 1 ? ` (tried ${attempts} times)` : "";
  if (e instanceof TransportError && e.timeout) {
    return `MICO360 Connect did not respond in time${tried}. The service may be busy or your connection slow — try again shortly.`;
  }
  const reason = e instanceof Error ? e.message : String(e);
  return `Could not reach MICO360 Connect (${reason})${tried}. Check your network connection.`;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd android && npx vitest run test/cloudClient.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/cloudClient.ts android/test/cloudClient.test.ts
git commit -m "Android core: MICO360 Connect client per the API guide"
```

---

### Task 8: Action items

**Files:**
- Create: `android/src/core/dates.ts`, `android/src/core/actionItems.ts`, `android/test/actionItems.test.ts`

**Interfaces:**
- Consumes: `parse` (Task 3); `@noble/hashes/legacy.js` `md5`, `@noble/hashes/utils.js` `bytesToHex`, `utf8ToBytes`.
- Produces (`dates.ts`): `type ISODate`, `makeDate(y,m,d): ISODate|null`, `addDays(iso, n)`, `todayLocal(now?)`, `localDateOf(ms)`, `weekday(iso): number` (Mon=0), `fromIso(s): ISODate|null`.
- Produces (`actionItems.ts`): `interface ActionItem {task, owner, deadline, status, priority, notes, meetingId, meetingTitle, meetingDate, okey, occ}`, `type ItemOverride`, `STATUS_CYCLE`, `OVERDUE`, `PRIORITIES`, `isPlaceholder(s)`, `normalizeStatus`, `normalizePriority`, `parseDeadline(text, ref?)`, `extractActionItems(md)`, `itemsForMeeting(m: {id, title, createdAt, minutes})`, `applyOverride(item, ov)`, `deadlineDate(item)`, `isOverdue(item, today?)`, `effectiveStatus(item, today?)`, `reminderCounts(items, today?, withinDays?)`, `csvSafe(v)`, `buildActionItemsCsv(items)`.

- [ ] **Step 1: Write the failing test** — `android/test/actionItems.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/actionItems.json";
import * as A from "../src/core/actionItems";

const toPy = (it: A.ActionItem) => ({ task: it.task, owner: it.owner, deadline: it.deadline, status: it.status, priority: it.priority });

describe("parity with desktop tasks.py", () => {
  it("deadlines", () => { for (const c of fx.deadlines) expect([c.input, A.parseDeadline(c.input, c.ref)]).toEqual([c.input, c.output]); });
  it("status", () => { for (const c of fx.status) expect(A.normalizeStatus(c.input)).toBe(c.output); });
  it("priority", () => { for (const c of fx.priority) expect(A.normalizePriority(c.input)).toBe(c.output); });
  it("extract", () => {
    for (const c of fx.extract) {
      expect(A.extractActionItems(c.input).map(toPy)).toEqual(
        c.items.map((i: Record<string, string>) => ({ task: i.task, owner: i.owner, deadline: i.deadline, status: i.status, priority: i.priority })));
    }
  });
  it("csvSafe", () => { for (const c of fx.csvSafe) expect(A.csvSafe(c.input)).toBe(c.output); });
});

describe("tracking", () => {
  const md = "| Task | Owner | Deadline | Status |\n|---|---|---|---|\n| Send report | Ali | 2026-10-01 | Pending |\n| Send report | Bob | 2026-10-30 | Done |";
  const items = A.itemsForMeeting({ id: "m1", title: "Budget", createdAt: Date.UTC(2026, 9, 5, 12), minutes: md });
  it("same task text in one meeting gets distinct keys", () => {
    expect(items.map((i) => i.okey)).toEqual([`m1:${items[0].okey.split(":")[1]}:0`, `m1:${items[0].okey.split(":")[1]}:1`]);
  });
  it("overdue is derived and closed tasks are never overdue", () => {
    expect(A.effectiveStatus(items[0], "2026-10-15")).toBe("Overdue");
    expect(A.effectiveStatus(items[1], "2026-10-15")).toBe("Completed");
    expect(A.reminderCounts(items, "2026-09-28")).toEqual({ overdue: 0, dueSoon: 1 });
  });
  it("overrides replace edited fields; a legacy string is a status", () => {
    const it2 = A.applyOverride({ ...items[0] }, { owner: "Sara", status: "In Progress" });
    expect([it2.owner, it2.status]).toEqual(["Sara", "In Progress"]);
    expect(A.applyOverride({ ...items[0] }, "Completed").status).toBe("Completed");
  });
  it("CSV for Excel: BOM, CRLF, quoting, formula neutralised", () => {
    const csv = A.buildActionItemsCsv([{ ...items[0], task: '=HYPERLINK("x"), "q"' }]);
    expect(csv.startsWith("\ufeffTask,Responsible,Deadline,Priority,Status,Notes,Meeting,Meeting date\r\n")).toBe(true);
    expect(csv).toContain(`"'=HYPERLINK(""x""), ""q"""`);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/actionItems.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/dates.ts`**

```ts
/** Calendar dates as ISO strings "YYYY-MM-DD" (no time zone surprises). */
export type ISODate = string;

const pad = (n: number, w = 2) => String(n).padStart(w, "0");
const isLeap = (y: number) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
const daysIn = (y: number, m: number) => [31, isLeap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];

export function makeDate(y: number, m: number, d: number): ISODate | null {
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d)) return null;
  if (y < 1 || y > 9999 || m < 1 || m > 12 || d < 1 || d > daysIn(y, m)) return null;
  return `${pad(y, 4)}-${pad(m)}-${pad(d)}`;
}

function toUtc(iso: ISODate): Date {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(0);
  t.setUTCFullYear(y, m - 1, d);
  return t;
}

const fromUtc = (t: Date): ISODate => `${pad(t.getUTCFullYear(), 4)}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`;

export function addDays(iso: ISODate, n: number): ISODate {
  const t = toUtc(iso);
  t.setUTCDate(t.getUTCDate() + n);
  return fromUtc(t);
}

/** Monday = 0 … Sunday = 6 (Python date.weekday()). */
export function weekday(iso: ISODate): number {
  return (toUtc(iso).getUTCDay() + 6) % 7;
}

export function todayLocal(now: Date = new Date()): ISODate {
  return makeDate(now.getFullYear(), now.getMonth() + 1, now.getDate())!;
}

export function localDateOf(ms: number): ISODate {
  return todayLocal(new Date(ms));
}

/** Python date.fromisoformat for the forms the app meets: YYYY-MM-DD, YYYYMMDD. */
export function fromIso(s: string): ISODate | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s) ?? /^(\d{4})(\d{2})(\d{2})$/.exec(s);
  return m ? makeDate(+m[1], +m[2], +m[3]) : null;
}
```

- [ ] **Step 4: `android/src/core/actionItems.ts`**

```ts
/** Action items: extraction from minutes, statuses, deadlines, CSV
 *  (port of desktop core/tasks.py; persistence lives in the app layer). */
import { md5 } from "@noble/hashes/legacy.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";
import data from "./data/desktop.json";
import { type ISODate, addDays, fromIso, localDateOf, makeDate, todayLocal } from "./dates";
import { parse } from "./mdBlocks";
import { WORD } from "./unicode";

export const STATUS_CYCLE: string[] = data.statusCycle;
export const OVERDUE = "Overdue";
export const PRIORITIES = ["High", "Medium", "Low"];
const CLOSED = new Set(["Completed", "Cancelled"]);
const PLACEHOLDER = new Set<string>(data.placeholders);
const STATUS_SYN = new Map<string, string>(Object.entries(data.statusSynonyms));
const PRIO_SYN = new Map<string, string>(Object.entries(data.prioritySynonyms));

export interface ActionItem {
  task: string; owner: string; deadline: string; status: string; priority: string; notes: string;
  meetingId: string; meetingTitle: string; meetingDate: string; okey: string; occ: number;
}
export type ItemOverride = Partial<Pick<ActionItem, "task" | "owner" | "deadline" | "priority" | "status" | "notes">>;

export const isPlaceholder = (s: string) => PLACEHOLDER.has((s ?? "").trim().toLowerCase());

export function normalizeStatus(status: string | null | undefined): string {
  const s = (status ?? "").trim();
  return STATUS_SYN.get(s.toLowerCase()) ?? (s || "Pending");
}

export function normalizePriority(value: string | null | undefined): string {
  const s = (value ?? "").trim();
  if (!s || PLACEHOLDER.has(s.toLowerCase())) return "";
  return PRIO_SYN.get(s.toLowerCase()) ?? s;
}

// ---- deadlines (Python strptime formats, case-insensitive) ------------------
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december"];
const MON3 = MONTHS.map((m) => m.slice(0, 3));
const TOKEN: Record<string, string> = {
  Y: "(\\d{4})", m: "(1[0-2]|0[1-9]|[1-9])", d: "(3[01]|[12]\\d|0[1-9]|[1-9]| [1-9])",
  b: `(${MON3.join("|")})`, B: `(${MONTHS.join("|")})`,
};
const DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
  "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d %b, %Y", "%d %B, %Y"];
const YEARLESS_FORMATS = ["%d %b", "%d %B", "%b %d", "%B %d"];
const ORDINAL_RE = new RegExp(`(?<![${WORD}])(\\d{1,2})(st|nd|rd|th)(?![${WORD}])`, "giu");
const YEARLESS_PAST_GRACE_DAYS = 90;

function compile(fmt: string): { re: RegExp; fields: string[] } {
  let src = "";
  const fields: string[] = [];
  for (let i = 0; i < fmt.length; i++) {
    const ch = fmt[i];
    if (ch === "%") { const f = fmt[++i]; src += TOKEN[f]; fields.push(f); }
    else if (/\s/.test(ch)) { src += "\\s+"; while (i + 1 < fmt.length && /\s/.test(fmt[i + 1])) i++; }
    else src += ch.replace(/[.*+?^${}()|[\]\\/-]/g, "\\$&");
  }
  return { re: new RegExp(`^${src}$`, "i"), fields };
}
const COMPILED = DATE_FORMATS.map(compile);
const COMPILED_YEARLESS = YEARLESS_FORMATS.map((f) => compile(f + " %Y"));

function fieldsOf(c: { re: RegExp; fields: string[] }, s: string): { y: number; m: number; d: number } | null {
  const mt = c.re.exec(s);
  if (!mt) return null;
  let y = 0, m = 0, d = 0;
  c.fields.forEach((f, k) => {
    const v = mt[k + 1].trim().toLowerCase();
    if (f === "Y") y = Number(v);
    else if (f === "m") m = Number(v);
    else if (f === "d") d = Number(v);
    else if (f === "b") m = MON3.indexOf(v) + 1;
    else if (f === "B") m = MONTHS.indexOf(v) + 1;
  });
  return { y, m, d };
}

function resolveYear(month: number, day: number, ref: ISODate): ISODate | null {
  let year = Number(ref.slice(0, 4));
  const cand = makeDate(year, month, day);
  if (cand === null || cand < addDays(ref, -YEARLESS_PAST_GRACE_DAYS)) year += 1;
  for (let y = year; y < year + 8; y++) {
    const d = makeDate(y, month, day);
    if (d) return d;
  }
  return null;
}

/** Best-effort parse of a free-text deadline. `ref` (the meeting date) decides
 *  the year of a year-less deadline; it defaults to today. */
export function parseDeadline(text: string | null | undefined, ref?: ISODate | null): ISODate | null {
  let s = (text ?? "").trim();
  if (!s || PLACEHOLDER.has(s.toLowerCase())) return null;
  const iso = fromIso(s.slice(0, 10));
  if (iso) return iso;
  s = s.replace(ORDINAL_RE, (_m, n: string) => n).trim().replace(/\.+$/, "");
  s = s.replace(/\s+/g, " ");
  for (const c of COMPILED) {
    const f = fieldsOf(c, s);
    const d = f && makeDate(f.y, f.m, f.d);
    if (d) return d;
  }
  for (const c of COMPILED_YEARLESS) {
    const f = fieldsOf(c, `${s} 2000`);
    if (f && makeDate(2000, f.m, f.d)) return resolveYear(f.m, f.d, ref ?? todayLocal());
  }
  return null;
}

// ---- extraction ---------------------------------------------------------------
const clean = (v: string) => (v ?? "").split("**").join("").trim();

export function extractActionItems(minutesMd: string): ActionItem[] {
  const items: ActionItem[] = [];
  for (const blk of parse(minutesMd ?? "")) {
    if (blk.kind !== "table" || !blk.headers.length) continue;
    const hdr = blk.headers.map((h) => h.toLowerCase());
    if (!hdr.some((h) => h.includes("task") || h.includes("action"))) continue;
    const col = (keys: string[]) => hdr.findIndex((h) => keys.some((k) => h.includes(k)));
    const ciTask = col(["task", "action", "item"]);
    const ciOwner = col(["responsible", "person", "owner", "assign", "who"]);
    const ciDue = col(["deadline", "due", "date", "by when", "when"]);
    const ciStatus = col(["status", "state"]);
    const ciPrio = col(["priority", "importance", "urgency"]);
    for (const row of blk.rows) {
      const get = (ci: number) => (ci >= 0 && ci < row.length ? clean(row[ci]) : "");
      const task = get(ciTask);
      if (!task || PLACEHOLDER.has(task.toLowerCase())) continue;
      items.push({ task, owner: get(ciOwner), deadline: get(ciDue), status: get(ciStatus) || "Pending",
        priority: normalizePriority(get(ciPrio)), notes: "", meetingId: "", meetingTitle: "", meetingDate: "", okey: "", occ: 0 });
    }
  }
  return items;
}

export const taskHash = (task: string) => bytesToHex(md5(utf8ToBytes(task.trim().toLowerCase()))).slice(0, 10);

/** A meeting's items with stable per-row keys: meeting + task text + which
 *  occurrence of that text it is within the meeting. */
export function itemsForMeeting(m: { id: string; title: string; createdAt: number; minutes: string }): ActionItem[] {
  const seen = new Map<string, number>();
  return extractActionItems(m.minutes).map((it) => {
    const h = taskHash(it.task);
    const occ = seen.get(h) ?? 0;
    seen.set(h, occ + 1);
    return { ...it, meetingId: m.id, meetingTitle: m.title, meetingDate: m.createdAt ? localDateOf(m.createdAt) : "",
      occ, okey: `${m.id}:${h}:${occ}` };
  });
}

const EDITABLE = ["task", "owner", "deadline", "priority", "status", "notes"] as const;

export function applyOverride(item: ActionItem, ov: ItemOverride | string | null | undefined): ActionItem {
  if (!ov) return item;
  if (typeof ov === "string") { item.status = ov; return item; }
  for (const f of EDITABLE) if (ov[f] !== undefined && ov[f] !== null) item[f] = String(ov[f]);
  return item;
}

export function deadlineDate(item: ActionItem): ISODate | null {
  return parseDeadline(item.deadline, fromIso((item.meetingDate ?? "").slice(0, 10)));
}

export function isOverdue(item: ActionItem, today: ISODate = todayLocal()): boolean {
  if (CLOSED.has(normalizeStatus(item.status))) return false;
  const d = deadlineDate(item);
  return d !== null && d < today;
}

export function effectiveStatus(item: ActionItem, today: ISODate = todayLocal()): string {
  return isOverdue(item, today) ? OVERDUE : normalizeStatus(item.status);
}

export function reminderCounts(items: ActionItem[], today: ISODate = todayLocal(), withinDays = 7): { overdue: number; dueSoon: number } {
  let overdue = 0;
  let dueSoon = 0;
  const limit = addDays(today, withinDays);
  for (const it of items) {
    if (CLOSED.has(normalizeStatus(it.status))) continue;
    if (isOverdue(it, today)) { overdue += 1; continue; }
    const d = deadlineDate(it);
    if (d !== null && today <= d && d <= limit) dueSoon += 1;
  }
  return { overdue, dueSoon };
}

export function csvSafe(value: unknown): string {
  const s = value === null || value === undefined ? "" : String(value);
  return ["=", "+", "-", "@", "\t", "\r"].some((p) => s.startsWith(p)) ? "'" + s : s;
}

const csvField = (s: string) => (/[",\r\n]/.test(s) ? `"${s.split('"').join('""')}"` : s);

/** Excel-friendly CSV: UTF-8 BOM (Arabic intact), CRLF, every cell neutralised. */
export function buildActionItemsCsv(items: ActionItem[]): string {
  const rows = [["Task", "Responsible", "Deadline", "Priority", "Status", "Notes", "Meeting", "Meeting date"]];
  for (const i of items) {
    rows.push([i.task, i.owner, i.deadline, i.priority, i.status, i.notes, i.meetingTitle, i.meetingDate].map(csvSafe));
  }
  return "\ufeff" + rows.map((r) => r.map(csvField).join(",")).join("\r\n") + "\r\n";
}
```

- [ ] **Step 5: Run the tests**

Run: `cd android && npx vitest run test/actionItems.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/dates.ts android/src/core/actionItems.ts android/test/actionItems.test.ts
git commit -m "Android core: action items, deadlines and CSV (parity with desktop)"
```

---

### Task 9: Calendar (.ics) export

**Files:**
- Create: `android/src/core/ics.ts`, `android/test/ics.test.ts`

**Interfaces:**
- Consumes: `ActionItem`, `deadlineDate`, `normalizeStatus`, `isPlaceholder`, `taskHash` (Task 8); `addDays`.
- Produces: `icsEscape(s)`, `foldLine(line)`, `eventUid(item)`, `datedItems(items)`, `buildIcs(items, opts?: {now?: Date; reminderDays?: number; calname?: string}): string`.

- [ ] **Step 1: Write the failing test** — `android/test/ics.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/ics.json";
import type { ActionItem } from "../src/core/actionItems";
import { buildIcs, eventUid, foldLine } from "../src/core/ics";

const items: ActionItem[] = fx.items.map((i: Record<string, unknown>) => ({
  task: String(i.task), owner: String(i.owner), deadline: String(i.deadline), status: String(i.status),
  priority: String(i.priority), notes: String(i.notes), meetingId: String(i.meeting_id), meetingTitle: String(i.meeting_title),
  meetingDate: String(i.meeting_date), okey: String(i.okey), occ: Number(i.occ),
}));

describe("ics parity with desktop calendar_ics.py", () => {
  it("same calendar, line for line (DTSTAMP aside)", () => {
    const out = buildIcs(items, { now: new Date(Date.UTC(2026, 9, 15, 8, 30)) });
    const lines = out.split("\r\n").filter((l) => !l.startsWith("DTSTAMP:")).join("\r\n");
    expect(lines).toBe(fx.ics);
    expect(out).toContain("DTSTAMP:20261015T083000Z");
  });
  it("same stable UIDs", () => { expect(items.map(eventUid)).toEqual(fx.uids); });
  it("folds by UTF-8 octets, never splitting a character", () => {
    const folded = foldLine("SUMMARY:" + "ع".repeat(80));
    for (const part of folded.split("\r\n")) expect(new TextEncoder().encode(part).length).toBeLessThanOrEqual(75);
    expect(folded.split("\r\n").map((p, i) => (i ? p.slice(1) : p)).join("")).toBe("SUMMARY:" + "ع".repeat(80));
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/ics.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/ics.ts`**

```ts
/** Action items with deadlines as an RFC 5545 calendar (port of desktop
 *  core/calendar_ics.py): all-day events, optional reminder, stable UIDs. */
import { sha1 } from "@noble/hashes/legacy.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";
import { type ActionItem, deadlineDate, isPlaceholder, normalizeStatus, taskHash } from "./actionItems";
import { addDays } from "./dates";

const MAX_OCTETS = 75;

export function icsEscape(s: unknown): string {
  return String(s ?? "").split("\\").join("\\\\").split(";").join("\\;").split(",").join("\\,")
    .split("\r\n").join("\\n").split("\n").join("\\n").split("\r").join("\\n");
}

const utf8Len = (cp: number) => (cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4);

/** Fold by octets: 75 on the first line, a space + 74 on each continuation. */
export function foldLine(line: string): string {
  const out: string[] = [];
  let cur = "";
  let size = 0;
  for (const ch of line) {
    const n = utf8Len(ch.codePointAt(0)!);
    if (size + n > MAX_OCTETS) { out.push(cur); cur = " "; size = 1; }
    cur += ch;
    size += n;
  }
  out.push(cur);
  return out.join("\r\n");
}

export function eventUid(item: ActionItem): string {
  const key = item.okey || `${item.meetingId}:${taskHash(item.task)}:${item.occ}`;
  return bytesToHex(sha1(utf8ToBytes(`mico360-action:${key}`))).slice(0, 24) + "@mico360";
}

export const datedItems = (items: ActionItem[]) => items.filter((it) => deadlineDate(it) !== null);

const compact = (iso: string) => iso.split("-").join("");

export function buildIcs(items: ActionItem[], opts: { now?: Date; reminderDays?: number; calname?: string } = {}): string {
  const now = opts.now ?? new Date();
  const reminderDays = opts.reminderDays ?? 1;
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MICO360//Meetings//EN", "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH", `X-WR-CALNAME:${icsEscape(opts.calname ?? "MICO360 Action Items")}`];
  const seen = new Set<string>();
  for (const it of items) {
    const d = deadlineDate(it);
    if (d === null) continue;
    const owner = (it.owner ?? "").trim();
    const named = !!owner && !isPlaceholder(owner);
    const summary = named ? `[${owner}] ${it.task}` : it.task;
    const desc: string[] = [];
    if (named) desc.push(`Owner: ${owner}`);
    if (it.meetingTitle) desc.push(`Meeting: ${it.meetingTitle}`);
    if (it.priority) desc.push(`Priority: ${it.priority}`);
    desc.push(`Status: ${normalizeStatus(it.status)}`);
    let uid = eventUid(it);
    let n = 2;
    while (seen.has(uid)) { uid = eventUid(it).replace("@", `-${n}@`); n += 1; }
    seen.add(uid);
    lines.push("BEGIN:VEVENT", `UID:${uid}`, `DTSTAMP:${stamp}`, `DTSTART;VALUE=DATE:${compact(d)}`,
      `DTEND;VALUE=DATE:${compact(addDays(d, 1))}`, `SUMMARY:${icsEscape(summary)}`,
      "DESCRIPTION:" + desc.map(icsEscape).join("\\n"), "TRANSP:TRANSPARENT");
    if (reminderDays) {
      lines.push("BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${icsEscape("Reminder: " + it.task)}`,
        `TRIGGER:-P${reminderDays}D`, "END:VALARM");
    }
    lines.push("END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.map(foldLine).join("\r\n") + "\r\n";
}
```

- [ ] **Step 4: Run the tests**

Run: `cd android && npx vitest run test/ics.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/ics.ts android/test/ics.test.ts
git commit -m "Android core: calendar export (parity with desktop)"
```

---

### Task 10: Company profiles

**Files:**
- Create: `android/src/core/profiles.ts`, `android/src/core/csv.ts`, `android/test/profiles.test.ts`

**Interfaces:**
- Produces (`csv.ts`): `parseCsv(text): string[][]` (RFC 4180, BOM stripped).
- Produces (`profiles.ts`): `interface CompanyProfile` (snake_case keys as the desktop JSON), `DEFAULT_PROFILE`, `PDF_DESIGNS`, `coerce(key, value)`, `choice(value, allowed, def)`, `normHeader(h)`, `normalizeRecord(rec)`, `profileFromRecord(rec)`, `sanitizeProfile(p)`, `uniqueProfileName(name, taken)`, `copyProfileName(name, taken)`, `contentKey(p, logoDigest)`, `interface ProfileImportRecord {profile, logoData, logoName, logoPath}`, `parseProfileFile(fileName, text): {records: ProfileImportRecord[]; invalid: number}`, `buildProfilesJson(profiles, logos: Record<string, {name: string; base64: string}>)`.

- [ ] **Step 1: Write the failing test** — `android/test/profiles.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/profiles.json";
import { parseCsv } from "../src/core/csv";
import * as P from "../src/core/profiles";

describe("parity with desktop profiles.py", () => {
  it("coerce", () => { for (const c of fx.coerce) expect([c.key, c.value, P.coerce(c.key, c.value)]).toEqual([c.key, c.value, c.output]); });
  it("normHeader", () => { for (const c of fx.normHeader) expect(P.normHeader(c.input)).toBe(c.output); });
  it("normalizeRecord", () => { for (const c of fx.normalize) expect(P.normalizeRecord(c.input)).toEqual(c.output); });
});

describe("import", () => {
  it("reads a desktop JSON export with an embedded logo", () => {
    const json = JSON.stringify([{ id: "abc", name: "Acme", phone: 96824123456, accent_color: "red;}</style>",
      logo_path: "C:/x/logo.png", logo_name: "logo.png", logo_data: "iVBORw0KGgo=" }]);
    const { records, invalid } = P.parseProfileFile("profiles.json", json);
    expect(invalid).toBe(0);
    expect(records[0].profile).toMatchObject({ id: "", name: "Acme", phone: "96824123456", accent_color: "#8B1E1E", pdf_design: "classic" });
    expect(records[0].logoData).toBe("iVBORw0KGgo=");
  });
  it("reads CSV with friendly headers and skips blank rows", () => {
    const csv = "\ufeffCompany Name,Tel,Design\r\nAcme,123,Formal\r\n,,\r\n\"Beta, Inc\",\"4\"\"5\",modern\r\n";
    const { records } = P.parseProfileFile("p.csv", csv);
    expect(records.map((r) => [r.profile.name, r.profile.phone, r.profile.pdf_design])).toEqual([
      ["Acme", "123", "formal"], ["Beta, Inc", '4"5', "modern"]]);
  });
  it("refuses Excel with a way forward", () => {
    expect(() => P.parseProfileFile("p.xlsx", "")).toThrow(/Export the profiles as JSON from the desktop app/);
  });
  it("counts non-objects as invalid", () => {
    expect(P.parseProfileFile("p.json", "[1, null, {\"name\": \"A\"}]").invalid).toBe(2);
  });
});

describe("names and identity", () => {
  it("unique and copy names", () => {
    expect(P.uniqueProfileName("Acme", ["acme"])).toBe("Acme (2)");
    expect(P.copyProfileName("Acme", ["Acme (copy)"])).toBe("Acme (copy 2)");
  });
  it("content key ignores id and logo location but not the logo bytes", () => {
    const a = { ...P.DEFAULT_PROFILE, id: "1", name: "A", logo_path: "x" };
    const b = { ...a, id: "2", logo_path: "y" };
    expect(P.contentKey(a, "d1")).toBe(P.contentKey(b, "d1"));
    expect(P.contentKey(a, "d1")).not.toBe(P.contentKey(b, "d2"));
  });
  it("export embeds logos so the desktop can import them", () => {
    const out = JSON.parse(P.buildProfilesJson([{ ...P.DEFAULT_PROFILE, id: "1", name: "A", logo_path: "file:///l.png" }],
      { "1": { name: "l.png", base64: "AAAA" } }));
    expect(out[0]).toMatchObject({ name: "A", logo_name: "l.png", logo_data: "AAAA" });
  });
  it("a saved profile never keeps a blank name", () => {
    expect(P.sanitizeProfile({ ...P.DEFAULT_PROFILE, name: "  " }).name).toBe("Company");
  });
});

it("parseCsv handles quotes, CRLF and embedded newlines", () => {
  expect(parseCsv('a,"b\r\nc",d\r\n1,2,3')).toEqual([["a", "b\r\nc", "d"], ["1", "2", "3"]]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/profiles.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/csv.ts`**

```ts
/** RFC 4180 CSV reader: quoted fields, "" escapes, CRLF/LF/CR, BOM stripped.
 *  Blank lines yield no row (as Python's csv.DictReader). */
export function parseCsv(input: string): string[][] {
  const text = input.replace(/^\ufeff/, "");
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let i = 0;
  const endRow = () => {
    row.push(field);
    if (!(row.length === 1 && row[0] === "")) rows.push(row);
    row = [];
    field = "";
  };
  while (i < text.length) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
        quoted = false; i += 1; continue;
      }
      field += ch; i += 1; continue;
    }
    if (ch === '"') { quoted = true; i += 1; continue; }
    if (ch === ",") { row.push(field); field = ""; i += 1; continue; }
    if (ch === "\r" || ch === "\n") {
      endRow();
      i += ch === "\r" && text[i + 1] === "\n" ? 2 : 1;
      continue;
    }
    field += ch; i += 1;
  }
  if (field !== "" || row.length) endRow();
  return rows;
}
```

- [ ] **Step 4: `android/src/core/profiles.ts`**

```ts
/** Company profiles: branding + layout for exports (port of desktop
 *  core/profiles.py). Keys stay snake_case so files move between the apps. */
import data from "./data/desktop.json";
import { parseCsv } from "./csv";

export interface CompanyProfile {
  id: string; name: string; address: string; phone: string; email: string; website: string;
  logo_path: string; logo_position: string; logo_width_mm: number; footer_text: string; footer_alignment: string;
  show_page_numbers: boolean; page_number_position: string; page_number_format: string; accent_color: string; pdf_design: string;
}

export const DEFAULT_PROFILE: CompanyProfile = { ...(data.profileDefaults as CompanyProfile) };
export const PDF_DESIGNS: string[] = data.pdfDesigns.map((d) => d.key);
const DEFAULT_PDF_DESIGN: string = data.defaultPdfDesign;
const ALIGNMENTS = ["left", "center", "right"];
const PAGENUM_POSITIONS = ["footer-left", "footer-center", "footer-right", "header-left", "header-center", "header-right"];
const FIELD_NAMES = new Set(Object.keys(DEFAULT_PROFILE));
const COLOR_RE = /^#[0-9A-Fa-f]{3,8}$/;
const HEADER_ALIASES: Record<string, string> = {
  company: "name", company_name: "name", organisation: "name", organization: "name", footer: "footer_text",
  logo: "logo_path", logo_file: "logo_path", accent: "accent_color", accent_colour: "accent_color",
  colour: "accent_color", color: "accent_color", telephone: "phone", tel: "phone", e_mail: "email", web: "website",
  url: "website", page_numbers: "show_page_numbers", logo_width: "logo_width_mm", design: "pdf_design",
  pdf_layout: "pdf_design", pdf_template: "pdf_design", layout: "pdf_design", template: "pdf_design",
};
const POSITION_ALIASES: Record<string, string> = { centre: "center", middle: "center", centered: "center", centred: "center" };

/** Python-like str() for the values JSON/CSV hand us. */
function pyStr(v: unknown): string {
  if (typeof v === "boolean") return v ? "True" : "False";
  return String(v);
}

export function choice(value: unknown, allowed: string[], def: string): string {
  let s = (value === null || value === undefined || value === false || value === 0 || value === "" ? "" : pyStr(value))
    .trim().toLowerCase().replace(/[\s_]+/g, "-");
  s = s.split("-").map((p) => POSITION_ALIASES[p] ?? p).join("-");
  s = s.split("top-").join("header-").split("bottom-").join("footer-");
  return allowed.includes(s) ? s : def;
}

function pyFloat(v: unknown): number | null {
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase().split("_").join("");
  if (/^[+-]?(inf|infinity)$/.test(s)) return s.startsWith("-") ? -Infinity : Infinity;
  if (/^[+-]?nan$/.test(s)) return NaN;
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/.test(s)) return null;
  return Number(s);
}

export function coerce(key: string, value: unknown): string | number | boolean {
  if (key === "logo_position" || key === "footer_alignment") return choice(value, ALIGNMENTS, key === "logo_position" ? "left" : "center");
  if (key === "page_number_position") return choice(value, PAGENUM_POSITIONS, "footer-right");
  if (key === "pdf_design") return choice(value, PDF_DESIGNS, DEFAULT_PDF_DESIGN);
  if (key === "page_number_format") {
    const s = value === null || value === undefined ? "" : pyStr(value).trim();
    return s || "Page {n} of {total}";
  }
  if (key === "accent_color") {
    const s = (value === null || value === undefined || value === "" ? "" : pyStr(value)).trim();
    return COLOR_RE.test(s) ? s : "#8B1E1E";             // it ends up in CSS: #hex only
  }
  if (key === "show_page_numbers") {
    if (typeof value === "boolean") return value;
    const s = value === null || value === undefined ? "" : pyStr(value).trim().toLowerCase();
    if (!s) return true;
    return ["1", "true", "yes", "y", "on"].includes(s);
  }
  if (key === "logo_width_mm") {
    const v = pyFloat(value);
    if (v === null || Number.isNaN(v)) return 35.0;
    return Math.min(Math.max(v, 10.0), 90.0);
  }
  if (value === null || value === undefined) return "";
  if (typeof value === "number" && Number.isInteger(value)) return String(value);
  return pyStr(value).trim();
}

export function normHeader(h: unknown): string {
  const k = (h === null || h === undefined || h === "" ? "" : String(h)).trim().toLowerCase().replace(/[\s-]+/g, "_");
  return HEADER_ALIASES[k] ?? k;
}

export function normalizeRecord(rec: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(rec)) {
    const nk = normHeader(k);
    if (FIELD_NAMES.has(nk) && (!(nk in out) || out[nk] === null || out[nk] === "")) out[nk] = v;
  }
  const meaningful = Object.entries(out).some(([k, v]) => k !== "id" && v !== null && v !== undefined && String(v).trim() !== "");
  return meaningful ? out : {};
}

export function profileFromRecord(rec: Record<string, unknown>): CompanyProfile {
  const p: Record<string, unknown> = { ...DEFAULT_PROFILE };
  for (const [k, v] of Object.entries(rec)) if (FIELD_NAMES.has(k)) p[k] = coerce(k, v);
  return p as unknown as CompanyProfile;
}

/** What a save stores: every field coerced, never a blank name. */
export function sanitizeProfile(profile: CompanyProfile): CompanyProfile {
  const p: Record<string, unknown> = { ...profile };
  for (const k of FIELD_NAMES) if (k !== "id") p[k] = coerce(k, p[k]);
  if (!String(p.name ?? "").trim()) p.name = "Company";
  return p as unknown as CompanyProfile;
}

export function uniqueProfileName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  const base = (name ?? "").trim() || "Company";
  if (!t.has(base.toLowerCase())) return base;
  let n = 2;
  while (t.has(`${base} (${n})`.toLowerCase())) n += 1;
  return `${base} (${n})`;
}

export function copyProfileName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  let cand = `${name} (copy)`;
  let n = 2;
  while (t.has(cand.toLowerCase())) { cand = `${name} (copy ${n})`; n += 1; }
  return cand;
}

/** Identity of a profile's CONTENT (not its id or where its logo lives). */
export function contentKey(p: CompanyProfile, logoDigest: string): string {
  const d: Record<string, unknown> = { ...p };
  delete d.id;
  delete d.logo_path;
  const sorted = Object.keys(d).sort().map((k) => [k, d[k]]);
  return JSON.stringify(sorted) + "|" + logoDigest;
}

export interface ProfileImportRecord { profile: CompanyProfile; logoData: string; logoName: string; logoPath: string; }

export function parseProfileFile(fileName: string, text: string): { records: ProfileImportRecord[]; invalid: number } {
  const ext = fileName.slice(fileName.lastIndexOf(".")).toLowerCase();
  let raw: unknown[];
  if (ext === ".json") {
    const d: unknown = JSON.parse(text.replace(/^\ufeff/, ""));
    raw = Array.isArray(d) ? d : [d];
  } else if (ext === ".csv") {
    const rows = parseCsv(text);
    const head = rows[0] ?? [];
    raw = rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, i < r.length ? r[i] : null])));
  } else if (ext === ".xlsx" || ext === ".xlsm") {
    throw new Error("Excel files can't be read on the phone. Export the profiles as JSON from the desktop app.");
  } else {
    throw new Error(`Unsupported import format: ${ext || "(no extension)"} - use a .json or .csv file`);
  }
  const records: ProfileImportRecord[] = [];
  let invalid = 0;
  for (const r of raw) {
    if (!r || typeof r !== "object" || Array.isArray(r)) { invalid += 1; continue; }
    const rec = r as Record<string, unknown>;
    const extra: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(rec)) {
      const nk = normHeader(k);
      if (nk === "logo_data" || nk === "logo_name") extra[nk] = v;
    }
    const norm = normalizeRecord(rec);
    if (!Object.keys(norm).length) continue;
    const profile = profileFromRecord(norm);
    const logoPath = profile.logo_path;
    records.push({ profile: { ...profile, id: "", logo_path: "" }, logoPath,
      logoData: typeof extra.logo_data === "string" ? extra.logo_data : "",
      logoName: typeof extra.logo_name === "string" ? extra.logo_name : "" });
  }
  return { records, invalid };
}

/** The desktop's portable JSON: every field plus the logo embedded. */
export function buildProfilesJson(profiles: CompanyProfile[], logos: Record<string, { name: string; base64: string }>): string {
  return JSON.stringify(profiles.map((p) => {
    const row: Record<string, unknown> = { ...p };
    const logo = logos[p.id];
    if (logo) { row.logo_path = logo.name; row.logo_name = logo.name; row.logo_data = logo.base64; }
    else row.logo_path = "";
    return row;
  }), null, 2);
}
```

- [ ] **Step 5: Run the tests**

Run: `cd android && npx vitest run test/profiles.test.ts`
Expected: PASS. A fixture mismatch in `coerce` means the Python rule differs — follow the fixture.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/csv.ts android/src/core/profiles.ts android/test/profiles.test.ts
git commit -m "Android core: company profiles and desktop-compatible import/export"
```

---

### Task 11: PDF designs as HTML

**Files:**
- Create: `android/src/core/designs.ts`, `android/src/core/exportHtml.ts`, `android/test/exportHtml.test.ts`

**Interfaces:**
- Consumes: `parse`, `plain`, `runs`, `cleanText` (Task 3); `hasArabic`; `normalizeStatus` (Task 8); `CompanyProfile` (Task 10).
- Produces (`designs.ts`): `interface Design` (snake_case fields as desktop), `DESIGNS`, `getDesign(key)`.
- Produces (`exportHtml.ts`): `DEFAULT_ACCENT`, `safeColor(v, def?)`, `escapeHtml(s)`, `interface HtmlOptions {profile?, design?, logoDataUri?}`, `renderMinutesHtml(md, opts?): string` (a complete document for Android's print engine), `renderPreviewHtml(md, accent?): string` (body fragment for the in-app preview).

- [ ] **Step 1: Write the failing test** — `android/test/exportHtml.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_PROFILE, type CompanyProfile } from "../src/core/profiles";
import { DESIGNS, getDesign } from "../src/core/designs";
import { renderMinutesHtml, renderPreviewHtml, safeColor } from "../src/core/exportHtml";

const MD = [
  "# Meeting Minutes", "**Meeting Title:** Budget <review>", "**Date and Time:** 5 Oct 2026", "**Attendees:** Ali, Sara; Omar",
  "", "## Decisions Made", "- Approve **plan**", "", "## Action Items",
  "| Task | Responsible Person | Deadline | Status |", "| --- | --- | --- | --- |",
  "| Send report | Ali | 10 Oct | Done |", "| Review | Sara | Not specified | Pending |",
].join("\n");
const profile: CompanyProfile = { ...DEFAULT_PROFILE, id: "p", name: "Acme & Co", address: "Muscat",
  footer_text: "Confidential", page_number_format: 'Page {n} of {total} "x"', accent_color: "#1F5FA8" };

describe("branded HTML for printing", () => {
  it("letterhead, title, details, attendees, sections and the action table", () => {
    const html = renderMinutesHtml(MD, { profile });
    expect(html).toContain("Acme &amp; Co");
    expect(html).toContain("Budget &lt;review&gt;");
    expect(html).toContain('class="details');
    expect(html).toMatch(/<li[^>]*>Ali<\/li>\s*<li[^>]*>Sara<\/li>\s*<li[^>]*>Omar<\/li>/);
    expect(html).toContain("<strong>plan</strong>");
    expect(html).toContain("<thead>");
    expect(html).toContain('class="dot"');
  });
  it("page numbers and footer in the page margins, quotes escaped", () => {
    const html = renderMinutesHtml(MD, { profile });
    expect(html).toContain('"Page " counter(page) " of " counter(pages) " \\"x\\""');
    expect(html).toContain('"Confidential"');
    expect(html).toContain("@bottom-right");
  });
  it("each design has its own look; only Formal signs off", () => {
    const looks = DESIGNS.map((d) => renderMinutesHtml(MD, { profile, design: d.key }));
    expect(new Set(looks.map((h) => /class="design-(\w+)/.exec(h)![1]))).toEqual(new Set(["classic", "modern", "formal", "compact"]));
    expect(looks.filter((h) => h.includes('class="signatures"'))).toHaveLength(1);
    expect(getDesign("nope").key).toBe("classic");
  });
  it("the company's own design is used when none is given", () => {
    expect(renderMinutesHtml(MD, { profile: { ...profile, pdf_design: "compact" } })).toContain('class="design-compact');
  });
  it("Arabic minutes are right-to-left", () => {
    expect(renderMinutesHtml("# محضر الاجتماع\n**عنوان الاجتماع:** الميزانية", { profile })).toContain('dir="rtl"');
  });
  it("hostile values are neutralised", () => {
    const evil = { ...profile, accent_color: "red;}</style><script>alert(1)</script>", name: "<script>x</script>" };
    const html = renderMinutesHtml("<img src=x onerror=alert(1)>", { profile: evil, logoDataUri: "javascript:alert(1)" });
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img src=x");
    expect(html).not.toContain("javascript:");
    expect(safeColor("#abc")).toBe("#abc");
    expect(safeColor("blue")).toBe("#8B1E1E");
  });
  it("a logo is embedded only as an image data URI", () => {
    expect(renderMinutesHtml(MD, { profile, logoDataUri: "data:image/png;base64,iVBORw0KGgo=" })).toContain('<img class="logo" src="data:image/png;base64,iVBORw0KGgo="');
  });
  it("no profile: plain minutes still render; nothing at all says so", () => {
    expect(renderMinutesHtml(MD)).toContain("Budget &lt;review&gt;");
    expect(renderMinutesHtml("")).toContain("No minutes content.");
  });
  it("preview fragment for the in-app viewer", () => {
    expect(renderPreviewHtml("## Hi\n- a")).toContain('<h2 dir="auto"');
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/exportHtml.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: `android/src/core/designs.ts`**

```ts
/** The four PDF designs a company can choose (data from desktop export/pdf_designs.py). */
import data from "./data/desktop.json";

export interface Design {
  key: string; name: string; blurb: string; font: string; body: number; lead: number; cell: number;
  title_size: number; title_align: string; h2_size: number; gap: number; letterhead: string; logo_max_mm: number;
  name_max: number; name_accent: boolean; top_band_mm: number; heading: string; details: string; table: string;
  status_color: boolean; signatures: boolean;
}

export const DESIGNS: Design[] = data.pdfDesigns as Design[];

export function getDesign(key?: string | null): Design {
  const k = String(key ?? "").trim().toLowerCase();
  return DESIGNS.find((d) => d.key === k) ?? DESIGNS.find((d) => d.key === data.defaultPdfDesign)!;
}
```

- [ ] **Step 4: `android/src/core/exportHtml.ts`**

```ts
/** Minutes as a branded HTML document for Android's print engine (PDF), in the
 *  company's design — the HTML/CSS counterpart of desktop export/pdf_export.py. */
import data from "./data/desktop.json";
import { normalizeStatus } from "./actionItems";
import { type Design, getDesign } from "./designs";
import { cleanText, parse, plain, runs } from "./mdBlocks";
import type { CompanyProfile } from "./profiles";
import { hasArabic } from "./rtl";

export const DEFAULT_ACCENT = "#8B1E1E";
const TEXT = "#221D1D", MUTED = "#6C6269", BORDER = "#E4DEDC", PANEL = "#F7F4F3", GRID_HEAD = "#ECE9E8", GRID_LINE = "#9A9096";
const STATUS_COLORS = data.statusColors as Record<string, string>;
const TITLE_KEYS = new Set<string>(data.pdfTitleKeys);
const PEOPLE_KEYS = new Set<string>(data.pdfPeopleKeys);
const STATUS_HEADERS = new Set<string>(data.pdfStatusHeaders);
const PLACEHOLDERS = new Set<string>(data.pdfPlaceholders);
const COLOR_RE = /^#[0-9A-Fa-f]{3,8}$/;
const LOGO_RE = /^data:image\/(png|jpe?g|gif|bmp|webp);base64,[A-Za-z0-9+/=]+$/;
const SANS = `Roboto, "Noto Sans", "Noto Sans Arabic", "Noto Naskh Arabic", sans-serif`;
const SERIF = `"Noto Serif", "Noto Naskh Arabic", serif`;

export function safeColor(v: unknown, def = DEFAULT_ACCENT): string {
  const s = String(v ?? "").trim();
  return COLOR_RE.test(s) ? s : def;
}

export function escapeHtml(s: unknown): string {
  return cleanText(s).split("&").join("&amp;").split("<").join("&lt;").split(">").join("&gt;")
    .split('"').join("&quot;").split("'").join("&#39;");
}

const inline = (t: string) => runs(t).map(([s, b]) => (b ? `<strong>${escapeHtml(s)}</strong>` : escapeHtml(s))).join("");
const isPlaceholder = (v: string) => PLACEHOLDERS.has(plain(v).trim().toLowerCase());

// ---- colour ------------------------------------------------------------------
function rgb(hex: string): [number, number, number] {
  let h = hex.slice(1);
  if (h.length === 3 || h.length === 4) h = h.slice(0, 3).split("").map((c) => c + c).join("");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function luminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
const contrastWithWhite = (c: [number, number, number]) => 1.05 / (luminance(c) + 0.05);
const hexOf = (c: [number, number, number]) => "#" + c.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
/** The accent darkened until text in it reads on white (≥ 4.5:1). */
function readable(accent: string): string {
  let c = rgb(accent);
  for (let i = 0; i < 20 && contrastWithWhite(c) < 4.5; i++) c = c.map((x) => x * 0.85) as [number, number, number];
  return hexOf(c);
}
const tint = (accent: string, a: number) => { const [r, g, b] = rgb(accent); return `rgba(${r}, ${g}, ${b}, ${a})`; };

// ---- page margin boxes ----------------------------------------------------------
function cssString(s: string): string {
  return `"${s.split("\\").join("\\\\").split('"').join('\\"').split("\n").join("\\A ").split("<").join("\\3C ")}"`;
}
function pageNumberContent(fmt: string): string {
  return fmt.split(/(\{n\}|\{total\})/).filter((t) => t).map((t) =>
    t === "{n}" ? "counter(page)" : t === "{total}" ? "counter(pages)" : cssString(cleanText(t))).join(" ");
}
const box = (pos: string) => (pos.startsWith("header-") ? "@top-" : "@bottom-") + pos.split("-")[1];

// ---- blocks --------------------------------------------------------------------
function details(rows: Array<[string, string]>, d: Design): string {
  const v = (s: string) => (isPlaceholder(s) ? `<span class="muted">${inline(s)}</span>` : inline(s));
  if (d.details === "cards") {
    return `<div class="details cards">${rows.map(([k, s]) => `<div class="card"><div class="k">${inline(k)}</div><div class="v" dir="auto">${v(s)}</div></div>`).join("")}</div>`;
  }
  if (d.details === "inline") {
    return `<p class="details inline" dir="auto">${rows.map(([k, s]) => `<strong>${inline(k)}:</strong> ${v(s)}`).join(" &nbsp;·&nbsp; ")}</p>`;
  }
  return `<table class="details ${d.details === "grid" ? "grid" : "panel"}">${rows
    .map(([k, s]) => `<tr><th>${inline(k)}</th><td dir="auto">${v(s)}</td></tr>`).join("")}</table>`;
}

function people(key: string, value: string): string {
  const names = isPlaceholder(value) ? [] : plain(value).split(/\s*(?:,|;|،|\n|•)\s*/).map((s) => s.trim()).filter(Boolean);
  const body = names.length
    ? `<ul class="names">${names.map((n) => `<li dir="auto">${escapeHtml(n)}</li>`).join("\n")}</ul>`
    : `<p class="muted">${inline(value || "Not specified")}</p>`;
  return `<section class="people"><div class="k">${inline(key)}</div>${body}</section>`;
}

function table(headers: string[], rows: string[][], d: Design): string {
  const statusCol = headers.findIndex((h) => STATUS_HEADERS.has(plain(h).trim().toLowerCase()));
  const cell = (s: string, j: number) => {
    if (j === statusCol && d.status_color) {
      const color = STATUS_COLORS[normalizeStatus(plain(s))];
      if (color) return `<span class="dot" style="background:${color}"></span>${inline(s)}`;
    }
    return inline(s);
  };
  const head = headers.map((h) => `<th dir="auto">${inline(h)}</th>`).join("");
  const body = rows.map((r) => `<tr>${headers.map((_h, j) => `<td dir="auto">${cell(r[j] ?? "", j)}</td>`).join("")}</tr>`).join("");
  return `<table class="t t-${d.table}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function signatures(arabic: boolean): string {
  const L: string[] = arabic ? data.signaturesAr : data.signaturesEn;
  const col = (title: string) => `<div class="sig"><div class="sig-title"><strong>${escapeHtml(title)}</strong></div>${L.slice(2)
    .map((label) => `<div class="sig-line"><span>${escapeHtml(label)}</span><span class="line"></span></div>`).join("")}</div>`;
  return `<section class="signatures">${col(L[0])}${col(L[1])}</section>`;
}

function letterhead(p: CompanyProfile, d: Design, accent: string, logoDataUri?: string | null): string {
  const logo = logoDataUri && LOGO_RE.test(logoDataUri)
    ? `<img class="logo" src="${logoDataUri}" style="width:${Number(p.logo_width_mm) || 35}mm;max-height:${d.logo_max_mm}mm" alt="">` : "";
  const contact = [p.address, p.phone, p.email, p.website].filter((x) => String(x ?? "").trim()).map(escapeHtml).join(" &nbsp;·&nbsp; ");
  const nameColor = d.name_accent ? readable(accent) : TEXT;
  const band = d.top_band_mm ? `<div class="band" style="height:${d.top_band_mm}mm"></div>` : "";
  return `<header class="letterhead lh-${d.letterhead} logo-${p.logo_position}">${band}<div class="lh-row">${logo}` +
    `<div class="lh-text"><div class="company" style="font-size:${d.name_max}pt;color:${nameColor}">${escapeHtml(p.name)}</div>` +
    (contact ? `<div class="contact">${contact}</div>` : "") + `</div></div></header>`;
}

function css(d: Design, accent: string, p: CompanyProfile | null, running: string): string {
  const ink = readable(accent);
  const headText = contrastWithWhite(rgb(accent)) >= 3 ? "#FFFFFF" : TEXT;
  const boxes = new Map<string, string[]>();
  const add = (b: string, c: string) => boxes.set(b, [...(boxes.get(b) ?? []), c]);
  if (p?.footer_text) add("@bottom-" + p.footer_alignment, cssString(cleanText(p.footer_text)));
  if (p?.show_page_numbers) add(box(p.page_number_position), pageNumberContent(p.page_number_format || "Page {n} of {total}"));
  const runningBox = ["@top-left", "@top-right"].find((b) => !boxes.has(b));
  const boxCss = [...boxes].map(([b, parts]) => `${b} { content: ${parts.join(' "   " ')}; font-size: 7.5pt; color: ${MUTED}; }`).join("\n  ");
  const runningCss = running && runningBox ? `${runningBox} { content: ${cssString(running)}; font-size: 7.5pt; color: ${MUTED}; }` : "";
  const firstCss = running && runningBox ? `@page :first { ${runningBox} { content: none; } }` : "";
  const g = d.gap;
  return `
@page { size: A4; margin: 16mm 16mm 18mm 16mm;
  ${boxCss}
  ${runningCss} }
${firstCss}
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: ${TEXT}; font-family: ${d.font === "serif" ? SERIF : SANS}; font-size: ${d.body}pt; line-height: ${d.lead}pt; }
.letterhead { margin-bottom: ${10 * g}pt; padding-bottom: 6pt; }
.lh-rule { border-bottom: 2pt solid ${accent}; } .lh-hairline { border-bottom: 0.5pt solid ${BORDER}; }
.lh-double { border-bottom: 3pt double ${accent}; }
.band { background: ${accent}; margin-bottom: 6pt; }
.lh-row { display: flex; align-items: center; gap: 10pt; }
.logo-right .lh-row { flex-direction: row-reverse; text-align: end; }
.logo-center .lh-row { flex-direction: column; text-align: center; }
.company { font-weight: 700; line-height: 1.2; } .contact { color: ${MUTED}; font-size: 8pt; margin-top: 2pt; }
.eyebrow { color: ${MUTED}; font-size: 8pt; letter-spacing: .08em; margin: 0; }
h1.title { font-size: ${d.title_size}pt; line-height: 1.25; margin: 2pt 0 ${8 * g}pt; text-align: ${d.title_align}; color: ${TEXT}; }
.details { margin: 0 0 ${8 * g}pt; width: 100%; }
table.details { border-collapse: collapse; }
.details th { text-align: start; color: ${ink}; font-weight: 600; width: 28%; padding: 3pt 6pt; vertical-align: top; }
.details td { padding: 3pt 6pt; }
.details.panel { background: ${PANEL}; border-radius: 4pt; }
.details.grid th, .details.grid td { border: 0.5pt solid ${GRID_LINE}; } .details.grid th { background: ${GRID_HEAD}; color: ${TEXT}; }
.details.cards { display: flex; flex-wrap: wrap; gap: 6pt; }
.card { border: 0.5pt solid ${BORDER}; border-top: 2pt solid ${accent}; border-radius: 3pt; padding: 4pt 6pt; min-width: 30%; flex: 1; }
.card .k { color: ${ink}; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .05em; }
.people { margin: 0 0 ${8 * g}pt; } .people .k { color: ${ink}; font-weight: 600; margin-bottom: 2pt; }
.names { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 2pt 10pt; }
h2.sec { font-size: ${d.h2_size}pt; line-height: 1.3; margin: ${13 * g}pt 0 ${6 * g}pt; break-after: avoid; page-break-after: avoid; }
.sec-rule { color: ${ink}; border-bottom: 0.7pt solid ${BORDER}; padding-bottom: 2pt; }
.sec-bar { color: ${ink}; background: ${tint(accent, 0.1)}; border-inline-start: 3pt solid ${accent}; padding: 3pt 6pt; }
.sec-numbered { color: ${TEXT}; text-transform: uppercase; letter-spacing: .04em; }
.sec-plain { color: ${TEXT}; border-bottom: 0.5pt solid ${BORDER}; }
h3 { font-size: ${d.h2_size - 1.5}pt; margin: ${9 * g}pt 0 ${4 * g}pt; break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 ${5 * g}pt; } ul, ol { margin: 0 0 ${5 * g}pt; padding-inline-start: 16pt; } li { break-inside: avoid; }
.muted { color: ${MUTED}; font-style: italic; }
table.t { width: 100%; border-collapse: collapse; font-size: ${d.cell}pt; line-height: 1.3; margin: 3pt 0 8pt; }
table.t thead { display: table-header-group; } table.t tr { break-inside: avoid; page-break-inside: avoid; }
table.t th, table.t td { padding: 3pt 5pt; text-align: start; vertical-align: top; }
.t-fill th { background: ${accent}; color: ${headText}; } .t-fill td { border-bottom: 0.5pt solid ${BORDER}; }
.t-tint th { background: ${tint(accent, 0.12)}; color: ${ink}; } .t-tint td { border-bottom: 0.5pt solid ${BORDER}; }
.t-grid th, .t-grid td { border: 0.5pt solid ${GRID_LINE}; } .t-grid th { background: ${GRID_HEAD}; }
.t-lines th { border-bottom: 1pt solid ${TEXT}; } .t-lines td { border-bottom: 0.3pt solid ${BORDER}; }
.dot { display: inline-block; width: 6pt; height: 6pt; border-radius: 50%; margin-inline-end: 4pt; vertical-align: middle; }
.signatures { display: flex; gap: 10mm; margin-top: 9mm; break-inside: avoid; page-break-inside: avoid; }
.sig { flex: 1; } .sig-line { display: flex; align-items: flex-end; gap: 6pt; height: 9mm; }
.sig-line .line { flex: 1; border-bottom: 0.6pt solid ${TEXT}; }`;
}

export interface HtmlOptions { profile?: CompanyProfile | null; design?: string | null; logoDataUri?: string | null; }

export function renderMinutesHtml(md: string, opts: HtmlOptions = {}): string {
  const profile = opts.profile ?? null;
  const d = getDesign(opts.design ?? profile?.pdf_design);
  const accent = safeColor(profile?.accent_color);
  const blocks = parse(md ?? "");

  let i = 0;
  let h1 = "";
  if (blocks.length && blocks[0].kind === "h1") { h1 = blocks[0].text; i = 1; }
  const det: Array<[string, string]> = [];
  while (i < blocks.length && blocks[i].kind === "kv") { det.push([blocks[i].key, blocks[i].text]); i += 1; }
  let title = "";
  let titleAt = -1;
  det.some(([k, v], n) => {
    if (TITLE_KEYS.has(plain(k).trim().toLowerCase()) && !isPlaceholder(v)) { title = v; titleAt = n; return true; }
    return false;
  });
  const mirror = hasArabic(h1 || title || (det[0]?.[0] ?? ""));

  const out: string[] = [];
  if (title) {
    if (h1) out.push(`<p class="eyebrow" dir="auto">${escapeHtml(plain(h1).toUpperCase())}</p>`);
    out.push(`<h1 class="title" dir="auto">${inline(title)}</h1>`);
  } else if (h1) {
    out.push(`<h1 class="title" dir="auto">${inline(h1)}</h1>`);
  }
  const panel = det.filter(([k], n) => n !== titleAt && !PEOPLE_KEYS.has(plain(k).trim().toLowerCase()));
  const ppl = det.filter(([k]) => PEOPLE_KEYS.has(plain(k).trim().toLowerCase()));
  if (panel.length) out.push(details(panel, d));
  for (const [k, v] of ppl) out.push(people(k, v));

  let counter = 0;
  let content = 0;
  for (const b of blocks.slice(i)) {
    content += 1;
    if (b.kind === "h1" || b.kind === "h2") {
      counter += 1;
      const text = d.heading === "numbered" ? `${counter}. ${plain(b.text).toUpperCase()}` : b.text;
      out.push(`<h2 class="sec sec-${d.heading}" dir="auto">${d.heading === "numbered" ? escapeHtml(text) : inline(text)}</h2>`);
    } else if (b.kind === "h3") out.push(`<h3 dir="auto">${inline(b.text)}</h3>`);
    else if (b.kind === "bullet") out.push(`<ul>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ul>`);
    else if (b.kind === "olist") out.push(`<ol${b.start !== 1 ? ` start="${b.start}"` : ""}>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ol>`);
    else if (b.kind === "kv") out.push(`<p dir="auto"><strong>${inline(b.key)}:</strong> ${inline(b.text)}</p>`);
    else if (b.kind === "para") out.push(`<p dir="auto"${isPlaceholder(b.text) ? ' class="muted"' : ""}>${inline(b.text)}</p>`);
    else if (b.kind === "table") out.push(table(b.headers, b.rows, d));
  }
  if (!out.length && !content) out.push("<p>No minutes content.</p>");
  else if (d.signatures) out.push(signatures(mirror));

  const docTitle = plain(title || h1).trim() || "Minutes of Meeting";
  const company = profile ? cleanText(profile.name).trim() : "";
  const running = profile ? [company, plain(title || h1).trim()].filter(Boolean).join("  ·  ") : "";
  return `<!doctype html><html lang="${mirror ? "ar" : "en"}" dir="${mirror ? "rtl" : "ltr"}"><head><meta charset="utf-8">` +
    `<title>${escapeHtml(docTitle)}</title><style>${css(d, accent, profile, running)}</style></head>` +
    `<body class="design-${d.key}">${profile ? letterhead(profile, d, accent, opts.logoDataUri) : ""}<main>${out.join("\n")}</main></body></html>`;
}

/** Body fragment for the in-app preview (no page furniture). */
export function renderPreviewHtml(md: string, accent?: string | null): string {
  const a = safeColor(accent);
  return parse(md ?? "").map((b) => {
    if (b.kind === "h1" || b.kind === "h2" || b.kind === "h3") {
      const lvl = b.kind === "h1" ? 1 : b.kind === "h2" ? 2 : Math.min(Math.max(b.level, 3), 6);
      return `<h${lvl} dir="auto" style="color:${a}">${inline(b.text)}</h${lvl}>`;
    }
    if (b.kind === "kv") return `<p dir="auto"><strong>${inline(b.key)}:</strong> ${inline(b.text)}</p>`;
    if (b.kind === "bullet") return `<ul>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ul>`;
    if (b.kind === "olist") return `<ol${b.start !== 1 ? ` start="${b.start}"` : ""}>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ol>`;
    if (b.kind === "table") {
      const head = b.headers.map((h) => `<th dir="auto">${inline(h)}</th>`).join("");
      const rows = b.rows.map((r) => `<tr>${b.headers.map((_h, j) => `<td dir="auto">${inline(r[j] ?? "")}</td>`).join("")}</tr>`).join("");
      return `<table><thead style="background:${a};color:#fff"><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
    }
    return `<p dir="auto">${inline(b.text)}</p>`;
  }).join("\n");
}
```

- [ ] **Step 5: Run the tests**

Run: `cd android && npx vitest run test/exportHtml.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/designs.ts android/src/core/exportHtml.ts android/test/exportHtml.test.ts
git commit -m "Android core: branded HTML in the four PDF designs"
```

---

### Task 12: Word, text and Markdown exports

**Files:**
- Create: `android/src/core/exportText.ts`, `android/src/core/exportDocx.ts`, `android/test/exportDocx.test.ts`

**Interfaces:**
- Consumes: `parse`, `runs`, `cleanText` (Task 3), `hasArabic`, `CompanyProfile` (Task 10), `safeColor` (Task 11).
- Produces: `buildTxt(md, profile?)`, `buildMd(md, profile?)`; `interface DocxLogo {base64: string; type: "png" | "jpg" | "gif" | "bmp"; widthPx: number; heightPx: number}`, `buildDocxBase64(md, profile?, logo?): Promise<string>`.

- [ ] **Step 1: Write the failing test** — `android/test/exportDocx.test.ts`

```ts
import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { buildDocxBase64 } from "../src/core/exportDocx";
import { buildMd, buildTxt } from "../src/core/exportText";
import { DEFAULT_PROFILE } from "../src/core/profiles";

const MD = "# Meeting Minutes\n**Attendees:** Ali\n## Notes\n- one **bold**\n1. first\n| Task | Owner |\n|---|---|\n| Send | Ali |\n\nمحضر الاجتماع النهائي";
const profile = { ...DEFAULT_PROFILE, id: "p", name: "Acme", phone: "123", footer_text: "Confidential" };

describe("text and markdown", () => {
  it("txt: header, no Markdown marks, footer", () => {
    const t = buildTxt(MD, profile);
    expect(t.startsWith("Acme\n123\n" + "=".repeat(60) + "\n\n")).toBe(true);
    expect(t).not.toContain("**");
    expect(t).not.toMatch(/^#/m);
    expect(t.trimEnd().endsWith("Confidential")).toBe(true);
  });
  it("md: profile header and footer around the minutes", () => {
    expect(buildMd("x", profile)).toBe("**Acme**  \n123\n\n---\n\nx\n\n---\nConfidential\n");
  });
});

describe("word", () => {
  it("a real .docx with the content, the table, RTL Arabic, header and page numbers", async () => {
    const zip = await JSZip.loadAsync(await buildDocxBase64(MD, profile), { base64: true });
    const doc = await zip.file("word/document.xml")!.async("string");
    expect(doc).toContain("Meeting Minutes");
    expect(doc).toContain("<w:tbl>");
    expect(doc).toContain("Send");
    expect(doc).toMatch(/<w:bidi\/>/);
    const parts = Object.keys(zip.files).join(" ");
    expect(parts).toMatch(/word\/header\d*\.xml/);
    const footers = await Promise.all(Object.keys(zip.files).filter((f) => /word\/footer\d*\.xml/.test(f)).map((f) => zip.file(f)!.async("string")));
    expect(footers.join("")).toContain("NUMPAGES");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/exportDocx.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: `android/src/core/exportText.ts`**

```ts
/** Plain-text and Markdown exports (port of desktop txt_export.py / export_md). */
import { cleanText } from "./mdBlocks";
import type { CompanyProfile } from "./profiles";

function txtHeader(p?: CompanyProfile | null): string {
  if (!p) return "";
  const lines = [p.name, ...[p.address, p.phone, p.email, p.website].filter((x) => x)];
  lines.push("=".repeat(60));
  return lines.join("\n") + "\n\n";
}

export function buildTxt(md: string, profile?: CompanyProfile | null): string {
  let body = cleanText(md ?? "").split("**").join("");
  body = body.replace(/^#+\s*/gm, "");
  let text = txtHeader(profile) + body.trim() + "\n";
  if (profile?.footer_text) text += "\n" + "-".repeat(60) + "\n" + String(profile.footer_text) + "\n";
  return cleanText(text);
}

export function buildMd(md: string, profile?: CompanyProfile | null): string {
  let head = "";
  if (profile) {
    const bits = [profile.address, profile.phone, profile.email, profile.website].filter((x) => x).join(" · ");
    head = `**${profile.name}**` + (bits ? `  \n${bits}` : "") + "\n\n---\n\n";
  }
  const foot = profile?.footer_text ? `\n\n---\n${profile.footer_text}\n` : "";
  return cleanText(head + (md ?? "").trim() + foot);
}
```

- [ ] **Step 4: `android/src/core/exportDocx.ts`**

```ts
/** Word (.docx) export with letterhead, logo, footer and page numbers
 *  (port of desktop export/docx_export.py, using the `docx` library). */
import {
  AlignmentType, Document, Footer, Header, HeadingLevel, ImageRun, Packer, PageNumber, Paragraph,
  ShadingType, Table, TableCell, TableRow, TextRun, WidthType,
} from "docx";
import { type Block, cleanText, parse, runs } from "./mdBlocks";
import type { CompanyProfile } from "./profiles";
import { hasArabic } from "./rtl";
import { safeColor } from "./exportHtml";

export interface DocxLogo { base64: string; type: "png" | "jpg" | "gif" | "bmp"; widthPx: number; heightPx: number; }

const ALIGN = { left: AlignmentType.LEFT, center: AlignmentType.CENTER, right: AlignmentType.RIGHT } as const;
const align = (s: string) => ALIGN[(s as keyof typeof ALIGN)] ?? AlignmentType.LEFT;
const hex6 = (c: string) => { let h = safeColor(c).slice(1); if (h.length <= 4) h = h.slice(0, 3).split("").map((x) => x + x).join(""); return h.slice(0, 6).toUpperCase(); };

function textRuns(text: string, opts: { bold?: boolean; color?: string; size?: number; rtl?: boolean } = {}): TextRun[] {
  return runs(cleanText(text)).filter(([t]) => t).map(([t, b]) =>
    new TextRun({ text: t, bold: b || opts.bold, color: opts.color, size: opts.size, rightToLeft: opts.rtl }));
}

function para(text: string, extra: Partial<ConstructorParameters<typeof Paragraph>[0] & object> = {}, runOpts: Parameters<typeof textRuns>[1] = {}): Paragraph {
  const rtl = hasArabic(text);
  return new Paragraph({ ...extra, bidirectional: rtl || undefined, alignment: rtl ? AlignmentType.RIGHT : (extra as { alignment?: never }).alignment,
    children: textRuns(text, { ...runOpts, rtl }) });
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function header(p: CompanyProfile, logo?: DocxLogo | null): Header {
  const children: Paragraph[] = [];
  if (logo) {
    const w = (Number(p.logo_width_mm) || 35) * 3.78;          // mm -> px at 96 dpi
    const h = logo.widthPx ? (w * logo.heightPx) / logo.widthPx : w / 3;
    children.push(new Paragraph({ alignment: align(p.logo_position),
      children: [new ImageRun({ type: logo.type, data: b64ToBytes(logo.base64), transformation: { width: Math.round(w), height: Math.round(h) } })] }));
  }
  const contact = [p.address, p.phone, p.email, p.website].filter((x) => x).map(cleanText).join("  •  ");
  children.push(new Paragraph({ alignment: align(p.logo_position), children: [
    new TextRun({ text: cleanText(p.name), bold: true, size: 26, color: hex6(p.accent_color) }),
    ...(contact ? [new TextRun({ text: contact, size: 16, color: "666666", break: 1 })] : []),
  ] }));
  return new Header({ children });
}

function footer(p: CompanyProfile): Footer {
  const children: Paragraph[] = [];
  if (p.footer_text) children.push(new Paragraph({ alignment: align(p.footer_alignment),
    children: [new TextRun({ text: cleanText(p.footer_text), size: 16, color: "888888" })] }));
  if (p.show_page_numbers) {
    const pos = p.page_number_position;
    const parts = (p.page_number_format || "Page {n} of {total}").split(/(\{n\}|\{total\})/).filter((t) => t)
      .map((t) => (t === "{n}" ? PageNumber.CURRENT : t === "{total}" ? PageNumber.TOTAL_PAGES : cleanText(t)));
    children.push(new Paragraph({ alignment: pos.endsWith("right") ? AlignmentType.RIGHT : pos.endsWith("left") ? AlignmentType.LEFT : AlignmentType.CENTER,
      children: [new TextRun({ children: parts, size: 16 })] }));
  }
  return new Footer({ children });
}

function tableOf(b: Block, accent: string): Table {
  const cols = Math.max(b.headers.length, 1);
  const cell = (t: string, head: boolean) => new TableCell({
    shading: head ? { type: ShadingType.CLEAR, fill: accent, color: "auto" } : undefined,
    children: [para(t, {}, head ? { bold: true, color: "FFFFFF" } : {})] });
  return new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, rows: [
    new TableRow({ tableHeader: true, children: Array.from({ length: cols }, (_x, j) => cell(b.headers[j] ?? "", true)) }),
    ...b.rows.map((r) => new TableRow({ children: Array.from({ length: cols }, (_x, j) => cell(r[j] ?? "", false)) })),
  ] });
}

export async function buildDocxBase64(md: string, profile?: CompanyProfile | null, logo?: DocxLogo | null): Promise<string> {
  const accent = hex6(profile?.accent_color ?? "#8B1E1E");
  const body: Array<Paragraph | Table> = [];
  for (const b of parse(md ?? "")) {
    if (b.kind === "h1") body.push(para(b.text, { heading: HeadingLevel.TITLE }, { color: accent }));
    else if (b.kind === "h2" || b.kind === "h3") {
      const lvl = b.kind === "h2" ? HeadingLevel.HEADING_1 : [HeadingLevel.HEADING_2, HeadingLevel.HEADING_3, HeadingLevel.HEADING_4, HeadingLevel.HEADING_5][Math.min(Math.max(b.level - 3, 0), 3)];
      body.push(para(b.text, { heading: lvl }));
    } else if (b.kind === "kv") body.push(para(`**${b.key}:** ${b.text}`));
    else if (b.kind === "bullet") for (const it of b.items) body.push(para(it, { bullet: { level: 0 } }));
    else if (b.kind === "olist") b.items.forEach((it, k) => body.push(para(`${b.start + k}.\u00a0${it}`, { indent: { left: 340, hanging: 340 } })));
    else if (b.kind === "table") body.push(tableOf(b, accent));
    else body.push(para(b.text));
  }
  const doc = new Document({
    styles: { default: { document: { run: { font: "Calibri", size: 22 } } } },
    sections: [{
      headers: profile ? { default: header(profile, logo) } : undefined,
      footers: profile ? { default: footer(profile) } : undefined,
      children: body.length ? body : [new Paragraph("No minutes content.")],
    }],
  });
  return Packer.toBase64String(doc);
}
```

- [ ] **Step 5: Run the tests and typecheck**

Run: `cd android && npx vitest run test/exportDocx.test.ts && npm run typecheck`
Expected: PASS. If the `docx` 9 typings reject an option name, check `node_modules/docx/dist/index.d.ts` for the 9.x spelling (e.g. `bidirectional`, `rightToLeft`, `tableHeader`) and adjust only the name.

- [ ] **Step 6: Commit**

```bash
git add android/src/core/exportText.ts android/src/core/exportDocx.ts android/test/exportDocx.test.ts
git commit -m "Android core: Word, text and Markdown exports"
```

---

### Task 13: Speaker identification

**Files:**
- Create: `android/src/core/diarization.ts`, `android/test/diarization.test.ts`

**Interfaces:**
- Consumes: `CancelledError` (Task 6).
- Produces: `decodeWav16(bytes: Uint8Array): {pcm: Float32Array; rate: number}`, `voiceStats(pcm, segments): {M: Float64Array[]; N: number[]}`, `VOICE_THRESHOLD = 0.05`, `diarize(pcm, segments: Array<{start: number; end: number}>, opts?: {maxSpeakers?, threshold?, signal?}): Promise<number[]>`, `applySpeakers(segments, labels): number`.

- [ ] **Step 1: Write the failing test** — `android/test/diarization.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/diarization.json";
import { decodeWav16, diarize, voiceStats } from "../src/core/diarization";

function pcmOf(b64: string): Float32Array {
  const bytes = Buffer.from(b64, "base64");
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(bytes.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = v.getInt16(i * 2, true) / 32768;
  return out;
}
const segs = (s: number[][]) => s.map(([start, end]) => ({ start, end }));

describe("parity with desktop diarization.py", () => {
  for (const name of ["two", "one"] as const) {
    it(`${name}: same labels and voice statistics`, async () => {
      const c = fx[name];
      const pcm = pcmOf(c.pcm16);
      expect(await diarize(pcm, segs(c.segments))).toEqual(c.labels);
      const { M, N } = voiceStats(pcm, segs(c.segments));
      expect(N).toEqual(c.N);
      M.forEach((row, i) => row.forEach((v, j) => expect(Math.abs(v - c.M[i][j])).toBeLessThan(0.02)));
    });
  }
  it("one segment or none: one speaker, nothing to compute", async () => {
    expect(await diarize(new Float32Array(16000), [{ start: 0, end: 1 }])).toEqual([0]);
    expect(await diarize(new Float32Array(0), [])).toEqual([]);
  });
});

it("reads a 16-bit mono WAV", () => {
  const n = 4;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, n * 2, true);
  v.setInt16(44, 16384, true);
  const { pcm, rate } = decodeWav16(new Uint8Array(buf));
  expect(rate).toBe(16000);
  expect(pcm[0]).toBeCloseTo(0.5);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/diarization.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/diarization.ts`**

```ts
/** Offline speaker identification (port of desktop core/diarization.py, M38):
 *  each segment's voice is its mean MFCC without c0 (loudness), normalised per
 *  coefficient over the whole recording; segments are compared relative to
 *  their standard error and grouped by average-linkage clustering. */
import { CancelledError } from "./pipeline";

const SR = 16000;
const N_MFCC = 13;
const N_MELS = 26;
const FRAME = 400;
const HOP = 160;
const FFT = 512;
const FRAMES_PER_SECOND = SR / HOP;
const MAX_CLUSTER_ITEMS = 1200;
export const VOICE_THRESHOLD = 0.05;

export interface Segment { start: number; end: number; }

// ---- WAV -------------------------------------------------------------------------
export function decodeWav16(bytes: Uint8Array): { pcm: Float32Array; rate: number } {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("Not a WAV file.");
  let o = 12;
  let rate = 0, channels = 1, bits = 16;
  while (o + 8 <= bytes.length) {
    const id = tag(o);
    const size = v.getUint32(o + 4, true);
    if (id === "fmt ") { channels = v.getUint16(o + 10, true); rate = v.getUint32(o + 12, true); bits = v.getUint16(o + 22, true); }
    if (id === "data") {
      if (bits !== 16) throw new Error("Only 16-bit WAV is supported.");
      const len = Math.min(size, bytes.length - o - 8);
      const frames = Math.floor(len / 2 / channels);
      const pcm = new Float32Array(frames);
      for (let i = 0; i < frames; i++) {
        let s = 0;
        for (let c = 0; c < channels; c++) s += v.getInt16(o + 8 + (i * channels + c) * 2, true);
        pcm[i] = s / channels / 32768;
      }
      return { pcm, rate };
    }
    o += 8 + size + (size % 2);
  }
  throw new Error("The WAV file has no audio data.");
}

// ---- MFCC ------------------------------------------------------------------------
const WIN = Float64Array.from({ length: FRAME }, (_x, n) => 0.54 - 0.46 * Math.cos((2 * Math.PI * n) / (FRAME - 1)));

const MEL_FB: Float64Array[] = (() => {
  const hz2mel = (f: number) => 2595 * Math.log10(1 + f / 700);
  const mel2hz = (m: number) => 700 * (10 ** (m / 2595) - 1);
  const low = hz2mel(0), high = hz2mel(SR / 2);
  const pts = Array.from({ length: N_MELS + 2 }, (_x, i) => mel2hz(low + ((high - low) * i) / (N_MELS + 1)));
  const bins = pts.map((p) => Math.floor(((FFT + 1) * p) / SR));
  const fb: Float64Array[] = [];
  for (let m = 1; m <= N_MELS; m++) {
    const row = new Float64Array(FFT / 2 + 1);
    const [a, b, c] = [bins[m - 1], bins[m], bins[m + 1]];
    for (let k = a; k < b; k++) if (b > a) row[k] = (k - a) / (b - a);
    for (let k = b; k < c; k++) if (c > b) row[k] = (c - k) / (c - b);
    fb.push(row);
  }
  return fb;
})();

const DCT: Float64Array[] = Array.from({ length: N_MFCC }, (_x, k) =>
  Float64Array.from({ length: N_MELS }, (_y, n) => Math.cos(((Math.PI * k) / N_MELS) * (n + 0.5))));

// radix-2 FFT tables for 512 points
const BITREV = (() => { const r = new Uint16Array(FFT); const bitsN = Math.log2(FFT); for (let i = 0; i < FFT; i++) { let x = i, y = 0; for (let b = 0; b < bitsN; b++) { y = (y << 1) | (x & 1); x >>= 1; } r[i] = y; } return r; })();
const COS = Float64Array.from({ length: FFT / 2 }, (_x, i) => Math.cos((-2 * Math.PI * i) / FFT));
const SIN = Float64Array.from({ length: FFT / 2 }, (_x, i) => Math.sin((-2 * Math.PI * i) / FFT));
const RE = new Float64Array(FFT);
const IM = new Float64Array(FFT);
const POWER = new Float64Array(FFT / 2 + 1);
const MEL = new Float64Array(N_MELS);

function powerSpectrum(frame: Float64Array): Float64Array {
  RE.fill(0); IM.fill(0);
  for (let i = 0; i < FRAME; i++) RE[BITREV[i]] = frame[i];
  for (let size = 2; size <= FFT; size <<= 1) {
    const half = size >> 1, step = FFT / size;
    for (let s = 0; s < FFT; s += size) {
      for (let k = 0; k < half; k++) {
        const wr = COS[k * step], wi = SIN[k * step];
        const a = s + k, b = a + half;
        const tr = RE[b] * wr - IM[b] * wi, ti = RE[b] * wi + IM[b] * wr;
        RE[b] = RE[a] - tr; IM[b] = IM[a] - ti; RE[a] += tr; IM[a] += ti;
      }
    }
  }
  for (let k = 0; k <= FFT / 2; k++) POWER[k] = (RE[k] * RE[k] + IM[k] * IM[k]) / FFT;
  return POWER;
}

/** Sum and sum of squares of c1..c12 over a segment's frames, and its count. */
function segmentMfcc(sig: Float32Array, sum: Float64Array, sumSq: Float64Array): number {
  if (sig.length < FRAME) return 0;
  const pre = new Float64Array(sig.length);
  pre[0] = sig[0];
  for (let i = 1; i < sig.length; i++) pre[i] = sig[i] - 0.97 * sig[i - 1];
  const nFrames = 1 + Math.floor((sig.length - FRAME) / HOP);
  const frame = new Float64Array(FRAME);
  for (let f = 0; f < nFrames; f++) {
    const off = f * HOP;
    for (let i = 0; i < FRAME; i++) frame[i] = pre[off + i] * WIN[i];
    const p = powerSpectrum(frame);
    for (let m = 0; m < N_MELS; m++) {
      let s = 0;
      const row = MEL_FB[m];
      for (let k = 0; k <= FFT / 2; k++) s += p[k] * row[k];
      MEL[m] = Math.log(Math.max(s, 1e-10));
    }
    for (let k = 1; k < N_MFCC; k++) {
      let c = 0;
      const row = DCT[k];
      for (let m = 0; m < N_MELS; m++) c += MEL[m] * row[m];
      sum[k - 1] += c;
      sumSq[k - 1] += c * c;
    }
  }
  return nFrames;
}

export function voiceStats(pcm: Float32Array, segments: Segment[]): { M: Float64Array[]; N: number[] } {
  const D = N_MFCC - 1;
  const means: Float64Array[] = [];
  const N: number[] = [];
  const s1 = new Float64Array(D), s2 = new Float64Array(D);
  let total = 0;
  for (const seg of segments) {
    const a = Math.trunc(Math.max(0, seg.start) * SR);
    const b = Math.trunc(Math.max(seg.start, seg.end) * SR);
    const sum = new Float64Array(D), sq = new Float64Array(D);
    const n = segmentMfcc(pcm.subarray(a, Math.min(b, pcm.length)), sum, sq);
    means.push(n ? sum.map((x) => x / n) : new Float64Array(D));
    N.push(n);
    for (let j = 0; j < D; j++) { s1[j] += sum[j]; s2[j] += sq[j]; }
    total += n;
  }
  if (!total) return { M: segments.map(() => new Float64Array(D)), N: segments.map(() => 0) };
  const mu = s1.map((x) => x / total);
  const sd = s2.map((x, j) => Math.sqrt(Math.max(x / total - mu[j] * mu[j], 1e-12)));
  return { M: means.map((m) => m.map((x, j) => (x - mu[j]) / sd[j])), N };
}

function distances(M: Float64Array[], N: number[]): Float64Array[] {
  const n = M.length, D = M[0]?.length ?? 1;
  const sq = M.map((r) => r.reduce((s, x) => s + x * x, 0));
  return Array.from({ length: n }, (_x, i) => {
    const row = new Float64Array(n);
    for (let j = 0; j < n; j++) {
      let dot = 0;
      for (let k = 0; k < D; k++) dot += M[i][k] * M[j][k];
      const diff = Math.sqrt(Math.max(sq[i] + sq[j] - 2 * dot, 0) / D);
      row[j] = diff / Math.sqrt(1 / N[i] + 1 / N[j]) / FRAMES_PER_SECOND;
    }
    return row;
  });
}

/** Average-linkage agglomerative clustering on a distance matrix (Lance–Williams). */
function agglomerative(Dm: Float64Array[], threshold: number, maxK: number, signal?: AbortSignal): number[] {
  const n = Dm.length;
  if (n === 0) return [];
  if (n === 1) return [0];
  const D = Dm.map((r) => Float64Array.from(r));
  for (let i = 0; i < n; i++) D[i][i] = Infinity;
  const sizes = new Float64Array(n).fill(1);
  const members: number[][] = Array.from({ length: n }, (_x, i) => [i]);
  let k = n;
  while (k > 1) {
    if (signal?.aborted) throw new CancelledError("Transcription cancelled.");
    let bi = 0, bj = 1, best = Infinity;
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (D[i][j] < best) { best = D[i][j]; bi = i; bj = j; }
    let i = bi, j = bj;
    if (i > j) [i, j] = [j, i];
    if (best > threshold && k <= maxK) break;
    const si = sizes[i], sj = sizes[j];
    for (let c = 0; c < n; c++) { const v = (si * D[i][c] + sj * D[j][c]) / (si + sj); D[i][c] = v; D[c][i] = v; }
    D[i][i] = Infinity;
    for (let c = 0; c < n; c++) { D[j][c] = Infinity; D[c][j] = Infinity; }
    sizes[i] = si + sj;
    members[i].push(...members[j]);
    members[j] = [];
    k -= 1;
    if (best > threshold && k <= maxK) break;
  }
  const labels = new Array<number>(n).fill(0);
  let cid = 0;
  for (const g of members) { if (!g.length) continue; for (const idx of g) labels[idx] = cid; cid += 1; }
  return labels;
}

function clusterVoices(M: Float64Array[], N: number[], durations: number[], threshold: number, maxK: number, signal?: AbortSignal): number[] {
  const n = M.length;
  const labels = new Array<number>(n).fill(0);
  const real = N.map((x, i) => (x > 0 ? i : -1)).filter((i) => i >= 0);
  if (real.length <= 1) return labels;
  let keep = real;
  if (real.length > MAX_CLUSTER_ITEMS) {
    keep = [...real].sort((a, b) => durations[b] - durations[a] || a - b).slice(0, MAX_CLUSTER_ITEMS).sort((a, b) => a - b);
  }
  const sub = agglomerative(distances(keep.map((i) => M[i]), keep.map((i) => N[i])), threshold, maxK, signal);
  keep.forEach((idx, k) => { labels[idx] = sub[k]; });
  if (keep.length < real.length) {
    const kset = new Set(keep);
    const K = Math.max(...sub) + 1;
    const D = M[0].length;
    const cent: Float64Array[] = [], cn: number[] = [];
    for (let c = 0; c < K; c++) {
      const acc = new Float64Array(D); let w = 0;
      keep.forEach((idx, k) => { if (sub[k] === c) { for (let j = 0; j < D; j++) acc[j] += M[idx][j] * N[idx]; w += N[idx]; } });
      cent.push(acc.map((x) => x / w)); cn.push(w);
    }
    for (const idx of real) {
      if (kset.has(idx)) continue;
      const d = distances([M[idx], ...cent], [N[idx], ...cn])[0].slice(1);
      let best = 0; for (let c = 1; c < d.length; c++) if (d[c] < d[best]) best = c;
      labels[idx] = best;
    }
  }
  let last = labels[real[0]];
  for (let i = 0; i < n; i++) { if (N[i] > 0) last = labels[i]; else labels[i] = last; }
  return labels;
}

/** Speaker index (0-based, in order of first appearance) for each segment. */
export async function diarize(pcm: Float32Array, segments: Segment[],
  opts: { maxSpeakers?: number; threshold?: number; signal?: AbortSignal } = {}): Promise<number[]> {
  if (segments.length <= 1) return segments.map(() => 0);
  if (!pcm.length) return segments.map(() => 0);
  await Promise.resolve();                               // let the UI breathe before a long run
  const { M, N } = voiceStats(pcm, segments);
  if (opts.signal?.aborted) throw new CancelledError("Transcription cancelled.");
  const durations = segments.map((s) => Math.max(0, s.end - s.start));
  const labels = clusterVoices(M, N, durations, opts.threshold ?? VOICE_THRESHOLD, opts.maxSpeakers ?? 4, opts.signal);
  const remap = new Map<number, number>();
  return labels.map((l) => { if (!remap.has(l)) remap.set(l, remap.size); return remap.get(l)!; });
}

/** Write "Speaker N" onto each segment; returns the number of speakers. */
export function applySpeakers(segments: Array<{ speaker?: string }>, labels: number[]): number {
  segments.forEach((s, i) => { s.speaker = `Speaker ${(labels[i] ?? 0) + 1}`; });
  return labels.length ? Math.max(...labels) + 1 : 0;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd android && npx vitest run test/diarization.test.ts`
Expected: PASS. If labels match but `M` differs by more than 0.02, compare the mel filterbank against desktop `_mel_filterbank()` first (bin rounding).

- [ ] **Step 5: Commit**

```bash
git add android/src/core/diarization.ts android/test/diarization.test.ts
git commit -m "Android core: speaker identification (parity with desktop M38)"
```

---

### Task 14: Insights

**Files:**
- Create: `android/src/core/insights.ts`, `android/test/insights.test.ts`

**Interfaces:**
- Consumes: `ActionItem`, `effectiveStatus`, `isOverdue`, `normalizeStatus`, `isPlaceholder` (Task 8); `dates.ts`; `LETTER` (Task 2).
- Produces: `interface MeetingLite {id, title, createdAt, minutes}`, `interface Insights {totalMeetings, totalItems, openItems, overdueItems, completionRate, statusCounts, byOwner, cadence, keywords}`, `themeWords(text)`, `computeInsights(meetings, items, opts?: {weeks?, topOwners?, topKeywords?, today?})`.

- [ ] **Step 1: Write the failing test** — `android/test/insights.test.ts`

```ts
import { expect, it } from "vitest";
import fx from "./fixtures/insights.json";
import type { ActionItem } from "../src/core/actionItems";
import { computeInsights, themeWords } from "../src/core/insights";

const items: ActionItem[] = fx.items.map((i: Record<string, unknown>) => ({
  task: String(i.task), owner: String(i.owner), deadline: String(i.deadline), status: String(i.status), priority: String(i.priority),
  notes: String(i.notes), meetingId: String(i.meeting_id), meetingTitle: String(i.meeting_title), meetingDate: String(i.meeting_date),
  okey: String(i.okey), occ: Number(i.occ) }));

it("matches desktop compute_insights", () => {
  expect(computeInsights(fx.meetings, items, { today: fx.today })).toEqual(fx.result);
});

it("theme words: any script, stopwords and Arabic diacritics removed", () => {
  expect(themeWords("The meeting discussed BUDGET budgets; الْمِيزَانِيَّة على")).toEqual(["budget", "budgets", "الميزانية"]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/insights.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/insights.ts`**

```ts
/** Cross-meeting insights for the dashboard (port of desktop core/insights.py). */
import data from "./data/desktop.json";
import { type ActionItem, effectiveStatus, isOverdue, isPlaceholder, normalizeStatus } from "./actionItems";
import { type ISODate, addDays, localDateOf, todayLocal, weekday } from "./dates";
import { LETTER } from "./unicode";

export interface MeetingLite { id: string; title: string; createdAt: number; minutes: string; }
export interface Insights {
  totalMeetings: number; totalItems: number; openItems: number; overdueItems: number; completionRate: number;
  statusCounts: Record<string, number>; byOwner: Array<[string, number]>; cadence: Array<[string, number]>; keywords: Array<[string, number]>;
}

const STATUS_ORDER: string[] = data.statusOrder;
const STOPWORDS = new Set<string>(data.insightsStopwords);
const MARKS = new Set<number>(data.arabicMarks);
const WORD_RE = new RegExp(`[${LETTER}]+`, "gu");
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function themeWords(text: string): string[] {
  const t = Array.from((text ?? "").toLowerCase()).filter((ch) => !MARKS.has(ch.codePointAt(0)!)).join("");
  const out: string[] = [];
  for (const m of t.matchAll(WORD_RE)) {
    const w = m[0];
    if (STOPWORDS.has(w)) continue;
    if (w.length >= (/^[\x00-\x7f]*$/.test(w) ? 4 : 3)) out.push(w);
  }
  return out;
}

/** Counter.most_common: by count, ties in first-seen order. */
function mostCommon(keys: string[], top: number): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const k of keys) counts.set(k, (counts.get(k) ?? 0) + 1);
  return [...counts.entries()].map((e, i) => [e, i] as const)
    .sort((a, b) => b[0][1] - a[0][1] || a[1] - b[1]).slice(0, top).map(([e]) => e);
}

const weekStart = (d: ISODate) => addDays(d, -weekday(d));

export function computeInsights(meetings: MeetingLite[], items: ActionItem[],
  opts: { weeks?: number; topOwners?: number; topKeywords?: number; today?: ISODate } = {}): Insights {
  const today = opts.today ?? todayLocal();
  const weeks = opts.weeks ?? 8;
  const sc = new Map<string, number>();
  for (const it of items) { const s = effectiveStatus(it, today); sc.set(s, (sc.get(s) ?? 0) + 1); }
  const statusCounts: Record<string, number> = {};
  for (const k of STATUS_ORDER) if (sc.get(k)) statusCounts[k] = sc.get(k)!;
  const closed = (it: ActionItem) => ["Completed", "Cancelled"].includes(normalizeStatus(it.status));
  const openItems = items.filter((it) => !closed(it)).length;
  const overdueItems = items.filter((it) => isOverdue(it, today)).length;
  const nonCancelled = items.filter((it) => normalizeStatus(it.status) !== "Cancelled").length;
  const completed = items.filter((it) => normalizeStatus(it.status) === "Completed").length;

  const owners: string[] = [];
  for (const it of items) {
    if (closed(it)) continue;
    const o = (it.owner ?? "").trim();
    if (o && !isPlaceholder(o)) owners.push(o);
  }

  const cad = new Map<string, number>();
  for (const m of meetings) {
    if (!m.createdAt) continue;
    const w = weekStart(localDateOf(m.createdAt));
    cad.set(w, (cad.get(w) ?? 0) + 1);
  }
  const end = weekStart(today);
  const cadence: Array<[string, number]> = [];
  for (let i = weeks - 1; i >= 0; i--) {
    const w = addDays(end, -7 * i);
    cadence.push([`${w.slice(8, 10)} ${MON[Number(w.slice(5, 7)) - 1]}`, cad.get(w) ?? 0]);
  }

  const words = meetings.flatMap((m) => themeWords(m.minutes ?? ""));
  return {
    totalMeetings: meetings.length, totalItems: items.length, openItems, overdueItems,
    completionRate: nonCancelled ? completed / nonCancelled : 0, statusCounts,
    byOwner: mostCommon(owners, opts.topOwners ?? 8), cadence, keywords: mostCommon(words, opts.topKeywords ?? 12),
  };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd android && npx vitest run test/insights.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/insights.ts android/test/insights.test.ts
git commit -m "Android core: insights (parity with desktop)"
```

---

### Task 15: Update selection

**Files:**
- Create: `android/src/core/updater.ts`, `android/test/updater.test.ts`

**Interfaces:**
- Produces: `parseVersion(v): number[]`, `isNewer(latest, current): boolean`, `parseChecksum(text, filename?, allowBare?): string`, `ANDROID_TAG_PREFIX = "android-v"`, `interface GithubRelease`, `interface AndroidUpdate {version, apkUrl, apkName, size, checksumUrl, checksumIsSums, notes, publishedAt, pageUrl}`, `pickAndroidRelease(releases): AndroidUpdate | null`.

- [ ] **Step 1: Write the failing test** — `android/test/updater.test.ts`

```ts
import { describe, expect, it } from "vitest";
import fx from "./fixtures/updater.json";
import { isNewer, parseChecksum, pickAndroidRelease, type GithubRelease } from "../src/core/updater";

describe("parity with desktop updater.py", () => {
  it("version comparison", () => { for (const c of fx.newer) expect([c.latest, c.current, isNewer(c.latest, c.current)]).toEqual([c.latest, c.current, c.output]); });
  it("checksums", () => { for (const c of fx.checksum) expect(parseChecksum(c.text, c.file, c.bare)).toBe(c.output); });
});

const rel = (tag: string, assets: string[], extra: Partial<GithubRelease> = {}): GithubRelease => ({
  tag_name: tag, prerelease: false, draft: false, html_url: `https://x/${tag}`, body: "notes", published_at: "2026-10-20T00:00:00Z",
  assets: assets.map((name) => ({ name, browser_download_url: `https://dl/${tag}/${name}`, size: 1000 })), ...extra });

describe("picking the Android release", () => {
  it("ignores desktop, draft and pre-releases; takes the highest android version", () => {
    const pick = pickAndroidRelease([
      rel("v1.2.4", ["MICO360Meetings-Setup.exe"]),
      rel("android-v1.1.0", ["MICO360Meetings-Android-1.1.0.apk", "MICO360Meetings-Android-1.1.0.apk.sha256"]),
      rel("android-v1.2.0-rc1", ["a.apk"], { prerelease: true }),
      rel("android-v1.3.0", ["a.apk"], { draft: true }),
      rel("android-v1.0.5", ["b.apk"]),
    ])!;
    expect(pick.version).toBe("1.1.0");
    expect(pick.apkUrl).toBe("https://dl/android-v1.1.0/MICO360Meetings-Android-1.1.0.apk");
    expect(pick.checksumUrl).toBe("https://dl/android-v1.1.0/MICO360Meetings-Android-1.1.0.apk.sha256");
  });
  it("falls back to SHA256SUMS.txt; no APK means no update", () => {
    expect(pickAndroidRelease([rel("android-v2.0.0", ["x.apk", "SHA256SUMS.txt"])])!.checksumIsSums).toBe(true);
    expect(pickAndroidRelease([rel("android-v2.0.0", ["notes.txt"])])).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && npx vitest run test/updater.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: `android/src/core/updater.ts`**

```ts
/** Choosing and verifying Android updates from GitHub Releases (version and
 *  checksum rules ported from desktop core/updater.py). */
export const ANDROID_TAG_PREFIX = "android-v";

/** Comparable key: up to 4 numeric parts; a pre-release ranks below the final. */
export function parseVersion(v: string): number[] {
  const s = (v ?? "").trim().replace(/^[vV]+/, "");
  const dash = s.indexOf("-");
  const core = dash >= 0 ? s.slice(0, dash) : s;
  const nums = (core.match(/\d+/g) ?? []).slice(0, 4).map(Number);
  const key = nums.length ? nums : [0];
  while (key.length < 4) key.push(0);
  return [...key, dash >= 0 ? 0 : 1];
}

export function isNewer(latest: string, current: string): boolean {
  const a = parseVersion(latest), b = parseVersion(current);
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}

const SHA256_RE = /(?<![A-Za-z0-9_])([0-9a-fA-F]{64})(?![A-Za-z0-9_])/;
const SHA256_ALL = new RegExp(SHA256_RE.source, "g");
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The SHA256 for `filename` in a checksums text; only a line naming that file
 *  counts. `allowBare` accepts a lone hash (a per-file .sha256 asset). */
export function parseChecksum(text: string, filename = "", allowBare = false): string {
  if (!text) return "";
  const fn = filename.toLowerCase();
  if (fn) {
    const nameRe = new RegExp(`(?<![A-Za-z0-9_.-])\\*?${escapeRe(fn)}(?![A-Za-z0-9_.-])`, "i");
    for (const line of text.split(/\r\n|\n|\r/)) {
      if (nameRe.test(line)) { const m = SHA256_RE.exec(line); if (m) return m[1].toLowerCase(); }
    }
    if (!allowBare) return "";
    const hashes = new Set([...text.matchAll(SHA256_ALL)].map((m) => m[1].toLowerCase()));
    return hashes.size === 1 ? [...hashes][0] : "";
  }
  const m = SHA256_RE.exec(text);
  return m ? m[1].toLowerCase() : "";
}

export interface GithubAsset { name: string; browser_download_url: string; size: number; }
export interface GithubRelease {
  tag_name: string; prerelease: boolean; draft: boolean; html_url: string; body: string; published_at: string; assets: GithubAsset[];
}
export interface AndroidUpdate {
  version: string; apkUrl: string; apkName: string; size: number; checksumUrl: string; checksumIsSums: boolean;
  notes: string; publishedAt: string; pageUrl: string;
}

/** The newest published Android release that carries an APK, or null. */
export function pickAndroidRelease(releases: GithubRelease[]): AndroidUpdate | null {
  let best: AndroidUpdate | null = null;
  for (const r of releases ?? []) {
    if (!r || r.draft || r.prerelease || !String(r.tag_name ?? "").startsWith(ANDROID_TAG_PREFIX)) continue;
    const apk = (r.assets ?? []).find((a) => a.name.toLowerCase().endsWith(".apk"));
    if (!apk) continue;
    const version = r.tag_name.slice(ANDROID_TAG_PREFIX.length);
    if (best && !isNewer(version, best.version)) continue;
    const own = r.assets.find((a) => a.name.toLowerCase() === `${apk.name.toLowerCase()}.sha256`);
    const sums = r.assets.find((a) => a.name.toLowerCase() === "sha256sums.txt");
    best = { version, apkUrl: apk.browser_download_url, apkName: apk.name, size: apk.size,
      checksumUrl: (own ?? sums)?.browser_download_url ?? "", checksumIsSums: !own && !!sums,
      notes: r.body ?? "", publishedAt: r.published_at ?? "", pageUrl: r.html_url ?? "" };
  }
  return best;
}
```

- [ ] **Step 4: Run all tests and typecheck**

Run: `cd android && npm test && npm run typecheck`
Expected: every suite PASS; tsc exits 0.

- [ ] **Step 5: Commit**

```bash
git add android/src/core/updater.ts android/test/updater.test.ts
git commit -m "Android core: update selection and checksum parsing"
```

---

## Self-review

- **Spec coverage (core parts of spec §3.1):** mdBlocks T3, prompts/meetingTypes T5, pipeline T6, cloudClient T7 (spec §5.1 row by row), actionItems T8, ics T9, profiles T10, insights T14, diarization T13, updater T15, rtl T3, exportHtml T11, docx T12, txt/md T12. Storage, device wrappers, screens, release workflow and the cleartext consent prompt belong to Plans 2 and 3.
- **Placeholders:** none; every code step is complete.
- **Type consistency:** `ActionItem` (T8) is used unchanged by T9, T11, T14; `CompanyProfile` (T10) by T11, T12; `CancelledError` (T6) by T7, T13; `HttpTransport` (T7) is the only network path.
- **Review Focus:** items 1–5 map to tests in T5 (`$&`, `{idx}`), T3 (emoji, lone surrogates), T3/T4/T11/T12 (Arabic), T7 (32,000 limit on a long meeting), T10/T11 (hostile profile values).
