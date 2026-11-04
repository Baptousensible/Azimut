"""Icônes SVG (Papicons + complément Phosphor) : présence, recoloration, repli et intégration."""
from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from azimut.launcher.registry import discover_with_errors
from azimut.core.prefs import Preferences
from azimut.ui import icons
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import DropZone, IconLabel, StatusChip


@pytest.fixture(autouse=True)
def manager(qapp, tmp_path, monkeypatch):
    tm = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", tm)
    icons.clear_cache()
    return tm


def _dominant_rgb(pm) -> tuple[int, int, int]:
    image: QImage = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    for x in range(image.width()):
        for y in range(image.height()):
            c = QColor.fromRgba(image.pixel(x, y))
            if c.alpha() == 255:
                return c.red(), c.green(), c.blue()
    raise AssertionError("aucun pixel opaque")


def test_available_and_unknown_names():
    assert icons.available("check") and icons.available("alert-triangle")
    assert not icons.available("") and not icons.available("🧪")
    assert not icons.available("../secret") and not icons.available("n'existe-pas")


def test_pixmap_is_recolored():
    assert _dominant_rgb(icons.pixmap("check", 32, "#ff0000")) == (255, 0, 0)
    assert _dominant_rgb(icons.pixmap("crown", 32, "#00ff00")) == (0, 255, 0)    # fill="#000" aussi


def test_pixmap_accepts_theme_token(manager):
    from azimut.ui.theme import palette
    expected = QColor(palette("dark")["ok_txt"])
    assert _dominant_rgb(icons.pixmap("check", 32, "ok_txt")) == (expected.red(), expected.green(), expected.blue())


def test_unknown_icon_gives_empty_pixmap_and_icon():
    assert icons.pixmap("nope").isNull() and icons.icon("nope").isNull()


def test_split_glyph():
    assert icons.split_glyph("✔ ffmpeg") == ("check", "ffmpeg")
    assert icons.split_glyph("✖ introuvable") == ("cross", "introuvable")
    assert icons.split_glyph("ffmpeg") == ("", "ffmpeg")


def test_tile_icon_not_empty():
    assert not icons.tile_icon("globe").isNull()
    assert icons.tile_icon("nope").isNull()


def test_every_module_uses_an_existing_icon():
    specs, errors = discover_with_errors()
    assert specs and not errors
    for spec in specs:
        assert icons.available(spec.icon), f"{spec.id} : icône « {spec.icon} » introuvable dans resources/icons"


def test_status_chip_turns_glyph_into_icon():
    chip = StatusChip("✔ ffmpeg", "ok")
    assert chip.text() == "ffmpeg" and chip.icon_name() == "check" and chip.property("level") == "ok"
    chip.set_state("✖ ffmpeg", "err")
    assert chip.icon_name() == "cross"
    chip.set_state("Prêt", "ok")
    assert chip.text() == "Prêt" and chip.icon_name() == ""
    chip.grab()                                     # le rendu ne doit pas lever d'exception


def test_icon_label_and_drop_zone_render():
    label = IconLabel("Attention", "alert-triangle", "warn_txt")
    assert label.icon_name() == "alert-triangle" and label.indent() > 0
    label.set_icon("")
    assert label.icon_name() == ""
    zone = DropZone("Déposez ici", icon="folder")
    assert not zone._icon_label.pixmap().isNull()
    zone.set_icon("")
    assert zone._icon_label.isHidden()
    zone.set_icon("check")
    assert not zone._icon_label.pixmap().isNull()


# ── Complément Phosphor Fill ─────────────────────────────────────────────────
_ICON_ROOT = Path(icons.__file__).resolve().parents[1] / "resources" / "icons"


def test_phosphor_complement_is_available_and_recolored():
    for name in ("tag", "swap", "copy", "file-zip", "pencil-simple-line", "folders", "music-notes", "file-video"):
        assert icons.available(name), name
    assert _dominant_rgb(icons.pixmap("tag", 32, "#ff0000")) == (255, 0, 0)
    assert not icons.tile_icon("swap").isNull()
    assert not icons.icon("copy").isNull()


def test_phosphor_files_are_normalized_and_never_shadow_papicons():
    papicons = {p.stem for p in _ICON_ROOT.glob("*.svg")}
    extra = sorted((_ICON_ROOT / "phosphor").glob("*.svg"))
    assert extra and (_ICON_ROOT / "phosphor" / "LICENSE-Phosphor.txt").is_file()
    for path in extra:
        assert 'fill="black"' in path.read_text(encoding="utf-8"), path.name   # recolorable comme Papicons
        assert "currentColor" not in path.read_text(encoding="utf-8"), path.name
        assert path.stem not in papicons, f"{path.stem} existe déjà dans Papicons (doublon inutile)"


def test_papicons_are_searched_first():
    """La recherche commence par le dossier Papicons (à nom égal, il l'emporte)."""
    found = icons._find("check")
    assert found is not None and found.parent == _ICON_ROOT


def test_every_icon_name_used_in_the_code_exists():
    """Tout nom d'icône écrit en dur (icon=…, set_icon, icons.icon/pixmap, tableau des boutons) doit exister."""
    root = _ICON_ROOT.parents[1]
    used: set[str] = set()
    patterns = (r'icon="([a-z][a-z0-9-]*)"', r'set_icon\("([a-z][a-z0-9-]*)"', r'icons\.(?:icon|pixmap)\("([a-z][a-z0-9-]*)"',
                r'_icon_\w+": "([a-z][a-z0-9-]*)"', r'\("[a-z_]+_button", "([a-z][a-z0-9-]*)"')
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for pat in patterns:
            used.update(re.findall(pat, text))
    used.update(icons.GLYPH_ICONS.values())
    missing = sorted(n for n in used if not icons.available(n))
    assert not missing, f"icônes introuvables : {missing}"
