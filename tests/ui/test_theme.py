from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from azimut.core import binaries, system_accessibility
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.ui import app as app_module
from azimut.ui.theme import TOKEN_NAMES, ThemeManager, build_qss, glass_style, palette, token


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
    assert len(TOKEN_NAMES) == 26
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


def test_side_panel_title_scales_with_zoom():
    assert "font-size: 15.6pt" in build_qss("dark", 1.0)
    assert "font-size: 25pt" in build_qss("dark", 1.6)
    # À 10 pt système, 100 % reste à l'échelle normale ; les titres suivent.
    qss = build_qss("dark", 1.0, base_point_size=10.0)
    assert "font-size: 12pt" in qss
    assert "font-size: 13pt" in qss
    assert "font-size: 19.2pt" in build_qss("dark", 1.6, base_point_size=10.0)


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


def test_stored_theme_is_ignored_at_launch(qt_app, prefs, monkeypatch):
    """Préférence « dark » sur le disque : le lancement repart de « system » et suit le schéma détecté."""
    prefs.set("theme", "dark")                  # clé héritée : ni relue, ni réécrite
    assert prefs.get("theme") == "dark" and prefs.contains("theme")
    monkeypatch.setattr(system_accessibility, "color_scheme", lambda: "light")

    manager = ThemeManager(prefs)

    assert manager.appearance() == "system"
    assert manager.theme() == "light"
    manager.set_theme("dark")                  # choix de session : la valeur disque n'est pas réécrite
    assert manager.theme() == "dark" and prefs.get("theme") == "dark"


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


def test_set_theme_is_session_only(manager, prefs, tmp_path):
    """Sombre/Clair vaut pour la session : rien n'est écrit, le lancement suivant repart du système."""
    got: list[str] = []
    manager.changed.connect(got.append)
    manager.set_theme("light")
    manager.set_theme("light")                    # sans effet : pas de second signal
    assert got == ["light"]
    assert manager.appearance() == "light" and manager.theme() == "light"
    assert not prefs.contains("theme")
    assert ThemeManager(Preferences(tmp_path / "prefs.json")).appearance() == "system"


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


def test_apply_installs_stylesheet_and_zoom_changes_font(manager, qt_app):
    base = max(10.0, qt_app.font().pointSizeF())    # taille du système, plancher 10 pt
    manager.apply(qt_app)
    assert palette("dark")["bg"] in qt_app.styleSheet()
    assert qt_app.font().pointSizeF() == pytest.approx(base)
    assert manager.base_point_size() == pytest.approx(base)
    manager.set_zoom(1.5)
    assert qt_app.font().pointSizeF() == pytest.approx(max(10.0, base * 1.5))
    manager.set_theme("light")
    assert palette("light")["bg"] in qt_app.styleSheet()


def test_external_zoom_change_is_followed(manager, prefs):
    """Le zoom reste une préférence suivie ; le thème, lui, n'en est plus une."""
    got: list[str] = []
    manager.changed.connect(got.append)
    prefs.set("theme", "light")
    prefs.set("zoom", 1.25)
    assert manager.zoom() == 1.25
    assert manager.appearance() == "system" and manager.theme() == "dark"
    assert got == ["dark"]


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
