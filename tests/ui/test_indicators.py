"""Indicateurs du thème : coche des cases, point des radios, chevrons de l'arbre (QSS ``image: url()``)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QCheckBox, QRadioButton

from azimut.core.prefs import Preferences
from azimut.ui import indicators
from azimut.ui.theme import ThemeManager, build_qss, palette


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def themed(qt_app, tmp_path, monkeypatch):
    """Thème installé sur l'application, restauré ensuite."""
    font, sheet, pal = qt_app.font(), qt_app.styleSheet(), qt_app.palette()
    tm = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", tm)

    def apply(theme: str) -> None:
        tm.set_theme(theme)
        tm.apply(qt_app)

    yield apply
    qt_app.setFont(font)
    qt_app.setPalette(pal)
    qt_app.setStyleSheet(sheet)


def _whitish(image: QImage, width: int | None = None) -> int:
    """Nombre de pixels quasi blancs dans la zone gauche (là où se trouve l'indicateur)."""
    count = 0
    for x in range(min(width or image.width(), image.width())):
        for y in range(image.height()):
            c = image.pixelColor(x, y)
            if min(c.red(), c.green(), c.blue()) > 235 and c.alpha() > 200:
                count += 1
    return count


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_images_are_written_for_both_densities(theme):
    paths = indicators.build_images(theme, palette(theme))
    assert set(paths) == {"check", "radio", "chevron_right", "chevron_down", "chevron_up"}
    for p in paths.values():
        assert "\\" not in p and Path(p).is_file()
        assert Path(p).with_name(Path(p).stem + "@2x.png").is_file()


def test_check_mark_is_drawn_on_a_transparent_background():
    img = indicators.check_image("#ffffff")
    assert img.pixelColor(0, 0).alpha() == 0 and img.pixelColor(img.width() - 1, 0).alpha() == 0
    assert _whitish(img) >= 8
    assert indicators.check_image("#ffffff", 2).width() == 2 * indicators.BOX


def test_qss_references_the_images():
    for theme in ("dark", "light"):
        qss = build_qss(theme)
        assert "QCheckBox::indicator:checked { background-color: " in qss
        assert qss.count(f"/indicators/{theme}/check.png") >= 2             # cochée et cochée désactivée
        assert f"/indicators/{theme}/radio.png" in qss
        assert "QRadioButton::indicator" in qss and "QSlider::handle:horizontal" in qss
        assert "$" not in qss


def test_qss_falls_back_without_images(monkeypatch):
    def boom(_theme):
        raise OSError("disque en lecture seule")
    monkeypatch.setattr(indicators, "_target_dir", boom)
    assert indicators.build_images("dark", palette("dark")) == {}
    qss = build_qss("dark")
    assert "image: none" in qss and "url(" not in qss


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_checked_box_differs_visibly_from_unchecked(themed, theme):
    """Régression : la case cochée était un aplat bleu sans coche, la décochée un cadre vide."""
    themed(theme)
    off, on = QCheckBox(), QCheckBox()
    on.setChecked(True)
    for w in (off, on):
        w.resize(30, 24)
        w.move(300, 300)                                   # loin du curseur virtuel (0, 0) : pas de survol
        w.show()
    QApplication.processEvents()
    img_off, img_on = off.grab().toImage(), on.grab().toImage()
    assert _whitish(img_on, 26) >= 8                       # coche blanche sur l'aplat bleu
    if theme == "dark":
        assert _whitish(img_off, 26) == 0                  # décochée : pas de coche
    assert img_off != img_on
    # le cadre de la case décochée est présent (ni transparent ni invisible)
    border = palette(theme)["muted"].lstrip("#")
    rgb = tuple(int(border[i:i + 2], 16) for i in (0, 2, 4))
    assert any((_c := img_off.pixelColor(x, y)).getRgb()[:3] == rgb
               for x in range(26) for y in range(img_off.height()))
    off.close()
    on.close()


def test_radio_shows_a_dot_only_when_selected(themed):
    themed("dark")
    off, on = QRadioButton(), QRadioButton()
    on.setChecked(True)
    for w in (off, on):
        w.resize(30, 24)
        w.move(300, 300)
        w.show()
    QApplication.processEvents()
    assert off.grab().toImage() != on.grab().toImage()
    off.close()
    on.close()
