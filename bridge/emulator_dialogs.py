"""
Two dialogs for working with config.json's "emulators" map, shared between
manager.py's Emulators settings page and onboarding_wizard.py's own
Emulator Mappings step -- factored out here instead of duplicated, since
both need exactly the same two dialogs and previously each kept its own
near-identical copy.

EmulatorDialog: add/edit one package-prefix -> emulator mapping.
RedirectorInstallDialog: builds and installs a stub app for every package
shared/emulator_defaults.py knows about, into the running AVD (see
installer/stub_apk.py for why a stub is needed at all).
"""

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, ttk

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.emulator_defaults import all_stub_packages
from shared.theme import ENTRY_KWARGS, FONT_BODY, FONT_MONO, PANEL_BG, TEXT, TEXT_DIM, QueueWriter

sys.path.insert(0, str(Path(__file__).parent.parent / "installer"))
import stub_apk


class EmulatorDialog(simpledialog.Dialog):
    """Add/edit a single package-prefix -> emulator mapping."""

    def __init__(self, parent, title, prefix="", exe_names="", pre_args=""):
        self.initial_prefix = prefix
        self.initial_exe_names = exe_names
        self.initial_pre_args = pre_args
        self.result_values = None
        super().__init__(parent, title)

    def body(self, master):
        self.configure(bg=PANEL_BG)
        master.configure(bg=PANEL_BG)

        tk.Label(master, text="Android package prefix (e.g. com.github.stenzek.duckstation)", bg=PANEL_BG, fg=TEXT, font=FONT_BODY).grid(
            row=0, column=0, sticky="w", padx=4, pady=(4, 0)
        )
        self.prefix_entry = tk.Entry(master, width=50, **ENTRY_KWARGS)
        self.prefix_entry.insert(0, self.initial_prefix)
        self.prefix_entry.grid(row=1, column=0, padx=4, pady=(0, 8))

        tk.Label(master, text="Executable name(s), comma-separated (e.g. retroarch.exe)", bg=PANEL_BG, fg=TEXT, font=FONT_BODY).grid(
            row=2, column=0, sticky="w", padx=4
        )
        self.exe_entry = tk.Entry(master, width=50, **ENTRY_KWARGS)
        self.exe_entry.insert(0, self.initial_exe_names)
        self.exe_entry.grid(row=3, column=0, padx=4, pady=(0, 8))

        tk.Label(master, text="Launch flags, comma-separated (e.g. -fullscreen)", bg=PANEL_BG, fg=TEXT, font=FONT_BODY).grid(
            row=4, column=0, sticky="w", padx=4
        )
        self.args_entry = tk.Entry(master, width=50, **ENTRY_KWARGS)
        self.args_entry.insert(0, self.initial_pre_args)
        self.args_entry.grid(row=5, column=0, padx=4, pady=(0, 4))

        return self.prefix_entry

    def buttonbox(self):
        box = tk.Frame(self, bg=PANEL_BG)
        ttk.Button(box, text="OK", style="Accent.TButton", command=self.ok).pack(side="left", padx=5, pady=8)
        ttk.Button(box, text="Cancel", style="Ghost.TButton", command=self.cancel).pack(side="left", padx=5, pady=8)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

    def apply(self):
        prefix = self.prefix_entry.get().strip()
        exe_names = [s.strip() for s in self.exe_entry.get().split(",") if s.strip()]
        pre_args = [s.strip() for s in self.args_entry.get().split(",") if s.strip()]
        self.result_values = (prefix, exe_names, pre_args)


class RedirectorInstallDialog(tk.Toplevel):
    """Builds and installs a stub app for each of shared/emulator_defaults
    .py's known packages into the running AVD, so iiSU's own
    installed-package check resolves each console to something our
    patched LaunchBridge recognizes (see that module's docstring for why
    a stub is needed at all). The AVD needs to already be running --
    Start it from Home first if this can't reach it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Install Redirector Apps")
        self.configure(bg=PANEL_BG)
        self.geometry("560x420")
        self.transient(parent)

        self.log_queue: queue.Queue = queue.Queue()
        self.running = False

        tk.Label(
            self, text="Installs a placeholder app for each of the emulators below into the\n"
            "running AVD, purely so iiSU recognizes that console's emulator as\n"
            "installed. The real launch is still handled by the PC-side emulator\n"
            "configured in the Emulators tab -- these apps do nothing themselves.",
            bg=PANEL_BG, fg=TEXT_DIM, font=FONT_BODY, justify="left",
        ).pack(anchor="w", padx=16, pady=(16, 8))

        self.replace_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self, text="Replace apps already installed under these package names\n(only do this if you're sure it's an old redirector, not a real app)",
            variable=self.replace_var,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.log_text = tk.Text(self, state="disabled", wrap="word", font=FONT_MONO, bg="#0e0e10", fg="#c9c9ce", relief="flat", padx=8, pady=8, height=12)
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        button_row = tk.Frame(self, bg=PANEL_BG)
        button_row.pack(fill="x", padx=16, pady=(0, 16))
        self.install_button = ttk.Button(button_row, text="Install All", style="Accent.TButton", command=self._start_install)
        self.install_button.pack(side="left")
        ttk.Button(button_row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="left", padx=(8, 0))

        self.after(100, self._poll_log_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _start_install(self) -> None:
        if self.running:
            return
        self.running = True
        self.install_button.config(state="disabled")
        threading.Thread(target=self._run_installs, args=(self.replace_var.get(),), daemon=True).start()

    def _run_installs(self, replace_existing: bool) -> None:
        writer = QueueWriter(self.log_queue)
        devices = subprocess.run(["adb", "devices"], capture_output=True, text=True)
        if not any(line.startswith("emulator-") and "device" in line for line in devices.stdout.splitlines()):
            writer.write("No running AVD found -- start it from Home first, then try again.\n")
            self.after(0, self._on_finished)
            return

        for package, label in all_stub_packages():
            writer.write(f"{label} ({package})... ")
            try:
                outcome = stub_apk.build_and_install(package, label, replace_existing=replace_existing)
                writer.write(f"{outcome}\n")
            except Exception as e:
                writer.write(f"failed ({e})\n")
        writer.write("\nDone.\n")
        self.after(0, self._on_finished)

    def _on_finished(self) -> None:
        self.running = False
        self.install_button.config(state="normal")
