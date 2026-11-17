#define AppName "Lectavo"
#ifndef AppPublisher
#define AppPublisher "Kou Hashizume"
#endif
#ifndef AppVersion
#define AppVersion "0.1.2"
#endif
#ifndef SourceDir
#define SourceDir "..\dist\Lectavo"
#endif
#ifndef OutputDir
#define OutputDir "..\release"
#endif
#ifndef IconFile
#define IconFile "..\assets\app_icon.ico"
#endif

[Setup]
AppId={{4E5686D1-C743-4F32-B939-BEF6E7839C2A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Lectavo
DefaultGroupName=Lectavo
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Lectavo-setup-{#AppVersion}-x64
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Lectavo.exe
UninstallDisplayName=Lectavo
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; Lectavo artwork instead of Inno Setup's default box-and-disc image.
; Regenerate with scripts/generate_installer_images.py. The small header
; image is hidden in [Code] below.
WizardImageFile=wizard\large-164.bmp,wizard\large-246.bmp,wizard\large-328.bmp,wizard\large-410.bmp
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Lectavo
VersionInfoProductName=Lectavo
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
japanese.StartMenuTask=スタートメニューに登録
english.StartMenuTask=Create a Start Menu shortcut

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "startmenuicon"; Description: "{cm:StartMenuTask}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Lectavo"; Filename: "{app}\Lectavo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Lectavo.exe"; Tasks: startmenuicon
Name: "{autodesktop}\Lectavo"; Filename: "{app}\Lectavo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Lectavo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Lectavo.exe"; Description: "{cm:LaunchProgram,Lectavo}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  { No picture in the top-right corner of the wizard header. }
  WizardForm.WizardSmallBitmapImage.Visible := False;
  WizardForm.PageNameLabel.Width :=
    WizardForm.MainPanel.ClientWidth - WizardForm.PageNameLabel.Left - ScaleX(16);
  WizardForm.PageDescriptionLabel.Width :=
    WizardForm.MainPanel.ClientWidth - WizardForm.PageDescriptionLabel.Left - ScaleX(16);
end;
