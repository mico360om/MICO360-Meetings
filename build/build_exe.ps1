<#
  Builds the standalone MICO360 Meetings executable with PyInstaller.
  Run from the project root:   powershell -ExecutionPolicy Bypass -File build\build_exe.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Ensuring build tooling…" -ForegroundColor Cyan
python -m pip install --upgrade pip pyinstaller | Out-Null
python -m pip install -r requirements.txt | Out-Null

Write-Host "==> Cleaning previous build…" -ForegroundColor Cyan
Remove-Item -Recurse -Force "$root\build\dist","$root\build\work" -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller…" -ForegroundColor Cyan
pyinstaller "build\mico360.spec" --noconfirm `
    --distpath "build\dist" --workpath "build\work"

$exe = "build\dist\MICO360Meetings\MICO360Meetings.exe"
if (Test-Path $exe) {
    Write-Host "==> Build OK -> $exe" -ForegroundColor Green
} else {
    Write-Error "Build failed: $exe not found"
}
