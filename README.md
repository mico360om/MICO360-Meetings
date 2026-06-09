# MICO360 Meetings

MICO360 Meetings is a local-first desktop assistant for turning meeting audio, video, or pasted transcripts into professional minutes. It uses local Whisper-compatible transcription tools and local Ollama models. Meeting content is not sent to external servers.

## Features

- Upload multiple files at once
- Upload audio: MP3, WAV, M4A
- Upload video: MP4, MOV, MKV, WebM
- Upload transcripts and notes: TXT, MD, CSV, JSON
- Upload documents: PDF and DOCX
- Attach image references: PNG, JPG, JPEG, WebP, BMP, GIF, TIFF
- Paste transcript manually
- Record microphone audio
- Record screen with audio when supported by the operating system
- Local transcription through `faster-whisper`, OpenAI Whisper CLI, or `whisper.cpp`
- Local AI generation through Ollama models such as Llama 3.1, Qwen, Mistral, and Gemma
- Editable minutes output
- Formal Minutes, Short Summary, Detailed Minutes, Action Item Report, and Executive Summary styles
- Custom prompt template per upload
- Prompt Library with add, view/edit, and delete controls
- DOCX, PDF, and TXT export
- Branded PDF export with company name, prepared-by line, classification, footer, and logo
- Local project history and meeting search
- Dark and light mode
- Single-instance desktop behavior
- Local error logs

## Privacy

The app calls Ollama only at `http://127.0.0.1:11434` and runs transcription through local command-line tools. Audio, transcripts, generated minutes, settings, logs, and project history stay on the computer.

## Requirements

- Node.js 20 or later
- npm
- Ollama
- One installed Ollama model
- FFmpeg for audio/video conversion. A bundled FFmpeg binary is included through `ffmpeg-static`; install system FFmpeg only if you prefer to use your own executable.
- One transcription engine:
  - `faster-whisper`
  - `whisper`
  - `whisper.cpp`

## Setup

```powershell
cd "C:\Users\Khurram\Documents\Angelina Scrap\mico360-meetings"
npm install
npm start
```

## Windows Installer

Build the installer:

```powershell
npm run dist
```

Installer output is written to `dist`. The installer includes the desktop app, bundled FFmpeg, app dependencies, logo assets, examples, export support, and GitHub auto-update metadata. The current smart installer is version `1.0.11`; use the `MICO360-Meetings-Setup-1.0.11.exe` file to avoid older installer behavior. For local AI prerequisites, use:

```powershell
powershell -ExecutionPolicy Bypass -File ".\installer\MICO360-Meetings-Prerequisites.ps1"
```

That script prepares Ollama, pulls `qwen2.5:0.5b`, automatically installs Python 3.12 when Python is missing or older than Python 3.10, creates a dedicated MICO360 Python environment under `%LOCALAPPDATA%\MICO360 Meetings\python-env`, and installs Python `faster-whisper` there.

To create an unpacked development build:

```powershell
npm run pack
```

## Auto Updates

MICO360 Meetings uses GitHub Releases for app updates:

- Repository: `mico360om/MICO360-Meetings`
- Provider: GitHub Releases through `electron-updater`
- Update controls: Settings -> AI -> Application Updates
- Installed builds automatically check for updates after launch and show desktop notifications when an update is found, downloading, ready, or failed.

Publish a release build with:

```powershell
$env:GH_TOKEN="YOUR_GITHUB_TOKEN"
npm run publish:github
```

Do not commit or store GitHub tokens in the repository.

## Ollama Installation

1. Install Ollama from [https://ollama.com/download](https://ollama.com/download).
2. Start Ollama.
3. Pull a model:

```powershell
ollama pull llama3.1
ollama pull qwen2.5
ollama pull mistral
ollama pull gemma2
```

4. Open MICO360 Meetings and click `Refresh` beside the model selector.

## Whisper Installation Options

### Option 1: faster-whisper

Install Python 3.10 or later, then:

```powershell
pip install faster-whisper
```

The app first tries a `faster-whisper` command if one exists, then falls back to Python with `from faster_whisper import WhisperModel`. The default model is `tiny` for low-resource computers; use `base`, `small`, or larger models if you want better accuracy.

### Option 2: OpenAI Whisper CLI

```powershell
pip install -U openai-whisper
```

The app expects the `whisper` command to be available in PATH.

### Option 3: whisper.cpp

1. Build or download whisper.cpp.
2. Download a local `.bin` model such as `ggml-base.en.bin`.
3. In app settings, select `whisper.cpp` and enter the model path.
4. Make sure `whisper-cli` is available in PATH, or update the executable name in `src/main/services.js`.

## FFmpeg

The app includes a bundled FFmpeg executable for formats such as M4A, MP4, MOV, MKV, and WebM. If you want to use a custom FFmpeg installation instead, install FFmpeg and enter its path in app settings:

```powershell
winget install Gyan.FFmpeg
```

## Workflow

1. Upload one or more meeting files, drag files into the drop zone, record audio, or paste a transcript.
2. Choose an Ollama model.
3. Choose the output style.
4. Open Settings to adjust AI, transcription, PDF profile, and prompt presets.
5. Click `Generate Minutes`.
6. Edit the generated minutes if needed.
7. Copy or export to DOCX, branded PDF, or TXT.
8. Save the project to local history.

## Logs and Local Data

Electron stores user data in the operating system app data folder for `MICO360 Meetings`. Error logs are written under a `logs` folder there.

## UI QA

Run the automated desktop UI/responsiveness scan with:

```powershell
npm run qa:ui
```

The scan launches Electron, checks the dashboard and all settings panes across small laptop, standard desktop, large monitor, tablet-width, and mobile-width viewports, captures screenshots under `qa-artifacts/ui-qa`, and reports horizontal overflow or clipped controls.

## Example Files

- `examples/sample-transcript.txt`
- `examples/sample-meeting-minutes.txt`

## PDF Profile

The Settings panel includes a `PDF Profile` tab. Use it to create multiple branded company profiles. Each profile can set:

- Profile name
- Company name
- Address and contact details
- Prepared-by label
- Classification such as Confidential, Internal, Public, or Draft
- Footer text
- Logo visibility
- Custom PNG or JPG logo
- Logo position and size
- Page number visibility and position
- Footer alignment and spacing
- Live layout preview

PDF exports use the MICO360 logo from `assets/logo.png` by default.

Profiles can be imported from JSON, CSV, XLSX, DOCX, and PDF source files. JSON, CSV, and XLSX are structured imports. DOCX/PDF imports create editable profile records from available source information and notes.

Profiles can be exported to PDF, Word, Excel, CSV, and JSON.

## Prompt Presets

The Settings panel includes a `Prompts` tab with presets for formal minutes, executive summaries, action item reports, and detailed minutes. Prompts are editable per upload and should keep `[TRANSCRIPT_HERE]` where transcript text should be inserted.

## Chrome Extension

The `chrome-extension` folder contains a lightweight companion extension that can collect selected text from a web meeting transcript page and copy it to the clipboard with a MICO360 heading. It does not send data anywhere.

To install it:

1. Open Chrome and go to `chrome://extensions`.
2. Enable Developer mode.
3. Click `Load unpacked`.
4. Select the `chrome-extension` folder.

## Notes

- Speaker identification depends on the selected transcription engine and model. If speakers are unclear, the app instructs the AI to mark them as unclear rather than inventing names.
- Long transcripts are split into chunks, summarized locally, then combined into final minutes.
- Low-resource computers should use smaller Whisper models such as `tiny` or `base` and smaller Ollama models.
