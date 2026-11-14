"""
Renders the brand artwork from its SVG sources in docs/images/brand/:

    banner.svg          ->  banner.png              the README header, at 2x
    social-preview.svg  ->  social-preview.png      uploaded by hand in the repo's Settings
    logo.svg            ->  bridge/assets/iisu_launch.png and .ico
                                                    the app icon: the launch shortcut, the installer
    logo-manager.svg    ->  bridge/assets/manager.png and .ico
                                                    the Manager's own icon: its window and shortcuts
    iipc-mark.svg       ->  bridge/assets/iipc_mark.png
                                                    the bare mark the Home page shows, trimmed

The SVGs keep their text as live text in Bahnschrift and Lexend, so they stay
editable, but GitHub would draw them in whatever font each visitor has. The
PNGs are what gets shown. Bahnschrift only comes with Windows, so run this
there after editing an SVG:
    python scripts/render_brand_assets.py
"""

import re
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = PROJECT_ROOT / "docs" / "images" / "brand"
ASSETS_DIR = PROJECT_ROOT / "bridge" / "assets"
# The app's own fonts, plus any weight only the artwork uses.
FONT_DIRS = [PROJECT_ROOT / "shared" / "assets" / "fonts", BRAND_DIR / "fonts"]
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
MARK_WIDTH = 512


def single_font_family(svg: str) -> str:
    """Qt's SVG renderer reads font-family as one name, so a CSS list such as
    "Bahnschrift, sans-serif" matches nothing. Keep the first family only."""
    def first(families: str) -> str:
        return families.replace("&apos;", "").replace("'", "").replace('"', "").split(",")[0].strip()

    svg = re.sub(r'font-family="([^"]*)"', lambda m: f'font-family="{first(m.group(1))}"', svg)
    return re.sub(r"font-family:([^;}]*)", lambda m: f"font-family:{first(m.group(1))}", svg)


def render(name: str, width: int) -> QImage:
    svg = single_font_family((BRAND_DIR / name).read_text(encoding="utf-8"))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize()
    image = QImage(width, round(width * size.height() / size.width()), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    renderer.render(painter)
    painter.end()
    return image


def save(image: QImage, target: Path) -> None:
    image.save(str(target))
    print(f"wrote {target.relative_to(PROJECT_ROOT).as_posix()} ({image.width()} x {image.height()})")


def save_icon(source: str, stem: str) -> None:
    """A 256px PNG and a multi-size ICO in bridge/assets/. Each ICO size is
    drawn from the vector, not scaled down from the biggest one."""
    save(render(source, 256), ASSETS_DIR / f"{stem}.png")
    sizes = {size: BRAND_DIR / f".icon-{size}.png" for size in ICO_SIZES}
    for size, path in sizes.items():
        render(source, size).save(str(path))
    frames = [Image.open(path).convert("RGBA") for path in sizes.values()]
    ico = ASSETS_DIR / f"{stem}.ico"
    frames[-1].save(ico, format="ICO", append_images=frames[:-1], sizes=[(size, size) for size in ICO_SIZES])
    for path in sizes.values():
        path.unlink()
    print(f"wrote {ico.relative_to(PROJECT_ROOT).as_posix()} ({len(ICO_SIZES)} sizes)")


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841; fonts and painting need it
    for folder in FONT_DIRS:
        for font in sorted(folder.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(font))

    save(render("banner.svg", 2560), BRAND_DIR / "banner.png")
    save(render("social-preview.svg", 1280), BRAND_DIR / "social-preview.png")
    save_icon("logo.svg", "iisu_launch")
    save_icon("logo-manager.svg", "manager")

    # The mark sits in a square artboard; the UI wants just the glyphs.
    mark_path = ASSETS_DIR / "iipc_mark.png"
    render("iipc-mark.svg", 1024).save(str(mark_path))
    mark = Image.open(mark_path).convert("RGBA")
    mark = mark.crop(mark.getbbox())
    mark = mark.resize((MARK_WIDTH, round(MARK_WIDTH * mark.height / mark.width)), Image.LANCZOS)
    mark.save(mark_path)
    print(f"wrote {mark_path.relative_to(PROJECT_ROOT).as_posix()} ({mark.width} x {mark.height})")


if __name__ == "__main__":
    main()
