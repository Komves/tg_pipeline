; GPT Windows Installer - pre-release for security review.
; Installs the app as a normal per-user application. No PowerShell,
; hidden commands, registry autorun, self-copy, service or driver.

#define MyAppName "GPT"
#define MyAppVersion "1.1.0"
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
OutputBaseFilename=GPT-Setup-Review
UninstallDisplayName=GPT
UninstallDisplayIcon={app}\GPT.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
; Unsigned test-only installer. Public publishing is disabled in CI.

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\GPT.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\GPT"; Filename: "{app}\GPT.exe"; WorkingDir: "{app}"; Comment: "GPT — отдельный профиль Chrome"
Name: "{userprograms}\GPT"; Filename: "{app}\GPT.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\GPT.exe"; Description: "Запустить GPT после установки"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Only the account file is deleted on uninstall. Chrome user profile is preserved
; so the user can decide whether to delete browsing data separately.
Type: files; Name: "{app}\account.dat"
