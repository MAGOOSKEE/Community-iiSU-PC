"""Which Android runtime hosts iiSU, and small pure helpers around it.

Two backends exist:

- "avd": the Android SDK emulator (QEMU). The only option on Windows, and
  the fallback on Linux.
- "waydroid": an LXC container sharing the host kernel. Used on Linux
  whenever a Wayland session is detected, since Waydroid only renders
  through a Wayland compositor.

The choice is made once at Setup and saved to config.json ("vm_backend"),
so logging into an X11 session later doesn't silently flip an installed
setup onto a runtime that has no iiSU in it. Anything that needs the choice
reads it through resolve_backend(). IISUPC_VM_BACKEND=avd|waydroid forces it
for a single Setup run (e.g. to use the AVD on Wayland).

Nothing in here runs a subprocess or touches the filesystem beyond
shutil.which, so it's all unit-testable.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from shared.platform_compat import IS_WINDOWS
from shared.steam_deck import is_gamescope_session

BACKEND_AVD = "avd"
BACKEND_WAYDROID = "waydroid"
VALID_BACKENDS = (BACKEND_AVD, BACKEND_WAYDROID)
ENV_OVERRIDE = "IISUPC_VM_BACKEND"

# Waydroid's container bridge (waydroid0) hands the Android side an address
# on this subnet; 112 is the lease it gets on a stock install.
WAYDROID_DEFAULT_ADDRESS = "192.168.240.112"
WAYDROID_ADB_PORT = 5555


def is_wayland_session(env: Mapping[str, str] | None = None, is_windows: bool = IS_WINDOWS) -> bool:
    if is_windows:
        return False
    env = os.environ if env is None else env
    return (
        env.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
        or bool(env.get("WAYLAND_DISPLAY", "").strip())
        # Steam's Game Mode runs gamescope, itself a Wayland compositor,
        # and doesn't always set the session variables above.
        or is_gamescope_session(env)
    )


def waydroid_installed(which: Callable[[str], str | None] = shutil.which) -> bool:
    return which("waydroid") is not None


def preferred_backend(env: Mapping[str, str] | None = None, is_windows: bool = IS_WINDOWS) -> str:
    """What Setup should install on this machine: Waydroid on a Wayland
    session, the AVD everywhere else. An explicit IISUPC_VM_BACKEND wins."""
    env = os.environ if env is None else env
    forced = env.get(ENV_OVERRIDE, "").strip().lower()
    if forced in VALID_BACKENDS:
        if forced == BACKEND_WAYDROID and is_windows:
            return BACKEND_AVD
        return forced
    return BACKEND_WAYDROID if is_wayland_session(env, is_windows) else BACKEND_AVD


def resolve_backend(config: Mapping | None = None, env: Mapping[str, str] | None = None, is_windows: bool = IS_WINDOWS) -> str:
    """The backend an already-set-up install uses: the one Setup saved, else
    what preferred_backend() would pick (an install from before this setting
    existed has no saved value and has always been an AVD)."""
    if is_windows:
        return BACKEND_AVD
    saved = (config or {}).get("vm_backend")
    if saved in VALID_BACKENDS:
        return saved
    if config and config.get("avd_name"):
        return BACKEND_AVD
    return preferred_backend(env, is_windows)


def _device_lines(adb_devices_stdout: str):
    for line in adb_devices_stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            yield parts[0]


_NETWORK_SERIAL_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|localhost|\[[0-9a-fA-F:]+\]):\d+$")


def vm_device_serial(adb_devices_stdout: str) -> str | None:
    """The serial of the connected Android VM in `adb devices` output, or
    None. An AVD shows up as emulator-<port>, Waydroid as <ip>:<port> (adb
    connects to it over TCP). A phone on a USB cable is deliberately not a
    match, it's never the VM this project drives."""
    for serial in _device_lines(adb_devices_stdout):
        if serial.startswith("emulator-") or _NETWORK_SERIAL_RE.match(serial):
            return serial
    return None


def vm_device_connected(adb_devices_stdout: str) -> bool:
    return vm_device_serial(adb_devices_stdout) is not None


def parse_waydroid_status(text: str) -> dict[str, str]:
    """`waydroid status` prints "Key:\\tValue" lines (Session, Container,
    Vendor type, ...). Keys are lower-cased with spaces as underscores."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        if key:
            result[key] = value.strip()
    return result


_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def waydroid_address_candidates(leases_text: str = "", neigh_text: str = "", configured: str | None = None) -> list[str]:
    """Ordered guesses for the container's IP: an explicitly configured
    address first, then the dnsmasq lease (newest first), then the host's
    neighbour table on waydroid0, then the stock default. Duplicates are
    dropped, order kept."""
    found: list[str] = []

    def add(addr: str | None) -> None:
        if addr and addr not in found:
            found.append(addr)

    if configured:
        add(configured.split(":")[0].strip())
    lease_rows = []
    for line in leases_text.splitlines():
        parts = line.split()
        # dnsmasq lease line: <expiry> <mac> <ip> <hostname> <client-id>
        if len(parts) >= 3 and _IPV4_RE.fullmatch(parts[2]):
            try:
                lease_rows.append((int(parts[0]), parts[2]))
            except ValueError:
                lease_rows.append((0, parts[2]))
    for _expiry, addr in sorted(lease_rows, reverse=True):
        add(addr)
    for line in neigh_text.splitlines():
        parts = line.split()
        if parts and _IPV4_RE.fullmatch(parts[0]) and parts[-1] not in ("FAILED", "INCOMPLETE"):
            add(parts[0])
    add(WAYDROID_DEFAULT_ADDRESS)
    return found


# == Opt-in Waydroid installation ==

WAYDROID_INSTALL_DOCS = "https://docs.waydro.id/usage/install-on-desktops"
ENV_AUTO_INSTALL = "IISUPC_INSTALL_WAYDROID"


@dataclass(frozen=True)
class WaydroidInstallPlan:
    manager: str
    command: tuple[str, ...]  # the package-manager command, without any privilege wrapper

    @property
    def display(self) -> str:
        return " ".join(self.command)

    @property
    def elevated_command(self) -> list[str]:
        """The command run through polkit so no terminal or sudo is needed."""
        return ["pkexec", *self.command]


def parse_os_release(text: str) -> dict[str, str]:
    """/etc/os-release KEY=VALUE lines (values optionally quoted)."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _distro_ids(os_release: Mapping[str, str]) -> set[str]:
    ids = {os_release.get("ID", "").lower()}
    ids.update(os_release.get("ID_LIKE", "").lower().split())
    ids.discard("")
    return ids


def waydroid_install_plan(os_release_text: str, which: Callable[[str], str | None] = shutil.which) -> WaydroidInstallPlan | None:
    """The package-manager command that installs Waydroid on this distro, or
    None when there's no safe unattended option (Arch only has Waydroid in
    the AUR, which needs a helper run as a normal user; unknown distros).

    Deliberately conservative: only the distro's own repositories, never
    `curl | sudo bash` from a third-party repo. On Debian/Ubuntu that means
    the install can fail when the release doesn't carry the package, in
    which case the caller falls back to pointing at the official docs."""
    ids = _distro_ids(parse_os_release(os_release_text))
    if ids & {"debian", "ubuntu"} and which("apt-get"):
        return WaydroidInstallPlan("apt", ("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "waydroid"))
    if ids & {"fedora", "rhel", "centos"} and which("dnf"):
        return WaydroidInstallPlan("dnf", ("dnf", "install", "-y", "waydroid"))
    if ids & {"opensuse", "suse", "sles"} and which("zypper"):
        return WaydroidInstallPlan("zypper", ("zypper", "--non-interactive", "install", "waydroid"))
    if ids & {"alpine", "postmarketos"} and which("apk"):
        return WaydroidInstallPlan("apk", ("apk", "add", "waydroid"))
    return None


def waydroid_manual_install_hint(os_release_text: str) -> str:
    ids = _distro_ids(parse_os_release(os_release_text))
    if ids & {"arch", "manjaro", "endeavouros", "cachyos"}:
        return "Waydroid is in the AUR on Arch-based distros: install it with an AUR helper (e.g. `yay -S waydroid`)."
    return f"Install Waydroid from your distro's packages, see {WAYDROID_INSTALL_DOCS}."
