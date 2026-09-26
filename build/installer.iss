; ============================================================================
;  MICO360 Meetings - Inno Setup installer
;  Build the EXE first:   powershell -File build\build_exe.ps1
;  Then compile:          ISCC build\installer.iss
;  (or both at once:      powershell -File build\build_all.ps1)
;  Produces:              build\Output\MICO360Meetings-Setup.exe
;
;  Optional compile-time overrides:
;    /DDistDir=<folder>   PyInstaller output to package (default dist\MICO360Meetings)
;    /O<folder>           output folder (default build\Output)
; ============================================================================

#define AppName        "MICO360 Meetings"
#define AppVersion     "1.2.3"
#define AppPublisher   "MICO360"
#define AppExeName     "MICO360Meetings.exe"
#define DefaultModel   "llama3.1"
; Named mutex the running app holds (created by the app as "Local\MICO360Meetings",
; i.e. this name in the user's session namespace). See [Code] AppIsRunning.
#define AppMutexName   "MICO360Meetings"
; Per-user data folder (meetings, recordings, settings incl. SMTP password).
#define DataFolder     "MICO360Meetings"
#ifndef DistDir
  #define DistDir      "dist\MICO360Meetings"
#endif

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
; Writes %TEMP%\Setup Log <date>.txt - the first thing to ask for in a bug report.
SetupLogging=yes
; NOTE: the AppMutex directive is deliberately NOT used. In a silent install it
; aborts Setup at once if the app still holds its mutex, and the in-app updater
; starts Setup ~1.5 s BEFORE the app quits. [Code] InitializeSetup and
; InitializeUninstall perform the same check but wait for the app to exit.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "pullmodel";   Description: "Download AI model ({#DefaultModel}) and set up Ollama now"; GroupDescription: "AI setup:"; Flags: checkedonce

[InstallDelete]
; An upgrade must never mix the old and new Python runtime: PyInstaller puts
; every DLL/.pyd in _internal, and some packages (ctranslate2) load every DLL in
; their folder, so a file dropped by a new release would otherwise linger and be
; loaded. Only the program folder is touched - user data lives in
; %LOCALAPPDATA%\{#DataFolder} and is never removed here.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; The PyInstaller one-folder output (app + bundled Python runtime)
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The smart environment setup script (run from [Code] after install - see RunSmartSetup)
Source: "smart_setup.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Interactive install: offer to launch the app at the end.
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; In-app update (silent, started by the app with /RESTARTAPPLICATIONS or
; /RELAUNCH): the app quit itself so its files could be replaced - reopen it,
; un-elevated, as the user who ran the update. (Restart Manager cannot do this:
; it only restarts applications registered with RegisterApplicationRestart.)
Filename: "{app}\{#AppExeName}"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; ----------------------------------------------------------------------------
; NOTE: This installer bundles Python + all Python packages via PyInstaller, so
; the user does NOT need Python installed. Whisper models download on first use
; inside the app. Ollama + the LLM are handled by smart_setup.ps1 (see RunSmartSetup), which
; is idempotent: it skips components that are already installed and writes a log
; to %LOCALAPPDATA%\MICO360Meetings\logs\setup.log.
; ----------------------------------------------------------------------------

[Code]
const
  SetupLogHint = '%LOCALAPPDATA%\{#DataFolder}\logs\setup.log';

{ ---- running-app detection (replaces the AppMutex directive) ------------- }
function AppIsRunning(): Boolean;
begin
  Result := CheckForMutexes('{#AppMutexName}');
end;

{ Poll until the app has released its mutex; True if it is gone. }
function WaitForAppExit(Seconds: Integer): Boolean;
var
  I: Integer;
begin
  I := 0;
  while AppIsRunning() and (I < Seconds * 4) do
  begin
    Sleep(250);
    I := I + 1;
  end;
  Result := not AppIsRunning();
end;

function CmdLineHas(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

{ The in-app updater runs:  Setup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART
  /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS  (/RELAUNCH is also accepted). }
function IsInAppUpdate(): Boolean;
begin
  Result := WizardSilent() and (CmdLineHas('/RESTARTAPPLICATIONS') or CmdLineHas('/RELAUNCH'));
end;

function ShouldRelaunch(): Boolean;
begin
  Result := IsInAppUpdate();
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if WizardSilent() then
  begin
    { In-app update: the app quits ~1.5 s after starting Setup. Give it time
      rather than failing; if it is still up, Restart Manager closes it. }
    if not WaitForAppExit(30) then
      Log('The app is still running after 30 s - Setup will ask Restart Manager to close it.');
    if IsInAppUpdate() then
      Sleep(2000);  { let the app finish exiting even if it holds no mutex yet }
    Exit;
  end;
  while AppIsRunning() do
  begin
    if MsgBox('{#AppName} is running.' + #13#10#13#10 +
              'Please close it (including from the system tray), then click OK to continue.',
              mbError, MB_OKCANCEL) <> IDOK then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

{ ---- AI setup (Ollama + model) ------------------------------------------- }
{ Runs smart_setup.ps1 as the user who started Setup - NOT the elevated admin -
  so Ollama and the model land in that user's profile, and reports failure
  instead of ignoring the exit code. }
procedure RunSmartSetup();
var
  Params: String;
  ShowCmd, ResultCode: Integer;
  Ok: Boolean;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\smart_setup.ps1') +
            '" -Models "{#DefaultModel}" -NonInteractive';
  if WizardSilent() then
    ShowCmd := SW_HIDE
  else
    ShowCmd := SW_SHOWNORMAL;   { the console shows download progress }
  WizardForm.StatusLabel.Caption :=
    'Preparing AI components (Ollama + model). This may take several minutes - see the console window for progress...';
  WizardForm.FilenameLabel.Caption := '';
  Log('Running smart_setup.ps1 as the original user: ' + Params);
  Ok := ExecAsOriginalUser('powershell.exe', Params, ExpandConstant('{app}'), ShowCmd,
                           ewWaitUntilTerminated, ResultCode);
  if Ok and (ResultCode = 0) then
  begin
    Log('smart_setup.ps1 finished OK.');
    Exit;
  end;
  if not Ok then
    Log('smart_setup.ps1 could not be started: ' + SysErrorMessage(ResultCode))
  else
    Log('smart_setup.ps1 failed with exit code ' + IntToStr(ResultCode));
  SuppressibleMsgBox(
    'AI setup (Ollama + the ' + '{#DefaultModel}' + ' model) did not complete' +
    ' (exit code ' + IntToStr(ResultCode) + ').' + #13#10#13#10 +
    '{#AppName} is installed. Transcription works offline; to generate minutes, ' +
    'finish AI setup later from Settings > Install Required Model, or install ' +
    'Ollama from https://ollama.com.' + #13#10#13#10 +
    'Details are in the setup log:' + #13#10 + SetupLogHint,
    mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { An in-app update must not stall on a multi-GB model download; the app
      offers "Install Required Model" itself. }
    if WizardIsTaskSelected('pullmodel') and not IsInAppUpdate() then
      RunSmartSetup();
  end;
end;

{ ---- uninstall ------------------------------------------------------------ }
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if UninstallSilent() then
  begin
    Result := WaitForAppExit(30);
    if not Result then
      Log('Uninstall aborted: {#AppName} is still running.');
    Exit;
  end;
  while AppIsRunning() do
  begin
    if MsgBox('{#AppName} is running.' + #13#10#13#10 +
              'Please close it (including from the system tray), then click OK to continue.',
              mbError, MB_OKCANCEL) <> IDOK then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

{ Meetings, recordings and settings (which include the saved SMTP password)
  are the user's data: ask before deleting, default No, never in silent mode. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  DataDir := ExpandConstant('{localappdata}\{#DataFolder}');
  if UninstallSilent() or not DirExists(DataDir) then
    Exit;
  if MsgBox('Do you also want to delete your {#AppName} data?' + #13#10#13#10 +
            'This permanently removes all saved meetings, transcripts, minutes, ' +
            'recordings, company profiles and settings (including the saved email ' +
            'password) in:' + #13#10 + DataDir + #13#10#13#10 +
            'Choose No to keep them (for example, to reinstall later).',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    if DelTree(DataDir, True, True, True) then
      Log('Deleted user data: ' + DataDir)
    else
      MsgBox('Some files could not be deleted. You can remove this folder by hand:' + #13#10 +
             DataDir, mbInformation, MB_OK);
  end
  else
    Log('User data kept: ' + DataDir);
end;
