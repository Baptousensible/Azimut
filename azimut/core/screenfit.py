"""Taille de fenêtre compatible avec l'écran (sans Qt : testable partout)."""
from __future__ import annotations

MARGIN_W = 24          # bordures et ombres de la fenêtre
MARGIN_H = 56          # barre de titre + bord inférieur (la barre des tâches est déjà déduite de la zone utile)
FLOOR_W, FLOOR_H = 480, 360


def fit_to_screen(width: int, height: int, available: tuple[int, int] | None) -> tuple[int, int]:
    """Réduit la taille voulue pour qu'elle tienne dans la zone utile `available` (largeur, hauteur) de l'écran.

    Un portable 1366 × 768 à 125 % n'offre que 1093 × 614 pixels logiques : une fenêtre imposée à 960 × 640 y
    dépasse en bas et cache les boutons. Sans écran connu (`None`), la taille voulue est conservée ; jamais
    en dessous de 480 × 360, taille à partir de laquelle plus aucune disposition n'est lisible.
    """
    if not available or available[0] <= 0 or available[1] <= 0:      # écran inconnu ou virtuel (taille nulle)
        return int(width), int(height)
    avail_w, avail_h = available
    return (min(int(width), max(FLOOR_W, int(avail_w) - MARGIN_W)),
            min(int(height), max(FLOOR_H, int(avail_h) - MARGIN_H)))
