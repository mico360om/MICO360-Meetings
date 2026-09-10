; ============================================================================
;  MICO360 Meetings - Inno Setup installer
;  Build the EXE first:   powershell -File build\build_exe.ps1
;  Then compile:          ISCC build\installer.iss
;  Produces:              build\Output\MICO360Meetings-Setup.exe
; ============================================================================

#define AppName        "MICO360 Meetings"
#define AppVersion     "1.2.1"
#define AppPublisher   "MICO360"
#define AppExeName     "MICO360Meetings.exe"
#define DefaultModel   "llama3.1"

[Setup]
AppId={{8B1F0C2A-7E54-4F2C-9A1D-MICO360MEET01}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://mico360.com
AppSupportURL=https://mico360.com
AppContact=info@mico360.com
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=MICO360Meetings-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "pullmodel";   Description: "Download AI model ({#DefaultModel}) and set up Ollama now"; GroupDescription: "AI setup:"; Flags: checkedonce

[Files]
; The PyInstaller one-folder output (app + bundled Python runtime)
Source: "dist\MICO360Meetings\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The smart environment setup script
Source: "smart_setup.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Smart setup: detect/install Ollama + pull the model (skips anything present).
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\smart_setup.ps1"" -Models ""{#DefaultModel}"""; \
  StatusMsg: "Preparing AI components (Ollama + model). This may take several minutes..."; \
  Flags: runhidden waituntilterminated; \
  Tasks: pullmodel
; NOTE: the packaged app bundles its own Python, so -EnsurePython is NOT passed
; here. For a source/dev install, run smart_setup.ps1 manually with -EnsurePython.
; Offer to launch the app at the end.
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; ----------------------------------------------------------------------------
; NOTE: This installer bundles Python + all Python packages via PyInstaller, so
; the user does NOT need Python installed. Whisper models download on first use
; inside the app. Ollama + the LLM are handled by smart_setup.ps1 above, which
; is idempotent: it skips components that are already installed and writes a log
; to %LOCALAPPDATA%\MICO360Meetings\logs\setup.log.
; ----------------------------------------------------------------------------
