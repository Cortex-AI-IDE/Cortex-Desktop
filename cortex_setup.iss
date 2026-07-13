
; Cortex AI IDE Setup Script
; Creates professional Windows installer with Next/Back wizard

#define MyAppName "Cortex AI IDE"
#define MyAppVersion "2.8.1"
#define MyAppPublisher "Cortex AI"
#define MyAppURL "https://github.com/cortex-ai"
#define MyAppExeName "Cortex.exe"

[Setup]
AppId=CORTEX-AI-IDE-2026-UNIQUE
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\Cortex
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=.\installer_output
OutputBaseFilename=Cortex_Setup_v{#MyAppVersion}
SetupIconFile=src\assets\logo\logo.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; License Agreement page (Cursor-style): user must select "I accept the
; agreement" before installation can continue. Links open in the browser.
LicenseFile=license.rtf
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; When PrivilegesRequired=lowest:
; - "Install for me only" = no admin prompt, installs to user folder
; - "Install for all users" = admin prompt, installs to Program Data
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=AI-Powered IDE for Developers
VersionInfoTextVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenuicon"; Description: "Create Start Menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "Create Quick Launch shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Remove env files shipped by older versions (<= 2.7.1). Installed builds
; must not carry any .env — API keys live in Windows Credential Manager via
; Settings, and a stray .env silently OVERRIDES them (env-var tier wins).
Type: files; Name: "{app}\.env"
Type: files; Name: "{app}\.env.example"
Type: files; Name: "{app}\_internal\.env"
Type: files; Name: "{app}\_internal\.env.example"

[Files]
; Main executable and all files from dist\Cortex folder
; This includes: Python runtime, PyQt6, all hidden imports, node_modules
; (monaco-editor), bin/ (node.exe, rg.exe), plugins/, etc.
Source: "dist\Cortex\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Configuration files
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcuts
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Comment: "Cortex AI IDE"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; \
    Comment: "Cortex AI IDE"

; Quick Launch shortcut
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon; \
    Comment: "Cortex AI IDE"

[Run]
; Launch Cortex after install. runasoriginaluser is REQUIRED: without it the
; app launches with the INSTALLER's token (elevated when the user chose
; "install for all users") — a different security context whose Credential
; Manager writes can diverge from normal launches.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

; Add Windows Defender exclusions (prevents subprocess scanning lag)
Filename: "powershell"; Parameters: "-ExecutionPolicy Bypass -Command ""Add-MpExclusion -Path '{app}'; Add-MpExclusion -Path '{localappdata}\Cortex'; Add-MpExclusion -Process 'Cortex.exe'"""; StatusMsg: "Adding Windows Defender exclusions..."; Flags: runhidden skipifsilent

[Messages]
; Custom message for Windows security warning
WelcomeLabel2=This will install [name] on your computer.%n%nNote: Windows may show a security warning during installation. If you see "Windows protected your PC", click "More info" then "Run anyway" to continue.%n%nIt is recommended that you close all other applications before continuing.

[Registry]
; Optional: Add to PATH (uncomment if needed)
; Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

; Right-click on a folder: "Open with Cortex IDE"
; Uses HKCU (user-level) instead of HKCR to work without admin privileges
Root: HKCU; Subkey: "Software\Classes\Directory\shell\CortexIDE"; ValueType: string; ValueName: ""; ValueData: "Open with Cortex IDE"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\CortexIDE"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\Cortex.exe"",0"
Root: HKCU; Subkey: "Software\Classes\Directory\shell\CortexIDE\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; Right-click on folder background (inside a folder): "Open with Cortex IDE"
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\CortexIDE"; ValueType: string; ValueName: ""; ValueData: "Open with Cortex IDE"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\CortexIDE"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\Cortex.exe"",0"
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\CortexIDE\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%V"""

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath)
  then begin
    Result := true;
    exit;
  end;
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;
