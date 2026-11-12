; PMV Nexus -- Windows installer (2.26.0; renamed 2.28.0). Built by .github/workflows/release.yml
; with Inno Setup 6.3+:  iscc /DAppVersion=x.y.z /DPythonSha256=<sha256> PMVNexus.iss
; (both defines are optional: the version is read from VERSION; without the hash the
; downloaded Python installer isn't checked).
;
; Pages: Welcome, License, folder, How it runs (shortcuts / start with Windows / service,
; FFmpeg), Ready, Installing (Python if missing, app files, .venv + packages, Firefox for
; site logins, FFmpeg), then -- only on a first install -- New library or From a backup,
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
  Excludes: "\.git\*,\.github\*,\ui\*,\installer\*,\data\*,\runtime\*,\.venv\*,\downloads\*,\config.yaml,__pycache__,*.pyc,node_modules"
Source: "app.ico"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "build\PMVNexusService.exe"; DestDir: "{app}\runtime\service"; Flags: ignoreversion; Tasks: service
Source: "PMVNexusService.xml"; DestDir: "{app}\runtime\service"; Flags: ignoreversion; Tasks: service

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m Tools.open_app"; \
  WorkingDir: "{app}"; IconFilename: "{app}\installer\app.ico"; Comment: "Open PMV Nexus (starts it if needed)"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m Tools.open_app"; \
  WorkingDir: "{app}"; IconFilename: "{app}\installer\app.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; \
  IconFilename: "{app}\installer\app.ico"; Flags: runminimized; Tasks: autostart

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m Tools.open_app"; WorkingDir: "{app}"; \
  Description: "Open PMV Nexus now"; Flags: postinstall nowait skipifsilent; Check: AppReady

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\backend\__pycache__"
Type: filesandordirs; Name: "{app}\backend\routers\__pycache__"
Type: filesandordirs; Name: "{app}\backend\library_mixins\__pycache__"
Type: filesandordirs; Name: "{app}\Tools\__pycache__"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
{ Note: no line here may start with "#" (e.g. a #13#10 continuation) -- the preprocessor
  would read it as one of its directives. Keep this file plain ASCII. }
const
  FolderKeysMain = 2;   { output_dir, cache_dir on the first folders page }

var
  DownloadPage: TDownloadWizardPage;
  WorkPage: TOutputMarqueeProgressWizardPage;   { "what it's doing" while a step runs (2.26.2) }
  ModePage: TInputOptionWizardPage;
  BackupFilePage: TInputFileWizardPage;
  BackupPwPage: TInputQueryWizardPage;
  FoldersPage: TInputDirWizardPage;
  MoreFoldersPage: TInputDirWizardPage;
  PortPage: TInputQueryWizardPage;
  PythonExe: String;        { base interpreter used to make .venv }
  NeedPython: Boolean;
  IsUpgrade: Boolean;       { config.yaml was already there: keep everything, skip the set-up pages }
  SetupFailed: Boolean;     { a step after copying failed: skip the set-up pages }
  NeedPassword: Boolean;
  MoreFoldersNeeded: Boolean;
  ServiceWasThere: Boolean;
  BackupInfo: String;       { shown on the folders page once a backup was opened }
  FolderKeys: array[0..5] of String;

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

{ Python 3.10-3.13 from python.org / the Microsoft Store, found through the registry (PEP 514). }
function FindPython: Boolean;
var
  Versions: array[0..3] of String;
  Roots: array[0..2] of Integer;
  I, J: Integer;
  Exe: String;
begin
  Result := False;
  Versions[0] := '3.12'; Versions[1] := '3.13'; Versions[2] := '3.11'; Versions[3] := '3.10';
  Roots[0] := HKCU; Roots[1] := HKLM64; Roots[2] := HKLM32;
  for I := 0 to 3 do
    for J := 0 to 2 do
      if RegQueryStringValue(Roots[J], 'Software\Python\PythonCore\' + Versions[I] + '\InstallPath', 'ExecutablePath', Exe) and FileExists(Exe) then
      begin
        PythonExe := Exe;
        Result := True;
        Exit;
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

{ Runs a program in the background -- its output appended to the install log -- while
  WorkPage (already shown) says Msg and keeps the window alive, with the progress file's text
  (or the log's last line) under it. Exec with "wait" froze the window until the program ended.
  Returns the exit code, or -1 if it couldn't be started. }
function RunWithProgress(const Msg, Exe, Params, Env, Progress: String): Integer;
var
  Done, Line, Detail: String;
  S: AnsiString;
  Code, Ticks: Integer;
begin
  Result := -1;
  Done := ExpandConstant('{tmp}\step-done.txt');
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
  Ticks := 0;
  while not FileExists(Done) do
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
  Sleep(200);   { let cmd finish writing it }
  if LoadStringFromFile(Done, S) then
    Result := StrToIntDef(Trim(String(S)), -1);
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

procedure InitializeWizard;
begin
  FolderKeys[0] := 'output_dir'; FolderKeys[1] := 'cache_dir';
  FolderKeys[2] := 'session_dir'; FolderKeys[3] := 'library_db_file';
  FolderKeys[4] := 'dir'; FolderKeys[5] := 'log_dir';

  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
  WorkPage := CreateOutputMarqueeProgressPage('Please wait', 'This can take a while. The details go to data\logs\install.log.');

  { These come after the installation (wpInfoAfter), on a first install only. }
  ModePage := CreateInputOptionPage(wpInfoAfter, 'Your library',
    'Start with an empty library, or bring one back from a backup?',
    'A backup is a .zip from the Backups page of an earlier install (this computer or another one).', True, False);
  ModePage.Add('Set up a new, empty library');
  ModePage.Add('Set up from a backup');
  ModePage.SelectedValueIndex := 0;

  BackupFilePage := CreateInputFilePage(ModePage.ID, 'Backup',
    'Which backup?', 'The library, users, playlists and settings come back from it. Video files are never in a backup: copy your video folder to this computer yourself.');
  BackupFilePage.Add('Backup file:', 'Backups (*.zip)|*.zip', '.zip');

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

  PortPage := CreateInputQueryPage(MoreFoldersPage.ID, 'Port',
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
  if CurPageID = FoldersPage.ID then
  begin
    if BackupInfo <> '' then
      FoldersPage.Description := BackupInfo
    else
      FoldersPage.Description := 'Folders for the videos and their thumbnails & previews';
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = ModePage.ID) or (PageID = BackupFilePage.ID) or (PageID = BackupPwPage.ID) or
     (PageID = FoldersPage.ID) or (PageID = MoreFoldersPage.ID) or (PageID = PortPage.ID) then
  begin
    if IsUpgrade or SetupFailed then
      Result := True
    else if (PageID = BackupFilePage.ID) then
      Result := ModePage.SelectedValueIndex <> 1
    else if (PageID = BackupPwPage.ID) then
      Result := (ModePage.SelectedValueIndex <> 1) or not NeedPassword
    else if (PageID = MoreFoldersPage.ID) then
      Result := not MoreFoldersNeeded;
  end;
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
  WorkPage.Show;
  try
    if ModePage.SelectedValueIndex = 1 then
      SetupCore('Restoring the backup and saving the settings...', 'apply --ini "' + IniFile + '"', True)
    else
      SetupCore('Saving the settings...', 'apply --ini "' + IniFile + '"', True);
    Result := GetIniString('result', 'ok', '0', IniFile) = '1';
    if Result and WizardIsTaskSelected('service') then
    begin
      RunLogged('Installing the Windows service...', ExpandConstant('{#ServiceExe}'), 'install', '');
      RunLogged('Starting the Windows service...', ExpandConstant('{#ServiceExe}'), 'start', '');
    end;
  finally
    WorkPage.Hide;
  end;
  if not Result then
  begin
    Problem := GetIniString('result', 'error', 'Unknown error', IniFile);
    StringChangeEx(Problem, ' | ', #13#10, True);   { setup_core puts one problem per " | " }
    MsgBox('These settings can''t be used yet:' + #13#10#13#10 + Problem + #13#10#13#10 +
      'Go Back to change a folder, or change the port.', mbError, MB_OK);
    Exit;
  end;
  FirewallRule(Port);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    { Downloads before anything is installed, so a failed download changes nothing. }
    NeedPython := not FindPython;
    DownloadPage.Clear;
    if NeedPython then
    begin
      if MsgBox('Python 3.10 or newer wasn''t found on this computer. PMV Nexus needs it.' + #13#10#13#10 +
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
              'Check the internet connection and try again.', mbCriticalError, MB_OK, IDOK);
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
        MsgBox('The app''s settings can''t be read: ' + GetIniString('result', 'error', '', IniFile), mbError, MB_OK);
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
        MsgBox('This backup can''t be used:' + #13#10#13#10 + GetIniString('result', 'error', 'Unknown error', IniFile), mbError, MB_OK);
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
        MsgBox('Wrong password - try again.' + #13#10#13#10 +
          'It''s the password set on the Backups page of the install the backup came from (letters are case-sensitive).', mbError, MB_OK)
      else
        MsgBox('This backup can''t be used:' + #13#10#13#10 + GetIniString('result', 'error', 'Unknown error', IniFile), mbError, MB_OK);
      Result := False;
    end;
    BackupPwPage.Values[0] := '';
  end
  else if CurPageID = PortPage.ID then
    Result := ApplySettings;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Params: String;
begin
  Result := '';
  IsUpgrade := FileExists(AppDir + '\config.yaml');
  ServiceWasThere := FileExists(ExpandConstant('{#ServiceExe}'));
  if ServiceWasThere then
  begin
    ServiceCommand('stop');   { an update: files in use otherwise }
    if not WizardIsTaskSelected('service') then
    begin                     { switched away from running as a service }
      ServiceCommand('uninstall');
      DeleteFile(ExpandConstant('{#ServiceExe}'));
      ServiceWasThere := False;
    end;
  end;
  if NeedPython then
  begin
    Params := '/passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0';
    if IsAdminInstallMode then
      Params := '/passive InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0';
    if not Exec(ExpandConstant('{tmp}\python-installer.exe'), Params, '', SW_SHOW, ewWaitUntilTerminated, Code) or (Code <> 0) then
      Result := 'Python couldn''t be installed (code ' + IntToStr(Code) + '). Install Python {#PythonVersion} from python.org, then run Setup again.'
    else if not FindPython then
      Result := 'Python was installed but Setup can''t find it. Run Setup again.';
  end;
end;

{ After the files are copied: .venv, packages, Firefox for logins, FFmpeg -- each step shown. }
procedure InstallAfterFiles;
var
  Env, Unzip, Tmp: String;
  Ok: Boolean;
begin
  WorkPage.Show;
  try
    Ok := True;
    if not FileExists(VenvPython) then
      Ok := RunLogged('Creating the Python environment (.venv)...', PythonExe, '-m venv "' + AppDir + '\.venv"', '');
    if Ok then
    begin
      RunLogged('Updating pip...', VenvPython, '-m pip install --disable-pip-version-check --upgrade pip', '');
      Ok := RunLogged('Installing the Python packages (a few minutes the first time)...', VenvPython,
        '-m pip install --disable-pip-version-check -r "' + AppDir + '\requirements.txt"', '');
    end;
    if Ok then
    begin
      Env := 'set "PLAYWRIGHT_BROWSERS_PATH=' + AppDir + '\runtime\ms-playwright"';
      if not RunLogged('Installing the browser used for site logins (Firefox)...', VenvPython, '-m playwright install firefox', Env) then
        MsgBox('The browser for site logins couldn''t be installed (see data\logs\install.log). ' +
          'Everything else works; run Setup again later to retry.', mbInformation, MB_OK);
    end;
    if Ok and WizardIsTaskSelected('ffmpeg') then
    begin
      Tmp := AppDir + '\runtime\ffmpeg-new';
      Unzip := '-NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference=''Stop''; ' +
        'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ''' + Tmp + '''; ' +
        'Expand-Archive -LiteralPath ''' + ExpandConstant('{tmp}\ffmpeg.zip') + ''' -DestinationPath ''' + Tmp + '''; ' +
        '$d=Get-ChildItem ''' + Tmp + ''' -Directory | Select-Object -First 1; ' +
        'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ''' + AppDir + '\runtime\ffmpeg''; ' +
        'Move-Item $d.FullName ''' + AppDir + '\runtime\ffmpeg''; Remove-Item -Recurse -Force ''' + Tmp + '''"';
      if not RunLogged('Unpacking FFmpeg...', 'powershell.exe', Unzip, '') then
        MsgBox('FFmpeg couldn''t be unpacked. Install it yourself (e.g. winget install Gyan.FFmpeg).', mbInformation, MB_OK);
    end;
    if not Ok then
    begin
      SetupFailed := True;
      MsgBox('The Python packages couldn''t be installed. Details: ' + LogFile + #13#10#13#10 +
        'Check the internet connection and run Setup again (it continues where it stopped).', mbCriticalError, MB_OK);
    end
    else if IsUpgrade and ServiceWasThere then
      RunLogged('Starting the Windows service again...', ExpandConstant('{#ServiceExe}'), 'start', '');
  finally
    WorkPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallAfterFiles;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    ServiceCommand('stop');
    ServiceCommand('uninstall');
    if IsAdminInstallMode then
      Exec('netsh', 'advfirewall firewall delete rule name="PMV Nexus"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(AppDir + '\data') and (SuppressibleMsgBox(
      'Also delete your library and settings?' + #13#10#13#10 +
      'This deletes the data folder (database, thumbnails, backups, logs - and the videos, if they are in data\videos) ' +
      'and config.yaml in ' + AppDir + '. Videos in other folders are not touched.' + #13#10#13#10 +
      'Choose No to keep them (installing again later picks them up).', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
    begin
      DelTree(AppDir + '\data', True, True, True);
      DeleteFile(AppDir + '\config.yaml');
      RemoveDir(AppDir);
    end;
  end;
end;
