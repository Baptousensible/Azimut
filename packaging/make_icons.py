#!/usr/bin/env python3
"""Prépare les icônes de l'application : packaging/azimut.png, azimut.ico et azimut.icns.

- Si packaging/azimut.png existe (idéalement 1024×1024, fond transparent), c'est elle qui sert de source :
  elle est convertie en .ico et .icns.
- Sinon, une icône de substitution (boussole) est dessinée : de quoi publier sans attendre un vrai logo.
- Un .ico ou .icns déjà présent n'est jamais écrasé : committez les vôtres pour les figer.

Usage : python packaging/make_icons.py
Nécessite Pillow (déjà une dépendance d'Azimut).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
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


def main() -> int:
    png = HERE / "azimut.png"
    if png.is_file():
        base = Image.open(png).convert("RGBA")
        if base.width != base.height:
            side = max(base.size)
            square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            square.paste(base, ((side - base.width) // 2, (side - base.height) // 2))
            base = square
        print(f"Icône source : {png.name} ({base.width}×{base.height})")
    else:
        base = draw_default_icon()
        base.save(png)
        print(f"Icône de substitution générée : {png.name}")

    ico = HERE / "azimut.ico"
    if not ico.exists():
        base.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"Généré : {ico.name}")

    icns = HERE / "azimut.icns"
    if not icns.exists():
        big = base.resize((1024, 1024), Image.LANCZOS) if base.size != (1024, 1024) else base
        big.save(icns)
        print(f"Généré : {icns.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
