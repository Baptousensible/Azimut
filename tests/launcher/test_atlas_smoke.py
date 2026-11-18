"""Tests de fumée d'Azimut, hors écran, avec de faux modules."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import types
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QMouseEvent, QPalette, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QPushButton, QTabBar, QWidget,
)

from azimut.core import binaries
from azimut.core.history import HistoryEntry, HistoryStore
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.screenfit import fit_to_screen
from azimut.core.storage import LocalJsonStorage, get_storage, set_storage
from azimut.launcher import atlas_window, dashboard
from azimut.launcher import atlas_window as atlas_mod
from azimut.ui.notification import notify_window


def _spec(module_id: str, order: int = 10, requires: tuple[str, ...] = (), icon: str = "🧪") -> ModuleSpec:
    return ModuleSpec(id=module_id, name=module_id.upper(), tagline="Module factice.",
                      icon=icon, entry=f"fake.{module_id}:Window", requires=requires, order=order)


def _missing(name: str, prefs=None) -> binaries.BinaryInfo:
    return binaries.BinaryInfo(name, None, "missing", "")


def _present(name: str, prefs=None) -> binaries.BinaryInfo:
    return binaries.BinaryInfo(name, Path("/x") / name, "system", "1.0")


class FakeWindow(QMainWindow):
    busyChanged = Signal(bool)

    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.received: list[str] = []
        self.embedded = False

    def set_embedded(self, embedded):
        self.embedded = bool(embedded)

    def open_paths(self, paths):
        self.received.extend(paths)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolation(qapp, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    previous = get_storage()
    set_storage(LocalJsonStorage(tmp_path / "storage"))
    monkeypatch.setattr(binaries, "resolve", _missing)
    monkeypatch.setattr(binaries, "refresh",
                        lambda prefs=None: {n: _missing(n) for n in binaries.KNOWN_BINARIES})
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(dashboard, "user_log_dir", lambda: tmp_path / "logs")
    yield
    set_storage(previous)


@pytest.fixture
def make_atlas():
    created: list[atlas_window.AtlasWindow] = []

    def factory(specs, errors=None, **kwargs):
        window = atlas_window.AtlasWindow(specs, errors or {}, **kwargs)
        created.append(window)
        return window

    yield factory
    for window in created:
        window.close()
        window.deleteLater()
    # Les suppressions différées doivent être traitées avant le test suivant : sinon
    # elles s'accumulent et leurs callbacks différés ralentissent la boucle d'événements.
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


@pytest.fixture
def themed_app(qapp):
    """Applique le thème à l'application, puis rend son état initial au test suivant.

    Le thème n'est pas installé globalement par défaut : les tests qui ont besoin d'une
    échelle précise l'appliquent ici (`themed_app.set_zoom(...)`), sans laisser derrière
    eux le zoom, la police, la palette ou la feuille de style du test.
    """
    from azimut.ui.theme import ThemeManager

    manager = ThemeManager.instance()
    previous_zoom = manager.zoom()
    previous_qss, previous_font, previous_palette = (
        qapp.styleSheet(), QFont(qapp.font()), QPalette(qapp.palette()))
    previous_app, previous_base_font = manager._app, manager._base_font
    manager.apply(qapp)
    yield manager
    manager.set_zoom(previous_zoom)
    manager._app, manager._base_font = previous_app, previous_base_font
    qapp.setStyleSheet(previous_qss)
    qapp.setFont(previous_font)
    qapp.setPalette(previous_palette)


def test_two_cards_sorted(make_atlas):
    win = make_atlas([_spec("fb", 20), _spec("fa", 10, ("ffmpeg",))])
    assert list(win.cards) == ["fa", "fb"]
    assert all(c.open_button.isEnabled() for c in win.cards.values())
    assert list(win.cards["fa"].chips) == ["ffmpeg"]
    assert win.windowTitle() == "Azimut"


def test_header_uses_the_azimut_emblem_and_brand_font(make_atlas):
    win = make_atlas([_spec("fa")])
    assert win.title_label.text() == "Azimut"
    assert win.title_label.font().family() == "Montserrat"
    assert win.logo_label.width() == win.HEADER_WORDMARK
    pixmap = win.logo_label.pixmap()
    assert not pixmap.isNull()
    # L'emblème est plus large que haut : le lettrage sous le pictogramme a disparu.
    assert pixmap.width() > pixmap.height()


def test_hidden_personal_note_opens_as_a_persistent_notification(make_atlas, monkeypatch):
    opened: list[tuple[object, str, str]] = []

    def fake_notify(origin, title, message=""):
        opened.append((origin, title, message))

    monkeypatch.setattr(atlas_window, "notify_persistent", fake_notify)
    win = make_atlas([_spec("fa")])
    win.show()
    QApplication.processEvents()

    for widget in (win.title_label, win.logo_label):
        assert widget.toolTip() == ""
        assert widget.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        QApplication.processEvents()

    assert [origin for origin, _title, _text in opened] == [win, win]
    assert opened[0][1] == "Merci !"
    assert opened[0][2].startswith("Merci d'avoir téléchargé cette application")


def test_personal_note_stays_above_the_pages(make_atlas):
    """La carte « Merci ! » reste devant quand on change de page juste après l'ouverture."""
    win = make_atlas([_spec("fa")])
    win.show()
    QApplication.processEvents()
    win._show_personal_note()
    card = win._azimut_persistent_notification
    assert card is not None and card.isVisible()

    def assert_on_top(label: str) -> None:
        centre = card.mapTo(win, QPoint(card.width() // 2, card.height() // 2))
        found = win.childAt(centre)
        while found is not None and found is not card:
            found = found.parentWidget()
        assert found is card, f"{label} couvre la notification"
        assert card.isVisible()

    for label, index in (("Journaux", win.PAGE_ACTIVITY), ("Conseils", win.PAGE_TIPS),
                         ("Préférences", win.PAGE_PREFERENCES), ("Outils", win.PAGE_TOOLS)):
        win.show_page(index)
        QApplication.processEvents()
        assert_on_top(label)

    activity = win.pages.widget(win.PAGE_ACTIVITY)
    win.show_page(win.PAGE_ACTIVITY)
    for index in range(activity.count()):
        activity.setCurrentIndex(index)
        QApplication.processEvents()
        assert_on_top("l'onglet %s" % activity.tabText(index))


def test_tips_and_preferences_are_pages(make_atlas):
    """Les anciens dialogues s'ouvrent désormais dans la navigation principale."""
    win = make_atlas([_spec("fa")])
    assert win.pages.currentIndex() == win.PAGE_TOOLS
    assert not hasattr(win, "tips_button") and not hasattr(win, "prefs_button")

    win.page_buttons[win.PAGE_TIPS].click()
    assert win.pages.currentIndex() == win.PAGE_TIPS
    assert win.pages.currentWidget() is win.tips_page
    assert win.page_buttons[win.PAGE_TIPS].isChecked()
    assert [module_id for module_id, _name, _desc in win.tips_page.steps] == [
        "takeout", "dedup", "convert", "rename", "scope", "tags"]

    win.page_buttons[win.PAGE_PREFERENCES].click()
    assert win.pages.currentIndex() == win.PAGE_PREFERENCES
    assert win.pages.currentWidget() is win.preferences_page
    assert win.page_buttons[win.PAGE_PREFERENCES].isChecked()


def test_preferences_save_stays_on_the_page_and_cancel_restores_the_saved_state(make_atlas):
    """« Enregistrer » applique sans quitter Préférences ; « Annuler » revient au dernier état."""
    from azimut.ui.theme import ThemeManager

    manager = ThemeManager.instance()
    original_zoom = manager.zoom()
    win = make_atlas([_spec("fa")])
    page = win.preferences_page
    cancel = next(button for button in page.findChildren(QPushButton)
                  if button.text() == "Annuler" and button.property("kind") == "secondary")
    try:
        win.show()
        win.page_buttons[win.PAGE_PREFERENCES].click()
        QApplication.processEvents()
        assert win.pages.currentIndex() == win.PAGE_PREFERENCES
        assert page.isVisible()

        saved_index = (page.zoom_combo.currentIndex() + 1) % page.zoom_combo.count()
        saved_zoom = float(page.zoom_combo.itemData(saved_index))
        page.zoom_combo.setCurrentIndex(saved_index)
        page.save_button.click()
        QApplication.processEvents()

        # L'enregistrement ne navigue pas : la page reste affichée et le réglage est appliqué.
        assert win.pages.currentIndex() == win.PAGE_PREFERENCES
        assert win.pages.currentWidget() is page and page.isVisible()
        assert page.zoom_combo.currentData() == saved_zoom
        assert manager.zoom() == pytest.approx(saved_zoom)
        assert page._initial_zoom == pytest.approx(saved_zoom)

        # Une modification non enregistrée revient au dernier état validé.
        other_index = next(index for index in range(page.zoom_combo.count())
                           if float(page.zoom_combo.itemData(index)) != saved_zoom)
        page.zoom_combo.setCurrentIndex(other_index)
        assert page.zoom_combo.currentData() != saved_zoom
        cancel.click()
        QApplication.processEvents()

        assert win.pages.currentIndex() == win.PAGE_PREFERENCES
        assert page.zoom_combo.currentData() == saved_zoom
        assert manager.zoom() == pytest.approx(saved_zoom)
        assert page._initial_zoom == pytest.approx(saved_zoom)
    finally:
        manager.set_zoom(original_zoom)


def test_shutdown_ui_destroys_the_window(qapp):
    """La fenêtre doit être détruite avant la fin de Python (sinon SIGBUS à la sortie)."""
    import shiboken6

    from azimut.ui.app import shutdown_ui

    win = atlas_window.AtlasWindow([_spec("fa")], {})
    card = win.cards["fa"]
    shutdown_ui(qapp, win)
    assert not shiboken6.isValid(win)
    assert not shiboken6.isValid(card)


def test_busy_modules_are_named(make_atlas):
    win = make_atlas([_spec("fa"), _spec("fb", 20)])
    win._tab_busy.update({"fb", "fa"})
    assert win._busy_module_names() == ["FA", "FB"]


def test_close_asks_before_interrupting_a_treatment(make_atlas, monkeypatch):
    win = make_atlas([_spec("fa")])
    win.show()
    win._tab_busy.add("fa")
    asked: list[bool] = []
    monkeypatch.setattr(win, "_confirm_close_with_busy", lambda: asked.append(True) or False)
    assert not win.close()
    assert asked == [True]
    assert win.isVisible()
    assert not win._shutting_down
    win._tab_busy.clear()          # le nettoyage de fixture doit pouvoir fermer la fenêtre


def test_close_proceeds_after_confirmation(make_atlas, monkeypatch):
    win = make_atlas([_spec("fa")])
    win.show()
    win._tab_busy.add("fa")
    monkeypatch.setattr(win, "_confirm_close_with_busy", lambda: True)
    assert win.close()
    assert win._shutting_down
    assert not win.isVisible()


def test_close_without_busy_does_not_ask(make_atlas, monkeypatch):
    win = make_atlas([_spec("fa")])
    win.show()

    def unexpected():
        raise AssertionError("aucune confirmation attendue sans traitement")

    monkeypatch.setattr(win, "_confirm_close_with_busy", unexpected)
    assert win.close()


def test_restart_spawns_a_new_instance_and_closes(make_atlas, monkeypatch):
    spawned: list[bool] = []
    monkeypatch.setattr(atlas_window, "restart_application", lambda: spawned.append(True) or True)
    win = make_atlas([_spec("fa")])
    win.show()
    assert win.restart()
    assert spawned == [True]
    assert win._shutting_down and not win.isVisible()


def test_restart_is_cancelled_when_a_treatment_is_busy(make_atlas, monkeypatch):
    win = make_atlas([_spec("fa")])
    win.show()
    win._tab_busy.add("fa")
    monkeypatch.setattr(win, "_confirm_close_with_busy", lambda: False)
    spawned: list[bool] = []
    monkeypatch.setattr(atlas_window, "restart_application", lambda: spawned.append(True) or True)
    assert not win.restart()
    assert not spawned and win.isVisible()
    win._tab_busy.clear()


def test_tools_cards_are_inset_below_the_tab_bar(make_atlas):
    win = make_atlas([_spec("fa")])
    assert win.tools_home.widget().layout().contentsMargins().top() >= 8


def test_tools_cards_are_inset_like_embedded_modules(make_atlas):
    """La grille commence au même retrait gauche que le contenu d'un module ouvert."""
    win = make_atlas([_spec("fa")])
    margins = win.tools_home.widget().layout().contentsMargins()
    assert (margins.left(), margins.right()) == (16, 16)


def test_tools_cards_are_narrower_taller_and_centered(make_atlas):
    """Les cartes forment un bloc au nombre d'or, plus étroit que la page et centré."""
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1250, 900)
    win.show()
    QTest.qWait(100)

    page = win.tools_home.widget()
    center = win._cards_center
    card = next(iter(win.cards.values()))
    gap = win._cards_grid.horizontalSpacing()
    columns = win._card_columns
    assert gap == 28 and columns == 3
    # Le bloc se règle sur les cartes dorées, jamais au-delà des trois quarts de la page.
    assert abs(center.width() - (columns * card.width() + (columns - 1) * gap)) <= 1
    assert center.width() <= round(page.width() * 0.75)
    assert card.width() / card.height() == pytest.approx(atlas_window.ModuleCard.ASPECT, abs=0.03)
    right_gap = page.width() - (center.x() + center.width())
    assert abs(center.x() - right_gap) <= 1
    assert win.tools_hint.width() == center.width()
    assert win.tools_hint.alignment() & Qt.AlignmentFlag.AlignHCenter
    heights = {card.height() for card in win.cards.values()}
    assert len(heights) == 1
    height = heights.pop()
    assert 148 <= height <= 320
    assert win.tools_home.verticalScrollBar().maximum() == 0
    for card in win.cards.values():
        assert card.width() == round((center.width() - (columns - 1) * gap) / columns)


def test_tools_cards_adapt_to_the_window_height(make_atlas):
    """Les cartes se répartissent l'espace vertical et suivent les redimensionnements."""
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1250, win.DEFAULT_HEIGHT)
    win.show()
    QTest.qWait(150)
    compact = {card.height() for card in win.cards.values()}
    assert len(compact) == 1
    compact_height = compact.pop()
    assert compact_height > 148                        # l'ouverture garde des cartes hautes
    assert win.tools_home.verticalScrollBar().maximum() == 0
    viewport = win.tools_home.viewport()
    top = win._cards_center.mapTo(viewport, QPoint(0, 0)).y()
    bottom = viewport.height() - (top + win._cards_center.height())
    assert abs(top - bottom) <= 1                     # les rangées restent centrées

    win.resize(1250, 1100)
    QTest.qWait(150)
    tall = {card.height() for card in win.cards.values()}
    assert len(tall) == 1
    tall_height = tall.pop()
    assert tall_height > compact_height
    assert tall_height <= 320
    assert win.tools_home.verticalScrollBar().maximum() == 0


def test_tools_cards_do_not_overlap_on_a_short_wide_window(make_atlas):
    """Fenêtre large et basse : deux rangées nettes, cartes alignées, « Ouvrir » visible.

    La grille est un layout imbriqué : sa colonne pouvait recevoir une hauteur périmée et
    les rangées se chevauchaient, rognant le bas des cartes.
    """
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1600, 870)
    win.show()
    QTest.qWait(200)

    cards = list(win.cards.values())
    tops = sorted({card.y() for card in cards})
    assert len(tops) == 2                                  # une seule ordonnée par rangée
    assert tops[1] - tops[0] >= cards[0].height() + win._cards_grid.verticalSpacing()
    for card in cards:
        assert card.open_button.isVisible()
        assert card.open_button.geometry().bottom() <= card.height()
    assert win.tools_home.verticalScrollBar().maximum() == 0


def test_tools_cards_keep_the_golden_ratio_when_the_window_grows(make_atlas):
    """Agrandir la fenêtre agrandit les cartes, sans les aplatir."""
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1250, 900)
    win.show()
    QTest.qWait(120)
    compact = next(iter(win.cards.values())).size()

    win.resize(1250, 1100)
    QTest.qWait(120)
    tall = next(iter(win.cards.values())).size()

    for size in (compact, tall):
        assert size.width() / size.height() == pytest.approx(
            atlas_window.ModuleCard.ASPECT, abs=0.03)
    assert tall.height() > compact.height()
    assert tall.width() > compact.width()
    assert win.tools_home.verticalScrollBar().maximum() == 0


def test_tools_grid_uses_three_columns_on_a_wide_short_window(make_atlas):
    """Fenêtre large et basse : une troisième colonne plutôt que des cartes aplaties."""
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1800, 900)
    win.show()
    QTest.qWait(150)

    assert win._card_columns == 3
    grid = win._cards_grid
    positions = [grid.getItemPosition(grid.indexOf(card))[:2] for card in win.cards.values()]
    assert {row for row, _ in positions} == {0, 1}
    assert {column for _, column in positions} == {0, 1, 2}
    card = next(iter(win.cards.values()))
    assert card.width() / card.height() == pytest.approx(
        atlas_window.ModuleCard.ASPECT, abs=0.03)
    assert win.tools_home.verticalScrollBar().maximum() == 0


def test_narrow_window_keeps_the_golden_cards_in_one_column(make_atlas):
    """Deux modules sur une fenêtre étroite : une colonne, jamais une carte écrasée."""
    win = make_atlas([_spec("fa"), _spec("fb")])
    win.resize(640, 900)
    win.show()
    QTest.qWait(120)

    assert win._card_columns == 1
    card = next(iter(win.cards.values()))
    assert card.width() / card.height() == pytest.approx(
        atlas_window.ModuleCard.ASPECT, abs=0.03)
    assert win.tools_home.verticalScrollBar().maximum() == 0


@pytest.mark.parametrize("zoom", [1.25, 1.5])
def test_golden_cards_follow_the_zoom(make_atlas, themed_app, zoom):
    """Le format doré se recalcule à chaque échelle : mêmes proportions, tailles en pixels."""
    from azimut.ui.theme import scaled

    themed_app.set_zoom(zoom)
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1250, 1100)
    win.show()
    QTest.qWait(150)

    card = next(iter(win.cards.values()))
    assert card.width() / card.height() == pytest.approx(
        atlas_window.ModuleCard.ASPECT, abs=0.03)
    assert card.height() > scaled(atlas_window.ModuleCard.CARD_MIN_HEIGHT)


def test_cards_never_clip_their_content(make_atlas):
    """Le bouton « Ouvrir » reste visible : aucune carte n'est plus courte que son contenu."""
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.show()
    for width, height in ((1600, 870), (1250, 900), (1800, 1200), (900, 700)):
        win.resize(width, height)
        QTest.qWait(150)
        for card in win.cards.values():
            assert (card.height() + atlas_window.ModuleCard.CONTENT_SLACK
                    >= card.layout().totalHeightForWidth(card.width()))


def test_a_card_grows_to_its_content_instead_of_clipping(make_atlas):
    """Une hauteur trop courte est relevée jusqu'au contenu, jamais rognée."""
    win = make_atlas([_spec("fa")])
    card = next(iter(win.cards.values()))
    needed = card.layout().totalHeightForWidth(360)
    card.set_card_geometry(360, needed - 40)
    assert card.height() == needed - atlas_window.ModuleCard.CONTENT_SLACK


def test_a_stretched_hint_does_not_shrink_the_cards(make_atlas):
    """Seuls le texte et la largeur de la consigne comptent, pas son étirement."""
    win = make_atlas([_spec(f"m{i}", i) for i in range(6)])
    win.resize(1250, 900)
    win.show()
    QTest.qWait(150)
    card = next(iter(win.cards.values()))
    before = (card.width(), card.height())

    # L'étiquette s'étire dans la page tant que la grille n'a pas pris sa place.
    win.tools_hint.resize(win.tools_hint.width(), before[1] + 200)
    win._fitted_cards = None
    win._fit_tools_cards()
    QTest.qWait(120)
    assert (card.width(), card.height()) == before


def test_tools_page_shares_the_active_tab_surface(make_atlas):
    """La page des outils reprend la surface de l'onglet : plus de bande sombre sous celui-ci."""
    from PySide6.QtGui import QPalette

    from azimut.ui.theme import token

    win = make_atlas([_spec("fa")])
    for widget in (win.tools_home.viewport(), win.tools_home.widget()):
        assert widget.autoFillBackground()
        assert widget.palette().color(QPalette.ColorRole.Base).name() == token("surface")


def test_tools_tab_bar_follows_the_merged_header(make_atlas):
    """Agencement par défaut : une rangée de navigation, puis les onglets."""
    win = make_atlas([_spec("fa")])
    win.show()
    QApplication.processEvents()
    assert win.page_switch.parentWidget() is win.header_nav_host
    assert win.page_bar_host.isHidden()
    bar = win.tools_page.tabBar()
    header_bottom = win.header_bar.mapTo(win, QPoint(0, win.header_bar.height())).y()
    bar_top = bar.mapTo(win, QPoint(0, 0)).y()
    assert 10 <= bar_top - header_bottom <= 14          # 12 px de respiration


def test_tools_tab_bar_touches_the_page_bar_in_classic_layout(make_atlas):
    """Ancien agencement restorable : la barre de pages touche les onglets."""
    win = make_atlas([_spec("fa")])
    win._set_top_layout(win.TOP_LAYOUT_CLASSIC, persist=False)
    win.show()
    QApplication.processEvents()
    assert win.page_switch.parentWidget() is win.page_bar_host
    assert not win.page_bar_host.isHidden()
    bar = win.tools_page.tabBar()
    button = win.page_buttons[win.PAGE_TOOLS]
    bar_top = bar.mapTo(win, QPoint(0, 0)).y()
    button_bottom = button.mapTo(win, QPoint(0, button.height())).y()
    assert 0 <= bar_top - button_bottom <= 2


def test_page_navigation_replaces_the_header_shortcuts(make_atlas):
    win = make_atlas([_spec("fa")])
    assert not hasattr(win, "tips_button") and not hasattr(win, "prefs_button")
    assert all(button.objectName() == "AtlasPageButton" for button in win.page_buttons.values())
    assert not hasattr(win, "theme_button")          # le thème se règle dans Préférences


def test_focus_ring_follows_the_focus_reason(make_atlas):
    """Tab allume l'anneau, un clic souris l'éteint (le focus reste pourtant sur la carte)."""
    from azimut.ui.theme import install_focus_ring_filter

    app = QApplication.instance()
    install_focus_ring_filter(app)
    win = make_atlas([_spec("fa")])
    win.show()
    win.activateWindow()
    app.processEvents()
    card = win.cards["fa"]

    card.setFocus(Qt.FocusReason.TabFocusReason)
    app.processEvents()
    assert card.hasFocus()
    assert card.property("keyboardFocus") is True

    card.clearFocus()
    app.processEvents()
    assert card.property("keyboardFocus") is False

    card.setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    assert card.hasFocus()
    assert card.property("keyboardFocus") is False


def test_clicking_a_keyboard_focused_card_clears_the_ring(make_atlas, monkeypatch):
    """Déjà focalisée au clavier, la carte ne reçoit pas de FocusIn : le clic doit éteindre l'anneau."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.show()
    win.activateWindow()
    QApplication.processEvents()
    card = win.cards["fa"]
    card.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert card.property("keyboardFocus") is True

    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert card.property("keyboardFocus") is False
    assert win.tools_page.count() == 2          # le clic ouvre bien l'onglet


def test_opening_a_module_moves_focus_off_the_card(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.show()
    win.activateWindow()
    QApplication.processEvents()
    card = win.cards["fa"]
    card.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert card.hasFocus()

    win.open_module("fa")
    QApplication.processEvents()
    assert win.tools_page.tabBar().hasFocus()
    assert card.property("keyboardFocus") is False


def test_header_compacts_when_a_module_tab_is_open(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    area = win.screen().availableGeometry()
    expected = fit_to_screen(win.DEFAULT_WIDTH, win.DEFAULT_HEIGHT, (area.width(), area.height()))
    assert (win.size().width(), win.size().height()) == expected
    win.show()
    QApplication.processEvents()
    assert win.title_label.text() == "Azimut"
    assert win.header_bar.width() == win.header_host.width()
    assert win.header_bar.height() == win.HEADER_WORDMARK + 2 * win.HEADER_VPAD
    assert win.page_switch.parentWidget() is win.header_nav_host
    assert win.page_bar_host.isHidden()

    win.open_module("fa")
    QTest.qWait(win.HEADER_ANIMATION_MS + 30)
    assert win._header_compact
    assert win.logo_label.width() == win.HEADER_COMPACT
    assert win.title_label.font().pixelSize() == win.HEADER_TITLE_COMPACT
    assert win.title_label.font().family() == "Montserrat"
    assert win.title_label.text() == "Azimut"
    assert win.title_label.graphicsEffect() is None
    assert win.header_bar.height() == win.HEADER_COMPACT + 2 * win.HEADER_VPAD_COMPACT
    # En agencement regroupé, la barre garde toute la largeur : la navigation y tient.
    assert win.header_bar.width() == win.header_host.width()
    assert win.subtitle_label.isHidden() and win.version_label.isHidden()
    assert win.page_switch.parentWidget() is win.header_nav_host
    assert win.page_bar_host.isHidden()

    # La largeur reste pleine quand la fenêtre est redimensionnée.
    win.resize(1200, 820)
    QApplication.processEvents()
    assert win.header_bar.width() == win.header_host.width()

    win._close_module_tab(1)
    QTest.qWait(win.HEADER_ANIMATION_MS + 30)
    assert not win._header_compact
    assert win.logo_label.width() == win.HEADER_WORDMARK
    assert win.title_label.font().pixelSize() == win.HEADER_TITLE
    assert win.title_label.font().family() == "Montserrat"
    assert win.title_label.text() == "Azimut"
    assert win.title_label.graphicsEffect() is None
    assert win.header_bar.width() == win.header_host.width()
    assert win.header_bar.height() == win.HEADER_WORDMARK + 2 * win.HEADER_VPAD
    assert win.page_switch.parentWidget() is win.header_nav_host
    assert win.page_bar_host.isHidden()
    # À 1200 px, le sous-titre et la version retrouvent leur place.
    assert not win.subtitle_label.isHidden() and not win.version_label.isHidden()


def test_page_selector_stays_anchored_in_the_header(make_atlas, monkeypatch):
    """Le sélecteur ne change jamais de parent : le compactage ne provoque pas de saut."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.show()
    QApplication.processEvents()
    win.open_module("fa")
    win._header_animation.stop()
    selector = win.page_switch
    x, width = selector.x(), selector.width()

    for progress in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
        win._apply_header_progress(progress)
        QApplication.processEvents()
        assert selector.parentWidget() is win.header_nav_host
        assert win.page_bar_host.isHidden()
        assert selector.x() == x and selector.width() == width
        switch_rect = QRect(selector.mapTo(win, QPoint(0, 0)), selector.size())
        bar_rect = QRect(win.header_bar.mapTo(win, QPoint(0, 0)), win.header_bar.size())
        assert bar_rect.contains(switch_rect), progress


def test_notifications_use_the_free_center_of_the_header(make_atlas, monkeypatch):
    """Les capsules naissent entre le nom et la navigation, pas au-dessus de l'en-tête."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    win.open_module("fa")
    QTest.qWait(win.HEADER_ANIMATION_MS + 30)

    lane = win.notification_lane
    lane_x = lane.mapTo(win, QPoint(0, 0)).x()
    title_right = win.title_label.mapTo(win, QPoint(win.title_label.width(), 0)).x()
    switch_x = win.page_switch.mapTo(win, QPoint(0, 0)).x()
    assert lane.width() > 300 and title_right <= lane_x < switch_x

    toast = notify_window(win._windows["fa"], "FA", "Traitement terminé.")
    QApplication.processEvents()
    assert toast.parentWidget() is lane and toast.inline and toast.is_animating()
    assert toast.geometry() == lane.rect()
    toast.dismiss()

    win.resize(851, 744)                      # encore assez de place pour la capsule
    QTest.qWait(100)
    narrow = notify_window(win._windows["fa"], "FA", "Traitement terminé.")
    QApplication.processEvents()
    assert narrow.parentWidget() is lane and narrow.inline
    narrow.dismiss()

    win.resize(776, 744)                      # le centre devient trop étroit : repli lisible
    QTest.qWait(100)
    fallback = notify_window(win._windows["fa"], "FA", "Traitement terminé.")
    QApplication.processEvents()
    assert fallback.parentWidget() is win.centralWidget() and not fallback.inline
    fallback.dismiss()


def test_top_layout_is_reversible_and_persisted(make_atlas):
    """Le menu « Présentation » revient à l'ancien agencement et mémorise le choix."""
    win = make_atlas([_spec("fa")])
    win.show()
    QApplication.processEvents()
    assert win._top_layout == win.TOP_LAYOUT_MERGED and win.top_layout_action.isChecked()

    win.top_layout_action.setChecked(False)
    QApplication.processEvents()
    assert win._top_layout == win.TOP_LAYOUT_CLASSIC
    assert win.page_switch.parentWidget() is win.page_bar_host
    assert not win.page_bar_host.isHidden() and win.header_nav_host.isHidden()
    assert Preferences.instance().get("top_layout") == "classic"

    win.top_layout_action.setChecked(True)
    QApplication.processEvents()
    assert win._top_layout == win.TOP_LAYOUT_MERGED
    assert win.page_switch.parentWidget() is win.header_nav_host
    assert win.page_bar_host.isHidden()
    assert Preferences.instance().get("top_layout") == "merged"


@pytest.mark.parametrize("zoom", [1.25, 1.5])
def test_narrow_window_at_high_zoom_falls_back_to_classic_without_widening(make_atlas, themed_app, zoom):
    """À 125 % et 150 %, une fenêtre étroite bascule d'elle-même vers l'ancien agencement."""
    themed_app.set_zoom(1.0)
    win = make_atlas([_spec("fa")])
    win.show()
    QTest.qWait(50)
    win.resize(850, 800)
    QTest.qWait(50)
    assert win._top_layout == win.TOP_LAYOUT_MERGED
    assert win.width() == 850

    themed_app.set_zoom(zoom)
    QTest.qWait(80)

    # Le repli est effectif, sans élargir la fenêtre, et le choix utilisateur reste
    # « en-tête regroupé » pour le jour où la fenêtre redeviendra assez large.
    assert win.width() == 850
    assert win._top_layout == win.TOP_LAYOUT_CLASSIC
    assert win._layout_preference == win.TOP_LAYOUT_MERGED
    assert win.page_bar_host.isVisible()
    assert win.header_nav_host.isHidden()
    assert win.page_switch.width() <= win.page_bar_host.width()

    win.resize(1200, 800)
    QTest.qWait(50)
    assert win._top_layout == win.TOP_LAYOUT_MERGED
    assert not win.page_bar_host.isVisible()
    assert win.header_nav_host.isVisible()


def test_default_opening_at_100_percent_keeps_the_computed_width(make_atlas, themed_app, monkeypatch):
    """Ouverture par défaut, échelle 100 % : la fenêtre garde la taille calculée pour l'écran.

    Au démarrage, ``create_app()`` applique le zoom mémorisé dans les préférences — 100 %
    par défaut — avant de construire la fenêtre : aucun redimensionnement ne doit suivre.
    L'écran est neutralisé : la CI Qt hors écran mesure 800×800, où la largeur calculée
    déclenche légitimement le repli vers l'ancien agencement.
    """
    monkeypatch.setattr(atlas_mod, "fit_to_screen", lambda w, h, avail: (w, h))
    assert Preferences.instance().get("zoom", 1.0) == pytest.approx(1.0)   # échelle par défaut
    themed_app.set_zoom(1.0)
    win = make_atlas([_spec("fa")])
    win.show()
    QTest.qWait(120)

    assert themed_app.zoom() == pytest.approx(1.0)
    assert win.width() == win.DEFAULT_WIDTH
    assert win._top_layout == win.TOP_LAYOUT_MERGED
    assert win.header_nav_host.isVisible() and win.page_bar_host.isHidden()


def test_narrow_opening_at_100_percent_falls_back_instead_of_widening(make_atlas, themed_app):
    """À 100 %, une fenêtre plus étroite que l'en-tête regroupé se replie au lieu d'être élargie.

    Sans repli, Qt élargissait la fenêtre jusqu'au minimum de l'en-tête regroupé (700 → 775 px).
    L'ancien agencement tient, lui, dans cette largeur : il est préféré à une fenêtre agrandie.
    """
    themed_app.set_zoom(1.0)                 # échelle par défaut : le repli n'est pas lié au zoom
    win = make_atlas([_spec("fa")])
    win.resize(700, 640)
    win.show()
    QTest.qWait(120)                         # le repli s'applique après la première frame

    assert win.width() == 700
    assert win._top_layout == win.TOP_LAYOUT_CLASSIC
    assert win._layout_preference == win.TOP_LAYOUT_MERGED
    assert win.page_bar_host.isVisible() and win.header_nav_host.isHidden()
    assert win.page_switch.width() <= win.page_bar_host.width()


@pytest.mark.parametrize("zoom", [1.25, 1.5])
def test_direct_opening_at_high_zoom_keeps_the_requested_width(make_atlas, themed_app, zoom):
    """Ouverte directement à 125 % ou 150 %, une fenêtre de 850 px garde sa largeur.

    Le zoom est déjà actif quand la fenêtre est construite, comme au démarrage avec une
    préférence d'échelle enregistrée (100 % par défaut), et non modifié à chaud comme dans
    le test précédent.
    """
    themed_app.set_zoom(zoom)                # le zoom est déjà actif au moment d'ouvrir
    assert themed_app.zoom() == pytest.approx(zoom)
    win = make_atlas([_spec("fa")])
    win.resize(850, 800)
    win.show()
    QTest.qWait(120)                         # le repli s'applique après la première frame

    # Le repli est effectif, mais la fenêtre n'est ni rétrécie (776 px) ni élargie
    # (858 px) : elle garde la largeur demandée.
    assert win.width() == 850
    assert win._top_layout == win.TOP_LAYOUT_CLASSIC
    assert win._layout_preference == win.TOP_LAYOUT_MERGED
    assert win.page_bar_host.isVisible() and win.header_nav_host.isHidden()
    assert win.page_switch.width() <= win.page_bar_host.width()


def test_switching_layout_while_a_module_is_open_keeps_the_window_width(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    win.open_module("fa")
    QTest.qWait(win.HEADER_ANIMATION_MS + 30)
    width = win.width()

    win.top_layout_action.setChecked(False)
    QTest.qWait(50)
    assert win.width() == width
    assert win.page_switch.parentWidget() is win.page_bar_host
    assert win.page_bar_host.isVisible()
    assert win.header_bar.width() < win.header_host.width()   # compact à mi-largeur

    win.top_layout_action.setChecked(True)
    QTest.qWait(50)
    assert win.width() == width
    assert win.page_switch.parentWidget() is win.header_nav_host
    assert win.page_bar_host.isHidden()
    assert win.header_bar.width() == win.header_host.width()


def test_classic_layout_is_restored_at_startup(make_atlas):
    Preferences.instance().set("top_layout", "classic")
    try:
        win = make_atlas([_spec("fa")])
        win.show()
        QApplication.processEvents()
        assert win._top_layout == win.TOP_LAYOUT_CLASSIC
        assert not win.top_layout_action.isChecked()
        assert win.page_switch.parentWidget() is win.page_bar_host
    finally:
        Preferences.instance().set("top_layout", "merged")


def test_header_details_hide_when_width_is_missing(make_atlas):
    win = make_atlas([_spec("fa")])
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    assert not win.subtitle_label.isHidden()
    win.resize(1000, 820)
    QApplication.processEvents()
    assert win.subtitle_label.isHidden() and win.version_label.isHidden()
    win.resize(1200, 820)
    QApplication.processEvents()
    assert not win.subtitle_label.isHidden()


def test_header_subtitle_is_never_clipped(make_atlas):
    """Le sous-titre occupe sa largeur naturelle, ou disparaît : jamais coupé en plein mot."""
    win = make_atlas([_spec("fa")])
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    label = win.subtitle_label
    assert not label.isHidden()
    assert label.width() >= label.sizeHint().width()

    win.resize(1000, 820)
    QApplication.processEvents()
    assert label.isHidden()

    win.resize(1200, 820)
    QApplication.processEvents()
    assert not label.isHidden()
    assert label.width() >= label.sizeHint().width()


def test_header_and_tab_animations_share_the_same_timeline(make_atlas):
    """L'en-tête et la capsule d'onglet partent ensemble et se stabilisent ensemble."""
    win = make_atlas([_spec("fa")])
    bar = win.tools_page.tabBar()
    assert win.HEADER_ANIMATION_MS == bar.DURATION + bar.PAGE_SETTLE_MS
    assert win._header_animation.easingCurve().type() == bar._animation.easingCurve().type()


def test_header_title_stays_azimut_while_compacting(make_atlas):
    """Le titre ne change plus de mot pendant le compactage de l'en-tête."""
    win = make_atlas([_spec("fa")])
    for progress in (0.0, 0.25, 0.5, 0.75, 1.0):
        win._apply_header_progress(progress)
        assert win.title_label.text() == "Azimut"
        assert win.title_label.graphicsEffect() is None


def _wait_until(predicate, timeout_ms: int = 3000) -> bool:
    """Attend qu'une condition devienne vraie, avec une borne de temps.

    Les animations Qt suivent l'horloge murale : un ``QTest.qWait`` fixe suppose un
    runner rapide et échoue dès qu'il est chargé.
    """
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return False


def _drag_tab(bar, start: QPoint, end: QPoint, steps: int = 16, on_step=None) -> None:
    """Simule un vrai glisser de souris sur la barre d'onglets (bouton maintenu)."""
    app = QApplication.instance()
    global_start = bar.mapToGlobal(start)
    app.sendEvent(bar, QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(start), QPointF(global_start),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    for step in range(1, steps + 1):
        point = start + (end - start) * step / steps
        app.sendEvent(bar, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(point), QPointF(bar.mapToGlobal(point)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        if on_step is not None:
            on_step(step)
    app.sendEvent(bar, QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(end), QPointF(bar.mapToGlobal(end)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    app.processEvents()


def test_tools_home_tab_stays_fixed_when_grabbed_or_pushed(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20)])
    win.open_module("fa")
    win.open_module("fb")
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    before = [bar.tabText(index) for index in range(bar.count())]

    _drag_tab(bar, bar.tabRect(0).center(), bar.tabRect(2).center())
    assert [bar.tabText(index) for index in range(bar.count())] == before

    _drag_tab(bar, bar.tabRect(1).center(), bar.tabRect(0).center())
    assert bar.tabText(0) == "Tous les outils"
    assert [bar.tabText(index) for index in range(bar.count())] == before

    # Les modules, eux, restent réordonnables entre eux après l'onglet fixe.
    _drag_tab(bar, bar.tabRect(2).center(), bar.tabRect(1).center())
    assert bar.tabText(0) == "Tous les outils"
    assert [bar.tabText(index) for index in range(bar.count())] == [before[0], before[2], before[1]]


def _shift_click_tab(bar, index: int) -> None:
    """Maj+clic sur un onglet, sans changer l'onglet actif."""
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     bar.tabRect(index).center())
    QApplication.processEvents()


def test_shift_click_selects_a_range_without_switching_tabs(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30), _spec("fd", 40)])
    for module_id in ("fa", "fb", "fc", "fd"):
        win.open_module(module_id)
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    QApplication.processEvents()
    assert win.tools_page.currentIndex() == 2
    _shift_click_tab(bar, 3)

    assert win.tools_page.currentIndex() == 2
    assert bar.selected_ids() == ["fb", "fc"]
    assert bar.selection_count() == 2
    assert bar._selection_animating or bar._selection_progress == 1.0
    assert _wait_until(lambda: not bar._selection_animating)   # fin de l'animation de la plage
    assert bar._selection_rect.width() > bar._tab_rect(2).width()


def test_a_normal_click_collapses_the_multiple_selection(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30)])
    for module_id in ("fa", "fb", "fc"):
        win.open_module(module_id)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    _shift_click_tab(bar, 1)
    assert bar.selection_count() == 3

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    QApplication.processEvents()
    assert bar.selection_count() == 0


def test_close_button_closes_the_selected_group(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30), _spec("fd", 40)])
    for module_id in ("fa", "fb", "fc", "fd"):
        win.open_module(module_id)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    _shift_click_tab(bar, 1)
    assert bar.selected_ids() == ["fa", "fb"]

    close = bar.tabButton(2, QTabBar.ButtonPosition.RightSide)
    assert close.is_active()
    assert "2 onglets" in close.toolTip()
    close.click()
    QApplication.processEvents()

    assert [bar.tabText(index) for index in range(bar.count())] == ["Tous les outils", "FC", "FD"]
    assert set(win._windows) == {"fc", "fd"}
    assert bar.selection_count() == 0


def test_delete_key_closes_the_selected_group(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30)])
    for module_id in ("fa", "fb", "fc"):
        win.open_module(module_id)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    _shift_click_tab(bar, 1)
    assert bar.selection_count() == 3
    bar.setFocus()

    QTest.keyClick(bar, Qt.Key.Key_Delete)
    QApplication.processEvents()
    assert win.tools_page.count() == 1 and win.tools_page.tabText(0) == "Tous les outils"
    assert not win._windows


def test_delete_key_closes_the_active_tab(make_atlas, monkeypatch):
    """Un clic sur un onglet donne le focus à la barre : Suppr ferme cet onglet."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30)])
    for module_id in ("fa", "fb", "fc"):
        win.open_module(module_id)
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    QApplication.processEvents()
    assert bar.currentIndex() == 2
    assert bar.hasFocus()

    QTest.keyClick(bar, Qt.Key.Key_Delete)
    QApplication.processEvents()
    assert [bar.tabText(index) for index in range(bar.count())] == ["Tous les outils", "FA", "FC"]
    assert set(win._windows) == {"fa", "fc"}


def test_shift_arrows_select_then_delete_several_tabs(make_atlas, monkeypatch):
    """Maj+flèches sélectionne une plage d'onglets, que Suppr ferme ensuite."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30)])
    for module_id in ("fa", "fb", "fc"):
        win.open_module(module_id)
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
    QApplication.processEvents()
    QTest.keyClick(bar, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(bar, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QApplication.processEvents()
    assert bar.currentIndex() == 3
    assert bar.selected_ids() == ["fa", "fb", "fc"]

    QTest.keyClick(bar, Qt.Key.Key_Delete)
    QApplication.processEvents()
    assert [bar.tabText(index) for index in range(bar.count())] == ["Tous les outils"]
    assert not win._windows


def test_dragging_a_selected_tab_moves_the_whole_group(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30), _spec("fd", 40)])
    for module_id in ("fa", "fb", "fc", "fd"):
        win.open_module(module_id)
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    _shift_click_tab(bar, 1)
    assert bar.selected_ids() == ["fa", "fb"]

    _drag_tab(bar, bar.tabRect(2).center(), bar.tabRect(4).center())

    texts = [bar.tabText(index) for index in range(bar.count())]
    assert texts[0] == "Tous les outils"
    first = texts.index("FA")
    assert texts.index("FB") == first + 1
    assert first > 1
    assert [text for text in texts[1:] if text not in ("FA", "FB")] == ["FC", "FD"]
    assert bar.selected_ids() == ["fa", "fb"]
    assert bar.tabText(bar.currentIndex()) == "FB"

    # Le groupe revient vers la gauche d'un seul bloc, en conservant son ordre.
    first = texts.index("FA")
    _drag_tab(bar, bar.tabRect(first + 1).center(), bar.tabRect(1).center())
    assert [bar.tabText(index) for index in range(bar.count())] == [
        "Tous les outils", "FA", "FB", "FC", "FD"]
    assert bar.selected_ids() == ["fa", "fb"]
    assert bar.tabText(bar.currentIndex()) == "FB"


def test_moving_a_selected_group_animates_the_tabs(make_atlas, monkeypatch):
    """Le bloc sélectionné glisse vers sa nouvelle place au lieu de sauter d'un coup."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30)])
    for module_id in ("fa", "fb", "fc"):
        win.open_module(module_id)
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    _shift_click_tab(bar, 1)
    assert bar.selected_ids() == ["fa", "fb"]
    bar._group_order = bar.selected_ids()
    bar._group_drag_current_id = "fb"

    bar._move_group_to(2)
    assert bar._move_animating and bar._move_shots
    QTest.qWait(60)
    QApplication.processEvents()
    mid = {tab_id: rect.x() for tab_id, rect in bar._displayed_cells().items()}
    final = {tab_id: rect.x() for tab_id, rect in bar._tab_cells().items()}
    assert any(mid[tab_id] != final[tab_id] for tab_id in final)     # en plein trajet

    QTest.qWait(bar.MOVE_DURATION + 40)
    QApplication.processEvents()
    assert not bar._move_animating
    assert {tab_id: rect.x() for tab_id, rect in bar._displayed_cells().items()} == final
    assert [bar.tabText(index) for index in range(bar.count())] == [
        "Tous les outils", "FC", "FA", "FB"]


def test_selected_group_stays_together_during_drag(make_atlas, monkeypatch):
    """Pendant le geste, les onglets sélectionnés restent un bloc compact."""
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20), _spec("fc", 30), _spec("fd", 40)])
    for module_id in ("fa", "fb", "fc", "fd"):
        win.open_module(module_id)
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    bar = win.tools_page.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(2).center())
    _shift_click_tab(bar, 1)
    assert bar.selected_ids() == ["fa", "fb"]

    snapshots: list[tuple[list[str], str]] = []

    def snapshot(_step: int) -> None:
        if bar._group_drag_moved:
            snapshots.append(([bar.tabText(index) for index in range(bar.count())],
                              bar.tabText(bar.currentIndex())))

    _drag_tab(bar, bar.tabRect(2).center(), bar.tabRect(4).center(), on_step=snapshot)

    assert snapshots
    for texts, current in snapshots:
        assert abs(texts.index("FA") - texts.index("FB")) == 1
        assert current == "FB"
    assert bar.selected_ids() == ["fa", "fb"]


def test_four_pages_and_default_tools(make_atlas):
    win = make_atlas([_spec("fa")])
    assert win.pages.count() == 4
    assert win.pages.currentIndex() == win.PAGE_TOOLS
    assert win.pages.currentWidget() is win.tools_page
    assert win.tools_page.count() == 1 and win.tools_page.tabText(0) == "Tous les outils"
    assert win.page_buttons[win.PAGE_TOOLS].isChecked()
    assert win.page_buttons[win.PAGE_TOOLS].property("kind") == "primary"
    assert [win.page_buttons[i].text() for i in range(4)] == [
        "Outils", "Journaux et historique", "Conseils", "Préférences"]

    for index, page in ((win.PAGE_ACTIVITY, win.dashboard),
                        (win.PAGE_TIPS, win.tips_page),
                        (win.PAGE_PREFERENCES, win.preferences_page)):
        win.page_buttons[index].click()
        assert win.pages.currentIndex() == index
        assert win.pages.currentWidget() is page
        assert win.page_buttons[index].isChecked()

    win.page_buttons[win.PAGE_TOOLS].click()
    assert win.pages.currentIndex() == win.PAGE_TOOLS


def test_secondary_pages_are_built_on_first_visit(make_atlas):
    """Conseils, Préférences et Journaux ne coûtent rien tant qu'on ne les ouvre pas."""
    win = make_atlas([_spec("fa")])
    assert win.pages.count() == 4
    placeholder = win.pages.widget(win.PAGE_TIPS)
    assert type(placeholder) is QWidget and placeholder is not win.tips_page

    page = win.tips_page
    assert win.pages.widget(win.PAGE_TIPS) is page
    assert win.tips_page is page                       # pas de reconstruction à la 2e lecture

    win.show_page(win.PAGE_PREFERENCES)
    assert win.pages.currentWidget() is win.preferences_page
    assert win.pages.widget(win.PAGE_PREFERENCES) is win.preferences_page

    win.show_page(win.PAGE_ACTIVITY)
    assert win.pages.currentWidget() is win.dashboard
    win.show_page(win.PAGE_TIPS)
    assert win.pages.currentWidget() is page


def test_page_switch_slides_content_with_the_spring(make_atlas):
    """Le contenu des quatre pages glisse au lieu de changer instantanément."""
    win = make_atlas([_spec("fa")])
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    assert win.pages.currentIndex() == win.PAGE_TOOLS

    win.show_page(win.PAGE_ACTIVITY)
    # L'onglet et le bouton sont déjà synchronisés, même pendant le trajet.
    assert win.pages.currentIndex() == win.PAGE_ACTIVITY
    assert win.pages.currentWidget() is win.dashboard
    assert win.pages.is_animating()
    QTest.qWait(100)
    assert 0.0 < win.pages.position() < 1.0
    QTest.qWait(850)
    QApplication.processEvents()
    assert not win.pages.is_animating()
    assert win.pages.currentWidget() is win.dashboard
    assert win.dashboard.x() == 0 and win.dashboard.width() == win.pages.width()
    assert win.tools_page.isHidden()


def test_failed_module_card_disabled(make_atlas):
    win = make_atlas([_spec("fa")], {"bad": "ImportError : boom"})
    card = win.cards["bad"]
    assert card.is_failed() and not card.open_button.isEnabled()
    assert "boom" in card.error_label.text()
    assert not win.cards["fa"].is_failed()


def test_warning_banner(make_atlas, monkeypatch):
    win = make_atlas([_spec("fa", 10, ("ffmpeg",))])
    assert not win.warning_banner.isHidden()
    assert "ffmpeg" in win.warning_banner.text()
    monkeypatch.setattr(binaries, "resolve", _present)
    win._refresh_binary_state()
    assert win.warning_banner.isHidden()


def test_open_module_reuses_window_and_forwards_paths(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    first = win.open_module("fa", ["a", "b"])
    assert isinstance(first, FakeWindow) and first.received == ["a", "b"]
    assert first.embedded is True
    assert win.tools_page.count() == 2 and win.tools_page.currentWidget() is first
    assert win.open_module("fa") is first
    win.cards["fa"].pathsDropped.emit("fa", ["c"])
    assert first.received == ["a", "b", "c"]
    win._on_module_closed("fa")
    assert "fa" not in win._windows
    assert win.tools_page.count() == 1


def test_background_open_adds_the_tab_without_leaving_the_tools_page(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    tab = win.open_module("fa", background=True)

    assert isinstance(tab, FakeWindow) and tab.embedded is True
    assert win.tools_page.count() == 2
    assert win.tools_page.currentIndex() == 0
    assert win.tools_page.currentWidget() is win.tools_home
    assert win._windows["fa"] is tab


def test_background_shortcut_opens_a_tab_without_switching(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20)])
    current = win.open_module("fa")
    target = QKeySequence("Ctrl+Shift+2")
    shortcut = next((sc for sc in win.findChildren(QShortcut) if sc.key() == target), None)
    assert shortcut is not None

    shortcut.activated.emit()
    QApplication.processEvents()
    assert win.tools_page.count() == 3
    assert win.tools_page.currentWidget() is current
    assert win._windows["fb"] is not current


def test_ctrl_click_opens_the_card_in_the_background(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.show()
    QApplication.processEvents()

    QTest.mouseClick(win.cards["fa"], Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert win.tools_page.count() == 2
    assert win.tools_page.currentWidget() is win.tools_home
    assert "fa" in win._windows


def test_ctrl_click_on_the_open_button_opens_in_the_background(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa"), _spec("fb", 20)])
    win.open_module("fa", background=True)
    win.show()
    QApplication.processEvents()

    QTest.mouseClick(win.cards["fb"].open_button, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert win.tools_page.count() == 3
    assert win.tools_page.currentWidget() is win.tools_home
    assert "fb" in win._windows

    # Sans Ctrl, le même bouton active bien l'onglet.
    QTest.mouseClick(win.cards["fb"].open_button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert win.tools_page.currentWidget() is win._windows["fb"]


def test_open_module_failure_disables_card(make_atlas, monkeypatch):
    def boom(spec):
        raise ImportError("introuvable")

    monkeypatch.setattr(atlas_window, "load_window_class", boom)
    monkeypatch.setattr(atlas_window, "QMessageBox", types.SimpleNamespace(warning=lambda *a, **k: None))
    win = make_atlas([_spec("fa")])
    assert win.open_module("fa") is None
    assert win.cards["fa"].is_failed() and not win.cards["fa"].open_button.isEnabled()
    assert "introuvable" in win.cards["fa"].error_label.text()


def test_module_tab_shows_tool_icon_and_zooms_while_busy(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa", icon="search")])
    tab = win.open_module("fa")
    assert tab is not None
    index = win.tools_page.indexOf(tab)
    assert index > 0 and win.tools_page.tabText(index) == "FA"
    assert not win.tools_page.tabIcon(index).isNull()

    tab.busyChanged.emit(True)
    assert "fa" in win._tab_busy and win._tab_icon_timer.isActive()
    QTest.qWait(win.TAB_ZOOM_INTERVAL_MS * 2 + 10)
    assert win._tab_icon_timer.isActive()
    tab.busyChanged.emit(False)
    assert "fa" not in win._tab_busy and not win._tab_icon_timer.isActive()


def test_module_tabs_can_be_closed(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.open_module("fa")
    assert win.tools_page.count() == 2
    win._close_module_tab(1)
    assert win.tools_page.count() == 1
    assert "fa" not in win._windows


def test_busy_module_names_can_be_limited_to_one_tab(make_atlas):
    win = make_atlas([_spec("fa"), _spec("fb", 20)])
    win._tab_busy.update({"fa", "fb"})
    assert win._busy_module_names({"fb"}) == ["FB"]


def test_closing_a_busy_tab_asks_before_interrupting(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.open_module("fa")
    win._tab_busy.add("fa")
    asked: list[tuple[set[str] | None, bool]] = []
    monkeypatch.setattr(win, "_confirm_close_with_busy",
                        lambda module_ids=None, closing_tab=False:
                        asked.append((module_ids, closing_tab)) or False)
    win._close_module_tab(1)
    assert asked == [({"fa"}, True)]
    assert win.tools_page.count() == 2 and "fa" in win._windows
    win._tab_busy.clear()


def test_closing_a_busy_tab_proceeds_after_confirmation(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    win.open_module("fa")
    win._tab_busy.add("fa")
    monkeypatch.setattr(win, "_confirm_close_with_busy", lambda *args, **kwargs: True)
    win._close_module_tab(1)
    assert win.tools_page.count() == 1 and "fa" not in win._windows
    assert "fa" not in win._tab_busy


def test_module_tab_close_button_is_revealed_on_hover(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa", icon="search")])
    tab = win.open_module("fa")
    assert tab is not None
    win.show()
    QApplication.processEvents()

    bar = win.tools_page.tabBar()
    index = win.tools_page.indexOf(tab)
    close = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
    assert isinstance(close, atlas_window._TabCloseButton)
    assert bar.tabButton(0, QTabBar.ButtonPosition.RightSide) is None
    assert not close.is_active()
    assert close.toolTip() == "Fermer l'onglet"
    assert not close.icon().isNull()
    width = bar.tabRect(index).width()

    # Éloigner d'abord le curseur : un test précédent a pu le laisser sur
    # l'onglet, auquel cas Qt n'émettrait pas de nouveau survol.
    QTest.mouseMove(bar, QPoint(2, 2))
    QTest.qWait(10)
    QTest.mouseMove(bar, bar.tabRect(index).center())
    QTest.qWait(20)
    assert win._hovered_tab == index and close.is_active()
    assert bar.tabRect(index).width() == width

    QTest.mouseMove(win, QPoint(10, 200))
    QTest.qWait(20)
    assert win._hovered_tab == -1 and not close.is_active()

    win._set_hovered_tab(index)
    close.click()
    assert win.tools_page.count() == 1 and "fa" not in win._windows


def test_tab_close_button_keeps_its_place_when_the_tab_is_clicked(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa", icon="search"), _spec("fb", 20, icon="search")])
    win.open_module("fa")
    win.open_module("fb")
    win.show()
    QApplication.processEvents()

    bar = win.tools_page.tabBar()
    edge = atlas_window.scaled(atlas_window._ToolsTabBar.EDGE)
    # Un clic fait recalculer les boutons de côté par Qt, sans tabLayoutChange :
    # la croix doit se replacer d'elle-même au même retrait et sur l'axe de la capsule.
    for clicked in (2, 1):
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(clicked).center())
        QApplication.processEvents()
        for index in range(1, bar.count()):
            button = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            cell = bar.tabRect(index)
            assert cell.right() + 1 - (button.x() + button.width()) == edge
            assert abs(button.y() + button.height() / 2
                       - bar._tab_rect(index).center().y()) <= 0.5


def test_compact_header_centres_the_title_on_the_logo(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa", icon="search")])
    win.resize(1200, 820)
    win.show()
    QApplication.processEvents()
    assert not win.subtitle_label.isHidden()
    assert win._titles_layout.contentsMargins().top() == 0

    win.open_module("fa")
    win._header_animation.stop()             # état compact final, sans attendre l'animation
    win._apply_header_progress(1.0)
    QTest.qWait(50)                          # laisse la mise en page se stabiliser

    assert win.logo_label.height() == win.HEADER_COMPACT
    title = win.title_label.mapTo(win, win.title_label.rect().center()).y()
    logo = win.logo_label.mapTo(win, win.logo_label.rect().center()).y()
    assert abs(title - logo) <= 1
    # Le titre doit rester lisible au repos, et son encre optiquement centrée
    # dans la zone horizontale compacte.
    assert win.title_label.text() == "Azimut"
    assert win.title_label.graphicsEffect() is None
    image = win.title_label.grab().toImage()
    background = image.pixelColor(0, 0)
    ink = [(x, y) for y in range(image.height()) for x in range(image.width())
           if image.pixelColor(x, y) != background]
    assert ink
    top, bottom = min(y for _x, y in ink), max(y for _x, y in ink)
    assert abs((top + bottom) / 2 - (image.height() - 1) / 2) <= 1.5


def test_open_tool_tabs_keep_their_icon_static(make_atlas):
    """Un onglet d'outil ouvert n'anime pas son pictogramme au survol."""
    win = make_atlas([_spec("fa", icon="search")])
    assert win.tools_page.tabBar()._icon_hover is None


def test_selected_tab_icon_turns_accent(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa", icon="search"), _spec("fb", 20, icon="search")])
    win.open_module("fa")
    win.open_module("fb")
    win.show()
    QApplication.processEvents()

    bar = win.tools_page.tabBar()

    def warm(index):
        image = bar.tabIcon(index).pixmap(16, 16).toImage()
        return any(image.pixelColor(x, y).red() > image.pixelColor(x, y).blue() + 20
                   for x in range(image.width()) for y in range(image.height()))

    win.tools_page.setCurrentIndex(2)
    QApplication.processEvents()
    assert warm(2) and not warm(1)
    win.tools_page.setCurrentIndex(1)
    QApplication.processEvents()
    assert warm(1) and not warm(2)


def test_history_tab_filter_and_clear(make_atlas, monkeypatch, tmp_path):
    store = HistoryStore(LocalJsonStorage(tmp_path / "hist"))
    store.add(HistoryEntry(module="fa", action="Test", summary="un", ok=2))
    store.add(HistoryEntry(module="fb", action="Test", summary="deux", ok=1, errors=1))
    win = make_atlas([_spec("fa"), _spec("fb", 20)], history_store=store)
    tab = win.dashboard.history_tab
    assert len(tab.table.data_model.rows()) == 2
    tab.filter_box.setCurrentIndex(tab.filter_box.findData("fa"))
    assert len(tab.table.data_model.rows()) == 1
    yes = QMessageBox.StandardButton.Yes
    monkeypatch.setattr(dashboard, "QMessageBox", types.SimpleNamespace(
        question=lambda *a, **k: yes, StandardButton=QMessageBox.StandardButton))
    tab.filter_box.setCurrentIndex(0)
    tab._on_clear()
    assert store.recent() == [] and tab.table.data_model.rows() == []


def test_tail_lines_and_log_tab(make_atlas, tmp_path):
    log_file = tmp_path / "logs" / "azimut.log"
    log_file.write_text("\n".join(f"ligne {i}" for i in range(600)), encoding="utf-8")
    lines = dashboard.tail_lines(log_file)
    assert len(lines) == 500 and lines[-1] == "ligne 599"
    assert dashboard.tail_lines(tmp_path / "absent.log") == []
    win = make_atlas([_spec("fa")])
    text = win.dashboard.log_tab.view.toPlainText()
    assert "ligne 599" in text and "ligne 0\n" not in text


def test_binaries_tab_rows(make_atlas):
    win = make_atlas([_spec("fa")])
    rows = win.dashboard.binaries_tab.table.data_model.rows()
    assert [r["name"] for r in rows] == list(binaries.KNOWN_BINARIES)
    assert all(r["_tag"] == "err" for r in rows)


def test_wordmark_follows_the_theme_and_keeps_the_window_icon(make_atlas):
    from azimut.ui.theme import ThemeManager

    manager = ThemeManager.instance()
    previous = manager.appearance()
    try:
        win = make_atlas([_spec("a", 10)])
        manager.set_theme("dark")
        win._restyle()
        assert win._wordmark_theme == "dark"
        manager.set_theme("light")
        win._restyle()
        assert win._wordmark_theme == "light"
        assert not win.logo_label.pixmap().isNull()
        assert not win.windowIcon().isNull()          # l'icône de fenêtre ne suit pas le logo
    finally:
        manager.set_theme(previous)
