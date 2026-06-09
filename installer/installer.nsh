!include LogicLib.nsh

!macro customInstall
  DetailPrint "Launching MICO360 Meetings local AI setup..."
  ${IfNot} ${Silent}
    MessageBox MB_ICONINFORMATION|MB_OK "MICO360 Meetings is installed. A setup window will now open to prepare local AI prerequisites such as Ollama, qwen2.5, Python, and faster-whisper. This can take several minutes on a new PC. Please keep that setup window open until it says completed."
  ${EndIf}
  ${If} ${Silent}
    ExecShell "open" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\installer\MICO360-Meetings-Prerequisites.ps1" -AppExe "$INSTDIR\MICO360 Meetings.exe" -NonInteractive -NoPause' SW_HIDE
  ${Else}
    ExecShell "open" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\installer\MICO360-Meetings-Prerequisites.ps1" -AppExe "$INSTDIR\MICO360 Meetings.exe"' SW_SHOWNORMAL
  ${EndIf}
!macroend
