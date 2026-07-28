"""Render the Marvin logo SVG into the PNG set required by home-assistant/brands.

Needs `cairosvg` and `pillow`; run from anywhere:

    python scripts/brand/build_brand_assets.py
"""
import tempfile
from pathlib import Path

import cairosvg
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = HERE / "marvin-logo.svg"
OUT = REPO / "custom_components" / "marvin_connected_home" / "brand"
OUT.mkdir(parents=True, exist_ok=True)

# Render far above the largest target so every downscale is a clean reduction.
MASTER_W, MASTER_H = 4160, 3120
with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
    cairosvg.svg2png(url=str(SRC), write_to=tmp.name,
                     output_width=MASTER_W, output_height=MASTER_H)
    master = Image.open(tmp.name).convert("RGBA")


def bands(img):
    """Rows that contain any ink, collapsed into contiguous bands."""
    alpha = img.split()[-1].load()
    w, h = img.size
    inked = [any(alpha[x, y] > 8 for x in range(0, w, 4)) for y in range(h)]
    out, start = [], None
    for y, v in enumerate(inked):
        if v and start is None:
            start = y
        elif not v and start is not None:
            out.append((start, y - 1))
            start = None
    if start is not None:
        out.append((start, h - 1))
    return out


mark_band, word_band = bands(master)

# --- icon: the rose mark only, trimmed and centred in a square canvas ---
mark = master.crop((0, mark_band[0], MASTER_W, mark_band[1] + 1))
mark = mark.crop(mark.getbbox())
side = max(mark.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))

for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
    square.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)

# --- logo: full stacked lockup, shortest side 256 (@1x) / 512 (@2x) ---
logo = master.crop((0, mark_band[0], MASTER_W, word_band[1] + 1))
logo = logo.crop(logo.getbbox())


def to_white(img):
    """Recolour the black wordmark to white, leaving the gold mark untouched."""
    out = img.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if a and max(r, g, b) < 60:
                px[x, y] = (255, 255, 255, a)
    return out


dark_logo = to_white(logo)

for src, stem in ((logo, "logo"), (dark_logo, "dark_logo")):
    for suffix, short in (("", 256), ("@2x", 512)):
        scale = short / min(src.size)
        size = (round(src.width * scale), round(src.height * scale))
        src.resize(size, Image.LANCZOS).save(OUT / f"{stem}{suffix}.png", optimize=True)

for p in sorted(OUT.iterdir()):
    print(f"{p.name:18} {Image.open(p).size}  {p.stat().st_size:>6} bytes")
