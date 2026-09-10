<#
  One-command build: PyInstaller .exe  ->  Inno Setup installer.
  Run from the project root:
      powershell -ExecutionPolicy Bypass -File build\build_all.ps1
  Produces:
      build\dist\MICO360Meetings\MICO360Meetings.exe   (standalone app)
      build\Output\MICO360Meetings-Setup.exe           (installer)
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Installing build tooling (pyinstaller)..." -ForegroundColor Cyan
python -m pip install --upgrade pip pyinstaller | Out-Null
python -m pip install -r requirements.txt | Out-Null

Write-Host "==> Cleaning previous build..." -ForegroundColor Cyan
Remove-Item -Recurse -Force "$root\build\dist","$root\build\work" -ErrorAction SilentlyContinue

Write-Host "==> Building app with PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller "build\mico360.spec" --noconfirm --distpath "build\dist" --workpath "build\work"

$exe = "build\dist\MICO360Meetings\MICO360Meetings.exe"
if (-not (Test-Path $exe)) { Write-Error "Build failed: $exe not found"; exit 1 }
Write-Host "==> App built: $exe" -ForegroundColor Green

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
& $iscc "build\installer.iss"

$setup = "build\Output\MICO360Meetings-Setup.exe"
if (Test-Path $setup) {
    Write-Host "==> Installer ready: $setup" -ForegroundColor Green
    # Publish a SHA256 so the in-app updater can verify a download before running it.
    $sha = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
    "$sha *$(Split-Path -Leaf $setup)" | Out-File -Encoding ascii "build\Output\SHA256SUMS.txt"
    $sha | Out-File -Encoding ascii "$setup.sha256"
    Write-Host "==> SHA256: $sha" -ForegroundColor Green
} else {
    Write-Error "Installer compile failed: $setup not found"
}
