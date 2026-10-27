from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from azimut import __version__
from azimut.core import binaries
from azimut.core.binaries import BinaryInfo
from azimut.core.history import HistoryStore
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import LocalJsonStorage
from azimut.core.workers import BaseWorker
from azimut.ui import module_window as mw
from azimut.ui.module_window import ModuleWindow
from azimut.ui.preferences_dialog import PreferencesDialog
from azimut.ui.theme import ThemeManager

SPEC = ModuleSpec(id="fake", name="Azimut Fake", tagline="Module de test.", icon="🧪",
                  entry="x:Y", requires=("ffmpeg",), optional=("exiftool",))


# ── Doubles de test ──────────────────────────────────────────────────────────
class QuickWorker(BaseWorker):
    def __init__(self, steps: int = 3, fail: bool = False) -> None:
        super().__init__()
        self.steps, self.fail = steps, fail

    def do_work(self):
        for i in range(self.steps):
            self.check_cancelled()
            self.emit_log("INFO", f"étape {i + 1}")
            self.emit_progress(i + 1, self.steps, f"fichier{i + 1}.jpg")
        if self.fail:
            raise RuntimeError("boum")
        self.ok_count, self.error_count = self.steps, 1
        self.summary = f"{self.steps} fichiers traités"
        return {"steps": self.steps}


class BlockingWorker(BaseWorker):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def do_work(self):
        self.emit_log("INFO", "démarré")
        self.started.set()
        while not self.release.wait(0.02):
            self.check_cancelled()
        self.ok_count, self.summary = 1, "terminé"
        return "fini"


class ProbeWindow(ModuleWindow):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.calls: list[str] = []
        self.opened: list[list[str]] = []

    def on_open(self):
        self.calls.append("open")

    def on_run(self):
        self.calls.append("run")

    def open_paths(self, paths):
        self.opened.append(paths)


# ── Fixtures ─────────────────────────────────────────────────────────────────
def wait_until(qt_app, predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qt_app.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def _fake_resolve(name, prefs=None):
    if name == "ffmpeg":
        return BinaryInfo("ffmpeg", Path("/opt/ffmpeg"), "bundled", "7.1")
    return BinaryInfo(name, None, "missing", "")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def env(qt_app, tmp_path, monkeypatch):
    """Préférences, thème, historique et détection des binaires isolés."""
    prefs = Preferences(tmp_path / "prefs.json")
    monkeypatch.setattr(Preferences, "instance", classmethod(lambda cls: prefs))
    theme = ThemeManager(prefs)
    monkeypatch.setattr(ThemeManager, "_instance", theme)
    store = HistoryStore(LocalJsonStorage(tmp_path / "storage"))
    monkeypatch.setattr(mw, "HistoryStore", lambda *a, **k: store)
    monkeypatch.setattr(binaries, "resolve", _fake_resolve)
    return SimpleNamespace(prefs=prefs, theme=theme, store=store)


@pytest.fixture
def make_window(qt_app):
    created: list[ModuleWindow] = []

    def factory(cls=ProbeWindow, spec=SPEC):
        w = cls(spec)
        created.append(w)
        return w

    yield factory
    for w in created:
        w.close()
        w.deleteLater()
    qt_app.processEvents()


# ── Structure, boutons, raccourcis ───────────────────────────────────────────
def test_standard_toolbar_buttons_and_shortcuts(make_window):
    win = make_window()
    expected = {win.open_button: ("Ouvrir…", "Ctrl+O"), win.run_button: ("Lancer", "Ctrl+Return"),
                win.cancel_button: ("Annuler", "Esc"), win.log_button: ("Journal", "Ctrl+L"),
                win.theme_button: (None, "Ctrl+T"), win.prefs_button: ("Préférences…", "Ctrl+,"),
                win.help_button: ("Aide", "F1")}
    for btn, (text, shortcut) in expected.items():
        assert win.toolbar.isAncestorOf(btn)
        assert btn.shortcut() == QKeySequence(shortcut)
        if text:
            assert btn.text() == text


def test_default_state_and_run_controls(make_window):
    win = make_window()
    assert not win.is_busy()
    assert win.run_button.isEnabled() and win.open_button.isEnabled()
    assert not win.cancel_button.isEnabled()                 # actif seulement pendant un traitement
    win.set_run_label("Convertir")
    assert win.run_button.text() == "Convertir"
    win.set_run_enabled(False)
    assert not win.run_button.isEnabled()
    win.set_run_enabled(True)
    assert win.run_button.isEnabled()
    assert win.prefs is not None and win.spec is SPEC
    assert win.windowTitle() == "Azimut Fake"


def test_buttons_call_hooks(make_window):
    win = make_window()
    win.open_button.click()
    win.run_button.click()
    assert win.calls == ["open", "run"]
    win.set_run_enabled(False)
    win.run_button.click()
    assert win.calls == ["open", "run"]


def test_default_hooks_do_nothing(make_window):
    win = make_window(ModuleWindow)
    assert win.on_run() is None and win.on_open() is None and win.open_paths(["/x"]) is None


def test_header_and_binary_chips(make_window):
    win = make_window()
    assert [c.text() for c in win.binary_chips] == ["✔ ffmpeg", "✖ exiftool"]
    assert [c.property("level") for c in win.binary_chips] == ["ok", "warn"]   # exiftool : facultatif
    assert "7.1" in win.binary_chips[0].toolTip()
    win.show_binary_status(["ffmpeg", "ffprobe"])
    assert [c.property("level") for c in win.binary_chips] == ["ok", "warn"]
    assert len(win.binary_chips) == 2
    win2 = make_window(spec=ModuleSpec(id="f2", name="Azimut F2", tagline="t", icon="x", entry="a:B",
                                       requires=("exiftool",)))
    assert win2.binary_chips[0].property("level") == "err"   # obligatoire et absent
    assert win.tagline_label.text() == "Module de test."


def test_set_content_and_side_panel(make_window):
    win = make_window()
    first, second = QLabel("un"), QLabel("deux")
    win.set_content(first)
    assert win.centralWidget().isAncestorOf(first)
    win.set_content(second)
    assert win.centralWidget().isAncestorOf(second) and first.parent() is None

    panel = QWidget()
    assert win.side_dock is None
    win.set_side_panel(panel, "Options")
    assert win.side_dock.widget() is panel and win.side_dock.windowTitle() == "Options"
    assert not win.side_dock.isHidden() and not win.side_dock.isFloating()
    win.set_side_panel(None)
    assert win.side_dock.isHidden()


def test_add_toolbar_button(make_window):
    win = make_window()
    hits: list[int] = []
    btn = win.add_toolbar_button("Extra", lambda: hits.append(1), kind="primary", shortcut="Ctrl+E",
                                 tooltip="Un bouton")
    assert win.toolbar.isAncestorOf(btn) and btn.property("kind") == "primary"
    assert btn.shortcut() == QKeySequence("Ctrl+E") and "Un bouton" in btn.toolTip()
    btn.click()
    assert hits == [1]
    assert win.add_toolbar_button("X", lambda: None, kind="inconnu").property("kind") == "secondary"


def test_toggle_log_dock(make_window):
    win = make_window()
    assert not win.log_dock.isHidden()
    win.log_button.click()
    assert win.log_dock.isHidden()
    win.log_button.click()
    assert not win.log_dock.isHidden()
    assert win.bottom_tabs.tabText(0) == "Journal" and win.bottom_tabs.tabText(1) == "Historique"


def test_theme_button_toggles_theme(make_window, env):
    win = make_window()
    assert "Clair" in win.theme_button.text()
    win.theme_button.click()
    assert env.theme.theme() == "light" and "Sombre" in win.theme_button.text()


def test_help_dialog_content(make_window):
    win = make_window()
    dlg = win._build_help_dialog()
    text = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    for needle in ("Azimut Fake", "Module de test.", __version__, "Ctrl+O", "F1", "Échap"):
        assert needle in text, needle


def test_drop_forwards_paths_unless_busy(make_window, qt_app, tmp_path):
    win = make_window()
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f))])
    assert win.handle_drop(mime) == [os.path.abspath(str(f))]
    assert win.opened == [[os.path.abspath(str(f))]]
    assert win.handle_drop(QMimeData()) == []
    w = BlockingWorker()
    win.run_worker(w)
    assert w.started.wait(5)
    assert win.handle_drop(mime) == [] and len(win.opened) == 1
    w.release.set()
    assert wait_until(qt_app, lambda: not win.is_busy())


# ── run_worker ───────────────────────────────────────────────────────────────
def test_run_worker_success_progress_log_history(make_window, qt_app, env):
    win = make_window()
    results: list[object] = []
    win.run_worker(QuickWorker(3), on_completed=results.append, history_action="Conversion")
    assert wait_until(qt_app, lambda: not win.is_busy() and bool(results))
    assert results == [{"steps": 3}]
    assert win.progress_panel.count_label.text() == "3 / 3 (100 %)"
    assert win.progress_panel.status_label.text() == "Terminé"
    text = win.log_panel.toPlainText()
    assert "étape 1" in text and "3 fichiers traités" in text
    entries = env.store.recent(module="fake")
    assert len(entries) == 1
    e = entries[0]
    assert (e.module, e.action, e.summary, e.ok, e.errors) == ("fake", "Conversion", "3 fichiers traités", 3, 1)
    rows = win.history_table.data_model.rows()
    assert len(rows) == 1 and rows[0]["summary"] == "3 fichiers traités" and rows[0]["action"] == "Conversion"
    win.run_worker(QuickWorker(1))                           # sans history_action : pas de nouvelle entrée
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert len(env.store.recent(module="fake")) == 1


def test_lock_widgets_disabled_then_reenabled_and_second_worker_refused(make_window, qt_app):
    win = make_window()
    locked, already_off = QPushButton("verrouillé"), QPushButton("déjà inactif")
    already_off.setEnabled(False)
    w = BlockingWorker()
    win.run_worker(w, lock_widgets=[locked, already_off])
    assert w.started.wait(5)
    assert win.is_busy() and not locked.isEnabled()
    assert not win.run_button.isEnabled() and not win.open_button.isEnabled() and win.cancel_button.isEnabled()

    second = QuickWorker()
    win.run_worker(second)
    assert not second.isRunning() and not second.isFinished()
    assert "déjà en cours" in win.log_panel.toPlainText()

    w.release.set()
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert locked.isEnabled() and not already_off.isEnabled()   # l'état d'origine est restauré
    assert win.run_button.isEnabled() and win.open_button.isEnabled() and not win.cancel_button.isEnabled()


def test_cancel_current(make_window, qt_app, env):
    win = make_window()
    cancelled: list[bool] = []
    completed: list[object] = []
    w = BlockingWorker()
    win.run_worker(w, on_cancelled=lambda: cancelled.append(True), on_completed=completed.append,
                   history_action="Analyse")
    assert w.started.wait(5)
    win.cancel_button.click()
    assert not win.cancel_button.isEnabled()                 # une seule demande d'annulation
    assert wait_until(qt_app, lambda: not win.is_busy() and bool(cancelled))
    assert w.is_cancelled() and cancelled == [True] and completed == []
    assert win.progress_panel.status_label.text() == "Annulé"
    assert "annulé" in win.log_panel.toPlainText().lower()
    assert [e.action for e in env.store.recent(module="fake")] == ["Analyse"]


def test_worker_failure(make_window, qt_app, env):
    win = make_window()
    failures: list[str] = []
    win.run_worker(QuickWorker(1, fail=True), on_failed=failures.append, history_action="Analyse")
    assert wait_until(qt_app, lambda: not win.is_busy() and bool(failures))
    assert "boum" in failures[0]
    assert "Échec" in win.log_panel.toPlainText()
    assert win.progress_panel.status_label.text() == "Échec"
    assert win.run_button.isEnabled()
    assert env.store.recent(module="fake")[0].summary.startswith("Échec")


def test_failing_callback_does_not_break_window(make_window, qt_app):
    win = make_window()

    def bad(_result):
        raise ValueError("rappel défaillant")

    win.run_worker(QuickWorker(1), on_completed=bad)
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert wait_until(qt_app, lambda: "rappel défaillant" in win.log_panel.toPlainText())
    win.run_worker(QuickWorker(1))                           # la fenêtre reste utilisable
    assert wait_until(qt_app, lambda: not win.is_busy())


def test_close_cancels_running_worker(make_window):
    win = make_window()
    w = BlockingWorker()
    win.run_worker(w)
    assert w.started.wait(5)
    win.close()
    assert w.is_cancelled() and not w.isRunning()


# ── Boîte de préférences ─────────────────────────────────────────────────────
def test_preferences_dialog_initial_state(env):
    env.theme.set_zoom(1.2)                                  # valeur hors liste : choix le plus proche (125 %)
    dlg = PreferencesDialog()
    assert dlg.theme_combo.currentData() == "dark"
    assert dlg.zoom_combo.currentIndex() == 3 and dlg.zoom_combo.currentText() == "125 %"
    assert set(dlg.fields) == {"ffmpeg", "ffprobe", "exiftool"}
    assert dlg.chips["ffmpeg"].text() == "✔ embarqué · 7.1"
    assert dlg.chips["exiftool"].text() == "✖ introuvable"


def test_preferences_dialog_saves_on_accept(env, monkeypatch, tmp_path):
    refreshed: list[int] = []
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: refreshed.append(1) or {})
    custom = tmp_path / "mon_ffmpeg"
    dlg = PreferencesDialog()
    dlg.theme_combo.setCurrentIndex(1)
    dlg.zoom_combo.setCurrentIndex(3)
    dlg.fields["ffmpeg"].set_path(str(custom))
    assert env.theme.theme() == "dark"                       # rien n'est écrit avant la validation
    dlg.accept()
    assert env.theme.theme() == "light" and env.theme.zoom() == 1.25
    assert env.prefs.get("theme") == "light" and env.prefs.get("zoom") == 1.25
    assert env.prefs.get("binaries.ffmpeg") == str(custom)
    assert refreshed == [1]


def test_preferences_dialog_reject_writes_nothing(env):
    dlg = PreferencesDialog()
    dlg.theme_combo.setCurrentIndex(1)
    dlg.fields["ffmpeg"].set_path("/quelque/part")
    dlg.reject()
    assert env.theme.theme() == "dark" and not env.prefs.get("binaries.ffmpeg")


def test_preferences_dialog_detect_button(env, monkeypatch):
    calls: list[int] = []
    infos = {"ffmpeg": BinaryInfo("ffmpeg", Path("/usr/bin/ffmpeg"), "system", "7.0"),
             "ffprobe": BinaryInfo("ffprobe", None, "missing", ""),
             "exiftool": BinaryInfo("exiftool", Path("/x/exiftool"), "custom", "12.76")}
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: calls.append(1) or infos)
    dlg = PreferencesDialog()
    dlg.detect_buttons["ffmpeg"].click()
    assert calls == [1]
    assert dlg.chips["ffmpeg"].text() == "✔ système · 7.0"
    assert dlg.chips["ffprobe"].text() == "✖ introuvable" and dlg.chips["ffprobe"].property("level") == "err"
    assert dlg.chips["exiftool"].text() == "✔ personnalisé · 12.76"
