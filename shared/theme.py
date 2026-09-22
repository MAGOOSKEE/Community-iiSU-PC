"""
Shared dark-UI theme for Community-iiSU-PC's tkinter front ends (bridge/manager.py,
bridge/onboarding_wizard.py, installer/setup_gui.py): one palette, font
set, and small set of building blocks so every window reads as one
application instead of several unrelated tools bolted together.

The palette echoes iiSU's own in-app look (dark background, tile-style
panels, its cyan-to-purple gradient) -- colors and layout only, not any of
iiSU's actual asset files (fonts, icons), since those are iiSU's own
copyrighted assets and not ours to include.
"""

import queue
import tkinter as tk
from tkinter import ttk

BG = "#141414"
PANEL_BG = "#212124"
PANEL_BG_HOVER = "#2a2a2e"
TEXT = "#f2f2f4"
TEXT_DIM = "#9a9aa2"
GRADIENT_STOPS = ["#71e0ff", "#68ccff", "#5e84ff", "#8258fa", "#c56eff"]
GREEN = "#4fd67a"
RED = "#ff6161"
GRAY = "#5a5a60"

FONT_TITLE = ("Segoe UI Semibold", 20)
FONT_HEADING = ("Segoe UI Semibold", 11)
FONT_BODY = ("Segoe UI", 10)
FONT_MONO = ("Consolas", 9)

# Classic (non-ttk) widgets like Entry and Listbox aren't reachable through
# ttk.Style, so their dark styling is a plain kwargs dict to unpack instead.
ENTRY_KWARGS = dict(
    bg="#0e0e10", fg=TEXT, insertbackground=TEXT, relief="flat",
    highlightthickness=1, highlightbackground=PANEL_BG_HOVER, highlightcolor=GRADIENT_STOPS[2],
)
LISTBOX_KWARGS = dict(
    bg="#0e0e10", fg=TEXT, selectbackground=GRADIENT_STOPS[2], selectforeground=TEXT,
    relief="flat", highlightthickness=1, highlightbackground=PANEL_BG_HOVER,
)


class QueueWriter:
    """A writable stream that pushes text into a queue instead of a real
    file, so a background thread's plain print() calls can reach a tkinter
    log widget without the printing code needing to know a GUI exists."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, text: str) -> None:
        if text:
            self.q.put(text)

    def flush(self) -> None:
        pass


def draw_gradient_bar(canvas: tk.Canvas, width: int, height: int) -> None:
    """Horizontal multi-stop gradient, drawn as a strip of thin rectangles
    since tkinter has no native gradient fill."""
    canvas.delete("all")
    steps = max(width, 1)
    n_stops = len(GRADIENT_STOPS)
    stop_rgbs = [canvas.winfo_rgb(c) for c in GRADIENT_STOPS]
    for x in range(steps):
        t = x / max(steps - 1, 1) * (n_stops - 1)
        i = min(int(t), n_stops - 2)
        frac = t - i
        r1, g1, b1 = stop_rgbs[i]
        r2, g2, b2 = stop_rgbs[i + 1]
        r = int(r1 + (r2 - r1) * frac) >> 8
        g = int(g1 + (g2 - g1) * frac) >> 8
        b = int(b1 + (b2 - b1) * frac) >> 8
        canvas.create_line(x, 0, x, height, fill=f"#{r:02x}{g:02x}{b:02x}")


def draw_menu_icon(canvas: tk.Canvas, size: float, color: str) -> None:
    """Draws Google's Material Symbols "menu" glyph (three equal,
    evenly-spaced horizontal bars) directly on a canvas, matching that
    icon's real proportions. tkinter has no built-in way to render a
    proper icon-font/SVG set like Material Symbols, and this specific
    glyph has no stylistic detail beyond "three bars" to lose by hand-
    drawing it -- crisper and more consistent than relying on Segoe UI's
    rendering of the "☰" Unicode character (which doesn't reliably
    match the surrounding icon style at all sizes/DPIs), without pulling
    in Pillow, a bundled icon-font file, or a network fetch for one
    glyph."""
    canvas.delete("all")
    margin = size * 0.17
    bar_width = size - 2 * margin
    bar_height = max(size * 0.09, 1.5)
    for cy in (size * 0.25, size * 0.5, size * 0.75):
        canvas.create_rectangle(
            margin, cy - bar_height / 2, margin + bar_width, cy + bar_height / 2,
            fill=color, outline="",
        )


class Card(tk.Frame):
    """A rounded-ish dark panel echoing iiSU's tile style. tkinter has no
    native rounded-rect widget background, so this approximates it with a
    plain dark panel and generous padding rather than fighting the toolkit
    for pixel-perfect corners."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=PANEL_BG, highlightthickness=0, **kwargs)


def apply_ttk_styles(style: ttk.Style) -> None:
    """Configures the ttk styles the front ends build their widgets from
    (buttons, progress bar, notebook tabs, treeview, checkbutton, plain
    frames), so a style tweak only has to happen in one place."""
    style.theme_use("clam")
    style.configure("Accent.TButton", background="#3a3a40", foreground=TEXT, font=FONT_HEADING, padding=(16, 10), borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#48484f"), ("disabled", "#2a2a2e")], foreground=[("disabled", TEXT_DIM)])
    style.configure("Ghost.TButton", background=PANEL_BG, foreground=TEXT, font=FONT_BODY, padding=(12, 6), borderwidth=0)
    style.map("Ghost.TButton", background=[("active", PANEL_BG_HOVER)])
    style.configure("Dark.Horizontal.TProgressbar", background=GRADIENT_STOPS[2], troughcolor=PANEL_BG, borderwidth=0)

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=TEXT_DIM, font=FONT_BODY, padding=(14, 8), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", PANEL_BG)], foreground=[("selected", TEXT)])

    style.configure("Treeview", background="#0e0e10", fieldbackground="#0e0e10", foreground=TEXT, font=FONT_BODY, borderwidth=0, rowheight=26)
    style.configure("Treeview.Heading", background=PANEL_BG, foreground=TEXT_DIM, font=FONT_HEADING, borderwidth=0)
    style.map("Treeview", background=[("selected", GRADIENT_STOPS[2])], foreground=[("selected", "#101010")])
    style.map("Treeview.Heading", background=[("active", PANEL_BG_HOVER)])

    style.configure("TCheckbutton", background=PANEL_BG, foreground=TEXT, font=FONT_BODY)
    style.map("TCheckbutton", background=[("active", PANEL_BG)], foreground=[("disabled", TEXT_DIM)])

    # The dropdown popup list itself is native-rendered and outside ttk's
    # reach on Windows, but this at least keeps the field itself dark.
    style.configure("TCombobox", fieldbackground="#0e0e10", background=PANEL_BG, foreground=TEXT, arrowcolor=TEXT, borderwidth=0, padding=4)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "#0e0e10")],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", "#0e0e10")],
        selectforeground=[("readonly", TEXT)],
    )
