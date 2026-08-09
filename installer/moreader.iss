; Inno Setup script for the moreader Windows installer.
; Built in CI by .github/workflows/release.yml:
;   ISCC.exe /DMyAppVersion=<x.y.z> installer\moreader.iss
; Expects the PyInstaller output at dist\moreader.exe.

#define MyAppName "moreader"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "moreader"
#define MyAppExeName "moreader.exe"

[Setup]
; A fixed AppId keeps upgrades/uninstalls tied to the same product.
AppId={{7C6B4B3E-2C4A-4E6B-9E4D-4A6F2E0B7A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=moreader-setup-{#MyAppVersion}
SetupIconFile=moreader\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "kioskstartup"; Description: "Start moreader in &kiosk mode at Windows log on (factory station)"; GroupDescription: "Station setup:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\moreader"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\moreader (kiosk)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--kiosk"
Name: "{group}\Uninstall moreader"; Filename: "{uninstallexe}"
Name: "{autodesktop}\moreader"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Optional: launch in kiosk mode at log on. For a hardened station that also
; restarts on crash, prefer a Task Scheduler task (see README).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "moreader"; ValueData: """{app}\{#MyAppExeName}"" --kiosk"; \
    Tasks: kioskstartup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch moreader now"; Flags: nowait postinstall skipifsilent
