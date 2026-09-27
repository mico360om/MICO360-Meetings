<#
  One-command build: PyInstaller .exe  ->  Inno Setup installer.
  Run from the project root:
      powershell -ExecutionPolicy Bypass -File build\build_all.ps1
      powershell -ExecutionPolicy Bypass -File build\build_all.ps1 -Python C:\Python312\python.exe
  Produces:
      build\dist\MICO360Meetings\MICO360Meetings.exe   (standalone app)
      build\Output\MICO360Meetings-Setup.exe           (installer)

  Rules this script enforces (so it can never hash and report a stale file):
    * build\dist, build\work and build\Output are removed before building;
    * every pip / PyInstaller / ISCC call must exit 0, or the build stops;
    * dependencies come from requirements.txt pinned by constraints.txt;
    * the injected MICO360 Connect key file is ALWAYS deleted afterwards.

  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a BOM-less
  script as cp1252, and a stray UTF-8 dash/quote can break parsing.
#>
param(
    # Interpreter to build with. Default: "py -3.12" (the version CI and the
    # release workflow use), falling back to "python" with a warning.
    [string] $Python = "",
    # Build only the app (build\dist), not the installer. build_exe.ps1 uses this.
    [switch] $SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$script:PyCmd = $null
$script:PyArgs = @()

# Run a native command; stop the build if it exits non-zero. Native stderr
# (pip warnings, etc.) is shown but must not abort the script by itself, so
# the error preference is relaxed only inside this function.
function Invoke-Native {
    param([string] $Exe, [string[]] $Arguments, [string] $What)
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$What failed (exit code $code)." }
}

function Invoke-Py {
    param([string[]] $Arguments, [string] $What)
    Invoke-Native -Exe $script:PyCmd -Arguments ($script:PyArgs + $Arguments) -What $What
}

function Test-PyLauncher312 {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { return $false }
    $ErrorActionPreference = "Continue"
    & py -3.12 -c "import sys" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# --- choose the Python that builds the release (L14) -------------------------
if ($Python) {
    $script:PyCmd = $Python
} elseif (Test-PyLauncher312) {
    $script:PyCmd = "py"; $script:PyArgs = @("-3.12")
} else {
    $script:PyCmd = "python"
}
$pyVer = (& $script:PyCmd @script:PyArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])")
if ($LASTEXITCODE -ne 0 -or -not $pyVer) { throw "Cannot run Python ($script:PyCmd $($script:PyArgs -join ' '))." }
Write-Host "==> Building with Python $pyVer ($script:PyCmd $($script:PyArgs -join ' '))" -ForegroundColor Cyan
if (-not $pyVer.StartsWith("3.12.")) {
    Write-Warning ("Python $pyVer is not 3.12. Releases are built and tested on Python 3.12 " +
                   "(CI + release workflow); other versions are not release-tested. " +
                   "Install 3.12 (winget install Python.Python.3.12) or pass -Python <path>.")
}

$keyFile = Join-Path $root "mico360\_build_key.py"
try {
    Write-Host "==> Installing pinned dependencies + build tooling..." -ForegroundColor Cyan
    Invoke-Py -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip") -What "pip self-upgrade"
    Invoke-Py -Arguments @("-m", "pip", "install", "--disable-pip-version-check",
                           "-r", "requirements.txt", "-r", "requirements-dev.txt",
                           "-c", "constraints.txt") -What "pip install (requirements + constraints)"
    Invoke-Py -Arguments @("-c", "import win32gui, pythoncom, win32com.client") -What "pywin32 check (meeting detection / Outlook)"

    Write-Host "==> Cleaning previous build (dist, work, Output)..." -ForegroundColor Cyan
    $clean = @("build\dist", "build\work")
    if (-not $SkipInstaller) { $clean += "build\Output" }
    foreach ($d in $clean) {
        $p = Join-Path $root $d
        # Retry: sync clients (the repo may live in Dropbox/OneDrive) briefly lock
        # a folder right after its contents are deleted.
        for ($i = 0; ($i -lt 6) -and (Test-Path $p); $i++) {
            try { Remove-Item -Recurse -Force $p -ErrorAction Stop } catch { Start-Sleep -Seconds 2 }
        }
        if (Test-Path $p) { throw "Could not remove $p (is the app or installer still running?)" }
    }

    # Inject the MICO360 Connect API key at build time so end-users need no
    # config. The file is gitignored, only exists for the duration of this
    # build, and is removed in the finally block below. The key is written
    # hex-encoded so no character in it can break the Python literal.
    #   $env:MICO360_CONNECT_API_KEY = "mico_..."   (do NOT commit it)
    if (Test-Path $keyFile) { Remove-Item -Force $keyFile }   # never ship a stale key
    $key = "$env:MICO360_CONNECT_API_KEY".Trim()
    if ($key) {
        $hex = -join ([System.Text.Encoding]::UTF8.GetBytes($key) | ForEach-Object { $_.ToString("x2") })
        "KEY = bytes.fromhex('$hex').decode('utf-8')" | Out-File -Encoding ascii $keyFile
        Write-Host "==> Injected MICO360 Connect API key into the build." -ForegroundColor Green
    } else {
        Write-Warning "MICO360_CONNECT_API_KEY not set - MICO360 Cloud mode will be inactive in this build (Local/Ollama still works)."
    }

    Write-Host "==> Building app with PyInstaller..." -ForegroundColor Cyan
    Invoke-Py -Arguments @("-m", "PyInstaller", "build\mico360.spec", "--noconfirm",
                           "--distpath", "build\dist", "--workpath", "build\work") -What "PyInstaller"

    $exe = "build\dist\MICO360Meetings\MICO360Meetings.exe"
    if (-not (Test-Path $exe)) { throw "Build failed: $exe not found" }
    Write-Host "==> App built: $exe" -ForegroundColor Green
} finally {
    if (Test-Path $keyFile) {
        Remove-Item -Force $keyFile -ErrorAction SilentlyContinue
        if (Test-Path $keyFile) { Write-Warning "Could not delete $keyFile - delete it by hand." }
        else { Write-Host "==> Removed the build-time key file." -ForegroundColor DarkGray }
    }
}

if ($SkipInstaller) {
    Write-Host "==> -SkipInstaller: done (app only)." -ForegroundColor Green
    exit 0
}

# locate ISCC.exe (Inno Setup compiler)
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning "Inno Setup (ISCC.exe) not found. Install it:  winget install JRSoftware.InnoSetup"
    Write-Warning "The standalone app is ready at $exe; re-run this script to build the installer."
    exit 0
}

Write-Host "==> Compiling installer with $iscc ..." -ForegroundColor Cyan
Invoke-Native -Exe $iscc -Arguments @("build\installer.iss") -What "Inno Setup compile (ISCC)"

$setup = "build\Output\MICO360Meetings-Setup.exe"
if (-not (Test-Path $setup)) { throw "Installer compile failed: $setup not found" }
Write-Host "==> Installer ready: $setup" -ForegroundColor Green
# Publish a SHA256 so the in-app updater can verify a download before running it.
$sha = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
"$sha *$(Split-Path -Leaf $setup)" | Out-File -Encoding ascii "build\Output\SHA256SUMS.txt"
$sha | Out-File -Encoding ascii "$setup.sha256"
Write-Host "==> SHA256: $sha" -ForegroundColor Green
