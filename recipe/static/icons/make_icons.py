"""Generiert die PWA-Icons fuer Kuechenbuch.

Vollflaechiger Akzent-Hintergrund (#bf4528) mit zentriertem, cremefarbenem
Marken-Zeichen (U+2756, schwarze Raute mit Punkt). Groesse ~0.58*Kantenlaenge,
liegt damit im sicheren Bereich fuer "maskable".

Aufruf:
    .venv\\Scripts\\python.exe recipe/static/icons/make_icons.py
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

ACCENT = (191, 69, 40, 255)   # #bf4528  Paprika-Rot
CREAM = (252, 247, 236, 255)  # #fcf7ec  Cremeton
GLYPH = "❖"              # Marken-Zeichen


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
