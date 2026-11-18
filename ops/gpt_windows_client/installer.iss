; GPT Windows installer (per-user, existing user profile/credentials preserved).
; Installs the app as a normal per-user application. No PowerShell,
; hidden commands, registry autorun, self-copy, service or driver.

#define MyAppName "GPT"
#define MyAppVersion "1.1.3"
#define MyAppPublisher "GPT Setup"

[Setup]
AppId={{4CC4653A-D297-4AA6-9D75-9F3AC0C35EBA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\GPT
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousPrivileges=no
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=GPT-Setup
UninstallDisplayName=GPT
UninstallDisplayIcon={app}\GPT-v1.1.2.ico
CloseApplications=force
RestartApplications=no
SetupLogging=yes
SetupIconFile=GPT-v1.1.2.ico
; Unsigned build. AV checks are mandatory before public publishing.

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\GPT.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "GPT-v1.1.2.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\GPT"; Filename: "{app}\GPT.exe"; IconFilename: "{app}\GPT-v1.1.2.ico"; WorkingDir: "{app}"; Comment: "GPT — отдельный профиль Chrome"
Name: "{userprograms}\GPT"; Filename: "{app}\GPT.exe"; IconFilename: "{app}\GPT-v1.1.2.ico"; WorkingDir: "{app}"

[Run]
Filename: "{app}\GPT.exe"; Description: "Запустить GPT после установки"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Only the account file is deleted on uninstall. Chrome user profile is preserved
; so the user can decide whether to delete browsing data separately.
Type: files; Name: "{app}\account.dat"


[Code]
{ During upgrades, shut down only our own GPT launcher and the Chrome
  started with GPT's private ChromeProfile. Other Chrome windows and
  unrelated programs must never be closed. No PowerShell, taskkill,
  registry autorun or global process-name termination. }
procedure CloseOwnedProcesses(const ProcessName: String;
  const InstalledExe: String; const PrivateProfile: String);
var
  Locator, Service, Items, Proc: Variant;
  I: Integer;
  ExeName, Args, ProfileFlag: String;
  ShouldClose: Boolean;
  Status: Integer;
begin
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Service := Locator.ConnectServer('.', 'root\CIMV2');
  Items := Service.ExecQuery(
    'SELECT Name, ExecutablePath, CommandLine, ProcessId FROM Win32_Process ' +
    'WHERE Name="' + ProcessName + '"');

  ProfileFlag := '--user-data-dir=' + Lowercase(PrivateProfile);
  for I := Items.Count - 1 downto 0 do
  begin
    Proc := Items.ItemIndex(I);
    ExeName := '';
    Args := '';
    if not VarIsNull(Proc.ExecutablePath) then
      ExeName := Lowercase(Trim(VarToStr(Proc.ExecutablePath)));
    if not VarIsNull(Proc.CommandLine) then
      Args := Lowercase(VarToStr(Proc.CommandLine));
    { The Go launcher may quote an entire command-line argument. }
    StringChangeEx(Args, '"', '', True);

    if ProcessName = 'GPT.exe' then
      ShouldClose := (ExeName = Lowercase(InstalledExe))
    else
      ShouldClose := (Pos(ProfileFlag, Args) > 0);

    if ShouldClose then
    begin
      Log('Closing prior GPT-owned process PID ' + IntToStr(Integer(Proc.ProcessId)) +
        ' before upgrade (image: ' + ProcessName + ')');
      Status := Proc.Terminate();
      if Status <> 0 then
        Log('WMI Terminate returned ' + IntToStr(Status) +
          ' for ' + ProcessName);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  InstalledExe, PrivateProfile: String;
  Pass: Integer;
begin
  Result := '';
  InstalledExe := ExpandConstant('{localappdata}\GPT\GPT.exe');
  PrivateProfile := ExpandConstant('{localappdata}\GPT\ChromeProfile');
  { Only close on update, never during the first install. }
  if not FileExists(InstalledExe) then
    exit;

  { Close only the private GPT Chrome and its local proxy executable.
    Running two passes handles short-lived helper/child process races. }
  for Pass := 1 to 2 do
  begin
    try
      CloseOwnedProcesses('chrome.exe', InstalledExe, PrivateProfile);
      CloseOwnedProcesses('GPT.exe', InstalledExe, PrivateProfile);
    except
      Log('WMI GPT process pre-close unavailable; Inno Restart Manager fallback');
    end;
    Sleep(700);
  end;
end;
