; Inno Setup script for Community-iiSU-PC.
;
; Bundles the two runtimes built by installer/build_jre.py and
; installer/build_embedded_python.py (run both first -- this script
; expects their output at ..\runtime\jre and ..\runtime\python, same as
; installer/jre_env.py checks at app runtime) so the installed app needs
; neither Python nor Java pre-installed. This replaces Setup.bat's old
; prerequisite-checking role entirely; that logic is deleted, not
; ported, since a bundled runtime is guaranteed present. First-run Setup
; itself (the multi-GB Android SDK download, AVD creation, APK patch --
; see installer/setup_wizard.py's run_setup()) is unchanged in scope and
; still happens after install, just with nothing left to pre-install.
;
; Compile with ISCC.exe (Inno Setup 6+) from the repo root:
;   ISCC installer\CommunityIisuPC.iss
;
; PrivilegesRequired=lowest + the {auto*} constants below mean this
; installs per-user (no admin, no UAC prompt) by default, which also
; sidesteps Program Files write-permission issues -- the app writes its
; own config.json/caches/etc. under {app}\bridge at runtime, which a
; machine-wide Program Files install wouldn't allow without extra ACL
; work. A user who runs the installer elevated can still redirect to a
; machine-wide location manually; that's Inno's standard behavior for
; PrivilegesRequired=lowest, not something this script special-cases.

#define AppName "Community-iiSU-PC"
#define AppPublisher "MAGOOSKEE"
#define AppURL "https://github.com/MAGOOSKEE/Community-iiSU-PC"

; Reads the first line of ..\VERSION so this script never needs manual
; edits per release -- bump VERSION, rebuild, done.
#define VersionFile FileOpen(SourcePath + "..\VERSION")
#define AppVersion Trim(FileRead(VersionFile))
#expr FileClose(VersionFile)

[Setup]
AppId={{8C6C6F2E-6B7B-4B6E-9B9B-2C7B7A9D5E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=Community-iiSU-PC-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Only first-party, ahead-of-time-packageable content ships (embedded
; runtimes, app source, apktool.jar, smali/stub templates, config
; template, launch icon) -- see the plan's "what ships"/"what never
; ships" lists. Never the multi-GB Android SDK pieces sdk_bootstrap.py
; downloads at first run, never any per-install generated state, and
; never installer/keystore or installer/input/*.apk (no redistribution
; rights to the user's own iiSU APK, and a shared signing key across
; installs would be a real security bug, not just a packaging nicety).
SetupIconFile=..\bridge\assets\iisu_launch.ico
UninstallDisplayIcon={app}\bridge\assets\iisu_launch.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Bundled runtimes -- built ahead of time by the two build_*.py scripts,
; never regenerated on the end user's machine.
Source: "..\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\runtime\jre\*"; DestDir: "{app}\runtime\jre"; Flags: recursesubdirs createallsubdirs ignoreversion

; App source. Excludes mirror .gitignore's "Local/build artifacts" and
; per-install generated-state entries -- none of that is this project's
; source, all of it gets recreated by Setup/first run/normal use.
Source: "..\bridge\*"; DestDir: "{app}\bridge"; Flags: recursesubdirs createallsubdirs ignoreversion; \
    Excludes: "__pycache__,*.pyc,android-sdk-portable,config.json,.path_cache.json,.runtime_state.json,.boot_fingerprint.json,.iisu_icon.ico,_icon_extract_tmp,windows_apps.json,windows_stubs,dedupe_exceptions.json,manager_debug.log,bridge_debug.log,launch_history.log,cache,iidb,restore_safety,emulator.log,bridge.log,stop.log"

Source: "..\installer\*"; DestDir: "{app}\installer"; Flags: recursesubdirs createallsubdirs ignoreversion; \
    Excludes: "__pycache__,*.pyc,android-sdk,_work,_cmdline_tools_extract,commandlinetools.zip,keystore,input\*.apk,tools\build-tools,*.iss,*.bat"

Source: "..\shared\*"; DestDir: "{app}\shared"; Flags: recursesubdirs createallsubdirs ignoreversion; \
    Excludes: "__pycache__,*.pyc"

Source: "..\VERSION"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName} Manager"; Filename: "{app}\runtime\python\pythonw.exe"; \
    Parameters: "-m bridge.ui.app"; WorkingDir: "{app}"; IconFilename: "{app}\bridge\assets\iisu_launch.ico"
Name: "{autodesktop}\{#AppName} Manager"; Filename: "{app}\runtime\python\pythonw.exe"; \
    Parameters: "-m bridge.ui.app"; WorkingDir: "{app}"; IconFilename: "{app}\bridge\assets\iisu_launch.ico"; Tasks: desktopicon
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
; Optional finish-page checkbox -- first-run Setup (SDK download, AVD
; creation, APK patch) is still a distinct, user-triggered step after
; install finishes, per the plan; this just offers to jump straight
; into it instead of making the user find the Start Menu entry.
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-m bridge.ui.setup_app"; \
    WorkingDir: "{app}"; Description: "Launch {#AppName} Setup now"; Flags: postinstall skipifsilent nowait

[UninstallRun]
; installer/uninstall.py is the one source of truth for what counts as
; this app's generated data (portable SDK/AVD copy, config, caches,
; keystores, the desktop shortcut, etc.) -- duplicating that removal
; logic in Inno script would just create a second place for it to drift
; out of sync. Inno runs [UninstallRun] entries before it removes the
; files below, so uninstall.py still has its own source tree (and the
; bundled Python that runs it) available when this fires.
Filename: "{app}\runtime\python\python.exe"; Parameters: "uninstall.py --yes"; \
    WorkingDir: "{app}\installer"; Flags: runhidden; RunOnceId: "RemoveAppData"

[UninstallDelete]
; Confirmed by an actual install/launch/uninstall cycle: running the app
; even once creates __pycache__/*.pyc throughout {app}\bridge (and
; installer/shared, from anything imported at Setup time). Inno never
; tracked those -- Python wrote them at runtime, not this installer --
; so its normal per-file uninstall leaves every directory containing one
; non-empty and thus undeleted (rmdir fails with error 145 rather than
; forcing removal). filesandordirs here force-removes the app's whole
; installed tree regardless, rather than leaving a scatter of empty-
; looking-but-not-actually-empty folders behind after every real
; uninstall.
Type: filesandordirs; Name: "{app}\bridge"
Type: filesandordirs; Name: "{app}\installer"
Type: filesandordirs; Name: "{app}\shared"
Type: filesandordirs; Name: "{app}\runtime"
