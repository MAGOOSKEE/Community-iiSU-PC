# Installing

[Back to the README](../README.md) · Next: [Using it](using.md)

- [Requirements](#requirements)
- [Disk space](#disk-space)
- [Getting it](#getting-it)
- [First-time setup](#first-time-setup)
- [Uninstalling](#uninstalling)

## Requirements

- Windows 10/11 (64-bit), or a 64-bit Linux distribution (KDE Plasma recommended, for fullscreen/window management support). Linux has its own page: [Linux](linux.md).
- Your own copy of the iiSU APK. iiSU is not included, see the [README](../README.md#what-it-is).
- Whichever PC emulators you want to use (DuckStation, Dolphin, PCSX2, etc.). Install these yourself, or use the Manager's built-in downloader (winget on Windows, Flatpak on Linux).
- About 15 GB of free disk space while Setup runs (see [Disk space](#disk-space)) and a decent internet connection: the first run downloads the Android SDK and a system image.
- **CPU virtualization must be turned on.**
  - Windows: enable it in your BIOS, and make sure Hyper-V or the Windows Hypervisor Platform and Virtual Machine Platform are enabled.
  - Linux: KVM enabled and `/dev/kvm` accessible (usually means being in the `kvm` group).
- Python 3.11+ and a JDK on PATH (`java` and `keytool` need to work from a terminal), e.g. [Eclipse Temurin](https://adoptium.net/) or your distro's OpenJDK package. **Windows users can skip this** by using the installer, which bundles both.

## Disk space

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

The VM storage file then grows as you use it: iiSU's downloaded artwork is about 1 MB per game (a 2,000-game library is about 2 GB). The 8 to 128 GB size you pick in Setup is a limit, not space taken up front, and the file never shrinks by itself (see [VM storage](troubleshooting.md#seeing-and-changing-the-vms-storage)). Resizing or recreating the VM keeps the old storage until you delete it, which can add several GB temporarily. Waydroid installs skip the SDK, system image and snapshot, and need far less.

## Getting it

**Windows, recommended:** download `Community-iiSU-PC-Setup-<version>.exe` from the [latest release](https://github.com/MAGOOSKEE/Community-iiSU-PC/releases/latest) and run it. It bundles its own Python and Java runtimes, so you don't need either installed first.

**Linux, or from source on either platform:** clone the repo (or download and extract a release zip). This needs Python and a JDK on PATH yourself (see [Requirements](#requirements)); there's no bundled-runtime installer for Linux yet.

```bash
git clone https://github.com/MAGOOSKEE/Community-iiSU-PC.git
```

## First-time setup

1. Open the Manager.
   - Installed on Windows: the **Community-iiSU-PC Manager** shortcut.
   - From source on Windows: run **`Community-iiSU-PC Manager.bat`**.
   - Linux: run **`./Community-iiSU-PC\ Manager.sh`**.
2. Click **Run Setup** on its Home page.
3. Point Setup at your iiSU APK with Browse (or drop the APK into `installer/input/` beforehand).

Setup checks your APK is actually iiSU and that you have enough disk space, then downloads and sets up a self-contained Android SDK and virtual device, patches your APK, installs it, installs a redirector app for every console it knows about, and creates a desktop shortcut. It shows which step it's on, since a first run can take a while and several GB.

Once it's done, a short onboarding wizard walks you through your ROM directory, emulator search folders (or downloading emulators directly), display resolution, and hotkeys. The Manager's settings pages are there afterward for anything the wizard doesn't cover, and its Home page switches from "Run Setup" to "Start iiSU" once setup finishes.

Re-running Setup is safe: it skips anything already done and won't overwrite an existing `config.json`'s settings.

### A leaner Android VM

Setup's **Lean VM** option (on by default, under **Android VM**) removes the Play image's bundled apps that iiSU doesn't need, for less memory and disk use: everything except Files (iiSU's folder picker), Chrome, the Play Store with Google Play services, and the core system. It also installs LeanKeyboard as the keyboard. Each step is checked across a reboot, a step that leaves the VM unhealthy is undone, and if the VM stops booting Setup rebuilds it and installs without the trim. Untick it for a stock VM. The full removal list is only applied to image builds it was verified on; other builds get just the user-facing apps.

### Setup from a terminal

`Setup.bat` / `Setup.sh` open the Setup window directly, and `Uninstall.bat` / `Uninstall.sh` run the uninstaller in a terminal. The Manager does both, so these are only there for convenience.

## Uninstalling

Open the Manager's **Uninstall** page for a preview of exactly what will be removed and how much space it frees before you confirm. It removes the Android VM and its portable SDK copy, `bridge/config.json`, the signing keystore, and the desktop shortcuts (both the Desktop copy and, on Linux, the app-menu entry under `~/.local/share/applications`).

It does not touch your ROM library, your PC emulators, or the iiSU APK you supplied. On Windows it also flags `%LOCALAPPDATA%\Android\Sdk`, which the SDK downloader can end up using; that is left alone by default, since a real Android Studio install would keep its own SDK there too.
