; LotVulture Windows Installer
; Requires Inno Setup 6+
; https://jrsoftware.org/isinfo.php

#define MyAppName "LotVulture"
#ifndef MyAppVersion
  #define MyAppVersion "v-dev"
#endif
#define MyAppPublisher "Lot Vulture LLC"
#define MyAppExeName "lotvulture.exe"
#define MyAppServiceName "LotVulture"
#define MyAppDefaultPort 8000

; Source paths — these are produced by windows\build.sh
#define PyInstallerDir "..\build\windows\dist\lotvulture"
#define NssmExe "nssm.exe"
#define MyAppIco "lotvulture.ico"

[Setup]
AppId={{B4A2C3D1-E8F5-4A7B-9C6D-1F2E3D4C5B6A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={commonpf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
ShowLanguageDialog=no
OutputDir=..\build\windows
OutputBaseFilename=LotVulture_Setup_{#MyAppVersion}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
DisableFinishedPage=no
SetupIconFile={#MyAppIco}

; Service stops on uninstall
UninstallDisplayIcon={app}\{#MyAppIco}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; --- Install Cleanup (remove stale files from prior versions) ---
[InstallDelete]
; Plugins that partially load and corrupt the GLib type system
; (all excluded from new packages — clean up from old installs)
Type: files; Name: "{app}\_internal\gstreamer\lib\gstreamer-1.0\libgstnvcodec.dll"
Type: files; Name: "{app}\_internal\gstreamer\lib\gstreamer-1.0\libgstd3d11.dll"
Type: files; Name: "{app}\_internal\gstreamer\lib\gstreamer-1.0\libgstdwrite.dll"
Type: files; Name: "{app}\_internal\gstreamer\lib\gstreamer-1.0\libgstqsv.dll"
Type: files; Name: "{app}\_internal\gstreamer\lib\gstreamer-1.0\libgstwin32ipc.dll"

; The GStreamer plugin allowlist was tightened to ~56 plugins (from
; ~228) when we added the AV1 fix.  Old installs still have the full
; 228-plugin set on disk.  Inno Setup's ``ignoreversion`` flag copies
; NEW files but doesn't DELETE old ones, so the stale plugins (which
; need unbundled libraries like cairo, pango, nettle, libcrypto, etc.)
; remain in the install dir and now fail to load — flooding the log
; with "module not found" warnings on every GStreamer init.
;
; Remove all ``*.dll`` files in the GStreamer plugin dir that aren't
; in the current allowlist.  We can't list the exact set here (the
; allowlist lives in the PyInstaller spec); instead we use a wildcard
; to nuke all plugins, then let ``[Files]`` copy the new curated set.
; This is safe because the plugin dir contains only drop-in libraries
; (no config, no user data) — losing them just means we re-copy the
; new allowlist from the installer.
Type: files; Name: "{app}\_internal\gstreamer\lib\gstreamer-1.0\*.dll"

; Same for the typelib dir: we keep the .typelib files we ship, but
; delete any typelibs for removed plugins so GIRepository doesn't
; try to load them.
Type: files; Name: "{app}\_internal\gstreamer\lib\girepository-1.0\*.typelib"

; --- Files ---
[Files]
; PyInstaller bundle (all files recursively)
Source: "{#PyInstallerDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; NSSM service wrapper
Source: "{#NssmExe}"; DestDir: "{app}\bin"; Flags: ignoreversion

; Application icon (for shortcuts)
Source: "{#MyAppIco}"; DestDir: "{app}"; Flags: ignoreversion

; EULA (consumed by /api/setup/eula — resolves to LV_HOME/EULA.txt on Windows)
Source: "..\EULA.txt"; DestDir: "{commonappdata}\{#MyAppName}"; Flags: ignoreversion

; Service start/stop helpers (elevate via PowerShell)
Source: "scripts\start_service.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\stop_service.bat"; DestDir: "{app}"; Flags: ignoreversion

; VC++ redistributable (included for convenience)
; Download from: https://aka.ms/vs/17/release/vc_redist.x64.exe
; Place next to installer.iss
; Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

; --- Directories ---
[Dirs]
; Persistent data directories.  The default ACLs Inno Setup applies
; to a directory created under {commonappdata} when run elevated
; grant the Administrators group full control but only allow the
; regular user "read & execute" — which is fine for reading the
; SQLite DB but breaks the scheduler's INSERT (SQLITE_READONLY).
; ``Permissions: users-modify`` widens the ACL to grant the
; ``Users`` group ``GENERIC_MODIFY`` (create/delete files,
; write to existing files), which is what both the service
; (running as the user account) and a desktop double-click of
; ``lotvulture.exe`` need.  We also include ``auth-users`` so
; that ``AUTHORITY\Authenticated Users`` — the SID used by
; services running as ``LocalSystem`` or as the installing
; Administrator on behalf of the user — gets the same access.
Name: "{commonappdata}\{#MyAppName}"; Permissions: users-modify
Name: "{commonappdata}\{#MyAppName}\logs"; Permissions: users-modify

; --- Registry ---
[Registry]
; Store the data path so the service can find it
Root: HKLM; Subkey: "SOFTWARE\{#MyAppName}"; ValueType: string; ValueName: "DataDir"; \
    ValueData: "{commonappdata}\{#MyAppName}"; Flags: uninsdeletekey

; Store the listening port so the uninstaller (and any future
; reconfigure UI) can show the right dashboard URL.  Written
; at install time from the wizard page; cleared on uninstall.
Root: HKLM; Subkey: "SOFTWARE\{#MyAppName}"; ValueType: dword; ValueName: "Port"; \
    ValueData: "{code:GetPort}"; Flags: uninsdeletekey

; --- Service Installation ---
[Run]
; Install VC++ redistributable if needed
; Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/quiet /norestart"; \
;   StatusMsg: "Installing VC++ Redistributable..."; Check: VCRedistNeedsInstall

; Register the Windows service via NSSM.  The --port flag is
; what the operator chose on the wizard page (default 8000).
Filename: "{app}\bin\nssm.exe"; Parameters: "install {#MyAppServiceName} ""{app}\{#MyAppExeName}"" --host 0.0.0.0 --port {code:GetPort} --no-reload"; \
    StatusMsg: "Registering LotVulture service..."; Flags: runhidden

; Configure service: auto-start, restart on failure, log output
Filename: "{app}\bin\nssm.exe"; Parameters: "set {#MyAppServiceName} Start SERVICE_AUTO_START"; \
    Flags: runhidden
Filename: "{app}\bin\nssm.exe"; Parameters: "set {#MyAppServiceName} AppNoConsole 1"; \
    Flags: runhidden
Filename: "{app}\bin\nssm.exe"; Parameters: "set {#MyAppServiceName} AppStdout ""{commonappdata}\{#MyAppName}\logs\service.log"""; \
    Flags: runhidden
Filename: "{app}\bin\nssm.exe"; Parameters: "set {#MyAppServiceName} AppStderr ""{commonappdata}\{#MyAppName}\logs\service-error.log"""; \
    Flags: runhidden
Filename: "{app}\bin\nssm.exe"; Parameters: "set {#MyAppServiceName} AppThrottle 5000"; \
    Flags: runhidden

; Start the service
Filename: "{app}\bin\nssm.exe"; Parameters: "start {#MyAppServiceName}"; \
    StatusMsg: "Starting LotVulture service..."; Flags: runhidden

; Open the dashboard
Filename: "{code:GetDashboardURL}"; \
    Description: "Open LotVulture Dashboard"; Flags: postinstall shellexec

; --- Uninstall ---
[UninstallRun]
; Stop and remove the service
Filename: "{app}\bin\nssm.exe"; Parameters: "stop {#MyAppServiceName}"; Flags: runhidden; RunOnceId: "StopService"
Filename: "{app}\bin\nssm.exe"; Parameters: "remove {#MyAppServiceName} confirm"; Flags: runhidden; RunOnceId: "RemoveService"

; --- Icons ---
[Icons]
Name: "{commondesktop}\LotVulture Dashboard"; Filename: "{code:GetDashboardURL}"; \
    IconFilename: "{app}\{#MyAppIco}"; Comment: "Open LotVulture Dashboard"
Name: "{group}\LotVulture Dashboard"; Filename: "{code:GetDashboardURL}"; \
    IconFilename: "{app}\{#MyAppIco}"; Comment: "Open LotVulture Dashboard"
Name: "{group}\Start LotVulture Service"; Filename: "{app}\start_service.bat"; \
    IconFilename: "{app}\{#MyAppIco}"
Name: "{group}\Stop LotVulture Service"; Filename: "{app}\stop_service.bat"; \
    IconFilename: "{app}\{#MyAppIco}"
Name: "{group}\LotVulture Service (Admin)"; Filename: "{app}\bin\nssm.exe"; \
    Parameters: "edit {#MyAppServiceName}"; IconFilename: "{app}\{#MyAppIco}"
Name: "{group}\Uninstall LotVulture"; Filename: "{uninstallexe}"

; --- Code ---
[Code]

{//////////////////////////////////////////////////////////////////////////////}
{ Custom wizard page: listening port                                            }
{//////////////////////////////////////////////////////////////////////////////}
{ The page is shown after the install-directory page (wpSelectDir).  It       }
{ asks the operator which TCP port the LotVulture service should listen on.  }
{                                                                              }
{ On an UPGRADE (existing install detected in the same directory), the       }
{ current port is read from the registry (written by a previous install via   }
{ the [Registry] section's Port= key) and used as the default.  On a FRESH   }
{ install, the field defaults to 8000.  This means the operator can simply    }
{ click Next on upgrade to keep their existing port, or change it to move   }
{ the service to a different port.                                            }
{                                                                              }
{ The value is stored in ``CustomPort`` (a String) and read back via the     }
{ ``GetPort`` / ``GetDashboardURL`` helpers used by the [Run] and [Icons]     }
{ sections.                                                                    }

var
  CustomPort: String;
  ExistingPortDetected: Boolean;

function GetPort(Param: String): String;
begin
  { Fall back to the default if the page was somehow skipped (e.g. a       }
  { silent install).  The service-install command also reads this value.    }
  if CustomPort = '' then
    Result := '{#MyAppDefaultPort}'
  else
    Result := CustomPort;
end;

function GetDashboardURL(Param: String): String;
begin
  Result := 'http://localhost:' + GetPort('');
end;

{ Read the previously-installed port from the registry.  Returns the port  }
{ string (e.g. "8002") if an existing install was detected, or empty       }
{ string if no previous install (fresh install uses the 8000 default).      }
{                                                                            }
{ We look up the uninstall registry key the previous installer would have   }
{ written.  The key uses ``HKLM\SOFTWARE\Wow6432Node\LotVulture\Port`` on   }
{ 64-bit Windows (Inno Setup runs as 32-bit on x64) and ``HKLM\SOFTWARE\    }
{ LotVulture\Port`` on 32-bit.                                                }
function GetExistingPort: String;
var
  PortValue: Cardinal;
begin
  Result := '';
  { Inno Setup's 32-bit installer on 64-bit Windows is automatically    }
  { reflected by the registry redirector: ``HKLM\SOFTWARE\LotVulture``    }
  { actually reads from ``HKLM\SOFTWARE\Wow6432Node\LotVulture`` (where   }
  { the previous installer wrote the value).  No HKLM32 workaround       }
  { needed — just use HKLM.                                                }
  if RegQueryDWordValue(HKLM,
    'SOFTWARE\{#MyAppName}', 'Port', PortValue) then
  begin
    Result := IntToStr(PortValue);
  end;
end;

{ Called on every keystroke in the port field.  Mirrors the Edit's text     }
{ into CustomPort and disables Next if the value isn't a valid port.        }
procedure PortEdit_OnChange(Sender: TObject);
var
  P: Integer;
begin
  if Sender is TEdit then
    CustomPort := TEdit(Sender).Text;
  if CustomPort = '' then
  begin
    WizardForm.NextButton.Enabled := False;
    Exit;
  end;
  P := StrToIntDef(CustomPort, -1);
  WizardForm.NextButton.Enabled := (P >= 1) and (P <= 65535);
end;

procedure CreatePortWizardPage;
var
  Page: TWizardPage;
  Description, PortLabel: TLabel;
  Edit: TEdit;
  Detected: String;
begin
  { Check for an existing install's port before creating the page so      }
  { we can pre-populate the field.                                          }
  Detected := GetExistingPort;
  if Detected <> '' then
  begin
    CustomPort := Detected;
    ExistingPortDetected := True;
  end
  else
  begin
    CustomPort := '{#MyAppDefaultPort}';
    ExistingPortDetected := False;
  end;

  Page := CreateCustomPage(wpSelectDir,
    'LotVulture Service Port',
    'Choose the TCP port the LotVulture service should listen on.');
  Description := TLabel.Create(Page);
  Description.Parent := Page.Surface;
  if ExistingPortDetected then
    { On upgrade, surface the detected value so the operator knows we      }
    { picked it up.  They can still change it if they want to move.        }
    Description.Caption :=
      'LotVulture is currently configured to use TCP port ' + Detected + '.' + #13#10 +
      'Click Next to keep this port, or change the value below to switch ports.'
  else
    { Fresh install: explain the default.                                }
    Description.Caption :=
      'LotVulture exposes a web dashboard and REST API on a single TCP port.' + #13#10 +
      'The default (8000) is fine for most installations.  Change it only if' + #13#10 +
      '8000 is already in use on this machine.';
  Description.Top := 16;
  Description.Left := 0;
  Description.Width := Page.SurfaceWidth;
  Description.WordWrap := True;

  PortLabel := TLabel.Create(Page);
  PortLabel.Parent := Page.Surface;
  PortLabel.Caption := '&Port:';
  PortLabel.Top := 84;
  PortLabel.Left := 0;

  Edit := TEdit.Create(Page);
  Edit.Parent := Page.Surface;
  Edit.Top := 80;
  Edit.Left := ScaleX(40);
  Edit.Width := ScaleX(80);
  Edit.Text := CustomPort;
  Edit.OnChange := @PortEdit_OnChange;
  CustomPort := Edit.Text;
end;

procedure InitializeWizard;
begin
  CreatePortWizardPage;
end;

{//////////////////////////////////////////////////////////////////////////////}
{ Check if VC++ Redistributable is already installed                         }
{//////////////////////////////////////////////////////////////////////////////}
function VCRedistNeedsInstall: Boolean;
var
  Version: String;
begin
  if RegQueryStringValue(HKLM,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
    'Version', Version) then
  begin
    { Version is like "v14.38.33135.00" — extract the build number }
    Version := Copy(Version, Pos('.', Version) + 1, 4);
    Result := StrToIntDef(Version, 0) < 3831;
  end
  else
    Result := True;
end;

{//////////////////////////////////////////////////////////////////////////////}
{ Stop AND remove the service before file overwrite (avoids "in use"       }
{ errors AND lets the new install apply the new port).                        }
{                                                                              }
{ ``nssm install`` refuses to overwrite an existing service — it just        }
{ returns an error and leaves the old one running with the old parameters.    }
{ So on upgrade we must stop the running service AND remove the registration  }
{ before the [Run] section's ``nssm install`` line fires.  If no service is   }
{ yet registered (fresh install), the stop/remove calls return non-zero and   }
{ we silently continue.                                                        }
{//////////////////////////////////////////////////////////////////////////////}
procedure StopAndRemoveService;
var
  StopRC, RemoveRC: Integer;
begin
  if Exec(ExpandConstant('{app}\bin\nssm.exe'),
     'stop {#MyAppServiceName}',
     '', SW_HIDE, ewWaitUntilTerminated, StopRC) then
  begin
    { Wait for process to fully exit so remove doesn't race. }
    Sleep(2000);
  end;
  Exec(ExpandConstant('{app}\bin\nssm.exe'),
     'remove {#MyAppServiceName} confirm',
     '', SW_HIDE, ewWaitUntilTerminated, RemoveRC);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    StopAndRemoveService;
  end;
end;

{//////////////////////////////////////////////////////////////////////////////}
{ Confirm service removal on uninstall                                        }
{//////////////////////////////////////////////////////////////////////////////}
function InitializeUninstall: Boolean;
begin
  Result := SuppressibleMsgBox(
    'This will stop and remove the LotVulture service and all its files.'#13#13 +
    'Data files (LMDB blobs, SQLite database, logs) will be kept at:'#13 +
    ExpandConstant('{commonappdata}\{#MyAppName}'),
    mbConfirmation, MB_YESNO, IDNO) = IDYES;
end;

{//////////////////////////////////////////////////////////////////////////////}
{ Clean up data directory on uninstall only if the user confirms              }
{//////////////////////////////////////////////////////////////////////////////}
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if SuppressibleMsgBox(
      'Remove all data files (LMDB blobs, SQLite database, logs)?'#13#13 +
      'This cannot be undone.',
      mbConfirmation, MB_YESNO, IDNO) = IDYES then
    begin
      DelTree(ExpandConstant('{commonappdata}\{#MyAppName}'), True, True, True);
    end;
  end;
end;
