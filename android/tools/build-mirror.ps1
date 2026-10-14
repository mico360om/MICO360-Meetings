<# Build the Android app OUTSIDE Dropbox: mirror the sources, install, prebuild, gradle.
     powershell -File android\tools\build-mirror.ps1 [-Variant debug|release] #>
param([ValidateSet("debug", "release")][string]$Variant = "release",
      [string]$BuildDir = "$env:USERPROFILE\mico360-build\app")
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot
robocopy $src $BuildDir /MIR /XD node_modules android .expo .claude /XF .lockhash /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
Push-Location $BuildDir
try {
  $lock = (Get-FileHash package-lock.json).Hash
  if (-not (Test-Path node_modules) -or -not (Test-Path .lockhash) -or (Get-Content .lockhash) -ne $lock) {
    npm ci; if ($LASTEXITCODE) { throw "npm ci failed" }
    Set-Content .lockhash $lock
  }
  $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
  if (-not $env:MICO360_NINJA) {   # the SDK's ninja 1.10 fails on paths over 260 characters
    $vsNinja = Get-ChildItem "${env:ProgramFiles(x86)}\Microsoft Visual Studio", "$env:ProgramFiles\Microsoft Visual Studio" `
      -Filter ninja.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($vsNinja) { $env:MICO360_NINJA = $vsNinja.FullName }
  }
  npx expo prebuild --platform android --no-install; if ($LASTEXITCODE) { throw "prebuild failed" }
  Push-Location android
  try {
    $task = "assemble" + $Variant.Substring(0, 1).ToUpper() + $Variant.Substring(1)
    $env:ORG_GRADLE_PROJECT_reactNativeArchitectures = $Abis   # -PreactNativeArchitectures was not applied when passed via gradlew.bat
    .\gradlew.bat $task; if ($LASTEXITCODE) { throw "gradle failed" }
  } finally { Pop-Location }
  $apk = Get-ChildItem "android\app\build\outputs\apk\$Variant\*.apk" | Select-Object -First 1
  Write-Output "APK: $($apk.FullName)"
} finally { Pop-Location }
