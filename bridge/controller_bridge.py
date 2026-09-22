"""
Automatic Xbox-compatible controller support for iiSU's own menu navigation.

Polls XInput (covers Xbox controllers whether wired USB or paired over
Bluetooth -- XInput doesn't care about transport) and forwards button
presses into the AVD as Android KeyEvents via a persistent `adb shell`
session, so plugging in or pairing a controller "just works" with no
manual configuration and no USB passthrough / driver swapping.

Scope: XInput only. PlayStation-style controllers (DualShock/DualSense)
enumerate as DirectInput/HID on Windows, not XInput, unless something like
Steam Input or DS4Windows remaps them to XInput -- those aren't covered
here.

Only forwards menu-navigation input while iiSU itself is what you'd be
looking at (no game currently running via the bridge); once a game
launches, the real PC emulator reads the same physical controller directly
through Windows (XInput polling isn't exclusive, so this doesn't conflict
with it), so there's nothing useful for this to send until you're back at
iiSU.

Also watches for a configurable button chord (config.json's
controller_quit_chord, Select+Start by default) pressed together on any
pad, regardless of whether a game is running, and runs the same action a
tap of the keyboard quit hotkey does (see launch_bridge.quit_tap_action):
force-quit the running game and return to iiSU, or close iiSU and shut
down the VM entirely if nothing's running. Fires once per press, not on
a hold, and the chord can be remapped to any combination of buttons from
BUTTON_NAME_TO_BIT via manager.py's Advanced page.

Also separately watches (via the legacy winmm joystick API, not XInput --
see detect_unmapped_sony_controller) for a DualSense/DS4 that's plugged in
but NOT already appearing as an XInput device, and opens Steam for you
when it sees one. There's no documented, stable API to flip Steam's
"PlayStation Configuration Support" setting programmatically -- it lives
in Steam's own config.vdf under an internal key that isn't part of any
public interface and could change or get silently reverted between Steam
versions, so this deliberately doesn't try to write it directly. Opening
Steam gets you one click away from Settings -> Controller -> General
Controller Settings instead, where switching that on is a one-time,
persistent choice covering every DualSense/DS4 from then on.
"""

import ctypes
import subprocess
import time
from ctypes import wintypes

# XINPUT_GAMEPAD.wButtons bitmask
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_LEFT_THUMB = 0x0040
XINPUT_GAMEPAD_RIGHT_THUMB = 0x0080
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_Y = 0x8000

ERROR_SUCCESS = 0

# Android KeyEvent codes -- iiSU listens to these directly (confirmed by its
# own on-screen "A Select / B Back / LB RB ..." gamepad legends).
BUTTON_TO_KEYCODE = {
    XINPUT_GAMEPAD_DPAD_UP: 19,
    XINPUT_GAMEPAD_DPAD_DOWN: 20,
    XINPUT_GAMEPAD_DPAD_LEFT: 21,
    XINPUT_GAMEPAD_DPAD_RIGHT: 22,
    XINPUT_GAMEPAD_A: 96,
    XINPUT_GAMEPAD_B: 97,
    XINPUT_GAMEPAD_X: 99,
    XINPUT_GAMEPAD_Y: 100,
    XINPUT_GAMEPAD_LEFT_SHOULDER: 102,
    XINPUT_GAMEPAD_RIGHT_SHOULDER: 103,
    XINPUT_GAMEPAD_LEFT_THUMB: 106,
    XINPUT_GAMEPAD_RIGHT_THUMB: 107,
    XINPUT_GAMEPAD_START: 108,
    XINPUT_GAMEPAD_BACK: 109,
}
KEYCODE_BUTTON_L2 = 104
KEYCODE_BUTTON_R2 = 105
KEYCODE_DPAD_UP, KEYCODE_DPAD_DOWN, KEYCODE_DPAD_LEFT, KEYCODE_DPAD_RIGHT = 19, 20, 21, 22

REPEATABLE = {KEYCODE_DPAD_UP, KEYCODE_DPAD_DOWN, KEYCODE_DPAD_LEFT, KEYCODE_DPAD_RIGHT}
INITIAL_REPEAT_DELAY = 0.4
REPEAT_INTERVAL = 0.12
TRIGGER_THRESHOLD = 128
STICK_DEADZONE = 12000
POLL_HZ = 60

# Every button the quit chord can be remapped to, by the name manager.py's
# Advanced page uses. "select" matches XInput's BACK bit, which different
# controller generations label Back, View, or Select -- same physical
# button, same bit, just a naming difference.
BUTTON_NAME_TO_BIT = {
    "dpad_up": XINPUT_GAMEPAD_DPAD_UP,
    "dpad_down": XINPUT_GAMEPAD_DPAD_DOWN,
    "dpad_left": XINPUT_GAMEPAD_DPAD_LEFT,
    "dpad_right": XINPUT_GAMEPAD_DPAD_RIGHT,
    "start": XINPUT_GAMEPAD_START,
    "select": XINPUT_GAMEPAD_BACK,
    "left_stick": XINPUT_GAMEPAD_LEFT_THUMB,
    "right_stick": XINPUT_GAMEPAD_RIGHT_THUMB,
    "left_shoulder": XINPUT_GAMEPAD_LEFT_SHOULDER,
    "right_shoulder": XINPUT_GAMEPAD_RIGHT_SHOULDER,
    "a": XINPUT_GAMEPAD_A,
    "b": XINPUT_GAMEPAD_B,
    "x": XINPUT_GAMEPAD_X,
    "y": XINPUT_GAMEPAD_Y,
}
DEFAULT_QUIT_CHORD = ["select", "start"]

# Short, standard Xbox-pad labels for the names above -- matches iiSU's own
# on-screen gamepad legend (see the BUTTON_TO_KEYCODE comment), and far more
# compact than a plain name.replace("_", " ").title() would be (e.g. "Left
# Stick" / "Right Shoulder"), which matters for manager.py's Advanced page
# where all 14 need to fit in a tight checkbox grid.
BUTTON_DISPLAY_NAMES = {
    "dpad_up": "D-Pad Up",
    "dpad_down": "D-Pad Down",
    "dpad_left": "D-Pad Left",
    "dpad_right": "D-Pad Right",
    "start": "Start",
    "select": "Select",
    "left_stick": "LS",
    "right_stick": "RS",
    "left_shoulder": "LB",
    "right_shoulder": "RB",
    "a": "A",
    "b": "B",
    "x": "X",
    "y": "Y",
}


def quit_chord_mask(button_names: list[str]) -> int:
    mask = 0
    for name in button_names:
        mask |= BUTTON_NAME_TO_BIT.get(name, 0)
    return mask

SONY_CONTROLLER_CHECK_INTERVAL = 3.0  # seconds -- winmm enumeration, not worth doing at POLL_HZ
MAXPNAMELEN = 32
MAX_JOYSTICKOEMVXDNAME = 260
JOYERR_NOERROR = 0
SONY_VID = 0x054C
SONY_PS_CONTROLLER_PIDS = {
    0x05C4: "DualShock 4",
    0x09CC: "DualShock 4 (v2)",
    0x0BA0: "DualShock 4 (wireless dongle)",
    0x0CE6: "DualSense",
    0x0DF2: "DualSense Edge",
}


class JoyCapsW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("wXmin", wintypes.UINT), ("wXmax", wintypes.UINT),
        ("wYmin", wintypes.UINT), ("wYmax", wintypes.UINT),
        ("wZmin", wintypes.UINT), ("wZmax", wintypes.UINT),
        ("wNumButtons", wintypes.UINT),
        ("wPeriodMin", wintypes.UINT), ("wPeriodMax", wintypes.UINT),
        ("wRmin", wintypes.UINT), ("wRmax", wintypes.UINT),
        ("wUmin", wintypes.UINT), ("wUmax", wintypes.UINT),
        ("wVmin", wintypes.UINT), ("wVmax", wintypes.UINT),
        ("wCaps", wintypes.UINT),
        ("wMaxAxes", wintypes.UINT), ("wNumAxes", wintypes.UINT), ("wMaxButtons", wintypes.UINT),
        ("szRegKey", wintypes.WCHAR * MAXPNAMELEN),
        ("szOEMVxD", wintypes.WCHAR * MAX_JOYSTICKOEMVXDNAME),
    ]


_winmm = ctypes.windll.winmm
_winmm.joyGetNumDevs.restype = wintypes.UINT
_winmm.joyGetDevCapsW.argtypes = [ctypes.c_uint, ctypes.POINTER(JoyCapsW), ctypes.c_uint]
_winmm.joyGetDevCapsW.restype = wintypes.UINT


def find_unmapped_sony_controllers() -> list[str]:
    """Enumerates legacy joystick devices (winmm's joyGetDevCapsW) rather
    than XInput -- a DualSense/DS4 shows up *here* when it's plugged in
    directly with nothing translating it, which is exactly the condition
    worth catching: if it already worked as an XInput device, Steam Input
    (or DS4Windows, etc.) is already doing that job and there's nothing to
    fix. Returns the product name of every Sony PlayStation controller
    found this way (there can be more than one)."""
    found = []
    caps = JoyCapsW()
    for joy_id in range(_winmm.joyGetNumDevs()):
        if _winmm.joyGetDevCapsW(joy_id, ctypes.byref(caps), ctypes.sizeof(caps)) != JOYERR_NOERROR:
            continue
        if caps.wMid == SONY_VID and caps.wPid in SONY_PS_CONTROLLER_PIDS:
            found.append(SONY_PS_CONTROLLER_PIDS[caps.wPid])
    return found


def find_steam_exe() -> str | None:
    """Steam's own install path, from the registry key it writes itself on
    install -- more reliable than guessing a Program Files location, since
    Steam can be installed anywhere the person chose."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return winreg.QueryValueEx(key, "SteamExe")[0]
    except OSError:
        return None


def open_steam_for_controller_setup(controller_name: str) -> None:
    """Opens Steam -- just the client, no documented way to jump straight
    to Controller settings (see this module's docstring for why this
    doesn't try to flip the underlying setting itself) -- so a detected
    DualSense/DS4 that isn't already working as an XInput device is one
    click away from being fixed."""
    steam_exe = find_steam_exe()
    if steam_exe is None:
        print(
            f"[controller] {controller_name} detected, but Steam doesn't appear to be installed -- install it and "
            "enable \"PlayStation Configuration Support\" under Settings > Controller for it to work with iiSU"
        )
        return
    print(
        f"[controller] {controller_name} detected -- opening Steam. Enable \"PlayStation Configuration Support\" "
        "under Settings > Controller > General Controller Settings (one-time, covers every DualSense/DS4 from then on)"
    )
    subprocess.Popen([steam_exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class XinputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XinputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint32), ("Gamepad", XinputGamepad)]


def _load_xinput():
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            return ctypes.windll.LoadLibrary(name)
        except OSError:
            continue
    return None


_xinput = _load_xinput()
if _xinput is not None:
    _xinput.XInputGetState.argtypes = [ctypes.c_uint32, ctypes.POINTER(XinputState)]
    _xinput.XInputGetState.restype = ctypes.c_uint32


def get_gamepad_state(slot: int) -> XinputGamepad | None:
    if _xinput is None:
        return None
    state = XinputState()
    if _xinput.XInputGetState(slot, ctypes.byref(state)) == ERROR_SUCCESS:
        return state.Gamepad
    return None


class ControllerBridge:
    """is_game_running: zero-arg callable returning True while a real PC
    emulator is active, so menu-nav forwarding pauses instead of fighting
    for input. on_quit: zero-arg callable that runs the same action a
    keyboard quit-hotkey tap does, invoked once whenever every button in
    quit_chord_buttons is pressed together on the same pad (see module
    docstring). quit_chord_buttons is a list of BUTTON_NAME_TO_BIT keys,
    defaulting to Select+Start."""

    def __init__(self, is_game_running, on_quit, quit_chord_buttons: list[str] | None = None):
        self.is_game_running = is_game_running
        self.on_quit = on_quit
        self._quit_chord_mask = quit_chord_mask(quit_chord_buttons or DEFAULT_QUIT_CHORD)
        self._quit_chord_label = "+".join(
            BUTTON_DISPLAY_NAMES.get(name, name) for name in (quit_chord_buttons or DEFAULT_QUIT_CHORD)
        )
        self._adb_shell: subprocess.Popen | None = None
        self._connected_slots: set[int] = set()
        self._held_since: dict[tuple[int, int], float] = {}
        self._last_repeat: dict[tuple[int, int], float] = {}
        self._quit_chord_fired: set[int] = set()
        self._sony_controller_check_due = 0.0
        self._sony_controllers_prompted: set[str] = set()

    def _ensure_shell(self) -> None:
        if self._adb_shell is None or self._adb_shell.poll() is not None:
            self._adb_shell = subprocess.Popen(
                ["adb", "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _send_keyevent(self, code: int) -> None:
        self._ensure_shell()
        try:
            self._adb_shell.stdin.write(f"input keyevent {code}\n".encode())
            self._adb_shell.stdin.flush()
        except (BrokenPipeError, OSError):
            self._adb_shell = None

    @staticmethod
    def _digital_buttons(pad: XinputGamepad) -> set[int]:
        pressed = {code for bit, code in BUTTON_TO_KEYCODE.items() if pad.wButtons & bit}
        if pad.bLeftTrigger > TRIGGER_THRESHOLD:
            pressed.add(KEYCODE_BUTTON_L2)
        if pad.bRightTrigger > TRIGGER_THRESHOLD:
            pressed.add(KEYCODE_BUTTON_R2)
        # Thumbsticks double as D-pad for menu navigation, past a deadzone.
        if pad.sThumbLY > STICK_DEADZONE or pad.sThumbRY > STICK_DEADZONE:
            pressed.add(KEYCODE_DPAD_UP)
        if pad.sThumbLY < -STICK_DEADZONE or pad.sThumbRY < -STICK_DEADZONE:
            pressed.add(KEYCODE_DPAD_DOWN)
        if pad.sThumbLX < -STICK_DEADZONE or pad.sThumbRX < -STICK_DEADZONE:
            pressed.add(KEYCODE_DPAD_LEFT)
        if pad.sThumbLX > STICK_DEADZONE or pad.sThumbRX > STICK_DEADZONE:
            pressed.add(KEYCODE_DPAD_RIGHT)
        return pressed

    def _check_quit_chord(self, slot: int, pad: XinputGamepad) -> None:
        """Fires on_quit() once per press of the configured chord, not on
        a hold: the chord becoming fully pressed when it wasn't a moment
        ago. _quit_chord_fired tracks that edge per pad so holding the
        chord down doesn't repeat the action every poll tick, and clears
        once any button in it is released so the next press fires again."""
        if self._quit_chord_mask == 0:
            return
        fully_pressed = pad.wButtons & self._quit_chord_mask == self._quit_chord_mask
        if not fully_pressed:
            self._quit_chord_fired.discard(slot)
            return
        if slot not in self._quit_chord_fired:
            self._quit_chord_fired.add(slot)
            self.on_quit(f"{self._quit_chord_label} pressed")

    def _forward_navigation(self, slot: int, pad: XinputGamepad, now: float) -> None:
        pressed = self._digital_buttons(pad)

        for key in [k for k in self._held_since if k[0] == slot and k[1] not in pressed]:
            del self._held_since[key]
            self._last_repeat.pop(key, None)

        for code in pressed:
            key = (slot, code)
            if key not in self._held_since:
                self._held_since[key] = now
                self._send_keyevent(code)
            elif code in REPEATABLE:
                held_for = now - self._held_since[key]
                last = self._last_repeat.get(key, self._held_since[key])
                if held_for >= INITIAL_REPEAT_DELAY and now - last >= REPEAT_INTERVAL:
                    self._send_keyevent(code)
                    self._last_repeat[key] = now

    def _check_sony_controllers(self, now: float) -> None:
        """Throttled to once every SONY_CONTROLLER_CHECK_INTERVAL -- winmm
        device enumeration is cheap but pointless to redo at POLL_HZ.
        Prompts (opens Steam) once per distinct controller name while it
        stays plugged in and un-translated, forgetting it once it
        disappears so unplugging and replugging -- or a later session,
        since this state doesn't persist anywhere -- prompts again if it's
        still not fixed."""
        if now < self._sony_controller_check_due:
            return
        self._sony_controller_check_due = now + SONY_CONTROLLER_CHECK_INTERVAL
        found = set(find_unmapped_sony_controllers())
        for name in found - self._sony_controllers_prompted:
            open_steam_for_controller_setup(name)
        self._sony_controllers_prompted = found

    def run(self) -> None:
        if _xinput is None:
            print("[controller] XInput not available on this system; controller support disabled")
            return
        print("[controller] watching for Xbox-compatible controllers (wired or Bluetooth)")
        if self._quit_chord_mask:
            print(f"[controller] {self._quit_chord_label} on any pad triggers the same action as the keyboard quit hotkey")
        while True:
            now = time.time()
            self._check_sony_controllers(now)
            game_running = self.is_game_running()
            for slot in range(4):
                pad = get_gamepad_state(slot)
                if pad is None:
                    if slot in self._connected_slots:
                        print(f"[controller] slot {slot} disconnected")
                        self._connected_slots.discard(slot)
                    self._quit_chord_fired.discard(slot)
                    continue
                if slot not in self._connected_slots:
                    print(f"[controller] slot {slot} connected (wired or Bluetooth, detected automatically)")
                    self._connected_slots.add(slot)

                # Checked regardless of game state -- this is a local
                # quit/shutdown action, not something forwarded into
                # Android, so there's no reason to gate it on iiSU being
                # the thing currently in focus.
                self._check_quit_chord(slot, pad)

                if not game_running:
                    self._forward_navigation(slot, pad, now)
            time.sleep(1 / POLL_HZ)
