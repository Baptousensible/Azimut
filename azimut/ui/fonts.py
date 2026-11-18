"""Polices embarquées d'Azimut.

Le lettrage du logotype est du Montserrat ExtraBold (SIL OFL 1.1). La police
est livrée avec l'application pour que le titre de l'en-tête garde le même
dessin sur toutes les machines, même sans Montserrat installé.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QFont, QFontDatabase

from azimut.core.logs import get_logger
from azimut.core.paths import resource_path

log = get_logger(__name__)

BRAND_FONT_FAMILY = "Montserrat"
BRAND_FONT_FILE = "Montserrat-ExtraBold.ttf"


@lru_cache(maxsize=1)
def brand_family() -> str:
    """Charge la police de marque et renvoie la famille réellement disponible."""
    path = resource_path("fonts", BRAND_FONT_FILE)
    font_id = QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    if not families:
        log.warning("Police de marque introuvable : %s", path)
        return BRAND_FONT_FAMILY
    return families[0]


def brand_font(pixel_size: int) -> QFont:
    """QFont du lettrage Azimut à la taille demandée, dans le poids du logotype."""
    font = QFont(brand_family())
    font.setPixelSize(pixel_size)
    font.setWeight(QFont.Weight.ExtraBold)
    return font
