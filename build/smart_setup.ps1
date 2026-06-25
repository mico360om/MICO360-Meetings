<#
  MICO360 Meetings - Smart environment setup.

  Invoked by the installer (or run standalone). It:
    1. Detects whether Ollama is installed; installs it silently if missing.
    2. Ensures the Ollama server is running.
    3. Pulls the required model(s), SKIPPING any already present.
    4. Verifies the result and writes a clear, tagged log of every step.

  Idempotent: safe to run repeatedly. Already-installed items are skipped.
  Offline / failed downloads produce a clear, actionable message.

  Usage:
    powershell -ExecutionPolicy Bypass -File smart_setup.ps1 -Models "llama3.1"
#>
param(
    [string[]] $Models = @("llama3.1"),
    [string]   $LogPath = "$env:LOCALAPPDATA\MICO360Meetings\logs\setup.log",
    [switch]   $EnsurePython,                      # install Python if missing/old (source runs only)
    [string]   $MinPython = "3.11",
    [string]   $PythonInstallVersion = "3.12.8",
    [switch]   $EnsurePyPackages,                  # check + pip-install missing packages (source runs only)
    [string]   $Requirements = "",                 # path to requirements.txt (default: alongside script root)
    [switch]   $EnsureFfmpeg,                       # optional: install ffmpeg via winget if absent
    [switch]   $NonInteractive
)

$ErrorActionPreference = "Continue"
$summary = [ordered]@{ Installed = @(); Skipped = @(); Failed = @() }

# Accept both -Models "a","b" and the flattened -File form -Models "a,b".
$Models = @($Models | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

# --- logging ----------------------------------------------------------------
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
function Write-Log {
    param([string]$Message, [ValidateSet("INFO","OK","SKIP","FAIL","STEP")]$Tag = "INFO")
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "{0}  [{1,-4}] {2}" -f $stamp, $Tag, $Message
    Add-Content -Path $LogPath -Value $line
    $colors = @{ INFO="Gray"; OK="Green"; SKIP="DarkYellow"; FAIL="Red"; STEP="Cyan" }
    Write-Host $line -ForegroundColor $colors[$Tag]
}

function Test-Online {
    try { return (Test-Connection -ComputerName "ollama.com" -Count 1 -Quiet -ErrorAction Stop) }
    catch { return $false }
}

Write-Log "===== MICO360 Meetings setup started =====" "STEP"

# --- 0. Python (only needed for source/dev installs; the packaged app bundles
#        its own Python, so this step is opt-in via -EnsurePython) ------------
function Get-PythonVersion {
    try {
        $v = (& python --version) 2>&1
        if ($v -match "Python (\d+)\.(\d+)\.(\d+)") {
            return [version]("{0}.{1}.{2}" -f $matches[1], $matches[2], $matches[3])
        }
    } catch {}
    return $null
}

if ($EnsurePython) {
    $min = [version]("$MinPython.0")
    $cur = Get-PythonVersion
    if ($cur -and $cur -ge $min) {
        Write-Log "Python $cur already installed (>= $MinPython) - skipping." "SKIP"
        $summary.Skipped += "Python $cur"
    } elseif (-not (Test-Online)) {
        Write-Log "Python missing/old and no internet. Install Python $MinPython+ from python.org, then re-run." "FAIL"
        $summary.Failed += "Python (offline)"
    } else {
        if ($cur) { Write-Log "Python $cur is older than $MinPython - installing $PythonInstallVersion." "STEP" }
        else { Write-Log "Python not found - installing $PythonInstallVersion." "STEP" }
        try {
            $pyexe = Join-Path $env:TEMP "python-$PythonInstallVersion-amd64.exe"
            $url = "https://www.python.org/ftp/python/$PythonInstallVersion/python-$PythonInstallVersion-amd64.exe"
            Write-Log "Downloading $url" "INFO"
            Invoke-WebRequest -Uri $url -OutFile $pyexe -UseBasicParsing
            # per-user silent install, add to PATH; does not disturb other versions
            Start-Process -FilePath $pyexe -ArgumentList `
                "/quiet","InstallAllUsers=0","PrependPath=1","Include_pip=1","Include_test=0" -Wait
            $new = Get-PythonVersion
            if ($new -and $new -ge $min) {
                Write-Log "Python $new installed." "OK"; $summary.Installed += "Python $new"
            } else {
                Write-Log "Python install finished but version not detected on PATH (re-login may be needed)." "FAIL"
                $summary.Failed += "Python (PATH)"
            }
        } catch {
            Write-Log "Python install failed: $($_.Exception.Message)" "FAIL"
            $summary.Failed += "Python"
        }
    }
}

# --- 0b. Python packages (source/dev installs only; packaged app bundles them) ---
function Get-PipName {
    param([string]$Line)
    $l = $Line.Trim()
    if ($l -eq "" -or $l.StartsWith("#")) { return $null }
    $l = (($l -split "#")[0]).Trim()          # strip inline comment
    $l = (($l -split ";")[0]).Trim()          # strip environment marker
    if ($l -match "^([A-Za-z0-9_.\-]+)") { return $matches[1] }
    return $null
}

if ($EnsurePyPackages) {
    Write-Log "Checking Python packages (skip those already installed)..." "STEP"
    $py = Get-PythonVersion
    if (-not $py) {
        Write-Log "Python not available; cannot check packages. Run with -EnsurePython first." "FAIL"
        $summary.Failed += "Python packages (no python)"
    } else {
        $req = if ($Requirements) { $Requirements } else { Join-Path (Split-Path $PSScriptRoot -Parent) "requirements.txt" }
        if (-not (Test-Path $req)) {
            Write-Log "requirements.txt not found at $req" "FAIL"
            $summary.Failed += "Python packages (no requirements)"
        } else {
            $names = Get-Content $req | ForEach-Object { Get-PipName $_ } | Where-Object { $_ }
            $missing = @()
            foreach ($n in $names) {
                & python -m pip show $n *> $null
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "Package '$n' already installed - skipping." "SKIP"
                    $summary.Skipped += "pkg:$n"
                } else {
                    $missing += $n
                }
            }
            if ($missing.Count -eq 0) {
                Write-Log "All required Python packages are present." "OK"
            } elseif (-not (Test-Online)) {
                Write-Log "Missing packages and no internet: $($missing -join ', '). Run 'pip install -r requirements.txt' when online." "FAIL"
                $summary.Failed += "packages (offline): $($missing -join ',')"
            } else {
                Write-Log "Installing missing packages: $($missing -join ', ')" "STEP"
                & python -m pip install @missing
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "Installed missing packages." "OK"
                    $summary.Installed += "pkgs:$($missing -join ',')"
                } else {
                    Write-Log "pip install failed (exit $LASTEXITCODE)." "FAIL"
                    $summary.Failed += "packages"
                }
            }
        }
    }
}

# --- 0c. ffmpeg (optional; PyAV already handles decoding, so this is opt-in) --
if ($EnsureFfmpeg) {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Log "ffmpeg already on PATH - skipping." "SKIP"
        $summary.Skipped += "ffmpeg"
    } elseif (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Log "ffmpeg missing and winget unavailable; install ffmpeg manually if needed." "FAIL"
        $summary.Failed += "ffmpeg (no winget)"
    } elseif (-not (Test-Online)) {
        Write-Log "ffmpeg missing and offline." "FAIL"
        $summary.Failed += "ffmpeg (offline)"
    } else {
        Write-Log "Installing ffmpeg via winget..." "STEP"
        & winget install --id Gyan.FFmpeg -e --silent --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) { Write-Log "ffmpeg installed." "OK"; $summary.Installed += "ffmpeg" }
        else { Write-Log "ffmpeg install returned exit $LASTEXITCODE." "FAIL"; $summary.Failed += "ffmpeg" }
    }
}

# --- 1. Ollama present? -----------------------------------------------------
function Get-OllamaCmd { return (Get-Command ollama -ErrorAction SilentlyContinue) }

if (Get-OllamaCmd) {
    $ver = (& ollama --version) 2>&1
    Write-Log "Ollama already installed: $ver" "SKIP"
    $summary.Skipped += "Ollama runtime"
} else {
    Write-Log "Ollama not found - installing." "STEP"
    if (-not (Test-Online)) {
        Write-Log "No internet connection. Cannot download Ollama. Connect to the internet and re-run, or install from https://ollama.com/download" "FAIL"
        $summary.Failed += "Ollama runtime (offline)"
    } else {
        try {
            $setup = Join-Path $env:TEMP "OllamaSetup.exe"
            Write-Log "Downloading OllamaSetup.exe ..." "INFO"
            Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $setup -UseBasicParsing
            Write-Log "Running Ollama installer (silent) ..." "INFO"
            Start-Process -FilePath $setup -ArgumentList "/VERYSILENT","/NORESTART" -Wait
            $ollamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
            $env:Path = "$env:Path;$ollamaDir"
            if (Get-OllamaCmd) {
                Write-Log "Ollama installed successfully." "OK"
                $summary.Installed += "Ollama runtime"
            } else {
                Write-Log "Ollama installer finished but 'ollama' is not on PATH yet. A reboot or re-login may be required." "FAIL"
                $summary.Failed += "Ollama runtime (PATH)"
            }
        } catch {
            Write-Log "Ollama install failed: $($_.Exception.Message)" "FAIL"
            $summary.Failed += "Ollama runtime"
        }
    }
}

# --- 2. Ensure server running ----------------------------------------------
function Test-OllamaServer {
    try { & ollama list *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

if (Get-OllamaCmd) {
    if (-not (Test-OllamaServer)) {
        Write-Log "Starting Ollama server ..." "INFO"
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 1
            if (Test-OllamaServer) { break }
        }
    }
    if (Test-OllamaServer) { Write-Log "Ollama server is responding." "OK" }
    else { Write-Log "Ollama server did not respond in time." "FAIL" }
}

# --- 3. Pull required models (skip existing) -------------------------------
if ((Get-OllamaCmd) -and (Test-OllamaServer)) {
    $present = (& ollama list) 2>$null | Out-String
    foreach ($m in $Models) {
        $name = $m.Trim()
        if ($present -match [regex]::Escape($name)) {
            Write-Log "Model '$name' already present - skipping." "SKIP"
            $summary.Skipped += "model:$name"
            continue
        }
        if (-not (Test-Online)) {
            Write-Log "Model '$name' missing and no internet. Run 'ollama pull $name' when online." "FAIL"
            $summary.Failed += "model:$name (offline)"
            continue
        }
        Write-Log "Pulling model '$name' (this can take several minutes) ..." "STEP"
        & ollama pull $name
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Model '$name' installed." "OK"
            $summary.Installed += "model:$name"
        } else {
            Write-Log "Failed to pull model '$name' (exit $LASTEXITCODE)." "FAIL"
            $summary.Failed += "model:$name"
        }
    }
}

# --- 4. Summary -------------------------------------------------------------
function Join-Or-None($a) { if ($a -and $a.Count -gt 0) { return ($a -join ', ') } else { return 'none' } }

Write-Log "----- Setup summary -----" "STEP"
Write-Log ("Installed: " + (Join-Or-None $summary.Installed)) "INFO"
Write-Log ("Skipped:   " + (Join-Or-None $summary.Skipped)) "INFO"
Write-Log ("Failed:    " + (Join-Or-None $summary.Failed)) "INFO"

if ($summary.Failed.Count -gt 0) {
    Write-Log "Setup completed WITH ERRORS. See messages above." "FAIL"
    exit 1
}
Write-Log "Setup completed successfully. MICO360 Meetings is ready to run." "OK"
exit 0
