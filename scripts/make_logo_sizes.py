"""Generate scaled logo variants and favicon-style icons from the source logo.

Usage: uv run --with pillow python scripts/make_logo_sizes.py

Reads public/assets/logo.png (RGBA) and writes:
  public/assets/logo-<w>.png for w in {1024, 512, 256, 128} (aspect preserved)
  public/assets/icon-<n>.png for n in {32, 64, 180, 512} (square, transparent pad)
PNGs are written with optimize=True. Deterministic: fixed resampling and padding.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "assets" / "logo.png"
OUT_DIR = SRC.parent

LOGO_WIDTHS = (1024, 512, 256, 128)
ICON_SIZES = (32, 64, 180, 512)


def scaled(width: int) -> Image.Image:
    img = Image.open(SRC).convert("RGBA")
    height = round(img.height * width / img.width)
    return img.resize((width, height), Image.LANCZOS)


def square_icon(size: int) -> Image.Image:
    img = Image.open(SRC).convert("RGBA")
    scale = (size * 0.8) / max(img.size)
    w = max(1, round(img.width * scale))
    h = max(1, round(img.height * scale))
    inner = img.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(inner, ((size - w) // 2, (size - h) // 2), inner)
    return canvas


def main() -> None:
    for width in LOGO_WIDTHS:
        scaled(width).save(OUT_DIR / f"logo-{width}.png", optimize=True)
    for size in ICON_SIZES:
        square_icon(size).save(OUT_DIR / f"icon-{size}.png", optimize=True)
    print(f"wrote {len(LOGO_WIDTHS)} logo sizes and {len(ICON_SIZES)} icons to {OUT_DIR}")


if __name__ == "__main__":
    main()
