# Community-iiSU-PC Setup

Runs iiSU (an Android emulation frontend) inside a Windows-hosted Android VM, patched so launching a game in iiSU hands off to a real PC emulator instead of an Android one.

**This does not include iiSU itself.** iiSU is closed-source, third-party software this project has no affiliation with -- you need your own copy of its APK. This tool patches *your* copy, the same way any APK-patching/modding tool works; it never bundles or redistributes iiSU's binary.

## Requirements

- Windows 10/11 (64-bit)
- A JDK on PATH (`java` and `keytool` need to work from a terminal) -- e.g. [Eclipse Temurin](https://adoptium.net/)
- Python 3.11+
- Your own copy of the iiSU APK
- Whichever PC emulators you actually want to use (DuckStation, Dolphin, PCSX2, etc.) -- install these yourself
- A few GB of free disk space and a decent internet connection (first run downloads the Android SDK + a system image)
- **CPU VIRTUALIZATION MUST BE TURNED ON!** Do this in your bios. 

## First-time setup

1. Drop your iiSU APK into `installer/input/`, or just point Setup at it with Browse.
2. Run **`Community-iiSU-PC Manager.bat`** (in `bridge/`) and click **Run Setup** on its Home page.

Setup checks your APK is actually iiSU and that you have enough disk space, then downloads and sets up a self-contained Android SDK and virtual device, patches your APK, installs it, installs a redirector app for every console `shared/emulator_defaults.py` knows about, and creates a desktop shortcut. It shows which step it's on, since first run can take a while and several GB.

Once it's done, a short onboarding wizard walks you through your ROM directory, emulator search folders, display resolution, and hotkeys. The Manager's settings pages are there afterward for anything the wizard doesn't cover, and its Home page switches from "Run Setup" to "Open" once setup finishes.

## Day to day use

Double-click the **desktop shortcut**, or run **`Community-iiSU-PC Manager.bat`** (in `bridge/`) for the full Manager -- one window, navigated with the hamburger (☰) sidebar:

- **Home** -- AVD/bridge status, Open/Stop, a running status line, and quick buttons to your ROMs folder and logs.
- **ROM Directory**, **Emulators**, **Display**, **Advanced** -- everything `config.json` holds, saved with one Save button. Emulators has a "Test Selected..." button to check a mapping without starting the AVD. Advanced has a "Show console windows" checkbox for debugging.
- **Credits** -- who built this and how (see below).
- **Uninstall** -- below a divider at the bottom of the sidebar.

Every start checks for updates (`bridge/updater.py`) and re-syncs your ROM library into the VM automatically -- both are no-ops when there's nothing to do, so neither adds noticeable time on repeat starts. iiSU still needs to notice new games: hit "Rescan full library" in its Library settings.

A fullscreen overlay (`bridge/boot_overlay.py`) covers the AVD boot and the emulator hand-off, showing what's happening ("Booting Community-iiSU-PC...", "Waiting on DuckStation...") instead of raw desktop. It's off while the debug console checkbox is on.

Inside the VM, tap **Escape** to force-quit the current game and return to iiSU (or close iiSU and shut down the VM entirely if nothing's running); hold it for 5s to always close iiSU and shut down the VM entirely, regardless of what's running (both the key and the hold duration are rebindable in Advanced, along with an optional separate full-shutdown combo). Holding **Back+Start** together on a controller for 2.5s does the same full shutdown.

## Uninstalling

Open the Manager's **Uninstall** page for a preview of exactly what will be removed and how much space it frees before you confirm. It removes the Android VM and its portable SDK copy, `bridge/config.json`, the signing keystore, and the desktop shortcut. It does not touch your ROM library, your PC emulators, or the iiSU APK you supplied. It also flags `%LOCALAPPDATA%\Android\Sdk`, which the SDK downloader can end up using -- left alone by default since a real Android Studio install would keep its own SDK there too.

## How it works, briefly

`installer/patch_iisu.py` decompiles your iiSU APK, redirects its ROM-launch code to a small injected class that sends the launch request to `bridge/launch_bridge.py` over a local socket, then rebuilds and signs it. The bridge matches the requested game to a PC emulator (configured in `bridge/config.json`) and launches it directly on Windows.

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
  manager.py                          the day-to-day app: Home, all settings, Credits, Uninstall
  Community-iiSU-PC Manager.bat       launches manager.py
  emulator_dialogs.py      dialogs shared by manager.py and onboarding_wizard.py
  onboarding_wizard.py     step-by-step first-run setup
  bridge_config.py         shared config.json loader
  apply_display.py         applies config.json's display settings to the AVD
  start_iisu_pc.py         checks for updates, boots the AVD, starts launch_bridge.py
  updater.py               checks for (and, on a git checkout, applies) updates
  boot_overlay.py          fullscreen overlay covering the AVD boot / emulator hand-off
  stop_iisu_pc.py          tears both back down
  launch_bridge.py         listens for launch requests, runs the PC emulator
  controller_bridge.py     forwards controller input into the AVD
  portable_sdk.py          copies the SDK/AVD into a self-contained folder
  sync_library.py          mirrors your ROM library into the AVD as placeholders
  console_names.py         resolves a ROM folder name to one of iiSU's known consoles
  create_shortcut.py       creates the desktop shortcut
  winapi.py                shared Win32 window-management helpers

tests/                    unit tests for the pure routing/mapping logic (no AVD needed)
```

## Running tests

`python -m unittest discover -s tests` runs the unit tests covering the console/emulator routing logic (`shared/emulator_defaults.py`, `bridge/console_names.py`, `bridge/launch_bridge.py`'s `find_emulator_for_package`). Stdlib-only, no AVD or adb needed -- these only check the pure mapping/decision logic, not an actual end-to-end launch.

## If something breaks

- `installer/patch_iisu.py`'s patch is anchored on a specific log string in iiSU's code. If iiSU updates and changes it, the patch fails loudly instead of silently producing a broken build.
- `bridge/emulator.log`, `bridge/bridge.log`, and `bridge/stop.log` cover the AVD, the launch bridge, and shutdown respectively. Open the Manager's Home page (Logs button) to check them.
- Re-running setup is safe -- it skips anything already done and won't overwrite an existing `config.json`'s settings.
- The AVD always cold-boots and never keeps a resume snapshot, trading a bit of boot time for avoiding stale storage/mount state.

## Credits

- **[MAGOOSKEE](https://github.com/MAGOOSKEE)** -- project owner, built and maintains Community-iiSU-PC.
- **[Claude](https://github.com/claude)** (Anthropic) -- AI coding assistant; wrote and refactored most of this codebase in collaboration with MAGOOSKEE.

Both are shown with live GitHub avatars on the Manager's Credits page.

**AI disclosure:** a large share of this project's code was written by Claude, an AI assistant, working under MAGOOSKEE's direction and review. If you're evaluating this project for safety or correctness before running it, keep that in mind and read the source.
