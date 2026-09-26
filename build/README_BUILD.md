# Building the MICO360 Meetings installer

This produces a Windows installer (currently **v1.2.3** — the version lives in
`mico360/__init__.py` and `build/installer.iss`, and the release gate checks they
match) that bundles the app **and its Python runtime** (via PyInstaller), then
runs a **smart setup** that installs Ollama and the AI model — skipping anything
already present.

> **Official releases are built by CI**, not on a developer PC: pushing a tag
> `vX.Y.Z` runs `.github/workflows/release.yml`, which builds on Python 3.12
> with the pinned dependencies, injects the MICO360 Connect key from a
> repository secret, and publishes exactly one installer plus its `.sha256`.
> Local builds are for testing.

## Prerequisites (build machine only)
- **Python 3.12** (64-bit) — the version CI and the release workflow use. The
  build scripts pick `py -3.12` automatically; with any other version they
  build anyway but print a warning (other versions are not release-tested —
  e.g. PySide6 on Python 3.14 has an intermittent crash while exiting). Install
  it with `winget install Python.Python.3.12`, or pass `-Python <path>`.
- [Inno Setup 6](https://jrsoftware.org/isdl.php) —
  `winget install JRSoftware.InnoSetup` (lands in
  `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`; the script finds it there
  or under Program Files).

## Dependencies are pinned

| File | Purpose |
| ---- | ------- |
| `requirements.txt` | what the app needs (version ranges) |
| `requirements-dev.txt` | build/test tools: PyInstaller, pypdf |
| `constraints.txt` | the exact tested version of every package (and PyInstaller) |

Every install — local build, CI, release — uses:
```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt
```
To upgrade a package: bump its pin in `constraints.txt`, run
`python tests/release_check.py` in a clean virtualenv, commit both together.

## Steps

**One command (recommended)** — builds the app *and* compiles the installer:
```powershell
$env:MICO360_CONNECT_API_KEY = "mico_..."     # optional; enables Cloud mode (never commit it)
powershell -ExecutionPolicy Bypass -File build\build_all.ps1
#  -> build\dist\MICO360Meetings\MICO360Meetings.exe   (standalone app)
#  -> build\Output\MICO360Meetings-Setup.exe           (installer, ~170 MB)
#  -> build\Output\MICO360Meetings-Setup.exe.sha256 + SHA256SUMS.txt
```
App only (no installer): `powershell -ExecutionPolicy Bypass -File build\build_exe.ps1`
(the same script with `-SkipInstaller`). Installer only, from an existing
`build\dist`: `ISCC build\installer.iss`.

What `build_all.ps1` guarantees:
- installs the **pinned** dependencies and stops if pip fails (pip output is shown);
- deletes `build\dist`, `build\work` **and `build\Output`** first, so it can never
  hash or report an installer left over from an earlier build;
- stops on a non-zero exit code from pip, PyInstaller or ISCC;
- writes the Connect key to `mico360\_build_key.py` only for the build and
  **always deletes it afterwards** (even when the build fails), so a later
  build without the variable can't ship a stale key. The key is written
  hex-encoded, so no character in it can break the file.

`build\mico360.spec` **fails the build** if a required package (Whisper stack,
audio/screen/camera capture, exports, PySide6, pywin32) is missing from the
build environment, instead of silently producing an app with features switched
off. Optional extras (`pypdf`, for PDF text import) are bundled when installed.

Keep the `.ps1` files **ASCII-only**: Windows PowerShell 5.1 reads a script
without a BOM as cp1252, and a single UTF-8 dash or curly quote can break parsing.

## Verify before tagging

```powershell
python tests\release_check.py          # full gate, incl. the built app
```
The full gate launches `build\dist\...\MICO360Meetings.exe` against a
throwaway data folder, requires the **main window** to appear (not the "already
running" box or a crash dialog) with no crash report, then closes the window
and requires a **clean exit (code 0)** — this is the check that catches a
frozen-app crash on exit. `--fast` (what CI runs) skips it. Every test suite
uses its own temporary data folder, so running the gate never changes your real
meetings or settings.

## What the installer does

- **Upgrades** replace the whole runtime: the old `{app}\_internal` folder is
  deleted before the new files are copied, so DLLs from different releases are
  never mixed. User data is not touched.
- **Running app**: Setup and Uninstall check the app's named mutex
  (`MICO360Meetings`, i.e. `Local\MICO360Meetings`) and ask the user to close
  the app; a silent install waits up to 30 s for it to exit.
- **AI setup** (task "Download AI model ... and set up Ollama", checked on first
  install): runs `smart_setup.ps1` **as the user who started Setup** (not the
  elevated admin account), with a visible console showing download progress.
  If it fails, Setup says so (exit code + log path) — the app is still
  installed, and the model can be installed later from Settings.
- **Setup log**: `%TEMP%\Setup Log <date> #<n>.txt` (`SetupLogging=yes`).

### Auto-update (wired)

The in-app **Updates** page checks GitHub Releases, downloads the installer,
verifies it against the release's `.sha256`, then runs it with
`/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS`
and quits. For such an in-app update (silent + `/RESTARTAPPLICATIONS` or
`/RELAUNCH`) the installer waits for the app to exit, skips the Ollama/model
step, and **relaunches the app** as the original (non-elevated) user when done.
A plain `/SILENT` or `/VERYSILENT` deployment does not relaunch it.

### Uninstall

Removes the program folder. It then asks whether to also delete the user's
data — meetings, transcripts, recordings, company profiles and settings
(including the saved email password) in `%LOCALAPPDATA%\MICO360Meetings` —
**default No**. A silent uninstall never deletes user data. Ollama and its
models are separate software and are not removed.

## The smart setup script

`smart_setup.ps1` auto-detects every component and installs **only what is
missing** — it never reinstalls something already present:

1. **Python** (`-EnsurePython`): detects >= 3.11; installs Python silently if
   missing/too old. *(The packaged app bundles its own Python, so the installer
   doesn't pass this; it's for source installs.)*
2. **Python packages** (`-EnsurePyPackages`): reads `requirements.txt`, runs
   `pip show` on each, and installs only the missing ones.
3. **ffmpeg** (`-EnsureFfmpeg`, optional): skips if on PATH, else installs via
   winget. *(Not required — PyAV handles decoding.)*
4. **Ollama**: missing → downloads & installs silently (per-user); present → skips.
5. **Ollama server**: started if not running.
6. **Model(s)** (`-Models`): each present model is skipped; missing ones are pulled.
7. **Online check**: an HTTPS request to ollama.com (ICMP ping is blocked on many
   networks, so it is not used).
8. **Offline / failures** → clear message + exit code 1 (reported by the installer).
9. **Logs every step** to `%LOCALAPPDATA%\MICO360Meetings\logs\setup.log`.

It is **idempotent** — re-running on a fully-set-up machine reports only
"skipped" and exits 0.

Run it standalone to test:
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
| 1 | Clean system, nothing installed | Installs Ollama (per-user), pulls model, app launches |
| 2 | Ollama present, model missing | Skips Ollama, pulls model |
| 3 | Everything present | All steps "SKIP", exits 0 |
| 4 | Offline / download fails | Setup shows "AI setup did not complete" + log path; app still installed |
| 5 | Standard user + admin credentials (UAC) | Ollama/model land in the *standard user's* profile |
| 6 | Upgrade over the previous version | Old `_internal` removed; app starts; data kept |
| 7 | In-app update | App closes, installs silently, reopens by itself |
| 8 | Uninstall, answer No / Yes | No: data folder kept. Yes: data folder removed |
| 9 | Uninstall → reinstall | No leftover program files; clean reinstall |

See [`../docs/TEST_REPORT.md`](../docs/TEST_REPORT.md) for the results template.
