# Building the MICO360 Meetings installer

This produces a Windows installer that bundles the app **and its Python
runtime** (via PyInstaller), then runs a **smart setup** that installs Ollama and
the AI model automatically — skipping anything already present.

## Prerequisites (build machine only)
- Python 3.11–3.14 (64-bit)
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (`ISCC.exe` on PATH)

## Steps

**One command (recommended)** — builds the app *and* compiles the installer,
auto-detecting `ISCC.exe`:
```powershell
powershell -ExecutionPolicy Bypass -File build\build_all.ps1
#  -> build\dist\MICO360Meetings\MICO360Meetings.exe   (standalone app, ~613 MB)
#  -> build\Output\MICO360Meetings-Setup.exe           (installer, ~166 MB)
```

Or the two steps separately:
```powershell
powershell -ExecutionPolicy Bypass -File build\build_exe.ps1   # app only
ISCC build\installer.iss                                       # installer
```

> Verified built here: `MICO360Meetings-Setup.exe` (166 MB), metadata
> Company=MICO360 / Product=MICO360 Meetings / v1.0.0, app exe icon embedded,
> bundled app launches cleanly. Inno Setup installs via
> `winget install JRSoftware.InnoSetup` (it lands in
> `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`).

## What the installer does (the "smart" part)

`smart_setup.ps1` auto-detects every component and installs **only what is
missing** — it never reinstalls something already present:

1. **Python** (`-EnsurePython`): detects ≥3.11; installs Python silently if
   missing/too old. Present → **skips**. *(The packaged app bundles its own
   Python, so the installer doesn't pass this; it's for source installs.)*
2. **Python packages** (`-EnsurePyPackages`): reads `requirements.txt`, runs
   `pip show` on each, **skips installed** ones and `pip install`s only the
   missing ones. *(Bundled app already contains them.)*
3. **ffmpeg** (`-EnsureFfmpeg`, optional): skips if on PATH, else installs via
   winget. *(Not required — PyAV handles decoding.)*
4. **Ollama**: missing → downloads & installs silently; present → **skips**.
5. **Ollama server**: started if not running.
6. **Model(s)** (`-Models`): each present model is **skipped**; missing ones
   are pulled.
7. **Offline / failures** → clear message + non-zero exit code.
8. **Logs every step** (INSTALLED / SKIPPED / FAILED / COMPLETED) to
   `%LOCALAPPDATA%\MICO360Meetings\logs\setup.log`.

It is **idempotent** — re-running on a fully-set-up machine reports only
"skipped" and exits 0 (verified). Both paths are tested: skip-all on a ready
machine, and install-missing (pip package + Ollama model pull) when absent.

Run it standalone (full check) to test:
```powershell
# packaged app (Ollama + model only):
powershell -ExecutionPolicy Bypass -File build\smart_setup.ps1 -Models "llama3.1"

# source install (also verify Python + pip packages, skipping any present):
powershell -ExecutionPolicy Bypass -File build\smart_setup.ps1 `
    -EnsurePython -EnsurePyPackages -Models "llama3.1"
```

## Installer test matrix (run on clean VMs)

| # | Scenario | Expected |
| - | -------- | -------- |
| 1 | Clean system, nothing installed | Installs Ollama, pulls model, app launches |
| 2 | Ollama present, model missing | Skips Ollama, pulls model |
| 3 | Everything present | All steps "SKIP", exits 0 — **verified on dev machine** |
| 4 | Offline / download fails | Clear error in log + dialog; app still installed |
| 5 | Uninstall → reinstall | No leftover files/paths; clean reinstall |

See [`../docs/TEST_REPORT.md`](../docs/TEST_REPORT.md) for the results template.

## Auto-update (roadmap)

The installer is structured for an updater: bump `AppVersion`, rebuild, and ship
the new `MICO360Meetings-Setup.exe`. An in-app update check can poll a GitHub
Releases endpoint and prompt the user to download the new setup. (Not yet wired —
see project roadmap.)
