# Development

[Back to the README](../README.md) · See also: [Contributing](../CONTRIBUTING.md)

- [How it works](#how-it-works)
- [Project layout](#project-layout)
- [Running from source](#running-from-source)
- [Running tests](#running-tests)
- [Developer scripts](#developer-scripts)
- [Building the Windows installer](#building-the-windows-installer)
- [Releasing](#releasing)

## How it works

```
 iiSU (patched, in the Android VM)            Your PC
 ┌──────────────────────────────┐   local    ┌────────────────────────────┐
 │ pick a game                  │   socket   │ bridge/launch_bridge.py    │
 │   └ LaunchBridge.launch() ───┼───────────▶│   ├ console ROM ─▶ PC emulator
 │      (injected smali class)  │            │   └ .pcgame    ─▶ program or URI
 └──────────────────────────────┘            └────────────────────────────┘
```

1. **Patching.** `installer/patch_iisu.py` decompiles your iiSU APK, redirects its ROM-launch code to a small injected class (`installer/smali_patch/`) that sends the launch request to the bridge over a local socket, then rebuilds and signs it.
2. **Routing.** `bridge/launch_bridge.py` matches normal ROM requests to a PC emulator (configured in `bridge/config.json`). PC app/game requests are instead matched against `bridge/windows_apps.json` and launched directly as native executables or registered URI/protocol targets.
3. **Redirector apps.** iiSU needs to think a real emulator is installed for each console before it'll treat it as playable. Setup installs a placeholder "redirector" app for each one (`installer/stub_apk.py`) that does nothing itself, since the patched launch never reaches it. The Manager's PC Emulators page has an "Install Redirector Apps..." button to re-run this any time.
4. **Library sync.** `bridge/sync_library.py` mirrors your ROM library into the VM as tiny placeholder files, so iiSU can catalog it without the real multi-GB files.

The Android runtime is the Android SDK emulator (an AVD), or Waydroid on Linux Wayland sessions. `shared/vm_backend.py` picks one.

## Project layout

```
Community-iiSU-PC Manager.bat/.sh   opens the Manager
Setup.bat/.sh, Uninstall.bat/.sh    open Setup, or run the uninstaller, without the Manager
VERSION                             this install's release tag

bridge/        everything that runs day to day
  ui/            the Manager, Setup window, onboarding wizard and loading overlay (PySide6/Qt)
  services/      non-GUI logic behind the Manager's pages
installer/     first-time setup: SDK bootstrap, APK patching, redirector apps, uninstall
shared/        code used by both: theme, emulator defaults, platform helpers, log helpers
tests/         unit tests (no Android VM needed)
scripts/       developer tools, not shipped in the installer
docs/          these guides and their images
```

### bridge/

| File | What it does |
|---|---|
| `start_iisu_pc.py` | Checks for updates, boots the VM, starts `launch_bridge.py` |
| `stop_iisu_pc.py` | Tears both back down |
| `launch_bridge.py` | Handles iiSU's launch requests for PC emulators and native PC apps |
| `emulator_lookup.py` | Finds the emulator and ROM for a launch and builds its command line |
| `sync_library.py` | Mirrors your ROM library into the VM as placeholders |
| `console_names.py` | Resolves a ROM folder name to one of iiSU's known consoles |
| `controller_bridge.py` | Forwards controller input into the VM (XInput on Windows, joydev on Linux) |
| `hotkey_keys.py` | Key and modifier tables for the quit and shutdown hotkeys |
| `game_overrides.py` | Per-game emulator/flags/environment/pre-launch settings |
| `emulator_profiles.py` | Named VM launch profiles (GPU, audio, cores, RAM, flags) |
| `apply_display.py` | Applies `config.json`'s display settings to the AVD |
| `bridge_config.py` | Loads and saves `config.json` |
| `start_progress.py` | The loading screen's percentage and current step |
| `updater.py` | Checks for (and, on a git checkout, applies) updates |
| `emulator_downloader.py` | Downloads PC emulators via winget (Windows) or Flatpak (Linux) |
| `portable_sdk.py` | Keeps the SDK and AVD in a self-contained folder |
| `waydroid_backend.py` | Starts/stops/connects Waydroid on Linux Wayland sessions |
| `create_shortcut.py` | Creates the desktop shortcut (`.lnk` on Windows, `.desktop` on Linux) |
| `winapi.py` | Window-management dispatch: `winapi_windows.py` (ctypes/Win32) or `winapi_linux.py` (KWin/xdotool) |

`bridge/ui/`: `app.py` is the Manager's entry point and `setup_app.py` is Setup's. `main_window.py` and `sidebar.py` hold the window and its navigation, `pages/` and `dialogs/` hold each screen, `widgets/` the shared building blocks, and `workers/` the background-thread helpers. `boot_overlay_app.py` is the loading screen, run as its own process.

`bridge/services/`: one module per Manager page that has real logic behind it (diagnostics, backups, PC apps, Android storage, media library, iiSU updates, Steam shortcuts, VM storage, lean VM), kept free of Qt so it can be tested without a GUI.

### installer/

| File | What it does |
|---|---|
| `setup_wizard.py` | First-time setup and the iiSU update flow |
| `patch_iisu.py` | Decompiles, patches, rebuilds, and signs your iiSU |
| `patch_check.py` | Read-only report of whether an iiSU APK is supported: `patch_check.py <apk>` |
| `smali_patch/` | The injected LaunchBridge classes |
| `sdk_bootstrap.py` | Downloads the Android SDK and creates the AVD |
| `stub_apk.py`, `stub_apk_template/` | Builds and installs the redirector apps |
| `uninstall.py` | Removes everything Setup and day-to-day use create |
| `jre_env.py` | Points Java calls at the bundled JRE when there is one |
| `build_embedded_python.py`, `build_jre.py`, `CommunityIisuPC.iss` | Build the Windows installer |

### shared/

| File | What it does |
|---|---|
| `emulator_defaults.py` | Curated console to PC emulator mappings, and which need a redirector |
| `linux_emulators.py` | What each emulator's program is called on Linux |
| `platform_compat.py` | Cross-platform subprocess and OS helpers |
| `vm_backend.py` | Picks the Android runtime (SDK emulator or Waydroid) |
| `vm_storage.py`, `vm_storage_breakdown.py`, `vm_trim.py` | VM storage sizing, the Storage page's categories, the lean VM profile |
| `adb_client.py` | A small client for the adb server's socket protocol |
| `qt_theme.py`, `qt_avatars.py` | The shared dark theme and fonts, and Credits page avatars |
| `assets/fonts/` | Bundled fonts: Lexend (the UI font) and Material Icons |
| `app_version.py`, `system_info.py`, `emulation_info.py`, `log_stamp.py`, `stage_timer.py` | What the logs say: version banner, PC details, acceleration, line times, stage timings |
| `iisu_install_record.py` | Which iiSU APK and patch version this install has |
| `steam_deck.py` | Steam Deck and gamescope (Game Mode) detection |

**Fonts.** The UI font is Lexend, bundled so Windows and Linux look the same. The product name is always set in Bahnschrift (`Fonts.brand()`), which ships with Windows and falls back to Lexend elsewhere. Get fonts from `shared.qt_theme.Fonts` instead of naming a family in page code.

Every module opens with a docstring that says what it is for and why it exists, so the source is the best reference for anything not listed here.

### Files an install creates

`bridge/config.json` (your settings), `bridge/windows_apps.json` (PC apps), `bridge/android-sdk-portable/` (the SDK and VM), the logs, and a few dot-prefixed state files. All of them are in `.gitignore` and are specific to one machine.

## Running from source

You need Python 3.11+ and a JDK on PATH. PySide6 and Pillow are installed for you on first run.

| Platform | Command |
|---|---|
| Windows | `"Community-iiSU-PC Manager.bat"` |
| Linux | `./Community-iiSU-PC\ Manager.sh` |
| Either | `python -m bridge.ui.app` from the repo root |

## Running tests

From the repo root:

```bash
python -m unittest discover -s tests
```

Use `python3` on Linux. No Android VM or adb is needed.

The tests cover the pure logic: console and emulator routing and the executable and ROM lookup (`shared/emulator_defaults.py`, `bridge/emulator_lookup.py`, `bridge/console_names.py`), the hotkey tables, start and stop sequencing including Boot Headless and the resume snapshot, the Waydroid and portable SDK file handling, and the services behind the Manager's pages.

`tests/test_ui_smoke.py` also builds every Manager page offscreen against a temporary config and switches through all of them; it needs PySide6 and is skipped without it. None of this runs an actual end-to-end launch.

The same tests run on every push and pull request to `dev` and `master` (`.github/workflows/tests.yml`).

## Developer scripts

| Script | What it does |
|---|---|
| `scripts/try_launch_args.py` | Launches each configured PC emulator with a real ROM from your library and checks it stays up, to catch a wrong argument order in `shared/emulator_defaults.py`. Run it before committing a change to that file. `--list` shows what it would do without launching anything. |
| `scripts/capture_screenshots.py` | Regenerates the Manager screenshots in `docs/images/` against a throwaway config and an invented ROM library. Run it after changing how the Manager looks. |
| `scripts/render_brand_assets.py` | Renders everything drawn from the SVG sources in `docs/images/brand/`: the README banner, the social preview, the app icon (`bridge/assets/iisu_launch.png` and `.ico`), the Manager's icon (`manager.png` and `.ico`) and the bare iiPC mark the Home page and sidebar show. Run it on Windows after editing an SVG, since the artwork uses Bahnschrift. |

## Building the Windows installer

Maintainer-only, and not needed to run or develop the project day to day.

```bash
python installer/build_jre.py
```

```bash
python installer/build_embedded_python.py
```

```bash
ISCC installer\CommunityIisuPC.iss
```

The first two produce the bundled runtimes under `runtime/`; the last (Inno Setup 6+) builds `dist/installer/Community-iiSU-PC-Setup-<version>.exe`.

## Releasing

1. Bump `VERSION` (for example `v0.6.0-alpha`) and merge to `master`.
2. Push a tag with exactly the same name.

`.github/workflows/build-installer.yml` then runs the tests, builds the installer and publishes a GitHub release with generated notes. It refuses to build if the tag and `VERSION` disagree. A tag with a suffix (`-alpha`, `-rc1`) is published as a pre-release.
