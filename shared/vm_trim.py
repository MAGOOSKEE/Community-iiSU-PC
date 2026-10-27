"""The "lean VM" profile: strip the Android VM down to what iiSU needs.

iiSU is a launcher. It needs the Files app (Android's folder picker, which
is how it gets read access to the ROM folder), a browser, the Play Store with
the Google services it depends on, a controller-friendly keyboard, and the
core system. Everything else Google's Play system image ships (Maps, Photos,
Gmail, YouTube, the dialer, Android Auto, the Google app and Assistant, ...)
only costs RAM, boot time, and, because Play keeps updating them, data space.

How it works, and why it is done this way:

- Apps are removed for user 0 with `pm uninstall`. The files stay in
  the read-only system image, so nothing about the image is modified or
  redistributed (Google's image license forbids that), and every removal can be
  undone with `cmd package install-existing`.
- Removal is a curated allow-to-remove list, never "everything not on a keep
  list": the dependency web of an Android system is too tangled to infer, so
  each entry was checked on a real image (see tests/test_vm_trim.py for the
  guard that keeps protected packages out of every tier).
- Only packages that exist on the running image are touched, so the same
  profile works on the Play, Google APIs, and stock AOSP images.

Pure logic only (no adb), so it is unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# == LeanKeyboard ==

LEAN_KEYBOARD_PACKAGE = "org.liskovsoft.androidtv.rukeyboard"
LEAN_KEYBOARD_IME = "org.liskovsoft.androidtv.rukeyboard/com.liskovsoft.leankeyboard.ime.LeanbackImeService"


@dataclass(frozen=True)
class PinnedApk:
    """A third-party APK Setup downloads. The file hash pins the exact build;
    the signer pin means even a re-uploaded file under the same name can't
    slip in a differently-signed app."""

    package: str
    version: str
    url: str
    sha256: str
    signer_sha256: str


LEAN_KEYBOARD = PinnedApk(
    package=LEAN_KEYBOARD_PACKAGE,
    version="6.1.31",
    url="https://github.com/yuliskov/LeanKeyboard/releases/download/6.1.31/LeanKeyboard_v6.1.31_playstore_r.apk",
    sha256="5a90529fcae55c664128fb36e752f90e84d158266eced85084594c1d336a1468",
    signer_sha256="955ef7b51f8fb9e4036678471a9edea0bbb3bb9b02753f796d9d6bf19dc2002d",
)

# Gboard stays until LeanKeyboard is installed and selected, so the VM is never
# left with no usable keyboard.
GBOARD = "com.google.android.inputmethod.latin"

# == What to keep (documentation and guard) ==
#
# Nothing here is ever removed. The tiers below are checked against this in the
# tests, so a typo or a future edit can't put one of these in a removal list.

KEEP = frozenset({
    "android",
    "com.android.settings",
    "com.android.systemui",
    "com.android.phone",
    "com.android.server.telecom",
    "com.android.providers.settings",
    "com.android.providers.media",
    "com.android.providers.downloads",
    "com.android.providers.downloads.ui",
    "com.android.providers.contacts",
    "com.android.providers.telephony",
    "com.android.providers.calendar",
    "com.android.providers.userdictionary",
    "com.android.externalstorage",
    "com.google.android.documentsui",  # "Files": the folder picker iiSU needs
    "com.google.android.providers.media.module",
    "com.google.android.photopicker",
    "com.android.chrome",
    "com.android.vending",  # Play Store
    "com.google.android.gms",  # Play services
    "com.google.android.gsf",  # Services Framework
    "com.google.android.webview",
    "com.google.android.configupdater",
    "com.google.android.ext.services",
    "com.google.android.ext.shared",
    "com.google.android.permissioncontroller",
    "com.google.android.packageinstaller",
    "com.android.shell",
    "com.android.keychain",
    "com.android.certinstaller",
    "com.android.inputdevices",
    "com.android.intentresolver",
    "com.android.location.fused",
    "com.android.bluetooth",
    "com.google.android.bluetooth",
    "com.android.mtp",
    "com.android.vpndialogs",
    "com.android.proxyhandler",
    "com.android.pacprocessor",
    "com.android.credentialmanager",
    "com.android.storagemanager",
    "com.google.android.networkstack",
    "com.google.android.captiveportallogin",
    "com.google.android.modulemetadata",
    "com.google.android.rkpdapp",
    "com.google.android.sdksandbox",
    "com.google.android.settings.intelligence",
    # Hard dependency: the system server's DeviceLockService constructor throws without it,
    # which crash-loops the whole VM at boot (found on a scratch VM).
    "com.android.devicelockcontroller",
    LEAN_KEYBOARD_PACKAGE,
    "com.iisulauncher",
})

# Overlays and resource packages: tiny, no processes, and other packages
# depend on them. Never touched.
_PROTECTED_PATTERNS = (
    re.compile(r"\.auto_generated_"),
    re.compile(r"\.overlay\."),
    re.compile(r"\.emulation\."),
    re.compile(r"\.resources(\.|$)"),
    re.compile(r"\.rro"),
    re.compile(r"navbar"),
    re.compile(r"theme\.font"),
    re.compile(r"^com\.google\.mainline\."),
    re.compile(r"\.cutout\."),
)


def is_protected(package: str) -> bool:
    return package in KEEP or any(p.search(package) for p in _PROTECTED_PATTERNS)


# == What can go ==

# Tier "apps": user-facing apps the VM never needs. Safe to remove.
APPS_TIER = (
    "com.google.android.apps.maps",
    "com.google.android.apps.photos",
    "com.google.android.apps.messaging",
    "com.google.android.youtube",
    "com.google.android.apps.youtube.music",
    "com.google.android.gm",
    "com.google.android.calendar",
    "com.google.android.contacts",
    "com.google.android.dialer",
    "com.google.android.deskclock",
    "com.google.android.apps.docs",
    "com.google.android.apps.restore",
    "com.google.android.apps.wellbeing",
    "com.google.android.apps.safetyhub",
    "com.google.android.apps.accessibility.voiceaccess",
    "com.google.android.accessibility.switchaccess",
    "com.google.android.marvin.talkback",
    "com.google.android.marvin.talkbackoverlay",
    "com.google.android.apps.wallpaper",
    "com.google.android.apps.customization.pixel",
    "com.android.wallpaper",
    "com.android.wallpaper.livepicker",
    "com.android.wallpapercropper",
    "com.android.wallpaperbackup",
    "com.google.android.avatarpicker",
    "com.google.android.markup",
    "com.google.android.printservice.recommendation",
    "com.android.bips",
    "com.android.printspooler",
    "com.google.android.projection.gearhead",
    "com.google.android.tts",
    "com.android.camera2",
    "com.android.htmlviewer",
    "com.android.egg",
    "com.android.emergency",
    "com.android.musicfx",
    "com.android.traceur",
    "com.android.dreams.basic",
    "com.android.DeviceAsWebcam",
    "com.android.stk",
    "com.android.soundpicker",
    "com.google.android.soundpicker",
    "com.google.android.apps.nexuslauncher",
    "com.android.virtualization.terminal",
    "com.android.backupconfirm",
    "com.android.sharedstoragebackup",
    "com.android.calllogbackup",
    "com.android.carrierdefaultapp",
)

# Tier "services": background Google services that cost real RAM. Slightly
# more involved, so applied separately and re-verified by the tests on a VM.
SERVICES_TIER = (
    "com.google.android.googlequicksearchbox",  # Google app / Assistant
    "com.google.android.as",  # Android System Intelligence
    "com.google.android.as.oss",
    "com.google.android.federatedcompute",
    "com.google.android.ondevicepersonalization.services",
    "com.google.android.appsearch.apk",
    "com.google.android.health.connect.backuprestore",
    "com.google.android.healthconnect.controller",
    "com.google.android.odad",
    "com.google.android.onetimeinitializer",
    "com.google.android.partnersetup",
    "com.google.android.googlesdksetup",
    "com.google.android.feedback",
    "com.google.android.gms.supervision",
    "com.google.android.cellbroadcastreceiver",
    "com.android.cellbroadcastreceiver",
    "com.android.mms.service",
    "com.android.devicediagnostics",
    "com.android.companiondevicemanager",
    "com.android.nfc",
    "com.google.android.nfc",
    "com.google.android.tag",
)

TIERS = {"apps": APPS_TIER, "services": SERVICES_TIER}
DEFAULT_TIERS = ("apps", "services")

# Image builds (ro.build.fingerprint) the full profile was verified on: removal
# plus a reboot on a fresh VM, then Play Store, Chrome, the Files picker, the
# keyboard and iiSU all checked. On any other build only the low-risk
# user-facing apps are removed, because a dependency like the one DeviceLock
# turned out to be can differ between builds.
VERIFIED_FINGERPRINTS = frozenset({
    "google/sdk_gphone64_x86_64/emu64xa:16/BE2A.250530.026.D1/13818094:user/release-keys",
})


def tiers_for_build(fingerprint: str) -> tuple[str, ...]:
    return DEFAULT_TIERS if fingerprint.strip() in VERIFIED_FINGERPRINTS else ("apps",)


# == Planning ==

def parse_package_list(output: str) -> set[str]:
    """Package names from `pm list packages` output."""
    return {line.strip().removeprefix("package:") for line in output.splitlines() if line.strip().startswith("package:")}


def removal_plan(installed: set[str], tiers: tuple[str, ...] = DEFAULT_TIERS, keyboard_ready: bool = False) -> list[str]:
    """Packages to remove, in a stable order: those in the chosen tiers that
    are actually installed. Gboard is added only once LeanKeyboard is in place
    (keyboard_ready), so the VM can never end up with no keyboard. Protected
    packages are filtered out as a last line of defense."""
    wanted: list[str] = []
    for tier in tiers:
        wanted.extend(TIERS[tier])
    if keyboard_ready:
        wanted.append(GBOARD)
    seen, plan = set(), []
    for package in wanted:
        if package in installed and package not in seen and not is_protected(package):
            seen.add(package)
            plan.append(package)
    return plan


def uninstall_args(package: str) -> list[str]:
    return ["pm", "uninstall", "--user", "0", package]


def restore_args(package: str) -> list[str]:
    return ["cmd", "package", "install-existing", "--user", "0", package]


def is_success(output: str) -> bool:
    """`pm uninstall` prints "Success" or "Failure [reason]"."""
    return "Success" in output


def restore_ok(output: str) -> bool:
    """`cmd package install-existing` prints "Package x installed for user: 0"
    on success (not "Success"), and "Failure ..." otherwise."""
    return "installed for user" in output


def ime_commands(ime_id: str = LEAN_KEYBOARD_IME) -> list[list[str]]:
    """Enable the keyboard, then make it the default."""
    return [["ime", "enable", ime_id], ["ime", "set", ime_id]]
