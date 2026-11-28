; PMV Nexus -- Windows installer (2.26.0; renamed 2.28.0). Built by .github/workflows/release.yml
; with Inno Setup 6.3+:  iscc /DAppVersion=x.y.z /DPythonSha256=<sha256> PMVNexus.iss
; (both defines are optional: the version is read from VERSION; without the hash the
; downloaded Python installer isn't checked).
;
; Pages: Welcome, License, folder, How it runs (shortcuts / start with Windows / service,
; FFmpeg), Ready, Installing (Python if missing, app files, .venv + packages, FFmpeg),
; then -- only on a first install -- New library or From a backup,
; backup file (+ password), Videos & cache folders, other folders (only when one can't be
; reached), Port, Finish. Every decision is made by Tools/setup_core.py (INI in, INI out);
; this script only shows pages and runs it.

#define AppName "PMV Nexus"
#ifndef AppVersion
  #define VersionFile FileOpen(AddBackslash(SourcePath) + "..\..\VERSION")
  #define AppVersion Trim(FileRead(VersionFile))
  #expr FileClose(VersionFile)
#endif
#ifndef PythonVersion
  #define PythonVersion "3.12.10"
#endif
#ifndef PythonSha256
  #define PythonSha256 ""
#endif
#define PythonUrl "https://www.python.org/ftp/python/" + PythonVersion + "/python-" + PythonVersion + "-amd64.exe"
#define FFmpegUrl "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
#define ServiceExe "{app}\runtime\service\PMVNexusService.exe"

[Setup]
AppId={{8C3E6A52-6F0B-4C7A-9D3E-2B51A7F0C4D1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=edstagdh
AppPublisherURL=https://github.com/edstagdh/PMV_Nexus
AppSupportURL=https://github.com/edstagdh/PMV_Nexus/issues
AppUpdatesURL=https://github.com/edstagdh/PMV_Nexus/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user by default (no admin needed); "for all users" (admin) is needed for the service option.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; The terms of use (TERMS.txt = DISCLAIMER.md) must be accepted to install (2.28.0).
LicenseFile=TERMS.txt
OutputDir=build\out
OutputBaseFilename=PMV-Nexus-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\installer\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=no
; Setup's own log (3.7.0): copied to C:\Logs with the install log when the install fails.
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Desktop shortcut"; Flags: unchecked
Name: "runmanual"; Description: "Only when I start it (Start menu shortcut)"; GroupDescription: "How the app runs:"; Flags: exclusive
Name: "autostart"; Description: "Also start it when I sign in to Windows"; GroupDescription: "How the app runs:"; Flags: exclusive unchecked
Name: "service"; Description: "As a Windows service: always on, even before anyone signs in"; GroupDescription: "How the app runs:"; Flags: exclusive unchecked; Check: IsAdminInstallMode
Name: "ffmpeg"; Description: "Download FFmpeg (needed for re-encoding, previews and some downloads; about 100 MB)"; GroupDescription: "Extras:"; Check: not FFmpegOnPath

[Dirs]
; The app writes its data next to itself, so the folder must be writable for the users running it.
Name: "{app}"; Permissions: users-modify

[Files]
Source: "..\..\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; \
  Excludes: "\.git\*,\.github\*,\extensions\.git\*,\extensions\.github\*,\extensions\*.md,\ui\*,\installer\*,\data\*,\runtime\*,\.venv\*,\downloads\*,\languages\*,\config.yaml,__pycache__,*.pyc,node_modules"
; Languages (3.7.0): only the English list ships; other languages are installed from the app.
Source: "..\..\languages\en.json"; DestDir: "{app}\languages"; Flags: ignoreversion
Source: "app.ico"; DestDir: "{app}\installer"; Flags: ignoreversion
; What the shortcuts start, so Windows shows "PMV Nexus" (Task Manager -> Startup) instead of
; "pythonw" (3.2.0; installer\windows\launcher, built by build_launcher.ps1). 3.7.0: a copy in
; .venv\Scripts (InstallAfterFiles) runs Python itself, so the running app is "PMV Nexus" too.
Source: "build\PMVNexus.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\PMVNexusService.exe"; DestDir: "{app}\runtime\service"; Flags: ignoreversion; Tasks: service
Source: "PMVNexusService.xml"; DestDir: "{app}\runtime\service"; Flags: ignoreversion; Tasks: service
; Closes a running app before an update (PrepareToInstall), run from {tmp} with the installed app's Python (2.33.0).
Source: "..\..\Tools\stop_app.py"; Flags: dontcopy
; What the update will change in the library, shown before anything is replaced (3.12.0): run from {tmp}
; with the installed app's Python, with this version's upgrade steps.
Source: "..\..\Tools\upgrade_plan.py"; Flags: dontcopy
Source: "..\..\Tools\pre_install_backup.py"; Flags: dontcopy
Source: "..\..\backend\upgrades\steps.py"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\PMVNexus.exe"; \
  WorkingDir: "{app}"; Comment: "Open PMV Nexus (starts it if needed)"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PMVNexus.exe"; \
  WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\PMVNexus.exe"; Parameters: "run.py --tray"; \
  WorkingDir: "{app}"; Comment: "Starts PMV Nexus in the tray"; Tasks: autostart
; A console version, for troubleshooting (shows the app's log as it runs).
Name: "{autoprograms}\{#AppName} (console)"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; \
  IconFilename: "{app}\installer\app.ico"; Check: not WizardIsTaskSelected('service')

[Run]
Filename: "{app}\PMVNexus.exe"; WorkingDir: "{app}"; \
  Description: "Open PMV Nexus now"; Flags: postinstall nowait skipifsilent; Check: AppReadyNew
; An update (2.34.0): the app's Help -> Changelog instead -- the usual "what's new" on an updater's last page.
Filename: "{app}\PMVNexus.exe"; Parameters: "-m Tools.open_app --page /help/changelog"; WorkingDir: "{app}"; \
  Description: "See what's new in version {#AppVersion}"; Flags: postinstall nowait skipifsilent; Check: AppReadyUpdated

[InstallDelete]
; An update replaces the app's own code folders as a whole (2.33.0), so files a new
; version no longer has don't stay behind. Nothing of yours is in them: the data,
; config.yaml, extensions, .venv and runtime folders are kept.
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\Tools"
Type: filesandordirs; Name: "{app}\userscript"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\languages"
; Left over from older versions: the site-login browser (2.32.0).
Type: filesandordirs; Name: "{app}\runtime\ms-playwright"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
; what an update keeps to undo itself, if a Setup was ended before it could remove it (3.7.0)
Type: filesandordirs; Name: "{app}\.rollback"
Type: filesandordirs; Name: "{app}\runtime"
; The app's code folders as a whole (2.33.4): Python leaves __pycache__ folders in every
; package (backend\sources, extensions...), which a hand-made list missed. More __pycache__
; folders (e.g. in extensions) are removed by DeletePyCaches below.
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\Tools"
Type: filesandordirs; Name: "{app}\userscript"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\languages"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
{ Note: no line here may start with "#" (e.g. a #13#10 continuation) -- the preprocessor
  would read it as one of its directives. Keep this file plain ASCII. }
const
  FolderKeysMain = 2;   { output_dir, cache_dir on the first folders page }
  FailureLogDir = 'C:\Logs';   { a failed install's details (3.7.0, SaveFailureLog) }

var
  DownloadPage: TDownloadWizardPage;
  WorkPage: TOutputMarqueeProgressWizardPage;   { "what it's doing" while a step runs (2.26.2) }
  ModePage: TInputOptionWizardPage;
  BackupFilePage: TInputFileWizardPage;
  BackupPwPage: TInputQueryWizardPage;
  FoldersPage: TInputDirWizardPage;
  MoreFoldersPage: TInputDirWizardPage;
  AdminPage: TInputQueryWizardPage;   { new library: the admin account (no setup code needed, 2.29.0) }
  BrowserPage: TInputQueryWizardPage;   { opening the app: browser command, open on start (2.31.0) }
  OpenOnStartCheck: TNewCheckBox;
  PortPage: TInputQueryWizardPage;
  AdminPassword: String;
  PythonExe: String;        { base interpreter used to make .venv }
  NeedPython: Boolean;
  IsUpgrade: Boolean;       { config.yaml was already there: keep everything, skip the set-up pages }
  SetupFailed: Boolean;     { a step after copying failed: skip the set-up pages }
  NeedPassword: Boolean;
  MoreFoldersNeeded: Boolean;
  ServiceWasThere: Boolean;
  AppWasRunning: Boolean;   { closed by Setup for the update: started again afterwards (2.33.0) }
  BackupInfo: String;       { shown on the folders page once a backup was opened }
  StepCount: Integer;       { RunWithProgress: each step has its own "done" file (3.7.0) }
  LastExitCode: Integer;    { the last step's exit code, -1 when it couldn't be started }
  FolderKeys: array[0..5] of String;
  { Cancel and undo (3.7.0): what this Setup run did, so Cancel can undo it (Rollback). }
  UndoButton: TNewButton;   { Cancel while Setup's own steps run and on the pages after installing }
  StepRunning: Boolean;     { a RunWithProgress step is running }
  CancelRequested: Boolean; { Cancel was confirmed while a step ran: it's stopped, then Rollback }
  RolledBack: Boolean;      { undone: nothing more to do but end }
  Committed: Boolean;       { finished: nothing to undo any more }
  RollbackProblems: String; { what couldn't be undone }
  JournalStarted: Boolean;  { PrepareToInstall ran: from here on there's something to undo }
  AppDirExisted: Boolean;   { the app folder was there before this run (else Rollback removes it) }
  PreExisting: String;      { '|name|...|' -- what was at the top of the app folder before }
  PreExistingOk: Boolean;
  JCodeSaved: Boolean;      { the installed version's code is in .rollback\code }
  JServiceSaved: Boolean;   { ... and its service files in .rollback\service }
  ServiceDirExisted: Boolean;
  JServiceStopped: Boolean; { the Windows service was stopped for the update }
  JServiceRemoved: Boolean; { ... and removed (switched away from running as a service) }
  JPythonInstalled: Boolean;{ Setup installed Python }
  JVenvMoved: Boolean;      { the old .venv is in .rollback\venv; .venv is a new one }
  JVenvCreated: Boolean;    { .venv was made by this run }
  VenvNoUndo: Boolean;      { packages were changed in place: can't be undone }
  JFfmpegMoved: Boolean;    { the old FFmpeg is in .rollback\ffmpeg }
  JFfmpegCreated: Boolean;  { runtime\ffmpeg is new }
  JServiceInstalled: Boolean;
  JFirewall: Boolean;
  NewFolders: TArrayOfString; { folders the set-up pages had Setup create (outside the app folder too) }

{ ---------------------------------------------------------------- helpers }

function FFmpegOnPath: Boolean;
begin
  Result := FileSearch('ffmpeg.exe', GetEnv('PATH')) <> '';
end;

function AppDir: String;
begin
  Result := ExpandConstant('{app}');
end;

function VenvPython: String;
begin
  Result := AppDir + '\.venv\Scripts\python.exe';
end;

function AppReady: Boolean;
begin
  Result := (not SetupFailed) and FileExists(VenvPython);
end;

{ The Finish page: a new install offers "Open PMV Nexus now", an update "See what's new". }
function AppReadyNew: Boolean;
begin
  Result := AppReady and not IsUpgrade;
end;

function AppReadyUpdated: Boolean;
begin
  Result := AppReady and IsUpgrade;
end;

function LogFile: String;
begin
  Result := AppDir + '\data\logs\install.log';
end;

function IniFile: String;
begin
  Result := ExpandConstant('{tmp}\setup.ini');
end;

function ProgressFile: String;
begin
  Result := ExpandConstant('{tmp}\progress.txt');
end;

{ The Pythons the app supports, newest first (3.7.0): 3.14, 3.13, 3.12 -- requirements.txt pins
  the package versions the app is tested with, and they need 3.12 or newer (numpy). 3.15 and
  newer once their packages are out and tested. Found through the
  registry entries python.org's installers make (PEP 514); a Python without them (conda,
  pyenv, a zip) isn't seen. }
function FindPython: Boolean;
var
  Versions: array[0..2] of String;
  Roots: array[0..2] of Integer;
  I, J: Integer;
  Exe: String;
begin
  Result := False;
  Versions[0] := '3.14'; Versions[1] := '3.13'; Versions[2] := '3.12';
  Roots[0] := HKCU; Roots[1] := HKLM64; Roots[2] := HKLM32;
  for I := 0 to 2 do
    for J := 0 to 2 do
      if RegQueryStringValue(Roots[J], 'Software\Python\PythonCore\' + Versions[I] + '\InstallPath', 'ExecutablePath', Exe) and FileExists(Exe) then
      begin
        PythonExe := Exe;
        Result := True;
        Exit;
      end;
end;

{ Was the app's .venv made with a supported Python (see FindPython)? Read from
  .venv\pyvenv.cfg ("version = 3.12.10"). An older .venv is made again on an update. }
function VenvVersionOk: Boolean;
var
  Lines: TArrayOfString;
  I, P: Integer;
  Value: String;
begin
  Result := False;
  if not LoadStringsFromFile(AppDir + '\.venv\pyvenv.cfg', Lines) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    P := Pos('=', Lines[I]);
    if (P > 0) and (Pos('version', Lowercase(Trim(Copy(Lines[I], 1, P - 1)))) = 1) then
    begin
      Value := Trim(Copy(Lines[I], P + 1, 20)) + '.';
      if (Pos('3.12.', Value) = 1) or (Pos('3.13.', Value) = 1) or (Pos('3.14.', Value) = 1) then
      begin
        Result := True;
        Exit;
      end;
    end;
  end;
end;

{ The last line of a text file (the install log), shown under the progress bar. }
function LastLine(const FileName: String): String;
var
  S: AnsiString;
  T: String;
  P: Integer;
begin
  Result := '';
  if not LoadStringFromFile(FileName, S) then
    Exit;
  T := TrimRight(String(S));
  P := Length(T);
  while (P > 0) and (T[P] <> #10) and (T[P] <> #13) do
    P := P - 1;
  Result := Copy(T, P + 1, 150);
end;

{ ---------------------------------------------------------------- cancel and undo (3.7.0) }

function RollbackDir: String;
begin
  Result := AppDir + '\.rollback';
end;

function UninstallKey: String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + '{' + '8C3E6A52-6F0B-4C7A-9D3E-2B51A7F0C4D1' + '}' + '_is1';
end;

{ '|name|name|' of what's at the top of Dir (lower case). Ok: it could be read. }
function TopEntries(const Dir: String; var Ok: Boolean): String;
var
  F: TFindRec;
begin
  Result := '|';
  Ok := False;
  if FindFirst(Dir + '\*', F) then
  try
    Ok := True;
    repeat
      if (F.Name <> '.') and (F.Name <> '..') then
        Result := Result + Lowercase(F.Name) + '|';
    until not FindNext(F);
  finally
    FindClose(F);
  end;
end;

procedure UndoProblem(const What: String);
begin
  Log('Undo: could not ' + What);
  RollbackProblems := RollbackProblems + '- ' + What + #13#10;
end;

{ Deletes a folder (or file) this run created. }
procedure RemoveCreated(const Path: String);
begin
  if DirExists(Path) then
  begin
    if not DelTree(Path, True, True, True) then
      UndoProblem('delete ' + Path);
  end
  else if FileExists(Path) and not DeleteFile(Path) then
    UndoProblem('delete ' + Path);
end;

{ Puts Saved back as Target (both on the app's drive: a rename). }
procedure PutBack(const Saved, Target: String);
begin
  if not (DirExists(Saved) or FileExists(Saved)) then
    Exit;
  RemoveCreated(Target);
  if not RenameFile(Saved, Target) then
    UndoProblem('put back ' + Target + ' (the saved copy is ' + Saved + ')');
end;

{ The installed version's code, saved before the update replaces it (PrepareToInstall). }
function SaveInstalledCode: Boolean;
var
  Dirs: array[0..7] of String;
  I, Code: Integer;
  Robocopy, Opt, RB: String;
begin
  Result := True;
  RB := RollbackDir;
  Robocopy := ExpandConstant('{sys}\robocopy.exe');
  Opt := ' /E /XD __pycache__ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP';
  Dirs[0] := 'backend'; Dirs[1] := 'frontend'; Dirs[2] := 'Tools'; Dirs[3] := 'userscript';
  Dirs[4] := 'docs'; Dirs[5] := 'languages'; Dirs[6] := 'extensions'; Dirs[7] := 'installer';
  for I := 0 to 7 do
    if DirExists(AppDir + '\' + Dirs[I]) then
      if not Exec(Robocopy, '"' + AppDir + '\' + Dirs[I] + '" "' + RB + '\code\' + Dirs[I] + '"' + Opt, '',
                  SW_HIDE, ewWaitUntilTerminated, Code) or (Code >= 8) then
        Result := False;
  { the files at the top (not config.yaml: an update doesn't change it) }
  if not Exec(Robocopy, '"' + AppDir + '" "' + RB + '\code" *.py *.md *.txt *.bat *.sh *.exe *.dat VERSION LICENSE ' +
              'config.example.yaml /XF config.yaml /R:1 /W:1 /NFL /NDL /NJH /NJS /NP', '',
              SW_HIDE, ewWaitUntilTerminated, Code) or (Code >= 8) then
    Result := False;
  if DirExists(AppDir + '\runtime\service') then
  begin
    if not Exec(Robocopy, '"' + AppDir + '\runtime\service" "' + RB + '\service"' + Opt, '',
                SW_HIDE, ewWaitUntilTerminated, Code) or (Code >= 8) then
      Result := False
    else
      JServiceSaved := True;
  end;
end;

{ The installed version's code back in place, and what the update added at the top removed. }
procedure RestoreCode;
var
  F: TFindRec;
  Names: TArrayOfString;
  I, N: Integer;
  Code: String;
begin
  Code := RollbackDir + '\code';
  N := 0;
  if FindFirst(Code + '\*', F) then
  try
    repeat
      if (F.Name <> '.') and (F.Name <> '..') then
      begin
        SetArrayLength(Names, N + 1);
        Names[N] := F.Name;
        N := N + 1;
      end;
    until not FindNext(F);
  finally
    FindClose(F);
  end;
  for I := 0 to N - 1 do
    PutBack(Code + '\' + Names[I], AppDir + '\' + Names[I]);
end;

{ What's at the top of the app folder now but wasn't before this run. }
procedure RemoveNewTopEntries;
var
  F: TFindRec;
  Names: TArrayOfString;
  I, N: Integer;
begin
  if not PreExistingOk then
    Exit;     { not known what was there: delete nothing }
  N := 0;
  if FindFirst(AppDir + '\*', F) then
  try
    repeat
      if (F.Name <> '.') and (F.Name <> '..') and (Lowercase(F.Name) <> '.rollback') and
         (Pos('|' + Lowercase(F.Name) + '|', PreExisting) = 0) then
      begin
        SetArrayLength(Names, N + 1);
        Names[N] := F.Name;
        N := N + 1;
      end;
    until not FindNext(F);
  finally
    FindClose(F);
  end;
  for I := 0 to N - 1 do
    RemoveCreated(AppDir + '\' + Names[I]);
end;

{ The topmost folder of Path that doesn't exist yet ('' when Path exists, or only a drive is missing). }
function TopNewFolder(const Path: String): String;
var
  P: String;
begin
  Result := '';
  P := RemoveBackslashUnlessRoot(Trim(Path));
  if (P = '') or DirExists(P) then
    Exit;
  while (ExtractFileDir(P) <> '') and (ExtractFileDir(P) <> P) and not DirExists(ExtractFileDir(P)) do
    P := ExtractFileDir(P);
  if (ExtractFileDir(P) = '') or (ExtractFileDir(P) = P) then
    Exit;     { never a drive or share root }
  Result := P;
end;

procedure NoteNewFolder(const Path: String);
var
  P: String;
  I: Integer;
begin
  P := TopNewFolder(Path);
  if P = '' then
    Exit;
  for I := 0 to GetArrayLength(NewFolders) - 1 do
    if CompareText(NewFolders[I], P) = 0 then
      Exit;
  I := GetArrayLength(NewFolders);
  SetArrayLength(NewFolders, I + 1);
  NewFolders[I] := P;
end;

{ What Cancel would undo, for the question. }
function UndoSummary: String;
var
  I: Integer;
begin
  Result := '';
  if not AppDirExisted then
    Result := Result + '- remove PMV Nexus from ' + AppDir + ' (Setup made that folder), with its shortcuts' + #13#10
  else if JCodeSaved then
  begin
    Result := Result + '- put back the version of PMV Nexus that was installed';
    if VenvNoUndo then
      Result := Result + ' (the Python packages it changed stay changed)';
    Result := Result + #13#10;
  end
  else
    Result := Result + '- remove what it added to ' + AppDir + #13#10;
  for I := 0 to GetArrayLength(NewFolders) - 1 do
    Result := Result + '- delete the folder it created: ' + NewFolders[I] + #13#10;
  if JServiceInstalled then
    Result := Result + '- remove the Windows service it set up' + #13#10;
  if JFirewall then
    Result := Result + '- remove the firewall rule it added' + #13#10;
  if JPythonInstalled then
    Result := Result + '- uninstall the Python it installed' + #13#10;
  if JServiceStopped or JServiceRemoved or AppWasRunning then
    Result := Result + '- start PMV Nexus again, as it was' + #13#10;
end;

function ConfirmCancel: Boolean;
begin
  Result := MsgBox('Cancel the installation?' + #13#10#13#10 +
    'Setup stops what it''s doing and undoes what it did so far:' + #13#10 + UndoSummary + #13#10 +
    'Nothing else is changed.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

{ Undoes this run, newest first. Only what this run did: a folder or file is deleted only when
  this run created it; what an update replaced is put back from .rollback. }
procedure Rollback;
var
  I, Code: Integer;
  Service: String;
begin
  if RolledBack or not JournalStarted then
    Exit;
  RolledBack := True;
  SetupFailed := True;
  Log('Cancelled: undoing what Setup did');
  Service := AppDir + '\runtime\service\PMVNexusService.exe';
  { the set-up pages: firewall, service, a staged restore, the folders they created }
  if JFirewall then
    Exec('netsh', 'advfirewall firewall delete rule name="PMV Nexus"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if JServiceInstalled and FileExists(Service) then
  begin
    Exec(Service, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(Service, 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  if not IsUpgrade and FileExists(VenvPython) and FileExists(AppDir + '\Tools\setup_core.py') then
    Exec(VenvPython, '-m Tools.setup_core cancel-restore', AppDir, SW_HIDE, ewWaitUntilTerminated, Code);
  for I := GetArrayLength(NewFolders) - 1 downto 0 do
    RemoveCreated(NewFolders[I]);
  { the app folder }
  if not AppDirExisted then
  begin
    if (Length(AppDir) > 3) and DirExists(AppDir) and not DelTree(AppDir, True, True, True) then
      UndoProblem('delete ' + AppDir);
  end
  else
  begin
    if JVenvMoved then
      PutBack(RollbackDir + '\venv', AppDir + '\.venv')
    else if JVenvCreated then
      RemoveCreated(AppDir + '\.venv');
    if JFfmpegMoved then
      PutBack(RollbackDir + '\ffmpeg', AppDir + '\runtime\ffmpeg')
    else if JFfmpegCreated then
      RemoveCreated(AppDir + '\runtime\ffmpeg');
    if JServiceSaved then
      PutBack(RollbackDir + '\service', AppDir + '\runtime\service')
    else if not ServiceDirExisted then
      RemoveCreated(AppDir + '\runtime\service');
    if JCodeSaved then
    begin
      RestoreCode;
      { the launcher's copy in .venv (made by the update) from the launcher put back }
      if not JVenvMoved and FileExists(AppDir + '\.venv\Scripts\PMVNexus.exe') then
        FileCopy(AppDir + '\PMVNexus.exe', AppDir + '\.venv\Scripts\PMVNexus.exe', False);
    end;
    RemoveNewTopEntries;
    DelTree(RollbackDir, True, True, True);
  end;
  if not IsUpgrade then
  begin
    DeleteFile(ExpandConstant('{autoprograms}\{#AppName}.lnk'));
    DeleteFile(ExpandConstant('{autoprograms}\{#AppName} (console).lnk'));
    DeleteFile(ExpandConstant('{autodesktop}\{#AppName}.lnk'));
    DeleteFile(ExpandConstant('{userstartup}\{#AppName}.lnk'));
    RegDeleteKeyIncludingSubkeys(HKA, UninstallKey);
  end;
  { what Setup stopped: started again }
  if JServiceRemoved and FileExists(Service) then
  begin
    Exec(Service, 'install', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(Service, 'start', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end
  else if JServiceStopped and FileExists(Service) then
    Exec(Service, 'start', '', SW_HIDE, ewWaitUntilTerminated, Code)
  else if AppWasRunning and FileExists(AppDir + '\PMVNexus.exe') then
    ExecAsOriginalUser(AppDir + '\PMVNexus.exe', 'run.py --tray', AppDir, SW_HIDE, ewNoWait, Code);
  { Python, last: nothing needs it any more }
  if JPythonInstalled and FileExists(ExpandConstant('{tmp}\python-installer.exe')) then
    if not Exec(ExpandConstant('{tmp}\python-installer.exe'), '/uninstall /passive', '', SW_SHOW, ewWaitUntilTerminated, Code)
       or (Code <> 0) then
      UndoProblem('uninstall the Python Setup installed (Settings -> Apps)');
end;

function RollbackText: String;
begin
  if RollbackProblems = '' then
    Result := 'Setup was cancelled, and what it did was undone.'
  else
    Result := 'Setup was cancelled. Most of what it did was undone, but not these:' + #13#10 + RollbackProblems;
end;

procedure ReportRollback;
begin
  SuppressibleMsgBox(RollbackText, mbInformation, MB_OK, IDOK);
end;

{ Done (3.7.0): nothing to undo any more; the saved copies go. }
procedure Commit;
begin
  if Committed or RolledBack then
    Exit;
  Committed := True;
  if JournalStarted then
    DelTree(RollbackDir, True, True, True);
end;

procedure ShowUndoButton(Visible: Boolean);
begin
  UndoButton.Visible := Visible and not RolledBack;
  UndoButton.Enabled := UndoButton.Visible;
  if UndoButton.Visible then
  begin
    WizardForm.CancelButton.Visible := False;
    UndoButton.BringToFront;
  end;
end;

{ Ends a step that's running: its cmd.exe (found by the step's own "done" file in its command
  line) and everything it started. }
procedure StopStep(const Done: String);
var
  Code: Integer;
  Quoted: String;
begin
  Quoted := Done;
  StringChangeEx(Quoted, '''', '''''', True);
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | ' +
    'Where-Object { ($_.Name -eq ''cmd.exe'') -and $_.CommandLine -and $_.CommandLine.Contains(''' + Quoted + ''') } | ' +
    'ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F }"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ Setup's Cancel button while its own steps run and on the pages after installing (3.7.0):
  asks, then stops the running step (RunWithProgress undoes everything when it sees
  CancelRequested) or, between steps, undoes everything and ends Setup. }
procedure UndoClick(Sender: TObject);
begin
  if RolledBack or CancelRequested then
    Exit;
  if not ConfirmCancel then
    Exit;
  if StepRunning then
    CancelRequested := True
  else
  begin
    Rollback;
    ReportRollback;
    WizardForm.Close;
  end;
end;

{ Runs a program in the background -- its output appended to the install log -- while
  WorkPage (already shown) says Msg and keeps the window alive, with the progress file's text
  (or the log's last line) under it. Exec with "wait" froze the window until the program ended.
  Returns the exit code, or -1 if it couldn't be started; -2 when Cancel stopped it (3.7.0:
  then everything is undone, RolledBack). }
function RunWithProgress(const Msg, Exe, Params, Env, Progress: String): Integer;
var
  Done, Line, Detail: String;
  S: AnsiString;
  Code, Ticks: Integer;
begin
  Result := -1;
  LastExitCode := -1;
  { A new name per step (3.7.0): a previous step's file that couldn't be deleted yet (an
    antivirus still reading it) ended the wait at once, before the program had even run. }
  StepCount := StepCount + 1;
  Done := ExpandConstant('{tmp}\step-done-' + IntToStr(StepCount) + '.txt');
  DeleteFile(Done);
  if Progress <> '' then
    DeleteFile(Progress);
  ForceDirectories(ExtractFileDir(LogFile));
  SaveStringToFile(LogFile, #13#10 + '> ' + Exe + ' ' + Params + #13#10, True);
  { cmd /v:on: !errorlevel! is read after the program ends, and written to Done. }
  Line := '/v:on /c "';
  if Env <> '' then
    Line := Line + Env + '&& ';
  Line := Line + '"' + Exe + '" ' + Params + ' >> "' + LogFile + '" 2>&1 & echo !errorlevel!> "' + Done + '""';
  WorkPage.SetText(Msg, '');
  if not Exec(ExpandConstant('{cmd}'), Line, AppDir, SW_HIDE, ewNoWait, Code) then
    Exit;
  StepRunning := True;
  Ticks := 0;
  while not FileExists(Done) and not CancelRequested do
  begin
    WorkPage.Animate;
    Sleep(100);
    Ticks := Ticks + 1;
    if Ticks mod 5 = 0 then
    begin
      Detail := '';
      if (Progress <> '') and LoadStringFromFile(Progress, S) then
        Detail := Trim(String(S));
      if Detail = '' then
        Detail := LastLine(LogFile);
      WorkPage.SetText(Msg, Detail);
    end;
  end;
  StepRunning := False;
  if CancelRequested then
  begin
    WorkPage.SetText('Cancelling: undoing what Setup did...', '');
    if not FileExists(Done) then
      StopStep(Done);
    Rollback;
    Result := -2;
    LastExitCode := -2;
    Exit;
  end;
  Sleep(200);   { let cmd finish writing it }
  if LoadStringFromFile(Done, S) then
    Result := StrToIntDef(Trim(String(S)), -1);
  LastExitCode := Result;
end;

function RunLogged(const Msg, Exe, Params, Env: String): Boolean;
begin
  Result := RunWithProgress(Msg, Exe, Params, Env, '') = 0;
end;

{ Tools/setup_core.py. WithProgress: it reports e.g. "Unpacking 1,200 of 3,400 files" as it goes. }
function SetupCore(const Msg, Args: String; WithProgress: Boolean): Boolean;
begin
  if WithProgress then
    Result := RunWithProgress(Msg, VenvPython, '-m Tools.setup_core ' + Args + ' --progress "' + ProgressFile + '"', '', ProgressFile) = 0
  else
    Result := RunWithProgress(Msg, VenvPython, '-m Tools.setup_core ' + Args, '', '') = 0;
end;

{ When the install fails (3.7.0): the details go to C:\Logs (made when it isn't there), where
  they're easy to find and stay after Setup ends -- why it failed, the install log (every
  program Setup ran and what it printed) and Setup's own log. Returns the file written, or
  '' when C:\Logs can't be written to (then the install log is the place to look). }
function SaveFailureLog(const Reason: String): String;
var
  Dest, Copied, SetupLog: String;
  S: AnsiString;
begin
  Result := '';
  if not ForceDirectories(FailureLogDir) then
  begin
    Log('Could not create ' + FailureLogDir);
    Exit;
  end;
  Dest := FailureLogDir + '\PMVNexus-install-' + GetDateTimeString('yyyymmdd-hhnnss', #0, #0) + '.log';
  SetupLog := ExpandConstant('{log}');
  if not SaveStringToFile(Dest, 'PMV Nexus {#AppVersion} Setup failed, ' + GetDateTimeString('yyyy/mm/dd hh:nn:ss', '-', ':') + #13#10 +
      'Install folder: ' + AppDir + #13#10 + 'Python: ' + PythonExe + #13#10#13#10 +
      'What failed:' + #13#10 + Reason + #13#10#13#10 +
      '===== Install log (' + LogFile + ') =====' + #13#10, False) then
  begin
    Log('Could not write ' + Dest);
    Exit;
  end;
  if LoadStringFromFile(LogFile, S) then
    SaveStringToFile(Dest, S, True)
  else
    SaveStringToFile(Dest, '(none yet: it fails before the files were copied)' + #13#10, True);
  if SetupLog <> '' then
  begin
    { Setup still has its own log open: read a copy of it }
    Copied := ExpandConstant('{tmp}\setup-log-copy.txt');
    SaveStringToFile(Dest, #13#10 + '===== Setup''s own log (' + SetupLog + ') =====' + #13#10, True);
    if FileCopy(SetupLog, Copied, False) and LoadStringFromFile(Copied, S) then
      SaveStringToFile(Dest, S, True)
    else
      SaveStringToFile(Dest, '(could not be copied: see the file above)' + #13#10, True);
  end;
  Result := Dest;
end;

{ Shown when a failure looks like an antivirus's doing (3.7.0, LooksBlocked). }
function AvNote: String;
begin
  Result := 'An antivirus may have blocked or removed part of PMV Nexus (it isn''t code-signed). ' +
    'Check the antivirus''s quarantine / protection history, allow the PMV Nexus folder there, then run Setup again.';
end;

{ The end of a text file (the install log), at most MaxLen characters. }
function LogTail(const FileName: String; MaxLen: Integer): String;
var
  S: AnsiString;
begin
  Result := '';
  if not LoadStringFromFile(FileName, S) then
    Exit;
  Result := String(S);
  if Length(Result) > MaxLen then
    Result := Copy(Result, Length(Result) - MaxLen + 1, MaxLen);
end;

{ Does a failure after the files were copied look like an antivirus's doing (3.7.0)? A program
  of ours gone, a step that couldn't start, or the usual messages of a blocked file. The app
  isn't code-signed, so heuristic scanners may take PMVNexus.exe or Python's files. }
function LooksBlocked: Boolean;
var
  Tail: String;
begin
  Result := (LastExitCode = -1) or (LastExitCode = 225) or (LastExitCode = 9009) or
    not FileExists(AppDir + '\PMVNexus.exe') or (DirExists(AppDir + '\.venv') and not FileExists(VenvPython));
  if Result then
    Exit;
  Tail := Lowercase(LogTail(LogFile, 3000));
  Result := (Pos('access is denied', Tail) > 0) or (Pos('[winerror 5]', Tail) > 0) or
    (Pos('permission denied', Tail) > 0) or (Pos('virus', Tail) > 0) or
    (Pos('being used by another process', Tail) > 0) or
    (Pos('is not recognized as an internal or external command', Tail) > 0);
end;

{ The message's last lines: the antivirus hint when the failure looks like one (only checked
  when AfterFiles -- before that, our files aren't there to check), and where the details are. }
function FailureLogNote(const Reason: String; AfterFiles: Boolean): String;
var
  Saved: String;
  Blocked: Boolean;
begin
  Blocked := AfterFiles and LooksBlocked;
  if Blocked then
    Saved := SaveFailureLog(Reason + #13#10#13#10 + 'Looks blocked: ' + AvNote)
  else
    Saved := SaveFailureLog(Reason);
  Result := '';
  if Blocked then
    Result := AvNote + #13#10#13#10;
  if Saved <> '' then
    Result := Result + 'The details are in ' + Saved
  else
    Result := Result + 'The details are in ' + LogFile;
end;

{ Why a setup_core step failed: its [result] error -- or, when it stopped without writing
  one (Python itself failed), its exit code and the install log's last line (3.7.0), with
  the details saved to C:\Logs. }
function CoreError(const Default: String): String;
begin
  if RolledBack then
  begin
    Result := '';           { cancelled: not an error }
    Exit;
  end;
  Result := GetIniString('result', 'error', '', IniFile);
  if Result <> '' then
    Exit;
  if GetIniString('result', 'ok', '', IniFile) <> '' then
    Result := Default
  else
  begin
    Result := 'Setup''s helper stopped without an answer (exit code ' + IntToStr(LastExitCode) + '). ' +
      'The last line of its log:' + #13#10 + LastLine(LogFile);
    Result := Result + #13#10#13#10 + FailureLogNote(Result, True);
  end;
end;

{ Quick service commands where no wizard page can be shown (before files / uninstall). }
procedure ServiceCommand(const Cmd: String);
var
  Code: Integer;
begin
  if FileExists(ExpandConstant('{#ServiceExe}')) then
    Exec(ExpandConstant('{#ServiceExe}'), Cmd, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure FirewallRule(const Port: String);
var
  Code: Integer;
begin
  { Only possible as admin; otherwise Windows asks the first time the app starts. }
  if not IsAdminInstallMode then Exit;
  Exec('netsh', 'advfirewall firewall delete rule name="PMV Nexus"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('netsh', 'advfirewall firewall add rule name="PMV Nexus" dir=in action=allow protocol=TCP localport=' + Port +
       ' profile=private,domain', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ ---------------------------------------------------------------- pages }

{ "Choose..." on the browser page: picks a program and fills in its path in quotes, then the url placeholder. }
procedure BrowseBrowserClick(Sender: TObject);
var
  FileName: String;
begin
  FileName := '';
  if GetOpenFileName('Choose the browser', FileName, ExpandConstant('{commonpf}'), 'Programs (*.exe)|*.exe', 'exe') then
    BrowserPage.Values[0] := '"' + FileName + '" {url}';
end;

{ The browser page's own controls, under its text box. }
procedure CreateBrowserControls;
var
  Button: TNewButton;
  Note: TNewStaticText;
begin
  Button := TNewButton.Create(BrowserPage);
  Button.Parent := BrowserPage.Surface;
  Button.Caption := 'Choose...';
  Button.Width := ScaleX(90);
  Button.Height := ScaleY(23);
  Button.Left := BrowserPage.SurfaceWidth - Button.Width;
  Button.Top := BrowserPage.Edits[0].Top + BrowserPage.Edits[0].Height + ScaleY(6);
  Button.OnClick := @BrowseBrowserClick;

  OpenOnStartCheck := TNewCheckBox.Create(BrowserPage);
  OpenOnStartCheck.Parent := BrowserPage.Surface;
  OpenOnStartCheck.Caption := 'Open the app in the browser when PMV Nexus starts';
  OpenOnStartCheck.Left := 0;
  OpenOnStartCheck.Top := Button.Top + Button.Height + ScaleY(12);
  OpenOnStartCheck.Width := BrowserPage.SurfaceWidth;
  OpenOnStartCheck.Height := ScaleY(17);
  OpenOnStartCheck.Checked := True;

  Note := TNewStaticText.Create(BrowserPage);
  Note.Parent := BrowserPage.Surface;
  Note.Left := ScaleX(18);
  Note.Top := OpenOnStartCheck.Top + OpenOnStartCheck.Height + ScaleY(4);
  Note.Width := BrowserPage.SurfaceWidth - ScaleX(18);
  Note.WordWrap := True;
  Note.Caption := 'Off: the shortcut only starts it (its icon appears by the clock; click it to open the app). ' +
    'Both can be changed later in the app: Server settings - Opening the app.';
end;

{ The program part of a browser command line (quoted or up to the first space). }
function BrowserProgram(const Command: String): String;
var
  S: String;
  P: Integer;
begin
  S := Trim(Command);
  if (Length(S) > 0) and (S[1] = '"') then
  begin
    Delete(S, 1, 1);
    P := Pos('"', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end else begin
    P := Pos(' ', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end;
  Result := S;
end;

procedure InitializeWizard;
begin
  FolderKeys[0] := 'output_dir'; FolderKeys[1] := 'cache_dir';
  FolderKeys[2] := 'session_dir'; FolderKeys[3] := 'library_db_file';
  FolderKeys[4] := 'dir'; FolderKeys[5] := 'log_dir';

  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
  WorkPage := CreateOutputMarqueeProgressPage('Please wait', 'This can take a while. The details go to data\logs\install.log (and to C:\Logs if something fails).');

  { Cancel for Setup's own steps and the pages after installing (3.7.0): where Setup's own
    Cancel button is gone (after the files are copied), in the same place. }
  UndoButton := TNewButton.Create(WizardForm);
  UndoButton.Parent := WizardForm;
  UndoButton.Left := WizardForm.CancelButton.Left;
  UndoButton.Top := WizardForm.CancelButton.Top;
  UndoButton.Width := WizardForm.CancelButton.Width;
  UndoButton.Height := WizardForm.CancelButton.Height;
  UndoButton.Anchors := WizardForm.CancelButton.Anchors;
  UndoButton.Caption := SetupMessage(msgButtonCancel);
  UndoButton.OnClick := @UndoClick;
  UndoButton.Visible := False;

  { These come after the installation (wpInfoAfter), on a first install only. }
  ModePage := CreateInputOptionPage(wpInfoAfter, 'Your library',
    'Start with an empty library, or bring one back from a backup?',
    'A backup is a .zip from the Backups page of an earlier install (this computer or another one). ' +
    'A backup split into parts: choose its .zip.001 file, with the other parts next to it.', True, False);
  ModePage.Add('Set up a new, empty library');
  ModePage.Add('Set up from a backup');
  ModePage.SelectedValueIndex := 0;

  BackupFilePage := CreateInputFilePage(ModePage.ID, 'Backup',
    'Which backup?', 'The library, users, playlists and settings come back from it. Video files are never in a backup: copy your video folder to this computer yourself.');
  BackupFilePage.Add('Backup file:', 'Backups (*.zip, *.zip.001)|*.zip;*.zip.001', '.zip');

  BackupPwPage := CreateInputQueryPage(BackupFilePage.ID, 'Backup password',
    'This backup is protected with a password', 'Enter the backup password (set on the Backups page of the install it came from). It''s checked before anything is unpacked.');
  BackupPwPage.Add('Password:', True);

  FoldersPage := CreateInputDirPage(BackupPwPage.ID, 'Where your library lives',
    'Folders for the videos and their thumbnails & previews',
    'From a backup: where you copied the videos to on this computer. Folders that don''t exist yet are created.', False, '');
  FoldersPage.Add('Videos:');
  FoldersPage.Add('Thumbnails && previews (cache):');

  MoreFoldersPage := CreateInputDirPage(FoldersPage.ID, 'Other folders',
    'Some folders from the backup''s settings can''t be reached on this computer',
    'Choose where they should be. The defaults inside the app folder are fine.', False, '');
  MoreFoldersPage.Add('Site logins:');
  MoreFoldersPage.Add('Library database:');
  MoreFoldersPage.Add('Backups:');
  MoreFoldersPage.Add('Logs:');

  AdminPage := CreateInputQueryPage(MoreFoldersPage.ID, 'Your admin account',
    'The account you sign in to PMV Nexus with',
    'It can manage everything (users, settings, backups). 8 to 64 characters, at least one letter and one number, not containing the username.');
  AdminPage.Add('Username:', False);
  AdminPage.Add('Password:', True);
  AdminPage.Add('Password again:', True);
  AdminPage.Values[0] := 'admin';

  BrowserPage := CreateInputQueryPage(AdminPage.ID, 'Opening the app',
    'Which browser should PMV Nexus open in?',
    'Leave it empty for your default browser. Or a browser with its options - {url} is the app''s address, e.g.' + #13#10 +
    '"C:\Program Files\Mozilla Firefox\firefox.exe" -P Videos --new-window {url}' + #13#10 +
    '"C:\Program Files\Google\Chrome\Application\chrome.exe" --profile-directory="Profile 1" {url}');
  BrowserPage.Add('Browser (optional):', False);
  CreateBrowserControls;

  PortPage := CreateInputQueryPage(BrowserPage.ID, 'Port',
    'Which port should the app use?', 'The app opens at http://localhost:<port> on this computer, and at http://<this computer''s name or address>:<port> from other devices. 8676 unless something else uses it.');
  PortPage.Add('Port:', False);
end;

{ Fills the folder pages from the INI that setup_core inspect wrote. }
procedure FillFolders;
var
  I: Integer;
  Prompt: String;
begin
  MoreFoldersNeeded := False;
  for I := 0 to 5 do
  begin
    Prompt := GetIniString('folders', FolderKeys[I] + '_label', '', IniFile) + ': ' +
              GetIniString('folders', FolderKeys[I] + '_status', '', IniFile);
    StringChangeEx(Prompt, '&', '&&', True);   { a single & would underline the next letter }
    if I < FolderKeysMain then
    begin
      FoldersPage.Values[I] := GetIniString('folders', FolderKeys[I], '', IniFile);
      FoldersPage.PromptLabels[I].Caption := Prompt;
    end else begin
      MoreFoldersPage.Values[I - FolderKeysMain] := GetIniString('folders', FolderKeys[I], '', IniFile);
      MoreFoldersPage.PromptLabels[I - FolderKeysMain].Caption := Prompt;
      if GetIniString('folders', FolderKeys[I] + '_ok', '1', IniFile) <> '1' then
        MoreFoldersNeeded := True;
    end;
  end;
  PortPage.Values[0] := GetIniString('server', 'port', '8676', IniFile);
  BrowserPage.Values[0] := GetIniString('tray', 'browser', '', IniFile);
  OpenOnStartCheck.Checked := GetIniString('tray', 'open_browser', '1', IniFile) <> '0';
end;

{ setup_core inspect (WorkPage must be shown): this install's folders, or -- with Zip -- the
  backup's, after checking it (VerifyOnly: only that, quickly) and unpacking it.
  True when it worked; the details are in IniFile. }
function Inspect(const Msg, Zip, Password: String; VerifyOnly: Boolean): Boolean;
var
  Args, PwFile: String;
begin
  DeleteFile(IniFile);
  Args := 'inspect --ini "' + IniFile + '"';
  if Zip <> '' then
    Args := Args + ' --zip "' + Zip + '"';
  if VerifyOnly then
    Args := Args + ' --verify-only';
  if Password <> '' then
  begin
    PwFile := ExpandConstant('{tmp}\backup-pw.txt');   { read and deleted by setup_core }
    SaveStringToFile(PwFile, Password, False);
    Args := Args + ' --password-file "' + PwFile + '"';
  end;
  SetupCore(Msg, Args, not VerifyOnly);
  Result := GetIniString('result', 'ok', '0', IniFile) = '1';
  if Result and not VerifyOnly then
    FillFolders;
end;

{ Checks the backup (and its password) first -- quick -- then unpacks it, showing each step. }
function OpenBackup(const Password: String): Boolean;
var
  Version, Accepted: String;
begin
  WorkPage.Show;
  try
    if Password = '' then
      Result := Inspect('Reading the backup...', BackupFilePage.Values[0], '', True)
    else
      Result := Inspect('Checking the password...', BackupFilePage.Values[0], Password, True);
    if not Result then
      Exit;
    Version := GetIniString('result', 'app_version', '?', IniFile);
    Accepted := '';
    if Password <> '' then
      Accepted := 'Password accepted. ';
    Result := Inspect(Accepted + 'Unpacking the backup...', BackupFilePage.Values[0], Password, False);
    if Result then
      BackupInfo := Accepted + 'Backup opened (made by version ' + Version + '). Choose where its folders are on this computer.';
  finally
    WorkPage.Hide;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { the pages after installing (a new install): Cancel undoes it (3.7.0) }
  ShowUndoButton(not IsUpgrade and ((CurPageID = wpInfoAfter) or (CurPageID = ModePage.ID) or
    (CurPageID = BackupFilePage.ID) or (CurPageID = BackupPwPage.ID) or (CurPageID = FoldersPage.ID) or
    (CurPageID = MoreFoldersPage.ID) or (CurPageID = AdminPage.ID) or (CurPageID = BrowserPage.ID) or
    (CurPageID = PortPage.ID)));
  if CurPageID = wpFinished then
  begin
    if RolledBack then
    begin
      WizardForm.FinishedHeadingLabel.Caption := 'Setup was cancelled';
      WizardForm.FinishedLabel.Caption := RollbackText;
    end
    else
      Commit;
  end;
  if CurPageID = FoldersPage.ID then
  begin
    if BackupInfo <> '' then
      FoldersPage.Description := BackupInfo
    else
      FoldersPage.Description := 'Folders for the videos and their thumbnails & previews';
  end;
end;

{ Setup's own Cancel and the window's close button (3.7.0): once Setup has changed something,
  the question says what Cancel undoes. While files are copied Setup removes its own files
  itself; DeinitializeSetup undoes the rest. Elsewhere the whole run is undone here. }
procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if RolledBack then
  begin
    Cancel := True;
    Confirm := False;
    Exit;
  end;
  if not JournalStarted then
    Exit;                     { nothing changed yet: Setup's usual question }
  Confirm := False;
  if StepRunning then
  begin
    Cancel := False;
    UndoClick(nil);
    Exit;
  end;
  Cancel := ConfirmCancel;
  if Cancel and (CurPageID <> wpInstalling) and (CurPageID <> wpPreparing) then
    Rollback;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = ModePage.ID) or (PageID = BackupFilePage.ID) or (PageID = BackupPwPage.ID) or
     (PageID = FoldersPage.ID) or (PageID = MoreFoldersPage.ID) or (PageID = AdminPage.ID) or (PageID = BrowserPage.ID) or
     (PageID = PortPage.ID) then
  begin
    if IsUpgrade or SetupFailed then
      Result := True
    else if (PageID = BackupFilePage.ID) then
      Result := ModePage.SelectedValueIndex <> 1
    else if (PageID = BackupPwPage.ID) then
      Result := (ModePage.SelectedValueIndex <> 1) or not NeedPassword
    else if (PageID = MoreFoldersPage.ID) then
      Result := not MoreFoldersNeeded
    else if (PageID = AdminPage.ID) then
      Result := ModePage.SelectedValueIndex = 1;   { a backup brings its own accounts }
  end;
end;

{ An error on a page -- not after Cancel undid everything (3.7.0): that says so itself. }
procedure ErrorBox(const Text: String);
begin
  if not RolledBack then
    MsgBox(Text, mbError, MB_OK);
end;

{ setup_core admin: checks (CheckOnly) or creates the admin account. True when it worked. }
function AdminAccount(const Msg: String; CheckOnly: Boolean): Boolean;
var
  Args, PwFile: String;
begin
  DeleteFile(IniFile);
  SetIniString('admin', 'username', Trim(AdminPage.Values[0]), IniFile);
  PwFile := ExpandConstant('{tmp}\admin-pw.txt');   { read and deleted by setup_core }
  SaveStringToFile(PwFile, AdminPassword, False);
  Args := 'admin --ini "' + IniFile + '" --password-file "' + PwFile + '"';
  if CheckOnly then
    Args := Args + ' --check-only';
  SetupCore(Msg, Args, False);
  Result := GetIniString('result', 'ok', '0', IniFile) = '1';
end;

function ApplySettings: Boolean;
var
  I: Integer;
  Port, Problem: String;
begin
  DeleteFile(IniFile);
  for I := 0 to 5 do
    if I < FolderKeysMain then
      SetIniString('folders', FolderKeys[I], FoldersPage.Values[I], IniFile)
    else
      SetIniString('folders', FolderKeys[I], MoreFoldersPage.Values[I - FolderKeysMain], IniFile);
  Port := Trim(PortPage.Values[0]);
  SetIniString('server', 'port', Port, IniFile);
  SetIniString('tray', 'browser', Trim(BrowserPage.Values[0]), IniFile);
  if OpenOnStartCheck.Checked then
    SetIniString('tray', 'open_browser', '1', IniFile)
  else
    SetIniString('tray', 'open_browser', '0', IniFile);
  { the folders that don't exist yet are created: Cancel deletes them again (3.7.0) }
  for I := 0 to 5 do
    if I < FolderKeysMain then
      NoteNewFolder(FoldersPage.Values[I])
    else
      NoteNewFolder(MoreFoldersPage.Values[I - FolderKeysMain]);
  WorkPage.Show;
  try
    if ModePage.SelectedValueIndex = 1 then
      SetupCore('Restoring the backup and saving the settings...', 'apply --ini "' + IniFile + '"', True)
    else
      SetupCore('Saving the settings...', 'apply --ini "' + IniFile + '"', True);
    Result := GetIniString('result', 'ok', '0', IniFile) = '1';
    if Result and (ModePage.SelectedValueIndex = 0) then
    begin
      Result := AdminAccount('Creating your admin account...', False);
      if not Result then
        SetIniString('result', 'error', 'The admin account couldn''t be created: ' +
          CoreError('unknown error'), IniFile);
    end;
    if Result and WizardIsTaskSelected('service') then
    begin
      JServiceInstalled := True;
      RunLogged('Installing the Windows service...', ExpandConstant('{#ServiceExe}'), 'install', '');
      RunLogged('Starting the Windows service...', ExpandConstant('{#ServiceExe}'), 'start', '');
    end;
  finally
    WorkPage.Hide;
  end;
  if not Result then
  begin
    Problem := CoreError('Unknown error');
    StringChangeEx(Problem, ' | ', #13#10, True);   { setup_core puts one problem per " | " }
    ErrorBox('These settings can''t be used yet:' + #13#10#13#10 + Problem + #13#10#13#10 +
      'Go Back to change a folder, or change the port.');
    Exit;
  end;
  JFirewall := IsAdminInstallMode;
  FirewallRule(Port);
end;

function PageNext(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    { Downloads before anything is installed, so a failed download changes nothing. }
    NeedPython := not FindPython and not (FileExists(VenvPython) and VenvVersionOk);
    DownloadPage.Clear;
    if NeedPython then
    begin
      if MsgBox('Python 3.12 to 3.14 wasn''t found on this computer. PMV Nexus needs one of them.' + #13#10#13#10 +
        'Setup will now download Python {#PythonVersion} from python.org and install it for your user account ' +
        '(it shows its own progress window), then continue.', mbInformation, MB_OKCANCEL) <> IDOK then
      begin
        Result := False;
        Exit;
      end;
      DownloadPage.Add('{#PythonUrl}', 'python-installer.exe', '{#PythonSha256}');
    end;
    if WizardIsTaskSelected('ffmpeg') then
      DownloadPage.Add('{#FFmpegUrl}', 'ffmpeg.zip', '');
    if NeedPython or WizardIsTaskSelected('ffmpeg') then
    begin
      DownloadPage.Show;
      try
        try
          DownloadPage.Download;
        except
          if not DownloadPage.AbortedByUser then
            SuppressibleMsgBox('Download failed: ' + AddPeriod(GetExceptionMessage) + #13#10 +
              'Check the internet connection and try again.' + #13#10#13#10 +
              FailureLogNote('Download failed: ' + GetExceptionMessage, False), mbCriticalError, MB_OK, IDOK);
          Result := False;
        end;
      finally
        DownloadPage.Hide;
      end;
    end;
  end
  else if CurPageID = ModePage.ID then
  begin
    if ModePage.SelectedValueIndex = 0 then
    begin
      BackupInfo := '';
      WorkPage.Show;
      try
        SetupCore('Checking the folders...', 'cancel-restore', False);   { in case a backup was chosen first, then New }
        Result := Inspect('Checking the folders...', '', '', False);
      finally
        WorkPage.Hide;
      end;
      if not Result then
        ErrorBox('The app''s settings can''t be read:' + #13#10#13#10 + CoreError('Unknown error'));
    end;
  end
  else if CurPageID = BackupFilePage.ID then
  begin
    NeedPassword := False;
    if not FileExists(BackupFilePage.Values[0]) then
    begin
      MsgBox('Choose the backup .zip file.', mbError, MB_OK);
      Result := False;
    end
    else if not OpenBackup('') then
    begin
      if GetIniString('result', 'password_needed', '0', IniFile) = '1' then
        NeedPassword := True
      else begin
        ErrorBox('This backup can''t be used:' + #13#10#13#10 + CoreError('Unknown error'));
        Result := False;
      end;
    end;
  end
  else if CurPageID = BackupPwPage.ID then
  begin
    if Trim(BackupPwPage.Values[0]) = '' then
    begin
      MsgBox('Enter the backup''s password.', mbError, MB_OK);
      Result := False;
    end
    else if not OpenBackup(BackupPwPage.Values[0]) then
    begin
      if GetIniString('result', 'wrong_password', '0', IniFile) = '1' then
        ErrorBox('Wrong password - try again.' + #13#10#13#10 +
          'It''s the password set on the Backups page of the install the backup came from (letters are case-sensitive).')
      else
        ErrorBox('This backup can''t be used:' + #13#10#13#10 + CoreError('Unknown error'));
      Result := False;
    end;
    BackupPwPage.Values[0] := '';
  end
  else if CurPageID = AdminPage.ID then
  begin
    if AdminPage.Values[1] <> AdminPage.Values[2] then
    begin
      MsgBox('The two passwords are different - type them again.', mbError, MB_OK);
      Result := False;
    end else begin
      AdminPassword := AdminPage.Values[1];
      WorkPage.Show;
      try
        Result := AdminAccount('Checking the account...', True);
      finally
        WorkPage.Hide;
      end;
      if not Result then
        ErrorBox(CoreError('This account can''t be used.'));
    end;
  end
  else if CurPageID = BrowserPage.ID then
  begin
    if (Trim(BrowserPage.Values[0]) <> '') and not FileExists(BrowserProgram(BrowserPage.Values[0])) then
      Result := MsgBox('This program wasn''t found:' + #13#10 + BrowserProgram(BrowserPage.Values[0]) + #13#10#13#10 +
        'Put the program in quotes when its path has spaces, e.g. "C:\Program Files\...\firefox.exe" {url}.' + #13#10 +
        'Use it anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  end
  else if CurPageID = PortPage.ID then
    Result := ApplySettings;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := PageNext(CurPageID);
  if RolledBack and (CurPageID <> wpInstalling) then
  begin                         { Cancel during one of the page's steps: everything was undone }
    Result := False;
    ReportRollback;
    WizardForm.Close;
  end;
end;

{ An update while the app runs (tray, console window, or anything else from its folder):
  asks, then closes it (Tools\stop_app.py: politely first, then ends what's left).
  Returns '' when nothing runs any more, otherwise why Setup can't go on. }
function CloseRunningApp: String;
var
  Code: Integer;
  Script, Args, ReportFile: String;
  Report: AnsiString;
begin
  Result := '';
  if not FileExists(VenvPython) then
    Exit;                      { a first install: nothing can be running }
  ExtractTemporaryFile('stop_app.py');
  Script := ExpandConstant('{tmp}\stop_app.py');
  ReportFile := ExpandConstant('{tmp}\stop_app_report.txt');
  Args := '"' + Script + '" --root "' + AppDir + '" --report "' + ReportFile + '"';
  if not Exec(VenvPython, Args + ' --check', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Exit;                      { the old Python doesn't run: then nothing of it runs either }
  if Code <> 3 then
    Exit;
  if SuppressibleMsgBox('PMV Nexus is running. Setup will close it now to update it.' + #13#10#13#10 +
      'Work in progress (downloads, re-encodes, scans) stops safely and continues when the app starts again. ' +
      'Setup starts it again when the update is done.', mbConfirmation, MB_OKCANCEL, IDOK) <> IDOK then
  begin
    Result := 'PMV Nexus is still running. Close it (tray icon: Exit PMV Nexus) and run Setup again.';
    Exit;
  end;
  AppWasRunning := True;
  WizardForm.PreparingLabel.Caption := 'Closing PMV Nexus...';
  if not Exec(VenvPython, Args, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
  begin
    Result := 'PMV Nexus couldn''t be closed. Close it yourself (tray icon: Exit PMV Nexus, or end it in Task Manager) ' +
      'and click Back, then Install again.';
    if LoadStringFromFile(ReportFile, Report) and (Trim(String(Report)) <> '') then
      Result := Result + #13#10#13#10 + 'Still running:' + #13#10 + String(Report);
  end;
end;

{ 3.12.0: what updating this library will change, and about how long it takes (Tools\upgrade_plan.py
  with this version's steps, run with the installed app's Python, read-only). Asked before anything
  is replaced; '' = go on, otherwise why Setup stops. }
function CheckUpgradePlan: String;
var
  Code: Integer;
  Script, Args, ReportFile: String;
  Report: AnsiString;
begin
  Result := '';
  if not FileExists(VenvPython) or not FileExists(AppDir + '\config.yaml') then
    Exit;                      { a first install: nothing to upgrade }
  ExtractTemporaryFile('upgrade_plan.py');
  ExtractTemporaryFile('steps.py');
  Script := ExpandConstant('{tmp}\upgrade_plan.py');
  ReportFile := ExpandConstant('{tmp}\upgrade_plan.txt');
  Args := '"' + Script + '" --root "' + AppDir + '" --to {#AppVersion} --steps "' + ExpandConstant('{tmp}\steps.py') +
    '" --report "' + ReportFile + '"';
  if not Exec(VenvPython, Args, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Exit;                      { the installed Python doesn't run: the update repairs it }
  Report := '';
  LoadStringFromFile(ReportFile, Report);
  if Code = 4 then
  begin
    Result := String(Report) + #13#10#13#10 + 'Nothing was changed.';
    Exit;
  end;
  if (Code = 3) and (SuppressibleMsgBox(String(Report) + #13#10#13#10 +
      'Your library is changed when the app starts after the update. Continue?',
      mbConfirmation, MB_OKCANCEL, IDOK) <> IDOK) then
    Result := 'The update was cancelled. Nothing was changed.';
  if (Code = 1) and (SuppressibleMsgBox('Setup couldn''t check what the update changes in your library:' + #13#10 +
      String(Report) + #13#10#13#10 + 'The app makes a copy of its database before it changes anything. Continue?',
      mbConfirmation, MB_OKCANCEL, IDOK) <> IDOK) then
    Result := 'The update was cancelled. Nothing was changed.';
end;

{ 3.13.0: a backup of the library before anything is replaced (Tools\pre_install_backup.py, run
  with the installed app's Python and code, after the app was closed): the database, the settings,
  own photos and covers, in the backups folder; the app deletes it after the days set in Server
  Settings -> Backups. '' = go on (made, switched off, or a first install), otherwise why Setup stops. }
function PreInstallBackup: String;
var
  Code: Integer;
  Script, Args, ReportFile: String;
  Report: AnsiString;
begin
  Result := '';
  if not FileExists(AppDir + '\config.yaml') then
    Exit;                      { a first install: nothing to back up }
  Report := '';
  ExtractTemporaryFile('pre_install_backup.py');
  Script := ExpandConstant('{tmp}\pre_install_backup.py');
  ReportFile := ExpandConstant('{tmp}\pre_install_backup.txt');
  Args := '"' + Script + '" --root "' + AppDir + '" --to {#AppVersion} --report "' + ReportFile + '"';
  WizardForm.PreparingLabel.Caption := 'Making a backup of your library first (database and settings)...';
  ForceDirectories(ExtractFileDir(LogFile));
  if FileExists(VenvPython) and Exec(VenvPython, Args, AppDir, SW_HIDE, ewWaitUntilTerminated, Code) then
  begin
    LoadStringFromFile(ReportFile, Report);
    SaveStringToFile(LogFile, #13#10 + '> backup before installing (code ' + IntToStr(Code) + '): ' + String(Report) + #13#10, True);
    if (Code = 0) or (Code = 2) then
      Exit;                    { made (or switched off in Server Settings -> Backups) }
  end
  else
    Report := 'the installed app''s Python didn''t start';
  if SuppressibleMsgBox('Setup couldn''t make a backup of your library before installing:' + #13#10 + String(Report) + #13#10#13#10 +
      'Install anyway, without that backup?', mbError, MB_YESNO or MB_DEFBUTTON2, IDNO) <> IDYES then
    Result := 'The update was cancelled because no backup could be made first. Nothing was changed. ' +
      'Check the free space where backups go (Management -> Backups), then run Setup again.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Params: String;
begin
  Result := '';
  IsUpgrade := FileExists(AppDir + '\config.yaml');
  { 3.12.0: what the update changes in the library -- asked before anything changes. }
  Result := CheckUpgradePlan;
  if Result <> '' then
    Exit;
  { What's there before anything changes, so Cancel can undo this run (3.7.0). }
  AppDirExisted := DirExists(AppDir);
  PreExisting := '|';
  PreExistingOk := not AppDirExisted;
  if AppDirExisted then
    PreExisting := TopEntries(AppDir, PreExistingOk);
  ServiceDirExisted := DirExists(AppDir + '\runtime\service');
  JCodeSaved := False;
  JServiceSaved := False;
  if DirExists(AppDir + '\backend') then
  begin                       { an installed version: a copy of its code first (a few seconds) }
    WizardForm.PreparingLabel.Caption := 'Saving a copy of the installed version, to put it back if the update is cancelled...';
    DelTree(RollbackDir, True, True, True);   { left over from a Setup that was ended }
    if not SaveInstalledCode then
    begin
      DelTree(RollbackDir, True, True, True);
      Result := 'Setup couldn''t save a copy of the installed version first, so the update couldn''t be undone if it ' +
        'went wrong. Nothing was changed. Check there''s free space on the drive, then run Setup again.';
      Exit;
    end;
    JCodeSaved := True;
  end;
  JournalStarted := True;
  ServiceWasThere := FileExists(ExpandConstant('{#ServiceExe}'));
  if ServiceWasThere then
  begin
    ServiceCommand('stop');   { an update: files in use otherwise }
    JServiceStopped := True;
  end;
  Result := CloseRunningApp;
  if Result = '' then
    Result := PreInstallBackup;   { 3.13.0: with the app closed, before any file is replaced }
  if Result <> '' then
  begin
    if ServiceWasThere then
      ServiceCommand('start');  { not updating after all: leave it as it was }
    JServiceStopped := False;
    Exit;
  end;
  if ServiceWasThere and not WizardIsTaskSelected('service') then
  begin                       { switched away from running as a service }
    ServiceCommand('uninstall');
    DeleteFile(ExpandConstant('{#ServiceExe}'));
    ServiceWasThere := False;
    JServiceRemoved := True;  { Cancel installs it again from .rollback\service }
  end;
  if NeedPython then
  begin
    Params := '/passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0';
    if IsAdminInstallMode then
      Params := '/passive InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0';
    if not Exec(ExpandConstant('{tmp}\python-installer.exe'), Params, '', SW_SHOW, ewWaitUntilTerminated, Code) or (Code <> 0) then
      Result := 'Python couldn''t be installed (code ' + IntToStr(Code) + '). Install Python {#PythonVersion} from python.org, then run Setup again.'
    else
      JPythonInstalled := True;
    if (Result = '') and not FindPython then
      Result := 'Python was installed but Setup can''t find it. Run Setup again.';
    if Result <> '' then
      Result := Result + #13#10#13#10 + FailureLogNote(Result, False);
  end;
end;

{ After a successful install (3.7.0): are our launchers still there? An antivirus may remove
  them as soon as they're written (the app isn't code-signed). Setup says so instead of
  leaving shortcuts that don't work. }
procedure CheckLaunchers;
var
  VenvCopy: String;
begin
  Sleep(1000);   { a real-time scanner takes a moment }
  VenvCopy := AppDir + '\.venv\Scripts\PMVNexus.exe';
  if not FileExists(AppDir + '\PMVNexus.exe') then
    SuppressibleMsgBox('PMVNexus.exe was removed right after it was installed - most likely by an antivirus ' +
      '(PMV Nexus isn''t code-signed). The shortcuts start PMV Nexus through it, so they won''t work until it''s back.' + #13#10#13#10 +
      'Restore it from the antivirus''s quarantine and allow the PMV Nexus folder there, or allow the folder and run Setup again.' + #13#10#13#10 +
      FailureLogNote('PMVNexus.exe was removed after it was installed (antivirus?): ' + AppDir + '\PMVNexus.exe', False),
      mbError, MB_OK, IDOK)
  else if not FileExists(VenvCopy) then
  begin
    Log('No ' + VenvCopy + ' (removed, or it couldn''t be copied): the app runs as pythonw.exe');
    SuppressibleMsgBox('PMV Nexus is installed, but the copy of PMVNexus.exe in its Python folder was removed or couldn''t be ' +
      'written - most likely by an antivirus (PMV Nexus isn''t code-signed).' + #13#10#13#10 +
      'PMV Nexus works anyway; Task Manager shows it as pythonw.exe instead. To change that, allow the PMV Nexus folder ' +
      'in the antivirus and run Setup again.', mbInformation, MB_OK, IDOK);
  end;
end;

{ An update whose requirements.txt differs from the installed version's (3.7.0): its packages
  go into a new .venv, so Cancel can put the old one back as it was. }
function RequirementsChanged: Boolean;
var
  OldReq, NewReq: AnsiString;
begin
  Result := not (LoadStringFromFile(RollbackDir + '\code\requirements.txt', OldReq) and
                 LoadStringFromFile(AppDir + '\requirements.txt', NewReq) and (OldReq = NewReq));
end;

{ After the files are copied: .venv, packages, FFmpeg -- each step shown, each can be
  cancelled (3.7.0: Cancel stops the step and undoes the whole run, see Rollback). }
procedure InstallAfterFiles;
var
  Unzip, Tmp: String;
  Ok: Boolean;
  Code: Integer;
begin
  WorkPage.Show;
  ShowUndoButton(True);
  try
    Ok := True;
    if FileExists(VenvPython) and (PythonExe <> '') and (not VenvVersionOk or (JCodeSaved and RequirementsChanged)) then
    begin                       { made with a Python the app no longer supports, or other packages: a new one }
      Log('Making a new .venv with ' + PythonExe + '; the old one is kept until Setup is done');
      ForceDirectories(RollbackDir);
      if RenameFile(AppDir + '\.venv', RollbackDir + '\venv') then
        JVenvMoved := True
      else if not VenvVersionOk then
      begin
        DelTree(AppDir + '\.venv', True, True, True);
        VenvNoUndo := True;
      end
      else
        VenvNoUndo := True;     { in use: updated in place instead }
    end;
    if not FileExists(VenvPython) then
    begin
      JVenvCreated := True;
      Ok := RunLogged('Creating the Python environment (.venv)...', PythonExe, '-m venv "' + AppDir + '\.venv"', '');
    end;
    if RolledBack then Exit;
    if Ok then
    begin
      RunLogged('Updating pip...', VenvPython, '-m pip install --disable-pip-version-check --upgrade pip', '');
      if RolledBack then Exit;
      Ok := RunLogged('Installing the Python packages (a few minutes the first time)...', VenvPython,
        '-m pip install --disable-pip-version-check -r "' + AppDir + '\requirements.txt"', '');
      if RolledBack then Exit;
      if Ok and DirExists(AppDir + '\extensions') then   { a build with extensions (2.30.0) }
        Ok := RunLogged('Installing the extensions'' packages...', VenvPython, '-m Tools.install_extensions', '');
      if RolledBack then Exit;
      { 3.7.0: the copy that runs Python itself (Task Manager shows PMV Nexus, not pythonw.exe) }
      if Ok and not FileCopy(AppDir + '\PMVNexus.exe', AppDir + '\.venv\Scripts\PMVNexus.exe', False) then
        Log('Could not copy PMVNexus.exe into .venv\Scripts: the app runs as pythonw.exe');
    end;
    if Ok and WizardIsTaskSelected('ffmpeg') then
    begin
      { the FFmpeg that was there is kept aside until Setup is done (Cancel puts it back) }
      if DirExists(AppDir + '\runtime\ffmpeg') then
      begin
        ForceDirectories(RollbackDir);
        DelTree(RollbackDir + '\ffmpeg', True, True, True);
        JFfmpegMoved := RenameFile(AppDir + '\runtime\ffmpeg', RollbackDir + '\ffmpeg');
      end
      else
        JFfmpegCreated := True;
      Tmp := AppDir + '\runtime\ffmpeg-new';
      Unzip := '-NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference=''Stop''; ' +
        'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ''' + Tmp + '''; ' +
        'Expand-Archive -LiteralPath ''' + ExpandConstant('{tmp}\ffmpeg.zip') + ''' -DestinationPath ''' + Tmp + '''; ' +
        '$d=Get-ChildItem ''' + Tmp + ''' -Directory | Select-Object -First 1; ' +
        'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ''' + AppDir + '\runtime\ffmpeg''; ' +
        'Move-Item $d.FullName ''' + AppDir + '\runtime\ffmpeg''; Remove-Item -Recurse -Force ''' + Tmp + '''"';
      if not RunLogged('Unpacking FFmpeg...', 'powershell.exe', Unzip, '') then
      begin
        if RolledBack then Exit;
        if JFfmpegMoved and not DirExists(AppDir + '\runtime\ffmpeg') and
           RenameFile(RollbackDir + '\ffmpeg', AppDir + '\runtime\ffmpeg') then
          JFfmpegMoved := False;   { the old one is back }
        MsgBox('FFmpeg couldn''t be unpacked. Install it yourself (e.g. winget install Gyan.FFmpeg).' + #13#10#13#10 +
          FailureLogNote('FFmpeg couldn''t be unpacked: ' + LastLine(LogFile), True), mbInformation, MB_OK);
      end;
    end;
    if not Ok then
    begin
      SetupFailed := True;
      ShowUndoButton(False);
      if SuppressibleMsgBox('The Python packages couldn''t be installed. ' +
          FailureLogNote('The Python environment or its packages couldn''t be installed: ' + LastLine(LogFile), True) + #13#10#13#10 +
          'Check the internet connection. Undo the installation now?' + #13#10 +
          'Yes: Setup undoes what it did.' + #13#10 +
          'No: it stays as it is; run Setup again later and it continues where it stopped.',
          mbCriticalError, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        WorkPage.SetText('Undoing what Setup did...', '');
        Rollback;
      end;
    end
    else
    begin
      CheckLaunchers;
      if IsUpgrade then
        ShowUndoButton(False);  { an update is done here: starting the app is all that's left }
      if IsUpgrade and ServiceWasThere then
        RunLogged('Starting the Windows service again...', ExpandConstant('{#ServiceExe}'), 'start', '')
      else if AppWasRunning and not WizardIsTaskSelected('service') then
        { closed for the update: back in the tray, as the person who ran Setup (not as admin) }
        ExecAsOriginalUser(AppDir + '\PMVNexus.exe', 'run.py --tray', AppDir,
          SW_HIDE, ewNoWait, Code);
    end;
  finally
    WorkPage.Hide;
    ShowUndoButton(False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallAfterFiles
  else if CurStep = ssDone then
    Commit;
end;

{ Setup ends without finishing (3.7.0): Cancel while files were copied (Setup removes its own
  files itself), Cancel on the Preparing page, or an error -- the rest of this run is undone. }
procedure DeinitializeSetup;
begin
  if JournalStarted and not Committed and not RolledBack then
  begin
    Rollback;
    if not WizardSilent then
      ReportRollback;
  end;
end;

{ Uninstalling while the app runs (2.33.3): asks, then closes it with the installed
  Tools\stop_app.py (the service is stopped first). False = don't uninstall. }
function InitializeUninstall: Boolean;
var
  Code: Integer;
  Script, Args, ReportFile: String;
  Report: AnsiString;
begin
  Result := True;
  Script := AppDir + '\Tools\stop_app.py';
  if not (FileExists(VenvPython) and FileExists(Script)) then
    Exit;                      { an install from before 2.33.0, or a broken one }
  ReportFile := GetTempDir + 'pmvnexus_stop_report.txt';
  DeleteFile(ReportFile);
  Args := '"' + Script + '" --root "' + AppDir + '" --report "' + ReportFile + '"';
  if not Exec(VenvPython, Args + ' --check', GetTempDir, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 3) then
    Exit;
  if SuppressibleMsgBox('PMV Nexus is running. It will be closed so it can be uninstalled.' + #13#10#13#10 +
      'Work in progress (downloads, re-encodes, scans) stops safely.', mbConfirmation, MB_OKCANCEL, IDOK) <> IDOK then
  begin
    Result := False;
    Exit;
  end;
  ServiceCommand('stop');      { a service would start the app again }
  if not Exec(VenvPython, Args, GetTempDir, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
  begin
    Report := '';
    LoadStringFromFile(ReportFile, Report);
    SuppressibleMsgBox('PMV Nexus couldn''t be closed, so it wasn''t uninstalled. Close it yourself (tray icon: Exit ' +
      'PMV Nexus, or end it in Task Manager) and uninstall again.' + #13#10#13#10 + 'Still running:' + #13#10 +
      String(Report), mbError, MB_OK, IDOK);
    Result := False;
  end;
  DeleteFile(ReportFile);
end;

{ Removes every __pycache__ folder under Dir (Python's compiled files, made while the app
  runs, so the uninstaller doesn't know them), e.g. in the extensions folder. }
procedure DeletePyCaches(const Dir: String);
var
  Find: TFindRec;
begin
  if FindFirst(Dir + '\*', Find) then
  try
    repeat
      if (Find.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and (Find.Name <> '.') and (Find.Name <> '..') then
      begin
        if CompareText(Find.Name, '__pycache__') = 0 then
          DelTree(Dir + '\' + Find.Name, True, True, True)
        else if (CompareText(Find.Name, 'data') <> 0) and (CompareText(Find.Name, '.venv') <> 0) then
          DeletePyCaches(Dir + '\' + Find.Name);
      end;
    until not FindNext(Find);
  finally
    FindClose(Find);
  end;
end;

{ What of your things to delete (3.7.0): Tools\uninstall_data.py lists them (before any file is
  removed, while the app's Python is still there) and deletes only the app's own files, each kind
  only in the folder shown for it -- never anything else in a folder you chose, and never backups
  in the other folders backups were saved to (an earlier 3.7.0 build did: fixed). Returns e.g.
  'library,cache'; '' = keep everything. }
function AskWhatToDelete(const Ini: String): String;
var
  Form: TSetupForm;
  Intro, Note: TNewStaticText;
  Boxes: array[0..3] of TNewCheckBox;
  Infos: array[0..3] of TNewStaticText;
  Kinds, Titles: array[0..3] of String;
  OkButton, KeepButton: TNewButton;
  I, Top: Integer;
begin
  Result := '';
  Kinds[0] := 'library'; Titles[0] := 'Library database and settings (videos list, playlists, users, credentials, logs)';
  Kinds[1] := 'cache';   Titles[1] := 'Thumbnails, previews, seekbar previews and frames';
  Kinds[2] := 'backups'; Titles[2] := 'Backups in the backups folder (only that folder)';
  Kinds[3] := 'videos';  Titles[3] := 'Downloaded and uploaded videos';
  Form := CreateCustomForm(460, 405, False, False);
  try
    Form.Caption := 'Uninstall PMV Nexus';
    Intro := TNewStaticText.Create(Form);
    Intro.Parent := Form;
    Intro.Left := ScaleX(12); Intro.Top := ScaleY(12); Intro.Width := Form.ClientWidth - ScaleX(24);
    Intro.AutoSize := False; Intro.WordWrap := True; Intro.Height := ScaleY(42);
    Intro.Caption := 'The program is removed either way. Also delete any of these? Nothing ticked keeps them all ' +
      '(installing again later picks them up).';
    Top := ScaleY(60);
    for I := 0 to 3 do
    begin
      Boxes[I] := TNewCheckBox.Create(Form);
      Boxes[I].Parent := Form;
      Boxes[I].Left := ScaleX(12); Boxes[I].Top := Top; Boxes[I].Width := Form.ClientWidth - ScaleX(24);
      Boxes[I].Height := ScaleY(17);
      Boxes[I].Caption := Titles[I];
      Infos[I] := TNewStaticText.Create(Form);
      Infos[I].Parent := Form;
      Infos[I].Left := ScaleX(30); Infos[I].Top := Top + ScaleY(18); Infos[I].Width := Form.ClientWidth - ScaleX(42);
      Infos[I].AutoSize := False; Infos[I].Height := ScaleY(44); Infos[I].WordWrap := True;
      Infos[I].Font.Color := clGray;
      if StrToIntDef(GetIniString(Kinds[I], 'count', '0', Ini), 0) > 0 then
        Infos[I].Caption := GetIniString(Kinds[I], 'count', '0', Ini) + ' files, ' + GetIniString(Kinds[I], 'size', '', Ini) +
          ' - only in ' + GetIniString(Kinds[I], 'path', '', Ini)
      else
      begin
        Infos[I].Caption := 'Nothing there';
        Boxes[I].Enabled := False;
      end;
      if GetIniString(Kinds[I], 'kept', '', Ini) <> '' then
        Infos[I].Caption := Infos[I].Caption + '. Never deleted: ' + GetIniString(Kinds[I], 'kept', '', Ini);
      Top := Top + ScaleY(66);
    end;
    Note := TNewStaticText.Create(Form);
    Note.Parent := Form;
    Note.Left := ScaleX(12); Note.Top := Top; Note.Width := Form.ClientWidth - ScaleX(24);
    Note.AutoSize := False; Note.WordWrap := True; Note.Height := ScaleY(30);
    Note.Caption := 'Only files PMV Nexus made are deleted; other files in these folders stay.';
    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := 'Continue';
    OkButton.Width := ScaleX(90); OkButton.Height := ScaleY(25);
    OkButton.Top := Form.ClientHeight - ScaleY(37); OkButton.Left := Form.ClientWidth - ScaleX(196);
    OkButton.ModalResult := mrOk; OkButton.Default := True;
    KeepButton := TNewButton.Create(Form);
    KeepButton.Parent := Form;
    KeepButton.Caption := 'Keep all';
    KeepButton.Width := ScaleX(90); KeepButton.Height := ScaleY(25);
    KeepButton.Top := OkButton.Top; KeepButton.Left := Form.ClientWidth - ScaleX(100);
    KeepButton.ModalResult := mrCancel; KeepButton.Cancel := True;
    if Form.ShowModal <> mrOk then
      Exit;
    if Boxes[2].Checked and (SuppressibleMsgBox('Delete ' + GetIniString('backups', 'count', '0', Ini) + ' backup files (' +
        GetIniString('backups', 'size', '', Ini) + ') in' + #13#10 + GetIniString('backups', 'path', '', Ini) + #13#10 + 'for good?' + #13#10#13#10 +
        'They don''t go to the Recycle Bin. Backups in other folders are not touched.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) <> IDYES) then
      Boxes[2].Checked := False;
    if Boxes[3].Checked and (SuppressibleMsgBox('Delete ' + GetIniString('videos', 'count', '0', Ini) + ' video files (' +
        GetIniString('videos', 'size', '', Ini) + ') for good?' + #13#10#13#10 +
        'They can''t be brought back, unless a backup made with "Include the videos" has them.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) <> IDYES) then
      Boxes[3].Checked := False;
    for I := 0 to 3 do
      if Boxes[I].Checked then
      begin
        if Result <> '' then
          Result := Result + ',';
        Result := Result + Kinds[I];
      end;
  finally
    Form.Free;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Ini, Kinds: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    ServiceCommand('stop');
    ServiceCommand('uninstall');
    if IsAdminInstallMode then
      Exec('netsh', 'advfirewall firewall delete rule name="PMV Nexus"', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Ini := GetTempDir + 'pmvnexus_uninstall.ini';
    DeleteFile(Ini);
    if not UninstallSilent and FileExists(VenvPython) and FileExists(AppDir + '\Tools\uninstall_data.py') and
       Exec(VenvPython, '-m Tools.uninstall_data scan --out "' + Ini + '"', AppDir, SW_HIDE, ewWaitUntilTerminated, Code) and
       (Code = 0) then
    begin
      Kinds := AskWhatToDelete(Ini);
      if Kinds <> '' then
        if not Exec(VenvPython, '-m Tools.uninstall_data delete --kinds ' + Kinds, AppDir, SW_HIDE, ewWaitUntilTerminated, Code)
           or (Code <> 0) then
          SuppressibleMsgBox('Some files couldn''t be deleted (in use, or no permission). They stay where they are.',
            mbInformation, MB_OK, IDOK);
    end;
    DeleteFile(Ini);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    DeletePyCaches(AppDir);
    RemoveDir(AppDir + '\extensions');   { only when nothing else is left in it }
    RemoveDir(AppDir);                   { only when nothing is left (nothing kept) }
  end;
end;
