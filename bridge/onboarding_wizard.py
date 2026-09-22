"""
Step-by-step first-run onboarding: ROM directory, emulator search folders,
display, and hotkeys, walked through one screen at a time with live
feedback -- instead of dropping a fresh install straight into manager.py's
settings pages, meant for occasional later editing (still reachable
afterward from its sidebar).

Launched automatically by installer/setup_gui.py right after setup
finishes. Shares its emulator-mapping dialogs with manager.py
(emulator_dialogs.py) and its data helpers (console-folder recognition,
monitor detection, executable search) with the same modules manager.py
uses, but keeps its own simpler step-flow UI -- a wizard is a different
shape of problem than a settings page.

Stdlib only (tkinter), no extra installs.
"""

import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import winapi
from bridge_config import CONFIG_PATH, ConfigMissingError, load_config
from console_names import load_console_lookup, resolve_console_shortname
from emulator_dialogs import EmulatorDialog
from launch_bridge import find_executable

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import theme
from shared.theme import (
    BG, ENTRY_KWARGS, GRADIENT_STOPS, GREEN, LISTBOX_KWARGS, PANEL_BG, PANEL_BG_HOVER,
    RED, TEXT, TEXT_DIM, FONT_BODY, FONT_HEADING, FONT_TITLE, Card, draw_gradient_bar,
)
from shared.emulator_defaults import all_emulator_exe_names, describe_profile

RESOLUTION_PRESETS = ["1280 x 720", "1600 x 900", "1920 x 1080", "2560 x 1440", "3840 x 2160"]
REFRESH_RATE_PRESETS = ["60", "90", "120", "144", "165", "240"]
MODIFIER_NAMES = ["ctrl", "alt", "shift", "win"]

STEP_TITLES = ["Welcome", "ROM Directory", "Emulator Folders", "Emulator Mappings", "Display", "Hotkeys", "Finish"]


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def read_avd_display(avd_name: str) -> dict:
    """The ground truth for whether display settings actually need
    (re)applying is the AVD's own config.ini, not config.json's "display"
    block -- that block gets written from a generic template default
    regardless of whether it was ever really applied to the VM's hardware
    profile (see apply_display.py). Returns {} if it can't be read, which
    callers should treat as "assume changed" rather than "assume matches"."""
    import apply_display

    config_ini = apply_display.avd_config_path(avd_name)
    if not config_ini.is_file():
        return {}
    values = {}
    key_map = {
        "hw.lcd.width": "width",
        "hw.lcd.height": "height",
        "hw.lcd.density": "density",
        "hw.lcd.vsync": "refresh_rate",
    }
    for line in config_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in key_map:
            values[key_map[key]] = value.strip()
    return values


class OnboardingWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Welcome to Community-iiSU-PC")
        # Tall enough that the Emulator Mappings step (title + description +
        # a 9-row treeview + button row, the tallest of any step at ~420px
        # of actual content) fits with room to spare -- measured against
        # actual rendered content rather than guessed, since a too-short
        # window doesn't just clip content, it squeezes the nav bar at the
        # bottom and cuts off half of the Back/Next buttons.
        self.geometry("760x760")
        # Matching minsize to the default height means the window can only
        # be resized *up* from here, never back down into clipping range.
        self.minsize(700, 760)
        self.configure(bg=BG)
        # Without this, a step with enough content (e.g. 10 scan-result
        # lines) makes the window silently grow to fit it, shifting the
        # Back/Next buttons between steps instead of keeping them anchored
        # at a fixed spot -- pinning propagation off keeps the window at
        # whatever size geometry()/the user's own resize set, and clips
        # overflow instead.
        self.pack_propagate(False)

        try:
            self.config_data = load_config()
        except ConfigMissingError as e:
            self.withdraw()
            messagebox.showerror("Config not found", str(e))
            sys.exit(1)

        self.step_index = 0
        self.running_bg_task = False
        self.scan_results: list[tuple[str, bool]] | None = None

        self._init_state()
        self._configure_style()
        self._build_chrome()
        self._show_step(0)

    # -- State -------------------------------------------------

    def _init_state(self) -> None:
        roms_dir = self.config_data.get("roms_dir", "")
        self.roms_dir_var = tk.StringVar(value="" if "CHANGE-ME" in roms_dir else roms_dir)

        self.search_roots = [r for r in self.config_data.get("search_roots", []) if "CHANGE-ME" not in r]
        self.emulators = dict(self.config_data.get("emulators", {}))

        display = self.config_data.get("display", {"width": 1920, "height": 1080, "density": 240, "refresh_rate": 60})
        self.display_width_var = tk.StringVar(value=str(display.get("width", 1920)))
        self.display_height_var = tk.StringVar(value=str(display.get("height", 1080)))
        self.display_density_var = tk.StringVar(value=str(display.get("density", 240)))
        self.display_refresh_var = tk.StringVar(value=str(display.get("refresh_rate", 60)))
        self.iisu_fullscreen_var = tk.BooleanVar(value=self.config_data.get("iisu_fullscreen", True))
        self.resolution_preset_var = tk.StringVar()
        self._autodetected_display = False
        self.original_avd_display = read_avd_display(self.config_data.get("avd_name", "iisuwin"))

        quit_initial = self.config_data.get("quit_hotkey") or {"modifiers": ["ctrl", "alt"], "key": "q"}
        # shutdown_hotkey defaults to None (no separate combo -- holding
        # quit_hotkey already covers full shutdown), not a missing key, so
        # .get()'s own default never kicks in for a fresh config and this
        # would otherwise crash on the None.get() below.
        shutdown_initial = self.config_data.get("shutdown_hotkey") or {"modifiers": ["ctrl", "alt"], "key": "x"}
        self.quit_mod_vars = {name: tk.BooleanVar(value=name in {m.lower() for m in quit_initial.get("modifiers", [])}) for name in MODIFIER_NAMES}
        self.quit_key_var = tk.StringVar(value=quit_initial.get("key", "q"))
        self.shutdown_mod_vars = {name: tk.BooleanVar(value=name in {m.lower() for m in shutdown_initial.get("modifiers", [])}) for name in MODIFIER_NAMES}
        self.shutdown_key_var = tk.StringVar(value=shutdown_initial.get("key", "x"))

    # -- Style / chrome -------------------------------------------------

    def _configure_style(self) -> None:
        theme.apply_ttk_styles(ttk.Style(self))

    def _build_chrome(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 6))
        self.title_label = tk.Label(header, text="", font=FONT_TITLE, bg=BG, fg=TEXT)
        self.title_label.pack(anchor="w")
        self.step_label = tk.Label(header, text="", font=FONT_BODY, bg=BG, fg=TEXT_DIM)
        self.step_label.pack(anchor="w", pady=(2, 0))

        gradient = tk.Canvas(self, height=3, bg=BG, highlightthickness=0)
        gradient.pack(fill="x", padx=24, pady=(10, 0))
        self.after(10, lambda: draw_gradient_bar(gradient, gradient.winfo_width() or 720, 3))
        self.bind("<Configure>", lambda e: draw_gradient_bar(gradient, gradient.winfo_width(), 3))

        self.progress = ttk.Progressbar(
            self, mode="determinate", style="Dark.Horizontal.TProgressbar", maximum=len(STEP_TITLES) - 1
        )
        self.progress.pack(fill="x", padx=24, pady=(10, 16))

        self.content_card = Card(self)
        self.content_card.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        self.content = tk.Frame(self.content_card, bg=PANEL_BG)
        self.content.pack(fill="both", expand=True, padx=20, pady=20)

        self.hint_label = tk.Label(self, text="", font=FONT_BODY, bg=BG, fg=RED, anchor="w", wraplength=700, justify="left")
        self.hint_label.pack(fill="x", padx=24)

        nav = tk.Frame(self, bg=BG)
        nav.pack(fill="x", padx=24, pady=(6, 20))
        self.back_button = ttk.Button(nav, text="Back", style="Ghost.TButton", command=self._go_back)
        self.back_button.pack(side="left")
        self.next_button = ttk.Button(nav, text="Next", style="Accent.TButton", command=self._go_next)
        self.next_button.pack(side="right")

    # -- Step machinery -------------------------------------------------

    def _step_builders(self) -> dict:
        return {
            0: self._build_welcome,
            1: self._build_roms,
            2: self._build_emulator_folders,
            3: self._build_emulator_mappings,
            4: self._build_display,
            5: self._build_hotkeys,
            6: self._build_finish,
        }

    def _validators(self) -> dict:
        return {1: self._validate_roms}

    def _capture_current_step(self) -> None:
        """Copies live widget state for whatever step is on screen back
        into the persistent fields that survive rebuilding self.content --
        only the search-folders Listbox and the emulator-mappings Treeview
        need this (every other step's widgets are bound directly to tk
        Variables created once in _init_state, which survive on their own)."""
        if self.step_index == 2 and getattr(self, "search_roots_list", None) is not None:
            self.search_roots = list(self.search_roots_list.get(0, "end"))
        elif self.step_index == 3 and getattr(self, "emulators_tree", None) is not None:
            self._capture_emulator_mappings()

    def _show_step(self, index: int) -> None:
        self.step_index = index
        for child in self.content.winfo_children():
            child.destroy()
        self.hint_label.config(text="")
        self.progress["value"] = index

        is_welcome = index == 0
        is_last = index == len(STEP_TITLES) - 1
        self.title_label.config(text="Welcome to Community-iiSU-PC" if is_welcome else STEP_TITLES[index])
        self.step_label.config(text="" if is_welcome or is_last else f"Step {index} of {len(STEP_TITLES) - 2}")

        self._step_builders()[index]()

        self.back_button.config(state="normal" if index > 0 else "disabled")
        self.next_button.config(
            text="Finish" if is_last else ("Get Started" if is_welcome else "Next"),
            command=self._finish if is_last else self._go_next,
            state="normal",
        )

    def _go_next(self) -> None:
        if self.running_bg_task:
            return
        self._capture_current_step()
        validator = self._validators().get(self.step_index)
        if validator:
            ok, message = validator()
            if not ok:
                self.hint_label.config(text=message)
                return
        self._show_step(self.step_index + 1)

    def _go_back(self) -> None:
        if self.running_bg_task:
            return
        self._capture_current_step()
        self._show_step(self.step_index - 1)

    def _set_nav_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.back_button.config(state=state if self.step_index > 0 else "disabled")
        self.next_button.config(state=state)

    # -- Step 0: Welcome -------------------------------------------------

    def _build_welcome(self) -> None:
        tk.Label(self.content, text="Let's get Community-iiSU-PC set up.", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            self.content,
            text="A few quick questions and you'll be ready to play, no manual\nconfig.json editing needed afterward.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(6, 18))

        for line in [
            "Where your ROMs live",
            "Where your PC emulators are installed",
            "What resolution the VM should run at",
            "Your quit / shutdown hotkeys",
        ]:
            row = tk.Frame(self.content, bg=PANEL_BG)
            row.pack(anchor="w", pady=3)
            tk.Label(row, text="•", font=FONT_BODY, bg=PANEL_BG, fg=GRADIENT_STOPS[2]).pack(side="left", padx=(0, 8))
            tk.Label(row, text=line, font=FONT_BODY, bg=PANEL_BG, fg=TEXT).pack(side="left")

        tk.Label(
            self.content,
            text="Takes under a minute. Anything here can be changed again later from\nthe control panel's Configure button.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(18, 0))

    # -- Step 1: ROM directory -------------------------------------------------

    def _build_roms(self) -> None:
        tk.Label(self.content, text="Where are your ROMs?", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            self.content,
            text="Pick the root folder that contains one subfolder per console (e.g. psx/, snes/, gc/).",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left", wraplength=660,
        ).pack(anchor="w", pady=(4, 14))

        row = tk.Frame(self.content, bg=PANEL_BG)
        row.pack(fill="x")
        entry = tk.Entry(row, textvariable=self.roms_dir_var, **ENTRY_KWARGS)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<FocusOut>", lambda e: self._refresh_roms_status())
        ttk.Button(row, text="Browse...", style="Ghost.TButton", command=self._browse_roms_dir).pack(side="left", padx=(8, 0))

        self.roms_status_label = tk.Label(self.content, text="", font=FONT_BODY, bg=PANEL_BG, justify="left", wraplength=660, anchor="w")
        self.roms_status_label.pack(anchor="w", fill="x", pady=(12, 0))
        self._refresh_roms_status()

    def _browse_roms_dir(self) -> None:
        path = filedialog.askdirectory(title="Select root ROM folder")
        if path:
            self.roms_dir_var.set(path)
            self._refresh_roms_status()

    def _refresh_roms_status(self) -> None:
        raw = self.roms_dir_var.get().strip()
        if not raw:
            self.roms_status_label.config(text="", fg=TEXT_DIM)
            return
        path = Path(raw)
        if not path.is_dir():
            self.roms_status_label.config(text="This folder doesn't exist yet. Create it or pick a different one.", fg=RED)
            return

        exact, by_compact = load_console_lookup()
        recognized, unrecognized = [], []
        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            (recognized if resolve_console_shortname(child.name, exact, by_compact) else unrecognized).append(child.name)

        if not recognized and not unrecognized:
            self.roms_status_label.config(text="This folder is empty, that's fine, add ROMs to it anytime.", fg=TEXT_DIM)
        elif not unrecognized:
            self.roms_status_label.config(
                text=f"✓ iiSU will recognize all {len(recognized)} folder(s): {', '.join(recognized)}", fg=GREEN
            )
        else:
            prefix = f"✓ {len(recognized)} recognized, " if recognized else ""
            self.roms_status_label.config(
                text=f"{prefix}✗ {len(unrecognized)} won't be seen by iiSU (rename these): {', '.join(unrecognized)}",
                fg=RED,
            )

    def _validate_roms(self) -> tuple[bool, str]:
        raw = self.roms_dir_var.get().strip()
        if not raw:
            return False, "Pick a ROM folder to continue."
        if not Path(raw).is_dir():
            return False, "That folder doesn't exist yet -- create it or pick a different one."
        return True, ""

    # -- Step 2: Emulator folders -------------------------------------------------

    def _build_emulator_folders(self) -> None:
        tk.Label(self.content, text="Where are your PC emulators installed?", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            self.content,
            text="Community-iiSU-PC searches these folders for the emulators it knows about\n"
            "(DuckStation, Dolphin, RetroArch, and more) -- install those yourself\n"
            "first if you haven't already, this project doesn't bundle them.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(4, 14))

        self.search_roots_list = tk.Listbox(self.content, height=5, font=FONT_BODY, **LISTBOX_KWARGS)
        self.search_roots_list.pack(fill="x")
        for root in self.search_roots:
            self.search_roots_list.insert("end", root)

        btn_row = tk.Frame(self.content, bg=PANEL_BG)
        btn_row.pack(fill="x", pady=(8, 16))
        ttk.Button(btn_row, text="Add folder...", style="Ghost.TButton", command=self._add_search_root).pack(side="left")
        ttk.Button(btn_row, text="Remove selected", style="Ghost.TButton", command=self._remove_search_root).pack(side="left", padx=(8, 0))
        self.scan_button = ttk.Button(btn_row, text="Scan for installed emulators", style="Ghost.TButton", command=self._start_emulator_scan)
        self.scan_button.pack(side="left", padx=(8, 0))

        self.scan_status_label = tk.Label(self.content, text="", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left", wraplength=660, anchor="w")
        self.scan_status_label.pack(anchor="w", fill="x")
        if self.scan_results is not None:
            self._render_scan_results()

    def _add_search_root(self) -> None:
        path = filedialog.askdirectory(title="Select a folder to search for emulators")
        if path:
            self.search_roots_list.insert("end", path)

    def _remove_search_root(self) -> None:
        for index in reversed(self.search_roots_list.curselection()):
            self.search_roots_list.delete(index)

    def _start_emulator_scan(self) -> None:
        if self.running_bg_task:
            return
        roots = list(self.search_roots_list.get(0, "end"))
        if not roots:
            self.scan_status_label.config(text="Add at least one folder above to scan.", fg=RED)
            return
        self.running_bg_task = True
        self._set_nav_enabled(False)
        self.scan_button.config(state="disabled")
        self.scan_status_label.config(text="Scanning. This can take a few seconds for large folders like Program Files...", fg=TEXT_DIM)
        threading.Thread(target=self._run_emulator_scan, args=(roots,), daemon=True).start()

    def _run_emulator_scan(self, roots: list[str]) -> None:
        search_paths = [Path(r) for r in roots]
        cache: dict = {"executables": {}}
        results = []
        for label, exe_names in all_emulator_exe_names():
            found = find_executable(exe_names, search_paths, cache) is not None
            results.append((label, found))
        self.after(0, self._on_scan_finished, results)

    def _on_scan_finished(self, results: list[tuple[str, bool]]) -> None:
        self.running_bg_task = False
        self._set_nav_enabled(True)
        self.scan_button.config(state="normal")
        self.scan_results = results
        self._render_scan_results()

    def _render_scan_results(self) -> None:
        found_labels = [label for label, ok in self.scan_results if ok]
        missing_labels = [label for label, ok in self.scan_results if not ok]
        total = len(self.scan_results)

        lines = [f"Found {len(found_labels)} of {total} known emulators: {', '.join(found_labels) or '(none yet)'}"]
        if missing_labels:
            lines.append(f"Not found yet: {', '.join(missing_labels)} -- install any of these and Community-iiSU-PC will pick them up automatically.")
        self.scan_status_label.config(text="\n".join(lines), fg=GREEN if found_labels else TEXT_DIM)

    # -- Step 3: Emulator mappings -------------------------------------------------

    def _build_emulator_mappings(self) -> None:
        tk.Label(self.content, text="Emulator mappings", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            self.content,
            text="Maps each console's Android package to the real PC emulator that runs\n"
            "it. The defaults above already cover most installs -- edit here only if\n"
            "you're using an unusual fork with a different executable name (e.g. a\n"
            "build of Azahar that ships as azahar.exe instead of citra-qt.exe).",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(4, 12))

        columns = ("prefix", "exe_names", "pre_args")
        self.emulators_tree = ttk.Treeview(self.content, columns=columns, show="headings", height=9)
        self.emulators_tree.heading("prefix", text="Package prefix")
        self.emulators_tree.heading("exe_names", text="Executable name(s)")
        self.emulators_tree.heading("pre_args", text="Launch flags")
        self.emulators_tree.column("prefix", width=230)
        self.emulators_tree.column("exe_names", width=210)
        self.emulators_tree.column("pre_args", width=140)
        self.emulators_tree.pack(fill="both", expand=True)

        for prefix, profile in self.emulators.items():
            exe_display, pre_args_display = describe_profile(profile)
            self.emulators_tree.insert("", "end", iid=prefix, values=(prefix, exe_display, pre_args_display))

        btn_row = tk.Frame(self.content, bg=PANEL_BG)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="Add...", style="Ghost.TButton", command=self._add_emulator_mapping).pack(side="left")
        ttk.Button(btn_row, text="Edit selected...", style="Ghost.TButton", command=self._edit_emulator_mapping).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Remove selected", style="Ghost.TButton", command=self._remove_emulator_mapping).pack(side="left", padx=(8, 0))

    def _add_emulator_mapping(self) -> None:
        dialog = EmulatorDialog(self, "Add emulator mapping")
        if dialog.result_values:
            prefix, exe_names, pre_args = dialog.result_values
            if not prefix:
                return
            if self.emulators_tree.exists(prefix):
                messagebox.showerror("Duplicate", f"A mapping for '{prefix}' already exists.")
                return
            self.emulators_tree.insert("", "end", iid=prefix, values=(prefix, ", ".join(exe_names), ", ".join(pre_args)))

    def _edit_emulator_mapping(self) -> None:
        selected = self.emulators_tree.selection()
        if not selected:
            return
        prefix = selected[0]
        if "by_extension" in self.emulators.get(prefix, {}):
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

    def _remove_emulator_mapping(self) -> None:
        for item in self.emulators_tree.selection():
            self.emulators_tree.delete(item)

    def _capture_emulator_mappings(self) -> None:
        original = self.emulators
        emulators = {}
        for item in self.emulators_tree.get_children():
            prefix, exe_names_str, pre_args_str = self.emulators_tree.item(item, "values")
            # "by_extension" entries (RetroArch, which maps a different real
            # PC emulator per ROM extension rather than one fixed exe) show
            # a human-readable summary in these columns (see describe_profile),
            # not the real underlying data -- always keep the original entry
            # verbatim rather than reconstructing it from that summary text.
            # The edit dialog already refuses to open on these, so the only
            # way one of these rows changes at all is via Remove.
            original_entry = original.get(prefix, {})
            if "by_extension" in original_entry:
                emulators[prefix] = original_entry
                continue
            emulators[prefix] = {
                "exe_names": [s.strip() for s in exe_names_str.split(",") if s.strip()],
                "pre_args": [s.strip() for s in pre_args_str.split(",") if s.strip()],
            }
        self.emulators = emulators

    # -- Step 4: Display -------------------------------------------------

    def _build_display(self) -> None:
        tk.Label(self.content, text="What resolution should the VM run at?", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            self.content,
            text="iiSU's default is a portrait phone screen, this switches it to a\nreal desktop-shaped display. Pre-filled from your primary monitor.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(4, 14))

        if not self._autodetected_display:
            self._autodetect_display(silent=True)
            self._autodetected_display = True

        columns = tk.Frame(self.content, bg=PANEL_BG)
        columns.pack(fill="x")
        left = tk.Frame(columns, bg=PANEL_BG)
        left.pack(side="left", anchor="n")
        right = tk.Frame(columns, bg=PANEL_BG)
        right.pack(side="left", anchor="n", padx=(28, 0))

        preset_row = tk.Frame(left, bg=PANEL_BG)
        preset_row.pack(anchor="w", pady=(0, 8))
        tk.Label(preset_row, text="Resolution:", bg=PANEL_BG, fg=TEXT, font=FONT_BODY).pack(side="left")
        resolution_combo = ttk.Combobox(
            preset_row, textvariable=self.resolution_preset_var, values=RESOLUTION_PRESETS,
            state="readonly", width=14, font=FONT_BODY,
        )
        resolution_combo.pack(side="left", padx=(8, 0))
        resolution_combo.bind("<<ComboboxSelected>>", self._apply_resolution_preset)

        exact_row = tk.Frame(left, bg=PANEL_BG)
        exact_row.pack(anchor="w", pady=(0, 8))
        tk.Label(exact_row, text="or exactly:", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY).pack(side="left")
        tk.Entry(exact_row, textvariable=self.display_width_var, width=6, **ENTRY_KWARGS).pack(side="left", padx=(8, 0))
        tk.Label(exact_row, text="x", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY).pack(side="left", padx=4)
        tk.Entry(exact_row, textvariable=self.display_height_var, width=6, **ENTRY_KWARGS).pack(side="left")

        refresh_row = tk.Frame(left, bg=PANEL_BG)
        refresh_row.pack(anchor="w", pady=(0, 8))
        tk.Label(refresh_row, text="Refresh rate:", bg=PANEL_BG, fg=TEXT, font=FONT_BODY).pack(side="left")
        tk.Entry(refresh_row, textvariable=self.display_refresh_var, width=6, **ENTRY_KWARGS).pack(side="left", padx=(8, 0))
        refresh_combo = ttk.Combobox(refresh_row, values=REFRESH_RATE_PRESETS, state="readonly", width=5, font=FONT_BODY)
        refresh_combo.pack(side="left", padx=(6, 0))
        refresh_combo.bind("<<ComboboxSelected>>", lambda e: self.display_refresh_var.set(refresh_combo.get()))
        tk.Label(refresh_row, text="Hz", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY).pack(side="left", padx=(4, 0))

        ttk.Button(left, text="Re-detect from primary monitor", style="Ghost.TButton", command=lambda: self._autodetect_display(silent=False)).pack(anchor="w", pady=(4, 0))

        tk.Label(right, text="Preview", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY).pack(anchor="w")
        self.aspect_canvas = tk.Canvas(right, width=160, height=100, bg="#0e0e10", highlightthickness=0)
        self.aspect_canvas.pack()
        self.display_width_var.trace_add("write", self._redraw_aspect_preview)
        self.display_height_var.trace_add("write", self._redraw_aspect_preview)
        self._redraw_aspect_preview()

        ttk.Checkbutton(
            self.content, text="Maximize the iiSU/AVD window automatically", variable=self.iisu_fullscreen_var
        ).pack(anchor="w", pady=(18, 0))
        tk.Label(
            self.content,
            text="Only affects iiSU's own UI inside the VM. Actual gameplay runs in a\n"
            "separate native emulator window at your monitor's real resolution already.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(10, 0))

    def _apply_resolution_preset(self, event=None) -> None:
        choice = self.resolution_preset_var.get()
        if "x" not in choice:
            return
        width, height = (part.strip() for part in choice.split("x"))
        self.display_width_var.set(width)
        self.display_height_var.set(height)

    def _autodetect_display(self, silent: bool) -> None:
        try:
            width, height, hz = winapi.get_primary_monitor_mode()
        except Exception as e:
            if not silent:
                messagebox.showerror("Couldn't detect monitor", str(e))
            return
        self.display_width_var.set(str(width))
        self.display_height_var.set(str(height))
        self.display_refresh_var.set(str(hz))

    def _redraw_aspect_preview(self, *_args) -> None:
        canvas = self.aspect_canvas
        try:
            canvas.delete("all")
        except tk.TclError:
            return  # a trace left over from a previous visit to this step
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

    # -- Step 5: Hotkeys -------------------------------------------------

    def _build_hotkeys(self) -> None:
        tk.Label(self.content, text="Hotkeys", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            self.content, text="Used inside the VM to get back out of a game (both rebindable later too).",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM,
        ).pack(anchor="w", pady=(4, 4))

        self._build_one_hotkey_editor(self.content, "Quit to iiSU (force-quits the running game):", self.quit_mod_vars, self.quit_key_var)
        self._build_one_hotkey_editor(self.content, "Full shutdown (closes iiSU and the VM entirely):", self.shutdown_mod_vars, self.shutdown_key_var)

        tk.Label(
            self.content,
            text="Also works with a controller: press Select+Start together on any pad for the same\nquit action, no keyboard needed. Remappable later in Advanced.",
            font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(16, 0))

    def _build_one_hotkey_editor(self, parent, title: str, mod_vars: dict, key_var: tk.StringVar) -> None:
        tk.Label(parent, text=title, bg=PANEL_BG, fg=TEXT, font=FONT_BODY, justify="left", wraplength=660).pack(anchor="w", pady=(12, 4))
        mod_row = tk.Frame(parent, bg=PANEL_BG)
        mod_row.pack(anchor="w")
        for name in MODIFIER_NAMES:
            ttk.Checkbutton(mod_row, text=name.capitalize(), variable=mod_vars[name]).pack(side="left", padx=(0, 12))

        key_row = tk.Frame(parent, bg=PANEL_BG)
        key_row.pack(anchor="w", pady=(4, 0))
        tk.Label(key_row, text="+", bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY).pack(side="left", padx=(0, 8))
        tk.Label(key_row, textvariable=key_var, width=8, bg="#0e0e10", fg=TEXT, font=FONT_BODY, relief="flat", padx=8, pady=4).pack(side="left")
        capture_button = ttk.Button(key_row, text="Press a key...", style="Ghost.TButton")
        capture_button.configure(command=lambda: self._capture_key(key_var, capture_button))
        capture_button.pack(side="left", padx=(8, 0))

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

    @staticmethod
    def _describe_hotkey(mod_vars: dict, key_var: tk.StringVar) -> str:
        mods = [name.capitalize() for name, var in mod_vars.items() if var.get()]
        key = (key_var.get().strip() or "?").upper()
        return " + ".join(mods + [key]) if mods else key

    @staticmethod
    def _read_hotkey(mod_vars: dict, key_var: tk.StringVar, default_key: str) -> dict:
        return {
            "modifiers": [name for name, var in mod_vars.items() if var.get()],
            "key": key_var.get().strip() or default_key,
        }

    # -- Step 6: Finish -------------------------------------------------

    def _build_finish(self) -> None:
        tk.Label(self.content, text="Ready to go", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        tk.Label(self.content, text="Here's what's about to be saved:", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM).pack(anchor="w", pady=(4, 12))

        for line in self._summary_lines():
            tk.Label(
                self.content, text=f"•  {line}", font=FONT_BODY, bg=PANEL_BG, fg=TEXT, justify="left", wraplength=660, anchor="w"
            ).pack(anchor="w", pady=2)

        missing_warning = self._missing_emulators_warning()
        if missing_warning:
            tk.Label(
                self.content, text=missing_warning, font=FONT_BODY, bg=PANEL_BG, fg=RED, justify="left", wraplength=660, anchor="w"
            ).pack(anchor="w", pady=(10, 0))

        self.finish_status_label = tk.Label(self.content, text="", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, justify="left", wraplength=660, anchor="w")
        self.finish_status_label.pack(anchor="w", fill="x", pady=(16, 8))

    def _missing_emulators_warning(self) -> str:
        """Surfaced here, not just on the Emulator Folders step itself, so
        it's the last thing seen before saving rather than something only
        visible if you happen to scroll back -- a console mapped to an
        emulator that was never found here will silently fail to launch
        later with no obvious link back to this step."""
        if self.scan_results is None:
            return (
                "You haven't scanned for installed PC emulators yet -- go back to "
                "\"Emulator Folders\" and click \"Scan for installed emulators\" to confirm "
                "they'll actually be found before finishing."
            )
        missing = [label for label, ok in self.scan_results if not ok]
        if not missing:
            return ""
        return (
            f"Still not found: {', '.join(missing)} -- games mapped to these won't launch until "
            "they're installed and you rescan (back on \"Emulator Folders\")."
        )

    def _summary_lines(self) -> list[str]:
        display_note = " (will cold-boot the VM once to apply)" if self._display_changed() else " (already matches)"
        if self.scan_results is not None:
            found = sum(1 for _, ok in self.scan_results if ok)
            emulator_note = f" ({found}/{len(self.scan_results)} emulators found)"
        else:
            emulator_note = " (not scanned yet)"
        return [
            f"ROM folder: {self.roms_dir_var.get().strip() or '(not set)'}",
            f"Emulator search folders: {len(self.search_roots)}{emulator_note}",
            f"Emulator mappings: {len(self.emulators)} configured",
            f"Display: {self.display_width_var.get()}×{self.display_height_var.get()} @ {self.display_refresh_var.get()}Hz{display_note}",
            f"Quit hotkey: {self._describe_hotkey(self.quit_mod_vars, self.quit_key_var)}",
            f"Shutdown hotkey: {self._describe_hotkey(self.shutdown_mod_vars, self.shutdown_key_var)}",
            "Density, AVD name, and other rarely-touched settings stay as-is -- edit those later from Configure -> Advanced if you ever need to.",
        ]

    def _display_changed(self) -> bool:
        current = {
            "width": self.display_width_var.get().strip(),
            "height": self.display_height_var.get().strip(),
            "refresh_rate": self.display_refresh_var.get().strip(),
        }
        original = self.original_avd_display
        if not original:
            return True
        return any(current.get(k) != original.get(k) for k in current)

    def _build_final_config(self) -> dict:
        try:
            display = {
                "width": int(self.display_width_var.get()),
                "height": int(self.display_height_var.get()),
                "density": int(self.display_density_var.get()),
                "refresh_rate": int(self.display_refresh_var.get()),
            }
        except ValueError:
            display = self.config_data.get("display", {})

        config = dict(self.config_data)
        config["roms_dir"] = self.roms_dir_var.get().strip()
        config["search_roots"] = self.search_roots
        config["emulators"] = self.emulators
        config["display"] = display
        config["iisu_fullscreen"] = self.iisu_fullscreen_var.get()
        config["quit_hotkey"] = self._read_hotkey(self.quit_mod_vars, self.quit_key_var, default_key="q")
        config["shutdown_hotkey"] = self._read_hotkey(self.shutdown_mod_vars, self.shutdown_key_var, default_key="x")
        return config

    def _finish(self) -> None:
        ok, message = self._validate_roms()
        if not ok:
            self._show_step(1)
            self.hint_label.config(text=message)
            return

        config = self._build_final_config()
        save_config(config)
        self.config_data = config

        self.back_button.config(state="disabled")
        self.next_button.config(state="disabled")

        # Writes straight into the AVD's own config.ini (no boot needed --
        # see _write_avd_display_profile) rather than the old behavior of
        # cold-booting the VM right here to "apply" it: this AVD always
        # cold-boots on its very first real start regardless (-no-snapshot,
        # never resumed), so the setting is already going to be in effect
        # the moment the person starts Community-iiSU-PC themselves -- proving it
        # here first, unprompted, just meant onboarding finished by
        # dropping whoever just set this up straight into a live session
        # instead of letting them start it deliberately, whenever they're
        # actually ready.
        if self._display_changed():
            self._write_avd_display_profile(config)

        self.finish_status_label.config(
            text=(
                "Saved. You're all set -- close this and start Community-iiSU-PC yourself whenever you're "
                "ready (the desktop shortcut, or Manager's Home page). Two things worth double-"
                "checking before you do: your real ROM library actually needs to be reachable from "
                "inside the VM at the folder you mapped (this only points at it, nothing gets copied "
                "in), and any standalone emulators your console mappings rely on need to actually be "
                "found -- rerun the scan on \"Emulator Folders\" if you're not sure."
            ),
            fg=GREEN,
        )
        self.next_button.config(text="Close", command=self.destroy, state="normal")

    def _write_avd_display_profile(self, config: dict) -> None:
        """Writes the chosen resolution/density straight into the AVD's own
        config.ini -- the actual hardware-profile file emulator.exe reads
        at boot -- without booting anything to do it. Best-effort: a
        failure here just leaves the AVD on its previous profile until
        manager.py's Display page is used later, never worth blocking
        onboarding's own completion over."""
        import apply_display

        config_ini = apply_display.avd_config_path(config.get("avd_name", "iisuwin"))
        if not config_ini.is_file():
            return
        try:
            apply_display.update_config_ini(config_ini, config["display"])
        except OSError as e:
            print(f"[onboarding] couldn't write the AVD's display profile ({e}) -- it'll get applied next time Display settings are saved")


if __name__ == "__main__":
    OnboardingWizard().mainloop()
