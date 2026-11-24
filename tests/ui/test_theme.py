from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTabBar, QVBoxLayout, QWidget

from azimut.core import binaries, system_accessibility
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.ui import app as app_module
from azimut.ui import theme as theme_module
from azimut.ui.theme import (
    MIN_POINT_SIZE, TOKEN_NAMES, ThemeManager, build_qss, calibrated_zoom, glass_style, palette,
    point_size_floor, token,
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def prefs(tmp_path):
    return Preferences(tmp_path / "prefs.json")


@pytest.fixture
def manager(qt_app, prefs, monkeypatch):
    """ThemeManager isolé (préférences temporaires) installé comme singleton."""
    monkeypatch.setattr(system_accessibility, "color_scheme", lambda: None)
    font, sheet, pal = qt_app.font(), qt_app.styleSheet(), qt_app.palette()
    tm = ThemeManager(prefs)
    monkeypatch.setattr(ThemeManager, "_instance", tm)
    yield tm
    qt_app.setFont(font)
    qt_app.setPalette(pal)
    qt_app.setStyleSheet(sheet)


# ── Palette ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_all_tokens_exist_in_both_themes(theme):
    pal = palette(theme)
    assert len(TOKEN_NAMES) == 30
    assert set(pal) == set(TOKEN_NAMES)
    for name in TOKEN_NAMES:
        assert re.fullmatch(r"#[0-9a-f]{6}", pal[name]), (theme, name)


def test_palette_values_match_spec():
    attendu = {
        "bg": ("#cbd5df", "#0b131e"),
        "primary": ("#e34b00", "#e8590c"),
        "ok_bg": ("#aacdba", "#17382a"),
        "secondary_hover": ("#b8c6d6", "#24395a"),
        "danger": ("#c93b3b", "#c24141"),
    }
    for name, (clair, sombre) in attendu.items():
        assert palette("light")[name] == clair
        assert palette("dark")[name] == sombre


def test_orange_tokens_follow_the_theme_accent():
    """L'accent reprend l'orange de l'aiguille, adapté à chaque fond."""
    assert palette("light")["accent"] == "#e34b00"
    assert palette("dark")["accent"] == palette("dark")["focus"] == "#e8590c"
    assert palette("light")["accent_hover"] == "#c2410c"
    assert palette("dark")["accent_hover"] == "#f97316"
    for theme in ("light", "dark"):
        qss = build_qss(theme)
        for old in ("#a95a0b", "#eaa25c", "#8f4b08", "#f2b273", "#8a5109", "#e5ae6d",
                    "#f6ebdb", "#33291d", "#f3e7d8", "#33281d"):
            assert old not in qss


def test_palette_unknown_theme():
    with pytest.raises(ValueError):
        palette("sepia")


def test_icon_theme_is_persisted_reloaded_and_normalized(manager, prefs):
    """Le choix est écrit, relu depuis le disque, et une valeur inconnue retombe sur Papicons."""
    assert manager.icon_theme() == "papicons"
    manager.set_icon_theme("animated")
    assert manager.icon_theme() == "animated"
    assert prefs.get("icon_theme") == "animated"

    reloaded = Preferences(prefs.path)
    assert reloaded.get("icon_theme") == "animated"
    assert ThemeManager(reloaded).icon_theme() == "animated"

    manager.set_icon_theme("lucide")                  # ancienne clé du thème remplacé
    assert manager.icon_theme() == "animated"

    manager.set_icon_theme("nope")
    assert manager.icon_theme() == "papicons"
    assert Preferences(prefs.path).get("icon_theme") == "papicons"


def test_brand_logo_has_a_variant_per_theme(qapp):
    """Le logo de l'interface change de tracés avec le thème, l'icône de fenêtre reste à part."""
    from azimut.ui import icons
    dark = icons.brand_pixmap("azimut_wordmark", 80, "dark")
    light = icons.brand_pixmap("azimut_wordmark", 80, "light")
    assert not dark.isNull() and not light.isNull()
    assert bytes(dark.toImage().constBits()) != bytes(light.toImage().constBits())
    assert not icons.brand_pixmap("azimut_wordmark", 80, "inconnu").isNull()
    assert not icons.app_icon().isNull()


# ── QSS ──────────────────────────────────────────────────────────────────────
def test_build_qss_contains_theme_background():
    dark, light = build_qss("dark"), build_qss("light")
    assert palette("dark")["bg"] in dark and palette("light")["bg"] not in dark
    assert palette("light")["bg"] in light and palette("dark")["bg"] not in light


def test_build_qss_covers_widgets_and_button_kinds():
    qss = build_qss("dark")
    for sel in ("QMainWindow", "QFrame#Card", "QGroupBox", "QLineEdit", "QComboBox", "QCheckBox", "QProgressBar",
                "QSpinBox::up-button", "QSpinBox::down-arrow",
                "QTableView", "QHeaderView", "QPlainTextEdit", "QScrollBar", "QToolBar", "QDockWidget",
                "QDockWidget#SideDock", "QWidget#SideDockTitleBar", "QLabel#SideDockTitle", "QTabWidget",
                "QTabBar", "QTabWidget#ToolsTabs::pane", "QTabWidget#ToolsTabs QTabBar::tab:selected",
                "QToolButton#TabCloseButton", "QMainWindow[embedded=\"true\"]",
                "QFrame#Card[embedded=\"true\"]", "QMenu", "QToolTip",
                "QPushButton#AtlasHeaderButton:focus", "QPushButton#SwapButton"):
        assert sel in qss, sel
    assert "QPushButton#SwapButton { padding: 0; }" in qss
    assert "border-radius: 8px" in qss               # conteneurs arrondis
    assert "border-radius: 6px" in qss               # boutons et champs
    for kind in ("primary", "success", "danger", "accent", "secondary", "ghost"):
        assert f'QPushButton[kind="{kind}"]' in qss
    assert "$" not in qss and "__KINDS__" not in qss
    assert "QTabWidget#ToolsTabs::pane { border: none; }" in qss


def test_build_qss_zoom_scales_paddings():
    assert build_qss("dark", 1.6) != build_qss("dark", 1.0)


def test_vertical_slider_fills_from_the_bottom():
    qss = build_qss("light")
    assert "QSlider::add-page:vertical" in qss
    assert "QSlider::sub-page:vertical { background-color: transparent" in qss


def test_page_nav_hover_darkens_in_light_theme_only():
    """Les boutons de page foncent au survol en clair ; le thème sombre garde son rendu."""
    def hover_fill(theme: str) -> str:
        rule = re.search(r"QPushButton#AtlasPageButton:hover \{([^}]*)\}", build_qss(theme))
        assert rule is not None, theme
        value = re.search(r"background-color: (#[0-9a-fA-F]{8})", rule.group(1))
        assert value is not None, rule.group(1)
        return value.group(1)

    light = QColor(hover_fill("light"))
    assert light.alpha() > 0
    assert light.lightness() < QColor(palette("light")["surface_alt"]).lightness()
    assert QColor(hover_fill("dark")).alpha() == 0


def test_focus_ring_is_reserved_for_keyboard_navigation():
    qss = build_qss("dark")
    for selector in ('QFrame#Card:focus[keyboardFocus="true"]',
                     'QFrame#DropZone:focus[keyboardFocus="true"]',
                     'QPushButton:focus[keyboardFocus="true"]'):
        assert selector in qss, selector
    # Un clic souris ne doit plus laisser de contour orange persistant.
    assert "\nQFrame#Card:focus {" not in qss
    assert "\nQPushButton:focus {" not in qss


def _font_size(qss: str, selector: str) -> float:
    """Taille de police déclarée dans le bloc d'un sélecteur de la feuille."""
    for block in qss.split("}"):
        if selector in block:
            found = re.search(r"font-size: ([\d.]+)pt", block)
            if found:
                return float(found.group(1))
    raise AssertionError(f"aucune taille de police pour {selector}")


def test_titles_and_captions_share_one_scale():
    """Nom du module, panneau latéral, boîte de dialogue et libellés : un seul corps."""
    qss = build_qss("dark", 1.0)
    body = 13.0
    assert _font_size(qss, "QLabel#ModuleName") == pytest.approx(body * 1.2)
    assert _font_size(qss, "QLabel#DialogTitle") == pytest.approx(body * 1.2)
    assert _font_size(qss, "QLabel#SideDockTitle") == body      # rang du corps, pas au-dessus
    assert _font_size(qss, "QLabel#StatusChip") == pytest.approx(body * 0.8)
    # Le zoom multiplie toute l'échelle sans changer les rapports.
    zoomed = build_qss("dark", 1.6)
    assert _font_size(zoomed, "QLabel#ModuleName") == pytest.approx(25.0)
    assert _font_size(zoomed, "QLabel#SideDockTitle") == pytest.approx(20.8)


def test_type_hierarchy_is_the_same_for_a_nine_or_a_thirteen_point_system():
    """Windows (9 pt) et macOS (13 pt) doivent produire la même hiérarchie relative."""
    for base in (9.0, 13.0):
        qss = build_qss("dark", 0.85, base_point_size=base)
        module = _font_size(qss, "QLabel#ModuleName")
        side = _font_size(qss, "QLabel#SideDockTitle")
        dialog = _font_size(qss, "QLabel#DialogTitle")
        caption = _font_size(qss, "QLabel#StatusChip")
        assert caption <= side < module <= dialog, base
        assert module == dialog, base


def test_reduce_transparency_makes_glass_opaque():
    assert glass_style("dark")["glass_panel"] == "#f2132338"
    opaque = glass_style("dark", reduce_transparency=True)
    assert opaque["glass_panel"] == "#ff132338"
    assert opaque["glass_shine"] == "#00FFFFFF"


def test_high_contrast_uses_stronger_border():
    assert palette("light")["border"] == "#aebdcd"
    assert palette("light", high_contrast=True)["border"] == "#94a3b8"
    assert "#94a3b8" in build_qss("light", high_contrast=True)


# ── ThemeManager ─────────────────────────────────────────────────────────────
def test_defaults(manager):
    assert manager.appearance() == "system"
    assert manager.theme() == "dark"                  # offscreen : schéma inconnu, repli sombre
    assert manager.zoom() == 1.0


def test_system_appearance_uses_the_detected_scheme(qt_app, prefs, monkeypatch):
    monkeypatch.setattr(system_accessibility, "color_scheme", lambda: "light")
    tm = ThemeManager(prefs)
    assert tm.appearance() == "system" and tm.theme() == "light"


def test_stored_theme_is_restored_at_launch(qt_app, prefs, monkeypatch):
    """Préférence « dark » sur le disque : le lancement la reprend, sans suivre le schéma détecté."""
    prefs.set("theme", "dark")
    monkeypatch.setattr(system_accessibility, "color_scheme", lambda: "light")

    manager = ThemeManager(prefs)

    assert manager.appearance() == "dark"
    assert manager.theme() == "dark"


def test_unknown_stored_theme_falls_back_to_system(qt_app, prefs, monkeypatch):
    """Une valeur illisible ne fige pas l'application sur un thème arbitraire."""
    prefs.set("theme", "sepia")
    monkeypatch.setattr(system_accessibility, "color_scheme", lambda: "light")

    assert ThemeManager(prefs).appearance() == "system"


def test_linux_color_scheme_reads_gnome_then_kde(monkeypatch):
    values = {"color-scheme": "prefer-light", "gtk-theme": "Adwaita"}
    monkeypatch.setattr(system_accessibility, "_gsettings_text",
                        lambda schema, key: values.get(key))
    monkeypatch.setattr(system_accessibility, "_kde_color_scheme", lambda: "dark")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert system_accessibility._linux_color_scheme() == "light"
    values["color-scheme"] = "prefer-dark"
    assert system_accessibility._linux_color_scheme() == "dark"
    values["color-scheme"] = "default"
    values["gtk-theme"] = "Adwaita"
    assert system_accessibility._linux_color_scheme() == "light"     # thème GTK clair
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert system_accessibility._linux_color_scheme() == "dark"


def test_token_follows_current_theme(manager):
    assert token("bg") == "#0b131e"
    manager.set_theme("light")
    assert token("bg") == "#cbd5df"
    with pytest.raises(KeyError):
        token("inexistant")


def test_set_theme_is_persisted(manager, prefs):
    """Sombre/Clair est enregistré et repris au lancement suivant, comme la langue."""
    got: list[str] = []
    manager.changed.connect(got.append)
    manager.set_theme("light")
    manager.set_theme("light")                    # sans effet : pas de second signal
    assert got == ["light"]
    assert manager.appearance() == "light" and manager.theme() == "light"
    assert prefs.get("theme") == "light"
    assert ThemeManager(Preferences(prefs.path)).appearance() == "light"


def test_toggle_is_persisted(manager, prefs):
    manager.toggle()
    assert prefs.get("theme") == "light"


def test_toggle(manager):
    manager.toggle()
    assert manager.theme() == "light"
    manager.toggle()
    assert manager.theme() == "dark"


def test_invalid_theme_rejected(manager):
    with pytest.raises(ValueError):
        manager.set_theme("sepia")
    assert manager.theme() == "dark"


def test_set_zoom_is_clamped(manager, prefs):
    manager.set_zoom(3.0)
    assert manager.zoom() == 1.6
    assert prefs.get("zoom") == 1.6
    manager.set_zoom(0.1)
    assert manager.zoom() == 0.8


def test_apply_sets_the_font_before_and_after_the_stylesheet(manager, qt_app, monkeypatch):
    """La police encadre la feuille de style : avant, puis de nouveau après.

    Avant, parce que la feuille de style re-polit les widgets et fige leur police :
    appliquée avant la nouvelle police, elle leur laissait le zoom précédent (consigne des
    outils, boutons « Ouvrir », onglets).

    Après aussi, parce qu'elle fait repartir de la police *par défaut* les widgets créés
    ensuite : une fenêtre construite après l'application affichait presque tout son texte
    à 100 % (13 pt sous macOS) alors que le zoom valait 80 % (AZ-C12).
    """
    order: list[str] = []
    real_font, real_sheet = qt_app.setFont, qt_app.setStyleSheet
    monkeypatch.setattr(qt_app, "setFont", lambda f: (order.append("font"), real_font(f))[1])
    monkeypatch.setattr(qt_app, "setStyleSheet", lambda s: (order.append("sheet"), real_sheet(s))[1])

    manager.set_zoom(1.25)                            # sans application encore : rien n'est posé
    manager.apply(qt_app)

    assert order == ["font", "sheet", "font"], order
    assert qt_app.font().pointSizeF() == pytest.approx(manager.base_point_size() * 1.25, abs=0.01)


def test_widget_created_after_apply_follows_the_zoom(manager, qt_app):
    """Une fenêtre construite après l'application garde le zoom, pas la taille système.

    Mesuré au relancement à 80 % : la consigne des outils, les descriptions des cartes et
    le sous-titre de l'en-tête restaient à 13 pt (100 %) alors que la police applicative
    valait 10,4 pt (AZ-C12).
    """
    manager.set_zoom(0.8)
    manager.apply(qt_app)
    host = QWidget()
    layout = QVBoxLayout(host)
    label = QLabel("Ouvrez un module, ou déposez-y des fichiers ou des dossiers.")
    layout.addWidget(label)
    host.show()
    from azimut.ui.theme import point_size_floor

    base = manager.base_point_size()
    expected = max(point_size_floor(base), base * 0.8)      # même plancher que apply()
    assert label.font().pointSizeF() == pytest.approx(expected, abs=0.01)
    host.close()


def test_zoom_change_updates_labels_buttons_and_tabs_immediately(manager, qt_app):
    """Un seul changement de zoom suffit pour tous les textes de la fenêtre.

    Reproduit les éléments défectueux d'Atlas : consigne (« Ouvrez un module… »),
    boutons de carte (« Ouvrir ») et onglets. Aucun ne doit rester au zoom précédent.
    """
    manager.apply(qt_app)
    host = QWidget()
    layout = QVBoxLayout(host)
    label = QLabel("Ouvrez un module, ou déposez-y des fichiers ou des dossiers.")
    button = QPushButton("Ouvrir")
    tabs = QTabBar()
    tabs.addTab("Tous les outils")
    for widget in (label, button, tabs):
        layout.addWidget(widget)
    host.show()

    try:
        manager.set_zoom(0.8)
        qt_app.processEvents()
        base = manager.base_point_size()
        expected = max(point_size_floor(base), base * 0.8)
        assert [w.font().pointSizeF() for w in (label, button, tabs)] == pytest.approx(
            [expected, expected, expected], abs=0.01)

        manager.set_zoom(1.5)
        qt_app.processEvents()
        expected = base * 1.5
        assert [w.font().pointSizeF() for w in (label, button, tabs)] == pytest.approx(
            [expected, expected, expected], abs=0.01)

        manager.set_zoom(1.0)
        qt_app.processEvents()
        expected = base
        assert [w.font().pointSizeF() for w in (label, button, tabs)] == pytest.approx(
            [expected, expected, expected], abs=0.01)
    finally:
        # Cette fenêtre factice ne doit pas rester au-dessus des tests suivants :
        # elle intercepterait leurs déplacements de souris.
        host.close()
        host.deleteLater()
        qt_app.processEvents()


def test_calibrated_zoom_targets_a_body_of_about_two_millimetres():
    """Le zoom initial vise 2,3 mm de corps, d'après la densité logique de l'écran."""
    # 13" en 1440 × 900 : 128 ppi logiques, 13 px = 2,58 mm → demande 0,89 → cran 0,9.
    assert calibrated_zoom(13.0, 72.0, 127.9) == pytest.approx(0.9)
    # 27" 4K à 200 % : 82 ppi logiques, 13 px = 4,03 mm → demande 0,57 → borné à 0,85.
    assert calibrated_zoom(13.0, 72.0, 82.0) == pytest.approx(0.85)
    # Écran très dense : 13 px = 1,65 mm → demande 1,39 → borné à 1,25.
    assert calibrated_zoom(13.0, 72.0, 200.0) == pytest.approx(1.25)
    # Windows 15,6" à 125 % : 9 pt à 120 dpi = 15 px, 113 ppi → 3,36 mm → borné à 0,85.
    assert calibrated_zoom(9.0, 120.0, 113.5) == pytest.approx(0.85)
    # Entrées inexploitables : repli neutre.
    assert calibrated_zoom(0.0, 96.0, 100.0) == pytest.approx(1.0)


def test_first_launch_calibrates_once_then_keeps_the_saved_zoom(manager, prefs, monkeypatch):
    """Le calibrage n'a lieu qu'au premier lancement : la préférence fige le résultat."""
    monkeypatch.setattr(theme_module, "_screen_density", lambda *_args: 127.9)
    monkeypatch.setattr(theme_module, "calibrated_zoom", lambda *args, **kwargs: 1.1)
    first = ThemeManager(prefs)
    assert first.zoom() == pytest.approx(1.1)
    assert prefs.get("zoom") == pytest.approx(1.1)      # enregistré une fois pour toutes

    # L'écran change : la préférence enregistrée l'emporte, rien n'est recalculé.
    monkeypatch.setattr(theme_module, "_screen_density", lambda *_args: 60.0)
    monkeypatch.setattr(theme_module, "calibrated_zoom", lambda *args, **kwargs: 0.85)
    second = ThemeManager(prefs)
    assert second.zoom() == pytest.approx(1.1)
    assert prefs.get("zoom") == pytest.approx(1.1)


def test_no_calibration_when_the_screen_is_not_measurable(manager, prefs, monkeypatch):
    """Hors écran, ou taille physique inconnue : on ne fige aucun zoom."""
    monkeypatch.setattr(theme_module, "_screen_density", lambda *_args: None)
    tm = ThemeManager(prefs)
    assert tm.zoom() == pytest.approx(1.0)
    assert not prefs.contains("zoom")        # le prochain lancement sur un vrai écran calibrera


class _FakeScreen:
    """Écran factice : nom, facteur d'échelle et DPI logique suffisent à la mémorisation."""

    def __init__(self, name: str, dpr: float, dpi: float = 96.0) -> None:
        self._name, self._dpr, self._dpi = name, dpr, dpi

    def name(self) -> str:
        return self._name

    def devicePixelRatio(self) -> float:  # noqa: N802 - API Qt
        return self._dpr

    def logicalDotsPerInch(self) -> float:  # noqa: N802 - API Qt
        return self._dpi


def test_screen_key_keeps_the_scale_factor_apart():
    """Nom + facteur d'échelle : un même écran à 100 % et à 200 % a deux réglages."""
    assert theme_module.screen_key(_FakeScreen("Écran", 1.0)) == "Écran|1.00"
    assert (theme_module.screen_key(_FakeScreen("Écran", 2.0))
            != theme_module.screen_key(_FakeScreen("Écran", 1.0)))


def test_zoom_is_remembered_per_screen(manager, prefs, monkeypatch):
    """Chaque écran garde son zoom : passer de l'un à l'autre le retrouve tel quel."""
    monkeypatch.setattr(theme_module, "_screen_density", lambda *_args: None)
    portable, externe = _FakeScreen("Portable", 2.0), _FakeScreen("Externe", 1.0)
    tm = ThemeManager(prefs)

    tm.activate_screen(portable)
    tm.set_zoom(1.1)
    assert tm.zoom() == pytest.approx(1.1)

    tm.activate_screen(externe)              # jamais vu : repli 100 %, rien de mémorisé
    assert tm.zoom() == pytest.approx(1.0)
    tm.set_zoom(1.25)                        # réglage propre au second écran

    tm.activate_screen(portable)             # retour au portable : son réglage revient
    assert tm.zoom() == pytest.approx(1.1)
    tm.activate_screen(externe)
    assert tm.zoom() == pytest.approx(1.25)

    saved = prefs.get("zoom_by_screen")
    assert set(saved) == {"Portable|2.00", "Externe|1.00"}
    assert saved["Portable|2.00"] == pytest.approx(1.1)
    assert saved["Externe|1.00"] == pytest.approx(1.25)


def test_legacy_global_zoom_becomes_the_current_screen_value(manager, prefs, monkeypatch):
    """L'ancienne préférence unique est reprise une fois, pour l'écran du lancement."""
    monkeypatch.setattr(theme_module, "_screen_density", lambda *_args: None)
    prefs.set("zoom", 0.85)
    tm = ThemeManager(prefs)
    assert tm.zoom() == pytest.approx(0.85)
    assert prefs.get("zoom_by_screen") == {"écran|1.00": 0.85}


def test_apply_installs_stylesheet_and_zoom_changes_font(manager, qt_app):
    # La taille du système est reprise telle quelle : le plancher ne la relève plus à 10 pt.
    base = max(MIN_POINT_SIZE, qt_app.font().pointSizeF())
    manager.apply(qt_app)
    assert palette("dark")["bg"] in qt_app.styleSheet()
    assert qt_app.font().pointSizeF() == pytest.approx(base)
    assert manager.base_point_size() == pytest.approx(base)
    manager.set_zoom(1.5)
    assert qt_app.font().pointSizeF() == pytest.approx(max(point_size_floor(base), base * 1.5))
    manager.set_theme("light")
    assert palette("light")["bg"] in qt_app.styleSheet()


def test_zoom_below_100_really_shrinks_a_nine_point_system_font(manager, qt_app):
    """Sur un système à 9 pt (Windows), 90 % et 80 % doivent vraiment réduire le texte.

    Le plancher fixe de 10 pt renvoyait 10 pt à 100 %, 90 % et 80 % : le zoom ne changeait
    plus que les marges, et l'interface restait « grosse » en se tassant.
    """
    base = QFont(qt_app.font())
    base.setPointSizeF(9.0)
    manager._base_font = base                    # simule une police système de 9 pt
    manager.apply(qt_app)
    assert qt_app.font().pointSizeF() == pytest.approx(9.0)
    manager.set_zoom(0.9)
    assert qt_app.font().pointSizeF() == pytest.approx(8.1)
    manager.set_zoom(0.8)
    assert qt_app.font().pointSizeF() == pytest.approx(MIN_POINT_SIZE)


def test_external_zoom_change_is_followed(manager, prefs):
    """Le zoom est une préférence suivie : l'écrire ailleurs s'applique aussitôt."""
    got: list[str] = []
    manager.changed.connect(got.append)
    prefs.set("zoom", 1.25)
    assert manager.zoom() == 1.25
    assert got == ["dark"]


def test_external_theme_change_is_followed(manager, prefs):
    """L'apparence aussi : préférences et fenêtres restent sur le même thème."""
    got: list[str] = []
    manager.changed.connect(got.append)
    prefs.set("theme", "light")
    assert manager.appearance() == "light" and manager.theme() == "light"
    assert got == ["light"]


def test_system_accessibility_values_are_used_when_prefs_are_unset(qt_app, prefs, monkeypatch):
    monkeypatch.setattr(system_accessibility, "reduce_motion", lambda: True)
    monkeypatch.setattr(system_accessibility, "reduce_transparency", lambda: True)
    monkeypatch.setattr(system_accessibility, "high_contrast", lambda: True)
    manager = ThemeManager(prefs)
    assert manager.reduce_motion() and manager.reduce_transparency() and manager.high_contrast()
    assert not prefs.contains("reduce_motion")            # rien n'est figé sans action de l'utilisateur


def test_refresh_accessibility_follows_system_until_an_explicit_choice(qt_app, prefs, monkeypatch):
    system = {"motion": False, "transparency": False, "contrast": False}
    monkeypatch.setattr(system_accessibility, "reduce_motion", lambda: system["motion"])
    monkeypatch.setattr(system_accessibility, "reduce_transparency", lambda: system["transparency"])
    monkeypatch.setattr(system_accessibility, "high_contrast", lambda: system["contrast"])
    manager = ThemeManager(prefs)
    assert not manager.reduce_motion()
    system.update(motion=True, transparency=True, contrast=True)
    manager.refresh_accessibility(force=True)
    assert manager.reduce_motion() and manager.reduce_transparency() and manager.high_contrast()
    manager.set_reduce_motion(False)                         # choix explicite
    system["motion"] = True
    manager.refresh_accessibility(force=True)
    assert not manager.reduce_motion()                       # le choix explicite reste
    assert manager.reduce_transparency() and manager.high_contrast()


def test_explicit_accessibility_preferences_win_over_system(qt_app, prefs, monkeypatch):
    prefs.set("reduce_motion", False)
    prefs.set("reduce_transparency", False)
    prefs.set("high_contrast", False)
    monkeypatch.setattr(system_accessibility, "reduce_motion", lambda: True)
    monkeypatch.setattr(system_accessibility, "reduce_transparency", lambda: True)
    monkeypatch.setattr(system_accessibility, "high_contrast", lambda: True)
    manager = ThemeManager(prefs)
    assert not manager.reduce_motion()
    assert not manager.reduce_transparency()
    assert not manager.high_contrast()


@pytest.mark.parametrize(("raw", "expected"), [
    ("true", True), ("false", False), ("'true'", True), ("0", False),
    ("1", True), (1, True), (0, False), (True, True), (None, None), ("bruit", None),
])
def test_system_accessibility_bool_parser(raw, expected):
    assert system_accessibility._parse_bool(raw) is expected


def test_accessibility_flags_are_persisted_and_announced(manager, prefs):
    got: list[int] = []
    manager.accessibility_changed.connect(lambda: got.append(1))
    manager.set_reduce_transparency(True)
    manager.set_high_contrast(True)
    manager.set_reduce_motion(True)
    assert prefs.get("reduce_transparency") is True
    assert prefs.get("high_contrast") is True
    assert prefs.get("reduce_motion") is True
    assert got == [1, 1, 1]
    assert manager.reduce_transparency() and manager.high_contrast() and manager.reduce_motion()


def test_corrupt_preferences_fall_back_to_defaults(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(system_accessibility, "color_scheme", lambda: None)
    prefs = Preferences(tmp_path / "p.json")
    prefs.set("zoom", "abc")
    prefs.set("language", "zz")
    tm = ThemeManager(prefs)
    assert tm.appearance() == "system" and tm.theme() == "dark" and tm.zoom() == 1.0


# ── app ──────────────────────────────────────────────────────────────────────
def test_create_app_is_idempotent(qt_app, manager, monkeypatch):
    calls = {"log": 0, "refresh": 0}
    monkeypatch.setattr(app_module, "setup_logging", lambda *a, **k: calls.__setitem__("log", calls["log"] + 1))
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: calls.__setitem__("refresh", calls["refresh"] + 1) or {})
    qt_app.setProperty("azimut.ready", False)
    try:
        a1 = app_module.create_app([])
        a2 = app_module.create_app([])
        assert a1 is a2 is qt_app
        assert calls == {"log": 1, "refresh": 1}
        assert qt_app.organizationName() == "Azimut"
        assert qt_app.applicationName() == "Azimut"
    finally:
        qt_app.setProperty("azimut.ready", False)


def test_create_app_does_not_probe_binaries_on_the_gui_thread(qt_app, manager, monkeypatch, tmp_path):
    """Le démarrage ne doit pas lancer les sondes de version sur le fil de l'interface (CORE-6)."""
    import threading
    import time

    caller = threading.get_ident()
    on_caller: list[str] = []
    probed: list[str] = []

    def slow_probe(name, path):
        if threading.get_ident() == caller:
            on_caller.append(name)                       # sonde sur le fil qui appelle create_app : défaut
        probed.append(name)
        time.sleep(0.2)
        return "9.9"

    leftover = getattr(binaries, "_refresh_thread", None)
    if leftover is not None:
        leftover.join(10)                                # ne pas mélanger avec une détection précédente
    monkeypatch.setattr(binaries, "app_root", lambda: tmp_path / "app-sans-embarque")
    monkeypatch.setattr(binaries.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    monkeypatch.setattr(binaries, "probe_version", slow_probe)
    monkeypatch.setattr(app_module, "setup_logging", lambda *a, **k: None)
    binaries._cache.clear()
    qt_app.setProperty("azimut.ready", False)
    try:
        app_module.create_app([])
    finally:
        qt_app.setProperty("azimut.ready", False)
    assert on_caller == [], f"sondes exécutées sur le fil de l'interface : {on_caller}"
    thread = binaries._refresh_thread
    assert thread is not None, "aucune détection d'arrière-plan lancée"
    thread.join(10)
    assert set(probed) == set(binaries.KNOWN_BINARIES)


def test_run_module_passes_existing_paths(tmp_path, monkeypatch):
    class FakeWindow:
        instances: list["FakeWindow"] = []

        def __init__(self, spec):
            self.spec, self.shown, self.paths = spec, False, None
            FakeWindow.instances.append(self)

        def show(self):
            self.shown = True

        def open_paths(self, paths):
            self.paths = paths

        def close(self):
            pass

        def deleteLater(self):
            pass

    spec = ModuleSpec(id="fake", name="Azimut Fake", tagline="test", icon="🧪", entry="x:Y")
    monkeypatch.setattr(app_module, "create_app",
                        lambda argv=None: SimpleNamespace(exec=lambda: 7, processEvents=lambda: None))
    monkeypatch.setattr(app_module, "load_window_class", lambda s: FakeWindow)
    code = app_module.run_module(spec, ["prog", str(tmp_path), "/chemin/inexistant/xyz"])
    assert code == 7
    win = FakeWindow.instances[-1]
    assert win.shown and win.spec is spec
    assert win.paths == [os.path.abspath(str(tmp_path))]


def test_run_module_without_paths_does_not_call_open_paths(monkeypatch):
    class FakeWindow:
        def __init__(self, spec):
            self.called = False

        def show(self):
            pass

        def open_paths(self, paths):
            self.called = True

        def close(self):
            pass

        def deleteLater(self):
            pass

    created: list[FakeWindow] = []

    def factory(spec):
        w = FakeWindow(spec)
        created.append(w)
        return w

    spec = ModuleSpec(id="fake", name="Azimut Fake", tagline="test", icon="🧪", entry="x:Y")
    monkeypatch.setattr(app_module, "create_app",
                        lambda argv=None: SimpleNamespace(exec=lambda: 0, processEvents=lambda: None))
    monkeypatch.setattr(app_module, "load_window_class", lambda s: factory)
    assert app_module.run_module(spec, ["prog"]) == 0
    assert created and created[0].called is False
