from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from azimut.core import binaries
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.ui import app as app_module
from azimut.ui.theme import TOKEN_NAMES, ThemeManager, build_qss, palette, token


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def prefs(tmp_path):
    return Preferences(tmp_path / "prefs.json")


@pytest.fixture
def manager(qt_app, prefs, monkeypatch):
    """ThemeManager isolé (préférences temporaires) installé comme singleton."""
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
    assert len(TOKEN_NAMES) == 24
    assert set(pal) == set(TOKEN_NAMES)
    for name in TOKEN_NAMES:
        assert re.fullmatch(r"#[0-9a-f]{6}", pal[name]), (theme, name)


def test_palette_values_match_spec():
    attendu = {
        "bg": ("#f3f5f9", "#12151a"),
        "primary": ("#2f6fed", "#3b82f6"),
        "ok_bg": ("#dff5e8", "#17382a"),
        "secondary_hover": ("#d5dbe7", "#363d49"),
        "danger": ("#d64545", "#c24141"),
    }
    for name, (clair, sombre) in attendu.items():
        assert palette("light")[name] == clair
        assert palette("dark")[name] == sombre


def test_palette_unknown_theme():
    with pytest.raises(ValueError):
        palette("sepia")


# ── QSS ──────────────────────────────────────────────────────────────────────
def test_build_qss_contains_theme_background():
    dark, light = build_qss("dark"), build_qss("light")
    assert palette("dark")["bg"] in dark and palette("light")["bg"] not in dark
    assert palette("light")["bg"] in light and palette("dark")["bg"] not in light


def test_build_qss_covers_widgets_and_button_kinds():
    qss = build_qss("dark")
    for sel in ("QMainWindow", "QFrame#Card", "QLineEdit", "QComboBox", "QCheckBox", "QProgressBar",
                "QTableView", "QHeaderView", "QPlainTextEdit", "QScrollBar", "QToolBar", "QDockWidget",
                "QTabWidget", "QTabBar", "QMenu", "QToolTip"):
        assert sel in qss, sel
    for kind in ("primary", "success", "danger", "accent", "secondary", "ghost"):
        assert f'QPushButton[kind="{kind}"]' in qss
    assert "$" not in qss and "__KINDS__" not in qss


def test_build_qss_zoom_scales_paddings():
    assert build_qss("dark", 1.6) != build_qss("dark", 1.0)


# ── ThemeManager ─────────────────────────────────────────────────────────────
def test_defaults(manager):
    assert manager.theme() == "dark"
    assert manager.zoom() == 1.0


def test_token_follows_current_theme(manager):
    assert token("bg") == "#12151a"
    manager.set_theme("light")
    assert token("bg") == "#f3f5f9"
    with pytest.raises(KeyError):
        token("inexistant")


def test_set_theme_persists_and_emits(manager, prefs, tmp_path):
    got: list[str] = []
    manager.changed.connect(got.append)
    manager.set_theme("light")
    manager.set_theme("light")                    # sans effet : pas de second signal
    assert got == ["light"]
    assert prefs.get("theme") == "light"
    assert ThemeManager(Preferences(tmp_path / "prefs.json")).theme() == "light"


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
    manager.apply(qt_app)
    assert palette("dark")["bg"] in qt_app.styleSheet()
    assert qt_app.font().pointSizeF() == pytest.approx(10.0)
    manager.set_zoom(1.5)
    assert qt_app.font().pointSizeF() == pytest.approx(15.0)
    manager.set_theme("light")
    assert palette("light")["bg"] in qt_app.styleSheet()


def test_external_preference_change_is_followed(manager, prefs):
    got: list[str] = []
    manager.changed.connect(got.append)
    prefs.set("theme", "light")
    prefs.set("zoom", 1.25)
    assert manager.theme() == "light"
    assert manager.zoom() == 1.25
    assert got == ["light", "light"]


def test_corrupt_preferences_fall_back_to_defaults(qt_app, tmp_path):
    prefs = Preferences(tmp_path / "p.json")
    prefs.set("theme", "nimporte")
    prefs.set("zoom", "abc")
    tm = ThemeManager(prefs)
    assert tm.theme() == "dark" and tm.zoom() == 1.0


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
