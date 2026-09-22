"""
iiSU-PC Manager: the single day-to-day app for iiSU-PC -- home status/
Start/Stop, every config.json setting, and uninstall, unified behind one
sidebar instead of three separate windows (this replaces control_panel.py
and config_editor.py; see git history for either's old standalone form).

Setup itself stays a genuinely separate window (installer/setup_gui.py,
launched as its own process from the Home page) rather than a sidebar page
-- a one-time install wizard is a different shape of problem than settings
you come back to, the same reasoning onboarding_wizard.py's own docstring
already applies to first-run configuration.

Stdlib only (tkinter) for the app itself; Pillow is used opportunistically
for the Credits page's circular GitHub avatars and degrades to a plain
colored circle if it isn't installed or the fetch fails (see
shared/avatars.py) -- same "cosmetic nice-to-have degrades quietly"
approach create_shortcut.py already takes for iiSU's own icon.
"""

import json
import urllib.request
import hashlib
import shutil
import uuid
import atexit
import zipfile
import socket
import os
import queue
import re
import subprocess
import tempfile
import time
import sys
import traceback
from datetime import datetime
import sys
import threading
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


MANAGER_LOG_PATH = Path(__file__).resolve().parent / "manager_debug.log"


def _manager_log_write(message: str) -> None:
    """Append one timestamped diagnostic entry without depending on stdout."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with MANAGER_LOG_PATH.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
            log_file.flush()
    except Exception:
        pass


class _ManagerTee:
    """Mirror console output to manager_debug.log while preserving the console."""
    def __init__(self, original, stream_name: str):
        self.original = original
        self.stream_name = stream_name
        self._buffer = ""

    def write(self, data):
        text = "" if data is None else str(data)
        try:
            if self.original is not None:
                self.original.write(text)
                self.original.flush()
        except Exception:
            pass

        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                _manager_log_write(f"{self.stream_name}: {line}")
        return len(text)

    def flush(self):
        try:
            if self.original is not None:
                self.original.flush()
        except Exception:
            pass
        if self._buffer:
            _manager_log_write(f"{self.stream_name}: {self._buffer}")
            self._buffer = ""

    def isatty(self):
        try:
            return bool(self.original and self.original.isatty())
        except Exception:
            return False

    def fileno(self):
        if self.original is None:
            raise OSError("No underlying console stream")
        return self.original.fileno()

    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")


_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr
sys.stdout = _ManagerTee(_ORIGINAL_STDOUT, "STDOUT")
sys.stderr = _ManagerTee(_ORIGINAL_STDERR, "STDERR")


def _manager_uncaught_exception(exc_type, exc_value, exc_tb):
    try:
        formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
        _manager_log_write("UNCAUGHT EXCEPTION\n" + formatted)
    finally:
        try:
            if _ORIGINAL_STDERR is not None:
                traceback.print_exception(exc_type, exc_value, exc_tb, file=_ORIGINAL_STDERR)
        except Exception:
            pass


sys.excepthook = _manager_uncaught_exception

# Python 3.8+: capture uncaught exceptions in worker threads too.
if hasattr(threading, "excepthook"):
    def _manager_thread_exception(args):
        try:
            formatted = "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            ).rstrip()
            _manager_log_write(
                f"UNCAUGHT THREAD EXCEPTION ({getattr(args.thread, 'name', 'unknown')})\n{formatted}"
            )
        except Exception:
            pass
    threading.excepthook = _manager_thread_exception


_manager_log_write("=" * 72)
_manager_log_write("MANAGER START")
_manager_log_write(f"Python: {sys.version.replace(chr(10), ' ')}")
_manager_log_write(f"Executable: {sys.executable}")
_manager_log_write(f"Manager: {Path(__file__).resolve()}")
_manager_log_write(f"Working directory: {Path.cwd()}")

def _manager_log_shutdown():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    _manager_log_write("MANAGER EXIT (normal interpreter shutdown)")


atexit.register(_manager_log_shutdown)

import winapi
from bridge_config import CONFIG_PATH, load_config
from console_names import load_console_lookup, resolve_console_shortname
from emulator_dialogs import EmulatorDialog, RedirectorInstallDialog
from launch_bridge import find_emulator_for_package, find_executable, find_rom

import start_iisu_pc
import stop_iisu_pc

BRIDGE_DIR = Path(__file__).parent
PROJECT_ROOT = BRIDGE_DIR.parent
INSTALLER_DIR = PROJECT_ROOT / "installer"
WINDOWS_APPS_PATH = BRIDGE_DIR / "windows_apps.json"
IIDB_DIR = BRIDGE_DIR / "iidb"
IIDB_LIBRARY_DIR = IIDB_DIR / "library"
IIDB_REGISTRY_PATH = IIDB_DIR / "installed_media.json"
IIDB_API_BASE = "https://iidb.iisu.network/api/v1"
IIDB_THUMB_CACHE_DIR = IIDB_DIR / "cache" / "thumbnails"
IIDB_AUDIO_CACHE_DIR = IIDB_DIR / "cache" / "audio"
MEDIABRIDGE_INBOX = "/storage/emulated/0/Android/media/com.iisulauncher/iiSULauncher/mediabridge/inbox"
MEDIABRIDGE_COMPONENT = "com.iisulauncher/com.iisulauncher.pcbridge.MediaBridgeReceiver"
MEDIABRIDGE_INSTALL_ACTION = "com.iisulauncher.pcbridge.INSTALL_ROM_ASSET"
MEDIABRIDGE_PING_ACTION = "com.iisulauncher.pcbridge.PING"
MEDIABRIDGE_RESCAN_ACTION = "com.iisulauncher.pcbridge.RESCAN_LIBRARY"

sys.path.insert(0, str(PROJECT_ROOT))
from shared import theme
from shared.avatars import fetch_avatar_bytes, make_circular_photo, make_placeholder_circle
from shared.emulator_defaults import build_emulators_map, describe_profile
from shared.theme import (
    BG, ENTRY_KWARGS, GRADIENT_STOPS, GRAY, GREEN, LISTBOX_KWARGS, PANEL_BG, PANEL_BG_HOVER,
    RED, TEXT, TEXT_DIM, FONT_BODY, FONT_HEADING, FONT_MONO, FONT_TITLE, Card, QueueWriter, draw_gradient_bar, draw_menu_icon,
)

sys.path.insert(0, str(INSTALLER_DIR))
import uninstall as uninstall_cli

RESOLUTION_PRESETS = ["1280 x 720", "1600 x 900", "1920 x 1080", "2560 x 1440", "3840 x 2160"]
REFRESH_RATE_PRESETS = ["60", "90", "120", "144", "165", "240"]
GPU_MODE_PRESETS = ["auto", "host", "swiftshader_indirect", "angle_indirect"]

# This project's known-good baseline profile (matches installer/setup_
# wizard.py's DEFAULT_DISPLAY) -- _autodetect_display scales density
# relative to this, not to any fixed Android density bucket, since the
# goal is "looks the same as it does at 1920x1080@240dpi," not matching
# a real handheld device's physical DPI.
REFERENCE_DISPLAY = {"width": 1920, "height": 1080, "density": 240}
MODIFIER_NAMES = ["ctrl", "alt", "shift", "win"]

STATUS_POLL_INTERVAL_MS = 2000

NAV_ITEMS = [
    ("home", "\U0001F3E0", "Home"),
    ("roms", "\U0001F4C1", "ROM Directory"),
    ("emulators", "\U0001F3AE", "Emulators"),
    ("windows_apps", "\U0001FA9F", "Windows Apps"),
    ("media_library", "\U0001F5BC", "Media Library"),
    ("android_storage", "\U0001F4F1", "Android Storage"),
    ("display", "\U0001F5A5", "Display"),
    ("backup_restore", "\U0001F4BE", "Backup & Restore"),
    ("advanced", "⚙", "Advanced"),
    ("diagnostics", "\U0001F50D", "Diagnostics"),
    ("credits", "❤", "Credits"),
]
DANGER_NAV_ITEMS = [
    ("uninstall", "\U0001F5D1", "Uninstall"),
]
SETTINGS_PAGES = {"roms", "emulators", "display", "advanced"}

SIDEBAR_WIDTH_EXPANDED = 200
SIDEBAR_WIDTH_COLLAPSED = 56


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


class StatusDot(tk.Canvas):
    def __init__(self, parent, size=10):
        super().__init__(parent, width=size, height=size, bg=PANEL_BG, highlightthickness=0)
        self.size = size
        self.set_state("unknown")

    def set_state(self, state: str) -> None:
        color = {"up": GREEN, "down": RED, "unknown": GRAY}.get(state, GRAY)
        self.delete("all")
        pad = 1
        self.create_oval(pad, pad, self.size - pad, self.size - pad, fill=color, outline="")


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("iiSU-PC Manager")
        self.geometry("1000x700")
        self.minsize(880, 620)
        self.configure(bg=BG)

        self.config_data: dict = {}
        self.configured = False
        self.busy = False
        self.sidebar_expanded = True
        self.current_page = "home"
        self.log_queue: queue.Queue = queue.Queue()
        self.uninstall_log_queue: queue.Queue = queue.Queue()
        self._last_avd_up: bool | None = None
        self._last_bridge_up: bool | None = None
        self._media_ping_inflight = False
        self._media_last_ping_at = 0.0
        self._setup_process: subprocess.Popen | None = None
        self._uninstall_targets_cache: list[Path] = []
        self.nav_buttons: dict[str, tk.Label] = {}
        self.pages: dict[str, tk.Frame] = {}

        self._configure_style()
        self._reload_config()
        self._build_ui()
        self._refresh_nav_enabled()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_page("home")
        self.after(100, self._poll_log_queue)
        self.after(100, self._poll_uninstall_log_queue)
        self.after(200, self._poll_status)

    # -- Style -------------------------------------------------

    def _configure_style(self) -> None:
        theme.apply_ttk_styles(ttk.Style(self))

    # -- Config state -------------------------------------------------

    def _reload_config(self) -> None:
        if CONFIG_PATH.is_file():
            try:
                self.config_data = load_config()
                self.configured = True
                return
            except Exception:
                pass
        self.config_data = {}
        self.configured = False

    # -- Chrome: sidebar + page container -------------------------------------------------

    def _build_ui(self) -> None:
        root_row = tk.Frame(self, bg=BG)
        root_row.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(root_row, bg=PANEL_BG, width=SIDEBAR_WIDTH_EXPANDED)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        content_col = tk.Frame(root_row, bg=BG)
        content_col.pack(side="left", fill="both", expand=True)

        self.page_container = tk.Frame(content_col, bg=BG)
        self.page_container.pack(side="top", fill="both", expand=True)
        self.page_container.grid_rowconfigure(0, weight=1)
        self.page_container.grid_columnconfigure(0, weight=1)

        for key, _icon, _label in NAV_ITEMS + DANGER_NAV_ITEMS:
            page = tk.Frame(self.page_container, bg=BG)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page

        self.save_bar = tk.Frame(content_col, bg=BG)
        self.save_button = ttk.Button(self.save_bar, text="Save", style="Accent.TButton", command=self._save_settings)
        self.save_button.pack(side="right", padx=24, pady=14)
        self.save_status_label = tk.Label(self.save_bar, text="", bg=BG, fg=GREEN, font=FONT_BODY)
        self.save_status_label.pack(side="left", padx=24, pady=14)

        self._build_home_page()
        self._build_settings_pages()
        self._build_windows_apps_page()
        self._build_media_library_page()
        self._build_diagnostics_page()
        self._build_backup_restore_page()
        self._build_android_storage_page()
        self._build_credits_page()
        self._build_uninstall_page()

    def _build_sidebar(self) -> None:
        header = tk.Frame(self.sidebar, bg=PANEL_BG)
        header.pack(fill="x", pady=(16, 10))
        hamburger_size = 20
        hamburger = tk.Canvas(header, width=hamburger_size, height=hamburger_size, bg=PANEL_BG, highlightthickness=0, cursor="hand2")
        draw_menu_icon(hamburger, hamburger_size, TEXT)
        hamburger.pack(side="left", padx=(16, 10))
        hamburger.bind("<Button-1>", lambda e: self._toggle_sidebar())
        self.sidebar_title_label = tk.Label(header, text="iiSU-PC", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT)
        self.sidebar_title_label.pack(side="left")

        nav_frame = tk.Frame(self.sidebar, bg=PANEL_BG)
        nav_frame.pack(fill="x", side="top")
        for key, icon, label in NAV_ITEMS:
            self._add_nav_button(nav_frame, key, icon, label)

        danger_frame = tk.Frame(self.sidebar, bg=PANEL_BG)
        danger_frame.pack(fill="x", side="bottom", pady=(0, 12))
        tk.Frame(danger_frame, bg=PANEL_BG_HOVER, height=1).pack(fill="x", padx=14, pady=(0, 8))
        for key, icon, label in DANGER_NAV_ITEMS:
            self._add_nav_button(danger_frame, key, icon, label, danger=True)

    def _add_nav_button(self, parent, key: str, icon: str, label: str, danger: bool = False) -> None:
        btn = tk.Label(
            parent, text=f"{icon}  {label}", font=FONT_BODY, bg=PANEL_BG, fg=(RED if danger else TEXT),
            anchor="w", padx=16, pady=10, cursor="hand2",
        )
        btn.pack(fill="x")
        btn.bind("<Button-1>", lambda e, k=key: self._on_nav_click(k))
        btn.bind("<Enter>", lambda e, b=btn, k=key: b.config(bg=PANEL_BG_HOVER))
        btn.bind("<Leave>", lambda e, b=btn, k=key: b.config(bg=PANEL_BG_HOVER if self.current_page == k else PANEL_BG))
        self.nav_buttons[key] = btn

    def _toggle_sidebar(self) -> None:
        self.sidebar_expanded = not self.sidebar_expanded
        self.sidebar.config(width=SIDEBAR_WIDTH_EXPANDED if self.sidebar_expanded else SIDEBAR_WIDTH_COLLAPSED)
        self.sidebar_title_label.config(text="iiSU-PC" if self.sidebar_expanded else "")
        for key, icon, label in NAV_ITEMS + DANGER_NAV_ITEMS:
            self.nav_buttons[key].config(text=f"{icon}  {label}" if self.sidebar_expanded else icon)

    def _on_nav_click(self, key: str) -> None:
        if key in SETTINGS_PAGES and not self.configured:
            return
        self._show_page(key)

    def _refresh_nav_enabled(self) -> None:
        for key in SETTINGS_PAGES:
            btn = self.nav_buttons[key]
            btn.config(fg=TEXT if self.configured else TEXT_DIM, cursor="hand2" if self.configured else "arrow")

    def _show_page(self, key: str) -> None:
        self.current_page = key
        for k, btn in self.nav_buttons.items():
            btn.config(bg=PANEL_BG_HOVER if k == key else PANEL_BG)
        self.pages[key].tkraise()
        if key in SETTINGS_PAGES:
            self.save_bar.pack(side="bottom", fill="x")
        else:
            self.save_bar.pack_forget()
        if key == "uninstall":
            self._refresh_uninstall_preview()
        elif key == "android_storage":
            self._android_storage_refresh()
        elif key == "media_library":
            self._media_library_refresh()

    # -- Home page -------------------------------------------------

    def _build_home_page(self) -> None:
        page = self.pages["home"]
        header = tk.Frame(page, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(header, text="iiSU-PC", font=FONT_TITLE, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(header, text="Android frontend, real PC emulators.", font=FONT_BODY, bg=BG, fg=TEXT_DIM).pack(anchor="w")

        gradient = tk.Canvas(page, height=3, bg=BG, highlightthickness=0)
        gradient.pack(fill="x", padx=24, pady=(0, 14))
        self.after(10, lambda: draw_gradient_bar(gradient, gradient.winfo_width() or 900, 3))
        page.bind("<Configure>", lambda e: draw_gradient_bar(gradient, gradient.winfo_width(), 3))

        self.status_card = Card(page)
        status_inner = tk.Frame(self.status_card, bg=PANEL_BG)
        status_inner.pack(fill="x", padx=16, pady=14)
        self.avd_dot = StatusDot(status_inner)
        self.avd_dot.grid(row=0, column=0, padx=(0, 8))
        tk.Label(status_inner, text="Android VM", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).grid(row=0, column=1, sticky="w")
        self.avd_status_label = tk.Label(status_inner, text="checking...", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM)
        self.avd_status_label.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.bridge_dot = StatusDot(status_inner)
        self.bridge_dot.grid(row=1, column=0, padx=(0, 8), pady=(8, 0))
        tk.Label(status_inner, text="Launch bridge", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.bridge_status_label = tk.Label(status_inner, text="checking...", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM)
        self.bridge_status_label.grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(8, 0))
        status_inner.grid_columnconfigure(2, weight=1)

        self.setup_intro_label = tk.Label(
            page,
            text="iiSU-PC hasn't been set up yet. Setup installs a self-contained Android VM and\n"
            "patches your copy of iiSU to hand off game launches to real PC emulators.",
            font=FONT_BODY, bg=BG, fg=TEXT_DIM, justify="left",
        )

        self.button_row = tk.Frame(page, bg=BG)
        self.button_row.pack(fill="x", padx=24, pady=(0, 12))
        self.primary_button = ttk.Button(self.button_row, text="Run Setup", style="Accent.TButton")
        self.primary_button.pack(side="left")
        self.roms_folder_button = ttk.Button(self.button_row, text="ROMs Folder", style="Ghost.TButton", command=self._open_roms_folder)
        self.roms_folder_button.pack(side="left", padx=(10, 0))
        self.logs_button = ttk.Button(self.button_row, text="Logs", style="Ghost.TButton", command=self._open_logs)
        self.logs_button.pack(side="left", padx=(10, 0))
        self.progress = ttk.Progressbar(self.button_row, mode="indeterminate", style="Dark.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(16, 0))

        self.stage_label = tk.Label(page, text="", font=FONT_BODY, bg=BG, fg=TEXT_DIM, anchor="w")
        self.stage_label.pack(fill="x", padx=24, pady=(0, 8))

        log_card = Card(page)
        log_card.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        log_inner = tk.Frame(log_card, bg=PANEL_BG)
        log_inner.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text = tk.Text(log_inner, state="disabled", wrap="word", font=FONT_MONO, bg="#0e0e10", fg="#c9c9ce", insertbackground=TEXT, relief="flat", padx=8, pady=8)
        log_scroll = ttk.Scrollbar(log_inner, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self._refresh_home_state()

    def _refresh_home_state(self) -> None:
        self.setup_intro_label.pack_forget()
        self.status_card.pack_forget()
        if self.configured:
            self.status_card.pack(fill="x", padx=24, pady=(0, 12), before=self.button_row)
        else:
            self.setup_intro_label.pack(anchor="w", padx=24, pady=(0, 14), before=self.button_row)
        self._refresh_primary_button()

    def _refresh_primary_button(self) -> None:
        if not self.configured:
            running = self._setup_process is not None and self._setup_process.poll() is None
            self.primary_button.config(
                text="Setup running..." if running else "Run Setup",
                command=self._start_setup_flow,
                state="disabled" if running else "normal",
            )
        elif self._last_bridge_up or self._last_avd_up:
            self.primary_button.config(text="Stop", command=self._stop, state="disabled" if self.busy else "normal")
        else:
            self.primary_button.config(text="Open", command=self._start, state="disabled" if self.busy else "normal")

    def _start_setup_flow(self) -> None:
        if self._setup_process is not None and self._setup_process.poll() is None:
            return
        self._setup_process = subprocess.Popen([sys.executable, "setup_gui.py"], cwd=str(INSTALLER_DIR))
        self._refresh_primary_button()

    def _start(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stage_label.config(text="Starting...")
        self._append_log("\n--- Start ---\n")
        threading.Thread(target=self._run_guarded, args=(start_iisu_pc.main,), daemon=True).start()

    def _stop(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stage_label.config(text="Stopping...")
        self._append_log("\n--- Stop ---\n")
        threading.Thread(target=self._run_guarded, args=(stop_iisu_pc.main,), daemon=True).start()

    def _run_guarded(self, func) -> None:
        writer = QueueWriter(self.log_queue)
        old_stdout = sys.stdout
        sys.stdout = writer
        try:
            func()
        except SystemExit as e:
            if e.code not in (0, None):
                print(f"\n[manager] exited with code {e.code}\n")
        except Exception:
            print(f"\n[manager] error:\n{traceback.format_exc()}")
        finally:
            sys.stdout = old_stdout
        self.after(0, self._set_busy, False)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        self._refresh_primary_button()

    def _append_log(self, text: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self._update_stage_label(text)

    def _update_stage_label(self, text: str) -> None:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[start] ") or line.startswith("[stop] "):
                self.stage_label.config(text=line.split("] ", 1)[1])

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _open_roms_folder(self) -> None:
        if not self.configured:
            return
        roms_dir = Path(self.config_data.get("roms_dir", ""))
        if not roms_dir.is_dir():
            messagebox.showerror("Can't open ROMs folder", f"{roms_dir} doesn't exist yet -- set it up in ROM Directory first.")
            return
        os.startfile(roms_dir)

    def _open_logs(self) -> None:
        # Opens the folder rather than one hardcoded file -- emulator.log
        # (AVD boot), bridge.log (launch/redirect activity), and stop.log
        # (shutdown-hotkey teardown) are all separate now that none of
        # those processes get a visible console of their own to check
        # instead.
        os.startfile(BRIDGE_DIR)

    def _on_close(self) -> None:
        if self._last_avd_up or self._last_bridge_up:
            proceed = messagebox.askyesno(
                "iiSU-PC is still running",
                "The Android VM and/or launch bridge are still running in the background.\n\n"
                "Closing this window will NOT stop them -- use Stop first if you want to shut "
                "everything down.\n\nClose this window anyway?",
            )
            if not proceed:
                return
        self.destroy()

    # -- Status polling (also watches for setup finishing / uninstall having run) -------------------------------------------------

    def _poll_status(self) -> None:
        threading.Thread(target=self._check_status, daemon=True).start()
        self.after(STATUS_POLL_INTERVAL_MS, self._poll_status)

    def _check_status(self) -> None:
        avd_up = bridge_up = None
        if self.configured:
            try:
                config = start_iisu_pc.load_config()
                avd_up = start_iisu_pc.is_avd_running(config["avd_name"])
                bridge_up = start_iisu_pc.is_port_open(config["bridge_port"])
            except Exception:
                pass
        now_configured = CONFIG_PATH.is_file()
        self.after(0, self._apply_status, avd_up, bridge_up, now_configured)

    def _apply_status(self, avd_up: bool | None, bridge_up: bool | None, now_configured: bool) -> None:
        self._last_avd_up = avd_up
        self._last_bridge_up = bridge_up

        if now_configured != self.configured:
            # Flips true right after Setup finishes, or false right after
            # an uninstall -- either way the settings pages need to be
            # rebuilt from scratch, since they were built (or last
            # rebuilt) against whatever config.json looked like before.
            self._reload_config()
            self._refresh_nav_enabled()
            self._refresh_home_state()
            self._build_settings_pages()

        if not self.busy:
            if avd_up is None:
                self.avd_dot.set_state("unknown")
                self.avd_status_label.config(text="unknown")
            else:
                self.avd_dot.set_state("up" if avd_up else "down")
                self.avd_status_label.config(text="running" if avd_up else "stopped")
            if bridge_up is None:
                self.bridge_dot.set_state("unknown")
                self.bridge_status_label.config(text="unknown")
            else:
                self.bridge_dot.set_state("up" if bridge_up else "down")
                self.bridge_status_label.config(text="running" if bridge_up else "stopped")

        self._refresh_primary_button()
        self._refresh_save_lock(avd_up, bridge_up)
        self._update_media_connection_indicator(avd_up)

    def _refresh_save_lock(self, avd_up: bool | None, bridge_up: bool | None) -> None:
        """Settings apply on the next Start (roms_dir/search_roots/
        emulators immediately; display/hotkeys/port on the next full
        restart) -- saving over a config the running instance already
        loaded from doesn't do anything to what's actually running, and
        just sets up a confusing mismatch between what the settings pages
        show and what's really in effect until the next Stop. Locking Save
        while the VM or bridge is up front-loads that "won't take effect
        until you restart anyway" into "can't save yet" instead, since the
        outcome (nothing changes until you Stop and Start again) is the
        same either way. `is None` (status unknown, e.g. mid-poll or not
        configured yet) doesn't lock -- only a *confirmed* running state
        does."""
        running = bool(avd_up) or bool(bridge_up)
        self.save_button.config(state="disabled" if running else "normal")
        if running:
            self.save_status_label.config(text="Stop iiSU-PC to change settings", fg=RED)
        elif self.save_status_label.cget("text") == "Stop iiSU-PC to change settings":
            self.save_status_label.config(text="", fg=GREEN)

    # -- Settings pages (ROM Directory / Emulators / Display / Advanced) -------------------------------------------------

    def _build_settings_pages(self) -> None:
        """Builds (or fully rebuilds) all four settings pages from the
        current self.config_data. Called once at startup and again
        whenever setup completes or an uninstall runs, since those are the
        only two ways self.config_data can change out from under
        already-built widgets."""
        self._build_roms_page()
        self._build_emulators_page()
        self._build_display_page()
        self._build_advanced_page()

    @staticmethod
    def _clear(page: tk.Frame) -> None:
        for child in page.winfo_children():
            child.destroy()

    def _build_roms_page(self) -> None:
        frame = self.pages["roms"]
        self._clear(frame)
        self._page_header(frame, "ROM Directory", "Where your games live, and where iiSU-PC looks for your PC emulators.")

        tk.Label(frame, text="Root ROM folder (contains one subfolder per console):", bg=BG, fg=TEXT, font=FONT_BODY).pack(
            anchor="w", padx=24, pady=(12, 0)
        )
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", padx=24, pady=4)
        self.roms_dir_var = tk.StringVar(value=self.config_data.get("roms_dir", ""))
        roms_entry = tk.Entry(row, textvariable=self.roms_dir_var, **ENTRY_KWARGS)
        roms_entry.pack(side="left", fill="x", expand=True, ipady=3)
        roms_entry.bind("<FocusOut>", lambda e: self._refresh_roms_status())
        ttk.Button(row, text="Browse...", style="Ghost.TButton", command=self._browse_roms_dir).pack(side="left", padx=(8, 0))

        self.roms_status_label = tk.Label(frame, text="", bg=BG, font=FONT_BODY, justify="left", wraplength=640, anchor="w")
        self.roms_status_label.pack(anchor="w", fill="x", padx=24, pady=(6, 0))
        self._refresh_roms_status()

        tk.Label(frame, text="Folders to search for emulator executables:", bg=BG, fg=TEXT, font=FONT_BODY).pack(
            anchor="w", padx=24, pady=(16, 0)
        )
        self.search_roots_list = tk.Listbox(frame, height=6, font=FONT_BODY, **LISTBOX_KWARGS)
        self.search_roots_list.pack(fill="both", expand=True, padx=24, pady=6)
        for root in self.config_data.get("search_roots", []):
            self.search_roots_list.insert("end", root)

        btn_row = tk.Frame(frame, bg=BG)
        btn_row.pack(fill="x", padx=24, pady=(0, 16))
        ttk.Button(btn_row, text="Add folder...", style="Ghost.TButton", command=self._add_search_root).pack(side="left")
        ttk.Button(btn_row, text="Remove selected", style="Ghost.TButton", command=self._remove_search_root).pack(side="left", padx=(8, 0))

    def _refresh_roms_status(self) -> None:
        raw = self.roms_dir_var.get().strip()
        if not raw:
            self.roms_status_label.config(text="", fg=TEXT_DIM)
            return
        path = Path(raw)
        if not path.is_dir():
            self.roms_status_label.config(text="✗ This folder doesn't exist yet.", fg=RED)
            return

        exact, by_compact = load_console_lookup()
        recognized, unrecognized = [], []
        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            (recognized if resolve_console_shortname(child.name, exact, by_compact) else unrecognized).append(child.name)

        if not recognized and not unrecognized:
            self.roms_status_label.config(text="This folder is empty.", fg=TEXT_DIM)
        elif not unrecognized:
            self.roms_status_label.config(text=f"✓ iiSU will recognize all {len(recognized)} folder(s): {', '.join(recognized)}", fg=GREEN)
        else:
            prefix = f"✓ {len(recognized)} recognized, " if recognized else ""
            self.roms_status_label.config(
                text=f"{prefix}✗ {len(unrecognized)} won't be seen by iiSU (rename these): {', '.join(unrecognized)}", fg=RED
            )

    def _browse_roms_dir(self) -> None:
        path = filedialog.askdirectory(title="Select root ROM folder")
        if path:
            self.roms_dir_var.set(path)
            self._refresh_roms_status()

    def _add_search_root(self) -> None:
        path = filedialog.askdirectory(title="Select a folder to search for emulators")
        if path:
            self.search_roots_list.insert("end", path)

    def _remove_search_root(self) -> None:
        for index in reversed(self.search_roots_list.curselection()):
            self.search_roots_list.delete(index)

    def _build_emulators_page(self) -> None:
        frame = self.pages["emulators"]
        self._clear(frame)
        self._page_header(frame, "Emulators", "Maps the Android package name iiSU tries to launch to a real PC emulator.")

        columns = ("prefix", "exe_names", "pre_args")
        self.emulators_tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        self.emulators_tree.heading("prefix", text="Package prefix")
        self.emulators_tree.heading("exe_names", text="Executable name(s)")
        self.emulators_tree.heading("pre_args", text="Launch flags")
        self.emulators_tree.column("prefix", width=230)
        self.emulators_tree.column("exe_names", width=210)
        self.emulators_tree.column("pre_args", width=150)
        self.emulators_tree.pack(fill="both", expand=True, padx=24, pady=(12, 4))

        for prefix, profile in self.config_data.get("emulators", {}).items():
            exe_display, pre_args_display = describe_profile(profile)
            self.emulators_tree.insert("", "end", iid=prefix, values=(prefix, exe_display, pre_args_display))

        btn_row = tk.Frame(frame, bg=BG)
        btn_row.pack(fill="x", padx=24, pady=(0, 16))
        ttk.Button(btn_row, text="Add...", style="Ghost.TButton", command=self._add_emulator).pack(side="left")
        ttk.Button(btn_row, text="Edit selected...", style="Ghost.TButton", command=self._edit_emulator).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Remove selected", style="Ghost.TButton", command=self._remove_emulator).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Test selected...", style="Ghost.TButton", command=self._test_emulator_mapping).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Install Redirector Apps...", style="Ghost.TButton", command=lambda: RedirectorInstallDialog(self)).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Restore Defaults", style="Ghost.TButton", command=self._restore_default_emulators).pack(side="left", padx=(8, 0))

    def _restore_default_emulators(self) -> None:
        if not messagebox.askyesno(
            "Restore default emulators?",
            "This replaces every mapping in this list with iiSU-PC's built-in defaults "
            "(shared/emulator_defaults.py). Any custom or edited mappings you've added "
            "will be lost. Save afterward to keep the change.",
        ):
            return
        self.config_data["emulators"] = build_emulators_map()
        self._build_emulators_page()

    def _add_emulator(self) -> None:
        dialog = EmulatorDialog(self, "Add emulator mapping")
        if dialog.result_values:
            prefix, exe_names, pre_args = dialog.result_values
            if not prefix:
                return
            if self.emulators_tree.exists(prefix):
                messagebox.showerror("Duplicate", f"A mapping for '{prefix}' already exists.")
                return
            self.emulators_tree.insert("", "end", iid=prefix, values=(prefix, ", ".join(exe_names), ", ".join(pre_args)))

    def _edit_emulator(self) -> None:
        selected = self.emulators_tree.selection()
        if not selected:
            return
        prefix = selected[0]
        if "by_extension" in self.config_data.get("emulators", {}).get(prefix, {}):
            messagebox.showinfo(
                "Can't edit here",
                "This entry maps a different executable per ROM file extension "
                "(see shared/emulator_defaults.py) -- editing it as one flat "
                "executable/flags pair isn't supported here. Edit config.json "
                "directly if you need to change it.",
            )
            return
        values = self.emulators_tree.item(prefix, "values")
        dialog = EmulatorDialog(self, "Edit emulator mapping", prefix=values[0], exe_names=values[1], pre_args=values[2])
        if dialog.result_values:
            new_prefix, exe_names, pre_args = dialog.result_values
            self.emulators_tree.delete(prefix)
            self.emulators_tree.insert("", "end", iid=new_prefix, values=(new_prefix, ", ".join(exe_names), ", ".join(pre_args)))

    def _remove_emulator(self) -> None:
        for item in self.emulators_tree.selection():
            self.emulators_tree.delete(item)

    def _test_emulator_mapping(self) -> None:
        selected = self.emulators_tree.selection()
        if not selected:
            messagebox.showinfo("Nothing selected", "Select a mapping in the list first.")
            return
        prefix = selected[0]
        profile = self.config_data.get("emulators", {}).get(prefix)
        if profile is None:
            messagebox.showerror("Can't test", "This mapping hasn't been saved yet -- click Save first, then try again.")
            return

        rom_filename = None
        if "by_extension" in profile:
            rom_path_str = filedialog.askopenfilename(title="Pick a ROM to test this mapping against (it resolves per file extension)")
            if not rom_path_str:
                return
            rom_filename = Path(rom_path_str).name

        resolved = find_emulator_for_package(prefix, {prefix: profile}, rom_filename, None)
        if resolved is None:
            messagebox.showerror(
                "No match", f"'{prefix}'" + (f" with '{rom_filename}'" if rom_filename else "") + " doesn't resolve to any configured executable."
            )
            return

        search_roots = [Path(r) for r in self.search_roots_list.get(0, "end")]
        executable = find_executable(resolved["exe_names"], search_roots, {"executables": {}})

        lines = [
            f"Looking for: {', '.join(resolved['exe_names'])}",
            f"Launch flags: {' '.join(resolved['pre_args']) or '(none)'}",
            f"Found: {executable}" if executable else f"NOT found under any of your {len(search_roots)} search folder(s).",
        ]
        if rom_filename:
            roms_dir = Path(self.roms_dir_var.get().strip())
            rom_path = find_rom(rom_filename, roms_dir, {"roms": {}})
            lines.append(f"ROM: found at {rom_path}" if rom_path else f"ROM: NOT found under {roms_dir}")

        messagebox.showinfo("Test result", "\n".join(lines))


    # -- Android Storage -------------------------------------------------

    def _adb_command(self, *args: str, timeout: int = 30, capture: bool = True):
        """Run adb using the same PATH-based adb setup iiSU-PC already uses."""
        flags = 0x08000000 if os.name == "nt" else 0
        kwargs = {
            "cwd": str(PROJECT_ROOT),
            "timeout": timeout,
            "creationflags": flags,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if capture:
            kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        bundled_adb = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / "adb.exe"
        adb_exe = str(bundled_adb) if bundled_adb.is_file() else "adb"
        return subprocess.run([adb_exe, *args], **kwargs)

    @staticmethod
    def _android_remote_quote(value: str) -> str:
        # Quote one value for Android's /system/bin/sh.
        # This safely handles spaces and apostrophes.
        return "'" + str(value).replace("'", "'\\''") + "'"

    def _adb_shell_direct(self, command: str, timeout: int = 30):
        # Give ADB one complete Android-side command string. This preserves
        # the quotes embedded by _android_remote_quote instead of splitting
        # the command again through an extra `sh -c` layer.
        return self._adb_command("shell", command, timeout=timeout)

    def _android_storage_media_scan(self, remote_path: str) -> None:
        """Notify Android that an ADB-side shared-storage path changed."""
        remote_path = str(remote_path).replace("\\", "/")
        uri = "file://" + remote_path
        result = self._adb_command(
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            uri,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Android media scan failed")

    def _adb_device_ready(self) -> tuple[bool, str]:
        try:
            result = self._adb_command("get-state", timeout=5)
        except FileNotFoundError:
            return False, "ADB was not found in PATH."
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"ADB error: {exc}"
        if result.returncode == 0 and result.stdout.strip() == "device":
            return True, "Android VM connected"
        detail = (result.stderr or result.stdout).strip()
        return False, detail or "Android VM is not connected."

    @staticmethod
    def _android_join(base: str, name: str) -> str:
        if base == "/":
            return "/" + name
        return base.rstrip("/") + "/" + name

    @staticmethod
    def _android_parent(path: str) -> str:
        path = path.rstrip("/")
        if not path or path == "/":
            return "/"
        parent = path.rsplit("/", 1)[0]
        return parent or "/"


    def _build_diagnostics_page(self) -> None:
        frame = self.pages["diagnostics"]
        self._clear(frame)
        self._page_header(
            frame,
            "Diagnostics",
            "Run non-destructive checks for iiSU-PC, the Android VM, bridge, Windows Apps, Steam, and logs.",
        )

        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", padx=24, pady=(0, 10))
        tk.Button(
            toolbar, text="Run Diagnostics", command=self._run_diagnostics
        ).pack(side="left")
        tk.Button(
            toolbar, text="Open Manager Log",
            command=lambda: self._diagnostics_open_path(MANAGER_LOG_PATH)
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            toolbar, text="Open Bridge Log",
            command=lambda: self._diagnostics_open_path(BRIDGE_DIR / "bridge_debug.log")
        ).pack(side="left", padx=(8, 0))

        update_frame = tk.Frame(frame, bg=PANEL_BG)
        update_frame.pack(fill="x", padx=24, pady=(0, 12))

        update_top = tk.Frame(update_frame, bg=PANEL_BG)
        update_top.pack(fill="x", padx=14, pady=(12, 4))

        tk.Label(
            update_top, text="Community-iiSU-PC Updates",
            bg=PANEL_BG, fg=TEXT, font=FONT_BODY, anchor="w"
        ).pack(side="left")

        self.auto_updates_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_updates", False))
        )
        tk.Checkbutton(
            update_top,
            text="Automatically apply updates on startup",
            variable=self.auto_updates_var,
            command=self._diagnostics_set_auto_updates,
            bg=PANEL_BG,
            fg=TEXT,
            selectcolor=BG,
            activebackground=PANEL_BG,
            activeforeground=TEXT,
            font=FONT_BODY,
        ).pack(side="right")

        tk.Label(
            update_frame,
            text=(
                "Off is recommended for customized installations. When enabled, startup may "
                "fast-forward a Git checkout or apply a newer release over tracked project files."
            ),
            bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w", justify="left",
            wraplength=880,
        ).pack(fill="x", padx=14, pady=(0, 8))

        update_actions = tk.Frame(update_frame, bg=PANEL_BG)
        update_actions.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(
            update_actions,
            text="Check for Updates Now",
            command=self._diagnostics_check_for_updates_now,
        ).pack(side="left")

        self.update_check_status_var = tk.StringVar(
            value="This check is read-only: it never downloads or installs an update."
        )
        tk.Label(
            update_actions,
            textvariable=self.update_check_status_var,
            bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w",
        ).pack(side="left", padx=(12, 0))

        self.diagnostics_summary_var = tk.StringVar(
            value="Diagnostics have not been run yet."
        )
        tk.Label(
            frame, textvariable=self.diagnostics_summary_var,
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w"
        ).pack(fill="x", padx=24, pady=(0, 8))

        columns = ("status", "check", "details")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        tree.heading("status", text="Status")
        tree.heading("check", text="Check")
        tree.heading("details", text="Details")
        tree.column("status", width=90, minwidth=80, stretch=False)
        tree.column("check", width=210, minwidth=160, stretch=False)
        tree.column("details", width=650, minwidth=300, stretch=True)
        tree.pack(fill="both", expand=True, padx=24, pady=(0, 24))
        self.diagnostics_tree = tree

    def _diagnostics_set_auto_updates(self) -> None:
        """Persist the startup auto-update preference immediately."""
        enabled = bool(self.auto_updates_var.get())
        self.config_data["auto_updates"] = enabled
        try:
            save_config(self.config_data)
        except Exception as exc:
            self.auto_updates_var.set(not enabled)
            self.config_data["auto_updates"] = not enabled
            messagebox.showerror(
                "Community-iiSU-PC Updates",
                f"Couldn't save the update setting:\n\n{exc}",
            )
            return

        state = "enabled" if enabled else "disabled"
        self.update_check_status_var.set(
            f"Automatic startup updates are {state}. "
            "Check for Updates Now remains read-only."
        )

    def _diagnostics_check_for_updates_now(self) -> None:
        """Check upstream state without downloading or applying an update."""
        if getattr(self, "_update_check_inflight", False):
            return
        self._update_check_inflight = True
        self.update_check_status_var.set("Checking for updates (read-only)...")

        def worker() -> None:
            try:
                import updater

                if updater.is_git_checkout():
                    branch = updater.current_branch()
                    if branch is None:
                        message = "Can't compare updates: this Git checkout is on a detached HEAD."
                    else:
                        # `git fetch` updates only Git's remote-tracking metadata. It does
                        # not modify the working tree, download a release archive, merge,
                        # pull, checkout, or install anything.
                        fetch = updater._run_git(["fetch", "origin", branch])
                        if fetch is None or fetch.returncode != 0:
                            reason = (
                                fetch.stderr.strip()[:200]
                                if fetch else "git not found or fetch timed out"
                            )
                            message = f"Couldn't check GitHub: {reason}"
                        else:
                            local = updater._run_git(["rev-parse", "HEAD"])
                            remote = updater._run_git(["rev-parse", f"origin/{branch}"])
                            local_sha = (
                                local.stdout.strip()
                                if local and local.returncode == 0 else None
                            )
                            remote_sha = (
                                remote.stdout.strip()
                                if remote and remote.returncode == 0 else None
                            )
                            if not local_sha or not remote_sha:
                                message = "Couldn't compare local and remote commits."
                            elif local_sha == remote_sha:
                                message = f"Up to date on {branch}. Nothing was downloaded or installed."
                            else:
                                count = updater._run_git(
                                    ["rev-list", "--count", f"HEAD..origin/{branch}"]
                                )
                                behind = (
                                    count.stdout.strip()
                                    if count and count.returncode == 0 else "one or more"
                                )
                                message = (
                                    f"Update available: {behind} new commit(s) on {branch}. "
                                    "Nothing was downloaded or installed."
                                )
                else:
                    current = (
                        updater.VERSION_PATH.read_text(encoding="utf-8").strip()
                        if updater.VERSION_PATH.is_file() else None
                    )
                    req = urllib.request.Request(
                        f"https://api.github.com/repos/{updater.GITHUB_REPO}/releases",
                        headers={
                            "User-Agent": "Community-iiSU-PC",
                            "Accept": "application/vnd.github+json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=updater.HTTP_TIMEOUT) as resp:
                        releases = json.loads(resp.read())
                    if not releases:
                        message = "No Community-iiSU-PC releases are published yet."
                    else:
                        latest = releases[0]["tag_name"]
                        if current == latest:
                            message = f"Up to date ({current}). Nothing was downloaded or installed."
                        elif current is None:
                            message = (
                                f"Latest release: {latest}. This install has no VERSION file "
                                "for comparison. Nothing was downloaded or installed."
                            )
                        else:
                            message = (
                                f"Update available: {latest} (installed: {current}). "
                                "Nothing was downloaded or installed."
                            )
            except Exception as exc:
                message = f"Update check failed: {exc}"

            def finish() -> None:
                self._update_check_inflight = False
                self.update_check_status_var.set(message)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _diagnostics_open_path(path: Path) -> None:
        try:
            if not path.exists():
                messagebox.showwarning("Diagnostics", f"Not found:\n{path}")
                return
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("Diagnostics", f"Couldn't open:\n{path}\n\n{exc}")

    def _diagnostics_add(self, status: str, check: str, details: str) -> None:
        self.diagnostics_tree.insert("", "end", values=(status, check, details))

    def _run_diagnostics(self) -> None:
        tree = self.diagnostics_tree
        for item in tree.get_children():
            tree.delete(item)
        self.diagnostics_summary_var.set("Running diagnostics...")
        self.update_idletasks()

        results: list[tuple[str, str, str]] = []

        def add(status: str, check: str, details: str):
            results.append((status, check, details))

        root = Path(__file__).resolve().parent
        config_path = BRIDGE_DIR / "config.json"
        apps_path = BRIDGE_DIR / "windows_apps.json"
        adb_path = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / "adb.exe"

        # 1. Core paths/files.
        add("OK" if BRIDGE_DIR.is_dir() else "ERROR", "Bridge directory",
            str(BRIDGE_DIR) if BRIDGE_DIR.is_dir() else f"Missing: {BRIDGE_DIR}")
        add("OK" if config_path.is_file() else "ERROR", "Bridge config",
            str(config_path) if config_path.is_file() else "bridge/config.json is missing")
        add("OK" if apps_path.is_file() else "WARNING", "Windows Apps config",
            str(apps_path) if apps_path.is_file() else "windows_apps.json is missing")
        add("OK" if adb_path.is_file() else "ERROR", "Bundled ADB",
            str(adb_path) if adb_path.is_file() else f"Missing: {adb_path}")

        # 2. JSON validity and bridge settings.
        config = {}
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8-sig"))
                add("OK", "Bridge config JSON", "Valid JSON")
            except Exception as exc:
                add("ERROR", "Bridge config JSON", f"Invalid JSON: {exc}")

        if apps_path.is_file():
            try:
                apps = json.loads(apps_path.read_text(encoding="utf-8-sig"))
                if isinstance(apps, dict):
                    add("OK", "Windows Apps JSON", f"Valid JSON • {len(apps)} entr{'y' if len(apps) == 1 else 'ies'}")
                else:
                    add("ERROR", "Windows Apps JSON", "Top-level JSON value is not an object")
            except Exception as exc:
                add("ERROR", "Windows Apps JSON", f"Invalid JSON: {exc}")

        bridge_port = config.get("bridge_port", 7737) if isinstance(config, dict) else 7737
        try:
            bridge_port = int(bridge_port)
            if 1 <= bridge_port <= 65535:
                add("OK", "Bridge port setting", str(bridge_port))
            else:
                add("ERROR", "Bridge port setting", f"Invalid port: {bridge_port}")
        except Exception:
            add("ERROR", "Bridge port setting", f"Invalid value: {bridge_port!r}")

        roms_dir = config.get("roms_dir") if isinstance(config, dict) else None
        if roms_dir:
            rp = Path(roms_dir)
            add("OK" if rp.is_dir() else "ERROR", "ROMs directory",
                str(rp) if rp.is_dir() else f"Configured path does not exist: {rp}")
            windows_roms = rp / "windows"
            add("OK" if windows_roms.is_dir() else "WARNING", "Windows ROMs folder",
                str(windows_roms) if windows_roms.is_dir() else f"Not found: {windows_roms}")
        else:
            add("WARNING", "ROMs directory", "roms_dir is not configured")

        # 3. ADB + Android shared storage.
        if adb_path.is_file():
            try:
                state = self._adb_command("get-state", timeout=10)
                state_text = (state.stdout or "").strip()
                if state.returncode == 0 and state_text == "device":
                    add("OK", "Android VM / ADB", "Device is connected")
                    listing = self._adb_shell_direct(
                        f"ls -ld {self._android_remote_quote('/storage/emulated/0')}",
                        timeout=10,
                    )
                    if listing.returncode == 0:
                        add("OK", "Android shared storage", "/storage/emulated/0 is accessible")
                    else:
                        add("ERROR", "Android shared storage",
                            (listing.stderr or listing.stdout or "Unable to access shared storage").strip())
                else:
                    add("ERROR", "Android VM / ADB",
                        state_text or (state.stderr or "ADB device is not ready").strip())
            except Exception as exc:
                add("ERROR", "Android VM / ADB", str(exc))

        # 4. Bridge listener status. This is informational; Manager need not have bridge running.
        try:
            port = int(bridge_port)
            with socket.create_connection(("127.0.0.1", port), timeout=0.35):
                add("OK", "Launch Bridge listener", f"Listening on localhost:{port}")
        except Exception:
            add("WARNING", "Launch Bridge listener",
                f"Nothing accepted a connection on localhost:{bridge_port} (normal if the bridge is not running)")

        # 5. Steam library visibility using known Steam roots, without changing anything.
        steam_roots = [
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
        ]
        found_steam = next((p for p in steam_roots if p.is_dir()), None)
        if found_steam:
            vdf = found_steam / "steamapps" / "libraryfolders.vdf"
            add("OK", "Steam installation", str(found_steam))
            add("OK" if vdf.is_file() else "WARNING", "Steam library config",
                str(vdf) if vdf.is_file() else f"Not found: {vdf}")
        else:
            add("WARNING", "Steam installation", "Default Steam installation was not detected")

        # 6. Persistent logs.
        for label, path in (
            ("Manager log", MANAGER_LOG_PATH),
            ("Bridge log", BRIDGE_DIR / "bridge_debug.log"),
        ):
            if path.is_file():
                try:
                    size = path.stat().st_size
                    add("OK", label, f"{path} • {size:,} bytes")
                except Exception:
                    add("OK", label, str(path))
            else:
                add("WARNING", label, f"Not found yet: {path}")

        # 7. Backup safety area, informational only.
        safety = root / "restore_safety"
        if safety.is_dir():
            try:
                count = sum(1 for p in safety.iterdir() if p.is_dir())
                add("OK", "Restore safety copies", f"{count} restore safety set(s) • {safety}")
            except Exception:
                add("OK", "Restore safety copies", str(safety))
        else:
            add("OK", "Restore safety copies", "No restore safety folder yet")

        for row in results:
            self._diagnostics_add(*row)

        errors = sum(1 for status, _, _ in results if status == "ERROR")
        warnings = sum(1 for status, _, _ in results if status == "WARNING")
        oks = sum(1 for status, _, _ in results if status == "OK")
        self.diagnostics_summary_var.set(
            f"{oks} OK • {warnings} Warning{'s' if warnings != 1 else ''} • "
            f"{errors} Error{'s' if errors != 1 else ''}"
        )
        _manager_log_write(
            f"DIAGNOSTICS completed ok={oks} warnings={warnings} errors={errors}"
        )

    def _backup_restore_candidates(self) -> list[tuple[Path, str]]:
        """Return safe, user-created/configuration files worth backing up."""
        candidates: list[tuple[Path, str]] = []

        def add(path: Path, archive_name: str):
            try:
                if path.is_file() and not any(p.resolve() == path.resolve() for p, _ in candidates):
                    candidates.append((path, archive_name))
            except Exception:
                pass

        # Core iiSU-PC configuration.
        add(BRIDGE_DIR / "windows_apps.json", "bridge/windows_apps.json")
        add(BRIDGE_DIR / "config.json", "bridge/config.json")
        add(Path(__file__).resolve().parent / "config.json", "config.json")

        # Include common Manager-created JSON configuration if present.
        root = Path(__file__).resolve().parent
        for name in (
            "windows_apps.json",
            "manager_config.json",
            "settings.json",
        ):
            add(root / name, name)

        return candidates

    def _build_backup_restore_page(self) -> None:
        frame = self.pages["backup_restore"]
        self._clear(frame)
        self._page_header(
            frame,
            "Backup & Restore",
            "Create a portable backup of iiSU-PC configuration, or restore one later.",
        )

        card = tk.Frame(frame, bg=PANEL_BG, padx=18, pady=18)
        card.pack(fill="x", padx=24, pady=(0, 14))

        tk.Label(
            card, text="Configuration Backup", bg=PANEL_BG, fg=TEXT,
            font=FONT_HEADING, anchor="w"
        ).pack(fill="x")
        tk.Label(
            card,
            text=(
                "Backs up detected iiSU-PC configuration such as Windows app mappings "
                "and bridge settings. ROMs, Android VM storage, caches, logs, executables, "
                "and Steam game files are intentionally excluded."
            ),
            bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY,
            justify="left", wraplength=760, anchor="w",
        ).pack(fill="x", pady=(6, 14))

        button_row = tk.Frame(card, bg=PANEL_BG)
        button_row.pack(fill="x")
        tk.Button(
            button_row, text="Create Backup...", command=self._create_manager_backup
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row, text="Restore Backup...", command=self._restore_manager_backup
        ).pack(side="left")

        self.backup_restore_status_var = tk.StringVar(
            value="Ready. Restore always creates a safety copy of files it replaces."
        )
        tk.Label(
            frame, textvariable=self.backup_restore_status_var,
            bg=BG, fg=TEXT_DIM, font=FONT_BODY,
            justify="left", wraplength=800, anchor="w",
        ).pack(fill="x", padx=24, pady=(4, 0))

    def _create_manager_backup(self) -> None:
        files = self._backup_restore_candidates()
        if not files:
            messagebox.showwarning(
                "Backup & Restore",
                "No supported iiSU-PC configuration files were found to back up.",
            )
            return

        default_name = "iisu-pc-backup-" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".zip"
        destination = filedialog.asksaveasfilename(
            title="Create iiSU-PC Backup",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("iiSU-PC Backup", "*.zip"), ("ZIP archive", "*.zip")],
        )
        if not destination:
            return

        try:
            manifest_lines = [
                "iiSU-PC Manager Backup",
                "Created: " + datetime.now().isoformat(timespec="seconds"),
                "Manager: " + str(Path(__file__).resolve()),
                "",
                "Files:",
            ]
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path, archive_name in files:
                    zf.write(path, archive_name)
                    manifest_lines.append(f"- {archive_name}")
                zf.writestr("backup_manifest.txt", "\n".join(manifest_lines) + "\n")

            self.backup_restore_status_var.set(
                f"Backup created: {destination} ({len(files)} configuration file(s))"
            )
            _manager_log_write(
                f"BACKUP created path={destination!r} files={len(files)}"
            )
            messagebox.showinfo(
                "Backup Complete",
                f"Backed up {len(files)} configuration file(s).\n\n{destination}",
            )
        except Exception as exc:
            _manager_log_write("BACKUP ERROR\n" + traceback.format_exc())
            messagebox.showerror("Backup & Restore", f"Backup failed:\n{exc}")

    @staticmethod
    def _backup_safe_member(name: str) -> bool:
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
            return False
        allowed = {
            "bridge/windows_apps.json",
            "bridge/config.json",
            "config.json",
            "windows_apps.json",
            "manager_config.json",
            "settings.json",
        }
        return normalized in allowed

    def _restore_manager_backup(self) -> None:
        source = filedialog.askopenfilename(
            title="Restore iiSU-PC Backup",
            filetypes=[("iiSU-PC Backup", "*.zip"), ("ZIP archive", "*.zip")],
        )
        if not source:
            return

        root = Path(__file__).resolve().parent
        restore_map = {
            "bridge/windows_apps.json": BRIDGE_DIR / "windows_apps.json",
            "bridge/config.json": BRIDGE_DIR / "config.json",
            "config.json": root / "config.json",
            "windows_apps.json": root / "windows_apps.json",
            "manager_config.json": root / "manager_config.json",
            "settings.json": root / "settings.json",
        }

        try:
            with zipfile.ZipFile(source, "r") as zf:
                members = [n.replace("\\", "/") for n in zf.namelist()]
                selected = [n for n in members if self._backup_safe_member(n)]
                if not selected:
                    raise RuntimeError(
                        "This archive does not contain supported iiSU-PC backup files."
                    )

                # Validate JSON before touching current configuration.
                payloads: dict[str, bytes] = {}
                import json
                for name in selected:
                    data = zf.read(name)
                    if name.lower().endswith(".json"):
                        json.loads(data.decode("utf-8-sig"))
                    payloads[name] = data

            shown = "\n".join(f"• {name}" for name in selected)
            if not messagebox.askyesno(
                "Restore Backup",
                "Restore these configuration files?\n\n"
                + shown
                + "\n\nExisting files will be copied to a timestamped safety folder first.",
            ):
                return

            safety_dir = root / "restore_safety" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safety_count = 0
            for name in selected:
                target = restore_map[name]
                if target.is_file():
                    relative = Path(name)
                    safety_target = safety_dir / relative
                    safety_target.parent.mkdir(parents=True, exist_ok=True)
                    safety_target.write_bytes(target.read_bytes())
                    safety_count += 1

            restored = 0
            for name, data in payloads.items():
                target = restore_map[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                temp_target = target.with_name(target.name + ".restore_tmp")
                temp_target.write_bytes(data)
                temp_target.replace(target)
                restored += 1

            self.backup_restore_status_var.set(
                f"Restored {restored} file(s). Safety copies: {safety_count}."
            )
            _manager_log_write(
                f"RESTORE completed source={source!r} restored={restored} "
                f"safety_copies={safety_count} safety_dir={str(safety_dir)!r}"
            )
            messagebox.showinfo(
                "Restore Complete",
                f"Restored {restored} configuration file(s).\n\n"
                f"Safety copies created: {safety_count}\n"
                + (f"{safety_dir}\n\n" if safety_count else "\n")
                + "Restart the Manager/bridge before relying on restored settings.",
            )
        except zipfile.BadZipFile:
            _manager_log_write(f"RESTORE ERROR invalid zip source={source!r}")
            messagebox.showerror(
                "Backup & Restore", "That file is not a valid ZIP backup."
            )
        except Exception as exc:
            _manager_log_write("RESTORE ERROR\n" + traceback.format_exc())
            messagebox.showerror("Backup & Restore", f"Restore failed:\n{exc}")

    def _build_android_storage_page(self) -> None:
        frame = self.pages["android_storage"]
        self._clear(frame)
        self._page_header(
            frame,
            "Android Storage",
            "Browse and transfer files directly between Windows and the iiSU Android VM.",
        )

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=24, pady=(12, 6))
        self.android_storage_path_var = tk.StringVar(value="/storage/emulated/0")
        ttk.Button(top, text="Up", style="Ghost.TButton", command=self._android_storage_up).pack(side="left")
        path_entry = tk.Entry(top, textvariable=self.android_storage_path_var, **ENTRY_KWARGS)
        path_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        path_entry.bind("<Return>", lambda _e: self._android_storage_refresh())
        ttk.Button(top, text="Go", style="Ghost.TButton", command=self._android_storage_refresh).pack(side="left")
        ttk.Button(top, text="Refresh", style="Ghost.TButton", command=self._android_storage_refresh).pack(side="left", padx=(8, 0))

        self.android_storage_status = tk.Label(
            frame, text="Open this page while the Android VM is running.",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w",
        )
        self.android_storage_status.pack(fill="x", padx=24, pady=(0, 6))

        tree_wrap = tk.Frame(frame, bg=BG)
        tree_wrap.pack(fill="both", expand=True, padx=24, pady=(0, 6))
        cols = ("name", "type", "size")
        self.android_storage_tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="extended")
        self.android_storage_tree.heading("name", text="Name")
        self.android_storage_tree.heading("type", text="Type")
        self.android_storage_tree.heading("size", text="Size")
        self.android_storage_tree.column("name", width=470, anchor="w")
        self.android_storage_tree.column("type", width=100, anchor="w")
        self.android_storage_tree.column("size", width=120, anchor="e")
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.android_storage_tree.yview)
        self.android_storage_tree.configure(yscrollcommand=scroll.set)
        self.android_storage_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.android_storage_tree.bind("<Double-1>", self._android_storage_open_selected)

        tk.Label(
            frame,
            text="Tip: use Upload File / Upload Folder below. Drag-and-drop will be added with a safer backend.",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 8))

        buttons = tk.Frame(frame, bg=BG)
        buttons.pack(fill="x", padx=24, pady=(0, 16))
        for label, command in (
            ("Upload File...", self._android_storage_upload_file),
            ("Upload Folder...", self._android_storage_upload_folder),
            ("Download...", self._android_storage_download),
            ("Edit Text...", self._android_storage_edit_text),
            ("New Folder...", self._android_storage_new_folder),
            ("Rename...", self._android_storage_rename),
            ("Delete", self._android_storage_delete),
        ):
            ttk.Button(buttons, text=label, style="Ghost.TButton", command=command).pack(side="left", padx=(0, 8))

    def _android_storage_set_status(self, text: str, error: bool = False) -> None:
        if hasattr(self, "android_storage_status"):
            self.android_storage_status.config(text=text, fg=RED if error else TEXT_DIM)

    def _android_storage_refresh(self) -> None:
        if not hasattr(self, "android_storage_tree"):
            return
        path = self.android_storage_path_var.get().strip() or "/storage/emulated/0"
        if not path.startswith("/"):
            path = "/" + path
        self.android_storage_path_var.set(path)
        self._android_storage_set_status("Loading...")
        threading.Thread(target=self._android_storage_load_worker, args=(path,), daemon=True).start()

    def _android_storage_load_worker(self, path: str) -> None:
        ready, detail = self._adb_device_ready()
        if not ready:
            self.after(0, self._android_storage_apply_listing, path, None, detail)
            return

        # Directory listing is intentionally kept on the exact direct-ADB
        # form already proven to work on this VM. Do not route browsing
        # through the write-operation shell helper.
        try:
            result = self._adb_shell_direct(f"ls -la {self._android_remote_quote(path)}", timeout=15)
        except Exception as exc:
            self.after(0, self._android_storage_apply_listing, path, None, str(exc))
            return

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or f"Can't open {path}"
            self.after(0, self._android_storage_apply_listing, path, None, detail)
            return

        rows = []
        for line in result.stdout.splitlines():
            line = line.rstrip()
            if not line or line.startswith("total "):
                continue

            # Android `ls -la` columns:
            # perms links owner group size date time name
            # maxsplit preserves spaces in filenames in the final field.
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue

            perms, _links, _owner, _group, size_raw, _date, _time, name = parts
            if name in {".", ".."}:
                continue

            is_dir = perms.startswith("d")
            try:
                size = int(size_raw)
            except ValueError:
                size = 0
            rows.append((name, is_dir, size))

        rows.sort(key=lambda r: (not r[1], r[0].casefold()))
        self.after(0, self._android_storage_apply_listing, path, rows, "Android VM connected")

    @staticmethod
    def _format_android_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _android_storage_apply_listing(self, path: str, rows, status: str) -> None:
        self.android_storage_tree.delete(*self.android_storage_tree.get_children())
        if rows is None:
            self._android_storage_set_status(status, True)
            return
        self.android_storage_path_var.set(path)
        for index, (name, is_dir, size) in enumerate(rows):
            self.android_storage_tree.insert(
                "", "end", iid=f"android-{index}",
                values=(name, "Folder" if is_dir else "File", "" if is_dir else self._format_android_size(size)),
                tags=("dir" if is_dir else "file",),
            )
        self._android_storage_set_status(f"{status} • {len(rows)} item(s)")

    def _android_storage_selected(self) -> list[tuple[str, bool]]:
        result = []
        for iid in self.android_storage_tree.selection():
            values = self.android_storage_tree.item(iid, "values")
            if values:
                result.append((str(values[0]), str(values[1]) == "Folder"))
        return result

    def _android_storage_open_selected(self, _event=None) -> None:
        selected = self._android_storage_selected()
        if len(selected) != 1 or not selected[0][1]:
            return
        self.android_storage_path_var.set(
            self._android_join(self.android_storage_path_var.get(), selected[0][0])
        )
        self._android_storage_refresh()

    def _android_storage_up(self) -> None:
        self.android_storage_path_var.set(self._android_parent(self.android_storage_path_var.get()))
        self._android_storage_refresh()

    def _android_storage_run_async(self, description: str, func) -> None:
        self._android_storage_set_status(description + "...")
        def worker():
            try:
                func()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Android Storage", str(exc)))
            finally:
                self.after(0, self._android_storage_refresh)
        threading.Thread(target=worker, daemon=True).start()

    def _android_storage_upload_paths(self, paths: list[str], description: str = "Uploading") -> None:
        paths = [str(Path(path)) for path in paths if path and Path(path).exists()]
        if not paths:
            return
        dest = self.android_storage_path_var.get()
        def work():
            for source in paths:
                result = self._adb_command("push", source, dest + "/", timeout=900)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or f"adb push failed for {Path(source).name}")
            self._android_storage_media_scan(dest)
        self._android_storage_run_async(description, work)

    def _android_storage_upload_file(self) -> None:
        source = filedialog.askopenfilename(title="Upload file to Android")
        if source:
            self._android_storage_upload_paths([source], "Uploading file")

    def _android_storage_upload_folder(self) -> None:
        source = filedialog.askdirectory(title="Upload folder to Android")
        if source:
            self._android_storage_upload_paths([source], "Uploading folder")

    def _android_storage_download(self) -> None:
        selected = self._android_storage_selected()
        if not selected:
            messagebox.showinfo("Android Storage", "Select one or more files/folders first.")
            return
        dest = filedialog.askdirectory(title="Download selected items to...")
        if not dest:
            return
        base = self.android_storage_path_var.get()
        def work():
            for name, _is_dir in selected:
                remote = self._android_join(base, name)
                result = self._adb_command("pull", remote, dest, timeout=900)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or f"adb pull failed for {name}")
        self._android_storage_run_async("Downloading", work)

    def _android_storage_edit_text(self) -> None:
        selected = self._android_storage_selected()
        if len(selected) != 1 or selected[0][1]:
            messagebox.showinfo("Android Storage", "Select exactly one text file to edit.")
            return

        name = selected[0][0]
        remote = self._android_join(self.android_storage_path_var.get(), name)
        result = self._adb_shell_direct(
            f"cat {self._android_remote_quote(remote)}", timeout=30
        )
        if result.returncode != 0:
            messagebox.showerror(
                "Android Storage",
                result.stderr.strip() or f"Couldn't read {name}.",
            )
            return

        content = result.stdout
        if "\x00" in content:
            messagebox.showerror(
                "Android Storage",
                "This file appears to be binary and can't be edited as text.",
            )
            return
        if len(content.encode("utf-8", errors="replace")) > 2 * 1024 * 1024:
            messagebox.showerror(
                "Android Storage",
                "Text editing is limited to files up to 2 MB.",
            )
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"Edit Text - {name}")
        dialog.geometry("820x600")
        dialog.minsize(560, 380)
        dialog.configure(bg=BG)
        dialog.transient(self)

        tk.Label(
            dialog, text=remote, bg=BG, fg=TEXT_DIM,
            font=FONT_BODY, anchor="w"
        ).pack(fill="x", padx=16, pady=(14, 8))

        wrap = tk.Frame(dialog, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16)
        text_widget = tk.Text(
            wrap, bg=PANEL_BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", undo=True, wrap="none", font=FONT_MONO,
        )
        yscroll = ttk.Scrollbar(wrap, orient="vertical", command=text_widget.yview)
        xscroll = ttk.Scrollbar(wrap, orient="horizontal", command=text_widget.xview)
        text_widget.configure(
            yscrollcommand=yscroll.set, xscrollcommand=xscroll.set
        )
        text_widget.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        text_widget.insert("1.0", content)

        row = tk.Frame(dialog, bg=BG)
        row.pack(fill="x", padx=16, pady=14)
        status = tk.Label(
            row, text="UTF-8 text editor", bg=BG, fg=TEXT_DIM, font=FONT_BODY
        )
        status.pack(side="left")

        def save_text():
            data = text_widget.get("1.0", "end-1c")
            status.config(text="Saving...")
            dialog.update_idletasks()
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", newline="", delete=False,
                    suffix=Path(name).suffix
                ) as tmp:
                    tmp.write(data)
                    temp_path = tmp.name

                result2 = self._adb_command("push", temp_path, remote, timeout=300)
                if result2.returncode != 0:
                    raise RuntimeError(
                        result2.stderr.strip() or "adb push failed"
                    )
                self._android_storage_media_scan(
                    self.android_storage_path_var.get()
                )
                status.config(text="Saved")
                self._android_storage_refresh()
            except Exception as exc:
                status.config(text="Save failed")
                messagebox.showerror("Android Storage", str(exc), parent=dialog)
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

        ttk.Button(
            row, text="Close", style="Ghost.TButton", command=dialog.destroy
        ).pack(side="right")
        ttk.Button(
            row, text="Save", style="Accent.TButton", command=save_text
        ).pack(side="right", padx=(0, 8))

        def save_shortcut(_event):
            save_text()
            return "break"

        text_widget.bind("<Control-s>", save_shortcut)
        text_widget.focus_set()

    def _android_storage_new_folder(self) -> None:
        name = self._android_storage_prompt("New Folder", "Folder name:")
        if not name:
            return
        remote = self._android_join(self.android_storage_path_var.get(), name)
        def work():
            result = self._adb_shell_direct(f"mkdir {self._android_remote_quote(remote)}", timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "mkdir failed")
            self._android_storage_media_scan(base)
        self._android_storage_run_async("Creating folder", work)

    def _android_storage_rename(self) -> None:
        selected = self._android_storage_selected()
        if len(selected) != 1:
            messagebox.showinfo("Android Storage", "Select exactly one item to rename.")
            return
        old_name, _ = selected[0]
        new_name = self._android_storage_prompt("Rename", "New name:", old_name)
        if not new_name or new_name == old_name:
            return
        base = self.android_storage_path_var.get()
        old_remote = self._android_join(base, old_name)
        # Preserve the current extension for files when the user renames only
        # the base name. Folders are left exactly as typed.
        _selected_type = selected[0][1]
        _is_dir = str(_selected_type).lower() in {"folder", "directory", "dir", "true"}
        if not _is_dir:
            _old_suffix = Path(old_name).suffix
            if _old_suffix and not Path(new_name).suffix:
                new_name += _old_suffix

        new_remote = self._android_join(base, new_name)
        def work():
            result = self._adb_shell_direct(f"mv {self._android_remote_quote(old_remote)} {self._android_remote_quote(new_remote)}", timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "rename failed")
            self._android_storage_media_scan(base)
        self._android_storage_run_async("Renaming", work)

    def _android_storage_delete(self) -> None:
        selected = self._android_storage_selected()
        if not selected:
            messagebox.showinfo("Android Storage", "Select one or more items first.")
            return
        names = ", ".join(name for name, _ in selected[:5])
        if len(selected) > 5:
            names += f" and {len(selected) - 5} more"
        if not messagebox.askyesno(
            "Delete from Android?",
            f"Permanently delete {names} from the VM?\n\nThis cannot be undone.",
        ):
            return
        base = self.android_storage_path_var.get()
        def work():
            for name, is_dir in selected:
                remote = self._android_join(base, name)
                if is_dir:
                    result = self._adb_shell_direct(f"rm -rf {self._android_remote_quote(remote)}", timeout=60)
                else:
                    result = self._adb_shell_direct(f"rm -f {self._android_remote_quote(remote)}", timeout=30)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or f"delete failed for {name}")
            self._android_storage_media_scan(base)
        self._android_storage_run_async("Deleting", work)

    def _android_storage_prompt(self, title: str, prompt: str, initial: str = "") -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        tk.Label(dialog, text=prompt, bg=BG, fg=TEXT, font=FONT_BODY).pack(anchor="w", padx=16, pady=(14, 4))
        var = tk.StringVar(value=initial)
        entry = tk.Entry(dialog, textvariable=var, width=46, **ENTRY_KWARGS)
        entry.pack(fill="x", padx=16, ipady=3)
        result = {"value": None}
        def accept():
            value = var.get().strip()
            if not value or "/" in value or value in {".", ".."}:
                messagebox.showerror(title, "Enter a valid single file/folder name.", parent=dialog)
                return
            result["value"] = value
            dialog.destroy()
        row = tk.Frame(dialog, bg=BG)
        row.pack(fill="x", padx=16, pady=14)
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(row, text="OK", style="Accent.TButton", command=accept).pack(side="right", padx=(0, 8))
        entry.bind("<Return>", lambda _e: accept())
        entry.bind("<Escape>", lambda _e: dialog.destroy())
        entry.focus_set()
        entry.selection_range(0, "end")
        self.wait_window(dialog)
        return result["value"]

    # -- Installed Media Registry -------------------------------------------------

    @staticmethod
    def _media_registry_empty() -> dict:
        return {"version": 1, "games": {}}

    def _load_media_registry(self) -> dict:
        if not IIDB_REGISTRY_PATH.is_file():
            return self._media_registry_empty()
        try:
            data = json.loads(IIDB_REGISTRY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("games"), dict):
                raise ValueError("Unsupported or invalid installed media registry")
            return data
        except Exception as exc:
            _manager_log_write(f"MEDIA REGISTRY read error: {exc}")
            raise RuntimeError(f"Couldn't read installed media registry:\n{IIDB_REGISTRY_PATH}\n\n{exc}") from exc

    def _save_media_registry(self, registry: dict) -> None:
        IIDB_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
        temp = IIDB_REGISTRY_PATH.with_suffix(".json.tmp")
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, IIDB_REGISTRY_PATH)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _media_game_key(tab_id: str, rom_id: str) -> str:
        return f"{tab_id}|{rom_id}"

    def _register_installed_media(self, *, tab_id: str, rom_id: str, display_name: str,
                                  asset_dir: str, asset_type: str, slot: int,
                                  source_file: Path, iidb_asset_id=None,
                                  iidb_parent_id=None, remote_filename: str | None = None) -> dict:
        """Persist one successfully installed asset and a durable Windows copy."""
        source_file = Path(source_file)
        if not source_file.is_file():
            raise FileNotFoundError(source_file)
        extension = source_file.suffix.lower().lstrip(".") or "bin"
        safe_tab = re.sub(r"[^A-Za-z0-9._-]+", "_", tab_id).strip("._") or "unknown"
        safe_game = re.sub(r'[<>:"/\\|?*]+', "_", display_name).strip(" .") or "game"
        asset_id = str(iidb_asset_id) if iidb_asset_id is not None else self._sha256_file(source_file)[:16]
        relative = Path(safe_tab) / safe_game / asset_type / f"{asset_id}.{extension}"
        durable = IIDB_LIBRARY_DIR / relative
        durable.parent.mkdir(parents=True, exist_ok=True)
        try:
            same_file = source_file.resolve() == durable.resolve()
        except OSError:
            same_file = False
        if not same_file:
            shutil.copy2(source_file, durable)
        sha256 = self._sha256_file(durable)

        registry = self._load_media_registry()
        key = self._media_game_key(tab_id, rom_id)
        game = registry["games"].setdefault(key, {
            "tab_id": tab_id, "rom_id": rom_id, "display_name": display_name,
            "asset_dir": asset_dir, "assets": []
        })
        game.update({"tab_id": tab_id, "rom_id": rom_id, "display_name": display_name, "asset_dir": asset_dir})
        assets = game.setdefault("assets", [])
        # One restorable current asset per logical slot. Superseding an asset does not
        # leave an old entry that Restore All could accidentally reinstall afterward.
        assets[:] = [a for a in assets if not (a.get("asset_type") == asset_type and int(a.get("slot", 1)) == int(slot))]
        record = {
            "iidb_asset_id": iidb_asset_id,
            "iidb_parent_id": iidb_parent_id,
            "asset_type": asset_type,
            "slot": int(slot),
            "file": relative.as_posix(),
            "sha256": sha256,
            "extension": extension,
            "remote_filename": remote_filename or self._media_remote_filename(asset_type, int(slot), extension),
            "installed_at": datetime.now().isoformat(timespec="seconds"),
        }
        assets.append(record)
        self._save_media_registry(registry)
        _manager_log_write(f"MEDIA REGISTRY recorded {display_name!r} {asset_type}[{slot}] sha256={sha256}")
        return record

    @staticmethod
    def _media_remote_filename(asset_type: str, slot: int, extension: str) -> str:
        base = {
            "hero": f"hero_{slot}", "screenshot": f"slide_{slot}", "title": "title",
            "icon": "icon", "home_icon": "home_icon", "soundbite": "music",
            "portrait": "portrait",
        }.get(asset_type)
        if not base:
            raise ValueError(f"Unsupported media asset type: {asset_type}")
        return f"{base}.{extension}"

    def _mediabridge_ping(self) -> tuple[bool, str]:
        result = self._adb_command("shell", "am", "broadcast", "-a", MEDIABRIDGE_PING_ACTION,
                                   "-n", MEDIABRIDGE_COMPONENT, timeout=15)
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        return result.returncode == 0 and "result=1" in output and "IISUPC_MEDIABRIDGE_READY_V1" in output, output

    def _mediabridge_rescan_library(self) -> tuple[bool, str]:
        """Trigger iiSU's native Full Library Rescan through MediaBridge."""
        result = self._adb_command(
            "shell", "am", "broadcast",
            "-a", MEDIABRIDGE_RESCAN_ACTION,
            "-n", MEDIABRIDGE_COMPONENT,
            timeout=60,
        )
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        ok = (
            result.returncode == 0
            and "result=1" in output
            and "IISUPC_MEDIABRIDGE_RESCAN_STARTED_V1" in output
        )
        return ok, output

    def _mediabridge_install_file(self, game: dict, asset: dict, local_file: Path) -> tuple[bool, str]:
        extension = str(asset.get("extension") or local_file.suffix.lstrip(".")).lower()
        stage_name = f"iisupc-{uuid.uuid4().hex}.{extension}"
        stage_remote = f"{MEDIABRIDGE_INBOX}/{stage_name}"
        mkdir = self._adb_shell_direct(f"mkdir -p {self._android_remote_quote(MEDIABRIDGE_INBOX)}", timeout=15)
        if mkdir.returncode != 0:
            return False, (mkdir.stderr or mkdir.stdout or "Could not create MediaBridge inbox").strip()
        pushed = self._adb_command("push", str(local_file), stage_remote, timeout=300)
        if pushed.returncode != 0:
            return False, (pushed.stderr or pushed.stdout or "adb push failed").strip()
        try:
            # adb shell ultimately passes this through Android's shell. Build one
            # explicitly quoted command so values containing spaces, URI punctuation,
            # percent escapes, etc. remain one --es value. This is especially
            # important for asset_dir paths such as "Planet Coaster".
            extras = (
                ("tab_id", game.get("tab_id")),
                ("rom_id", game.get("rom_id")),
                ("asset_dir", game.get("asset_dir")),
                ("source", stage_remote),
                ("asset_type", asset.get("asset_type")),
                ("extension", extension),
            )
            missing = [name for name, value in extras if value is None or str(value) == ""]
            if missing:
                return False, "Manager registry is missing required field(s): " + ", ".join(missing)
            command = (
                f"am broadcast -a {self._android_remote_quote(MEDIABRIDGE_INSTALL_ACTION)} "
                f"-n {self._android_remote_quote(MEDIABRIDGE_COMPONENT)} "
                + " ".join(
                    f"--es {self._android_remote_quote(name)} {self._android_remote_quote(str(value))}"
                    for name, value in extras
                )
            )
            result = self._adb_shell_direct(command, timeout=60)
            output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            ok = result.returncode == 0 and "result=1" in output and "IISUPC_MEDIABRIDGE_INSTALLED_V1" in output
            return ok, output
        finally:
            self._adb_shell_direct(f"rm -f {self._android_remote_quote(stage_remote)}", timeout=15)

    def _media_asset_remote_path(self, game: dict, asset: dict) -> str:
        remote_filename = asset.get("remote_filename")
        if not remote_filename:
            extension = str(asset.get("extension") or Path(str(asset.get("file", ""))).suffix.lstrip(".") or "bin").lower()
            remote_filename = self._media_remote_filename(
                str(asset.get("asset_type", "")), int(asset.get("slot", 1)), extension
            )
        return str(game["asset_dir"]).rstrip("/") + "/" + str(remote_filename)

    @staticmethod
    def _media_local_file(asset: dict) -> Path:
        # Registry v1 paths are relative to iidb/library. Tolerate the early
        # bootstrap form that accidentally included a leading "library/".
        relative = Path(str(asset.get("file", "")))
        parts = relative.parts
        if parts and parts[0].lower() == "library":
            relative = Path(*parts[1:])
        return IIDB_LIBRARY_DIR / relative

    def _media_check_asset(self, game: dict, asset: dict) -> tuple[str, str]:
        local_file = self._media_local_file(asset)
        if not local_file.is_file():
            return "LOCAL_MISSING", f"Local copy missing: {local_file}"
        local_hash = self._sha256_file(local_file)
        expected = str(asset.get("sha256", "")).lower()
        if expected and local_hash.lower() != expected:
            return "LOCAL_CHANGED", "Local library file no longer matches its registry hash"
        remote = self._media_asset_remote_path(game, asset)
        result = self._adb_shell_direct(f"sha256sum {self._android_remote_quote(remote)}", timeout=20)
        if result.returncode != 0:
            return "REMOTE_MISSING", remote
        remote_hash = (result.stdout or "").strip().split(None, 1)[0].lower()
        if remote_hash == local_hash.lower():
            return "OK", remote_hash
        return "REMOTE_CHANGED", remote_hash or "Different file"

    def _build_media_library_page(self) -> None:
        frame = self.pages["media_library"]
        self._clear(frame)
        self._page_header(frame, "Media Library", "Durable iiDB artwork history and one-click recovery after iiSU rescans.")
        connection_row = tk.Frame(frame, bg=BG)
        connection_row.pack(fill="x", padx=24, pady=(0, 10))
        self.media_connection_dot = StatusDot(connection_row)
        self.media_connection_dot.pack(side="left", padx=(0, 8))
        self.media_connection_var = tk.StringVar(value="Checking VM connection...")
        tk.Label(connection_row, textvariable=self.media_connection_var, bg=BG, fg=TEXT_DIM,
                 font=FONT_BODY, anchor="w").pack(side="left")
        card = Card(frame)
        card.pack(fill="x", padx=24, pady=(0, 12))
        inner = tk.Frame(card, bg=PANEL_BG)
        inner.pack(fill="x", padx=16, pady=14)
        self.media_library_summary_var = tk.StringVar(value="Loading installed media registry...")
        tk.Label(inner, textvariable=self.media_library_summary_var, bg=PANEL_BG, fg=TEXT,
                 font=FONT_HEADING, anchor="w").pack(fill="x")
        self.media_library_detail_var = tk.StringVar(value="")
        tk.Label(inner, textvariable=self.media_library_detail_var, bg=PANEL_BG, fg=TEXT_DIM,
                 font=FONT_BODY, anchor="w", justify="left", wraplength=800).pack(fill="x", pady=(5, 0))
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", padx=24, pady=(0, 10))
        ttk.Button(row, text="Check iiSU Media", style="Ghost.TButton", command=self._media_library_check).pack(side="left")
        ttk.Button(row, text="Restore Missing", style="Accent.TButton", command=lambda: self._media_library_restore(False)).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Restore All", style="Ghost.TButton", command=lambda: self._media_library_restore(True)).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Open Local Library", style="Ghost.TButton", command=self._media_library_open).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Browse iiDB", style="Accent.TButton", command=self._iidb_open_browser).pack(side="right")

        # Game-first hierarchy plus a local preview pane. The viewer reads the
        # durable Media Library copy -- the exact file Manager will restore/commit
        # through MediaBridge -- rather than fetching a fresh iiDB preview.
        media_body = tk.PanedWindow(frame, orient="horizontal", bg=BG, sashwidth=5, bd=0)
        media_body.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        tree_frame = tk.Frame(media_body, bg=BG)
        preview_frame = tk.Frame(media_body, bg=PANEL_BG)
        media_body.add(tree_frame, minsize=500)
        media_body.add(preview_frame, minsize=250)

        columns = ("slot", "status", "file")
        self.media_library_tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", height=15)
        self.media_library_tree.heading("#0", text="Game / Asset")
        self.media_library_tree.column("#0", width=240, stretch=True)
        for col, title, width in (("slot","Slot",60),("status","Status",130),("file","Local file",300)):
            self.media_library_tree.heading(col, text=title)
            self.media_library_tree.column(col, width=width, stretch=(col == "file"))
        self.media_library_tree.pack(fill="both", expand=True)
        self.media_library_tree.bind("<<TreeviewSelect>>", self._media_library_asset_selected)

        tk.Label(preview_frame, text="Asset Viewer", bg=PANEL_BG, fg=TEXT,
                 font=FONT_HEADING, anchor="w").pack(fill="x", padx=12, pady=(12, 6))
        self.media_library_preview_label = tk.Label(
            preview_frame, text="Select an asset\nto preview",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="center", compound="top"
        )
        self.media_library_preview_label.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.media_library_preview_detail_var = tk.StringVar(value="")
        tk.Label(
            preview_frame, textvariable=self.media_library_preview_detail_var,
            bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
            anchor="w", wraplength=260
        ).pack(fill="x", padx=12, pady=(0, 8))
        preview_controls = tk.Frame(preview_frame, bg=PANEL_BG)
        preview_controls.pack(fill="x", padx=12, pady=(0, 12))
        self.media_library_audio_player = tk.Frame(preview_controls, bg="#E9EDF2", bd=0)
        self.media_library_audio_play_var = tk.StringVar(value="▶")
        self.media_library_audio_time_var = tk.StringVar(value="0:00 / 0:00")
        self.media_library_audio_play_button = tk.Button(
            self.media_library_audio_player, textvariable=self.media_library_audio_play_var,
            command=self._media_library_toggle_audio, bg="#E9EDF2", fg="#111111",
            activebackground="#DCE2E9", activeforeground="#111111", relief="flat",
            bd=0, font=("Segoe UI Symbol", 12, "bold"), width=2, cursor="hand2")
        self.media_library_audio_play_button.pack(side="left", padx=(10,4), pady=8)
        tk.Label(self.media_library_audio_player, textvariable=self.media_library_audio_time_var,
                 bg="#E9EDF2", fg="#202020", font=("Segoe UI",9)).pack(side="left", padx=(0,7))
        self.media_library_audio_seek = ttk.Scale(
            self.media_library_audio_player, from_=0, to=1000, orient="horizontal",
            command=self._media_library_seek_preview)
        self.media_library_audio_seek.pack(side="left", fill="x", expand=True, padx=(0,8))
        tk.Label(self.media_library_audio_player, text="🔊", bg="#E9EDF2", fg="#111111",
                 font=("Segoe UI Emoji",10)).pack(side="left", padx=(0,3))
        self.media_library_audio_volume = ttk.Scale(
            self.media_library_audio_player, from_=0, to=1000, orient="horizontal",
            command=self._media_library_set_volume)
        self.media_library_audio_volume.set(850)
        self.media_library_audio_volume.pack(side="left", padx=(0,10))
        self.media_library_open_file_button = ttk.Button(
            preview_controls, text="Open File", style="Ghost.TButton",
            command=self._media_library_open_selected_file
        )
        self.media_library_open_file_button.pack(fill="x")
        self._media_library_tree_assets = {}
        self._media_library_selected = None
        self._media_library_preview_photo = None
        self._media_library_audio_alias = None
        self._media_library_audio_state = "stopped"
        self._media_library_audio_length_ms = 0
        self._media_library_audio_after = None
        self._media_library_audio_loading = False
        self._media_library_seek_internal = False
        self._media_library_audio_token = None

        self._media_library_refresh()
        self._update_media_connection_indicator(self._last_avd_up)

    def _media_library_records(self):
        registry = self._load_media_registry()
        for key, game in registry.get("games", {}).items():
            if not isinstance(game, dict):
                continue
            for asset in game.get("assets", []):
                if isinstance(asset, dict):
                    yield key, game, asset

    def _update_media_connection_indicator(self, avd_up: bool | None) -> None:
        if not hasattr(self, "media_connection_var"):
            return
        if avd_up is not True:
            self.media_connection_dot.set_state("unknown" if avd_up is None else "down")
            self.media_connection_var.set("VM status unknown" if avd_up is None else "VM Disconnected")
            return
        # Avoid launching a PING every 2-second global status poll. A five-second
        # cadence is responsive enough for a visual readiness indicator.
        now = time.monotonic()
        if self._media_ping_inflight or now - self._media_last_ping_at < 5.0:
            return
        self._media_ping_inflight = True
        self.media_connection_dot.set_state("unknown")
        self.media_connection_var.set("VM Connected • Checking MediaBridge…")
        def worker():
            try:
                ok, _detail = self._mediabridge_ping()
            except Exception:
                ok = False
            def done():
                self._media_ping_inflight = False
                self._media_last_ping_at = time.monotonic()
                if not hasattr(self, "media_connection_var"):
                    return
                if ok:
                    self.media_connection_dot.set_state("up")
                    self.media_connection_var.set("VM Connected • MediaBridge Ready")
                else:
                    self.media_connection_dot.set_state("down")
                    self.media_connection_var.set("VM Connected • MediaBridge Unavailable")
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _media_library_tree_open_games(self) -> set[str]:
        if not hasattr(self, "media_library_tree"):
            return set()
        result=set()
        for iid in self.media_library_tree.get_children(""):
            try:
                if self.media_library_tree.item(iid, "open"):
                    result.add(str(self.media_library_tree.item(iid, "text")))
            except Exception:
                pass
        return result

    def _media_library_populate_tree(self, rows, checked: bool = False) -> None:
        """Populate one parent row per game and child rows for individual assets.

        rows may contain either (key, game, asset) or
        (key, game, asset, status, info). Expansion state is preserved by game name.
        """
        tree=self.media_library_tree
        open_games=self._media_library_tree_open_games()
        selected_record = getattr(self, "_media_library_selected", None)
        for item in tree.get_children(""):
            tree.delete(item)
        self._media_library_tree_assets = {}
        grouped={}
        for row in rows:
            key,game,asset=row[:3]
            status=row[3] if len(row) >= 4 else "Saved"
            grouped.setdefault(key,{"game":game,"items":[]})["items"].append((asset,status))
        for gidx,(key,bucket) in enumerate(grouped.items()):
            game=bucket["game"]; items=bucket["items"]
            name=game.get("display_name","Unknown")
            bad=sum(1 for _asset,status in items if status not in {"OK","Saved"})
            if checked:
                overall="All OK" if bad == 0 else f"{bad} need attention"
            else:
                overall="Saved"
            count=len(items)
            parent=tree.insert("","end",iid=f"game-{gidx}",text=name,
                               values=("",f"{count} asset{'s' if count != 1 else ''} • {overall}",""),
                               open=(name in open_games))
            for aidx,(asset,status) in enumerate(items):
                label=str(asset.get("asset_type","?")).replace("_"," ").title()
                display_status=str(status).replace("_"," ").title()
                child_iid=f"game-{gidx}-asset-{aidx}"
                tree.insert(parent,"end",iid=child_iid,text=label,
                            values=(asset.get("slot",1),display_status,asset.get("file","")))
                self._media_library_tree_assets[child_iid]=(game,asset)

    def _media_library_clear_preview(self, text: str = "Select an asset\nto preview") -> None:
        self._media_library_stop_audio()
        self._media_library_selected = None
        self._media_library_preview_photo = None
        if hasattr(self, "media_library_preview_label"):
            self.media_library_preview_label.configure(image="", text=text)
        if hasattr(self, "media_library_preview_detail_var"):
            self.media_library_preview_detail_var.set("")
        if hasattr(self, "media_library_audio_player"):
            self.media_library_audio_player.pack_forget()

    def _media_library_asset_selected(self, _event=None) -> None:
        selected = self.media_library_tree.selection()
        if not selected:
            self._media_library_clear_preview()
            return
        record = self._media_library_tree_assets.get(selected[0])
        if record is None:
            self._media_library_clear_preview("Select one of this game's\nassets to preview")
            return

        self._media_library_stop_audio()
        game, asset = record
        self._media_library_selected = (game, asset)
        local = self._media_local_file(asset)
        asset_type = str(asset.get("asset_type", "?"))
        slot = int(asset.get("slot", 1))
        detail = [
            str(game.get("display_name", "Unknown")),
            f"{asset_type.replace('_', ' ').title()} • Slot {slot}",
            local.name,
        ]
        try:
            detail.append(self._iidb_human_size(local.stat().st_size))
        except OSError:
            pass
        self.media_library_preview_detail_var.set("\n".join(x for x in detail if x))

        self.media_library_audio_player.pack_forget()
        self._media_library_preview_photo = None

        if not local.is_file():
            self.media_library_preview_label.configure(image="", text="Local library file\nis missing")
            return

        if asset_type == "soundbite":
            self.media_library_preview_label.configure(image="", text="♪\nSoundbite")
            self.media_library_audio_time_var.set("0:00 / 0:00")
            self.media_library_audio_play_var.set("▶")
            self.media_library_audio_seek.set(0)
            self.media_library_audio_player.pack(fill="x", pady=(0,8), before=self.media_library_open_file_button)
            return

        try:
            from PIL import Image, ImageTk
            image = Image.open(local).convert("RGB")
            width, height = image.size
            image.thumbnail((280, 380))
            photo = ImageTk.PhotoImage(image)
            self._media_library_preview_photo = photo
            self.media_library_preview_label.configure(image=photo, text="")
            current = self.media_library_preview_detail_var.get()
            self.media_library_preview_detail_var.set(current + f"\n{width}×{height}")
        except ImportError:
            self.media_library_preview_label.configure(
                image="", text="Image preview requires Pillow.\n\nThe saved file can still be\nopened with Open File."
            )
        except Exception as exc:
            self.media_library_preview_label.configure(image="", text="Preview unavailable")
            _manager_log_write(f"MEDIA LIBRARY preview failed path={str(local)!r}: {exc}")

    def _media_library_open_selected_file(self) -> None:
        record = getattr(self, "_media_library_selected", None)
        if not record:
            return
        _game, asset = record
        local = self._media_local_file(asset)
        if not local.is_file():
            messagebox.showwarning("Media Library", f"Local file not found:\n{local}")
            return
        try:
            os.startfile(str(local))
        except Exception as exc:
            messagebox.showerror("Media Library", f"Couldn't open:\n{local}\n\n{exc}")

    @staticmethod
    def _media_library_format_ms(value: int) -> str:
        seconds=max(0,int(value)//1000)
        return f"{seconds//60}:{seconds%60:02d}"

    def _media_library_audio_load(self, path: Path) -> dict:
        """Load a standard PCM WAV for the native Windows waveOut previewer."""
        import wave
        with wave.open(str(path), "rb") as wav:
            channels=wav.getnchannels()
            width=wav.getsampwidth()
            rate=wav.getframerate()
            frames=wav.getnframes()
            comptype=wav.getcomptype()
            if comptype!="NONE":
                raise RuntimeError(f"Unsupported WAV compression: {comptype}")
            if width not in (1,2):
                raise RuntimeError(f"Unsupported WAV sample width: {width*8}-bit")
            if channels not in (1,2):
                raise RuntimeError(f"Unsupported WAV channel count: {channels}")
            pcm=wav.readframes(frames)
        return {
            "path":path, "channels":channels, "width":width, "rate":rate,
            "frames":frames, "pcm":pcm,
            "length_ms":int((frames*1000)/rate) if rate else 0,
            "block_align":channels*width,
        }

    def _media_library_waveout_error(self, code: int, operation: str) -> None:
        if code:
            raise RuntimeError(f"Windows waveOut {operation} failed (MMRESULT {code})")

    def _media_library_waveout_open(self, audio: dict) -> None:
        import ctypes
        from ctypes import wintypes

        class WAVEFORMATEX(ctypes.Structure):
            _fields_=[
                ("wFormatTag",wintypes.WORD),
                ("nChannels",wintypes.WORD),
                ("nSamplesPerSec",wintypes.DWORD),
                ("nAvgBytesPerSec",wintypes.DWORD),
                ("nBlockAlign",wintypes.WORD),
                ("wBitsPerSample",wintypes.WORD),
                ("cbSize",wintypes.WORD),
            ]
        class WAVEHDR(ctypes.Structure):
            _fields_=[
                ("lpData",ctypes.c_void_p),
                ("dwBufferLength",wintypes.DWORD),
                ("dwBytesRecorded",wintypes.DWORD),
                ("dwUser",ctypes.c_size_t),
                ("dwFlags",wintypes.DWORD),
                ("dwLoops",wintypes.DWORD),
                ("lpNext",ctypes.c_void_p),
                ("reserved",ctypes.c_size_t),
            ]

        winmm=ctypes.WinDLL("winmm")
        fmt=WAVEFORMATEX()
        fmt.wFormatTag=1
        fmt.nChannels=audio["channels"]
        fmt.nSamplesPerSec=audio["rate"]
        fmt.wBitsPerSample=audio["width"]*8
        fmt.nBlockAlign=audio["block_align"]
        fmt.nAvgBytesPerSec=audio["rate"]*audio["block_align"]
        fmt.cbSize=0

        handle=ctypes.c_void_p()
        result=winmm.waveOutOpen(ctypes.byref(handle),0xFFFFFFFF,ctypes.byref(fmt),0,0,0)
        self._media_library_waveout_error(result,"open")

        self._media_library_waveout_handle=handle
        self._media_library_waveout_winmm=winmm
        self._media_library_waveout_header_type=WAVEHDR
        self._media_library_waveout_fmt=fmt
        self._media_library_set_volume(self.media_library_audio_volume.get())

    def _media_library_waveout_submit_from(self, position_ms: int) -> None:
        import ctypes
        audio=self._media_library_audio_data
        handle=self._media_library_waveout_handle
        winmm=self._media_library_waveout_winmm
        WAVEHDR=self._media_library_waveout_header_type

        frame=max(0,min(audio["frames"],int(position_ms*audio["rate"]/1000)))
        byte_offset=frame*audio["block_align"]
        chunk=audio["pcm"][byte_offset:]
        if not chunk:
            self._media_library_audio_state="stopped"
            return

        buf=ctypes.create_string_buffer(chunk)
        hdr=WAVEHDR()
        hdr.lpData=ctypes.cast(buf,ctypes.c_void_p)
        hdr.dwBufferLength=len(chunk)
        hdr.dwBytesRecorded=0
        hdr.dwUser=0
        hdr.dwFlags=0
        hdr.dwLoops=0
        hdr.lpNext=None
        hdr.reserved=0

        result=winmm.waveOutPrepareHeader(handle,ctypes.byref(hdr),ctypes.sizeof(hdr))
        self._media_library_waveout_error(result,"prepare")
        result=winmm.waveOutWrite(handle,ctypes.byref(hdr),ctypes.sizeof(hdr))
        if result:
            winmm.waveOutUnprepareHeader(handle,ctypes.byref(hdr),ctypes.sizeof(hdr))
            self._media_library_waveout_error(result,"write")

        # Keep both objects alive until playback is reset/unprepared.
        self._media_library_waveout_buffer=buf
        self._media_library_waveout_header=hdr
        self._media_library_audio_base_ms=position_ms
        self._media_library_audio_started_at=time.monotonic()

    def _media_library_waveout_release_buffer(self) -> None:
        import ctypes
        handle=getattr(self,"_media_library_waveout_handle",None)
        hdr=getattr(self,"_media_library_waveout_header",None)
        winmm=getattr(self,"_media_library_waveout_winmm",None)
        if handle and hdr is not None and winmm:
            winmm.waveOutReset(handle)
            winmm.waveOutUnprepareHeader(handle,ctypes.byref(hdr),ctypes.sizeof(hdr))
        self._media_library_waveout_header=None
        self._media_library_waveout_buffer=None

    def _media_library_toggle_audio(self)->None:
        if getattr(self,"_media_library_audio_loading",False):return
        state=getattr(self,"_media_library_audio_state","stopped")
        handle=getattr(self,"_media_library_waveout_handle",None)

        if state=="playing" and handle:
            result=self._media_library_waveout_winmm.waveOutPause(handle)
            try:self._media_library_waveout_error(result,"pause")
            except Exception as exc:
                messagebox.showerror("Media Library",f"Soundbite preview failed.\n\n{exc}");return
            elapsed=int((time.monotonic()-self._media_library_audio_started_at)*1000)
            self._media_library_audio_base_ms=min(
                self._media_library_audio_length_ms,
                self._media_library_audio_base_ms+elapsed)
            self._media_library_audio_state="paused"
            self.media_library_audio_play_var.set("▶")
            return

        if state=="paused" and handle:
            result=self._media_library_waveout_winmm.waveOutRestart(handle)
            try:self._media_library_waveout_error(result,"resume")
            except Exception as exc:
                messagebox.showerror("Media Library",f"Soundbite preview failed.\n\n{exc}");return
            self._media_library_audio_started_at=time.monotonic()
            self._media_library_audio_state="playing"
            self.media_library_audio_play_var.set("Ⅱ")
            self._media_library_schedule_audio_tick()
            return

        if state=="stopped" and getattr(self,"_media_library_audio_data",None):
            self._media_library_seek_to_ms(0,autoplay=True)
            return

        self._media_library_play_soundbite()

    def _media_library_play_soundbite(self)->None:
        record=getattr(self,"_media_library_selected",None)
        if not record:return
        _game,asset=record
        if str(asset.get("asset_type","")).lower()!="soundbite":return
        local=self._media_local_file(asset)
        if not local.is_file():
            messagebox.showwarning("Media Library",f"Local soundbite not found:\n{local}");return

        self._media_library_stop_audio()
        self._media_library_audio_loading=True
        self.media_library_audio_play_var.set("…")
        try:
            audio=self._media_library_audio_load(local)
            self._media_library_audio_data=audio
            self._media_library_audio_length_ms=audio["length_ms"]
            self.media_library_audio_seek.configure(to=max(1,audio["length_ms"]))
            self._media_library_waveout_open(audio)
            self._media_library_waveout_submit_from(0)
            self._media_library_audio_state="playing"
            self.media_library_audio_play_var.set("Ⅱ")
            self.media_library_audio_time_var.set(
                f"0:00 / {self._media_library_format_ms(audio['length_ms'])}")
            self._media_library_schedule_audio_tick()
        except Exception as exc:
            self._media_library_stop_audio()
            messagebox.showerror(
                "Media Library",
                "Soundbite preview failed.\n\n"
                "The saved original is still intact and will continue to be used by iiSU.\n\n"
                f"{exc}")
        finally:
            self._media_library_audio_loading=False

    def _media_library_schedule_audio_tick(self)->None:
        if self._media_library_audio_after is not None:
            try:self.after_cancel(self._media_library_audio_after)
            except Exception:pass
        self._media_library_audio_after=self.after(100,self._media_library_audio_tick)

    def _media_library_audio_position_ms(self)->int:
        base=int(getattr(self,"_media_library_audio_base_ms",0))
        if getattr(self,"_media_library_audio_state","stopped")=="playing":
            base+=int((time.monotonic()-getattr(self,"_media_library_audio_started_at",time.monotonic()))*1000)
        return max(0,min(int(getattr(self,"_media_library_audio_length_ms",0)),base))

    def _media_library_audio_tick(self)->None:
        self._media_library_audio_after=None
        if getattr(self,"_media_library_audio_state","stopped") not in ("playing","paused"):return
        position=self._media_library_audio_position_ms()
        length=int(getattr(self,"_media_library_audio_length_ms",0))
        self._media_library_seek_internal=True
        try:self.media_library_audio_seek.set(position)
        finally:self._media_library_seek_internal=False
        self.media_library_audio_time_var.set(
            f"{self._media_library_format_ms(position)} / {self._media_library_format_ms(length)}")
        if length and position>=length:
            self._media_library_audio_state="stopped"
            self._media_library_audio_base_ms=0
            self.media_library_audio_play_var.set("▶")
            self._media_library_seek_internal=True
            try:self.media_library_audio_seek.set(0)
            finally:self._media_library_seek_internal=False
            self.media_library_audio_time_var.set(f"0:00 / {self._media_library_format_ms(length)}")
            return
        self._media_library_schedule_audio_tick()

    def _media_library_seek_to_ms(self,target:int,autoplay:bool|None=None)->None:
        if not getattr(self,"_media_library_audio_data",None):return
        target=max(0,min(int(getattr(self,"_media_library_audio_length_ms",0)),int(target)))
        old_state=getattr(self,"_media_library_audio_state","stopped")
        if autoplay is None:autoplay=(old_state=="playing")
        try:
            self._media_library_waveout_release_buffer()
            self._media_library_waveout_submit_from(target)
            if not autoplay:
                result=self._media_library_waveout_winmm.waveOutPause(self._media_library_waveout_handle)
                self._media_library_waveout_error(result,"pause")
                self._media_library_audio_state="paused"
                self.media_library_audio_play_var.set("▶")
            else:
                self._media_library_audio_state="playing"
                self.media_library_audio_play_var.set("Ⅱ")
                self._media_library_schedule_audio_tick()
        except Exception as exc:
            messagebox.showerror("Media Library",f"Couldn't seek soundbite.\n\n{exc}")

    def _media_library_seek_preview(self,value)->None:
        if self._media_library_seek_internal:return
        if not getattr(self,"_media_library_audio_data",None):return
        try:target=int(float(value))
        except Exception:return
        # ttk.Scale invokes command continuously while dragging. Debounce so
        # waveOut isn't repeatedly torn down for every pixel of mouse motion.
        pending=getattr(self,"_media_library_seek_after",None)
        if pending is not None:
            try:self.after_cancel(pending)
            except Exception:pass
        self._media_library_seek_after=self.after(
            120,lambda t=target:self._media_library_seek_commit(t))

    def _media_library_seek_commit(self,target:int)->None:
        self._media_library_seek_after=None
        self._media_library_seek_to_ms(target)

    def _media_library_set_volume(self,value)->None:
        handle=getattr(self,"_media_library_waveout_handle",None)
        if not handle:return
        try:
            level=max(0,min(1000,int(float(value))))
            word=int(level*0xFFFF/1000)
            packed=(word<<16)|word
            result=self._media_library_waveout_winmm.waveOutSetVolume(handle,packed)
            self._media_library_waveout_error(result,"volume")
        except Exception:
            pass

    def _media_library_stop_audio(self,reset_ui:bool=True)->None:
        import ctypes
        if self._media_library_audio_after is not None:
            try:self.after_cancel(self._media_library_audio_after)
            except Exception:pass
            self._media_library_audio_after=None
        pending=getattr(self,"_media_library_seek_after",None)
        if pending is not None:
            try:self.after_cancel(pending)
            except Exception:pass
            self._media_library_seek_after=None
        handle=getattr(self,"_media_library_waveout_handle",None)
        winmm=getattr(self,"_media_library_waveout_winmm",None)
        hdr=getattr(self,"_media_library_waveout_header",None)
        if handle and winmm:
            try:
                winmm.waveOutReset(handle)
                if hdr is not None:
                    winmm.waveOutUnprepareHeader(handle,ctypes.byref(hdr),ctypes.sizeof(hdr))
                winmm.waveOutClose(handle)
            except Exception:pass
        self._media_library_waveout_handle=None
        self._media_library_waveout_header=None
        self._media_library_waveout_buffer=None
        self._media_library_waveout_winmm=None
        self._media_library_audio_data=None
        self._media_library_audio_state="stopped"
        self._media_library_audio_loading=False
        self._media_library_audio_length_ms=0
        self._media_library_audio_base_ms=0
        if reset_ui and hasattr(self,"media_library_audio_play_var"):
            self.media_library_audio_play_var.set("▶")
            self.media_library_audio_time_var.set("0:00 / 0:00")
            self._media_library_seek_internal=True
            try:self.media_library_audio_seek.set(0)
            finally:self._media_library_seek_internal=False

    def _media_library_refresh(self) -> None:
        if not hasattr(self, "media_library_tree"):
            return
        try:
            rows = list(self._media_library_records())
        except Exception as exc:
            self.media_library_summary_var.set("Installed media registry error")
            self.media_library_detail_var.set(str(exc)); return
        games = len({key for key, _, _ in rows})
        total_bytes = 0
        for _key, _game, asset in rows:
            local = self._media_local_file(asset)
            try: total_bytes += local.stat().st_size
            except OSError: pass
        self._media_library_populate_tree(rows, checked=False)
        self.media_library_summary_var.set(f"{games} game{'s' if games != 1 else ''} • {len(rows)} saved asset{'s' if len(rows) != 1 else ''} • {total_bytes / (1024*1024):.1f} MB")
        self.media_library_detail_var.set(f"Registry: {IIDB_REGISTRY_PATH}\nLibrary: {IIDB_LIBRARY_DIR}")

    def _media_library_open(self) -> None:
        IIDB_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(IIDB_LIBRARY_DIR))

    def _media_library_check(self) -> None:
        ready, detail = self._adb_device_ready()
        if not ready:
            messagebox.showwarning("Media Library", "Start the Android VM before checking iiSU media.\n\n" + detail); return
        self.media_library_summary_var.set("Checking iiSU media...")
        def worker():
            rows=[]
            try:
                for key, game, asset in self._media_library_records():
                    status, info = self._media_check_asset(game, asset); rows.append((key,game,asset,status,info))
                self.after(0, self._media_library_show_check_results, rows)
            except Exception as exc:
                self.after(0, lambda e=str(exc): messagebox.showerror("Media Library", e))
        threading.Thread(target=worker, daemon=True).start()

    def _media_library_show_check_results(self, rows) -> None:
        missing=sum(1 for _key,_game,_asset,status,_info in rows if status != "OK")
        self._media_library_populate_tree(rows, checked=True)
        self.media_library_summary_var.set(f"Check complete • {len(rows)-missing} correct • {missing} need attention")

    def _media_library_restore(self, restore_all: bool) -> None:
        ready, detail = self._adb_device_ready()
        if not ready:
            messagebox.showwarning("Media Library", "Start the Android VM before restoring media.\n\n" + detail); return
        ping_ok, ping_detail = self._mediabridge_ping()
        if not ping_ok:
            messagebox.showerror("Media Library", "MediaBridge V1 is not ready. Repatch iiSU first.\n\n" + ping_detail); return
        if restore_all and not messagebox.askyesno("Restore All Media", "Reinstall every saved media asset through MediaBridge?\n\nThis intentionally replaces the corresponding iiSU media slots with the saved copies."):
            return
        self.media_library_summary_var.set("Preparing restore...")
        def worker():
            restored=skipped=failed=0; failures=[]
            for _key, game, asset in self._media_library_records():
                local = self._media_local_file(asset)
                if not local.is_file(): failed += 1; failures.append(f"{game.get('display_name')}: local copy missing"); continue
                if not restore_all:
                    status, _ = self._media_check_asset(game, asset)
                    if status == "OK": skipped += 1; continue
                    if status.startswith("LOCAL_"): failed += 1; failures.append(f"{game.get('display_name')}: {status}"); continue
                ok, output = self._mediabridge_install_file(game, asset, local)
                if ok: restored += 1
                else:
                    failed += 1
                    match = re.search(r'IISUPC_MEDIABRIDGE_ERROR_V1:([A-Z0-9_]+)', output or "")
                    detail = f"MediaBridge: {match.group(1)}" if match else (output or "Unknown restore error")
                    failures.append(f"{game.get('display_name')} {asset.get('asset_type')}: {detail}")
            def done():
                self._media_library_refresh()
                self.media_library_summary_var.set(f"Restore complete • {restored} restored • {skipped} already correct • {failed} failed")
                if failures: messagebox.showwarning("Media Restore", "Some assets could not be restored:\n\n" + "\n".join(failures[:10]))
                else: messagebox.showinfo("Media Restore", f"Restore complete.\n\nRestored: {restored}\nAlready correct: {skipped}")
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    # -- iiDB browser ---------------------------------------------------------------

    @staticmethod
    def _iidb_json_get(path: str, params: dict | None = None, timeout: int = 15):
        """Read one iiDB JSON endpoint. iiDB is currently an unauthenticated public API,
        but it is not treated as a stable contract; callers validate fields defensively."""
        import urllib.parse
        import urllib.request
        url = IIDB_API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": "iiSU-PC Manager",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _iidb_find_dicts(value):
        """Yield dictionaries nested in a JSON response, preserving API flexibility."""
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from Manager._iidb_find_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from Manager._iidb_find_dicts(child)

    @staticmethod
    def _iidb_human_size(value) -> str:
        try:
            size = float(value)
        except (TypeError, ValueError):
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return ""

    def _iidb_open_browser(self) -> None:
        if getattr(self, "_iidb_browser_window", None) is not None:
            try:
                if self._iidb_browser_window.winfo_exists():
                    self._iidb_browser_window.lift(); self._iidb_browser_window.focus_force(); return
            except Exception:
                pass

        win = tk.Toplevel(self)
        self._iidb_browser_window = win
        win.title("Browse iiDB Media")
        win.geometry("1180x760")
        win.minsize(940, 620)
        win.configure(bg=BG)
        def close_browser():
            self._iidb_stop_audio()
            self._iidb_browser_window = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close_browser)

        top = tk.Frame(win, bg=BG); top.pack(fill="x", padx=18, pady=(16, 10))
        title_row=tk.Frame(top,bg=BG); title_row.pack(fill="x")
        tk.Label(title_row, text="Browse iiDB", bg=BG, fg=TEXT, font=FONT_TITLE).pack(side="left")
        self.iidb_cart_count_var=tk.StringVar(value="Cart (0)")
        ttk.Button(title_row,textvariable=self.iidb_cart_count_var,style="Accent.TButton",command=self._iidb_open_cart).pack(side="right")
        tk.Label(top, text="Search, preview, collect, and install iiDB media through MediaBridge.",
                 bg=BG, fg=TEXT_DIM, font=FONT_BODY).pack(anchor="w", pady=(2, 10))
        search_row = tk.Frame(top, bg=BG); search_row.pack(fill="x")
        self.iidb_search_var = tk.StringVar(); entry = ttk.Entry(search_row, textvariable=self.iidb_search_var)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="Search", style="Accent.TButton", command=self._iidb_search).pack(side="left", padx=(8, 0))
        self.iidb_status_var = tk.StringVar(value="Search for a game to begin. No VM connection is required.")
        tk.Label(top, textvariable=self.iidb_status_var, bg=BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w").pack(fill="x", pady=(8, 0))
        entry.bind("<Return>", lambda _e: self._iidb_search())

        body = tk.PanedWindow(win, orient="horizontal", bg=BG, sashwidth=5, bd=0); body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        left = tk.Frame(body, bg=PANEL_BG); right = tk.Frame(body, bg=PANEL_BG); body.add(left, minsize=300); body.add(right, minsize=600)
        tk.Label(left, text="Search Results", bg=PANEL_BG, fg=TEXT, font=FONT_HEADING, anchor="w").pack(fill="x", padx=12, pady=(12, 6))
        self.iidb_results_tree = ttk.Treeview(left, columns=("name","subtitle"), show="headings", height=18)
        self.iidb_results_tree.heading("name", text="Game"); self.iidb_results_tree.heading("subtitle", text="Platform / Details")
        self.iidb_results_tree.column("name", width=190); self.iidb_results_tree.column("subtitle", width=130)
        self.iidb_results_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12)); self.iidb_results_tree.bind("<<TreeviewSelect>>", self._iidb_result_selected)

        self.iidb_game_title_var = tk.StringVar(value="Select a game"); self.iidb_game_detail_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.iidb_game_title_var, bg=PANEL_BG, fg=TEXT, font=FONT_HEADING, anchor="w").pack(fill="x", padx=12, pady=(12, 2))
        tk.Label(right, textvariable=self.iidb_game_detail_var, bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY, anchor="w").pack(fill="x", padx=12)
        self.iidb_category_frame = tk.Frame(right, bg=PANEL_BG); self.iidb_category_frame.pack(fill="x", padx=12, pady=(10, 8))

        lower = tk.PanedWindow(right, orient="horizontal", bg=PANEL_BG, sashwidth=4, bd=0); lower.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        list_frame = tk.Frame(lower, bg=PANEL_BG); preview_frame = tk.Frame(lower, bg=PANEL_BG); lower.add(list_frame, minsize=430); lower.add(preview_frame, minsize=240)
        cols=("type","resolution","size","filename"); self.iidb_assets_tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=17)
        for col,title,width in (("type","Type",90),("resolution","Resolution",100),("size","Size",75),("filename","Filename",180)):
            self.iidb_assets_tree.heading(col,text=title); self.iidb_assets_tree.column(col,width=width,stretch=(col=="filename"))
        self.iidb_assets_tree.pack(fill="both", expand=True); self.iidb_assets_tree.bind("<<TreeviewSelect>>", self._iidb_asset_selected)

        self.iidb_preview_label = tk.Label(preview_frame, text="Select an asset\nto preview", bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="center", compound="top")
        self.iidb_preview_label.pack(fill="both", expand=True, padx=(10,0))
        self.iidb_preview_detail_var = tk.StringVar(value="")
        tk.Label(preview_frame, textvariable=self.iidb_preview_detail_var, bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY, justify="left", anchor="w", wraplength=250).pack(fill="x", padx=(10,0), pady=(8,0))
        controls=tk.Frame(preview_frame,bg=PANEL_BG); controls.pack(fill="x",padx=(10,0),pady=(8,0))
        self.iidb_audio_button=ttk.Button(controls,text="Play Soundbite",style="Ghost.TButton",command=self._iidb_play_selected_soundbite)
        self.iidb_stop_audio_button=ttk.Button(controls,text="Stop",style="Ghost.TButton",command=self._iidb_stop_audio)
        self.iidb_cart_asset_var=tk.StringVar(value="Add to Cart")
        self.iidb_cart_asset_button=ttk.Button(controls,textvariable=self.iidb_cart_asset_var,style="Accent.TButton",command=self._iidb_toggle_selected_cart)
        self.iidb_cart_asset_button.pack(fill="x")

        self._iidb_search_results=[]; self._iidb_assets=[]; self._iidb_filtered_assets=[]; self._iidb_current_parent_id=None
        self._iidb_current_game=None; self._iidb_selected_asset=None; self._iidb_preview_photo=None; self._iidb_cart={}; self._iidb_audio_alias=None
        entry.focus_set()

    def _iidb_search(self) -> None:
        query=self.iidb_search_var.get().strip()
        if not query:return
        self.iidb_status_var.set(f"Searching iiDB for {query!r}…")
        for item in self.iidb_results_tree.get_children(): self.iidb_results_tree.delete(item)
        def worker():
            try:
                data=self._iidb_json_get("/search/suggestions",{"q":query,"mode":"default","parent_limit":14,"platform_limit":4})
                found=[];seen=set()
                for d in self._iidb_find_dicts(data):
                    kind=str(d.get("kind","")).lower(); pid=d.get("parent_id",d.get("id")); name=d.get("name") or d.get("title")
                    if pid is None or not name:continue
                    if kind and "parent" not in kind and "game" not in kind:continue
                    key=str(pid)
                    if key in seen:continue
                    seen.add(key);found.append({"id":pid,"name":str(name),"subtitle":str(d.get("subtitle") or d.get("platform_name") or ""),"raw":d})
                self.after(0,lambda:self._iidb_show_search_results(found))
            except Exception as exc:self.after(0,lambda e=str(exc):self.iidb_status_var.set("iiDB search failed: "+e))
        threading.Thread(target=worker,daemon=True).start()

    def _iidb_show_search_results(self, results) -> None:
        self._iidb_search_results=results
        for item in self.iidb_results_tree.get_children():self.iidb_results_tree.delete(item)
        for idx,row in enumerate(results):self.iidb_results_tree.insert("","end",iid=f"iidb-result-{idx}",values=(row["name"],row["subtitle"]))
        self.iidb_status_var.set(f"Found {len(results)} game result{'s' if len(results)!=1 else ''}.") if results else self.iidb_status_var.set("No game results found.")

    def _iidb_result_selected(self, _event=None) -> None:
        selected=self.iidb_results_tree.selection()
        if not selected:return
        try:idx=int(selected[0].rsplit("-",1)[1]);row=self._iidb_search_results[idx]
        except Exception:return
        self._iidb_stop_audio(); self._iidb_current_parent_id=row["id"]; self._iidb_current_game=row; self._iidb_selected_asset=None
        self.iidb_game_title_var.set(row["name"]);self.iidb_game_detail_var.set(f"iiDB parent ID: {row['id']} • Loading asset catalog…");self.iidb_status_var.set(f"Loading {row['name']} metadata and previews…")
        for item in self.iidb_assets_tree.get_children():self.iidb_assets_tree.delete(item)
        for child in self.iidb_category_frame.winfo_children():child.destroy()
        parent_id=row["id"]
        def worker():
            try:
                landing=self._iidb_json_get(f"/parents/{parent_id}/landing");assets=[];skip=0;limit=50;seen=set()
                while True:
                    page=self._iidb_json_get("/assets/browse/enriched",{"skip":skip,"limit":limit,"parent_id":parent_id},timeout=20);page_assets=[]
                    for d in self._iidb_find_dicts(page):
                        if d.get("type") and (d.get("raw_url") or d.get("preview_url") or d.get("library_preview_url")):
                            marker=(str(d.get("id",d.get("asset_id"))),str(d.get("filename")))
                            if marker not in seen:seen.add(marker);page_assets.append(d)
                    assets.extend(page_assets)
                    if len(page_assets)<limit or len(assets)>=1000:break
                    skip+=limit
                self.after(0,lambda:self._iidb_show_parent(row,landing,assets))
            except Exception as exc:self.after(0,lambda e=str(exc):self.iidb_status_var.set("Couldn't load iiDB game: "+e))
        threading.Thread(target=worker,daemon=True).start()

    @staticmethod
    def _iidb_type_label(asset_type: str) -> str:
        return {"iisu_boxart":"iiSU Box Arts","boxart":"Box Arts","icon":"Icons","logo":"Logos","banner":"Banners","hero":"Heroes","screenshot":"Screenshots","soundbite":"Soundbites"}.get(str(asset_type).lower(),str(asset_type).replace("_"," ").title())

    def _iidb_show_parent(self,row,landing,assets)->None:
        if str(self._iidb_current_parent_id)!=str(row["id"]):return
        self._iidb_assets=assets;counts={}
        for a in assets:
            t=str(a.get("type","")).lower();counts[t]=counts.get(t,0)+1
        total=len(assets)
        for d in self._iidb_find_dicts(landing):
            if isinstance(d.get("asset_count"),(int,float)):total=int(d["asset_count"]);break
        self.iidb_game_detail_var.set(f"iiDB parent ID: {row['id']} • {total} assets")
        for child in self.iidb_category_frame.winfo_children():child.destroy()
        buttons=[("All",len(assets),None)]+[(self._iidb_type_label(t),counts[t],t) for t in ["iisu_boxart","boxart","icon","logo","banner","hero","screenshot","soundbite"] if counts.get(t)]
        for i,(label,count,typ) in enumerate(buttons):
            b=ttk.Button(self.iidb_category_frame,text=f"{label} ({count})",style="Ghost.TButton",command=lambda x=typ:self._iidb_filter_assets(x))
            b.grid(row=i//4,column=i%4,sticky="ew",padx=(0,5),pady=2)
        for c in range(4):self.iidb_category_frame.grid_columnconfigure(c,weight=1)
        self._iidb_filter_assets(None);self.iidb_status_var.set(f"Loaded {len(assets)} asset records for {row['name']}. Select a category or asset to preview.")

    def _iidb_filter_assets(self,asset_type)->None:
        self._iidb_filtered_assets=[a for a in self._iidb_assets if asset_type is None or str(a.get("type","")).lower()==asset_type]
        for item in self.iidb_assets_tree.get_children():self.iidb_assets_tree.delete(item)
        for idx,a in enumerate(self._iidb_filtered_assets):
            resolution=a.get("resolution") or (f"{a.get('width')}×{a.get('height')}" if a.get("width") and a.get("height") else "")
            self.iidb_assets_tree.insert("","end",iid=f"iidb-asset-{idx}",values=(self._iidb_type_label(a.get("type","")),resolution,self._iidb_human_size(a.get("size")),a.get("filename") or a.get("id") or ""))

    def _iidb_cart_key(self,asset):
        return f"{self._iidb_current_parent_id}|{asset.get('id',asset.get('asset_id',asset.get('filename','?')))}"

    def _iidb_asset_selected(self,_event=None)->None:
        selected=self.iidb_assets_tree.selection()
        if not selected:return
        try:idx=int(selected[0].rsplit("-",1)[1]);asset=self._iidb_filtered_assets[idx]
        except Exception:return
        self._iidb_stop_audio();self._iidb_selected_asset=asset
        aid=asset.get("id",asset.get("asset_id","?"));typ=self._iidb_type_label(asset.get("type",""));resolution=asset.get("resolution") or (f"{asset.get('width')}×{asset.get('height')}" if asset.get("width") and asset.get("height") else "");duration=asset.get("duration_ms")
        detail=f"{typ}\nAsset ID: {aid}\n{resolution}\n{self._iidb_human_size(asset.get('size'))}"
        if duration:detail+=f"\nDuration: {float(duration)/1000:.1f}s"
        self.iidb_preview_detail_var.set(detail.strip());self._iidb_refresh_cart_button()
        if str(asset.get("type","")).lower()=="soundbite":
            self.iidb_preview_label.configure(image="",text="Soundbite\n\nUse Play Soundbite below to preview audio.");self._iidb_preview_photo=None
            self.iidb_audio_button.pack(fill="x",pady=(0,5));self.iidb_stop_audio_button.pack(fill="x",pady=(0,5));return
        self.iidb_audio_button.pack_forget();self.iidb_stop_audio_button.pack_forget()
        url=asset.get("preview_url") or asset.get("library_preview_url")
        if not url:self.iidb_preview_label.configure(image="",text="No image preview\navailable");self._iidb_preview_photo=None;return
        self.iidb_preview_label.configure(image="",text="Loading preview…");token=(str(self._iidb_current_parent_id),str(aid),str(url));self._iidb_preview_token=token
        def worker():
            try:
                import urllib.request
                IIDB_THUMB_CACHE_DIR.mkdir(parents=True,exist_ok=True);suffix=Path(str(url).split("?",1)[0]).suffix.lower()
                if suffix not in {".jpg",".jpeg",".png",".webp"}:suffix=".img"
                cache=IIDB_THUMB_CACHE_DIR/(hashlib.sha256(str(url).encode()).hexdigest()[:24]+suffix)
                if not cache.is_file():
                    req=urllib.request.Request(str(url),headers={"User-Agent":"iiSU-PC Manager"})
                    with urllib.request.urlopen(req,timeout=15) as response:cache.write_bytes(response.read())
                from PIL import Image,ImageTk
                image=Image.open(cache).convert("RGB");image.thumbnail((250,360));photo=ImageTk.PhotoImage(image);self.after(0,lambda:self._iidb_set_preview(token,photo))
            except Exception as exc:self.after(0,lambda e=str(exc):self._iidb_preview_failed(token,e))
        threading.Thread(target=worker,daemon=True).start()

    def _iidb_set_preview(self,token,photo)->None:
        if getattr(self,"_iidb_preview_token",None)!=token:return
        self._iidb_preview_photo=photo;self.iidb_preview_label.configure(image=photo,text="")

    def _iidb_preview_failed(self,token,error)->None:
        if getattr(self,"_iidb_preview_token",None)!=token:return
        self._iidb_preview_photo=None;self.iidb_preview_label.configure(image="",text="Preview unavailable");self.iidb_status_var.set("Preview failed: "+error)

    def _iidb_refresh_cart_button(self):
        asset=getattr(self,"_iidb_selected_asset",None)
        if not asset:return
        self.iidb_cart_asset_var.set("✓ In Cart — Remove" if self._iidb_cart_key(asset) in self._iidb_cart else "Add to Cart")

    def _iidb_toggle_selected_cart(self):
        asset=getattr(self,"_iidb_selected_asset",None);game=getattr(self,"_iidb_current_game",None)
        if not asset or not game:return
        key=self._iidb_cart_key(asset)
        if key in self._iidb_cart:self._iidb_cart.pop(key,None)
        else:self._iidb_cart[key]={"parent_id":game["id"],"game_name":game["name"],"game_subtitle":game.get("subtitle","") ,"asset":dict(asset)}
        self.iidb_cart_count_var.set(f"Cart ({len(self._iidb_cart)})");self._iidb_refresh_cart_button()

    @staticmethod
    def _iidb_install_mapping(asset_type: str) -> tuple[str, bool]:
        """Map iiDB categories to the iiSU MediaBridge logical slot.

        bool=True means the category supports numbered slots. Windows box art is
        intentionally mapped to iiSU's icon slot because that is the behavior
        verified against the current iiSU Windows platform implementation.
        """
        mapping = {
            "hero": ("hero", True),
            "screenshot": ("screenshot", True),
            "banner": ("screenshot", True),
            "logo": ("title", False),
            "icon": ("home_icon", False),
            "iisu_boxart": ("icon", False),
            "boxart": ("icon", False),
            "soundbite": ("soundbite", False),
        }
        if asset_type not in mapping:
            raise ValueError(f"Unsupported iiDB asset type: {asset_type}")
        return mapping[asset_type]

    def _iidb_windows_target(self, game_name: str) -> dict:
        """Resolve an iiDB game to an existing Windows .pcgame placeholder."""
        import urllib.parse
        rom_dir = self._windows_rom_dir(show_error=False)
        if rom_dir is None or not rom_dir.is_dir():
            raise RuntimeError("The configured Windows ROM directory is unavailable.")
        wanted = game_name.strip().casefold()
        matches = [p for p in rom_dir.glob("*.pcgame") if p.stem.casefold() == wanted]
        if not matches:
            raise RuntimeError(
                f"No matching Windows game was found for {game_name!r}.\n\n"
                f"Expected an existing placeholder named {game_name}.pcgame in:\n{rom_dir}\n\n"
                "Install All currently requires an exact Windows game-name match so it cannot write media to the wrong iiSU entry."
            )
        if len(matches) > 1:
            raise RuntimeError(f"More than one matching .pcgame exists for {game_name!r}.")
        display_name = matches[0].stem
        document = f"primary:Roms/windows/{matches[0].name}"
        rom_id = (
            "content://com.android.externalstorage.documents/tree/primary%3ARoms/document/"
            + urllib.parse.quote(document, safe="")
        )
        asset_dir = (
            "/storage/emulated/0/Android/media/com.iisulauncher/iiSULauncher/"
            f"assets/media/roms/consoles/windows/{display_name}"
        )
        return {"tab_id":"windows", "rom_id":rom_id, "display_name":display_name, "asset_dir":asset_dir}

    @staticmethod
    def _iidb_asset_extension(asset: dict, url: str) -> str:
        import urllib.parse
        filename = str(asset.get("filename") or "")
        suffix = Path(filename).suffix.lower().lstrip(".")
        if not suffix:
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower().lstrip(".")
        if not suffix:
            mime = str(asset.get("mime_type") or "").lower()
            suffix = {"image/png":"png", "image/jpeg":"jpg", "image/webp":"webp",
                      "audio/mpeg":"mp3", "audio/wav":"wav", "audio/x-wav":"wav",
                      "audio/ogg":"ogg"}.get(mime, "bin")
        return re.sub(r"[^a-z0-9]+", "", suffix) or "bin"

    def _iidb_download_original(self, item: dict, target: dict, logical_type: str) -> Path:
        import urllib.request
        asset = item["asset"]
        url = asset.get("raw_url")
        if not url:
            raise RuntimeError("iiDB did not provide a raw/original URL for this asset.")
        extension = self._iidb_asset_extension(asset, str(url))
        aid = str(asset.get("id", asset.get("asset_id", "unknown")))
        safe_game = re.sub(r'[<>:"/\\|?*]+', '_', target["display_name"]).strip(" .") or "game"
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", aid) or "asset"
        durable = IIDB_LIBRARY_DIR / "windows" / safe_game / logical_type / f"{safe_id}.{extension}"
        durable.parent.mkdir(parents=True, exist_ok=True)
        temp = durable.with_suffix(durable.suffix + ".download")
        req = urllib.request.Request(str(url), headers={"User-Agent":"iiSU-PC Manager", "Accept":"*/*"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response, temp.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024*1024)
            if not temp.is_file() or temp.stat().st_size <= 0:
                raise RuntimeError("iiDB returned an empty original file.")
            os.replace(temp, durable)
        finally:
            try:
                if temp.exists(): temp.unlink()
            except OSError:
                pass
        return durable

    def _iidb_build_install_plan(self) -> list[dict]:
        """Validate cart targets and assign deterministic iiSU slots."""
        if not self._iidb_cart:
            raise RuntimeError("The iiDB cart is empty.")
        plan=[]; numbered={}; singles=set()
        target_cache={}
        for key, item in self._iidb_cart.items():
            game_name=item["game_name"]
            target=target_cache.get(game_name)
            if target is None:
                target=self._iidb_windows_target(game_name); target_cache[game_name]=target
            raw_type=str(item["asset"].get("type","")).lower()
            logical, is_numbered=self._iidb_install_mapping(raw_type)
            group=(target["rom_id"],logical)
            if is_numbered:
                slot=numbered.get(group,0)+1; numbered[group]=slot
            else:
                if group in singles:
                    raise RuntimeError(
                        f"The cart contains more than one asset for the single iiSU slot {logical!r} "
                        f"on {target['display_name']}. Remove one before installing."
                    )
                singles.add(group); slot=1
            plan.append({"key":key,"item":item,"target":target,"logical_type":logical,"slot":slot})
        return plan

    def _iidb_install_cart(self, cart_window=None) -> None:
        try:
            plan=self._iidb_build_install_plan()
        except Exception as exc:
            messagebox.showerror("Install iiDB Media", str(exc), parent=cart_window or self._iidb_browser_window); return
        ready, detail=self._adb_device_ready()
        if not ready:
            messagebox.showwarning("Install iiDB Media", "Start the Android VM before installing iiDB media.\n\n"+detail, parent=cart_window or self._iidb_browser_window); return
        ping_ok, ping_detail=self._mediabridge_ping()
        if not ping_ok:
            messagebox.showerror("Install iiDB Media", "MediaBridge V1 is not ready. Repatch iiSU first.\n\n"+ping_detail, parent=cart_window or self._iidb_browser_window); return
        summary=[]
        for p in plan:
            a=p["item"]["asset"]
            summary.append(f"• {p['target']['display_name']} — {self._iidb_type_label(a.get('type',''))} → {p['logical_type']} slot {p['slot']}")
        if not messagebox.askyesno("Install iiDB Media", "Install these iiDB originals into iiSU?\n\n"+"\n".join(summary)+"\n\nThe originals will also be saved permanently in the iiDB Media Library for recovery.", parent=cart_window or self._iidb_browser_window):
            return
        self.iidb_status_var.set(f"Installing {len(plan)} iiDB asset{'s' if len(plan)!=1 else ''}…")
        def worker():
            installed=[]; failures=[]
            for index,p in enumerate(plan,1):
                item=p["item"]; asset=item["asset"]; target=p["target"]
                aid=asset.get("id",asset.get("asset_id"))
                try:
                    self.after(0, lambda i=index,n=len(plan),g=target['display_name']: self.iidb_status_var.set(f"Install All • {i}/{n} • {g}"))
                    durable=self._iidb_download_original(item,target,p["logical_type"])
                    ext=durable.suffix.lower().lstrip(".")
                    install_asset={"asset_type":p["logical_type"],"slot":p["slot"],"extension":ext}
                    ok,output=self._mediabridge_install_file(target,install_asset,durable)
                    if not ok:
                        match=re.search(r'IISUPC_MEDIABRIDGE_ERROR_V1:([A-Z0-9_]+)',output or "")
                        raise RuntimeError("MediaBridge: "+match.group(1) if match else (output or "MediaBridge install failed"))
                    record=self._register_installed_media(
                        tab_id=target["tab_id"],rom_id=target["rom_id"],display_name=target["display_name"],
                        asset_dir=target["asset_dir"],asset_type=p["logical_type"],slot=p["slot"],
                        source_file=durable,iidb_asset_id=aid,iidb_parent_id=item.get("parent_id"),
                        remote_filename=self._media_remote_filename(p["logical_type"],p["slot"],ext))
                    installed.append((p,record))
                except Exception as exc:
                    failures.append((p,str(exc)))
            rescan_ok = None
            rescan_detail = ""
            if installed:
                try:
                    self.after(0, lambda: self.iidb_status_var.set("Install All • Refreshing iiSU library…"))
                    rescan_ok, rescan_detail = self._mediabridge_rescan_library()
                except Exception as exc:
                    rescan_ok = False
                    rescan_detail = str(exc)
            def done():
                for p,_record in installed:self._iidb_cart.pop(p["key"],None)
                self.iidb_cart_count_var.set(f"Cart ({len(self._iidb_cart)})");self._iidb_refresh_cart_button();self._media_library_refresh()
                refresh_text = " • iiSU refreshed" if rescan_ok is True else (" • iiSU refresh failed" if rescan_ok is False else "")
                self.iidb_status_var.set(f"Install All complete • {len(installed)} installed • {len(failures)} failed{refresh_text}")
                warnings=[]
                if failures:
                    details="\n".join(f"• {p['target']['display_name']} / {self._iidb_type_label(p['item']['asset'].get('type',''))}: {err}" for p,err in failures[:10])
                    warnings.append(f"Asset install failures:\n{details}")
                if rescan_ok is False:
                    warnings.append(
                        "The media files were installed and registered, but iiSU's automatic Full Library Rescan did not start. "
                        "The successful installs were kept.\n\n" + (rescan_detail or "No MediaBridge rescan detail was returned.")
                    )
                if warnings:
                    messagebox.showwarning(
                        "Install iiDB Media",
                        f"Installed: {len(installed)}\nFailed: {len(failures)}\n\n" + "\n\n".join(warnings),
                        parent=cart_window or self._iidb_browser_window
                    )
                else:
                    messagebox.showinfo(
                        "Install iiDB Media",
                        f"Install complete.\n\nInstalled: {len(installed)}\nFailed: 0\n\niiSU's Full Library Rescan was started automatically.\n\n"
                        "The installed originals are registered for Check / Restore Missing / Restore All.",
                        parent=cart_window or self._iidb_browser_window
                    )
                if cart_window is not None:
                    try: cart_window.destroy()
                    except Exception: pass
            self.after(0,done)
        threading.Thread(target=worker,daemon=True).start()

    def _iidb_open_cart(self):
        win=tk.Toplevel(self._iidb_browser_window);win.title(f"iiDB Cart ({len(self._iidb_cart)})");win.geometry("880x500");win.configure(bg=BG)
        tk.Label(win,text="iiDB Cart",bg=BG,fg=TEXT,font=FONT_TITLE).pack(anchor="w",padx=16,pady=(16,4))
        tk.Label(win,text="Install All downloads originals to the durable Media Library, then installs them through MediaBridge.",bg=BG,fg=TEXT_DIM,font=FONT_BODY).pack(anchor="w",padx=16,pady=(0,10))
        tree=ttk.Treeview(win,columns=("game","type","asset","details"),show="headings")
        for c,t,w in (("game","Game",190),("type","Category",130),("asset","Asset ID",100),("details","Resolution / Duration",240)):
            tree.heading(c,text=t);tree.column(c,width=w,stretch=(c in {"game","details"}))
        tree.pack(fill="both",expand=True,padx=16,pady=(0,10))
        keys=list(self._iidb_cart.keys())
        for i,k in enumerate(keys):
            item=self._iidb_cart[k];a=item["asset"];detail=a.get("resolution") or (f"{a.get('width')}×{a.get('height')}" if a.get("width") and a.get("height") else "")
            if a.get("duration_ms"):detail=(detail+" • " if detail else "")+f"{float(a['duration_ms'])/1000:.1f}s"
            tree.insert("","end",iid=f"cart-{i}",values=(item["game_name"],self._iidb_type_label(a.get("type","")),a.get("id",a.get("asset_id","")),detail))
        row=tk.Frame(win,bg=BG);row.pack(fill="x",padx=16,pady=(0,16))
        def remove_selected():
            selected=tree.selection()
            for iid in selected:
                try:k=keys[int(iid.rsplit("-",1)[1])]
                except Exception:continue
                self._iidb_cart.pop(k,None);tree.delete(iid)
            self.iidb_cart_count_var.set(f"Cart ({len(self._iidb_cart)})");self._iidb_refresh_cart_button()
        def clear_all():
            self._iidb_cart.clear()
            for iid in tree.get_children():tree.delete(iid)
            self.iidb_cart_count_var.set("Cart (0)");self._iidb_refresh_cart_button()
        ttk.Button(row,text="Remove Selected",style="Ghost.TButton",command=remove_selected).pack(side="left")
        ttk.Button(row,text="Clear Cart",style="Ghost.TButton",command=clear_all).pack(side="left",padx=(8,0))
        ttk.Button(row,text="Install All",style="Accent.TButton",command=lambda:self._iidb_install_cart(win)).pack(side="right")
        ttk.Button(row,text="Close",style="Ghost.TButton",command=win.destroy).pack(side="right",padx=(0,8))

    def _iidb_play_selected_soundbite(self):
        asset=getattr(self,"_iidb_selected_asset",None)
        if not asset or str(asset.get("type","")).lower()!="soundbite":return
        url=asset.get("preview_url") or asset.get("raw_url") or asset.get("library_preview_url")
        if not url:self.iidb_status_var.set("This soundbite has no playable URL.");return
        self._iidb_stop_audio();self.iidb_status_var.set("Loading soundbite preview…")
        token=(str(self._iidb_current_parent_id),str(asset.get("id",asset.get("asset_id","?"))),str(url));self._iidb_audio_token=token
        def worker():
            try:
                import urllib.request
                IIDB_AUDIO_CACHE_DIR.mkdir(parents=True,exist_ok=True);suffix=Path(str(url).split("?",1)[0]).suffix.lower()
                if suffix not in {".mp3",".wav",".wma",".m4a",".aac",".ogg"}:suffix=".mp3"
                cache=IIDB_AUDIO_CACHE_DIR/(hashlib.sha256(str(url).encode()).hexdigest()[:24]+suffix)
                if not cache.is_file():
                    req=urllib.request.Request(str(url),headers={"User-Agent":"iiSU-PC Manager"})
                    with urllib.request.urlopen(req,timeout=20) as response:cache.write_bytes(response.read())
                self.after(0,lambda:self._iidb_start_mci_audio(token,cache))
            except Exception as exc:self.after(0,lambda e=str(exc):self.iidb_status_var.set("Soundbite preview failed: "+e))
        threading.Thread(target=worker,daemon=True).start()

    def _iidb_start_mci_audio(self,token,path):
        if getattr(self,"_iidb_audio_token",None)!=token:return
        try:
            import ctypes
            alias="iisupc_iidb_preview";winmm=ctypes.windll.winmm
            winmm.mciSendStringW(f'close {alias}',None,0,None)
            err=winmm.mciSendStringW(f'open "{str(path)}" alias {alias}',None,0,None)
            if err:raise RuntimeError(f"Windows audio open failed (MCI {err})")
            err=winmm.mciSendStringW(f'play {alias}',None,0,None)
            if err:raise RuntimeError(f"Windows audio playback failed (MCI {err})")
            self._iidb_audio_alias=alias;self.iidb_status_var.set("Playing soundbite preview. Use Stop to end playback.")
        except Exception as exc:self.iidb_status_var.set("Soundbite preview failed: "+str(exc))

    def _iidb_stop_audio(self):
        alias=getattr(self,"_iidb_audio_alias",None)
        if alias:
            try:
                import ctypes
                ctypes.windll.winmm.mciSendStringW(f"stop {alias}",None,0,None);ctypes.windll.winmm.mciSendStringW(f"close {alias}",None,0,None)
            except Exception:pass
        self._iidb_audio_alias=None

    # -- Native Windows applications -------------------------------------------------

    def _load_windows_apps(self) -> dict:
        if not WINDOWS_APPS_PATH.is_file():
            return {}
        try:
            data = json.loads(WINDOWS_APPS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Windows Apps", f"Couldn't read {WINDOWS_APPS_PATH.name}:\n\n{e}")
            return {}
        return data if isinstance(data, dict) else {}

    def _save_windows_apps(self, apps: dict) -> bool:
        try:
            WINDOWS_APPS_PATH.write_text(json.dumps(apps, indent=2) + "\n", encoding="utf-8")
            return True
        except OSError as e:
            messagebox.showerror("Windows Apps", f"Couldn't save {WINDOWS_APPS_PATH.name}:\n\n{e}")
            return False

    def _windows_rom_dir(self, show_error: bool = True) -> Path | None:
        raw = self.config_data.get("roms_dir", "")
        if not raw:
            if show_error:
                messagebox.showerror("Windows Apps", "Set your ROM directory first.")
            return None
        return Path(raw) / "windows"

    @staticmethod
    def _safe_pcgame_name(name: str) -> str | None:
        name = name.strip()
        if not name or name in {".", ".."}:
            return None
        if any(ch in name for ch in '<>:"/\\|?*'):
            return None
        return name

    @staticmethod
    def _safe_steam_pcgame_name(name: str) -> str:
        """Make a Steam title safe as a Windows/.pcgame filename."""
        cleaned = re.sub(r'[<>:"/\\|?*]+', ' - ', name)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
        return cleaned or "Steam Game"

    @staticmethod
    def _unique_windows_app_name(base: str, apps: dict) -> str:
        """Return a collision-free app/placeholder name."""
        if base not in apps:
            return base
        number = 2
        while f"{base} ({number})" in apps:
            number += 1
        return f"{base} ({number})"

    @staticmethod
    def _valid_uri(uri: str) -> bool:
        return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$", uri.strip()))

    @staticmethod
    def _steam_app_id(value: str) -> str | None:
        """Accept an App ID, Steam protocol URI, or Steam store URL."""
        value = value.strip()
        if value.isdigit():
            return value

        patterns = (
            r"^steam://(?:run|rungameid)/(\d+)(?:[/?#].*)?$",
            r"^https?://(?:store\.)?steampowered\.com/app/(\d+)(?:[/?#].*)?$",
            r"^https?://steamcommunity\.com/app/(\d+)(?:[/?#].*)?$",
        )
        for pattern in patterns:
            match = re.match(pattern, value, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _is_steam_uri(uri: str) -> bool:
        return bool(re.match(r"^steam://(?:run|rungameid)/\d+(?:[/?#].*)?$", uri.strip(), re.IGNORECASE))

    @staticmethod
    def _parse_steam_vdf_strings(text: str) -> dict[str, str]:
        """Small VDF reader for the flat key/value data we need from Steam files."""
        return {m.group(1): m.group(2).replace(r"\\", "\\") for m in re.finditer(r'"([^"]+)"\s*"([^"]*)"', text)}

    def _steam_library_paths(self) -> list[Path]:
        """Find Steam plus every configured library folder without requiring Steam APIs."""
        candidates: list[Path] = []
        env_candidates = [
            os.environ.get("PROGRAMFILES(X86)", ""),
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for base in env_candidates:
            if base:
                candidates.extend([Path(base) / "Steam", Path(base) / "steam"])
        # Common registry locations are useful when Steam lives somewhere non-default.
        try:
            import winreg
            for root, key_name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(root, key_name) as key:
                        for value_name in ("SteamPath", "InstallPath"):
                            try:
                                value, _ = winreg.QueryValueEx(key, value_name)
                                if value:
                                    candidates.append(Path(str(value)))
                            except OSError:
                                pass
                except OSError:
                    pass
        except Exception:
            pass

        steam_root = next((p for p in candidates if (p / "steamapps").is_dir()), None)
        if steam_root is None:
            return []

        libraries = [steam_root]
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                raw = vdf.read_text(encoding="utf-8", errors="ignore")
                for match in re.finditer(r'"path"\s*"([^"]+)"', raw):
                    path = Path(match.group(1).replace(r"\\", "\\"))
                    if (path / "steamapps").is_dir():
                        libraries.append(path)
            except OSError:
                pass

        unique: list[Path] = []
        seen: set[str] = set()
        for path in libraries:
            key = str(path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _installed_steam_games(self) -> list[dict]:
        games: list[dict] = []
        seen: set[str] = set()
        for library in self._steam_library_paths():
            steamapps = library / "steamapps"
            for manifest in steamapps.glob("appmanifest_*.acf"):
                try:
                    fields = self._parse_steam_vdf_strings(
                        manifest.read_text(encoding="utf-8", errors="ignore")
                    )
                except OSError:
                    continue
                appid = fields.get("appid") or manifest.stem.removeprefix("appmanifest_")
                name = fields.get("name")
                installdir = fields.get("installdir", "")
                if not appid.isdigit() or not name or appid in seen:
                    continue
                seen.add(appid)
                games.append({
                    "appid": appid,
                    "name": name,
                    "library": str(library),
                    "install_dir": str(steamapps / "common" / installdir) if installdir else "",
                })
        return sorted(games, key=lambda g: g["name"].casefold())

    def _installed_steam_ids(self) -> set[str]:
        """Return App IDs currently represented by local Steam manifests."""
        return {game["appid"] for game in self._installed_steam_games()}

    def _steam_ids_already_added(self) -> set[str]:
        ids: set[str] = set()
        for entry in self._load_windows_apps().values():
            if isinstance(entry, dict) and str(entry.get("type", "executable")).lower() == "uri":
                appid = self._steam_app_id(str(entry.get("uri", "")))
                if appid:
                    ids.add(appid)
        return ids

    def _steam_artwork_cache_file(self, appid: str) -> Path:
        return BRIDGE_DIR / "cache" / "steam_artwork" / f"{appid}.jpg"

    def _ensure_added_at(self, entry: dict) -> dict:
        entry = dict(entry)
        entry.setdefault("added_at", __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"))
        return entry

    def _windows_app_sort_key(self, name: str, entry: dict):
        column = getattr(self, "_windows_apps_sort_column", "name")
        if column == "type":
            return self._windows_app_display_type(entry).casefold()
        if column == "status":
            return self._windows_app_status(name, entry).casefold()
        if column == "added":
            return str(entry.get("added_at", "")) if isinstance(entry, dict) else ""
        return name.casefold()

    def _sort_windows_apps(self, column: str) -> None:
        if getattr(self, "_windows_apps_sort_column", "name") == column:
            self._windows_apps_sort_reverse = not getattr(self, "_windows_apps_sort_reverse", False)
        else:
            self._windows_apps_sort_column = column
            self._windows_apps_sort_reverse = False
        self._refresh_windows_apps_tree()

    def _load_cached_windows_artwork(self, item_id: str, appid: str) -> None:
        cache_file = self._steam_artwork_cache_file(appid)
        if not cache_file.is_file():
            return
        try:
            from PIL import Image, ImageTk
            image = Image.open(cache_file).convert("RGB")
            image.thumbnail((72, 27))
            photo = ImageTk.PhotoImage(image)
        except Exception:
            return
        if not hasattr(self, "_windows_apps_images"):
            self._windows_apps_images = {}
        self._windows_apps_images[item_id] = photo
        if self.windows_apps_tree.exists(item_id):
            self.windows_apps_tree.item(item_id, image=photo)

    def _show_windows_apps_context_menu(self, event) -> None:
        row = self.windows_apps_tree.identify_row(event.y)
        if row and row not in self.windows_apps_tree.selection():
            self.windows_apps_tree.selection_set(row)
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Launch / Test", command=self._test_windows_app)
        menu.add_command(label="Edit...", command=self._edit_windows_app)
        menu.add_command(label="Duplicate...", command=self._duplicate_windows_app)
        menu.add_separator()
        menu.add_command(label="Open Location / Copy URI", command=self._windows_app_open_or_copy)
        menu.add_command(label="Repair Placeholders", command=self._repair_selected_windows_apps)
        menu.add_separator()
        menu.add_command(label="Remove Selected", command=self._remove_windows_app)
        menu.tk_popup(event.x_root, event.y_root)

    def _repair_selected_windows_apps(self) -> None:
        selected = list(self.windows_apps_tree.selection())
        if not selected:
            messagebox.showinfo("Nothing selected", "Select one or more applications first.")
            return
        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return
        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
            repaired = 0
            for name in selected:
                placeholder = windows_dir / f"{name}.pcgame"
                if not placeholder.exists():
                    placeholder.touch()
                    repaired += 1
        except OSError as e:
            messagebox.showerror("Repair failed", str(e))
            return
        self._refresh_windows_apps_tree()
        messagebox.showinfo("Repair complete", f"Recreated {repaired} missing placeholder(s).")

    def _export_windows_apps(self) -> None:
        apps = self._load_windows_apps()
        if not apps:
            messagebox.showinfo("Export", "There are no Windows Apps to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Windows Apps", defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="windows_apps_export.json",
        )
        if not path:
            return
        payload = {
            "format": "iisu-pc-windows-apps",
            "version": 1,
            "apps": apps,
        }
        try:
            Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Export failed", str(e))
            return
        messagebox.showinfo(
            "Export complete",
            "Windows Apps exported.\n\nSteam and URI entries are portable. Executable entries may need their paths updated on another PC.",
        )

    def _import_windows_apps_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Windows Apps", filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Import failed", str(e))
            return
        incoming = payload.get("apps", payload) if isinstance(payload, dict) else {}
        if not isinstance(incoming, dict):
            messagebox.showerror("Import failed", "That file doesn't contain a Windows Apps mapping.")
            return

        apps = self._load_windows_apps()
        existing_steam_ids = self._steam_ids_already_added()
        added = skipped = 0
        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return
        windows_dir.mkdir(parents=True, exist_ok=True)
        for raw_name, raw_entry in incoming.items():
            name = self._safe_pcgame_name(str(raw_name))
            if not name or not isinstance(raw_entry, dict) or name in apps:
                skipped += 1
                continue
            incoming_steam_id = self._steam_app_id(str(raw_entry.get("uri", "")))
            if incoming_steam_id and incoming_steam_id in existing_steam_ids:
                skipped += 1
                continue
            entry = self._ensure_added_at(raw_entry)
            apps[name] = entry
            try:
                (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
            except OSError:
                apps.pop(name, None)
                skipped += 1
                continue
            added += 1
            if incoming_steam_id:
                existing_steam_ids.add(incoming_steam_id)
        if self._save_windows_apps(apps):
            self._refresh_windows_apps_tree()
            messagebox.showinfo("Import complete", f"Imported {added} application(s).\nSkipped {skipped} duplicate/invalid item(s).")

    def _refresh_steam_library_summary(self) -> None:
        """Refresh the compact Steam status shown on the Windows Apps page."""
        if not hasattr(self, "steam_library_summary_label"):
            return

        self.steam_library_summary_label.config(text="Steam: scanning libraries...")

        def worker():
            games = self._installed_steam_games()
            installed_ids = {g["appid"] for g in games}
            added_ids = self._steam_ids_already_added()
            in_iisu = len(installed_ids & added_ids)
            available = len(installed_ids - added_ids)
            libraries = len(self._steam_library_paths())

            def apply():
                if not hasattr(self, "steam_library_summary_label"):
                    return
                self.steam_library_summary_label.config(
                    text=(
                        f"Steam: {len(games)} installed  •  {in_iisu} in iiSU  •  "
                        f"{available} available to import  •  {libraries} librar"
                        f"{'y' if libraries == 1 else 'ies'}"
                    )
                )

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_import_new_steam_games(self) -> None:
        """Add every installed Steam game that does not already have an iiSU mapping."""
        games = self._installed_steam_games()
        if not games:
            messagebox.showinfo(
                "Auto-import Steam Games",
                "No installed Steam games were found.",
            )
            return

        apps = self._load_windows_apps()
        already = self._steam_ids_already_added()
        missing = [game for game in games if game["appid"] not in already]

        if not missing:
            messagebox.showinfo(
                "Auto-import Steam Games",
                "Every installed Steam game is already in iiSU.",
            )
            self._refresh_steam_library_summary()
            return

        preview = "\n".join(f"• {game['name']}" for game in missing[:12])
        if len(missing) > 12:
            preview += f"\n• …and {len(missing) - 12} more"

        if not messagebox.askyesno(
            "Auto-import Steam Games",
            f"Add {len(missing)} installed Steam game(s) that are not currently in iiSU?\n\n"
            f"{preview}\n\n"
            "This creates the Windows Apps mappings and .pcgame placeholders. "
            "It does not change iiSU artwork.",
        ):
            return

        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return

        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Auto-import failed", str(e))
            return

        imported = skipped = 0
        for game in missing:
            appid = game["appid"]
            if appid in already:
                skipped += 1
                continue

            name = self._unique_windows_app_name(
                self._safe_steam_pcgame_name(game["name"]), apps
            )
            entry = self._ensure_added_at({
                "type": "uri",
                "uri": f"steam://rungameid/{appid}",
                "steam_name": game["name"],
            })
            apps[name] = entry

            try:
                (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
            except OSError:
                apps.pop(name, None)
                skipped += 1
                continue

            already.add(appid)
            imported += 1

        if self._save_windows_apps(apps):
            self._refresh_windows_apps_tree()
            self._refresh_steam_library_summary()
            messagebox.showinfo(
                "Steam auto-import complete",
                f"Imported {imported} new Steam game(s)."
                + (f"\nSkipped {skipped} item(s)." if skipped else ""),
            )

    def _import_steam_library(self) -> None:
        games = self._installed_steam_games()
        if not games:
            messagebox.showinfo(
                "Steam Library",
                "No installed Steam games were found. Steam may not be installed, or no app manifests are available.",
            )
            return

        already = self._steam_ids_already_added()
        dialog = tk.Toplevel(self)
        dialog.title("Import Steam Library")
        dialog.geometry("760x560")
        dialog.minsize(650, 440)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()

        header = tk.Frame(dialog, bg=BG)
        header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="Import Steam Library", bg=BG, fg=TEXT, font=FONT_HEADING).pack(anchor="w")
        libs = self._steam_library_paths()
        tk.Label(
            header, text=f"Found {len(games)} installed game(s) across {len(libs)} Steam library folder(s).",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY,
        ).pack(anchor="w")

        filter_var = tk.StringVar()
        filter_row = tk.Frame(dialog, bg=BG)
        filter_row.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(filter_row, text="Search:", bg=BG, fg=TEXT, font=FONT_BODY).pack(side="left")
        tk.Entry(filter_row, textvariable=filter_var, **ENTRY_KWARGS).pack(side="left", fill="x", expand=True, padx=(8, 0))

        columns = ("name", "appid", "state")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", selectmode="extended")
        tree.heading("name", text="Game")
        tree.heading("appid", text="App ID")
        tree.heading("state", text="Status")
        tree.column("name", width=420)
        tree.column("appid", width=90, anchor="center")
        tree.column("state", width=130)
        tree.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        def refill(*_):
            selected_ids = {tree.item(i, "values")[1] for i in tree.selection()}
            for i in tree.get_children():
                tree.delete(i)
            q = filter_var.get().strip().casefold()
            for game in games:
                if q and q not in game["name"].casefold() and q not in game["appid"]:
                    continue
                state = "Already in iiSU" if game["appid"] in already else "Installed"
                iid = f"app_{game['appid']}"
                tree.insert("", "end", iid=iid, values=(game["name"], game["appid"], state))
                if game["appid"] in selected_ids and game["appid"] not in already:
                    tree.selection_add(iid)
        filter_var.trace_add("write", refill)
        refill()

        controls = tk.Frame(dialog, bg=BG)
        controls.pack(fill="x", padx=18, pady=(0, 16))

        def select_all():
            for iid in tree.get_children():
                values = tree.item(iid, "values")
                if len(values) >= 3 and values[2] != "Already in iiSU":
                    tree.selection_add(iid)

        def do_import():
            chosen = []
            for iid in tree.selection():
                values = tree.item(iid, "values")
                if len(values) >= 3 and values[2] != "Already in iiSU":
                    chosen.append((str(values[0]), str(values[1])))
            if not chosen:
                messagebox.showinfo("Steam Library", "Select at least one game to import.", parent=dialog)
                return

            apps = self._load_windows_apps()
            windows_dir = self._windows_rom_dir()
            if windows_dir is None:
                return
            windows_dir.mkdir(parents=True, exist_ok=True)
            imported = skipped = 0
            existing_steam_ids = self._steam_ids_already_added()
            for game_name, appid in chosen:
                if appid in existing_steam_ids:
                    skipped += 1
                    continue
                name = self._unique_windows_app_name(self._safe_steam_pcgame_name(game_name), apps)
                entry = self._ensure_added_at({
                    "type": "uri",
                    "uri": f"steam://rungameid/{appid}",
                    "steam_name": game_name,
                })
                apps[name] = entry
                try:
                    (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
                except OSError:
                    apps.pop(name, None)
                    skipped += 1
                    continue
                imported += 1
                existing_steam_ids.add(appid)

            if self._save_windows_apps(apps):
                dialog.destroy()
                self._refresh_windows_apps_tree()
                self._refresh_steam_library_summary()
                messagebox.showinfo("Steam import complete", f"Imported {imported} game(s).\nSkipped {skipped} item(s).")

        ttk.Button(controls, text="Select All Installed", style="Ghost.TButton", command=select_all).pack(side="left")
        ttk.Button(controls, text="Cancel", style="Ghost.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(controls, text="Import Selected", style="Accent.TButton", command=do_import).pack(side="right", padx=(0, 8))

    def _windows_app_status(self, name: str, entry: dict) -> str:
        if not isinstance(entry, dict):
            return "✗ Invalid entry"

        launch_type = str(entry.get("type", "executable")).lower()
        if launch_type == "uri":
            uri = entry.get("uri", "")
            if not isinstance(uri, str) or not self._valid_uri(uri):
                return "✗ Invalid URI"
            if self._is_steam_uri(uri):
                appid = self._steam_app_id(uri)
                installed_ids = getattr(self, "_windows_apps_installed_steam_ids", None)
                if installed_ids is not None and appid and appid not in installed_ids:
                    return "○ Steam game not installed"
        elif launch_type == "executable":
            exe = entry.get("exe", "")
            if not isinstance(exe, str) or not exe.strip():
                return "✗ No EXE"
            if not Path(os.path.expandvars(os.path.expanduser(exe))).is_file():
                return "✗ Missing EXE"
            args = entry.get("args", [])
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                return "✗ Invalid args"
            working = entry.get("working_dir")
            if working and not Path(os.path.expandvars(os.path.expanduser(str(working)))).is_dir():
                return "✗ Missing work dir"
        else:
            return "✗ Unknown type"

        windows_dir = self._windows_rom_dir(show_error=False)
        if windows_dir is None:
            return "? ROM dir unset"
        if not (windows_dir / f"{name}.pcgame").is_file():
            return "✗ Missing placeholder"
        return "✓ Ready"

    def _windows_app_display_type(self, entry: dict) -> str:
        launch_type = str(entry.get("type", "executable")).lower()
        if launch_type == "uri":
            return "Steam Game" if self._is_steam_uri(str(entry.get("uri", ""))) else "Custom URI"
        return "Executable"

    def _refresh_windows_apps_tree(self, *_args) -> None:
        if not hasattr(self, "windows_apps_tree"):
            return
        selected = set(self.windows_apps_tree.selection())
        for item in self.windows_apps_tree.get_children():
            self.windows_apps_tree.delete(item)
        self._windows_apps_images = {}

        query = self.windows_apps_search_var.get().strip().casefold() if hasattr(self, "windows_apps_search_var") else ""
        apps = self._load_windows_apps()
        # One manifest scan per refresh lets status distinguish an installed
        # Steam game from a valid mapping for a temporarily uninstalled game.
        self._windows_apps_installed_steam_ids = self._installed_steam_ids()
        rows = list(apps.items())
        rows.sort(
            key=lambda item: self._windows_app_sort_key(item[0], item[1] if isinstance(item[1], dict) else {}),
            reverse=getattr(self, "_windows_apps_sort_reverse", False),
        )

        for name, entry in rows:
            if not isinstance(entry, dict):
                target = ""
                args_display = ""
                display_type = "Invalid"
                added = ""
            else:
                launch_type = str(entry.get("type", "executable")).lower()
                target = entry.get("uri", "") if launch_type == "uri" else entry.get("exe", "")
                args = entry.get("args", [])
                args_display = " ".join(str(arg) for arg in args) if isinstance(args, list) and launch_type == "executable" else ""
                display_type = self._windows_app_display_type(entry)
                added = str(entry.get("added_at", ""))[:10]

            haystack = f"{name} {display_type} {target} {args_display} {added}".casefold()
            if query and query not in haystack:
                continue

            self.windows_apps_tree.insert(
                "", "end", iid=name, text="",
                values=(name, display_type, target, args_display, self._windows_app_status(name, entry), added),
            )
            if name in selected:
                self.windows_apps_tree.selection_add(name)

            if isinstance(entry, dict) and display_type == "Steam Game":
                appid = self._steam_app_id(str(entry.get("uri", "")))
                if appid:
                    self.after(0, self._load_cached_windows_artwork, name, appid)

        count = len(self.windows_apps_tree.get_children())
        total = len(apps)
        if hasattr(self, "windows_apps_count_label"):
            self.windows_apps_count_label.config(text=f"{count} shown / {total} total" if query else f"{total} application(s)")

    def _refresh_steam_artwork_cache(self) -> None:
        """Fetch/refresh Manager-only Steam artwork for mapped Steam games."""
        apps = self._load_windows_apps()
        steam_games = []
        for name, entry in apps.items():
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri", "")
            if str(entry.get("type", "executable")).lower() != "uri" or not isinstance(uri, str):
                continue
            appid = self._steam_app_id(uri)
            if appid:
                steam_games.append((name, appid))

        if not steam_games:
            messagebox.showinfo("Refresh Steam Artwork", "No mapped Steam games were found.")
            return

        if not messagebox.askyesno(
            "Refresh Steam Artwork",
            f"Refresh Manager artwork for {len(steam_games)} mapped Steam game(s)?\n\n"
            "This only updates the Manager artwork cache. iiSU/SteamGridDB artwork is untouched.",
        ):
            return

        progress = tk.Toplevel(self)
        progress.title("Refreshing Steam Artwork")
        progress.geometry("520x150")
        progress.resizable(False, False)
        progress.configure(bg=BG)
        progress.transient(self)
        progress.grab_set()

        status_var = tk.StringVar(value=f"Preparing to refresh {len(steam_games)} game(s)...")
        tk.Label(progress, text="Steam Artwork", bg=BG, fg=TEXT, font=FONT_HEADING).pack(
            anchor="w", padx=18, pady=(16, 4)
        )
        tk.Label(progress, textvariable=status_var, bg=BG, fg=TEXT_DIM, font=FONT_BODY).pack(
            anchor="w", padx=18, pady=(0, 8)
        )
        bar = ttk.Progressbar(progress, maximum=len(steam_games), value=0)
        bar.pack(fill="x", padx=18, pady=(0, 14))

        def worker():
            import io
            import urllib.request

            refreshed = failed = 0
            cache_dir = BRIDGE_DIR / "cache" / "steam_artwork"
            cache_dir.mkdir(parents=True, exist_ok=True)

            try:
                from PIL import Image
            except Exception:
                Image = None

            for index, (name, appid) in enumerate(steam_games, start=1):
                self.after(
                    0, lambda i=index, n=name: (
                        status_var.set(f"{i}/{len(steam_games)}  {n}"),
                        bar.configure(value=i - 1),
                    )
                )
                try:
                    # appdetails gives us the canonical Steam header image directly
                    # from the App ID, so auto-imported games do not need a prior search.
                    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english&cc=US"
                    req = urllib.request.Request(url, headers={"User-Agent": "iiSU-PC Manager"})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    record = payload.get(str(appid), {})
                    data = record.get("data", {}) if record.get("success") else {}
                    image_url = str(data.get("header_image") or "")
                    if not image_url:
                        raise ValueError("Steam returned no header image")

                    req = urllib.request.Request(image_url, headers={"User-Agent": "iiSU-PC Manager"})
                    with urllib.request.urlopen(req, timeout=12) as response:
                        raw = response.read()

                    # Validate/normalize the image when Pillow is available.
                    cache_file = self._steam_artwork_cache_file(appid)
                    if Image is not None:
                        image = Image.open(io.BytesIO(raw)).convert("RGB")
                        image.save(cache_file, format="JPEG", quality=92)
                    else:
                        cache_file.write_bytes(raw)
                    refreshed += 1
                except Exception:
                    failed += 1

                self.after(0, lambda i=index: bar.configure(value=i))

            def finish():
                try:
                    progress.destroy()
                except tk.TclError:
                    pass
                self._windows_apps_images = {}
                self._refresh_windows_apps_tree()
                messagebox.showinfo(
                    "Steam artwork refresh complete",
                    f"Refreshed artwork for {refreshed} game(s)."
                    + (f"\nCould not refresh {failed} game(s)." if failed else "")
                    + "\n\niiSU artwork was not changed.",
                )

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_windows_apps_health(self) -> dict:
        """Return non-destructive health findings for Windows Apps and placeholders."""
        apps = self._load_windows_apps()
        windows_dir = self._windows_rom_dir(show_error=False)
        installed_steam_ids = self._installed_steam_ids()

        findings = {
            "missing_placeholders": [],
            "orphan_placeholders": [],
            "missing_executables": [],
            "uninstalled_steam": [],
            "invalid_uris": [],
            "invalid_entries": [],
            "duplicate_steam_ids": [],
        }

        steam_owners: dict[str, list[str]] = {}

        for name, entry in apps.items():
            if not isinstance(entry, dict):
                findings["invalid_entries"].append(name)
                continue

            if windows_dir is not None and not (windows_dir / f"{name}.pcgame").is_file():
                findings["missing_placeholders"].append(name)

            launch_type = str(entry.get("type", "executable")).lower()
            if launch_type == "executable":
                exe = entry.get("exe", "")
                if not isinstance(exe, str) or not exe.strip():
                    findings["missing_executables"].append(name)
                else:
                    expanded = Path(os.path.expandvars(os.path.expanduser(exe)))
                    if not expanded.is_file():
                        findings["missing_executables"].append(name)
            elif launch_type == "uri":
                uri = entry.get("uri", "")
                if not isinstance(uri, str) or not self._valid_uri(uri):
                    findings["invalid_uris"].append(name)
                    continue
                appid = self._steam_app_id(uri)
                if appid:
                    steam_owners.setdefault(appid, []).append(name)
                    if appid not in installed_steam_ids:
                        findings["uninstalled_steam"].append(name)
            else:
                findings["invalid_entries"].append(name)

        for appid, names in steam_owners.items():
            if len(names) > 1:
                findings["duplicate_steam_ids"].append((appid, names))

        if windows_dir is not None and windows_dir.is_dir():
            mapped = {name.casefold() for name in apps}
            try:
                for placeholder in windows_dir.glob("*.pcgame"):
                    if placeholder.stem.casefold() not in mapped:
                        findings["orphan_placeholders"].append(placeholder.stem)
            except OSError:
                pass

        return findings

    def _windows_apps_cleanup(self) -> None:
        findings = self._scan_windows_apps_health()

        counts = {
            "Missing placeholders": len(findings["missing_placeholders"]),
            "Orphan placeholders": len(findings["orphan_placeholders"]),
            "Missing executables": len(findings["missing_executables"]),
            "Steam games not installed": len(findings["uninstalled_steam"]),
            "Invalid URIs": len(findings["invalid_uris"]),
            "Invalid/unknown entries": len(findings["invalid_entries"]),
            "Duplicate Steam App IDs": len(findings["duplicate_steam_ids"]),
        }
        problem_total = sum(counts.values())

        dialog = tk.Toplevel(self)
        dialog.title("Windows Apps Health Check")
        dialog.geometry("760x580")
        dialog.minsize(650, 480)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()

        header = tk.Frame(dialog, bg=BG)
        header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="Windows Apps Health Check", bg=BG, fg=TEXT, font=FONT_HEADING).pack(anchor="w")
        tk.Label(
            header,
            text=("Everything looks healthy." if problem_total == 0 else f"Found {problem_total} item(s) worth reviewing."),
            bg=BG, fg=(GREEN if problem_total == 0 else TEXT_DIM), font=FONT_BODY,
        ).pack(anchor="w", pady=(2, 0))

        summary = Card(dialog)
        summary.pack(fill="x", padx=18, pady=(0, 10))
        inner = tk.Frame(summary, bg=PANEL_BG)
        inner.pack(fill="x", padx=14, pady=10)
        for label, count in counts.items():
            row = tk.Frame(inner, bg=PANEL_BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=PANEL_BG, fg=TEXT, font=FONT_BODY, anchor="w").pack(side="left")
            tk.Label(
                row, text=str(count), bg=PANEL_BG,
                fg=(GREEN if count == 0 else TEXT), font=FONT_BODY,
            ).pack(side="right")

        details_card = Card(dialog)
        details_card.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        details = tk.Text(
            details_card, wrap="word", state="normal", font=FONT_MONO,
            bg="#0e0e10", fg="#c9c9ce", insertbackground=TEXT,
            relief="flat", padx=10, pady=10,
        )
        details.pack(fill="both", expand=True, padx=8, pady=8)

        sections = [
            ("Missing placeholders", findings["missing_placeholders"],
             "Safe to repair automatically; the mapping itself is intact."),
            ("Orphan placeholders", findings["orphan_placeholders"],
             "A .pcgame file exists with no Windows Apps mapping. Left untouched."),
            ("Missing executables", findings["missing_executables"],
             "The configured EXE is missing or invalid. Update or remove the mapping manually."),
            ("Steam games not installed", findings["uninstalled_steam"],
             "The mapping is valid; Steam simply has no installed manifest right now. Left untouched."),
            ("Invalid URIs", findings["invalid_uris"],
             "The URI mapping needs to be edited or removed manually."),
            ("Invalid/unknown entries", findings["invalid_entries"],
             "The JSON entry is malformed or uses an unknown launch type."),
        ]
        for title, items, note in sections:
            if not items:
                continue
            details.insert("end", f"{title} ({len(items)})\n")
            details.insert("end", f"{note}\n")
            for item in items:
                details.insert("end", f"  • {item}\n")
            details.insert("end", "\n")

        if findings["duplicate_steam_ids"]:
            details.insert("end", f"Duplicate Steam App IDs ({len(findings['duplicate_steam_ids'])})\n")
            details.insert("end", "Multiple mappings point to the same Steam App ID. Left untouched.\n")
            for appid, names in findings["duplicate_steam_ids"]:
                details.insert("end", f"  • {appid}: {', '.join(names)}\n")
            details.insert("end", "\n")

        if problem_total == 0:
            details.insert("end", "No Windows Apps maintenance issues were found.\n")
        details.config(state="disabled")

        controls = tk.Frame(dialog, bg=BG)
        controls.pack(fill="x", padx=18, pady=(0, 16))

        def repair_safe():
            names = findings["missing_placeholders"]
            if not names:
                messagebox.showinfo(
                    "Nothing to repair",
                    "No safely repairable placeholder issues were found.",
                    parent=dialog,
                )
                return

            windows_dir = self._windows_rom_dir()
            if windows_dir is None:
                return
            try:
                windows_dir.mkdir(parents=True, exist_ok=True)
                repaired = 0
                for name in names:
                    placeholder = windows_dir / f"{name}.pcgame"
                    if not placeholder.exists():
                        placeholder.touch()
                        repaired += 1
            except OSError as e:
                messagebox.showerror("Repair failed", str(e), parent=dialog)
                return

            dialog.destroy()
            self._refresh_windows_apps_tree()
            self._refresh_steam_library_summary()
            messagebox.showinfo(
                "Repair complete",
                f"Recreated {repaired} missing placeholder(s).\n\n"
                "No mappings, orphan placeholders, or uninstalled Steam games were deleted.",
            )

        ttk.Button(
            controls, text="Repair Safe Issues", style="Accent.TButton", command=repair_safe,
        ).pack(side="left")
        ttk.Button(
            controls, text="Close", style="Ghost.TButton", command=dialog.destroy,
        ).pack(side="right")

    def _build_windows_apps_page(self) -> None:
        frame = self.pages["windows_apps"]
        self._clear(frame)
        self._page_header(
            frame, "Windows Apps",
            "Native executables, Steam games, and registered Windows protocol links in iiSU.",
        )
        self._windows_apps_sort_column = getattr(self, "_windows_apps_sort_column", "name")
        self._windows_apps_sort_reverse = getattr(self, "_windows_apps_sort_reverse", False)
        self._windows_apps_images = {}
        ttk.Style(self).configure("WindowsApps.Treeview", rowheight=32)

        steam_card = Card(frame)
        steam_card.pack(fill="x", padx=24, pady=(12, 4))
        steam_inner = tk.Frame(steam_card, bg=PANEL_BG)
        steam_inner.pack(fill="x", padx=14, pady=10)

        steam_text = tk.Frame(steam_inner, bg=PANEL_BG)
        steam_text.pack(side="left", fill="x", expand=True)
        tk.Label(
            steam_text, text="Steam Library", bg=PANEL_BG, fg=TEXT, font=FONT_HEADING
        ).pack(anchor="w")
        self.steam_library_summary_label = tk.Label(
            steam_text, text="Steam: scanning libraries...", bg=PANEL_BG,
            fg=TEXT_DIM, font=FONT_BODY, anchor="w",
        )
        self.steam_library_summary_label.pack(anchor="w", pady=(2, 0))

        ttk.Button(
            steam_inner, text="Auto-import New", style="Accent.TButton",
            command=self._auto_import_new_steam_games,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            steam_inner, text="Refresh Artwork", style="Ghost.TButton",
            command=self._refresh_steam_artwork_cache,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            steam_inner, text="Choose Games...", style="Ghost.TButton",
            command=self._import_steam_library,
        ).pack(side="right")
        ttk.Button(
            steam_inner, text="Health Check...", style="Ghost.TButton",
            command=self._windows_apps_cleanup,
        ).pack(side="right", padx=(0, 8))

        search_row = tk.Frame(frame, bg=BG)
        search_row.pack(fill="x", padx=24, pady=(12, 4))
        tk.Label(search_row, text="Search:", bg=BG, fg=TEXT, font=FONT_BODY).pack(side="left")
        self.windows_apps_search_var = tk.StringVar()
        tk.Entry(search_row, textvariable=self.windows_apps_search_var, **ENTRY_KWARGS).pack(
            side="left", fill="x", expand=True, padx=(8, 10), ipady=3
        )
        self.windows_apps_count_label = tk.Label(search_row, text="", bg=BG, fg=TEXT_DIM, font=FONT_BODY)
        self.windows_apps_count_label.pack(side="right")
        self.windows_apps_search_var.trace_add("write", self._refresh_windows_apps_tree)

        columns = ("name", "type", "target", "args", "status", "added")
        self.windows_apps_tree = ttk.Treeview(frame, columns=columns, show="tree headings", height=12, selectmode="extended")
        self.windows_apps_tree.heading("#0", text="Art")
        for col, label in (
            ("name", "Name"), ("type", "Launch type"), ("target", "Executable / URI"),
            ("args", "Arguments"), ("status", "Status"), ("added", "Added"),
        ):
            self.windows_apps_tree.heading(col, text=label, command=lambda c=col: self._sort_windows_apps(c))
        self.windows_apps_tree.heading("#0", text="Art")
        self.windows_apps_tree.column("#0", width=78, minwidth=60, stretch=False)
        self.windows_apps_tree.column("name", width=150)
        self.windows_apps_tree.column("type", width=95)
        self.windows_apps_tree.column("target", width=275)
        self.windows_apps_tree.column("args", width=105)
        self.windows_apps_tree.column("status", width=130)
        self.windows_apps_tree.column("added", width=90)
        self.windows_apps_tree.pack(fill="both", expand=True, padx=24, pady=(4, 4))
        self.windows_apps_tree.bind("<Double-1>", lambda _e: self._edit_windows_app())
        self.windows_apps_tree.bind("<Button-3>", self._show_windows_apps_context_menu)

        action_row = tk.Frame(frame, bg=BG)
        action_row.pack(fill="x", padx=24, pady=(0, 4))
        ttk.Button(action_row, text="Add Application...", style="Accent.TButton", command=self._add_windows_app).pack(side="left")
        ttk.Button(action_row, text="Import Steam Library...", style="Ghost.TButton", command=self._import_steam_library).pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Edit...", style="Ghost.TButton", command=self._edit_windows_app).pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Duplicate...", style="Ghost.TButton", command=self._duplicate_windows_app).pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Remove Selected", style="Ghost.TButton", command=self._remove_windows_app).pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Test...", style="Ghost.TButton", command=self._test_windows_app).pack(side="left", padx=(8, 0))

        utility_row = tk.Frame(frame, bg=BG)
        utility_row.pack(fill="x", padx=24, pady=(0, 8))
        ttk.Button(utility_row, text="Open Location / Copy URI", style="Ghost.TButton", command=self._windows_app_open_or_copy).pack(side="left")
        ttk.Button(utility_row, text="Sync / Repair...", style="Ghost.TButton", command=self._repair_windows_apps).pack(side="left", padx=(8, 0))
        ttk.Button(utility_row, text="Export...", style="Ghost.TButton", command=self._export_windows_apps).pack(side="left", padx=(8, 0))
        ttk.Button(utility_row, text="Import...", style="Ghost.TButton", command=self._import_windows_apps_file).pack(side="left", padx=(8, 0))
        ttk.Button(utility_row, text="Open Windows ROMs", style="Ghost.TButton", command=self._open_windows_roms).pack(side="left", padx=(8, 0))

        tk.Label(
            frame,
            text="Steam import reads your installed Steam libraries locally. Steam artwork shown here is Manager-only; "
                 "iiSU's own SteamGridDB artwork workflow is untouched.",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left", wraplength=850,
        ).pack(anchor="w", padx=24, pady=(0, 12))
        self._refresh_windows_apps_tree()

        self._refresh_steam_library_summary()

    def _windows_app_dialog(self, title: str, initial_name: str = "", initial: dict | None = None):
        initial = initial or {}
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        stored_type = str(initial.get("type", "executable")).lower()
        stored_uri = str(initial.get("uri", ""))
        if stored_type == "uri" and self._is_steam_uri(stored_uri):
            initial_type = "Steam Game"
        elif stored_type == "uri":
            initial_type = "Custom URI"
        else:
            initial_type = "Executable"

        name_var = tk.StringVar(value=initial_name)
        type_var = tk.StringVar(value=initial_type)
        exe_var = tk.StringVar(value=str(initial.get("exe", "")))
        uri_var = tk.StringVar(value=stored_uri)
        steam_search_var = tk.StringVar(value=initial_name if initial_type == "Steam Game" else "")
        steam_manual_var = tk.StringVar()
        selected_steam = {"appid": self._steam_app_id(stored_uri), "name": initial_name or None}
        existing_steam_id = self._steam_app_id(stored_uri)
        if existing_steam_id:
            steam_manual_var.set(existing_steam_id)
        args = initial.get("args", [])
        args_var = tk.StringVar(value=" ".join(str(a) for a in args) if isinstance(args, list) else "")
        work_var = tk.StringVar(value=str(initial.get("working_dir", "")))
        result = {"value": None}

        # Keep PhotoImage objects alive for as long as the dialog exists.
        steam_images = {}
        steam_search_generation = {"value": 0}

        body = tk.Frame(dialog, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=18)

        tk.Label(body, text="Name:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=0, column=0, sticky="w", pady=4)
        name_entry = tk.Entry(body, textvariable=name_var, width=58, **ENTRY_KWARGS)
        name_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

        tk.Label(body, text="Launch type:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=1, column=0, sticky="w", pady=4)
        type_combo = ttk.Combobox(
            body, textvariable=type_var,
            values=("Executable", "Steam Game", "Custom URI"), state="readonly", width=18, font=FONT_BODY,
        )
        type_combo.grid(row=1, column=1, columnspan=2, sticky="w", pady=4)

        dynamic = tk.Frame(body, bg=BG)
        dynamic.grid(row=2, column=0, columnspan=3, sticky="ew")
        dynamic.grid_columnconfigure(1, weight=1)

        def browse_exe():
            path = filedialog.askopenfilename(
                parent=dialog, title="Select Windows application",
                filetypes=[("Windows applications", "*.exe"), ("All files", "*.*")],
            )
            if path:
                exe_var.set(path)
                if not name_var.get().strip():
                    name_var.set(Path(path).stem)

        def browse_work():
            path = filedialog.askdirectory(parent=dialog, title="Select working directory")
            if path:
                work_var.set(path)

        def set_selected_steam(appid: str, game_name: str):
            selected_steam["appid"] = str(appid)
            selected_steam["name"] = game_name
            steam_manual_var.set(str(appid))
            name_var.set(self._safe_steam_pcgame_name(game_name))

        def rebuild_dynamic(*_args):
            steam_search_generation["value"] += 1
            for child in dynamic.winfo_children():
                child.destroy()
            steam_images.clear()

            if type_var.get() == "Steam Game":
                tk.Label(dynamic, text="Search Steam:", bg=BG, fg=TEXT, font=FONT_BODY).grid(
                    row=0, column=0, sticky="w", pady=(6, 4)
                )
                search_entry = tk.Entry(dynamic, textvariable=steam_search_var, width=44, **ENTRY_KWARGS)
                search_entry.grid(row=0, column=1, sticky="ew", pady=(6, 4))

                search_button = ttk.Button(dynamic, text="Search", style="Ghost.TButton")
                search_button.grid(row=0, column=2, padx=(8, 0), pady=(6, 4))

                status_var = tk.StringVar(value="Search by game name, then choose the correct Steam result.")
                tk.Label(
                    dynamic, textvariable=status_var, bg=BG, fg=TEXT_DIM, font=FONT_BODY,
                    justify="left", anchor="w",
                ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 6))

                results_frame = tk.Frame(dynamic, bg=BG)
                results_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
                columns = ("name", "appid", "state")
                ttk.Style(dialog).configure("SteamResults.Treeview", rowheight=48)
                results = ttk.Treeview(
                    results_frame, columns=columns, show="tree headings",
                    height=7, selectmode="browse", style="SteamResults.Treeview",
                )
                results.heading("#0", text="Artwork")
                results.heading("name", text="Game")
                results.heading("appid", text="App ID")
                results.heading("state", text="Status")
                results.column("#0", width=128, minwidth=128, stretch=False)
                results.column("name", width=330, minwidth=220)
                results.column("appid", width=80, minwidth=70, stretch=False)
                results.column("state", width=105, minwidth=90, stretch=False)
                scroll = ttk.Scrollbar(results_frame, orient="vertical", command=results.yview)
                results.configure(yscrollcommand=scroll.set)
                results.pack(side="left", fill="both", expand=True)
                scroll.pack(side="right", fill="y")

                selected_var = tk.StringVar(
                    value=(
                        f"Selected: {selected_steam['name']}  •  App ID {selected_steam['appid']}"
                        if selected_steam.get("appid") else "Selected: none"
                    )
                )
                tk.Label(
                    dynamic, textvariable=selected_var, bg=BG, fg=TEXT, font=FONT_BODY,
                    justify="left", anchor="w",
                ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(7, 2))

                manual_frame = tk.Frame(dynamic, bg=BG)
                manual_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 8))
                tk.Label(
                    manual_frame, text="Manual App ID / Steam URL:", bg=BG, fg=TEXT_DIM, font=FONT_BODY
                ).pack(side="left")
                tk.Entry(manual_frame, textvariable=steam_manual_var, width=26, **ENTRY_KWARGS).pack(
                    side="left", padx=(8, 0)
                )
                ttk.Button(
                    manual_frame, text="Use", style="Ghost.TButton",
                    command=lambda: use_manual_steam(selected_var),
                ).pack(side="left", padx=(8, 0))

                def choose_result(_event=None):
                    selected = results.selection()
                    if not selected:
                        return
                    item = selected[0]
                    values = results.item(item, "values")
                    if len(values) < 2:
                        return
                    game_name, appid = values[0], str(values[1])
                    set_selected_steam(appid, game_name)
                    selected_var.set(f"Selected: {game_name}  •  App ID {appid}")

                def use_manual_steam(label_var=selected_var):
                    app_id = self._steam_app_id(steam_manual_var.get())
                    if not app_id:
                        messagebox.showerror(
                            "Invalid Steam game",
                            "Enter a Steam App ID, Steam store URL, or steam:// launch URI.",
                            parent=dialog,
                        )
                        return

                    # If a search result already selected this ID, keep its known name.
                    if str(selected_steam.get("appid") or "") == str(app_id) and selected_steam.get("name"):
                        label_var.set(f"Selected: {selected_steam['name']}  •  App ID {app_id}")
                        return

                    selected_steam["appid"] = str(app_id)
                    selected_steam["name"] = None
                    label_var.set(f"Selected: App ID {app_id} (name lookup in progress...)")

                    def worker():
                        try:
                            import urllib.request
                            url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english&cc=US"
                            req = urllib.request.Request(url, headers={"User-Agent": "iiSU-PC Manager"})
                            with urllib.request.urlopen(req, timeout=8) as response:
                                payload = json.loads(response.read().decode("utf-8"))
                            data = payload.get(str(app_id), {})
                            game_name = data.get("data", {}).get("name") if data.get("success") else None
                        except Exception:
                            game_name = None

                        def finish():
                            if str(selected_steam.get("appid") or "") != str(app_id):
                                return
                            if game_name:
                                set_selected_steam(str(app_id), game_name)
                                label_var.set(f"Selected: {game_name}  •  App ID {app_id}")
                            else:
                                label_var.set(f"Selected: App ID {app_id}")
                        self.after(0, finish)

                    threading.Thread(target=worker, daemon=True).start()

                def load_artwork(appid: str, image_url: str, item_id: str, generation: int):
                    if not image_url:
                        return
                    try:
                        import io
                        import urllib.request
                        from PIL import Image, ImageTk

                        cache_dir = BRIDGE_DIR / "cache" / "steam_artwork"
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        cache_file = cache_dir / f"{appid}.jpg"

                        if cache_file.is_file():
                            raw = cache_file.read_bytes()
                        else:
                            req = urllib.request.Request(image_url, headers={"User-Agent": "iiSU-PC Manager"})
                            with urllib.request.urlopen(req, timeout=8) as response:
                                raw = response.read()
                            cache_file.write_bytes(raw)

                        image = Image.open(io.BytesIO(raw)).convert("RGB")
                        image.thumbnail((120, 45))
                        photo = ImageTk.PhotoImage(image)
                    except Exception:
                        # Artwork is cosmetic: no Pillow/network/bad image = text-only result.
                        return

                    def apply_image():
                        if generation != steam_search_generation["value"] or not results.exists(item_id):
                            return
                        steam_images[item_id] = photo
                        results.item(item_id, image=photo)
                    self.after(0, apply_image)

                def perform_search(_event=None):
                    query = steam_search_var.get().strip()
                    if len(query) < 2:
                        messagebox.showinfo("Steam search", "Type at least two characters to search Steam.", parent=dialog)
                        return

                    steam_search_generation["value"] += 1
                    generation = steam_search_generation["value"]
                    for item in results.get_children():
                        results.delete(item)
                    steam_images.clear()
                    search_button.config(state="disabled")
                    status_var.set("Searching Steam...")

                    def worker():
                        local_games = self._installed_steam_games()
                        installed_ids = {g["appid"] for g in local_games}
                        already_ids = self._steam_ids_already_added()

                        def steam_state(appid: str) -> str:
                            if str(appid) in already_ids:
                                return "Already Added"
                            if str(appid) in installed_ids:
                                return "Installed"
                            return "Store"

                        local_matches = [
                            {"appid": g["appid"], "name": g["name"], "image": "", "state": steam_state(g["appid"])}
                            for g in local_games
                            if query.casefold() in g["name"].casefold() or query == g["appid"]
                        ][:25]
                        try:
                            import urllib.parse
                            import urllib.request
                            params = urllib.parse.urlencode({"term": query, "l": "english", "cc": "US"})
                            url = f"https://store.steampowered.com/api/storesearch/?{params}"
                            req = urllib.request.Request(url, headers={"User-Agent": "iiSU-PC Manager"})
                            with urllib.request.urlopen(req, timeout=10) as response:
                                payload = json.loads(response.read().decode("utf-8"))
                            items = payload.get("items", [])
                            if not isinstance(items, list):
                                items = []
                            normalized = []
                            for item in items[:25]:
                                if not isinstance(item, dict):
                                    continue
                                appid = item.get("id")
                                game_name = item.get("name")
                                if appid is None or not game_name:
                                    continue
                                normalized.append({
                                    "appid": str(appid),
                                    "name": str(game_name),
                                    "image": str(item.get("tiny_image") or ""),
                                    "state": steam_state(str(appid)),
                                })
                            seen_ids = {item["appid"] for item in local_matches}
                            normalized = local_matches + [item for item in normalized if item["appid"] not in seen_ids]
                            normalized = normalized[:25]
                            error = None
                        except Exception as e:
                            normalized = local_matches
                            error = None if local_matches else str(e)

                        def finish():
                            if generation != steam_search_generation["value"]:
                                return
                            search_button.config(state="normal")
                            if error:
                                status_var.set("Steam search failed.")
                                messagebox.showerror(
                                    "Steam search failed",
                                    "Couldn't search the Steam Store right now.\n\n"
                                    f"{error}\n\nYou can still use the manual App ID field or Custom URI.",
                                    parent=dialog,
                                )
                                return
                            if not normalized:
                                status_var.set("No matching Steam games found.")
                                return

                            status_var.set(f"{len(normalized)} result(s) — select or double-click a game.")
                            for index, item in enumerate(normalized):
                                iid = f"steam_{generation}_{index}"
                                results.insert(
                                    "", "end", iid=iid, text="",
                                    values=(item["name"], item["appid"], item.get("state", "Store")),
                                )
                                if item["image"]:
                                    threading.Thread(
                                        target=load_artwork,
                                        args=(item["appid"], item["image"], iid, generation),
                                        daemon=True,
                                    ).start()

                        self.after(0, finish)

                    threading.Thread(target=worker, daemon=True).start()

                search_button.config(command=perform_search)
                search_entry.bind("<Return>", perform_search)
                results.bind("<<TreeviewSelect>>", choose_result)
                results.bind("<Double-1>", choose_result)
                self.after(50, search_entry.focus_set)

            elif type_var.get() == "Custom URI":
                tk.Label(dynamic, text="URI:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=0, column=0, sticky="w", pady=4)
                tk.Entry(dynamic, textvariable=uri_var, width=58, **ENTRY_KWARGS).grid(
                    row=0, column=1, columnspan=2, sticky="ew", pady=4
                )
                tk.Label(
                    dynamic,
                    text="Any registered Windows protocol URI, including non-Steam launchers and custom application links.",
                    bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
                ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 10))
            else:
                tk.Label(dynamic, text="Executable:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=0, column=0, sticky="w", pady=4)
                tk.Entry(dynamic, textvariable=exe_var, width=58, **ENTRY_KWARGS).grid(row=0, column=1, sticky="ew", pady=4)
                ttk.Button(dynamic, text="Browse...", style="Ghost.TButton", command=browse_exe).grid(
                    row=0, column=2, padx=(8, 0), pady=4
                )
                tk.Label(dynamic, text="Arguments:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=1, column=0, sticky="w", pady=4)
                tk.Entry(dynamic, textvariable=args_var, width=58, **ENTRY_KWARGS).grid(
                    row=1, column=1, columnspan=2, sticky="ew", pady=4
                )
                tk.Label(dynamic, text="Working directory:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=2, column=0, sticky="w", pady=4)
                tk.Entry(dynamic, textvariable=work_var, width=58, **ENTRY_KWARGS).grid(row=2, column=1, sticky="ew", pady=4)
                ttk.Button(dynamic, text="Browse...", style="Ghost.TButton", command=browse_work).grid(
                    row=2, column=2, padx=(8, 0), pady=4
                )
                tk.Label(
                    dynamic,
                    text="Arguments are space-separated. Leave Working directory blank to use the executable's folder.",
                    bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
                ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 10))

            dialog.update_idletasks()

        type_combo.bind("<<ComboboxSelected>>", rebuild_dynamic)
        rebuild_dynamic()

        def accept():
            if type_var.get() == "Steam Game":
                app_id = selected_steam.get("appid")
                if not app_id:
                    # Allow Save after typing a valid manual ID even if Use wasn't clicked.
                    app_id = self._steam_app_id(steam_manual_var.get())
                if not app_id:
                    messagebox.showerror(
                        "No Steam game selected",
                        "Search for a Steam game and select it, or enter an App ID manually.",
                        parent=dialog,
                    )
                    return

                # Search/manual lookup already filled Name with a filename-safe
                # version of the canonical Steam title. Keep any user edits here.
                if selected_steam.get("name") and not name_var.get().strip():
                    name_var.set(self._safe_steam_pcgame_name(str(selected_steam["name"])))

            name = self._safe_pcgame_name(name_var.get())
            if not name:
                messagebox.showerror("Invalid name", 'Enter a name without < > : " / \\ | ? *.', parent=dialog)
                return

            if type_var.get() == "Steam Game":
                app_id = selected_steam.get("appid") or self._steam_app_id(steam_manual_var.get())
                entry = {"type": "uri", "uri": f"steam://rungameid/{app_id}"}
                if selected_steam.get("name"):
                    entry["steam_name"] = str(selected_steam["name"])
            elif type_var.get() == "Custom URI":
                uri = uri_var.get().strip()
                if not self._valid_uri(uri):
                    messagebox.showerror(
                        "Invalid URI",
                        "Enter a registered protocol URI such as mylauncher://game/123.",
                        parent=dialog,
                    )
                    return
                entry = {"type": "uri", "uri": uri}
            else:
                exe = exe_var.get().strip()
                if not exe or not Path(os.path.expandvars(os.path.expanduser(exe))).is_file():
                    messagebox.showerror("Executable not found", "Choose an existing executable.", parent=dialog)
                    return
                import shlex
                try:
                    parsed_args = shlex.split(args_var.get(), posix=False)
                    parsed_args = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a for a in parsed_args]
                except ValueError as e:
                    messagebox.showerror("Invalid arguments", str(e), parent=dialog)
                    return
                entry = {"type": "executable", "exe": exe, "args": parsed_args}
                if work_var.get().strip():
                    entry["working_dir"] = work_var.get().strip()

            result["value"] = (name, entry)
            dialog.destroy()

        buttons = tk.Frame(body, bg=BG)
        buttons.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", style="Ghost.TButton", command=dialog.destroy).pack(side="left")
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=accept).pack(side="left", padx=(8, 0))
        body.grid_columnconfigure(1, weight=1)

        dialog.update_idletasks()
        dialog.geometry(f"+{self.winfo_rootx()+100}+{self.winfo_rooty()+55}")
        self.wait_window(dialog)
        return result["value"]

    def _create_windows_app(self, name: str, entry: dict, apps: dict | None = None) -> bool:
        entry = self._ensure_added_at(entry)
        apps = self._load_windows_apps() if apps is None else apps
        if any(existing.casefold() == name.casefold() for existing in apps):
            messagebox.showerror("Duplicate", f"A Windows app named '{name}' already exists.")
            return False

        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return False
        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
            (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
        except OSError as e:
            messagebox.showerror("Windows Apps", f"Couldn't create the .pcgame placeholder:\n\n{e}")
            return False

        apps[name] = entry
        if not self._save_windows_apps(apps):
            return False
        self._refresh_windows_apps_tree()
        if self.windows_apps_tree.exists(name):
            self.windows_apps_tree.selection_set(name)
            self.windows_apps_tree.see(name)
        return True

    def _add_windows_app(self) -> None:
        result = self._windows_app_dialog("Add Windows Application")
        if result:
            self._create_windows_app(*result)

    def _edit_windows_app(self) -> None:
        selected = self.windows_apps_tree.selection()
        if not selected:
            messagebox.showinfo("Nothing selected", "Select a Windows app first.")
            return
        old_name = selected[0]
        apps = self._load_windows_apps()
        old_entry = apps.get(old_name)
        if not isinstance(old_entry, dict):
            return
        result = self._windows_app_dialog("Edit Windows Application", old_name, old_entry)
        if not result:
            return
        new_name, new_entry = result
        if new_name.casefold() != old_name.casefold() and any(k.casefold() == new_name.casefold() for k in apps):
            messagebox.showerror("Duplicate", f"A Windows app named '{new_name}' already exists.")
            return

        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return
        old_stub = windows_dir / f"{old_name}.pcgame"
        new_stub = windows_dir / f"{new_name}.pcgame"
        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
            if old_stub != new_stub and old_stub.exists():
                old_stub.rename(new_stub)
            else:
                new_stub.touch(exist_ok=True)
        except OSError as e:
            messagebox.showerror("Windows Apps", f"Couldn't update the .pcgame placeholder:\n\n{e}")
            return

        apps.pop(old_name, None)
        apps[new_name] = new_entry
        if self._save_windows_apps(apps):
            self._refresh_windows_apps_tree()
            if self.windows_apps_tree.exists(new_name):
                self.windows_apps_tree.selection_set(new_name)
                self.windows_apps_tree.see(new_name)

    def _duplicate_windows_app(self) -> None:
        selected = self.windows_apps_tree.selection()
        if not selected:
            messagebox.showinfo("Nothing selected", "Select a Windows app first.")
            return
        if len(selected) != 1:
            messagebox.showinfo("Select one", "Select one Windows app to duplicate.")
            return
        source_name = selected[0]
        entry = self._load_windows_apps().get(source_name)
        if not isinstance(entry, dict):
            return

        apps = self._load_windows_apps()
        base = f"{source_name} Copy"
        suggested = base
        number = 2
        while any(name.casefold() == suggested.casefold() for name in apps):
            suggested = f"{base} {number}"
            number += 1

        # JSON round-trip makes a simple deep copy without adding another dependency.
        cloned = json.loads(json.dumps(entry))
        result = self._windows_app_dialog("Duplicate Windows Application", suggested, cloned)
        if result:
            self._create_windows_app(*result)

    def _remove_windows_app(self) -> None:
        selected = list(self.windows_apps_tree.selection())
        if not selected:
            messagebox.showinfo("Nothing selected", "Select one or more applications first.")
            return
        if not messagebox.askyesno(
            "Remove Windows Apps?",
            f"Remove {len(selected)} selected application(s) from iiSU-PC?\n\n"
            "Their .pcgame placeholders will also be removed. This does not uninstall the applications themselves.",
        ):
            return
        apps = self._load_windows_apps()
        windows_dir = self._windows_rom_dir(show_error=False)
        for name in selected:
            apps.pop(name, None)
            if windows_dir is not None:
                try:
                    (windows_dir / f"{name}.pcgame").unlink(missing_ok=True)
                except OSError:
                    pass
        if self._save_windows_apps(apps):
            self._refresh_windows_apps_tree()
        self._refresh_steam_library_summary()

    def _test_windows_app(self) -> None:
        """Launch the selected Windows app directly, bypassing iiSU and the bridge."""
        selected = self.windows_apps_tree.selection()
        if not selected:
            messagebox.showinfo("Nothing selected", "Select a Windows app first.")
            return
        if len(selected) != 1:
            messagebox.showinfo("Select one", "Select one Windows app to test.")
            return

        app_name = selected[0]
        entry = self._load_windows_apps().get(app_name)
        if not isinstance(entry, dict):
            messagebox.showerror("Can't test", f"'{app_name}' has an invalid configuration entry.")
            return

        launch_type = str(entry.get("type", "executable")).lower()
        if launch_type == "uri":
            uri = entry.get("uri")
            if not isinstance(uri, str) or not self._valid_uri(uri):
                messagebox.showerror("Can't test", f"'{app_name}' has an invalid URI.")
                return
            try:
                os.startfile(uri.strip())
            except OSError as e:
                scheme = uri.split(":", 1)[0]
                messagebox.showerror(
                    "Launch failed",
                    f"Windows couldn't open '{app_name}'.\n\n"
                    f"URI: {uri}\nProtocol: {scheme}://\n\n"
                    "The application that handles this protocol may not be installed or registered.\n\n"
                    f"Windows error: {e}",
                )
            return

        if launch_type != "executable":
            messagebox.showerror("Can't test", f"'{app_name}' has an unknown launch type: {launch_type}")
            return

        exe_value = entry.get("exe")
        if not isinstance(exe_value, str) or not exe_value.strip():
            messagebox.showerror("Can't test", f"'{app_name}' has no executable configured.")
            return

        executable = Path(os.path.expandvars(os.path.expanduser(exe_value)))
        if not executable.is_file():
            messagebox.showerror("Executable not found", f"The configured executable for '{app_name}' does not exist:\n\n{executable}")
            return

        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            messagebox.showerror("Can't test", f"'{app_name}' has invalid arguments. The args value must be a list of strings.")
            return

        working_dir_value = entry.get("working_dir")
        working_dir = (
            Path(os.path.expandvars(os.path.expanduser(str(working_dir_value))))
            if working_dir_value else executable.parent
        )
        if not working_dir.is_dir():
            messagebox.showerror("Working directory not found", f"The configured working directory for '{app_name}' does not exist:\n\n{working_dir}")
            return

        command = [str(executable), *args]
        try:
            subprocess.Popen(command, cwd=str(working_dir))
        except OSError as e:
            messagebox.showerror(
                "Launch failed",
                f"Windows couldn't launch '{app_name}'.\n\n"
                f"Executable: {executable}\nArguments: {' '.join(args) or '(none)'}\n"
                f"Working directory: {working_dir}\n\n{e}",
            )

    def _windows_app_open_or_copy(self) -> None:
        selected = self.windows_apps_tree.selection()
        if not selected:
            messagebox.showinfo("Nothing selected", "Select a Windows app first.")
            return
        if len(selected) != 1:
            messagebox.showinfo("Select one", "Select one Windows app first.")
            return

        name = selected[0]
        entry = self._load_windows_apps().get(name)
        if not isinstance(entry, dict):
            return
        if str(entry.get("type", "executable")).lower() == "uri":
            uri = str(entry.get("uri", "")).strip()
            if not uri:
                messagebox.showerror("No URI", f"'{name}' has no URI configured.")
                return
            self.clipboard_clear()
            self.clipboard_append(uri)
            self.update()
            messagebox.showinfo("URI copied", f"Copied to clipboard:\n\n{uri}")
            return

        exe = Path(os.path.expandvars(os.path.expanduser(str(entry.get("exe", "")))))
        if not exe.is_file():
            messagebox.showerror("Executable not found", f"The configured executable does not exist:\n\n{exe}")
            return
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(exe)])
        except OSError as e:
            messagebox.showerror("Couldn't open location", str(e))

    def _repair_windows_apps(self) -> None:
        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return
        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Windows Apps", f"Couldn't create the Windows ROM folder:\n\n{e}")
            return

        apps = self._load_windows_apps()
        mapped = {name.casefold(): name for name in apps}
        try:
            placeholders = {p.stem.casefold(): p for p in windows_dir.glob("*.pcgame") if p.is_file()}
        except OSError as e:
            messagebox.showerror("Windows Apps", f"Couldn't scan placeholders:\n\n{e}")
            return

        missing = [name for key, name in mapped.items() if key not in placeholders]
        orphans = [path for key, path in placeholders.items() if key not in mapped]

        created = []
        failed = []
        for name in missing:
            try:
                (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
                created.append(name)
            except OSError as e:
                failed.append(f"{name}: {e}")

        self._refresh_windows_apps_tree()

        summary = []
        if created:
            summary.append(f"Recreated {len(created)} missing placeholder(s):\n  " + "\n  ".join(created))
        if orphans:
            summary.append(f"Found {len(orphans)} unconfigured placeholder(s):\n  " + "\n  ".join(p.name for p in orphans))
        if failed:
            summary.append("Could not repair:\n  " + "\n  ".join(failed))
        if not summary:
            messagebox.showinfo("Windows Apps", "Everything is already in sync. No repairs were needed.")
            return

        if orphans and messagebox.askyesno(
            "Windows Apps — Sync / Repair",
            "\n\n".join(summary) + "\n\nWould you like to configure the unconfigured placeholders now?",
        ):
            apps = self._load_windows_apps()
            for orphan in orphans:
                original_name = orphan.stem
                result = self._windows_app_dialog("Configure Existing Placeholder", original_name)
                if not result:
                    continue
                new_name, entry = result
                if any(existing.casefold() == new_name.casefold() for existing in apps):
                    messagebox.showerror("Duplicate", f"A Windows app named '{new_name}' already exists.")
                    continue
                new_stub = windows_dir / f"{new_name}.pcgame"
                try:
                    if orphan != new_stub:
                        orphan.rename(new_stub)
                except OSError as e:
                    messagebox.showerror("Windows Apps", f"Couldn't rename the placeholder:\n\n{e}")
                    continue
                apps[new_name] = entry
            if self._save_windows_apps(apps):
                self._refresh_windows_apps_tree()
        else:
            messagebox.showinfo("Windows Apps — Sync / Repair", "\n\n".join(summary))

    def _open_windows_roms(self) -> None:
        windows_dir = self._windows_rom_dir()
        if windows_dir is None:
            return
        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(windows_dir)
        except OSError as e:
            messagebox.showerror("Windows Apps", str(e))


    def _build_display_page(self) -> None:
        frame = self.pages["display"]
        self._clear(frame)
        self._page_header(frame, "Display", "The emulated device's actual hardware profile -- applying it cold-boots the AVD.")
        # _page_header() already packed a header + gradient bar straight
        # into `frame` -- everything below needs grid's row/column layout,
        # and Tk refuses to mix pack and grid children in the same parent,
        # so this all lives in its own packed sub-frame instead.
        body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)

        display = self.config_data.get("display", {"width": 1920, "height": 1080, "density": 240, "refresh_rate": 60})
        self.display_width_var = tk.StringVar(value=str(display.get("width", 1920)))
        self.display_height_var = tk.StringVar(value=str(display.get("height", 1080)))
        self.display_density_var = tk.StringVar(value=str(display.get("density", 240)))
        self.display_refresh_var = tk.StringVar(value=str(display.get("refresh_rate", 60)))
        self.gpu_mode_var = tk.StringVar(value=display.get("gpu_mode", "auto"))

        settings_col = tk.Frame(body, bg=BG)
        settings_col.grid(row=1, column=0, sticky="nw", padx=24, pady=(8, 0))

        tk.Label(settings_col, text="Resolution:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=0, column=0, sticky="w")
        self.resolution_preset_var = tk.StringVar()
        resolution_combo = ttk.Combobox(
            settings_col, textvariable=self.resolution_preset_var, values=RESOLUTION_PRESETS, state="readonly", width=14, font=FONT_BODY
        )
        resolution_combo.grid(row=0, column=1, sticky="w", padx=(8, 4), pady=3)
        resolution_combo.bind("<<ComboboxSelected>>", self._apply_resolution_preset)

        tk.Label(settings_col, text="or exactly:", bg=BG, fg=TEXT_DIM, font=FONT_BODY).grid(row=1, column=0, sticky="w", pady=3)
        exact_row = tk.Frame(settings_col, bg=BG)
        exact_row.grid(row=1, column=1, sticky="w", padx=(8, 0))
        tk.Entry(exact_row, textvariable=self.display_width_var, width=6, **ENTRY_KWARGS).pack(side="left")
        tk.Label(exact_row, text="x", bg=BG, fg=TEXT_DIM, font=FONT_BODY).pack(side="left", padx=4)
        tk.Entry(exact_row, textvariable=self.display_height_var, width=6, **ENTRY_KWARGS).pack(side="left")

        tk.Label(settings_col, text="Density (dpi):", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=2, column=0, sticky="w", pady=3)
        tk.Entry(settings_col, textvariable=self.display_density_var, width=8, **ENTRY_KWARGS).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=3)

        tk.Label(settings_col, text="Refresh rate (Hz):", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=3, column=0, sticky="w", pady=3)
        refresh_row = tk.Frame(settings_col, bg=BG)
        refresh_row.grid(row=3, column=1, sticky="w", padx=(8, 0))
        tk.Entry(refresh_row, textvariable=self.display_refresh_var, width=6, **ENTRY_KWARGS).pack(side="left")
        refresh_combo = ttk.Combobox(refresh_row, values=REFRESH_RATE_PRESETS, state="readonly", width=5, font=FONT_BODY)
        refresh_combo.pack(side="left", padx=(6, 0))
        refresh_combo.bind("<<ComboboxSelected>>", lambda e: self.display_refresh_var.set(refresh_combo.get()))

        tk.Label(settings_col, text="GPU rendering:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=4, column=0, sticky="w", pady=3)
        gpu_combo = ttk.Combobox(
            settings_col, textvariable=self.gpu_mode_var, values=GPU_MODE_PRESETS, state="readonly", width=14, font=FONT_BODY
        )
        gpu_combo.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=3)
        tk.Label(
            settings_col,
            text="Try \"host\" or \"swiftshader_indirect\" here if you see screen tearing\nor audio cutting out after tabbing away and back -- a known Android\nEmulator GPU-backend issue on some hardware. \"auto\" is the default.",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))

        preview_col = tk.Frame(body, bg=BG)
        preview_col.grid(row=1, column=1, sticky="ne", padx=24, pady=(8, 0))
        tk.Label(preview_col, text="Preview", bg=BG, fg=TEXT_DIM, font=FONT_BODY).pack(anchor="e")
        self.aspect_canvas = tk.Canvas(preview_col, width=150, height=100, bg="#0e0e10", highlightthickness=0)
        self.aspect_canvas.pack()
        self.display_width_var.trace_add("write", self._redraw_aspect_preview)
        self.display_height_var.trace_add("write", self._redraw_aspect_preview)
        self._redraw_aspect_preview()

        tk.Label(
            body,
            text="Only affects iiSU's own UI smoothness inside the AVD -- actual gameplay runs\n"
            "in a separate native Windows emulator process, which already uses your\n"
            "monitor's real refresh rate with no setup needed.",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=24, pady=(14, 8))

        ttk.Button(body, text="Auto-detect from primary monitor", style="Ghost.TButton", command=self._autodetect_display).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=24, pady=(4, 0)
        )

        self.iisu_fullscreen_var = tk.BooleanVar(value=self.config_data.get("iisu_fullscreen", True))
        ttk.Checkbutton(body, text="Maximize the iiSU/AVD window automatically", variable=self.iisu_fullscreen_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=24, pady=(12, 0)
        )

        tk.Label(body, text="AVD name:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=5, column=0, sticky="w", padx=24, pady=(16, 0))
        self.avd_name_var = tk.StringVar(value=self.config_data.get("avd_name", "iisuwin"))
        tk.Entry(body, textvariable=self.avd_name_var, width=20, **ENTRY_KWARGS).grid(row=6, column=0, sticky="w", padx=24, pady=(4, 8))

    def _apply_resolution_preset(self, event=None) -> None:
        choice = self.resolution_preset_var.get()
        if "x" not in choice:
            return
        width, height = (part.strip() for part in choice.split("x"))
        self.display_width_var.set(width)
        self.display_height_var.set(height)

    def _redraw_aspect_preview(self, *_args) -> None:
        canvas = self.aspect_canvas
        try:
            canvas.delete("all")
        except tk.TclError:
            return
        box_w, box_h = int(canvas["width"]), int(canvas["height"])
        try:
            width = int(self.display_width_var.get())
            height = int(self.display_height_var.get())
        except ValueError:
            return
        if width <= 0 or height <= 0:
            return
        margin = 10
        scale = min((box_w - margin * 2) / width, (box_h - margin * 2) / height)
        rect_w, rect_h = width * scale, height * scale
        x0, y0 = (box_w - rect_w) / 2, (box_h - rect_h) / 2
        canvas.create_rectangle(x0, y0, x0 + rect_w, y0 + rect_h, fill=PANEL_BG_HOVER, outline=GRADIENT_STOPS[2], width=2)
        canvas.create_text(box_w / 2, box_h / 2, text=f"{width}×{height}", fill=TEXT, font=FONT_BODY)

    def _autodetect_display(self) -> None:
        try:
            width, height, hz = winapi.get_primary_monitor_mode()
        except Exception as e:
            messagebox.showerror("Couldn't detect monitor", str(e))
            return
        self.display_width_var.set(str(width))
        self.display_height_var.set(str(height))
        self.display_refresh_var.set(str(hz))
        # Density has to scale with resolution, not stay fixed -- this was
        # previously left completely untouched by Auto-detect. Android's
        # own UI sizing is density-driven (dp -> px = dp * density/160), so
        # jumping from this project's 1920x1080 default to e.g. a 4K TV's
        # 3840x2160 while density stayed at its default 240 quadrupled the
        # screen's real pixel area under UI elements sized in the same
        # fixed number of physical pixels -- confirmed live: "ran iiSU at
        # 4K on my TV, it was tiny as." Windows' own per-monitor DPI
        # doesn't help here (it reflects the user's Windows text-scaling
        # preference, not how big Android UI should render on a
        # console-style fullscreen display) -- scaling density by the same
        # ratio as the resolution change instead keeps everything the same
        # apparent size as this project's known-good 1920x1080@240dpi
        # baseline, just sharper at higher resolutions.
        density = round(REFERENCE_DISPLAY["density"] * height / REFERENCE_DISPLAY["height"])
        self.display_density_var.set(str(density))


    def _build_advanced_page(self) -> None:
        frame = self.pages["advanced"]
        self._clear(frame)
        self._page_header(frame, "Advanced", "Bridge port, window matching, and hotkeys -- rarely need to change these.")
        # See the matching comment in _build_display_page() -- pack (used by
        # _page_header) and grid (used below) can't share the same parent.
        body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="iiSU window title match:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=1, column=0, sticky="w", padx=24, pady=(12, 0))
        self.window_title_var = tk.StringVar(value=self.config_data.get("iisu_window_title", ""))
        tk.Entry(body, textvariable=self.window_title_var, width=40, **ENTRY_KWARGS).grid(row=2, column=0, sticky="w", padx=24, pady=(4, 8))

        tk.Label(body, text="Bridge listen port:", bg=BG, fg=TEXT, font=FONT_BODY).grid(row=3, column=0, sticky="w", padx=24)
        self.port_var = tk.StringVar(value=str(self.config_data.get("bridge_port", 7737)))
        tk.Entry(body, textvariable=self.port_var, width=10, **ENTRY_KWARGS).grid(row=4, column=0, sticky="w", padx=24, pady=(4, 8))

        self.quit_hotkey_vars = self._build_hotkey_editor(
            body, row=5, title="Quit key (tap to force-quit the running emulator and return to iiSU):",
            initial=self.config_data.get("quit_hotkey", {"modifiers": [], "key": "escape"}),
        )

        tk.Label(body, text="Hold the quit key this long to close iiSU and the AVD entirely (seconds):", bg=BG, fg=TEXT, font=FONT_BODY).grid(
            row=8, column=0, columnspan=2, sticky="w", padx=24, pady=(8, 2)
        )
        self.shutdown_hold_seconds_var = tk.StringVar(value=str(self.config_data.get("shutdown_hold_seconds", 5)))
        tk.Entry(body, textvariable=self.shutdown_hold_seconds_var, width=6, **ENTRY_KWARGS).grid(row=9, column=0, sticky="w", padx=24, pady=(0, 8))

        self.shutdown_hotkey_vars = self._build_hotkey_editor(
            body, row=10, title="Optional separate full-shutdown hotkey (in addition to holding the quit key above -- leave blank for none):",
            initial=self.config_data.get("shutdown_hotkey") or {"modifiers": [], "key": ""},
        )

        tk.Label(
            body,
            text="Port and hotkey changes need the bridge restarted to take effect (ROM\n"
            "directory, search folders, and emulator mappings apply on the very next\n"
            "game launch, no restart needed).",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
        ).grid(row=13, column=0, sticky="w", padx=24, pady=(8, 8))

        self.debug_console_var = tk.BooleanVar(value=self.config_data.get("debug_show_console_windows", False))
        ttk.Checkbutton(
            body, text="Show console windows for the AVD and bridge (debugging)", variable=self.debug_console_var
        ).grid(row=14, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 4))
        tk.Label(
            body,
            text="Off by default: the AVD, bridge, and shutdown-hotkey teardown all run without a visible\n"
            "console, logging to emulator.log/bridge.log/stop.log instead, and the fullscreen loading\n"
            "overlay covers the AVD-boot/emulator-handoff gaps. Turn this on to watch their live output\n"
            "directly instead -- also turns the overlay off, since it would just hide those consoles.\n"
            "Trades away that run's log file, since a process can't sensibly have both. Takes effect on\n"
            "the next Start.",
            bg=BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
        ).grid(row=15, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 16))

    def _build_hotkey_editor(self, parent, row: int, title: str, initial: dict) -> dict:
        tk.Label(parent, text=title, bg=BG, fg=TEXT, font=FONT_BODY).grid(row=row, column=0, columnspan=2, sticky="w", padx=24, pady=(4, 2))

        initial_mods = {m.lower() for m in initial.get("modifiers", [])}
        mod_row = tk.Frame(parent, bg=BG)
        mod_row.grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=24)
        mod_vars = {}
        for name in MODIFIER_NAMES:
            var = tk.BooleanVar(value=name in initial_mods)
            mod_vars[name] = var
            ttk.Checkbutton(mod_row, text=name.capitalize(), variable=var).pack(side="left", padx=(0, 12))

        key_row = tk.Frame(parent, bg=BG)
        key_row.grid(row=row + 2, column=0, columnspan=2, sticky="w", padx=24, pady=(4, 8))
        tk.Label(key_row, text="+", bg=BG, fg=TEXT_DIM, font=FONT_BODY).pack(side="left", padx=(0, 8))
        key_var = tk.StringVar(value=initial.get("key", ""))
        tk.Label(key_row, textvariable=key_var, width=8, bg="#0e0e10", fg=TEXT, font=FONT_BODY, relief="flat", padx=8, pady=4).pack(side="left")
        capture_button = ttk.Button(key_row, text="Press a key...", style="Ghost.TButton")
        capture_button.configure(command=lambda: self._capture_key(key_var, capture_button))
        capture_button.pack(side="left", padx=(8, 0))

        return {"mods": mod_vars, "key": key_var}

    def _capture_key(self, key_var: tk.StringVar, button: ttk.Button) -> None:
        original_text = button.cget("text")
        button.configure(text="press any key...", state="disabled")

        def on_key(event: tk.Event) -> None:
            self.unbind("<KeyPress>")
            keysym = event.keysym if event.keysym not in ("??", "") else event.char
            if keysym:
                key_var.set(keysym.lower())
            button.configure(text=original_text, state="normal")

        self.bind("<KeyPress>", on_key)

    def _page_header(self, frame: tk.Frame, title: str, subtitle: str) -> None:
        header = tk.Frame(frame, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(header, text=title, font=FONT_TITLE, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(header, text=subtitle, font=FONT_BODY, bg=BG, fg=TEXT_DIM, justify="left").pack(anchor="w")
        gradient = tk.Canvas(frame, height=3, bg=BG, highlightthickness=0)
        gradient.pack(fill="x", padx=24, pady=(10, 0))
        self.after(10, lambda: draw_gradient_bar(gradient, gradient.winfo_width() or 900, 3))
        frame.bind("<Configure>", lambda e: draw_gradient_bar(gradient, gradient.winfo_width(), 3))

    @staticmethod
    def _read_hotkey(hotkey_vars: dict, default_key: str) -> dict:
        return {"modifiers": [name for name, var in hotkey_vars["mods"].items() if var.get()], "key": hotkey_vars["key"].get().strip() or default_key}

    @staticmethod
    def _read_optional_hotkey(hotkey_vars: dict) -> dict | None:
        key = hotkey_vars["key"].get().strip()
        if not key:
            return None
        return {"modifiers": [name for name, var in hotkey_vars["mods"].items() if var.get()], "key": key}

    def _save_settings(self) -> None:
        original_emulators = self.config_data.get("emulators", {})
        emulators = {}
        for item in self.emulators_tree.get_children():
            prefix, exe_names_str, pre_args_str = self.emulators_tree.item(item, "values")
            original = original_emulators.get(prefix, {})
            if "by_extension" in original:
                emulators[prefix] = original
                continue
            emulators[prefix] = {
                "exe_names": [s.strip() for s in exe_names_str.split(",") if s.strip()],
                "pre_args": [s.strip() for s in pre_args_str.split(",") if s.strip()],
            }

        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Bridge listen port must be a number.")
            return

        try:
            display = {
                "width": int(self.display_width_var.get()),
                "height": int(self.display_height_var.get()),
                "density": int(self.display_density_var.get()),
                "refresh_rate": int(self.display_refresh_var.get()),
                "gpu_mode": self.gpu_mode_var.get(),
            }
        except ValueError:
            messagebox.showerror("Invalid display settings", "Width, height, density, and refresh rate must be numbers.")
            return

        try:
            shutdown_hold_seconds = int(self.shutdown_hold_seconds_var.get())
        except ValueError:
            messagebox.showerror("Invalid hold duration", "The quit-key hold duration must be a number of seconds.")
            return

        self.config_data = {
            "bridge_port": port,
            "roms_dir": self.roms_dir_var.get().strip(),
            "search_roots": list(self.search_roots_list.get(0, "end")),
            "iisu_window_title": self.window_title_var.get().strip(),
            "iisu_fullscreen": self.iisu_fullscreen_var.get(),
            "iisu_component": self.config_data.get("iisu_component", "com.iisulauncher/com.iisulauncher.launcher.StartupSafeModeActivity"),
            "avd_name": self.avd_name_var.get().strip() or "iisuwin",
            "display": display,
            "quit_hotkey": self._read_hotkey(self.quit_hotkey_vars, default_key="escape"),
            "shutdown_hold_seconds": shutdown_hold_seconds,
            "shutdown_hotkey": self._read_optional_hotkey(self.shutdown_hotkey_vars),
            "usb_passthrough": self.config_data.get("usb_passthrough", []),
            "debug_show_console_windows": self.debug_console_var.get(),
            "emulators": emulators,
        }
        save_config(self.config_data)
        self._write_avd_display_profile(self.config_data)
        self.save_status_label.config(text=f"Saved to {CONFIG_PATH.name}")
        self.after(3000, lambda: self.save_status_label.config(text=""))

    def _write_avd_display_profile(self, config: dict) -> None:
        """Writes the chosen resolution/density straight into the AVD's own
        config.ini -- the actual hardware-profile file emulator.exe reads
        at boot -- without booting anything to do it, same as onboarding_
        wizard.py's own copy of this. Now that Save is locked out while
        the VM is running (see _refresh_save_lock), this only ever runs
        while it's stopped, so there's nothing live to disturb -- it just
        needs to be in place before the next Start, which always cold-
        boots anyway. Best-effort: a failure here just leaves the AVD on
        its previous profile until this runs again successfully."""
        import apply_display

        config_ini = apply_display.avd_config_path(config.get("avd_name", "iisuwin"))
        if not config_ini.is_file():
            return
        try:
            apply_display.update_config_ini(config_ini, config["display"])
        except OSError as e:
            print(f"[manager] couldn't write the AVD's display profile ({e})")

    # -- Credits page -------------------------------------------------

    def _build_credits_page(self) -> None:
        page = self.pages["credits"]
        self._page_header(page, "Credits", "Who made this, and how.")

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=(4, 0))

        self._build_credit_row(body, username="MAGOOSKEE", display_name="MAGOOSKEE", role="Project owner -- built and maintains iiSU-PC.")
        self._build_credit_row(
            body, username="claude", display_name="Claude (Anthropic)",
            role="AI coding assistant -- wrote and refactored most of this codebase, including this Manager app, in collaboration with MAGOOSKEE.",
        )

        disclaimer = Card(body)
        disclaimer.pack(fill="x", pady=(8, 0))
        tk.Label(
            disclaimer,
            text="AI disclosure: a large share of this project's code (including this Manager\n"
            "app) was written by Claude, an AI assistant, working under MAGOOSKEE's direction\n"
            "and review. If you're evaluating this project's safety or correctness, keep that\n"
            "in mind -- read the source rather than assuming a human wrote every line.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left", wraplength=680,
        ).pack(anchor="w", padx=16, pady=14)

    def _build_credit_row(self, parent, username: str, display_name: str, role: str) -> None:
        row = Card(parent)
        row.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(row, bg=PANEL_BG)
        inner.pack(fill="x", padx=16, pady=14)

        avatar_size = 64
        avatar_holder = tk.Frame(inner, width=avatar_size, height=avatar_size, bg=PANEL_BG)
        avatar_holder.pack(side="left")
        avatar_holder.pack_propagate(False)
        placeholder = make_placeholder_circle(avatar_holder, avatar_size, display_name, GRADIENT_STOPS[2], "#101010")
        placeholder.pack()

        text_col = tk.Frame(inner, bg=PANEL_BG)
        text_col.pack(side="left", padx=(14, 0), fill="x", expand=True)
        name_label = tk.Label(text_col, text=display_name, font=FONT_HEADING, bg=PANEL_BG, fg=GRADIENT_STOPS[2], cursor="hand2")
        name_label.pack(anchor="w")
        name_label.bind("<Button-1>", lambda e: webbrowser.open(f"https://github.com/{username}"))
        tk.Label(text_col, text=f"github.com/{username}", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM).pack(anchor="w")
        tk.Label(text_col, text=role, font=FONT_BODY, bg=PANEL_BG, fg=TEXT, justify="left", wraplength=560).pack(anchor="w", pady=(6, 0))

        threading.Thread(target=self._load_avatar, args=(username, avatar_holder, avatar_size, placeholder), daemon=True).start()

    def _load_avatar(self, username: str, holder: tk.Frame, size: int, placeholder: tk.Widget) -> None:
        data = fetch_avatar_bytes(username)
        if data is None:
            return
        self.after(0, self._apply_avatar, data, holder, size, placeholder)

    def _apply_avatar(self, data: bytes, holder: tk.Frame, size: int, placeholder: tk.Widget) -> None:
        photo = make_circular_photo(data, size)
        if photo is None:
            return
        placeholder.destroy()
        label = tk.Label(holder, image=photo, bg=PANEL_BG, bd=0)
        label.image = photo
        label.pack()

    # -- Uninstall page -------------------------------------------------

    def _build_uninstall_page(self) -> None:
        page = self.pages["uninstall"]
        header = tk.Frame(page, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(header, text="Uninstall", font=FONT_TITLE, bg=BG, fg=RED).pack(anchor="w")
        tk.Label(
            header,
            text="Removes the Android VM, its SDK, your bridge config, the signing keystore, and\n"
            "the desktop shortcut. Does NOT touch your ROM library, your PC emulators, or the\n"
            "iiSU APK you supplied.",
            font=FONT_BODY, bg=BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        gradient = tk.Canvas(page, height=3, bg=BG, highlightthickness=0)
        gradient.pack(fill="x", padx=24, pady=(10, 14))
        self.after(10, lambda: draw_gradient_bar(gradient, gradient.winfo_width() or 900, 3))
        page.bind("<Configure>", lambda e: draw_gradient_bar(gradient, gradient.winfo_width(), 3))

        list_card = Card(page)
        list_card.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        list_inner = tk.Frame(list_card, bg=PANEL_BG)
        list_inner.pack(fill="both", expand=True, padx=10, pady=10)
        columns = ("path", "size")
        self.uninstall_tree = ttk.Treeview(list_inner, columns=columns, show="headings", height=8)
        self.uninstall_tree.heading("path", text="Will remove")
        self.uninstall_tree.heading("size", text="Size")
        self.uninstall_tree.column("path", width=580)
        self.uninstall_tree.column("size", width=100, anchor="e")
        self.uninstall_tree.pack(fill="both", expand=True)

        bottom_row = tk.Frame(page, bg=BG)
        bottom_row.pack(fill="x", padx=24, pady=(0, 12))
        self.uninstall_total_label = tk.Label(bottom_row, text="", font=FONT_BODY, bg=BG, fg=TEXT_DIM)
        self.uninstall_total_label.pack(side="left")
        ttk.Button(bottom_row, text="Refresh", style="Ghost.TButton", command=self._refresh_uninstall_preview).pack(side="right")
        self.uninstall_button = ttk.Button(bottom_row, text="Remove Everything", style="Accent.TButton", command=self._confirm_uninstall)
        self.uninstall_button.pack(side="right", padx=(0, 10))

        log_card = Card(page)
        log_card.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        log_inner = tk.Frame(log_card, bg=PANEL_BG)
        log_inner.pack(fill="both", expand=True, padx=10, pady=10)
        self.uninstall_log_text = tk.Text(log_inner, state="disabled", wrap="word", font=FONT_MONO, bg="#0e0e10", fg="#c9c9ce", relief="flat", padx=8, pady=8, height=6)
        self.uninstall_log_text.pack(fill="both", expand=True)

    def _refresh_uninstall_preview(self) -> None:
        self.uninstall_total_label.config(text="Scanning...")
        self.uninstall_button.config(state="disabled")
        threading.Thread(target=self._scan_uninstall_targets, daemon=True).start()

    def _scan_uninstall_targets(self) -> None:
        avd_name = uninstall_cli.detect_avd_name()
        targets = uninstall_cli.collect_targets(avd_name)
        existing = [(p, uninstall_cli.dir_size(p)) for p in targets if p.exists()]
        self.after(0, self._apply_uninstall_preview, targets, existing)

    def _apply_uninstall_preview(self, targets: list[Path], existing: list[tuple[Path, int]]) -> None:
        self._uninstall_targets_cache = targets
        for row in self.uninstall_tree.get_children():
            self.uninstall_tree.delete(row)
        total = 0
        for path, size in existing:
            total += size
            if size >= 1e8:
                size_text = f"{size / 1e9:.2f} GB"
            elif size >= 1e3:
                size_text = f"{size / 1e6:.1f} MB"
            else:
                size_text = ""
            self.uninstall_tree.insert("", "end", values=(str(path), size_text))
        if not existing:
            self.uninstall_total_label.config(text="Nothing to remove -- this already looks like a clean slate.")
            self.uninstall_button.config(state="disabled")
        else:
            self.uninstall_total_label.config(text=f"~{total / 1e9:.2f} GB will be reclaimed.")
            self.uninstall_button.config(state="normal")

    def _confirm_uninstall(self) -> None:
        existing_count = sum(1 for p in self._uninstall_targets_cache if p.exists())
        if existing_count == 0:
            return
        proceed = messagebox.askyesno(
            "Remove everything?",
            f"This will permanently remove {existing_count} item(s) -- the Android VM, its SDK, "
            "your bridge config, the signing keystore, and the desktop shortcut.\n\n"
            "This cannot be undone. Continue?",
            icon="warning",
        )
        if not proceed:
            return
        self.uninstall_button.config(state="disabled")
        threading.Thread(target=self._run_uninstall, daemon=True).start()

    def _run_uninstall(self) -> None:
        writer = QueueWriter(self.uninstall_log_queue)
        old_stdout = sys.stdout
        sys.stdout = writer
        try:
            print("[uninstall] stopping the AVD and bridge (if running)...")
            uninstall_cli.stop_running_instance()
            print("[uninstall] removing...")
            reclaimed = 0
            for path in self._uninstall_targets_cache:
                if path.exists():
                    print(f"  removing {path}...")
                reclaimed += uninstall_cli.remove_path(path)
            print(f"\n=== Done -- reclaimed {reclaimed / 1e9:.1f} GB ===")
        except Exception:
            print(f"\n[uninstall] error:\n{traceback.format_exc()}")
        finally:
            sys.stdout = old_stdout
        self.after(0, self._on_uninstall_finished)

    def _on_uninstall_finished(self) -> None:
        self._refresh_uninstall_preview()

    def _poll_uninstall_log_queue(self) -> None:
        try:
            while True:
                text = self.uninstall_log_queue.get_nowait()
                self.uninstall_log_text.config(state="normal")
                self.uninstall_log_text.insert("end", text)
                self.uninstall_log_text.see("end")
                self.uninstall_log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_uninstall_log_queue)


if __name__ == "__main__":
    Manager().mainloop()
