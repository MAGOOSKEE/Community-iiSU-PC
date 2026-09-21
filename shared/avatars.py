"""
Fetches a GitHub user's avatar and renders it as a circular Tk image, for
the Credits page in bridge/manager.py.

GitHub serves a user's current avatar at a stable, unauthenticated URL --
https://github.com/<username>.png -- which redirects to the real image on
avatars.githubusercontent.com. No API token or rate-limited REST call is
needed just to show a picture.

Everything here is best-effort: no internet, no Pillow, or a GitHub outage
should degrade to a plain colored circle with the user's first initial
(drawn on a plain tk.Canvas, no dependencies at all) rather than break the
page -- the same "cosmetic nice-to-have degrades quietly" approach
create_shortcut.py already takes for iiSU's own icon.
"""

import tkinter as tk
import urllib.request

GITHUB_AVATAR_URL = "https://github.com/{username}.png"
FETCH_TIMEOUT = 5.0


def fetch_avatar_bytes(username: str) -> bytes | None:
    """Runs on a background thread (network I/O) -- callers shouldn't call
    this from the Tk main thread. Returns None on any failure at all."""
    try:
        req = urllib.request.Request(
            GITHUB_AVATAR_URL.format(username=username),
            headers={"User-Agent": "iiSU-PC"},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return resp.read()
    except Exception:
        return None


def make_circular_photo(image_bytes: bytes, size: int):
    """Converts raw image bytes into a circular ImageTk.PhotoImage of
    (size x size). Returns None if Pillow isn't available or the bytes
    aren't a decodable image -- callers fall back to make_placeholder_circle
    in that case. Must be called from the Tk main thread (PhotoImage needs
    a live Tk root)."""
    try:
        from PIL import Image, ImageDraw, ImageTk
    except ImportError:
        return None

    try:
        import io
        image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception:
        return None

    # Supersample the mask so the circle's edge isn't visibly jagged at
    # typical avatar sizes (64-96px).
    supersample = 4
    big = size * supersample
    image = image.resize((big, big), Image.LANCZOS)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big, big), fill=255)
    image.putalpha(mask)
    image = image.resize((size, size), Image.LANCZOS)
    return ImageTk.PhotoImage(image)


def make_placeholder_circle(canvas_parent, size: int, label: str, color: str, text_color: str) -> tk.Canvas:
    """A plain tk.Canvas circle with a single letter, used whenever a real
    avatar couldn't be fetched or rendered -- no PIL, no network, cannot
    fail short of Tk itself being broken."""
    canvas = tk.Canvas(canvas_parent, width=size, height=size, highlightthickness=0, bg=canvas_parent["bg"])
    pad = 1
    canvas.create_oval(pad, pad, size - pad, size - pad, fill=color, outline="")
    canvas.create_text(size / 2, size / 2, text=(label[:1] or "?").upper(), fill=text_color, font=("Segoe UI Semibold", int(size * 0.4)))
    return canvas
