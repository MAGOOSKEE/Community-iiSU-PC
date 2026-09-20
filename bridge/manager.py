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
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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

sys.path.insert(0, str(PROJECT_ROOT))
from shared import theme
from shared.avatars import fetch_avatar_bytes, make_circular_photo, make_placeholder_circle
from shared.emulator_defaults import build_emulators_map, describe_profile
from shared.theme import (
    BG, ENTRY_KWARGS, GRADIENT_STOPS, GRAY, GREEN, LISTBOX_KWARGS, PANEL_BG, PANEL_BG_HOVER,
    RED, TEXT, TEXT_DIM, FONT_BODY, FONT_HEADING, FONT_MONO, FONT_TITLE, Card, QueueWriter, draw_gradient_bar,
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
    ("display", "\U0001F5A5", "Display"),
    ("advanced", "⚙", "Advanced"),
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
        ttk.Button(self.save_bar, text="Save", style="Accent.TButton", command=self._save_settings).pack(side="right", padx=24, pady=14)
        self.save_status_label = tk.Label(self.save_bar, text="", bg=BG, fg=GREEN, font=FONT_BODY)
        self.save_status_label.pack(side="left", padx=24, pady=14)

        self._build_home_page()
        self._build_settings_pages()
        self._build_credits_page()
        self._build_uninstall_page()

    def _build_sidebar(self) -> None:
        header = tk.Frame(self.sidebar, bg=PANEL_BG)
        header.pack(fill="x", pady=(16, 10))
        hamburger = tk.Label(header, text="☰", font=("Segoe UI", 15), bg=PANEL_BG, fg=TEXT, cursor="hand2")
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

        ttk.Button(body, text="Apply now (saves + cold-boots the AVD)", style="Accent.TButton", command=self._apply_display).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=24, pady=(8, 0)
        )
        self.display_status_label = tk.Label(body, text="", bg=BG, fg=GRADIENT_STOPS[2], font=FONT_BODY)
        self.display_status_label.grid(row=8, column=0, columnspan=2, sticky="w", padx=24, pady=(8, 16))

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

    def _apply_display(self) -> None:
        self._save_settings()
        self.display_status_label.config(text="Cold-booting the AVD, this takes a minute...")
        self.update_idletasks()
        subprocess.Popen([sys.executable, str(BRIDGE_DIR / "apply_display.py")], cwd=str(BRIDGE_DIR))

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
        self.save_status_label.config(text=f"Saved to {CONFIG_PATH.name}")
        self.after(3000, lambda: self.save_status_label.config(text=""))

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
