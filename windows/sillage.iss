; Installateur Windows (Inno Setup 6) : iscc windows\sillage.iss
#define AppVersion GetEnv("SILLAGE_VERSION")
[Setup]
AppName=Sillage
AppVersion={#AppVersion}
AppPublisher=Armand Mulier
DefaultDirName={localappdata}\Programs\Sillage
DefaultGroupName=Sillage
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Sillage-{#AppVersion}-Windows-Setup
SetupIconFile=Sillage.ico
UninstallDisplayIcon={app}\Sillage.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; Flags: unchecked

[Files]
Source: "..\dist\Sillage\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Sillage"; Filename: "{app}\Sillage.exe"
Name: "{userdesktop}\Sillage"; Filename: "{app}\Sillage.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Sillage.exe"; Description: "Lancer Sillage"; Flags: nowait postinstall skipifsilent
