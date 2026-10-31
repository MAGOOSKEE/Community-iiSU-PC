# Community-iiSU-PC Setup

Runs iiSU (an Android emulation frontend) inside a hardware-accelerated Android runtime on Windows or Linux (the Android SDK emulator, or Waydroid on Linux Wayland sessions), patched so launching a game in iiSU hands off to a real PC emulator instead of an Android one.

**This does not include iiSU itself.** iiSU is closed-source, third-party software this project has no direct affiliation with. You will need your own copy of its APK. This tool patches *your* copy, the same way any APK-patching/modding tool works; it never bundles or redistributes iiSU's binary.

**Linux support** (KDE Plasma) was contributed by [jacksterson](https://github.com/jacksterson). It's real and functional: SDK/AVD bootstrap, patching, the keyboard and controller quit hotkeys, KWin-based fullscreen/window management, `.desktop` shortcuts, and a Flatpak-based emulator downloader. The one gap: fullscreen/window management for the SDK emulator is KWin-specific, so it works on KDE Plasma but not GNOME/other desktop environments.

**Waydroid on Wayland:** on a Linux Wayland session, Setup uses [Waydroid](https://waydro.id) instead of the Android SDK emulator (it skips the multi-GB emulator and system image download, and needs no KVM group setup). Waydroid draws iiSU as its own fullscreen Wayland window, so none of the KWin window handling applies and it works on GNOME too. On X11, or if you set `IISUPC_VM_BACKEND=avd` before running Setup, the SDK emulator is used as before. Setup saves the choice to `config.json` (`vm_backend`), so logging into a different session type later never switches an installed setup. This path was written against Waydroid's documented CLI and is unit-tested for its parsing and selection logic, but hasn't been run end to end on a real Waydroid install yet, so reports are welcome.

## Installing

**Windows, recommended:** download `Community-iiSU-PC-Setup-<version>.exe` from the [latest release](https://github.com/MAGOOSKEE/Community-iiSU-PC/releases/latest) and run it. It bundles its own Python and Java runtimes, so you don't need either installed first.

**Linux, or from source on either platform:** clone the repo (or download and extract a release zip). This needs Python and a JDK on PATH yourself (see Requirements below); there's no bundled-runtime installer for Linux yet.

## Requirements

- Windows 10/11 (64-bit), or a 64-bit Linux distribution (KDE Plasma recommended, for fullscreen/window management support)
- Your own copy of the iiSU APK
- Whichever PC emulators you want to use (DuckStation, Dolphin, PCSX2, etc.); install these yourself, or use the Manager's built-in downloader (winget on Windows, Flatpak on Linux)
- About 15 GB of free disk space while Setup runs (see Disk space below) and a decent internet connection (first run downloads the Android SDK + a system image)
- **CPU virtualization must be turned on.** Windows: enable it in your BIOS, and ensure Hyper-V or the Windows Hypervisor Platform and Virtual Machine Platform are enabled. Linux: KVM enabled and `/dev/kvm` accessible (usually means being in the `kvm` group).
- **Linux Wayland sessions** additionally need Waydroid installed from your distro (`waydroid` on PATH), a kernel with binder support, and the `waydroid-container` service. If Waydroid isn't installed, the Setup window offers to install it from your distro's own repositories (apt, dnf, zypper or apk) through a polkit permission prompt, and only if you say yes (set `IISUPC_INSTALL_WAYDROID=1` to opt in for a headless run). Arch-based distros get pointed at the AUR instead, since it needs a helper run as a normal user. Setup also runs `waydroid init` for you (through a polkit prompt) if it hasn't been run, and tells you the exact command for anything else it can't do without root, such as a kernel without binder.
- Python 3.11+ and a JDK on PATH (`java` and `keytool` need to work from a terminal), e.g. [Eclipse Temurin](https://adoptium.net/) or your distro's OpenJDK package. Windows users can skip this by using the installer above instead, which bundles both.

### Disk space

Measured on a default install (16 GB VM storage, Lean VM on), not counting your ROMs or PC emulators:

| | Size |
|---|---|
| Free space needed while Setup runs | 15 GB (the downloaded SDK and the portable copy briefly coexist; about 4 GB is freed at the end) |
| Android emulator | 1.0 GB |
| Android system image | 2.3 GB |
| VM storage file (fresh, with iiSU installed) | 2.1 GB |
| Patched iiSU and Setup's working files | 0.9 GB |
| **Finished install, VM never started** | **about 6.5 GB** |
| Quick-resume snapshot, saved after the first run | +2.5 GB |
| **After the first run** | **about 9 GB** |

The VM storage file then grows as you use it: iiSU's downloaded artwork is about 1 MB per game (a 2,000-game library is about 2 GB). The 8 to 128 GB size you pick in Setup is a limit, not space taken up front, and the file never shrinks by itself (see Settings > Storage below). Resizing or recreating the VM keeps the old storage until you delete it, which can add several GB temporarily. Waydroid installs skip the SDK, system image and snapshot, and need far less.

## First-time setup

1. Drop your iiSU APK into `installer/input/`, or just point Setup at it with Browse.
2. Windows: run **`Community-iiSU-PC Manager.bat`**. Linux: run **`./Community-iiSU-PC\ Manager.sh`**. Click **Run Setup** on its Home page.

Setup checks your APK is actually iiSU and that you have enough disk space, then downloads and sets up a self-contained Android SDK and virtual device, patches your APK, installs it, installs a redirector app for every console `shared/emulator_defaults.py` knows about, and creates a desktop shortcut. It shows which step it's on, since first run can take a while and several GB.

Once it's done, a short onboarding wizard walks you through your ROM directory, emulator search folders (or downloading emulators directly), display resolution, and hotkeys. The Manager's settings pages are there afterward for anything the wizard doesn't cover, and its Home page switches from "Run Setup" to "Open" once setup finishes.

## Day to day use

Double-click the **desktop shortcut**, or run the Manager script for your platform (**`Community-iiSU-PC Manager.bat`** on Windows, **`./Community-iiSU-PC\ Manager.sh`** on Linux): one window, navigated with the sidebar:

- **Home**: AVD/bridge status, Open/Stop, a running status line and a log drawer. The ▾ next to Start also has shortcuts to your ROMs and logs folders, Recreate Shortcut and Clear Resume State. Recreate Shortcut, like the end of onboarding, asks whether the desktop shortcuts and the Manager show iiSU's own icon (taken from your iiSU APK) or Community-iiSU-PC's iiPC icon. Onboarding's also lets you go without a desktop shortcut. Its **Boot Headless (no window)** boots the VM without its window or the launch bridge. A headless boot always cold boots, ignores and clears the saved resume state, and on Windows uses the host GPU with audio off, because a windowless emulator that renders in software crashes shortly after Android boots. Its Ready means Android has finished booting: the start waits on the VM for the boot animation to end, then watches the emulator for 10 more seconds, and if it dies it says so with the exit code and retries. It isn't available with Waydroid, which has no window to leave out: the menu item is greyed out and a headless start request just starts normally.
- **Library**: ROM Directory (your host ROM folder), Media Library (check/restore saved media), Android Storage (browse and manage the VM's shared storage over ADB).
- **Games**: Console (every detected ROM, grouped by multi-disc playlist) and PC (native apps and URI launches, see below).
- **Settings**: Display (the Android VM's resolution and DPI), VM Profiles (named launch profiles for the Android SDK emulator, see below), PC Emulators (which PC emulator each console launches, plus downloading emulators and reinstalling iiSU's redirector apps), Storage (what uses the VM's storage, and resizing it, see below), and Advanced (AVD name, bridge port, hotkeys and debugging options such as "Show console windows").
- **Backup & Diagnostics**: Backup & Restore (back up configuration to a ZIP or restore an earlier one, plus a separate backup of the artwork and media files iiSU keeps inside the running VM; iiSU's private in-app data such as favorites and play history can't be backed up) and Diagnostics (non-destructive checks of the install, Android VM/ADB, bridge, PC apps, Steam integration, logs; also where you check for and apply Community-iiSU-PC and iiSU updates).
- **Credits**: who built this and how (see below).
- **Uninstall**: below a divider at the bottom of the sidebar.

On startup, Community-iiSU-PC re-syncs your ROM library into the VM automatically. Automatic project updates are opt-in and can be enabled from Backup & Diagnostics; **Check for Updates Now** only checks whether an update is available and does not download or install it. The same page also checks whether a newer iiSU is available (against its official GitHub releases) and can re-patch and reinstall it, with a manual override to point at any APK directly, useful for an official pre-release build shared before it's on the releases page. **Check an APK...** on that page inspects any iiSU APK first (about a minute, installs nothing) and reports which of the patch's hooks it found, so you know before updating whether a new pre-release is supported; the same report is available from a terminal with `python installer/patch_check.py <apk>`. When an update is available, the Updates page also shows what's in it. iiSU still needs to notice new games: hit "Rescan full library" in its Library settings.

### Emulator profiles

Settings > **VM Profiles** holds named launch configurations for the Android SDK emulator: GPU rendering (`auto`, `host`, `swiftshader_indirect`, `angle_indirect`), hardware acceleration, CPU cores, RAM, audio (speakers only, the default; microphone and speakers; or no audio), extra emulator flags, and environment variables. Every profile also sets `QEMU_AUDIO_TIMER_PERIOD=1000`, which stops the emulator's sound from crackling on Windows; a profile's own environment variables can override it. Pick a profile, Save, Stop, Open to test it; a profile change cold-boots the VM once. Presets cover the usual suspects, and Duplicate lets you A/B one change at a time. To chase down audio problems: duplicate your profile, try "Speakers only", then "No audio at all" to confirm audio is the cause, then try other GPU modes. Profiles only change launch options on the AVD that Setup created, not which Android system image it runs. (They don't apply on Waydroid.)

### Steam and Steam Deck

Settings > Advanced > **Add to Steam** adds Community-iiSU-PC to Steam as a non-Steam game (on Windows and Linux, native or Flatpak Steam), so you can start it from Steam's library, Big Picture, or a Steam Deck's Game Mode. Close Steam first: it rewrites its shortcut list when it exits and would undo the change, so the button refuses while Steam is running. The previous list is kept as a `.bak` next to it, and **Remove from Steam** undoes it. The shortcut starts the VM and bridge and then ends, so Steam shows it as stopped while iiSU keeps running.

On a Steam Deck, Setup starts with a 1280x800 display profile, and Game Mode (gamescope) counts as a Wayland session, so Waydroid is chosen. Both are untested on real Deck hardware.

A fullscreen overlay covers the AVD boot and the emulator hand-off, showing what's happening ("Booting Community-iiSU-PC...", "Waiting on DuckStation...") instead of raw desktop. It's off while the debug console checkbox is on.

By default, **Escape** controls game quitting and shutdown: tap it while a game is running to force-quit the game and return to iiSU, or tap it while already in iiSU to shut down Community-iiSU-PC. On Linux, Alt+F4, Ctrl+Q, and Ctrl+Alt+X also force a full shutdown, and Ctrl+Shift+Esc mirrors an Escape tap; these four aren't currently rebindable (Escape itself is, in Settings > Advanced, on both platforms). Pressing **Select+Start** together on a controller does the same thing on either platform, and the chord is remappable to any combination of buttons.

## PC apps and games

The Manager's **Games > PC** page lets iiSU launch native applications in addition to emulated console games: an executable (`.exe` on Windows) or a registered URI/protocol such as `steam://rungameid/...` on either platform.

Each entry is represented in iiSU by an empty `.pcgame` placeholder under `bridge/windows_stubs/` (the name is historical, from before Linux support), kept out of your actual ROM directory so it never shows up when you're browsing your real ROM library. The real launch information stays in `bridge/windows_apps.json`; the launch bridge intercepts iiSU's request for the placeholder and starts the configured target instead. Sync into the AVD merges these placeholders into iiSU's `windows` folder automatically.

Use **Add** for individual programs, or **Import Steam Library** to pick installed Steam games and create URI-based entries automatically (Steam's Linux install locations, including Flatpak, are detected too). `windows_apps.json` is local runtime configuration and is intentionally ignored by Git. Executable paths are specific to the PC they were configured on, while URI-based entries are generally more portable. The page also includes import/export and a health check for missing executables, placeholders, and other library inconsistencies.

## Console Games and multi-disc playlists

The Manager's **Games > Console** page lists every game detected in your ROM library, grouped the same way syncing to the AVD does: a multi-disc game backed by an `.m3u`/`.cue` shows up as one entry, not one per disc. Select rows and right-click for two exceptions to that default:

- **Keep Discs Separate** is for a game like Gran Turismo 2, where an `.m3u` actually bundles distinct, separately-launchable modes rather than continuation discs: the individual files show up in iiSU as their own entries instead, and the playlist/sheet itself is hidden from iiSU (still listed on this page, greyed as "hidden", so you can merge it back together later). Only applies to selected playlists/sheets, never to a plain single-file game.
- **Merge Discs Together** undoes that for a previously-excepted playlist/sheet.

Both take effect on your next Start, not while Community-iiSU-PC is already running.

### Per-game settings

Right-click a single game and choose **Game Settings...** to change how just that game launches: use a different PC emulator than the console default, add extra command-line flags, set environment variables, or run a program (a display-mode switcher, a helper script) and wait for it before the game starts. Games with settings are marked "(custom settings)" in the list. They're stored in `bridge/config.json` under `game_overrides`, keyed by the ROM's file name, and apply on that game's next launch with no restart. A pre-launch program that fails or runs longer than 30 seconds is logged and the game launches anyway. RetroArch can't be picked as the override emulator because it needs a core chosen per file type.

## Uninstalling

Open the Manager's **Uninstall** page for a preview of exactly what will be removed and how much space it frees before you confirm. It removes the Android VM and its portable SDK copy, `bridge/config.json`, the signing keystore, and the desktop shortcuts (both the Desktop copy and, on Linux, the app-menu entry under `~/.local/share/applications`). It does not touch your ROM library, your PC emulators, or the iiSU APK you supplied. On Windows it also flags `%LOCALAPPDATA%\Android\Sdk`, which the SDK downloader can end up using; left alone by default since a real Android Studio install would keep its own SDK there too.

## How it works, briefly

`installer/patch_iisu.py` decompiles your iiSU APK, redirects its ROM-launch code to a small injected class that sends the launch request to `bridge/launch_bridge.py` over a local socket, then rebuilds and signs it. The bridge matches normal ROM requests to a PC emulator (configured in `bridge/config.json`). PC app/game requests are instead matched against `bridge/windows_apps.json` and launched directly as native executables or registered URI/protocol targets.

iiSU also needs to think a real emulator is installed for each console before it'll treat it as playable. Setup installs a placeholder "redirector" app for each one (`installer/stub_apk.py`) that does nothing itself, since the patched launch never reaches it. The Manager's PC Emulators page (under Settings) has an "Install Redirector Apps..." button to re-run this any time.

## Project layout

```
Setup.bat/.sh, Uninstall.bat/.sh   CLI-only entry points; use the Manager instead for normal use
Community-iiSU-PC Manager.bat/.sh  launches the Manager directly
VERSION                    this install's release tag, compared against GitHub Releases on a non-git install

shared/qt_theme.py         the dark/gradient look and fonts shared by every window in this project
shared/emulator_defaults.py  curated console -> PC emulator mappings, and which need a redirector
shared/platform_compat.py  cross-platform subprocess/creationflags helpers (Windows vs Linux)
shared/vm_backend.py       picks the Android runtime (SDK emulator vs Waydroid), adb-device parsing
shared/steam_deck.py       Steam Deck and gamescope (Game Mode) detection
shared/app_version.py      the version banner at the top of every log
shared/qt_avatars.py       fetches+circle-crops a GitHub avatar for the Credits page

installer/
  setup_wizard.py          first-time setup and the iiSU update flow, called by setup_app.py
  patch_iisu.py            decompiles, patches, rebuilds, and signs your iiSU (or an update to it)
  patch_check.py           read-only report of whether an iiSU APK is supported, `patch_check.py <apk>`
  sdk_bootstrap.py         downloads/installs the Android SDK and creates the AVD
  smali_patch/             the injected LaunchBridge classes patch_iisu.py adds to iiSU
  stub_apk.py              builds/installs the placeholder "redirector" apps
  stub_apk_template/       the redirector app project
  uninstall.py             removes everything Setup and day-to-day use create
  jre_env.py               points Java-invoking subprocess calls at the bundled JRE, once installed that way
  build_embedded_python.py, build_jre.py, CommunityIisuPC.iss   builds the bundled-runtime Windows installer

bridge/
  ui/                      the Manager GUI (PySide6/Qt): app.py is the entry point, pages/ and dialogs/
                            hold each screen, widgets/ shared building blocks like the display-resolution
                            preview and the sidebar's rounded Card
  services/                non-GUI logic behind the GUI pages (diagnostics, backups, PC apps,
                            Android storage, media library, iiSU update checks, Android-side
                            backups, Steam shortcuts)
  bridge_config.py         shared config.json loader
  apply_display.py         applies config.json's display settings to the AVD
  emulator_profiles.py     named emulator launch profiles (GPU, audio, cores, RAM, flags)
  game_overrides.py        per-game emulator/flags/environment/pre-launch settings
  waydroid_backend.py      starts/stops/connects Waydroid on Linux Wayland sessions
  start_iisu_pc.py         checks for updates, boots the AVD, starts launch_bridge.py
  updater.py               checks for (and, on a git checkout, applies) updates
  stop_iisu_pc.py          tears both back down
  launch_bridge.py         handles iiSU launches for PC emulators and native PC apps
  emulator_lookup.py       finds the emulator and ROM for a launch and builds its command line
  hotkey_keys.py           key and modifier tables for the quit and shutdown hotkeys
  controller_bridge.py     forwards controller input into the AVD (XInput on Windows, joydev on Linux)
  emulator_downloader.py   downloads PC emulators via winget (Windows) or Flatpak (Linux)
  portable_sdk.py          copies the SDK/AVD into a self-contained folder
  sync_library.py          mirrors your ROM library into the AVD as placeholders
  console_names.py         resolves a ROM folder name to one of iiSU's known consoles
  create_shortcut.py       creates the desktop shortcut (.lnk on Windows, .desktop on Linux)
  winapi.py                window-management dispatch: winapi_windows.py (ctypes/Win32) or
                            winapi_linux.py (KWin/xdotool), picked by shared/platform_compat.py

tests/                    unit tests for the pure routing/mapping logic (no AVD needed)
```

## Building the Windows installer

Maintainer-only, not needed to run or develop the project day to day: `installer/build_embedded_python.py` and `installer/build_jre.py` produce the bundled runtimes under `runtime/`, then `ISCC installer\CommunityIisuPC.iss` (Inno Setup 6+) builds `dist/installer/Community-iiSU-PC-Setup-<version>.exe`.

## Running tests

`python -m unittest discover -s tests` (or `python3` on Linux), run from the repo root, runs the unit tests. They cover the pure logic: console and emulator routing and the executable and ROM lookup (`shared/emulator_defaults.py`, `bridge/emulator_lookup.py`, `bridge/console_names.py`), the hotkey tables, start and stop sequencing including Boot Headless and the resume snapshot, the Waydroid and portable SDK file handling, and the services behind the Manager's pages. No AVD or adb is needed.

`tests/test_ui_smoke.py` also builds every Manager page offscreen against a temporary config and switches through all of them; it needs PySide6 and is skipped without it. None of this runs an actual end-to-end launch.

## If something breaks

- Run the Manager's **Backup & Diagnostics > Diagnostics** page first for a non-destructive check of the installation, configuration, Android VM/ADB, bridge, PC apps, Steam integration, and logs.
- `bridge/manager_debug.log` and `bridge/bridge_debug.log` preserve Manager and launch-bridge diagnostics, including uncaught Python exceptions that might otherwise disappear when a console closes.
- `installer/patch_iisu.py`'s patch is anchored on specific log strings in iiSU's code. If iiSU updates and changes them, the patch fails loudly instead of silently producing a broken build. The Media Library's MediaBridge hooks find iiSU's obfuscated helper classes by method signature at patch time; if that fails the patch still completes with a warning and media is installed without re-indexing.
- `bridge/emulator.log`, `bridge/bridge.log`, `bridge/stop.log` and `bridge/setup.log` cover the AVD, the launch bridge, shutdown and Setup respectively. Setup's log keeps what it printed even if the window is closed partway. Open the Manager's Home page (Logs button) to check them.
- Re-running setup is safe: it skips anything already done and won't overwrite an existing `config.json`'s settings.
- Community-iiSU-PC uses a quick resume when nothing relevant has changed since the last start, and only cold-boots (a bit slower) when something that changes the VM itself has changed since then (display, VM profile, USB passthrough, the AVD, or switching between a normal and a headless start), or on the very first start. Adding ROMs or changing PC emulator mappings doesn't need a cold boot: the ROM library is synced into the VM on every start either way, and iiSU's "Rescan full library" picks new games up.

### A leaner Android VM

Setup's **Lean VM** option (on by default, under **Android VM**) removes the Play image's bundled apps that iiSU doesn't need, for less memory and disk use: everything except Files (iiSU's folder picker), Chrome, the Play Store with Google Play services, and the core system. It also installs LeanKeyboard as the keyboard. Each step is checked across a reboot, a step that leaves the VM unhealthy is undone, and if the VM stops booting Setup rebuilds it and installs without the trim. Untick it for a stock VM. The full removal list is only applied to image builds it was verified on; other builds get just the user-facing apps.

### Seeing and changing the VM's storage

**Settings > Storage** shows what is using the VM's internal storage as a colour-coded bar (iiSU's title art and media, iiSU itself, Google Play and Chrome, other apps, other files, Android itself, and free space), how big the VM's file is on your drive, and any old copies left by earlier resizes (with a button to delete them). The VM has to be running for the breakdown. **Recreate VM at this size** changes the internal storage size: it backs up iiSU's title art and media (and checks the backup) before touching anything, moves the old storage aside, builds a fresh VM at the new size, reinstalls iiSU and restores the backup, and puts the old VM back if the reinstall fails. iiSU's own settings and favorites, and the folder access you granted it, are not restored (you'll pick your ROM folder again). The storage file never shrinks when you delete things inside the VM, so recreating it is also the way to get drive space back. When it finishes it offers to delete the old storage (it can be most of a drive's worth of space) and to start the VM, so you can pick your ROM folder again in iiSU.

### If the VM runs out of internal storage

Symptom: iiSU crashes at startup or while scanning, and the system log shows `SQLiteFullException: database or disk is full` or `ENOSPC (No space left on device)`. Diagnostics shows **Android VM storage** as an error. The cause is the Android data partition, which AVDs created before v0.5.5 size at 6 GB. A Play-image Android plus iiSU's downloaded artwork (about 2 GB for a 2,000-game library) fills it. New installs get 16 GB by default, and Setup's **Android VM** card lets you choose 8 to 128 GB (a maximum: the file on your drive only grows as the VM fills). Android can't grow an existing data partition, so an existing VM has to have its data image recreated:

1. **Back up** iiSU's artwork: Backup & Restore > Back Up iiSU Data. Note that iiSU's private in-app data (favorites, play history, settings) can't be backed up and will be reset.
2. **Stop** Community-iiSU-PC.
3. In `bridge/android-sdk-portable/avd-home/iisuwin.avd/`, move (don't delete, so you can roll back) `userdata-qemu.img.qcow2`, `userdata-qemu.img`, `encryptionkey.img(.qcow2)`, `cache.img(.qcow2)` and the `snapshots` folder somewhere else, and set `disk.dataPartition.size=16G` in `config.ini`.
4. **Open** it (it creates a fresh, bigger data image), then reinstall iiSU: Updates > iiSU App Updates > Use a Different APK..., pick your iiSU APK. This also reinstalls the redirector apps.
5. **Restore** the artwork: Backup & Restore > Restore iiSU Data, then Stop and Open once more.

Freeing space without rebuilding is rarely enough: clearing Android's caches frees little, and the bulk of the space is app data Android keeps private.

## Credits

- **[MAGOOSKEE](https://github.com/MAGOOSKEE)**: project owner, built and maintains Community-iiSU-PC.
- **[jacksterson](https://github.com/jacksterson)**: contributed the original Linux port (KWin/xdotool window management, joydev controller support, cross-platform SDK/shortcut/process handling, the Flatpak emulator downloader), integrated here.
- **[Claude](https://github.com/claude)** (Anthropic): AI coding assistant; wrote and refactored most of this codebase in collaboration with MAGOOSKEE.

Shown with live GitHub avatars on the Manager's Credits page.

**AI disclosure:** a large share of this project's code was written by Claude, an AI assistant, working under MAGOOSKEE's direction and review. If you're evaluating this project for safety or correctness before running it, keep that in mind and read the source.

## License

[PolyForm Noncommercial 1.0.0](LICENSE): free to use, fork, modify, and redistribute for any noncommercial purpose. Not licensed for commercial use, including selling it, bundling it into a paid product, or soliciting donations/payment tied to a distribution of it.
