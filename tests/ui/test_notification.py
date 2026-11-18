"""Notifications liquides : apparition, file d'attente et mode sans animation."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from azimut.core.prefs import Preferences
from azimut.ui.notification import (
    LiquidNotification,
    notify_persistent,
    notify_window,
)
from azimut.ui.theme import ThemeManager


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def manager(qt_app, tmp_path, monkeypatch):
    theme = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", theme)
    return theme


def _host(qt_app) -> QWidget:
    host = QWidget()
    host.resize(800, 360)
    layout = QVBoxLayout(host)
    layout.addWidget(QLabel("Contenu"))
    host.show()
    qt_app.processEvents()
    return host


def _is_on_top(host: QWidget, widget: QWidget) -> bool:
    """Le widget est-il celui que la fenêtre renvoie à l'endroit où il se trouve ?"""
    found = host.childAt(widget.mapTo(host, widget.rect().center()))
    while found is not None and found is not widget:
        found = found.parentWidget()
    return found is widget


def _wait_for(qt_app, predicate, timeout: float = 1.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_toast_appears_with_its_content_and_can_be_dismissed(qt_app):
    host = _host(qt_app)
    toast = LiquidNotification(host)
    toast.show_notification("Convert", "3 fichiers traités")
    qt_app.processEvents()
    assert toast.is_animating() and toast.isVisibleTo(host)
    assert toast.title() == "Convert" and toast.message() == "3 fichiers traités"
    assert toast.kind() == "success" and not toast.grab().isNull()

    toast.dismiss()
    qt_app.processEvents()
    assert not toast.is_animating() and not toast.isVisible()
    host.close()


def test_notifications_are_queued_and_reuse_the_window_toast(qt_app):
    host = _host(qt_app)
    first = notify_window(host, "Convert", "Premier")
    second = notify_window(host, "Dedup", "Deuxième")
    assert first is second and first.title() == "Convert"
    assert len(first._queue) == 1
    first.dismiss()
    assert _wait_for(qt_app, lambda: first.title() == "Dedup")
    first.dismiss()
    host.close()


def test_inline_notification_stays_inside_its_header_lane(qt_app):
    host = _host(qt_app)
    lane = QWidget(host)
    lane.setGeometry(250, 60, 300, 40)
    lane.show()
    qt_app.processEvents()

    toast = LiquidNotification(lane, inline=True)
    toast.show_notification("Convert", "3 fichiers traités")
    qt_app.processEvents()
    assert toast.inline and toast.is_animating()
    assert toast.geometry() == lane.rect()
    _, target_w, target_h, _ = toast._metrics()
    assert 0 < target_w <= lane.width()
    assert 0 < target_h <= lane.height()
    assert not toast.grab().isNull()

    toast.dismiss()
    host.close()


def test_inline_notification_width_follows_its_content(qt_app):
    host = _host(qt_app)
    lane = QWidget(host)
    lane.setGeometry(100, 60, 700, 40)
    lane.show()
    qt_app.processEvents()

    short = LiquidNotification(lane, inline=True)
    short.show_notification("Convert", "3 fichiers traités")
    _, short_width, _, _ = short._metrics()
    long = LiquidNotification(lane, inline=True)
    long.show_notification("Un titre vraiment beaucoup plus long que les autres",
                           "avec un message encore plus long pour tester la largeur")
    _, long_width, _, _ = long._metrics()

    assert 0 < short_width < long_width <= lane.width()
    short.dismiss()
    long.dismiss()
    host.close()


def test_notify_window_prefers_the_visible_notification_lane(qt_app):
    host = _host(qt_app)
    lane = QWidget(host)
    lane.setGeometry(250, 60, 300, 40)
    lane.show()
    host.notification_lane = lane
    qt_app.processEvents()

    toast = notify_window(lane, "Convert", "Premier")
    assert toast.parentWidget() is lane and toast.inline

    toast.dismiss()
    lane.resize(80, 40)                       # trop étroite : retour au toast de fenêtre
    qt_app.processEvents()
    fallback = notify_window(host, "Convert", "Deuxième")
    assert fallback.parentWidget() is host and not fallback.inline
    fallback.dismiss()
    host.close()


def test_toast_hides_automatically_after_its_duration(qt_app):
    host = _host(qt_app)
    toast = LiquidNotification(host)
    toast.total_ms = 80
    toast.show_notification("Convert", "3 fichiers traités")
    assert toast.is_animating()
    assert _wait_for(qt_app, lambda: not toast.is_animating(), timeout=1.0)
    assert not toast.isVisible()
    host.close()


def test_reduced_motion_shows_the_toast_without_the_drop(qt_app, manager):
    manager.set_reduce_motion(True)
    try:
        host = _host(qt_app)
        toast = LiquidNotification(host)
        toast.show_notification("Convert", "3 fichiers traités")
        qt_app.processEvents()
        assert toast.is_animating() and toast._reduced
        assert not toast.grab().isNull()
        toast.dismiss()
        host.close()
    finally:
        manager.set_reduce_motion(False)


def test_persistent_notification_stays_until_it_is_closed(qt_app):
    """La carte ne s'efface jamais seule : elle attend le clic sur sa croix."""
    host = _host(qt_app)
    text = "Merci d'avoir téléchargé cette application !\n\nDeuxième paragraphe."
    card = notify_persistent(host, "Merci !", text)
    qt_app.processEvents()

    assert card is not None and card.isVisibleTo(host)
    assert card.title() == "Merci !" and card.message() == text
    assert card.geometry().top() >= 0 and card.width() <= host.width()
    assert not card.grab().isNull()

    assert _wait_for(qt_app, lambda: card._progress >= 0.999)   # fin de l'ouverture
    qt_app.processEvents()                     # le temps passe : rien ne la retire
    assert card.isVisible()

    closed: list[int] = []
    card.closed.connect(lambda: closed.append(1))
    card.close_button.click()
    assert _wait_for(qt_app, lambda: closed == [1], timeout=1.0)
    host.close()


def test_persistent_notification_opens_and_closes_as_a_splash(qt_app):
    """La carte naît en goutte et se referme dans l'autre sens, le texte en fondu."""
    host = _host(qt_app)
    card = notify_persistent(host, "Merci !", "Un texte assez long.\n\nDeuxième paragraphe.")

    assert card._progress < 0.999
    assert card._splash_rect().width() < card._card_rect().width()
    assert card._opacity.opacity() == 0.0            # le texte attend la fin de l'ouverture

    assert _wait_for(qt_app, lambda: card._progress >= 0.999)
    assert card._opacity.opacity() == 1.0
    assert card._splash_rect() == card._card_rect()

    closed: list[int] = []
    card.closed.connect(lambda: closed.append(1))
    card.close_button.click()
    assert card._closing
    assert _wait_for(qt_app, lambda: card._progress < 0.5, timeout=0.5)
    assert card._splash_rect().width() < card._card_rect().width()
    assert _wait_for(qt_app, lambda: closed == [1], timeout=1.0)
    host.close()


def test_persistent_notification_returns_above_content_added_later(qt_app):
    """Un repli de module ou une page ajoutée après coup ne doit pas couvrir la carte."""
    host = _host(qt_app)
    card = notify_persistent(host, "Merci !", "Texte")
    assert _wait_for(qt_app, lambda: card._progress >= 0.999)

    cover = QLabel("Autre page", host)
    cover.setGeometry(0, 0, host.width(), host.height())
    cover.show()
    cover.raise_()

    assert _wait_for(qt_app, lambda: _is_on_top(host, card))
    assert card.isVisible()
    host.close()


def test_persistent_notification_can_be_closed_while_it_opens(qt_app):
    host = _host(qt_app)
    card = notify_persistent(host, "Merci !", "Texte")
    assert card._progress < 0.999

    closed: list[int] = []
    card.closed.connect(lambda: closed.append(1))
    card.close_button.click()
    assert _wait_for(qt_app, lambda: closed == [1], timeout=1.0)
    host.close()


def test_persistent_notification_skips_the_splash_when_motion_is_reduced(qt_app, manager):
    manager.set_reduce_motion(True)
    try:
        host = _host(qt_app)
        card = notify_persistent(host, "Merci !", "Texte")
        qt_app.processEvents()
        assert card._progress == 1.0 and card._opacity.opacity() == 1.0
        assert card._splash_rect() == card._card_rect()

        card.close_button.click()
        assert not card.isVisible()
        host.close()
    finally:
        manager.set_reduce_motion(False)


def test_persistent_notification_is_unique_per_window(qt_app):
    host = _host(qt_app)
    first = notify_persistent(host, "Merci !", "Texte")
    second = notify_persistent(host, "Autre", "Autre texte")
    qt_app.processEvents()
    assert first is second and second.title() == "Autre"

    second.close_button.click()
    host.close()
