# Community-iiSU-PC Setup

Runs iiSU (an Android emulation frontend) inside a Windows-hosted Android VM, patched so launching a game in iiSU hands off to a real PC emulator instead of an Android one.

**This does not include iiSU itself.** iiSU is closed-source, third-party software this project has no direct affiliation with. You will need your own copy of its APK. This tool patches *your* copy, the same way any APK-patching/modding tool works; it never bundles or redistributes iiSU's binary.

## Requirements

- Windows 10/11 (64-bit)
- A JDK on PATH (`java` and `keytool` need to work from a terminal), e.g. [Eclipse Temurin](https://adoptium.net/)
- Python 3.11+
- Your own copy of the iiSU APK
- Whichever PC emulators you want to use (DuckStation, Dolphin, PCSX2, etc.) install these yourself
- A few GB of free disk space and a decent internet connection (first run downloads the Android SDK + a system image)
- **CPU VIRTUALIZATION MUST BE TURNED ON!** Do this in your bios. Ensure that Hyper-V or the Windows Hypervisor Platform and Virtual Machine Platform are enabled also.

## First-time setup

1. Drop your iiSU APK into `installer/input/`, or just point Setup at it with Browse.
2. Run **`Community-iiSU-PC Manager.bat`** and click **Run Setup** on its Home page.

Setup checks your APK is actually iiSU and that you have enough disk space, then downloads and sets up a self-contained Android SDK and virtual device, patches your APK, installs it, installs a redirector app for every console `shared/emulator_defaults.py` knows about, and creates a desktop shortcut. It shows which step it's on, since first run can take a while and several GB.

Once it's done, a short onboarding wizard walks you through your ROM directory, emulator search folders, display resolution, and hotkeys. The Manager's settings pages are there afterward for anything the wizard doesn't cover, and its Home page switches from "Run Setup" to "Open" once setup finishes.

## Day to day use

Double-click the **desktop shortcut**, or run **`Community-iiSU-PC Manager.bat`** for the full Manager: one window, navigated with the hamburger menu sidebar:

- **Home**: AVD/bridge status, Open/Stop, a running status line, and quick buttons to your ROMs folder and logs.
- **ROM Directory**: configure the host folder containing your ROM library.
- **Emulators**: configure PC emulator mappings and search folders, test a selected mapping without starting the AVD, and reinstall iiSU's redirector apps.
- **Windows Apps**: manage native Windows applications and URI/protocol launches exposed to iiSU through `.pcgame` placeholders, including Steam library import and health checks.
- **Android Storage**: browse and manage the Android VM's shared storage, transfer files and folders, and edit text files over ADB.
- **Display**: configure the Android VM's resolution and DPI.
- **Backup & Restore**: back up iiSU-PC configuration to a ZIP or safely restore an earlier configuration.
- **Advanced**: configure hotkeys and debugging options such as "Show console windows."
- **Diagnostics**: run non-destructive checks of iiSU-PC's configuration, Android VM/ADB, bridge, Windows Apps, Steam integration, logs, and related components.
- **Credits**: who built this and how (see below).
- **Uninstall**: below a divider at the bottom of the sidebar.

On startup, Community-iiSU-PC re-syncs your ROM library into the VM automatically. Automatic project updates are opt-in and can be enabled from the Manager's Diagnostics page; **Check for Updates Now** only checks whether an update is available and does not download or install it. iiSU still needs to notice new games: hit "Rescan full library" in its Library settings.

A fullscreen overlay (`bridge/boot_overlay.py`) covers the AVD boot and the emulator hand-off, showing what's happening ("Booting Community-iiSU-PC...", "Waiting on DuckStation...") instead of raw desktop. It's off while the debug console checkbox is on.

By default, **Escape** controls game quitting and shutdown: tap it while a game is running to force-quit the game and return to iiSU, or tap it while already in iiSU to shut down Community-iiSU-PC. The keyboard controls are rebindable in Advanced. Pressing **Select+Start** together on a controller does the same thing, and the chord is remappable to any combination of buttons.

## Windows Apps

The Manager's **Windows Apps** page lets iiSU launch native Windows applications in addition to emulated console games. An entry can launch either an executable (`.exe`) or a registered Windows URI/protocol such as `steam://rungameid/...`.

Each entry is represented in iiSU by an empty `.pcgame` placeholder under `bridge/windows_stubs/`, kept out of your actual ROM directory so it never shows up when you're browsing your real ROM library. The real launch information stays in `bridge/windows_apps.json`; the launch bridge intercepts iiSU's request for the placeholder and starts the configured Windows target instead. Sync into the AVD merges these placeholders into iiSU's `windows` folder automatically. An install upgraded from a version that kept placeholders under `<roms_dir>/windows` copies them into the new location automatically the first time the Manager notices that folder; the old folder is left alone and is safe to delete yourself once you've confirmed everything still works.

Use **Add** for individual programs, drag and drop an `.exe` file anywhere on the Windows Apps page, or **Steam Library Import** to find installed Steam games and create URI-based entries automatically. Steam entries can use artwork cached by the Manager; this does not modify iiSU's own artwork or SteamGridDB integration.

`windows_apps.json` is local runtime configuration and is intentionally ignored by Git. Executable paths are specific to the PC they were configured on, while URI-based entries are generally more portable. The Windows Apps page also includes import/export and a health check for missing executables, placeholders, and other library inconsistencies.

## Console Games and multi-disc playlists

The Manager's **Games > Console** page lists every game detected in your ROM library, grouped the same way syncing to the AVD does: a multi-disc game backed by an `.m3u`/`.cue` shows up as one entry, not one per disc. Select rows and right-click for two exceptions to that default:

- **Keep Discs Separate** is for a game like Gran Turismo 2, where an `.m3u` actually bundles distinct, separately-launchable modes rather than continuation discs: the individual files show up in iiSU as their own entries instead, and the playlist/sheet itself is hidden from iiSU (still listed on this page, greyed as "hidden", so you can merge it back together later). Only applies to selected playlists/sheets, never to a plain single-file game.
- **Merge Discs Together** undoes that for a previously-excepted playlist/sheet.

Both take effect on your next Start, not while Community-iiSU-PC is already running.

## Uninstalling

Open the Manager's **Uninstall** page for a preview of exactly what will be removed and how much space it frees before you confirm. It removes the Android VM and its portable SDK copy, `bridge/config.json`, the signing keystore, and the desktop shortcut. It does not touch your ROM library, your PC emulators, or the iiSU APK you supplied. It also flags `%LOCALAPPDATA%\Android\Sdk`, which the SDK downloader can end up using; left alone by default since a real Android Studio install would keep its own SDK there too.

## How it works, briefly

`installer/patch_iisu.py` decompiles your iiSU APK, redirects its ROM-launch code to a small injected class that sends the launch request to `bridge/launch_bridge.py` over a local socket, then rebuilds and signs it. The bridge matches normal ROM requests to a PC emulator (configured in `bridge/config.json`). Windows Apps requests are instead matched against `bridge/windows_apps.json` and launched directly as native executables or registered URI/protocol targets.

iiSU also needs to think a real emulator is installed for each console before it'll treat it as playable. Setup installs a placeholder "redirector" app for each one (`installer/stub_apk.py`) that does nothing itself, since the patched launch never reaches it. The Manager's Emulators page has an "Install Redirector Apps..." button to re-run this any time.

## Project layout

```
Setup.bat, Uninstall.bat   CLI-only entry points; use the Manager instead for normal use
VERSION                    this install's release tag, compared against GitHub Releases on a non-git install

shared/theme.py            the dark/gradient look shared by every GUI in this project
shared/emulator_defaults.py  curated console -> PC emulator mappings, and which need a redirector
shared/avatars.py          fetches+circle-crops a GitHub avatar for the Credits page

installer/
  setup_wizard.py          first-time setup, called by setup_gui.py
  setup_gui.py             GUI front end for setup_wizard.py
  sdk_bootstrap.py         downloads/installs the Android SDK and creates the AVD
  patch_iisu.py            decompiles, patches, rebuilds, and signs your iiSU APK
  smali_patch/             the injected LaunchBridge classes patch_iisu.py adds to iiSU
  stub_apk.py              builds/installs the placeholder "redirector" apps
  stub_apk_template/       the redirector app project
  uninstall.py             removes everything Setup and day-to-day use create

bridge/
  manager.py               day-to-day settings, Windows Apps, Android storage, backup/restore, diagnostics
  Community-iiSU-PC Manager.bat       launches manager.py
  emulator_dialogs.py      dialogs shared by manager.py and onboarding_wizard.py
  onboarding_wizard.py     step-by-step first-run setup
  bridge_config.py         shared config.json loader
  apply_display.py         applies config.json's display settings to the AVD
  start_iisu_pc.py         checks for updates, boots the AVD, starts launch_bridge.py
  updater.py               checks for (and, on a git checkout, applies) updates
  boot_overlay.py          fullscreen overlay covering the AVD boot / emulator hand-off
  stop_iisu_pc.py          tears both back down
  launch_bridge.py         handles iiSU launches for PC emulators and native Windows apps
  controller_bridge.py     forwards controller input into the AVD
  portable_sdk.py          copies the SDK/AVD into a self-contained folder
  sync_library.py          mirrors your ROM library into the AVD as placeholders
  console_names.py         resolves a ROM folder name to one of iiSU's known consoles
  create_shortcut.py       creates the desktop shortcut
  winapi.py                shared Win32 window-management helpers

tests/                    unit tests for the pure routing/mapping logic (no AVD needed)
```

## Running tests

`python -m unittest discover -s tests` runs the unit tests covering the console/emulator routing logic (`shared/emulator_defaults.py`, `bridge/console_names.py`, `bridge/launch_bridge.py`'s `find_emulator_for_package`). Stdlib-only, no AVD or adb needed. These only check the pure mapping/decision logic, not an actual end-to-end launch.

## If something breaks

- Run the Manager's **Diagnostics** page first for a non-destructive check of the installation, configuration, Android VM/ADB, bridge, Windows Apps, Steam integration, and logs.
- `bridge/manager_debug.log` and `bridge/bridge_debug.log` preserve Manager and launch-bridge diagnostics, including uncaught Python exceptions that might otherwise disappear when a console closes.
- `installer/patch_iisu.py`'s patch is anchored on a specific log string in iiSU's code. If iiSU updates and changes it, the patch fails loudly instead of silently producing a broken build.
- `bridge/emulator.log`, `bridge/bridge.log`, and `bridge/stop.log` cover the AVD, the launch bridge, and shutdown respectively. Open the Manager's Home page (Logs button) to check them.
- Re-running setup is safe: it skips anything already done and won't overwrite an existing `config.json`'s settings.
- Community-iiSU-PC uses a quick resume when nothing relevant has changed since the last start, and only cold-boots (a bit slower) when your settings or ROM library have changed since then, or on the very first start.

## Credits

- **[MAGOOSKEE](https://github.com/MAGOOSKEE)**: project owner, built and maintains Community-iiSU-PC.
- **[Claude](https://github.com/claude)** (Anthropic): AI coding assistant; wrote and refactored most of this codebase in collaboration with MAGOOSKEE.

Both are shown with live GitHub avatars on the Manager's Credits page.

**AI disclosure:** a large share of this project's code was written by Claude, an AI assistant, working under MAGOOSKEE's direction and review. If you're evaluating this project for safety or correctness before running it, keep that in mind and read the source.
