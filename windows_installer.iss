; Build with Inno Setup 6
; Output: Windows installer (.exe)

#define MyAppName "AI招聘工作台"
#define MyAppPublisher "AI招聘工作台"
#define VersionFileHandle FileOpen("app_version.txt")
#if VersionFileHandle
  #define MyAppVersion Trim(FileRead(VersionFileHandle))
  #expr FileClose(VersionFileHandle)
#else
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{D4A5C4BB-CC45-47F3-B8C8-0A6BBE27F4A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=release
OutputBaseFilename=AIBossWorkbench-Windows-Installer-v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\AI招聘工作台\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\AI招聘工作台.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\AI招聘工作台.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\AI招聘工作台.exe"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
