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
import atexit
import zipfile
import socket
import os
import queue
import re
import subprocess
import tempfile
import sys
import threading
import traceback
from datetime import datetime
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
