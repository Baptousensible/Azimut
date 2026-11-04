"""Régressions d'affichage d'Atlas 0.2.0 : curseurs, cartes repliables, pastilles, panneaux latéraux."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from azimut.core import binaries
from azimut.core.binaries import BinaryInfo
from azimut.core.module_spec import ModuleSpec
from azimut.ui import module_window as mw
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import CollapsibleCard, ImagePreview, PathField, SliderField, tame_combos

SPEC = ModuleSpec(id="fake", name="Azimut Fake", tagline="Module de test.", icon="🧪",
                  entry="x:Y", requires=("ffmpeg",), optional=("exiftool",))


@pytest.fixture(autouse=True)
def _bins(monkeypatch):
    monkeypatch.setattr(binaries, "resolve",
                        lambda name, prefs=None: BinaryInfo(name, None, "missing", ""))


def _app(qapp):
    from azimut.ui.theme import ThemeManager
    ThemeManager.instance().apply(qapp)
    return qapp


# ── SliderField ──────────────────────────────────────────────────────────────
def test_slider_field_spinbox_api_and_signals(qapp):
    _app(qapp)
    f = SliderField(10, 50, 30, suffix=" s", low_hint="bas", high_hint="haut")
    seen: list[int] = []
    f.valueChanged.connect(seen.append)
    assert f.value() == 30 and f.spin.value() == 30 and f.slider.value() == 30
    f.setValue(40)
    assert seen == [40] and f.slider.value() == 40 and f.spin.value() == 40
    f.setValue(40)                                        # inchangé : aucun signal
    f.setValue(999)                                       # borné au maximum
    assert f.value() == 50 and seen == [40, 50]
    f.spin.setValue(12)                                   # saisie dans la case
    assert f.value() == 12 and f.slider.value() == 12 and seen[-1] == 12
    f.slider.setValue(20)                                 # curseur (clavier / molette / relâchement)
    assert f.value() == 20 and f.spin.value() == 20 and seen[-1] == 20
    f.setRange(0, 15)                                     # la valeur est ramenée dans la plage
    assert f.value() == 15 and (f.minimum(), f.maximum()) == (0, 15)


def test_slider_field_drag_does_not_commit_until_release(qapp):
    _app(qapp)
    f = SliderField(0, 100, 10)
    committed, live = [], []
    f.valueChanged.connect(committed.append)
    f.dragging.connect(live.append)
    f.slider.sliderMoved.emit(60)                         # glissement en cours
    assert live == [60] and committed == [] and f.spin.value() == 60
    f.slider.setValue(60)                                 # relâchement
    assert committed == [60] and f.value() == 60


def test_slider_field_special_text_and_hints(qapp):
    _app(qapp)
    f = SliderField(0, 16, 0, special_text="Auto", low_hint="Auto", high_hint="16")
    assert f.spin.text() == "Auto"
    assert not f._hints_box.isHidden()
    f.setHints("", "")
    assert f._hints_box.isHidden()
    f.setToolTip("aide")
    assert f.slider.toolTip() == f.spin.toolTip() == "aide"


def test_slider_spin_text_is_actually_drawn(qapp):
    """Le QSS ne doit pas rendre la case numérique vide (texte invisible)."""
    _app(qapp)
    f = SliderField(1, 7, 3)
    f.resize(300, 40)
    f.show()
    qapp.processEvents()
    assert f.spin.width() >= 78 and f.spin.text() == "3"
    # largeur suffisante pour le texte (padding du style compris)
    assert f.spin.fontMetrics().horizontalAdvance("3") < f.spin.width() - 20


# ── CollapsibleCard ──────────────────────────────────────────────────────────
def test_collapsible_card_toggle_and_summary(qapp):
    _app(qapp)
    card = CollapsibleCard("Options", expanded=True)
    label = QLabel("contenu")
    card.body_layout.addWidget(label)
    card.show()
    events: list[bool] = []
    card.toggled.connect(events.append)
    assert card.is_expanded() and not card._body.isHidden() and card.summary_label.isHidden()
    card.set_summary("a · b")
    card.set_expanded(False)
    assert events == [False] and card._body.isHidden() and not card.summary_label.isHidden()
    assert card.summary_label.text() == "a · b" and card._button.text().startswith("▸")
    card.set_expanded(True)
    assert events == [False, True] and card._button.text().startswith("▾")


# ── PathField / combos ───────────────────────────────────────────────────────
def test_pathfield_long_label_is_stacked_and_wrapped(qapp):
    _app(qapp)
    short = PathField("Dossier :")
    long_ = PathField("Dossier de sortie (vide : « <source>_Pressed » voisin de la source)")
    assert isinstance(short.layout(), QHBoxLayout) and not short.label_widget.wordWrap()
    assert long_.label_widget.wordWrap() and isinstance(long_.layout(), QVBoxLayout)
    assert long_.minimumSizeHint().width() < 400          # n'impose plus la largeur du libellé


def test_tame_combos_limits_minimum_width(qapp):
    _app(qapp)
    root = QWidget()
    lay = QVBoxLayout(root)
    combo = QComboBox()
    combo.addItem("Un libellé vraiment très très long qui imposerait toute sa largeur au panneau")
    lay.addWidget(combo)
    before = root.minimumSizeHint().width()
    tame_combos(root)
    assert root.minimumSizeHint().width() < before and root.minimumSizeHint().width() < 250


def test_image_preview_keeps_a_usable_height(qapp):
    _app(qapp)
    p = ImagePreview(size=300, min_size=200)
    assert p.minimumHeight() == 200 and not p.hasHeightForWidth()
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.addWidget(p)
    lay.addStretch(0)
    host.resize(340, 100)                                  # fenêtre trop basse : l'aperçu ne s'écrase pas
    host.show()
    qapp.processEvents()
    assert p.height() >= 200


# ── ModuleWindow ─────────────────────────────────────────────────────────────
def test_binary_chips_are_replaced_not_leaked(qapp):
    """Régression : l'ancienne pastille restait affichée (grand bloc vert qui masquait le titre)."""
    _app(qapp)
    w = ModuleWindow(SPEC)
    w.show_binary_status(("ffmpeg",))
    w.show_binary_status(("ffmpeg", "exiftool"))
    w.show()
    qapp.processEvents()
    shown = [c for c in w.header.findChildren(type(w.binary_chips[0])) if c.isVisible()]
    assert len(shown) == 2 == len(w.binary_chips)
    assert all(c.height() < 60 and c.width() < 300 for c in shown)
    w.close()


def test_header_is_compact_single_row(qapp):
    _app(qapp)
    w = ModuleWindow(SPEC)
    w.set_content(QWidget())
    w.resize(1180, 740)
    w.show()
    qapp.processEvents()
    assert w.header.height() <= 64
    assert w.title_label.geometry().bottom() < w.header.height()
    w.close()


def test_side_panel_is_scrollable_and_fits_small_screens(qapp):
    _app(qapp)
    w = ModuleWindow(SPEC)
    tall = QWidget()
    lay = QVBoxLayout(tall)
    for i in range(40):
        lay.addWidget(QLabel(f"ligne {i}"))
    w.set_side_panel(tall, "Réglages")
    w.resize(*mw.MIN_WINDOW_SIZE)
    w.show()
    QTest.qWait(30)
    area = w.side_dock.widget()
    assert isinstance(area, QScrollArea) and area.widget() is tall
    assert w.minimumSizeHint().height() <= 640           # utilisable sur un portable 1366×768
    assert mw.MIN_WINDOW_SIZE[0] <= 820 and mw.MIN_WINDOW_SIZE[1] <= 540
    w.close()


def test_side_panel_scroll_area_passed_as_is(qapp):
    _app(qapp)
    w = ModuleWindow(SPEC)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(QLabel("x"))
    w.set_side_panel(area, "Réglages")
    assert w.side_dock.widget() is area
    w.close()


def test_log_is_discreet_by_default(qapp):
    _app(qapp)
    w = ModuleWindow(SPEC)
    w.resize(1180, 760)
    w.show()
    QTest.qWait(40)
    assert w.log_dock.height() <= mw.LOG_HEIGHT_IDLE + 60
    w.close()
