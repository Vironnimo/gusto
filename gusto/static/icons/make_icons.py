"""Export Gusto's selected mortar artwork into browser and PWA PNG sizes.

Run from a checkout with Pillow installed:
    python gusto/static/icons/make_icons.py
The original artwork stays in assets/branding, outside the runtime package.
"""
from pathlib import Path

from PIL import Image


OUT = Path(__file__).resolve().parent
SOURCE = OUT.parents[2] / "assets" / "branding" / "gusto-mortar.png"


def main() -> None:
    with Image.open(SOURCE) as original:
        artwork = original.convert("RGB")
    if artwork.width != artwork.height:
        raise ValueError("The icon master must be square")
    for size in (32, 64, 180, 192, 512):
        artwork.resize((size, size), Image.Resampling.LANCZOS).save(
            OUT / f"icon-{size}.png", optimize=True,
        )

    # Extra breathing room keeps the mortar and pestle within circular masks.
    size = 512
    inset = 52
    maskable = Image.new("RGB", (size, size), artwork.getpixel((0, 0)))
    maskable.paste(artwork.resize(
        (size - 2 * inset, size - 2 * inset), Image.Resampling.LANCZOS,
    ), (inset, inset))
    maskable.save(OUT / "icon-maskable-512.png", optimize=True)
    print("Exported browser, Apple touch and PWA icons")


if __name__ == "__main__":
    main()
