# Linux

[Back to the README](../README.md) · See also: [Installing](installing.md), [Using it](using.md)

Linux support was contributed by [jacksterson](https://github.com/jacksterson). It's real and functional: SDK/AVD bootstrap, patching, the keyboard and controller quit hotkeys, KWin-based fullscreen/window management, `.desktop` shortcuts, and a Flatpak-based emulator downloader.

- [Which Android runtime you get](#which-android-runtime-you-get)
- [KDE Plasma and other desktops](#kde-plasma-and-other-desktops)
- [Waydroid on Wayland](#waydroid-on-wayland)
- [Steam Deck](#steam-deck)
- [How emulators are found](#how-emulators-are-found)

## Which Android runtime you get

| Your session | Runtime | Notes |
|---|---|---|
| X11 | Android SDK emulator | Fullscreen/window management needs KDE Plasma |
| Wayland | [Waydroid](https://waydro.id) | Works on GNOME too; not yet run end to end on a real install |
| Steam Deck Game Mode | Waydroid | Counts as a Wayland session; untested on real hardware |

Set `IISUPC_VM_BACKEND=avd` before running Setup to use the SDK emulator on Wayland. Setup saves the choice to `config.json` (`vm_backend`), so logging into a different session type later never switches an installed setup.

## KDE Plasma and other desktops

Fullscreen/window management for the SDK emulator is KWin-specific, so it works on KDE Plasma but not GNOME or other desktop environments. That is the one gap in the SDK emulator path.

You need KVM enabled and `/dev/kvm` accessible, which usually means being in the `kvm` group.

## Waydroid on Wayland

On a Linux Wayland session, Setup uses Waydroid instead of the Android SDK emulator. It skips the multi-GB emulator and system image download, and needs no KVM group setup. Waydroid draws iiSU as its own fullscreen Wayland window, so none of the KWin window handling applies and it works on GNOME too.

It needs:

- Waydroid installed from your distro (`waydroid` on PATH)
- a kernel with binder support
- the `waydroid-container` service

If Waydroid isn't installed, the Setup window offers to install it from your distro's own repositories (apt, dnf, zypper or apk) through a polkit permission prompt, and only if you say yes (set `IISUPC_INSTALL_WAYDROID=1` to opt in for a headless run). Arch-based distros get pointed at the AUR instead, since it needs a helper run as a normal user. Setup also runs `waydroid init` for you (through a polkit prompt) if it hasn't been run, and tells you the exact command for anything else it can't do without root, such as a kernel without binder.

VM profiles and Boot Headless don't apply on Waydroid.

> [!NOTE]
> This path was written against Waydroid's documented CLI and is unit-tested for its parsing and selection logic, but hasn't been run end to end on a real Waydroid install yet. Reports are welcome.

## Steam Deck

On a Steam Deck, Setup starts with a 1280x800 display profile, and Game Mode (gamescope) counts as a Wayland session, so Waydroid is chosen. Both are untested on real Deck hardware.

To start it from Game Mode, use [Add to Steam](using.md#steam-and-steam-deck).

## How emulators are found

The emulator mappings name each emulator by its Windows program (`pcsx2-qt.exe`). On Linux each one is read as that emulator's Linux names:

- its own binary (`pcsx2-qt`)
- its Flatpak launcher (`net.pcsx2.PCSX2`)
- EmuDeck's launcher script (`pcsx2-qt.sh`)
- an AppImage whose file name starts with the emulator's name

Flatpak's launcher folders, `~/Emulation/tools/launchers`, `~/Applications` and `~/.local/bin` are searched without being added, after your own search folders. An AppImage or script has to be marked executable. A mapping can also name a Linux program directly.

RetroArch cores come from RetroArch's own Online Updater on Linux.
