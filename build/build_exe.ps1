<#
  Builds only the standalone MICO360 Meetings app with PyInstaller (no installer).
  Run from the project root:
      powershell -ExecutionPolicy Bypass -File build\build_exe.ps1 [-Python <path>]

  This is build_all.ps1 -SkipInstaller, so it follows the same rules: pinned
  dependencies (constraints.txt), Python 3.12 preferred, every step's exit code
  checked, and the build-time key file removed afterwards.
  Keep this file ASCII-only (Windows PowerShell 5.1 reads it as cp1252).
#>
param([string] $Python = "")
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "build_all.ps1") -Python $Python -SkipInstaller
exit $LASTEXITCODE
