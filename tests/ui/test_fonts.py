from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase

from azimut.ui.fonts import brand_family, brand_font


def test_brand_font_is_the_embedded_montserrat(qapp):
    family = brand_family()
    assert family == "Montserrat"
    assert family in QFontDatabase.families()

    font = brand_font(22)
    assert font.family() == "Montserrat"
    assert font.pixelSize() == 22
    assert font.weight() == QFont.Weight.ExtraBold
