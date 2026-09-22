"""
GUI front-end for setup_wizard.py.

Lets you pick your iiSU APK, then runs the whole first-time setup (SDK/AVD
bootstrap, patching, install) on a background thread while streaming its
progress into a log view. All the actual work lives in setup_wizard.py /
sdk_bootstrap.py / patch_iisu.py -- this is purely a front end for it.

Uses shared/theme.py so this and bridge/manager.py look like one
application instead of two different tools bolted together -- this stays
its own separate window rather than a page inside manager.py, since a
one-time install wizard is a different shape of problem than the settings
manager.py's sidebar covers afterward.

Stdlib only (tkinter), no extra installs.
"""

import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import setup_wizard

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import theme
from shared.theme import BG, GREEN, PANEL_BG, RED, TEXT, TEXT_DIM, FONT_BODY, FONT_HEADING, FONT_MONO, FONT_TITLE, Card, QueueWriter, draw_gradient_bar

BRIDGE_DIR = setup_wizard.BRIDGE_DIR
sys.path.insert(0, str(BRIDGE_DIR))
import create_shortcut


class SetupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Community-iiSU-PC Setup")
        self.geometry("720x600")
        self.minsize(620, 480)
        self.configure(bg=BG)

        self.apk_path: Path | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.running = False

        self._configure_style()
        self._build_ui()
        self._autodetect_apk()
        self.after(100, self._poll_log_queue)

    # -- Style -------------------------------------------------

    def _configure_style(self) -> None:
        theme.apply_ttk_styles(ttk.Style(self))

    # -- UI -------------------------------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 8))
        tk.Label(header, text="Community-iiSU-PC Setup", font=FONT_TITLE, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(
            header,
            text="Patches your own copy of iiSU to hand off game launches to real PC\nemulators, and sets up a self-contained Android VM to run it in.",
            font=FONT_BODY, bg=BG, fg=TEXT_DIM, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        gradient = tk.Canvas(self, height=3, bg=BG, highlightthickness=0)
        gradient.pack(fill="x", padx=20, pady=(0, 14))
        self.after(10, lambda: draw_gradient_bar(gradient, gradient.winfo_width() or 720, 3))
        self.bind("<Configure>", lambda e: draw_gradient_bar(gradient, gradient.winfo_width(), 3))

        apk_card = Card(self)
        apk_card.pack(fill="x", padx=20, pady=(0, 12))
        apk_inner = tk.Frame(apk_card, bg=PANEL_BG)
        apk_inner.pack(fill="x", padx=16, pady=14)
        tk.Label(apk_inner, text="iiSU APK", font=FONT_HEADING, bg=PANEL_BG, fg=TEXT).pack(anchor="w")
        row = tk.Frame(apk_inner, bg=PANEL_BG)
        row.pack(fill="x", pady=(6, 0))
        self.apk_label = tk.Label(row, text="No APK selected.", font=FONT_BODY, bg=PANEL_BG, fg=TEXT_DIM, anchor="w")
        self.apk_label.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(row, text="Browse...", style="Ghost.TButton", command=self._browse_apk)
        self.browse_button.pack(side="right")

        action_frame = tk.Frame(self, bg=BG)
        action_frame.pack(fill="x", padx=20, pady=(0, 12))
        self.start_button = ttk.Button(action_frame, text="Start Setup", style="Accent.TButton", command=self._start_setup, state="disabled")
        self.start_button.pack(side="left")
        self.progress = ttk.Progressbar(action_frame, mode="indeterminate", style="Dark.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(16, 0))

        log_card = Card(self)
        log_card.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        log_inner = tk.Frame(log_card, bg=PANEL_BG)
        log_inner.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text = tk.Text(log_inner, state="disabled", wrap="word", font=FONT_MONO, bg="#0e0e10", fg="#c9c9ce", insertbackground=TEXT, relief="flat", padx=8, pady=8)
        log_scroll = ttk.Scrollbar(log_inner, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.status_label = tk.Label(self, text="Ready.", font=FONT_BODY, bg=BG, fg=TEXT_DIM, anchor="w")
        self.status_label.pack(fill="x", padx=20, pady=(0, 8))

        self.next_steps_frame = tk.Frame(self, bg=BG)
        # Populated (and packed) only after a successful setup.

    def _autodetect_apk(self) -> None:
        found = setup_wizard.find_input_apk()
        if found is not None:
            self._set_apk(found)

    def _browse_apk(self) -> None:
        chosen = filedialog.askopenfilename(title="Select your iiSU APK", filetypes=[("Android APK", "*.apk")])
        if chosen:
            self._set_apk(Path(chosen))

    def _set_apk(self, path: Path) -> None:
        self.apk_path = path
        self.apk_label.config(text=str(path), fg=TEXT)
        if not self.running:
            self.start_button.config(state="normal")

    # -- Log handling -------------------------------------------------

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

    # -- Setup run -------------------------------------------------

    def _start_setup(self) -> None:
        if self.apk_path is None or self.running:
            return
        self.running = True
        self.start_button.config(state="disabled")
        self.browse_button.config(state="disabled")
        self.status_label.config(text="Running setup -- this can take a long time on first run (several GB)...", fg=TEXT_DIM)
        self.progress.start(12)

        thread = threading.Thread(target=self._run_setup_thread, args=(self.apk_path,), daemon=True)
        thread.start()

    def _run_setup_thread(self, apk_path: Path) -> None:
        writer = QueueWriter(self.log_queue)
        old_stdout = sys.stdout
        sys.stdout = writer
        error: Exception | None = None
        try:
            setup_wizard.run_setup(apk_path, on_stage=self._on_stage)
        except Exception as e:  # noqa: BLE001 -- surfaced to the user below, not swallowed
            error = e
            print(f"\n[setup] FAILED: {e}\n")
            print(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
        self.after(0, self._on_setup_finished, error)

    def _on_stage(self, label: str, index: int, total: int) -> None:
        # Called from the worker thread -- self.after() is safe to call
        # from any thread, it just schedules onto the Tk main loop.
        self.after(0, self._apply_stage, label, index, total)

    def _apply_stage(self, label: str, index: int, total: int) -> None:
        self.status_label.config(text=f"Step {index}/{total}: {label}...", fg=TEXT_DIM)

    def _on_setup_finished(self, error: Exception | None) -> None:
        self.running = False
        self.progress.stop()
        self.browse_button.config(state="normal")
        self.start_button.config(state="normal")

        if error is None:
            self.status_label.config(text="Setup complete.", fg=GREEN)
            self._show_next_steps()
            self._open_onboarding()
        else:
            self.status_label.config(text=f"Setup failed: {error}", fg=RED)
            if isinstance(error, setup_wizard.VirtualizationError):
                self._offer_hypervisor_fix(str(error))
            else:
                messagebox.showerror("Setup failed", f"{error}\n\nSee the log for details.")

    def _offer_hypervisor_fix(self, message: str) -> None:
        """VirtualizationError specifically (not every setup failure) means
        there's a concrete, one-click-away fix worth offering right in the
        dialog instead of leaving the person to go search for what
        "Windows Hypervisor Platform" even is. Enabling it needs admin
        rights and a restart -- both handled by enable_hypervisor_platform()
        itself (a real UAC prompt, and this never reboots the PC on its
        own), so this is just the confirm step."""
        if "Hypervisor Platform" not in message:
            messagebox.showerror("Setup failed", f"{message}\n\nSee the log for details.")
            return
        if messagebox.askyesno(
            "Enable Windows Hypervisor Platform?",
            f"{message}\n\nEnable Windows Hypervisor Platform now? This asks Windows for admin "
            "permission and won't take effect until you restart your PC -- re-run Setup.bat "
            "after restarting.",
        ):
            try:
                setup_wizard.enable_hypervisor_platform()
                messagebox.showinfo(
                    "Enabling...",
                    "Windows is enabling Hypervisor Platform now (you may see a UAC prompt). "
                    "Restart your PC once it's done, then re-run Setup.bat.",
                )
            except Exception as e:
                messagebox.showerror("Couldn't enable it automatically", f"{e}\n\nTry enabling \"Windows Hypervisor Platform\" yourself via \"Turn Windows features on or off\".")
        else:
            messagebox.showerror("Setup failed", f"{message}\n\nSee the log for details.")

    def _show_next_steps(self) -> None:
        self.next_steps_frame.pack(fill="x", padx=20, pady=(0, 18))
        tk.Label(self.next_steps_frame, text="Next step:", font=FONT_HEADING, bg=BG, fg=TEXT).pack(anchor="w", pady=(0, 6))
        ttk.Button(
            self.next_steps_frame, text="Open Community-iiSU-PC Manager", style="Accent.TButton",
            command=lambda: self._launch_bridge_script("manager.py"),
        ).pack(side="left")
        ttk.Button(
            self.next_steps_frame, text="Create Desktop Shortcut", style="Ghost.TButton",
            command=self._create_shortcut,
        ).pack(side="left", padx=(10, 0))

    def _open_onboarding(self) -> None:
        """Runs right after a successful setup, unprompted -- roms_dir still
        holds the template's placeholder value at this point, so without
        this the frontend would show an empty library until the user found
        their own way to a settings screen. Walks through ROM directory,
        emulator folders, display, and hotkeys one step at a time instead
        of dropping manager.py's settings pages on someone who's never seen
        this app before; that Manager is still there afterward via "Open
        Community-iiSU-PC Manager" for anything this doesn't cover."""
        subprocess.Popen([sys.executable, "onboarding_wizard.py"], cwd=str(BRIDGE_DIR))

    def _create_shortcut(self) -> None:
        try:
            path = create_shortcut.create_desktop_shortcut(self.apk_path)
        except Exception as e:
            messagebox.showerror("Couldn't create shortcut", str(e))
            return
        messagebox.showinfo("Shortcut created", f"Created {path.name} on your desktop.")

    def _launch_bridge_script(self, script_name: str) -> None:
        # CREATE_NO_WINDOW: this only ever launches manager.py, a GUI app
        # with nothing worth showing in a console -- a console window
        # alongside it would just be clutter with no content.
        subprocess.Popen(
            [sys.executable, script_name],
            cwd=str(BRIDGE_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


if __name__ == "__main__":
    SetupApp().mainloop()
