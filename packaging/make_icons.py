#!/usr/bin/env python3
"""Prépare les icônes de l'application : packaging/azimut.png, azimut.ico et azimut.icns.

- L'icône source est ``azimut/resources/azimut.png`` (1024×1024, fond transparent) ; à défaut,
  ``packaging/azimut.png`` sert de source. Elle est copiée dans packaging/ puis convertie en .ico et .icns.
- Sinon, une icône de substitution (boussole) est dessinée : de quoi publier sans attendre un vrai logo.
- Le .ico et le .icns sont régénérés si la source est plus récente.

Usage : python packaging/make_icons.py
Nécessite Pillow (déjà une dépendance d'Azimut).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME_ICON = ROOT / "azimut" / "resources" / "azimut.png"
SIZE = 1024


def draw_default_icon() -> Image.Image:
    """Boussole : tuile bleu nuit, cercle clair, aiguille orientée (l'« azimut »)."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 64
    draw.rounded_rectangle((margin, margin, SIZE - margin, SIZE - margin), radius=210, fill=(18, 36, 66, 255))

    c = SIZE // 2
    ring = 330
    draw.ellipse((c - ring, c - ring, c + ring, c + ring), outline=(226, 234, 246, 255), width=34)
    for step in range(0, 360, 30):                               # graduations
        a = math.radians(step)
        long_tick = step % 90 == 0
        r1, r2 = ring - (70 if long_tick else 44), ring - 10
        draw.line((c + r1 * math.sin(a), c - r1 * math.cos(a), c + r2 * math.sin(a), c - r2 * math.cos(a)),
                  fill=(226, 234, 246, 255), width=22 if long_tick else 12)

    angle = math.radians(40)                                      # cap de l'aiguille
    ux, uy = math.sin(angle), -math.cos(angle)                    # direction (nord = haut)
    px, py = -uy, ux                                              # perpendiculaire
    tip, tail, half = 250, 250, 58
    north = [(c + ux * tip, c + uy * tip), (c + px * half, c + py * half), (c - px * half, c - py * half)]
    south = [(c - ux * tail, c - uy * tail), (c + px * half, c + py * half), (c - px * half, c - py * half)]
    draw.polygon(north, fill=(239, 83, 80, 255))
    draw.polygon(south, fill=(226, 234, 246, 255))
    draw.ellipse((c - 26, c - 26, c + 26, c + 26), fill=(18, 36, 66, 255))
    return img


def _square(image: Image.Image) -> Image.Image:
    """Complète une image presque carrée avec une marge transparente, sans la déformer."""
    side = max(image.size)
    if image.size == (side, side):
        return image
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def main() -> int:
    Image.init()                                      # charge les greffons ICO/ICNS
    png = HERE / "azimut.png"
    source = next((p for p in (RUNTIME_ICON, png) if p.is_file()), None)
    if source is not None:
        with Image.open(source) as im:
            base = _square(im.convert("RGBA"))
        base = base.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
        if source != png:
            base.save(png, optimize=True)
        print(f"Icône source : {source.relative_to(ROOT)} ({base.width}×{base.height})")
    else:
        base = draw_default_icon()
        base.save(png, optimize=True)
        print(f"Icône de substitution générée : {png.name}")

    ico = HERE / "azimut.ico"
    if not ico.exists() or ico.stat().st_mtime < png.stat().st_mtime:
        base.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"Généré : {ico.name}")

    icns = HERE / "azimut.icns"
    if not icns.exists() or icns.stat().st_mtime < png.stat().st_mtime:
        big = base.resize((1024, 1024), Image.Resampling.LANCZOS) if base.size != (1024, 1024) else base
        big.save(icns)
        print(f"Généré : {icns.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
