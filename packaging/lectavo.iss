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
; Start with the desktop and Start Menu shortcuts checked instead of repeating
; the choices of an earlier install.
UsePreviousTasks=no
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
; Both start checked on every install and update (see UsePreviousTasks above).
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startmenuicon"; Description: "{cm:StartMenuTask}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Lectavo"; Filename: "{app}\Lectavo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Lectavo.exe"; Tasks: startmenuicon
Name: "{autodesktop}\Lectavo"; Filename: "{app}\Lectavo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Lectavo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Lectavo.exe"; Description: "{cm:LaunchProgram,Lectavo}"; Flags: nowait postinstall skipifsilent

[Code]
var
  TasksHeading: TNewStaticText;
  DesktopIconCheck, StartMenuCheck, LaunchCheck: TNewCheckBox;

function CreateTaskCheckBox(const Caption: String; Row: Integer): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(WizardForm);
  Result.Parent := WizardForm.SelectTasksPage;
  Result.Caption := Caption;
  Result.SetBounds(
    WizardForm.TasksList.Left,
    WizardForm.TasksList.Top + ScaleY(30) + Row * ScaleY(30),
    WizardForm.TasksList.Width,
    ScaleY(24));
end;

procedure InitializeWizard;
begin
  { No picture in the top-right corner of the wizard header. }
  WizardForm.WizardSmallBitmapImage.Visible := False;
  WizardForm.PageNameLabel.Width :=
    WizardForm.MainPanel.ClientWidth - WizardForm.PageNameLabel.Left - ScaleX(16);
  WizardForm.PageDescriptionLabel.Width :=
    WizardForm.MainPanel.ClientWidth - WizardForm.PageDescriptionLabel.Left - ScaleX(16);

  { Setup's built-in task list draws clipped checkboxes on high-DPI Windows 11.
    Show ordinary checkboxes instead; the real task selection is kept in sync
    below, so silent installs and the summary page behave as before. }
  WizardForm.TasksList.Visible := False;
  TasksHeading := TNewStaticText.Create(WizardForm);
  TasksHeading.Parent := WizardForm.SelectTasksPage;
  TasksHeading.Caption := ExpandConstant('{cm:AdditionalIcons}');
  TasksHeading.SetBounds(
    WizardForm.TasksList.Left, WizardForm.TasksList.Top, WizardForm.TasksList.Width, ScaleY(20));
  DesktopIconCheck := CreateTaskCheckBox(ExpandConstant('{cm:CreateDesktopIcon}'), 0);
  StartMenuCheck := CreateTaskCheckBox(ExpandConstant('{cm:StartMenuTask}'), 1);

  { The "Launch Lectavo" box on the last page is clipped the same way. }
  LaunchCheck := TNewCheckBox.Create(WizardForm);
  LaunchCheck.Parent := WizardForm.FinishedPage;
  LaunchCheck.Caption := ExpandConstant('{cm:LaunchProgram,Lectavo}');
  LaunchCheck.Checked := True;
  LaunchCheck.Visible := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
  begin
    DesktopIconCheck.Checked := WizardIsTaskSelected('desktopicon');
    StartMenuCheck.Checked := WizardIsTaskSelected('startmenuicon');
  end;
  if (CurPageID = wpFinished) and WizardForm.RunList.Visible
    and (WizardForm.RunList.Items.Count > 0) then
  begin
    LaunchCheck.SetBounds(
      WizardForm.RunList.Left,
      WizardForm.RunList.Top,
      WizardForm.RunList.Width,
      ScaleY(24));
    LaunchCheck.Checked := WizardForm.RunList.Checked[0];
    LaunchCheck.Visible := True;
    WizardForm.RunList.Visible := False;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Tasks: String;
begin
  if CurPageID = wpSelectTasks then
  begin
    if DesktopIconCheck.Checked then Tasks := 'desktopicon' else Tasks := '!desktopicon';
    if StartMenuCheck.Checked then Tasks := Tasks + ',startmenuicon' else Tasks := Tasks + ',!startmenuicon';
    WizardSelectTasks(Tasks);
  end;
  if (CurPageID = wpFinished) and LaunchCheck.Visible then
    WizardForm.RunList.Checked[0] := LaunchCheck.Checked;
  Result := True;
end;
