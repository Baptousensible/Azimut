; Installeur Windows d'Azimut (Inno Setup 6.3+, gratuit).
; Compilation : ISCC.exe /DAppVersion=1.2.3 packaging\azimut.iss   (depuis la racine du dépôt)
; Prérequis : dist\Azimut\ (python packaging\build.py) et packaging\azimut.ico (python packaging\make_icons.py).
; Résultat : release\Azimut-<version>-windows-x64-setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Identifiant stable de l'application : NE PAS le modifier d'une version à l'autre (mises à jour sur place).
AppId={{6E2B8C1A-4F37-4D59-A1C8-3B9E7D50F2A4}
AppName=Azimut
AppVersion={#AppVersion}
AppVerName=Azimut {#AppVersion}
AppPublisher=Azimut
DefaultDirName={autopf}\Azimut
DefaultGroupName=Azimut
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=Azimut-{#AppVersion}-windows-x64-setup
SetupIconFile=azimut.ico
UninstallDisplayIcon={app}\Azimut.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installation sans droits administrateur par défaut (dans le profil de l'utilisateur) ; la boîte de
; dialogue propose l'installation pour tous les utilisateurs si la personne le souhaite.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Azimut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Azimut"; Filename: "{app}\Azimut.exe"
Name: "{autodesktop}\Azimut"; Filename: "{app}\Azimut.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Azimut.exe"; Description: "{cm:LaunchProgram,Azimut}"; Flags: nowait postinstall skipifsilent
