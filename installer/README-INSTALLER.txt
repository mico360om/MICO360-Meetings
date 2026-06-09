MICO360 Meetings Smart Installer Files

1. Run the Windows installer:
   MICO360 Meetings Setup *.exe

2. The installer automatically opens a visible "MICO360 Meetings Local AI Setup" window after app installation.
   Keep that setup window open until it says completed. On a new PC it can take several minutes because it may download Ollama, qwen2.5:0.5b, Python, and faster-whisper. The setup window shows an overall progress bar, current-step progress bar, and download/install messages.

3. If you need to rerun prerequisite setup manually, run PowerShell as the current user:
   powershell -ExecutionPolicy Bypass -File .\MICO360-Meetings-Prerequisites.ps1

The desktop installer includes the app, app packages, bundled FFmpeg, logos, examples, and export support.
The smart prerequisite step installs or prepares:
- Ollama
- qwen2.5:0.5b local Ollama model
- Python faster-whisper package for local transcription

Logs are written to:
%LOCALAPPDATA%\MICO360 Meetings\logs

Meeting data remains local.
