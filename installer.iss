; Inno Setup: installs PeopleSearch for the current user (no admin rights needed)
#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{7C1D4B52-9E0A-4B6B-8D53-3F1A2C0E5A11}
AppName=PeopleSearch
AppVersion={#AppVersion}
AppPublisher=PeopleSearch
DefaultDirName={localappdata}\Programs\PeopleSearch
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=PeopleSearch-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\חיפוש אנשים"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{userdesktop}\חיפוש אנשים"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Start PeopleSearch"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\code_prev"
Type: filesandordirs; Name: "{app}\code"
Type: filesandordirs; Name: "{app}\python"
