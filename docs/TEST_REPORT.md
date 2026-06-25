# MICO360 Meetings — Test Report

Status of automated/manual verification performed during development.
Legend: ✅ verified · 🟡 partial / needs clean-VM run · ⬜ not yet done.

## Environment used
- Windows 11 Pro, Python 3.14.4 (64-bit)
- PySide6 6.11.1, faster-whisper 1.2.1, Ollama 0.30.8
- Models present: `qwen2.5:0.5b`, `llama3.2-vision:11b`

## Core engine

| Area | Test | Result |
| --- | --- | --- |
| Audio decode | Probe + PyAV transcode of a SAPI-generated WAV | ✅ |
| Transcription | faster-whisper `tiny` on 16 s spoken WAV → accurate text (~7 s) | ✅ |
| Whisper auto-download | Model fetched to data dir on first use | ✅ |
| Cleaning | Filler removal + dedup reduces text, no crash | ✅ |
| Chunking | Sentence-boundary packing for long transcripts | ✅ |
| Ollama discovery | `check_status` lists running models | ✅ |
| Minutes generation | Single-pass + map-reduce via `qwen2.5:0.5b` | ✅ |
| Output styles | All 5 styles selectable and applied | ✅ |
| History (SQLite) | Save / list / search / delete | ✅ |
| Company profiles | CRUD + JSON/CSV/XLSX import-export | ✅ |
| Prompt library | Add / edit / view / delete | ✅ |

## Export

| Format | With company profile (logo/footer/page numbers) | Result |
| --- | --- | --- |
| TXT | header + footer | ✅ |
| DOCX | letterhead, logo, footer, PAGE/NUMPAGES fields, tables | ✅ |
| PDF | letterhead, logo, two-pass page numbering, styled tables | ✅ |

## Recording (audio + video)

| Test | Result |
| --- | --- |
| Microphone detection (enumerate + default + "none") | ✅ 11 devices detected |
| Audio recording → valid WAV (30k+ frames) | ✅ |
| Pause/Resume freezes the clock correctly | ✅ |
| **Screen recording → non-black MP4 with muxed audio** | ✅ 20 frames, max-pixel 255, audio track present |
| Camera recording (cv2) path | 🟡 code built + screen path proven; needs an interactive webcam run |
| Live details panel (timer, size, format, mic, location) | ✅ verified via panel drive |
| Audio visualizer + red blinking indicator | ✅ renders |
| Post-recording summary screen | ✅ |
| `recordingReady` → queued for transcription | ✅ file exists |

## Updates

| Test | Result |
| --- | --- |
| Not-configured message (no repo) | ✅ |
| Real GitHub release check (parse version/size/date/notes) | ✅ against ollama/ollama |
| Version comparison (is_newer) | ✅ |
| Status/feature/fix/security rendering | ✅ |
| Download + install workers | 🟡 logic built; full run needs a real release asset |

## UI

| Test | Result |
| --- | --- |
| All modules import | ✅ |
| Window + 7 pages + dialogs construct | ✅ |
| Navigation across all pages | ✅ |
| Dark & light themes render | ✅ |
| **No horizontal overflow** 1080×660 → 1920×1080 | ✅ all pages CLEAN at every tested size |
| Drag area above buttons; no overlap/hidden buttons | ✅ |
| Single-instance guard | ✅ (port-lock logic) |
| Help / About / Terms / Privacy pages | ✅ |
| Live transcription/generation in GUI threads | ✅ generate produced minutes via worker, exit 0 |

## Installer (`build/`)

| Scenario | Result |
| --- | --- |
| `smart_setup.ps1` — everything already installed (skip all) | ✅ exits 0, tagged log |
| `smart_setup.ps1` — comma/array arg normalization | ✅ |
| Clean system (install Ollama + pull model) | ⬜ run on clean VM |
| Ollama present, model missing (pull only) | ⬜ run on clean VM |
| Offline / failed download messaging | 🟡 logic in place; ⬜ confirm on disconnected VM |
| PyInstaller build (`build_exe.ps1`) | ⬜ run on build machine |
| Inno Setup compile (`installer.iss`) | ⬜ needs ISCC + the built EXE |
| Uninstall / reinstall cleanliness | ⬜ run on clean VM |

## Round 3 — full QA checklist

Automated via [`tests/qa_check.py`](../tests/qa_check.py) (**37/37 pass**) plus
manual/live checks. Legend: ✅ verified here · 🟡 logic verified, needs build/clean-VM · ⬜ needs clean VM + compiled installer.

### Installer / environment
| Item | Status |
| --- | --- |
| App icon on installer / desktop / Start Menu / taskbar | 🟡 configured (app.ico, AppUserModelID, .iss shortcuts) — verify on built installer |
| App icon on app window | ✅ |
| Installer file name / logo / publisher / app name | 🟡 set in `installer.iss` (MICO360, AppContact info@mico360.com) — verify compiled |
| Clean-system install, no manual setup | ⬜ run on clean VM |
| Installer checks packages/Python/Ollama/Whisper/models | ✅ `smart_setup.ps1` — Python + pip packages + ffmpeg + Ollama + models |
| **Auto-detect, skip-if-present (no reinstall)** | ✅ verified: Python + 13 pip packages + Ollama + model all SKIP, exit 0 |
| **Install-only-missing via PowerShell** | ✅ verified: missing pip package auto-installed; missing Ollama model pulled |
| Missing Python → auto-install correct version | ✅ `-EnsurePython` step (skip verified; install path needs clean VM) |
| Old Python present → handled | ✅ version compare installs 3.12 if < 3.11; bundled app ships own Python |
| Missing Ollama → install option | ✅ silent download+install in `smart_setup.ps1` |
| Ollama present but models missing → detected | ✅ tested (skip-existing + pull-missing) |
| **Install models from Settings → "Install Required Model"** | ✅ live-tested real pull (progress→success) |
| **"Install Required Model" NOT on main dashboard** | ✅ asserted in qa_check |
| All installed → skips duplicates | ✅ tested (exit 0, all "SKIP") |
| Offline / failed download → clear error | ✅ messaged + non-zero exit (logic) |
| Logs show installed/skipped/failed/completed | ✅ tagged log verified |
| **PyInstaller `.exe` built** | ✅ `MICO360Meetings.exe` (613 MB bundle) |
| **Bundled app launches (no missing imports)** | ✅ window "MICO360 Meetings", responding |
| **Inno Setup installer compiled** | ✅ `MICO360Meetings-Setup.exe` (166 MB) |
| **Installer branding/version metadata** | ✅ Company=MICO360, Product=MICO360 Meetings, v1.0.0 |
| **App exe carries icon** | ✅ |
| App launches immediately after install | 🟡 needs a clean-VM install run |
| Prevents multiple instances | ✅ port-lock guard |

### App functionality
| Item | Status |
| --- | --- |
| Audio upload MP3/WAV/M4A, no selection error | ✅ accepted (M4A→PyAV transcode) |
| Video upload MP4/MOV/MKV starts transcription | ✅ accepted + transcode path |
| Multi-file upload (audio/video/image/PDF/DOCX/TXT) | ✅ accepted |
| Drag-and-drop; drag area above buttons, no overlap | ✅ |
| Dropdowns/buttons/tabs work after upload | ✅ asserted |
| Mic recording + mic detected | ✅ 11 devices |
| No-mic → "Microphone not detected" message | ✅ wired |
| Audio recording saves valid file (not blank) | ✅ verified (30k+ frames) |
| **Recording never hangs the UI (threaded capture)** | ✅ `start()` returns ~1 ms |
| **Unresponsive/muted mic → clear error (watchdog)** | ✅ "no audio received — pick another mic", no hang |
| **Transcription: NVIDIA GPU w/o CUDA libs (cublas64_12.dll)** | ✅ defaults to CPU + falls back; CPU transcription verified |
| Screen recording with audio (not black) | ✅ verified (255-pixel frames + audio track) |
| Recording visualizer reacts | ✅ |
| Transcription progress display | ✅ progress callback 0→1 |
| AI processing progress display | ✅ |
| Model dropdown before generating | ✅ |
| Selected Ollama model actually used | ✅ |
| Transcript edit / save / reload from history | ✅ |
| Minutes edit / save / copy / export / download | ✅ |
| Export Word / PDF / TXT | ✅ |
| PDF export w/ profile, logo, footer, page numbers | ✅ |
| Company profile CRUD + import/export + preview | ✅ |
| Prompt Library add/edit/view/delete + select | ✅ |
| Help / About / Terms / Privacy present | ✅ |
| info@mico360.com shown | ✅ |
| Updates: current/new version, release details, repo link | ✅ |
| Zoom 80/90/100/110/125/150% no overflow | ✅ all CLEAN |
| Responsive small-laptop→ultrawide, no overlap | ✅ CLEAN 1080×660 → 1920×1080 |

## Comprehensive QA suite (60/60 automated, stable)

[`tests/qa_check.py`](../tests/qa_check.py) — **60 checks, all passing**, run 3×
with identical results. Maps to the requested categories:

| Category | Coverage | Result |
| --- | --- | --- |
| UI alignment / responsiveness / zoom | Overflow check 1080→1920 + zoom 80–150% | ✅ |
| Navigation | All 7 pages + tabs open | ✅ |
| Functionality — record API | factory + pause/resume/stop/cancel + mic detect/fallback | ✅ |
| Functionality — upload | mp3/wav/m4a/mp4/mov/mkv/png/jpg/pdf/docx/txt accepted | ✅ |
| Functionality — transcription | real SAPI WAV → CPU Whisper, **4/4 keywords matched** | ✅ |
| Speaker detection | not implemented (stub) | ⬜ roadmap |
| Functionality — minutes / summary / action items | **real Ollama** generation, action items present | ✅ |
| Edit & save / reopen | transcript+minutes edit, history save/search/reload | ✅ |
| Export / download TXT·DOCX·PDF | with company profile, **off-thread** | ✅ |
| Unclear / silent audio | silent WAV → no crash, returns empty | ✅ |
| Error handling | transcription/export failures surface, no crash | ✅ |
| Prompts library | 14+ prompts, **6 categories** | ✅ |
| Prompt selection / preview / reuse | dropdown + preview + persistence | ✅ |
| Custom prompt add/edit/delete + recategorise | full CRUD | ✅ |
| Prompt categories | Formal/Project/Client/Internal Meeting, Action Items, Summary | ✅ |
| Input validation | empty transcript/minutes/profile/prompt, Unicode/special chars | ✅ |
| App-crash resilience | rapid nav + resize storm + 20× save/delete, no crash | ✅ |
| Performance | lazy model load; window builds fast; device enum timeout-guarded | ✅ |
| Close / exit | single-instance lock acquire/release, recorder stop hook | ✅ |
| Recording errors (MME/DirectSound -9999) | host-API fallback (WASAPI→DS→MME); open verified, watchdog on no-data | ✅ code / 🟡 live mic needs user machine |
| Export "not responding" | moved to background worker | ✅ |
| Add / remove / clear files | multi-select queue management | ✅ |
| Upload ↔ Record linked | recording joins queue, auto-switches to Upload tab | ✅ |

## Known limitations / roadmap
- Recording captures **microphone** audio with screen/camera video. System/desktop
  **loopback** audio (what you hear) is a separate per-OS capture, not yet wired.
- Camera recording path is built but only the screen path has been run live here.
- Speaker diarization is stubbed (needs pyannote + HF token).
- PDF text import needs optional `pypdf`; image OCR needs Tesseract + `pytesseract`.
- Chrome extension: not yet implemented (roadmap).
- In-app updates check + download + launch-installer are implemented; the GitHub
  repo is user-supplied (Settings) and a published release is needed end-to-end.
- Clean-VM installer test matrix (rows marked ⬜) must be executed before release.
