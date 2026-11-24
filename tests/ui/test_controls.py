"""Les commandes animées gardent le comportement des widgets qu'elles remplacent."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSlider, QVBoxLayout, QWidget

from azimut.ui.controls import (
    CopyFeedbackButton,
    SegmentedControl,
    Switch,
    SwitchRow,
    ValueBubble,
    attach_value_bubble,
)
from azimut.ui.theme import ThemeManager


def test_switch_toggles_and_keeps_the_liquid_face(qapp) -> None:
    switch = Switch()
    switch.show()
    qapp.processEvents()
    assert not switch.isChecked()
    switch.setChecked(True)
    qapp.processEvents()
    assert switch.isChecked()
    assert switch.width() > switch.height()
    assert not switch.grab().isNull()


def test_switch_row_matches_the_checkbox_api(qapp) -> None:
    seen: list[bool] = []
    row = SwitchRow("Option")
    row.toggled.connect(seen.append)
    assert not row.isChecked()
    row.setChecked(True)
    assert row.isChecked()
    row.switch.toggle()
    assert seen == [True, False]
    row.setText("Autre option")
    assert row.text() == "Autre option"
    row.setEnabled(False)
    assert not row.switch.isEnabled()


def test_switch_row_keeps_its_switch_next_to_the_label(qapp) -> None:
    host = QWidget()
    layout = QVBoxLayout(host)
    row = SwitchRow("Option")
    layout.addWidget(row)
    host.resize(600, 80)
    host.show()
    qapp.processEvents()
    gap = row.switch.x() - (row.label.x() + row.label.width())
    assert 0 <= gap <= 12
    assert row.switch.x() < host.width() // 2
    host.close()


def test_switch_row_aligned_right_keeps_its_label_on_one_line(qapp) -> None:
    """Le libellé reçoit la largeur libre, même quand la police est petite."""
    host = QWidget()
    layout = QVBoxLayout(host)
    row = SwitchRow("Réduire les animations", align_right=True)
    layout.addWidget(row)
    host.resize(750, 80)
    host.show()
    qapp.processEvents()
    needed = row.label.fontMetrics().horizontalAdvance(row.text())
    assert row.label.width() >= needed
    assert row.switch.x() > row.width() // 2
    host.close()


def test_switch_dark_track_has_a_visible_gradient(qapp) -> None:
    manager = ThemeManager.instance()
    previous = manager.theme()
    manager.set_theme("dark")
    try:
        switch = Switch()
        switch.show()
        qapp.processEvents()
        image = switch.grab().toImage()
        # Le bord supérieur du rail est plus clair que le bord inférieur.
        top = image.pixelColor(switch.width() // 2, 5).lightness()
        bottom = image.pixelColor(switch.width() // 2, switch.height() - 5).lightness()
        assert top > bottom + 8
    finally:
        switch.close()
        manager.set_theme(previous)


def test_switch_snaps_when_motion_is_reduced(qapp) -> None:
    manager = ThemeManager.instance()
    previous = manager.reduce_motion()
    manager.set_reduce_motion(True)
    try:
        switch = Switch()
        switch.show()
        switch.setChecked(True)
        assert switch._p == 1.0
        assert not switch._timer.isActive()
    finally:
        manager.set_reduce_motion(previous)


def test_segmented_control_speaks_the_combo_language(qapp) -> None:
    control = SegmentedControl()
    control.addItem("Système", "system")
    control.addItem("Sombre", "dark")
    control.addItem("Clair", "light")
    seen: list[int] = []
    control.currentIndexChanged.connect(seen.append)
    assert control.count() == 3
    assert control.findData("dark") == 1
    assert control.currentData() == "system"
    control.setCurrentIndex(2)
    assert control.currentIndex() == 2
    assert control.currentText() == "Clair"
    assert seen == [2]
    control.show()
    qapp.processEvents()
    assert control.width() == control._segment * 3
    assert not control.grab().isNull()


def test_segmented_control_follows_a_zoom_reduction(qapp) -> None:
    """150 % → 100 % : les segments et leurs libellés reprennent l'échelle demandée."""
    manager = ThemeManager.instance()
    original_zoom = manager.zoom()
    try:
        manager.set_zoom(1.5)
        control = SegmentedControl()
        for text, data in (("Système", "system"), ("Sombre", "dark"), ("Clair", "light")):
            control.addItem(text, data)
        large_segment, large_height = control._segment, control.height()
        large_font = control._label_font(QFont.Weight.DemiBold).pixelSize()

        manager.set_zoom(1.0)

        # Le sélecteur ouvert à 150 % rejoint exactement celui construit à 100 %.
        fresh = SegmentedControl()
        for text, data in (("Système", "system"), ("Sombre", "dark"), ("Clair", "light")):
            fresh.addItem(text, data)
        assert control._segment < large_segment and control.height() < large_height
        assert control._segment == fresh._segment
        assert control.height() == fresh.height()
        assert control._label_font(QFont.Weight.DemiBold).pixelSize() \
            == fresh._label_font(QFont.Weight.DemiBold).pixelSize()
        assert control._label_font(QFont.Weight.DemiBold).pixelSize() < large_font
    finally:
        manager.set_zoom(original_zoom)


def test_segmented_control_has_a_hover_glow(qapp) -> None:
    control = SegmentedControl()
    control.addItem("Système", "system")
    control.addItem("Sombre", "dark")
    control.addItem("Clair", "light")
    control.show()
    qapp.processEvents()
    QTest.mouseMove(control, QPoint(control._segment + 4, control.height() // 2))
    QTest.qWait(240)
    qapp.processEvents()
    assert control._hover_index == 1
    assert control._hover_value > 0.8
    assert not control.grab().isNull()
    control.close()


def test_segmented_control_focus_ring_follows_keyboard_focus_only(qapp) -> None:
    """Le clavier allume l'anneau orange, le clic souris l'éteint sans retirer le focus."""
    from azimut.ui.theme import install_focus_ring_filter

    install_focus_ring_filter(qapp)
    control = SegmentedControl()
    control.addItem("Options")
    control.addItem("Résultats")
    control.show()
    control.activateWindow()
    qapp.processEvents()

    control.clearFocus()
    qapp.processEvents()
    control.setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    assert control.hasFocus()
    assert control.property("keyboardFocus") is True

    QTest.mouseClick(control, Qt.MouseButton.LeftButton,
                     pos=QPoint(control._segment + 2, control.height() // 2))
    qapp.processEvents()
    assert control.hasFocus()
    assert control.property("keyboardFocus") is False
    assert control.currentIndex() == 1

    control.clearFocus()
    qapp.processEvents()
    assert control.property("keyboardFocus") is False
    control.close()


def test_value_bubble_follows_the_slider_then_hides(qapp) -> None:
    window = QWidget()
    layout = QVBoxLayout(window)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 10)
    slider.setValue(4)
    layout.addWidget(slider)
    link = attach_value_bubble(slider, lambda: f"{slider.value() * 10} %")
    window.show()
    qapp.processEvents()

    slider.sliderPressed.emit()
    qapp.processEvents()
    bubble = window.findChild(ValueBubble)
    assert bubble is not None and bubble.isVisible()
    assert bubble.text() == "40 %"

    slider.setValue(7)
    qapp.processEvents()
    assert bubble.text() == "70 %"

    slider.sliderReleased.emit()
    qapp.processEvents()
    assert not bubble.isVisible()
    assert link._bubble is bubble


def test_copy_button_confirms_then_restores(qapp) -> None:
    button = CopyFeedbackButton("Copier")
    button.show()
    qapp.processEvents()
    button.click()
    QTest.qWait(250)
    assert button.text() == "Copié"
    QTest.qWait(CopyFeedbackButton.HOLD_MS)
    assert button.text() == "Copier"
