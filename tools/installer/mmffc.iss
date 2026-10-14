; MMFFC 安装包脚本（Inno Setup 6）——权威版本由 tools/installer/build.ps1 编译
;
; 编译参数（由 build.ps1 通过 ISCC /D 注入）：
;   /DStagePath=<packge/stage>  /DOutputDir=<packge/output>
;   /DMyAppVersion=<版本>      /DMyAppChannel=<alpha|beta|stable>
;
; 关键点：
;   * 安装前检查 Python >= 3.11（py launcher -> 注册表 HKLM/HKCU）
;   * 用户协议：MIT（stage/LICENSE）
;   * PATH：写入 HKLM SYSTEM Environment key 的 Path（安装目录 bin\）
;   * 依赖安装：py -3 -m pip install -r requirements.txt（deps 组件，失败仅告警）
;   * 不捆绑 Python 解释器本体

#define MyAppName "MMFFC"
#define MyAppNameFull "MMFFC - Minecraft Mod / FancyMenu / FTB / Control"
#define MyAppVersion "0.1.0a1"
#define MyAppChannel "alpha"
#define MyAppVersion4 "0.1.0.1"
#define MyAppPublisher "louie369887"
#define MyAppURL "https://github.com/louie369887/MMFFC"
#define MyAppMutex "MMFFC-Setup-Mutex"

; 可由 ISCC /D 覆盖（测试或无管理员权限时的用户级安装）
#ifndef MyAppDefaultDir
  #define MyAppDefaultDir "{autopf}\MMFFC"
#endif
#ifndef MyAppPrivileges
  #define MyAppPrivileges "admin"
#endif
#ifndef MyAppLanguagePath
  #define MyAppLanguagePath ""
#endif

[Setup]
AppId={{7E9F2C41-3A5D-4B8E-9C1F-000000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} ({#MyAppChannel})
VersionInfoVersion={#MyAppVersion4}
VersionInfoProductName={#MyAppName}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={#MyAppDefaultDir}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile={#StagePath}\LICENSE
InfoAfterFile={#StagePath}\post-install.txt
OutputDir={#OutputDir}
OutputBaseFilename=MMFFC-{#MyAppVersion}-{#MyAppChannel}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired={#MyAppPrivileges}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\bin\mmffc.exe
SetupLogging=yes
CloseApplications=no
AppMutex={#MyAppMutex}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "{#MyAppLanguagePath}\ChineseSimplified.isl"

[Types]
Name: "full"; Description: "Full installation"
Name: "compact"; Description: "Compact installation"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core"; Description: "MMFFC core (source + docs)"; Types: full compact custom; Flags: fixed
Name: "shell"; Description: "mmffc> interactive shell launchers (bin\*.cmd)"; Types: full compact custom; Flags: fixed
Name: "exe"; Description: "Standalone executable mmffc.exe (PyInstaller, ~30 MB)"; Types: full
Name: "deps"; Description: "Python dependencies (click/rich/prompt_toolkit/jsonschema/requests/pyyaml)"; Types: full

[Tasks]
; 默认勾选：创建桌面图标、添加到 PATH
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addpath"; Description: "Add MMFFC to system PATH"; GroupDescription: "Environment:"

[Files]
Source: "{#StagePath}\src\*"; DestDir: "{app}\src"; Components: core; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StagePath}\pyproject.toml"; DestDir: "{app}"; Components: core; Flags: ignoreversion
Source: "{#StagePath}\requirements.txt"; DestDir: "{app}"; Components: deps; Flags: ignoreversion
Source: "{#StagePath}\LICENSE"; DestDir: "{app}"; Components: core; Flags: ignoreversion
Source: "{#StagePath}\README.md"; DestDir: "{app}"; Components: core; Flags: ignoreversion
Source: "{#StagePath}\CLI.md"; DestDir: "{app}"; Components: core; Flags: ignoreversion
Source: "{#StagePath}\bin\mmffc.cmd"; DestDir: "{app}\bin"; Components: shell; Flags: ignoreversion
Source: "{#StagePath}\bin\mmffc-mcp.cmd"; DestDir: "{app}\bin"; Components: shell; Flags: ignoreversion
Source: "{#StagePath}\bin\mmffc.exe"; DestDir: "{app}\bin"; Components: exe; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\bin\mmffc.cmd"; Components: shell
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\bin\mmffc.cmd"; Tasks: desktopicon; Components: shell

[Run]
Filename: "{app}\bin\mmffc.exe"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Parameters: "--version"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\src"

[Code]

const
  REQUIRED_MAJOR = 3;
  REQUIRED_MINOR = 11;

var
  PythonFound: Boolean;
  PythonCommand: String;
  PythonVersion: String;
  PipFailed: Boolean;

{ 解析 "Python 3.14.6" 形式的输出（纯 Pascal Script，不用 ISPP 函数） }
function ParsePythonVersion(const Output: String; var Major, Minor: Integer): Boolean;
var
  Text, Rest: String;
  DotPos: Integer;
begin
  Result := False;
  Major := 0;
  Minor := 0;
  Text := Trim(Output);
  if Pos('Python ', Text) = 0 then
    Exit;
  Text := Trim(Copy(Text, Pos('Python ', Text) + 7, 64));
  if Pos(' ', Text) > 0 then
    Text := Copy(Text, 1, Pos(' ', Text) - 1);
  DotPos := Pos('.', Text);
  if DotPos = 0 then
    Exit;
  Major := StrToIntDef(Copy(Text, 1, DotPos - 1), 0);
  Rest := Copy(Text, DotPos + 1, Length(Text) - DotPos);
  DotPos := Pos('.', Rest);
  if DotPos > 0 then
    Rest := Copy(Rest, 1, DotPos - 1);
  if Rest <> '' then
    Minor := StrToIntDef(Rest, -1);
  Result := (Major > 0) and (Minor >= 0);
end;

function VersionAtLeast(Major, Minor: Integer): Boolean;
begin
  Result := (Major > REQUIRED_MAJOR) or
            ((Major = REQUIRED_MAJOR) and (Minor >= REQUIRED_MINOR));
end;

procedure RememberPython(const Command, Version: String);
begin
  PythonFound := True;
  PythonCommand := Command;
  PythonVersion := Version;
  Log(Format('Python found: %s (via %s)', [Version, Command]));
end;

function ProbePythonLauncher(): Boolean;
var
  TempFile: String;
  ResultCode: Integer;
  Content: TStringList;
  Major, Minor: Integer;
begin
  Result := False;
  TempFile := ExpandConstant('{tmp}\mmffc-pyver.txt');
  if Exec(ExpandConstant('{sys}\cmd.exe'),
          '/C py -3 -V 2>"' + TempFile + '"', '',
          SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
    begin
      Content := TStringList.Create();
      try
        Content.LoadFromFile(TempFile);
        if ParsePythonVersion(Content.Text, Major, Minor) and VersionAtLeast(Major, Minor) then
        begin
          RememberPython('py -3', IntToStr(Major) + '.' + IntToStr(Minor));
          Result := True;
        end;
      finally
        Content.Free();
      end;
    end;
    DeleteFile(TempFile);
  end;
end;

function ProbeRegistryPython(): Boolean;
var
  RootKey: Integer;
  SubKey: String;
  Minor: Integer;
  InstallPath: String;
begin
  Result := False;
  for RootKey := HKCU to HKLM do
  begin
    for Minor := 11 to 20 do
    begin
      SubKey := 'Software\Python\PythonCore\3.' + IntToStr(Minor) + '\InstallPath';
      if RegQueryStringValue(RootKey, SubKey, '', InstallPath) then
      begin
        RememberPython('python', '3.' + IntToStr(Minor));
        Log(Format('Registry: %s -> %s', [SubKey, InstallPath]));
        Result := True;
        Exit;
      end;
    end;
  end;
end;

function OpenPythonDownloadPage(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\cmd.exe'),
                 '/C start "" "https://www.python.org/downloads/"', '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
var
  Answer: Integer;
begin
  Result := True;
  PythonFound := False;
  PythonCommand := '';
  PythonVersion := '';
  PipFailed := False;

  { 安装前检查 Python 环境（安装包不捆绑解释器本体） }
  if not ProbePythonLauncher() then
    ProbeRegistryPython();

  if not PythonFound then
  begin
    Answer := MsgBox('MMFFC requires Python 3.11 or newer, but none was found.' + #13#10#13#10 +
                     'This installer does not bundle a Python interpreter.' + #13#10 +
                     'Please install Python 3.11+ from https://www.python.org/downloads/' + #13#10 +
                     '(on Windows, enable "Add python.exe to PATH"), then run this installer again.' + #13#10#13#10 +
                     'Open the Python download page now?',
                     mbConfirmation, MB_YESNO);
    if Answer = IDYES then
      OpenPythonDownloadPage();
    Result := False;
  end;
end;

function PathRegRoot: Integer;
begin
  if IsAdminInstallMode() then
    Result := HKLM
  else
    Result := HKCU;
end;

function PathRegKey: String;
begin
  if IsAdminInstallMode() then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

procedure AppendPathEntry(const Dir: String);
var
  RootKey: Integer;
  Paths: TStringList;
  Current: String;
begin
  RootKey := PathRegRoot();
  if not RegQueryStringValue(RootKey, PathRegKey(), 'Path', Current) then
    Current := '';
  Paths := TStringList.Create();
  try
    Paths.Delimiter := ';';
    Paths.StrictDelimiter := True;
    Paths.DelimitedText := Current;
    if Paths.IndexOf(Dir) < 0 then
    begin
      Paths.Append(Dir);
      RegWriteStringValue(RootKey, PathRegKey(), 'Path', Paths.DelimitedText);
      Log(Format('PATH += %s (root=%d)', [Dir, RootKey]));
    end;
  finally
    Paths.Free();
  end;
end;

procedure RemovePathEntry(const Dir: String);
var
  RootKey: Integer;
  Paths: TStringList;
  Current: String;
  Index: Integer;
begin
  RootKey := PathRegRoot();
  if not RegQueryStringValue(RootKey, PathRegKey(), 'Path', Current) then
    Exit;
  Paths := TStringList.Create();
  try
    Paths.Delimiter := ';';
    Paths.StrictDelimiter := True;
    Paths.DelimitedText := Current;
    Index := Paths.IndexOf(Dir);
    if Index >= 0 then
    begin
      Paths.Delete(Index);
      RegWriteStringValue(RootKey, PathRegKey(), 'Path', Paths.DelimitedText);
      Log(Format('PATH -= %s', [Dir]));
    end;
  finally
    Paths.Free();
  end;
end;

function RunPipInstall(): Boolean;
var
  ResultCode: Integer;
  LogFile: String;
  Command: String;
begin
  Result := False;
  LogFile := ExpandConstant('{tmp}\mmffc-pip.log');
  Command := '/C ' + PythonCommand +
             ' -m pip install --no-input --disable-pip-version-check -r "' +
             ExpandConstant('{app}') + '\requirements.txt" > "' + LogFile + '" 2>&1';
  Log(Format('pip: %s', [Command]));
  if Exec(ExpandConstant('{sys}\cmd.exe'), Command, '', SW_HIDE,
          ewWaitUntilTerminated, ResultCode) then
    Result := (ResultCode = 0)
  else
    Result := False;
  if not Result then
    Log(Format('pip install failed (exit=%d), log: %s', [ResultCode, LogFile]))
  else
    DeleteFile(LogFile);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('addpath') then
      AppendPathEntry(ExpandConstant('{app}\bin'));

    if WizardIsComponentSelected('deps') then
    begin
      if not RunPipInstall() then
        PipFailed := True;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemovePathEntry(ExpandConstant('{app}\bin'));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpFinished) and PipFailed then
    SuppressibleMsgBox('Python dependencies could not be installed automatically ' +
                       '(offline or pip unavailable).' + #13#10 +
                       'Install them manually later:' + #13#10 +
                       '  ' + PythonCommand + ' -m pip install -r "' +
                       ExpandConstant('{app}') + '\requirements.txt"',
                       mbInformation, MB_OK, IDOK);
end;
