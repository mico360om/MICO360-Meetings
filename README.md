# MICO360 Meetings

**Offline AI meeting-minutes assistant.** Upload audio/video or paste a
transcript, transcribe it locally with Whisper, and generate professional
meeting minutes with a local Ollama model — **no data ever leaves your computer.**

![MICO360 Meetings](assets/logo_256.png)

---

## ✨ Features

- **Multiple inputs** — drag & drop audio (MP3/WAV/M4A), video (MP4/MOV/MKV),
  documents (PDF/DOCX/TXT), images, or paste a transcript. Multiple files at once.
- **Recording** — microphone (WAV/MP3), **screen + audio** and **camera + audio**
  (MP4). **Audio source: Microphone, System audio (what you hear), or both** —
  capture online-meeting participants, not just yourself. Live details panel,
  red blinking indicator, audio **visualizer**, Pause/Resume/Stop/Cancel, summary.
- **Speed ⇄ Quality presets** (Fast/Balanced/Accurate) and **offline speaker
  identification** (Speaker 1/2/3, beta).
- **Rendered minutes preview** + export to **DOCX/PDF/TXT/Markdown/HTML** and
  **copy-with-formatting**.
- **First-run setup guide**, keyboard shortcuts, drag-to-reorder file queue.
- **Application Updates** page — checks GitHub Releases and shows current vs.
  new version, status, size, release date, description, new features, bug fixes,
  security improvements, download progress, restart note, and a direct repo link.
- **Offline transcription** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  (tiny → large-v3), with live progress.
- **Local AI minutes** via [Ollama](https://ollama.com) — Llama 3.1, Qwen, Mistral, Gemma…
- **5 output styles:** Formal Minutes · Short Summary · Detailed Minutes ·
  Action Item Report · Executive Summary. **Model and style are chosen right next
  to the Generate button.**
- **Editable everything** — transcript and minutes are fully editable before export.
- **Export** to **Word (.docx)**, **PDF**, and **TXT**, with a chosen
  **company profile** (logo, letterhead, footer, page numbers).
- **Company Profiles** — create multiple branded profiles; import/export
  JSON/CSV/XLSX; live layout preview.
- **Prompt Library** — add / edit / view / delete reusable prompts; edit the
  prompt per generation.
- **Local history** with full-text search.
- **Action Items tracker** — every task from every meeting in one filterable
  list (owner · deadline · status), editable status, CSV export.
- **Meeting-type presets** (prompt + style + profile in one click) and **calendar
  (.ics) import** to pre-fill title / date / attendees.
- **Autosave** (never lose a transcript) + native undo/redo, and **in-place
  auto-update** (download → install → relaunch) from GitHub Releases.
- **Opt-in crash reporter** — on an unexpected error, bundles the traceback +
  recent log locally and (only if you click) opens a pre-filled GitHub issue in
  your browser. Nothing is ever sent automatically; toggle in Settings.
- **Help / About / Terms & Conditions / Privacy Policy** built in (support: info@mico360.com).
- **Dark & light themes**, fully responsive layout (no overflow 1080px → 4K).
- **Single-instance** guard, rotating error logs, low-resource friendly (int8).

---

## 🚀 Quick start (from source)

### 1. Prerequisites
- **Python 3.11 – 3.14** (64-bit)
- **[Ollama](https://ollama.com/download)** installed and running

### 2. Install Python dependencies
```powershell
cd "MICO360-Meeting"
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Install an Ollama model
Easiest: open the app → **Settings → Install AI model → "Install Required Model"**
(downloads with a progress bar, skips anything already present). Or from a terminal:
```powershell
ollama pull llama3.1          # recommended (4.7 GB)
# lighter options:
ollama pull qwen2.5:3b
ollama pull gemma2:2b
```
Make sure the server is running (the installer does this automatically; from
source just keep the Ollama app open, or run `ollama serve`).

### 4. Whisper models
Nothing to do — the first time you transcribe, the selected Whisper model is
**downloaded automatically** to your data folder and cached for offline use.
Choose the model under **Settings → Whisper model** (`tiny` is fastest,
`large-v3` is most accurate).

### 5. Run
```powershell
python run.py
```

---

## 🖥️ Using the app

1. **New Meeting → Step 1** — drop a file, record your mic, or just paste text.
2. **Step 2** — review/edit the transcript.
3. **Step 3** — pick the **Ollama model**, the **output style**, and (optionally)
   tweak the **prompt** for this run, then click **Generate minutes**.
4. **Step 4** — edit the minutes, **Copy**, **Save to history**, or **Export…**.

Set an active **Company Profile** (Company Profiles → *Set as active*) to brand
your PDF/Word exports with a logo, footer and page numbers.

---

## 📁 Where data is stored

Everything is local, under:

| OS | Path |
| --- | --- |
| Windows | `%LOCALAPPDATA%\MICO360Meetings` |
| macOS | `~/Library/Application Support/MICO360Meetings` |
| Linux | `~/.local/share/MICO360Meetings` |

Subfolders: `history/` (SQLite `mico360.db`), `profiles/`, `prompts/`,
`whisper-models/`, `logs/`, `exports/`, `tmp/`.

---

## 🧰 Troubleshooting

| Problem | Fix |
| --- | --- |
| "Ollama offline" in the sidebar | Start Ollama (open the app or `ollama serve`) and click a model refresh. |
| No models in the dropdown | `ollama pull llama3.1` then **Settings → Refresh models**. |
| `.m4a` / `.mov` won't load | The app transcodes these automatically via PyAV; if it still fails, the error is shown and logged. |
| PDF text import fails | `pip install pypdf` to enable PDF text extraction. |
| Image text import fails | Needs Tesseract OCR + `pip install pytesseract`. |
| Slow transcription | Use a smaller Whisper model (`tiny`/`base`) and `int8` compute in Settings. |

Logs: `…\MICO360Meetings\logs\app.log`.

---

## 📦 Building the Windows installer

See [`build/README_BUILD.md`](build/README_BUILD.md) for packaging the app into a
standalone `.exe` (PyInstaller) and a smart installer (Inno Setup) that detects
and installs Ollama + required models automatically.

---

## 🗂️ Project layout

```
mico360/
  main.py               entry point + single-instance guard
  config.py             paths, settings, logging
  core/
    audio.py            PyAV decode/transcode (m4a/mp4/mkv safe)
    transcription.py    faster-whisper engine + progress
    cleaning.py         filler removal, dedup, chunking
    prompts.py          5 styles + Prompt Library
    ollama_client.py    model discovery + map-reduce minutes
    history.py          SQLite history + search
    profiles.py         company profiles + import/export
    documents.py        pdf/docx/txt/image text extraction
  export/
    md_blocks.py        markdown block parser
    txt_export.py / docx_export.py / pdf_export.py / service.py
  ui/
    theme.py components.py workers.py dialogs.py
    context.py pages.py main_window.py
assets/                 logo + app icon
samples/                example transcript, sample exports
build/                  PyInstaller + Inno Setup installer
```

---

## 🔒 Privacy

100% local. Transcription (Whisper) and summarization (Ollama) run on your
machine. The app makes **no network calls** except the one-time, optional
download of a Whisper/Ollama model.

## License

Proprietary — © MICO360. Replace `assets/logo.png` and `assets/app.ico` with
your brand assets.
