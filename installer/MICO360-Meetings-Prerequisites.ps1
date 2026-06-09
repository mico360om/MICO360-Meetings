param(
  [string]$OllamaModel = "qwen2.5:0.5b",
  [string]$WhisperPackage = "faster-whisper",
  [string]$AppExe = "",
  [switch]$VerifyOnly,
  [switch]$OfflineTest,
  [switch]$NonInteractive,
  [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "Continue"

$InstallRoot = Join-Path $env:LOCALAPPDATA "MICO360 Meetings"
$LogDir = Join-Path $InstallRoot "logs"
$ConfigDir = Join-Path $InstallRoot "config"
$PythonEnvDir = Join-Path $InstallRoot "python-env"
$PythonEnvExe = Join-Path $PythonEnvDir "Scripts\python.exe"
$LogFile = Join-Path $LogDir ("smart-installer-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force -Path $LogDir, $ConfigDir | Out-Null

$RequiredPythonMajor = 3
$RequiredPythonMinor = 10
$RequiredPythonText = "Python $RequiredPythonMajor.$RequiredPythonMinor or newer"
$PythonWingetId = "Python.Python.3.12"
$PythonInstallText = "Python 3.12"

$Summary = [ordered]@{
  Installed = New-Object System.Collections.Generic.List[string]
  Skipped = New-Object System.Collections.Generic.List[string]
  Failed = New-Object System.Collections.Generic.List[string]
  Verified = New-Object System.Collections.Generic.List[string]
}

$TotalSteps = 6
$CurrentStep = 0

function Write-Log {
  param(
    [ValidateSet("INFO", "SKIP", "INSTALL", "VERIFY", "WARN", "ERROR", "DONE")]
    [string]$Level,
    [string]$Message
  )
  $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
  Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
  Write-Host $line
}

function Show-SetupProgress {
  param(
    [string]$Status,
    [int]$Step = $CurrentStep,
    [int]$SubPercent = 0
  )
  $stepBase = [math]::Max(0, $Step - 1) / $TotalSteps * 100
  $stepSize = 100 / $TotalSteps
  $overall = [math]::Min(99, [math]::Round($stepBase + (($SubPercent / 100) * $stepSize), 0))
  Write-Progress -Id 1 -Activity "MICO360 Meetings Local AI Setup" -Status $Status -PercentComplete $overall
  Write-Progress -Id 2 -ParentId 1 -Activity "Current Step" -Status $Status -PercentComplete $SubPercent
}

function Add-Summary {
  param([string]$Bucket, [string]$Value)
  $Summary[$Bucket].Add($Value) | Out-Null
}

function Test-Command {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Script
  )
  try {
    $script:CurrentStep += 1
    Show-SetupProgress -Status "Step ${CurrentStep} of ${TotalSteps}: $Name" -SubPercent 5
    Write-Log "INFO" "Starting: $Name"
    & $Script
    Show-SetupProgress -Status "Completed: $Name" -SubPercent 100
    Write-Log "DONE" "Completed: $Name"
  } catch {
    $message = "$Name failed: $($_.Exception.Message)"
    Add-Summary "Failed" $message
    Write-Log "ERROR" $message
    throw
  }
}

function Test-Online {
  if ($OfflineTest) {
    Write-Log "WARN" "Offline test mode enabled."
    return $false
  }
  try {
    $request = [System.Net.WebRequest]::Create("https://ollama.com")
    $request.Method = "HEAD"
    $request.Timeout = 7000
    $response = $request.GetResponse()
    $response.Close()
    Write-Log "VERIFY" "Internet connectivity verified."
    Add-Summary "Verified" "Internet connectivity"
    return $true
  } catch {
    Write-Log "WARN" "Internet connectivity check failed: $($_.Exception.Message)"
    return $false
  }
}

function Invoke-Native {
  param(
    [string]$FilePath,
    [string[]]$ArgumentList,
    [int[]]$AllowedExitCodes = @(0)
  )
  Write-Log "INFO" ("Running: {0} {1}" -f $FilePath, ($ArgumentList -join " "))
  Show-SetupProgress -Status ("Running: {0}" -f $FilePath) -SubPercent 40
  & $FilePath @ArgumentList
  $exitCode = $LASTEXITCODE
  if ($AllowedExitCodes -notcontains $exitCode) {
    throw "$FilePath exited with code $exitCode"
  }
}

function Invoke-CommandLine {
  param(
    [string]$FilePath,
    [string[]]$ArgumentList,
    [int[]]$AllowedExitCodes = @(0)
  )
  Write-Log "INFO" ("Running: {0} {1}" -f $FilePath, ($ArgumentList -join " "))
  & $FilePath @ArgumentList
  $exitCode = $LASTEXITCODE
  if ($AllowedExitCodes -notcontains $exitCode) {
    throw "$FilePath exited with code $exitCode"
  }
}

function Ensure-Ollama {
  param([bool]$Online)
  if (Test-Command "ollama") {
    $version = (& ollama --version 2>$null) -join " "
    Write-Log "SKIP" "Ollama already installed. $version"
    Add-Summary "Skipped" "Ollama already installed"
    return
  }
  if ($VerifyOnly) {
    throw "Ollama is missing."
  }
  if (-not $Online) {
    throw "Ollama is missing and the system appears offline. Connect to the internet, then rerun this installer or install Ollama from https://ollama.com/download."
  }
  if (-not (Test-Command "winget")) {
    throw "Ollama is missing and winget is not available. Install Ollama from https://ollama.com/download, then rerun this installer."
  }
  Write-Host ""
  Write-Host "Downloading and installing Ollama..." -ForegroundColor Cyan
  Show-SetupProgress -Status "Downloading and installing Ollama" -SubPercent 35
  Invoke-Native -FilePath "winget" -ArgumentList @("install", "--id", "Ollama.Ollama", "--accept-package-agreements", "--accept-source-agreements", "--silent")
  Add-Summary "Installed" "Ollama"
  if (-not (Test-Command "ollama")) {
    throw "Ollama installation completed, but ollama.exe was not found in PATH. Restart Windows or add Ollama to PATH."
  }
}

function Ensure-OllamaServer {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 5 | Out-Null
    Write-Log "VERIFY" "Ollama service is running."
    Add-Summary "Verified" "Ollama service"
    return
  } catch {
    Write-Log "INFO" "Starting Ollama service..."
    Start-Process -FilePath "ollama" -ArgumentList @("serve") -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 6
  }
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 10 | Out-Null
    Write-Log "VERIFY" "Ollama service started successfully."
    Add-Summary "Verified" "Ollama service started"
  } catch {
    throw "Ollama is installed but the local service did not respond. Open Ollama once, then rerun this installer."
  }
}

function Ensure-OllamaModel {
  param([bool]$Online)
  $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 10
  $installedModels = @($tags.models | ForEach-Object { $_.name })
  if ($installedModels -contains $OllamaModel) {
    Write-Log "SKIP" "Ollama model already installed: $OllamaModel"
    Add-Summary "Skipped" "Ollama model $OllamaModel already installed"
  } else {
    if ($VerifyOnly) {
      throw "Ollama model is missing: $OllamaModel"
    }
    if (-not $Online) {
      throw "Ollama model '$OllamaModel' is missing and the system appears offline. Connect to the internet and rerun this installer."
    }
    Write-Host ""
    Write-Host "Downloading local Ollama model: $OllamaModel" -ForegroundColor Cyan
    Write-Host "This is usually the longest step on a new PC. Ollama will show its own download progress below."
    Show-SetupProgress -Status "Downloading Ollama model $OllamaModel" -SubPercent 20
    Invoke-Native -FilePath "ollama" -ArgumentList @("pull", $OllamaModel)
    Show-SetupProgress -Status "Verifying Ollama model $OllamaModel" -SubPercent 85
    Add-Summary "Installed" "Ollama model $OllamaModel"
  }

  Show-SetupProgress -Status "Testing Ollama model response" -SubPercent 90
  $body = @{ model = $OllamaModel; prompt = "Reply with OK only."; stream = $false } | ConvertTo-Json
  $result = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 60
  if (-not $result.response) {
    throw "Ollama model verification did not return a response."
  }
  Write-Log "VERIFY" "Ollama model generated a response successfully."
  Add-Summary "Verified" "Ollama model $OllamaModel"
}

function Get-PythonVersionInfo {
  param(
    [string]$File,
    [string[]]$CommandArgs = @()
  )
  if (-not (Test-Command $File)) { return $null }
  try {
    $output = (& $File @($CommandArgs + @("--version")) 2>&1) -join " "
    if ($output -match "Python was not found|Microsoft Store|App execution aliases") {
      return $null
    }
    if ($output -match "Python\s+(\d+)\.(\d+)(?:\.(\d+))?") {
      return @{
        File = $File
        Args = $CommandArgs
        VersionText = $matches[0]
        Major = [int]$matches[1]
        Minor = [int]$matches[2]
        Patch = if ($matches[3]) { [int]$matches[3] } else { 0 }
      }
    }
    Write-Log "WARN" "Could not parse Python version from '$File': $output"
  } catch {
    Write-Log "WARN" "Could not run Python candidate '$File': $($_.Exception.Message)"
  }
  return $null
}

function Test-SupportedPython {
  param($Python)
  return $Python -and ($Python["Major"] -gt $RequiredPythonMajor -or ($Python["Major"] -eq $RequiredPythonMajor -and $Python["Minor"] -ge $RequiredPythonMinor))
}

function Get-PythonCommand {
  $candidates = @(
    @{ File = "py"; Args = @("-3") },
    @{ File = "python3"; Args = @() },
    @{ File = "python"; Args = @() }
  )
  foreach ($candidate in $candidates) {
    $python = Get-PythonVersionInfo -File $candidate["File"] -CommandArgs $candidate["Args"]
    if (Test-SupportedPython $python) { return $python }
    if ($python) {
      Write-Log "WARN" "Old or unsupported Python detected at '$($candidate["File"])': $($python["VersionText"]). $RequiredPythonText is required, so the installer will use or install $PythonInstallText."
    }
  }
  return $null
}

function Ensure-Python {
  param([bool]$Online)
  $python = Get-PythonCommand
  if ($python) {
    Write-Log "SKIP" "Supported Python already installed. $($python["VersionText"])"
    Add-Summary "Skipped" "Python $($python["VersionText"]) already installed"
    return
  }
  if ($VerifyOnly) {
    throw "$RequiredPythonText is missing."
  }
  if (-not $Online) {
    throw "$RequiredPythonText is missing and the system appears offline. Connect to the internet and rerun this installer."
  }
  if (-not (Test-Command "winget")) {
    throw "$RequiredPythonText is missing and winget is not available. Install Python 3.10+ from https://www.python.org/downloads/, then rerun this installer."
  }
  Write-Host ""
  Write-Log "INSTALL" "$RequiredPythonText was not found. Installing $PythonInstallText automatically."
  Write-Host "Downloading and installing $PythonInstallText..." -ForegroundColor Cyan
  Show-SetupProgress -Status "Downloading and installing $PythonInstallText" -SubPercent 35
  Invoke-Native -FilePath "winget" -ArgumentList @("install", "--id", $PythonWingetId, "--accept-package-agreements", "--accept-source-agreements", "--silent")
  Add-Summary "Installed" $PythonInstallText
  $python = Get-PythonCommand
  if (-not $python) {
    throw "$PythonInstallText installation completed, but no supported Python 3 command was detected. Restart Windows, then rerun this installer."
  }
  Write-Log "VERIFY" "Supported Python detected after installation. $($python["VersionText"])"
  Add-Summary "Verified" "Python $($python["VersionText"])"
}

function Invoke-Python {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
  $python = Get-PythonCommand
  if (-not $python) { throw "Python command not available." }
  Invoke-CommandLine -FilePath $python["File"] -ArgumentList @($python["Args"] + $CommandArgs)
}

function Test-PythonImport {
  param([string]$ModuleName)
  $python = Get-PythonCommand
  if (-not $python) { return $false }
  $code = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') else 1)"
  & $python["File"] @($python["Args"] + @("-c", $code)) | Out-Null
  return $LASTEXITCODE -eq 0
}

function Test-InstallerPythonReady {
  if (-not (Test-Path -LiteralPath $PythonEnvExe)) { return $false }
  $version = Get-PythonVersionInfo -File $PythonEnvExe
  return (Test-SupportedPython $version)
}

function Invoke-InstallerPython {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
  if (-not (Test-InstallerPythonReady)) {
    throw "MICO360 Python environment is not ready."
  }
  Invoke-CommandLine -FilePath $PythonEnvExe -ArgumentList $CommandArgs
}

function Test-InstallerPythonImport {
  param([string]$ModuleName)
  if (-not (Test-InstallerPythonReady)) { return $false }
  $code = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') else 1)"
  & $PythonEnvExe -c $code | Out-Null
  return $LASTEXITCODE -eq 0
}

function Ensure-FasterWhisper {
  param([bool]$Online)

  if (-not (Test-InstallerPythonReady)) {
    if ($VerifyOnly) {
      throw "MICO360 Python environment is missing."
    }
    if (-not $Online) {
      throw "MICO360 Python environment is missing and the system appears offline. Connect to the internet and rerun this installer."
    }
    Write-Host ""
    Write-Host "Creating dedicated MICO360 Python environment..." -ForegroundColor Cyan
    Show-SetupProgress -Status "Creating MICO360 Python environment" -SubPercent 15
    Invoke-Python -CommandArgs @("-m", "venv", $PythonEnvDir)
    if (-not (Test-InstallerPythonReady)) {
      throw "Could not create MICO360 Python environment at $PythonEnvDir."
    }
    Write-Log "INSTALL" "Created MICO360 Python environment: $PythonEnvDir"
    Add-Summary "Installed" "MICO360 Python environment"
  }

  if (Test-InstallerPythonImport "faster_whisper") {
    Write-Log "SKIP" "Python package already installed in MICO360 environment: faster_whisper"
    Add-Summary "Skipped" "faster-whisper already installed"
  } else {
    if ($VerifyOnly) {
      throw "Python package faster-whisper is missing from the MICO360 Python environment."
    }
    if (-not $Online) {
      throw "Python package faster-whisper is missing and the system appears offline. Connect to the internet and rerun this installer."
    }
    Write-Host ""
    Write-Host "Installing Python transcription package in MICO360 environment: $WhisperPackage" -ForegroundColor Cyan
    Write-Host "pip will show package download and installation progress below."
    Show-SetupProgress -Status "Updating pip" -SubPercent 25
    Invoke-InstallerPython -CommandArgs @("-m", "pip", "install", "--upgrade", "pip")
    Show-SetupProgress -Status "Installing $WhisperPackage" -SubPercent 55
    Invoke-InstallerPython -CommandArgs @("-m", "pip", "install", $WhisperPackage)
    Add-Summary "Installed" $WhisperPackage
  }
  Show-SetupProgress -Status "Verifying faster-whisper import" -SubPercent 90
  if (-not (Test-InstallerPythonImport "faster_whisper")) {
    throw "faster-whisper verification failed after installation."
  }
  Write-Log "VERIFY" "faster-whisper import verified in MICO360 Python environment."
  Add-Summary "Verified" "faster-whisper"
}

function Write-Config {
  $config = [ordered]@{
    ollamaModel = $OllamaModel
    whisperPackage = $WhisperPackage
    requiredPython = $RequiredPythonText
    autoInstallPython = $PythonInstallText
    pythonExecutable = $PythonEnvExe
    pythonEnvDir = $PythonEnvDir
    logFile = $LogFile
    completedAt = (Get-Date).ToString("o")
  }
  $configPath = Join-Path $ConfigDir "installer-config.json"
  $config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding UTF8
  Write-Log "VERIFY" "Configuration written: $configPath"
  Add-Summary "Verified" "Installer configuration"
}

try {
  $host.UI.RawUI.WindowTitle = "MICO360 Meetings Local AI Setup"
  Write-Host ""
  Write-Host "MICO360 Meetings Local AI Setup" -ForegroundColor Cyan
  Write-Host "This window prepares Ollama, the local AI model, Python, and faster-whisper."
  Write-Host "On a new PC this can take several minutes. Please keep this window open."
  Write-Host ""
  Write-Log "INFO" "MICO360 Meetings smart installer started."
  Write-Log "INFO" "Log file: $LogFile"
  $online = Test-Online

  Invoke-Step "Ollama detection and installation" { Ensure-Ollama -Online $online }
  Invoke-Step "Ollama service verification" { Ensure-OllamaServer }
  Invoke-Step "Ollama model preparation" { Ensure-OllamaModel -Online $online }
  Invoke-Step "Python detection and installation" { Ensure-Python -Online $online }
  Invoke-Step "faster-whisper preparation" { Ensure-FasterWhisper -Online $online }
  Invoke-Step "Write configuration" { Write-Config }

  Write-Progress -Id 2 -Activity "Current Step" -Completed
  Write-Progress -Id 1 -Activity "MICO360 Meetings Local AI Setup" -Status "Completed" -PercentComplete 100 -Completed
  Write-Log "DONE" "Smart installer completed successfully."
  Write-Log "DONE" ("Installed: {0}" -f (($Summary.Installed | ForEach-Object { $_ }) -join "; "))
  Write-Log "DONE" ("Skipped: {0}" -f (($Summary.Skipped | ForEach-Object { $_ }) -join "; "))
  Write-Log "DONE" ("Verified: {0}" -f (($Summary.Verified | ForEach-Object { $_ }) -join "; "))
  if (-not $NoPause -and -not $NonInteractive) {
    Write-Host ""
    Write-Host "MICO360 Meetings local AI setup completed successfully." -ForegroundColor Green
    if ($AppExe -and (Test-Path $AppExe)) {
      Write-Host "Opening MICO360 Meetings..."
      Start-Process -FilePath $AppExe | Out-Null
    } else {
      Write-Host "You can now open MICO360 Meetings."
    }
    Write-Host "Press Enter to close."
    [void][System.Console]::ReadLine()
  }
  exit 0
} catch {
  Write-Log "ERROR" $_.Exception.Message
  Write-Log "ERROR" "Smart installer failed. Review this log: $LogFile"
  if (-not $NoPause -and -not $NonInteractive) {
    Write-Host ""
    Write-Host "MICO360 Meetings prerequisite setup failed."
    Write-Host "Reason: $($_.Exception.Message)"
    Write-Host "Log: $LogFile"
    Write-Host "Press Enter to close."
    [void][System.Console]::ReadLine()
  }
  exit 1
}
