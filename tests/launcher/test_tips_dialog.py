"""Tests : boîte « Conseils » (ordre des étapes, libellés) et bouton d'Azimut."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea

from azimut.core.module_spec import ModuleSpec
from azimut.launcher import tips_dialog
from azimut.ui.glass import GlassTabBar
from azimut.ui.widgets import Card, IconLabel

ORDER = ["takeout", "dedup", "convert", "rename", "scope", "tags"]


def _spec(module_id: str, order: int = 10) -> ModuleSpec:
    return ModuleSpec(id=module_id, name=module_id.capitalize(), tagline="…",
                      icon="info", entry=f"x:{module_id}", order=order)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_steps_follow_the_recommended_order(qt_app):
    specs = [_spec(module_id, index) for index, module_id in enumerate(reversed(ORDER))]
    dlg = tips_dialog.TipsDialog(specs)
    assert [module_id for module_id, _name, _desc in dlg.steps] == ORDER
    assert all(description for _module_id, _name, description in dlg.steps)


def test_steps_use_the_module_names(qt_app):
    dlg = tips_dialog.TipsDialog([_spec(module_id) for module_id in ORDER])
    assert [name for _module_id, name, _desc in dlg.steps] == [m.capitalize() for m in ORDER]


def test_missing_modules_get_a_readable_name(qt_app):
    dlg = tips_dialog.TipsDialog([])
    assert dlg.steps[0][1] == "Takeout"
    assert dlg.steps[-1][1] == "Tags"


def test_close_button_is_labelled(qt_app):
    dlg = tips_dialog.TipsDialog([])
    box = dlg.findChild(QDialogButtonBox)
    assert box is not None
    assert box.button(QDialogButtonBox.StandardButton.Close).text() == "Fermer"


def test_tabs_expose_takeout_and_precautions(qt_app):
    dlg = tips_dialog.TipsDialog([_spec(module_id) for module_id in ORDER])
    assert isinstance(dlg.tab_switch, GlassTabBar)
    assert dlg.tab_switch.objectName() == "TipsTabs"
    assert [dlg.tab_switch.tabText(index) for index in range(dlg.tab_switch.count())] == [
        "Google Photos Takeout", "Précautions"]
    assert dlg.content_stack.count() == 2
    assert dlg.content_stack.currentIndex() == dlg.TAKEOUT
    assert dlg.tab_switch.current_index() == dlg.TAKEOUT

    dlg.tab_switch.setCurrentIndex(dlg.PRECAUTIONS)
    assert dlg.content_stack.currentIndex() == dlg.PRECAUTIONS
    assert dlg.tab_switch.current_index() == dlg.PRECAUTIONS

    dlg.tab_switch.setCurrentIndex(dlg.TAKEOUT)
    assert dlg.content_stack.currentIndex() == dlg.TAKEOUT
    assert dlg.tab_switch.current_index() == dlg.TAKEOUT


def test_tips_tool_icons_do_not_animate(qt_app):
    """Dans Conseils, les pictogrammes d'outils restent fixes au survol."""
    dlg = tips_dialog.TipsDialog([_spec(module_id) for module_id in ORDER])
    assert dlg.tab_switch._icon_hover is None

    titles = [label for label in dlg.findChildren(IconLabel)
              if label.objectName() == "TipsStepTitle"]
    assert len(titles) == len(ORDER)
    assert all(not title._animate for title in titles)


def test_page_leaves_the_window_background_visible(qt_app):
    """Comme « Journaux et historique » : aucun fond propre, l'aurore de la fenêtre passe."""
    page = tips_dialog.TipsPage([])
    assert not page.autoFillBackground()
    assert not page.scroll.autoFillBackground()
    assert not page.scroll.viewport().autoFillBackground()
    assert not page.content_stack.autoFillBackground()
    # La boîte de dialogue autonome garde son verre : sa page ne peint rien non plus.
    assert not tips_dialog.TipsDialog([]).page.autoFillBackground()


def test_tips_tabs_reuse_the_glass_capsule_animation(qt_app):
    """La barre de Conseils est celle des autres pages, capsule animée comprise."""
    dlg = tips_dialog.TipsDialog([])
    dlg.resize(1000, 820)
    dlg.show()
    qt_app.processEvents()
    bar = dlg.tab_switch
    assert bar.isVisible()
    bar.setCurrentIndex(dlg.PRECAUTIONS)
    assert bar._animating
    QTest.qWait(350)
    qt_app.processEvents()
    assert not bar._animating
    dlg.close()


def test_cards_follow_their_content_height(qt_app):
    """Sans les deux notes supprimées, la carte ne doit plus s'étirer dans le vide."""
    dlg = tips_dialog.TipsDialog([])
    dlg.show()
    qt_app.processEvents()
    scroll = dlg.findChild(QScrollArea, "TipsScroll")
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() == 0

    for index in (dlg.TAKEOUT, dlg.PRECAUTIONS):
        page = dlg.content_stack.widget(index)
        card = page.findChild(Card)
        assert card is not None
        assert card.height() <= page.height()
        assert not card.body_layout.itemAt(card.body_layout.count() - 1).spacerItem()
        assert page.layout().itemAt(page.layout().count() - 1).spacerItem() is not None
    dlg.close()


def test_cards_keep_a_readable_width_on_a_wide_window(qt_app):
    """La carte ne s'étire pas sur toute la largeur quand le texte reste court."""
    dlg = tips_dialog.TipsDialog([])
    dlg.resize(1400, 900)
    dlg.show()
    qt_app.processEvents()
    for index in (dlg.TAKEOUT, dlg.PRECAUTIONS):
        page = dlg.content_stack.widget(index)
        card = page.findChild(Card)
        assert card is not None
        assert card.width() <= tips_dialog.TipsPage.CONTENT_MAX_WIDTH
    dlg.close()


def test_precautions_include_the_copy_advice(qt_app):
    dlg = tips_dialog.TipsDialog([])
    assert len(dlg.precautions) >= 5
    assert all(title and description for title, description in dlg.precautions)
    assert any("copie" in title.lower() for title, _description in dlg.precautions)
    assert any("copie" in description.lower() for _title, description in dlg.precautions)


def test_guide_follows_the_language(qt_app):
    from azimut.core import i18n
    assert i18n.install(qt_app, "en").active == "en"
    try:
        assert tips_dialog.TipsDialog([]).windowTitle() == "Tips"
    finally:
        i18n.uninstall(qt_app)
