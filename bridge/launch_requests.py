"""What the patched iiSU sends the launch bridge, and how to read it.

Each launch arrives over a local socket as the text of the Android Intent
iiSU was about to start (Intent.toString(): its component, action,
categories, flags, data and extras), ended by a marker line. The bridge
answers with one byte saying whether the PC took the launch. This module
holds that small protocol and the parsing of the Intent text: pure text
handling, no state, so it can be read and tested on its own. What the
bridge then does with a launch stays in launch_bridge.py.
"""

from __future__ import annotations

import re
from typing import Callable

from shared import adb_client

INTENT_CMP_RE = re.compile(r"cmp=(\S+)")
INTENT_DAT_RE = re.compile(r"dat=(\S+)")

# iiSU's own default emulator list routes Steam/GOG/Epic-style entries to
# GameNative, an Android app that runs Windows PC games under Wine/Box64,
# irrelevant here, since this project always has the real thing (Steam
# itself) available PC-side already. Its launch command
# (emuladores_default.json's "%PACKAGE%/.MainActivity -a
# app.gamenative.LAUNCH_GAME -e app_id %GAMENATIVE_APP_ID_INT%") passes the
# Steam App ID as a plain int Intent extra, not a file path or ClipData URI,
# there's no "rom" to find under roms_dir at all, unlike every other
# entry in config.json's emulators map, so this is handled as its own
# special case below rather than forced into the exe_names/rom_path shape
# every other profile uses.
GAMENATIVE_PACKAGE = "app.gamenative"
WINDOWS_LAUNCH_PACKAGES = {"com.winlator.cmod", "com.winlator", "com.cmodded.winlator"}

# The one byte answered to the patched iiSU after each launch (see
# installer/smali_patch/LaunchBridge$1.smali). "A" hands the launch back to
# Android unchanged; "P" means the PC took it.
REPLY_LAUNCH_ON_ANDROID = b"A"
REPLY_HANDLED_ON_PC = b"P"
# The line a patched iiSU adds to every launch once it reads that answer. An
# iiSU patched before then hangs up without reading it, so for that one the
# bridge starts the Android app itself, and nobody has to re-patch.
REPLY_CAPABLE_LINE = "BRIDGE_REPLY:1"
# The line that ends a request from an iiSU with patch 6 or later. Earlier
# patches ended theirs by half-closing the connection, which the bridge still
# accepts; over the adb route patch 6 uses, a half-close would close the
# connection both ways and lose the answer.
REQUEST_END_LINE = b"\nBRIDGE_END:1"
# A connection that neither ends its request nor closes is dropped after this
# long, so one stuck client can't stop the bridge taking launches.
REQUEST_TIMEOUT_SECONDS = 15.0
INTENT_ACT_RE = re.compile(r"\bact=(\S+)")
INTENT_CAT_RE = re.compile(r"\bcat=\[([^\]]*)\]")
INTENT_FLG_RE = re.compile(r"\bflg=(0x[0-9a-fA-F]+)")


def read_request(conn, log: Callable[[str], None] | None = None) -> str:
    """One launch request from a connection: everything up to its end line
    (patch 6 on) or up to the sender closing its side (older patches). The
    end line itself is left out. log hears about a request cut short."""
    conn.settimeout(REQUEST_TIMEOUT_SECONDS)
    data = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            end = data.find(REQUEST_END_LINE)
            if end != -1:
                data = data[:end]
                break
    except (TimeoutError, OSError):
        if log is not None:
            log("a launch request stopped arriving before it was complete; using what came")
    return data.decode("utf-8", errors="replace")


def launch_package(raw_intent: str) -> str | None:
    cmp_match = INTENT_CMP_RE.search(raw_intent)
    return cmp_match.group(1).split("/")[0] if cmp_match else None


def belongs_on_android(raw_intent: str, config: dict) -> bool:
    """Whether a launch from iiSU is an Android app no PC program stands in
    for, such as Chrome or the Play Store. iiSU sends those through the same
    call as games, so the patch used to send them here, where they failed
    with "no known PC emulator mapped" and could never be opened. Anything
    this bridge would handle stays on the PC: a mapped emulator package,
    Winlator for Windows apps, or GameNative for Steam."""
    package = launch_package(raw_intent)
    if package is None:
        return True
    if package in WINDOWS_LAUNCH_PACKAGES or package == GAMENATIVE_PACKAGE:
        return False
    return not any(package.startswith(prefix) for prefix in config.get("emulators", {}))


def android_start_command(raw_intent: str) -> str | None:
    """The `am start` that opens an app the way iiSU asked to: its component,
    action, categories and flags, all of which Intent.toString() shows in
    full. None when the launch names no component to start."""
    cmp_match = INTENT_CMP_RE.search(raw_intent)
    if not cmp_match:
        return None
    args = ["am", "start", "-n", cmp_match.group(1)]
    if act := INTENT_ACT_RE.search(raw_intent):
        args += ["-a", act.group(1)]
    if cat := INTENT_CAT_RE.search(raw_intent):
        for category in filter(None, (c.strip() for c in cat.group(1).split(","))):
            args += ["-c", category]
    if flg := INTENT_FLG_RE.search(raw_intent):
        args += ["-f", flg.group(1)]
    return " ".join(adb_client.quote(arg) for arg in args)
