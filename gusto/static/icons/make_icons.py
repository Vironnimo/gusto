"""Generate the PWA icons for Gusto.

Full-bleed accent background (#bf4528) with a centered, cream-colored brand
glyph (U+2756, black diamond with a dot). Size ~0.58*edge length, which keeps
it in the safe area for "maskable".

Usage:
    .venv\\Scripts\\python.exe gusto/static/icons/make_icons.py
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

ACCENT = (191, 69, 40, 255)   # #bf4528  paprika red
CREAM = (252, 247, 236, 255)  # #fcf7ec  cream
GLYPH = "❖"              # brand glyph


def make(size: int) -> None:
    img = Image.new("RGBA", (size, size), ACCENT)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("seguisym.ttf", int(size * 0.58))
    box = draw.textbbox((0, 0), GLYPH, font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), GLYPH, font=font, fill=CREAM)
    img.save(OUT / f"icon-{size}.png")


if __name__ == "__main__":
    for s in (192, 512):
        make(s)
    print("icons generated:", [f"icon-{s}.png" for s in (192, 512)])
