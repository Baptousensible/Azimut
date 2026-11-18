from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QCloseEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from azimut import __version__
from azimut.core import binaries
from azimut.core.binaries import BinaryInfo
from azimut.core.history import HistoryStore
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import LocalJsonStorage
from azimut.core.workers import BaseWorker
from azimut.ui import module_window as mw
from azimut.ui.flow_layout import FlowLayout
from azimut.ui.module_window import ModuleWindow
from azimut.ui.preferences_dialog import PreferencesDialog, PreferencesPage, describe_binary
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import Card, ImportProgress

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


class ProgressBlockingWorker(BaseWorker):
    """Émet une progression puis attend : permet de vérifier quelle barre la reçoit."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def do_work(self):
        self.emit_progress(1, 2, "a.jpg")
        self.started.set()
        while not self.release.wait(0.02):
            self.check_cancelled()
        self.summary = "2 fichiers traités"
        return None


class StubbornWorker(BaseWorker):
    """Ignore l'annulation : simule un traitement qui ne peut pas s'interrompre tout de suite."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()

    def do_work(self):
        self.started.set()
        time.sleep(1.5)                              # ni check_cancelled() ni run_process() : le drapeau est ignoré
        self.summary = "fini"
        return "fini"


class ProbeWindow(ModuleWindow):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.calls: list[str] = []
        self.opened: list[list[str]] = []

    def on_open(self):
        self.calls.append("open")

    def on_open_folder(self):
        self.calls.append("folder")

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
    monkeypatch.setattr(binaries, "refresh_in_progress", lambda: False)
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
def test_tool_header_icon_stays_static(make_window):
    """L'icône de l'outil, dans son onglet ouvert, ne s'anime pas au survol."""
    win = make_window()
    assert not hasattr(win, "_header_icon_hover")


def test_standard_toolbar_buttons_and_shortcuts(make_window):
    win = make_window()
    toolbar_buttons = {win.prefs_button: ("Préférences…", "Ctrl+,")}
    assert not hasattr(win, "theme_button")           # le thème se règle dans Préférences
    for btn, (text, shortcut) in toolbar_buttons.items():
        assert win.toolbar.isAncestorOf(btn)
        assert btn.shortcut() == QKeySequence(shortcut)
        if text:
            assert btn.text() == text

    header_buttons = {win.log_button: ("Journal", "Ctrl+L"),
                      win.help_button: ("Aide", "F1")}
    for btn, (text, shortcut) in header_buttons.items():
        assert win.header.isAncestorOf(btn)
        assert btn.shortcut() == QKeySequence(shortcut)
        assert btn.text() == text

    action_buttons = {win.run_button: ("Démarrer", "Ctrl+Return"),
                      win.cancel_button: ("Annuler", "Esc")}
    for btn, (text, shortcut) in action_buttons.items():
        assert win.action_layout.indexOf(btn) >= 0
        assert not win.toolbar.isAncestorOf(btn)
        assert btn.shortcut() == QKeySequence(shortcut)
        assert btn.text() == text


def test_embedded_mode_hides_global_toolbar_controls(make_window, qt_app):
    win = make_window()
    win.show()
    qt_app.processEvents()
    win.set_embedded(True)
    assert win.toolbar.isHidden()
    assert win.header.property("embedded") is True
    assert win._outer_layout.contentsMargins().top() == 0
    assert win.prefs_button.isHidden()
    assert not win._prefs_action.isVisible()
    assert win.prefs_button.shortcut().isEmpty()
    assert not hasattr(win, "theme_button") and not hasattr(win, "_theme_action")
    assert (not win.log_button.isHidden() and not win.help_button.isHidden()
            and win.header.isAncestorOf(win.log_button) and win.header.isAncestorOf(win.help_button))

    win.set_embedded(False)
    qt_app.processEvents()
    assert not win.toolbar.isHidden()
    assert win.header.property("embedded") is False
    assert win._outer_layout.contentsMargins().top() == 12
    assert not win.prefs_button.isHidden()
    assert win._prefs_action.isVisible()
    assert win.prefs_button.shortcut() == QKeySequence("Ctrl+,")


def test_default_state_and_run_controls(make_window):
    win = make_window()
    assert not win.is_busy()
    assert win.run_button.isEnabled()
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
    win.run_button.click()
    assert win.calls == ["run"]
    win.set_run_enabled(False)
    win.run_button.click()
    assert win.calls == ["run"]


def test_clear_list_confirmation_is_safe_by_default(make_window, monkeypatch):
    """Le dialogue de vidage propose Oui/Non et choisit « Non » par défaut."""
    win = make_window()
    calls = []

    def question(*args):
        calls.append(args)
        return mw.QMessageBox.StandardButton.No

    monkeypatch.setattr(mw, "QMessageBox", SimpleNamespace(
        question=question, StandardButton=mw.QMessageBox.StandardButton))
    assert win.confirm_clear_list() is False
    _parent, title, text, _buttons, default = calls[0]
    assert title == "Vider la liste"
    assert "Les fichiers ne seront pas modifiés" in text
    assert default == mw.QMessageBox.StandardButton.No


def test_import_buttons_removed(make_window):
    win = make_window()
    assert not hasattr(win, "open_button") and not hasattr(win, "folder_button")
    assert all("Importer" not in text for _key, text in win._help_shortcuts())


def test_default_hooks_do_nothing(make_window):
    win = make_window(ModuleWindow)
    assert win.on_run() is None and win.on_open() is None and win.on_open_folder() is None
    assert win.open_paths(["/x"]) is None


def test_header_shows_papicons_icon_instead_of_its_name(make_window):
    win = make_window(spec=ModuleSpec(id="gallery", name="Azimut Galerie", tagline="t",
                                      icon="gallery", entry="x:Y"))
    assert not win.header_icon.pixmap().isNull()
    assert "gallery" not in win.title_label.text()
    assert "Azimut Galerie" in win.title_label.text()
    fallback = make_window(spec=ModuleSpec(id="fallback", name="Azimut Test", tagline="t",
                                           icon="🧪", entry="x:Y"))
    assert fallback.header_icon.text() == "🧪"
    assert fallback.header_icon.pixmap().isNull()


def test_header_and_binary_chips(make_window):
    win = make_window()
    assert [c.text() for c in win.binary_chips] == ["ffmpeg", "exiftool"]
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
    title = win.side_dock.titleBarWidget().findChild(QLabel, "SideDockTitle")
    assert title is not None and title.text() == "Options"
    assert not win.side_dock.isHidden() and not win.side_dock.isFloating()
    win.set_side_panel(None)
    assert win.side_dock.isHidden()


def test_side_panel_scroll_content_stays_transparent(make_window):
    """QScrollArea active l'auto-remplissage de son contenu : le panneau doit le désactiver,
    sinon un grand rectangle opaque remplace le fond en verre (Convert, Rename)."""
    win = make_window()
    inner = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    assert inner.autoFillBackground()                   # comportement de QScrollArea
    win.set_side_panel(scroll, "Réglages")
    assert win.side_dock.widget() is scroll
    assert not inner.autoFillBackground()


def test_add_action_button(make_window):
    win = make_window()
    hits: list[int] = []
    btn = win.add_action_button("Extra", lambda: hits.append(1), kind="primary", shortcut="Ctrl+E",
                                tooltip="Un bouton")
    assert win.action_layout.indexOf(btn) >= 0 and btn.property("kind") == "primary"
    assert win.action_layout.indexOf(win.run_button) < win.action_layout.indexOf(btn)
    assert btn.shortcut() == QKeySequence("Ctrl+E") and "Un bouton" in btn.toolTip()
    btn.click()
    assert hits == [1]
    assert win.add_action_button("X", lambda: None, kind="inconnu").property("kind") == "secondary"


def test_flow_layout_wraps_long_buttons(qt_app):
    """Une barre étroite place les boutons sur plusieurs lignes, sans rogner leur texte."""
    host = QWidget()
    host.setFixedSize(520, 160)
    flow = FlowLayout(h_spacing=8, v_spacing=6)
    host.setLayout(flow)
    buttons = []
    for i in range(4):
        button = QPushButton(f"Une action assez longue {i}")
        flow.addWidget(button)
        buttons.append(button)
    host.show()
    qt_app.processEvents()
    assert all(button.width() >= button.sizeHint().width() for button in buttons)
    assert len({button.y() for button in buttons}) > 1


def test_action_bar_uses_the_wrapping_layout(make_window):
    assert isinstance(make_window().action_layout, FlowLayout)


def test_side_panel_keeps_room_for_its_widest_content(make_window):
    """Le panneau s'élargit pour son contenu au lieu de couper les libellés longs."""
    win = make_window()
    panel = QWidget()
    lay = QVBoxLayout(panel)
    label = QLabel("Réglage très long " * 8)
    lay.addWidget(label)
    win.set_side_panel(panel, "Options")
    assert win.side_dock.minimumWidth() >= label.minimumSizeHint().width()


def test_toggle_log_dock(make_window, qt_app):
    win = make_window()
    win.show()
    qt_app.processEvents()
    assert win.log_dock.isHidden()                            # replié à l'ouverture
    assert win.log_button.isCheckable() and not win.log_button.isChecked()
    win.log_button.click()
    assert not win.log_dock.isHidden()
    assert win.log_button.isChecked()                         # l'état du bouton suit le panneau
    win.log_button.click()
    assert win.log_dock.isHidden()
    assert not win.log_button.isChecked()
    win.log_dock.setVisible(True)                             # autre chemin (menu, Ctrl+L)
    assert win.log_button.isChecked()
    win.log_dock.hide()
    assert not win.log_button.isChecked()
    assert win.bottom_tabs.tabText(0) == "Journal" and win.bottom_tabs.tabText(1) == "Historique"


def test_theme_menu_action_toggles_theme(make_window, env):
    win = make_window()
    view_menu = next(action.menu() for action in win.menuBar().actions()
                     if action.text() == "Présentation")
    theme_action = next(action for action in view_menu.actions()
                        if action.text() == "Basculer entre thème sombre et clair")
    assert theme_action.shortcut() == QKeySequence("Ctrl+T")
    theme_action.trigger()
    assert env.theme.theme() == "light"


def test_menu_bar_exposes_core_commands(make_window):
    win = make_window()
    titles = [action.text() for action in win.menuBar().actions()]
    for title in ("Azimut", "Fichier", "Édition", "Présentation", "Fenêtre", "Aide"):
        assert title in titles, titles
    app_menu = win.menuBar().actions()[0].menu()
    labels = [action.text() for action in app_menu.actions()]
    assert "Préférences…" in labels and "Quitter" in labels


def test_edit_menu_exposes_the_module_undo_command(make_window):
    win = make_window()
    edit_menu = next(action.menu() for action in win.menuBar().actions() if action.text() == "Édition")
    labels = [action.text() for action in edit_menu.actions()]
    assert "Annuler le dernier renommage" in labels
    assert "Copier" in labels and "Tout sélectionner" in labels


def test_progress_bar_lives_in_the_action_bar_not_above_the_drop_zone(make_window):
    win = make_window()
    assert win.progress_bar.parent() is win.action_bar
    assert win.progress_bar.isHidden()                    # au repos, aucune piste vide
    assert not win.header.findChildren(type(win.progress_bar))


def _shortcut_label(sequence: str) -> str:
    """Libellé d'un raccourci tel que Qt le rend sur cette plateforme (⌘↩ sur macOS).

    Hors macOS, la fenêtre traduit les touches en français ; on reproduit ce rendu au lieu
    de figer « Ctrl+Entrée ».
    """
    label = QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)
    if sys.platform != "darwin":
        label = label.replace("Return", "Entrée").replace("Enter", "Entrée").replace("Esc", "Échap")
    return label


def test_help_dialog_content(make_window):
    win = make_window()
    dlg = win._build_help_dialog()
    text = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    for needle in ("Azimut Fake", "Module de test.", __version__,
                   _shortcut_label("Ctrl+Return"), _shortcut_label("F1"), _shortcut_label("Esc")):
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
    assert win.progress_bar.isHidden()            # aucune info d'import ne reste affichée
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


def test_run_worker_keeps_the_liquid_bar_visible_for_a_minimum_time(make_window, qt_app):
    """Une application instantanée laisse quand même le temps de voir la barre s'animer."""
    win = make_window()
    started = time.monotonic()
    win.run_worker(QuickWorker(1), min_progress_ms=250)
    assert not win.progress_bar.isHidden()          # visible dès le départ, même sans progression
    assert wait_until(qt_app, lambda: not win.is_busy(), timeout=2)
    assert time.monotonic() - started >= 0.18       # le worker a fini bien avant, la barre est restée
    assert win.progress_bar.isHidden()


def test_run_worker_import_progress_is_temporary(make_window, qt_app):
    win = make_window()
    progress = ImportProgress()
    progress.completion_ms = 20
    progress.min_progress_ms = 0
    progress.value_ms = 0
    win.run_worker(QuickWorker(2), import_progress=progress)
    assert progress.is_importing()
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert not progress.is_importing()
    assert wait_until(qt_app, lambda: progress.isHidden(), timeout=2)


def test_run_worker_can_leave_progress_to_the_import_zone(make_window, qt_app):
    win = make_window()
    progress = ImportProgress()
    progress.completion_ms = 20
    progress.min_progress_ms = 0
    progress.value_ms = 0
    worker = ProgressBlockingWorker()
    win.run_worker(worker, import_progress=progress, show_progress=False)
    assert worker.started.wait(5)
    QTest.qWait(50)                              # laisse passer la progression du worker
    assert progress.is_importing()
    assert win.progress_bar.isHidden()           # aucune barre dans la barre d'actions
    worker.release.set()
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert not progress.is_importing()


def test_a_finished_treatment_shows_a_liquid_notification(make_window, qt_app):
    win = make_window()
    win.run_worker(QuickWorker(1), history_action="Traitement")
    assert wait_until(qt_app, lambda: not win.is_busy())
    toast = getattr(win, "_azimut_notification", None)
    assert toast is not None
    assert toast.title() == SPEC.name and toast.message() == "1 fichiers traités"
    assert toast.kind() == "success"
    toast.dismiss()


def test_a_finished_treatment_prefers_its_short_notification_summary(make_window, qt_app):
    win = make_window()
    worker = QuickWorker(1)
    worker.notification_summary = "1 fichier(s) traité(s) · 0 erreur(s)"
    win.run_worker(worker, history_action="Conversion")
    assert wait_until(qt_app, lambda: not win.is_busy())
    toast = getattr(win, "_azimut_notification", None)
    assert toast is not None and toast.message() == worker.notification_summary
    toast.dismiss()


def test_an_import_without_history_does_not_show_a_notification(make_window, qt_app):
    win = make_window()
    win.run_worker(QuickWorker(1))
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert getattr(win, "_azimut_notification", None) is None


def test_run_worker_can_animate_several_import_zones(make_window, qt_app):
    win = make_window()
    first, second = ImportProgress(), ImportProgress()
    for progress in (first, second):
        progress.completion_ms = 20
        progress.min_progress_ms = 0
        progress.value_ms = 0
    win.run_worker(QuickWorker(2), import_progress=[first, second])
    assert first.is_importing() and second.is_importing()
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert not first.is_importing() and not second.is_importing()
    assert wait_until(qt_app, lambda: first.isHidden() and second.isHidden(), timeout=2)


def test_busy_changed_signal_tracks_worker_lifecycle(make_window, qt_app):
    win = make_window()
    states: list[bool] = []
    win.busyChanged.connect(states.append)
    w = BlockingWorker()
    win.run_worker(w)
    assert w.started.wait(5)
    assert states == [True]
    w.release.set()
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert states == [True, False]


def test_lock_widgets_disabled_then_reenabled_and_second_worker_refused(make_window, qt_app):
    win = make_window()
    locked, already_off = QPushButton("verrouillé"), QPushButton("déjà inactif")
    already_off.setEnabled(False)
    w = BlockingWorker()
    win.run_worker(w, lock_widgets=[locked, already_off])
    assert w.started.wait(5)
    assert win.is_busy() and not locked.isEnabled()
    assert not win.run_button.isEnabled() and win.cancel_button.isEnabled()

    second = QuickWorker()
    win.run_worker(second)
    assert not second.isRunning() and not second.isFinished()
    assert "déjà en cours" in win.log_panel.toPlainText()

    w.release.set()
    assert wait_until(qt_app, lambda: not win.is_busy())
    assert locked.isEnabled() and not already_off.isEnabled()   # l'état d'origine est restauré
    assert win.run_button.isEnabled() and not win.cancel_button.isEnabled()


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
    assert win.progress_bar.isHidden()
    assert "annulé" in win.log_panel.toPlainText().lower()
    assert [e.action for e in env.store.recent(module="fake")] == ["Analyse"]


def test_worker_failure(make_window, qt_app, env):
    win = make_window()
    failures: list[str] = []
    win.run_worker(QuickWorker(1, fail=True), on_failed=failures.append, history_action="Analyse")
    assert wait_until(qt_app, lambda: not win.is_busy() and bool(failures))
    assert "boum" in failures[0]
    assert "Échec" in win.log_panel.toPlainText()
    assert win.progress_bar.isHidden()
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


class CloseCountingWindow(ProbeWindow):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.close_events = 0

    def closeEvent(self, event):
        self.close_events += 1
        super().closeEvent(event)


def test_close_is_deferred_until_the_worker_really_stops(make_window, qt_app, monkeypatch):
    """Un worker qui ignore l'annulation ne doit pas être détruit avec la fenêtre : la fermeture est
    refusée puis reportée à sa fin réelle (CORE-5)."""
    monkeypatch.setattr(mw, "_CLOSE_WAIT_MS", 200, raising=False)
    win = make_window(CloseCountingWindow)
    w = StubbornWorker()
    win.run_worker(w)
    assert w.started.wait(5)
    event = QCloseEvent()
    win.closeEvent(event)
    assert not event.isAccepted(), "la fenêtre a accepté sa fermeture alors que le worker tourne encore"
    assert win._close_pending and win.close_events == 1
    assert wait_until(qt_app, lambda: win.close_events == 2), "la fermeture reportée n'a pas eu lieu"
    assert not win._close_pending and not win.is_busy()


# ── Boîte de préférences ─────────────────────────────────────────────────────
def test_preferences_dialog_initial_state(env):
    env.theme.set_zoom(1.2)                                  # valeur hors liste : choix le plus proche (125 %)
    dlg = PreferencesDialog()
    assert dlg.theme_combo.currentData() == "system"
    assert dlg.zoom_combo.currentIndex() == 3 and dlg.zoom_combo.currentText() == "125 %"
    assert dlg.icon_combo.currentData() == "papicons"
    assert set(dlg.fields) == {"ffmpeg", "ffprobe", "exiftool"}
    assert dlg.chips["ffmpeg"].text() == "embarqué · 7.1"
    assert dlg.chips["exiftool"].text() == "introuvable"


def test_preferences_cards_and_language_field_stay_compact(env, qt_app):
    """Les cartes gardent une largeur de lecture et le champ de langue reste court."""
    dlg = PreferencesDialog()
    dlg.resize(1400, 900)
    dlg.show()
    qt_app.processEvents()
    cards = dlg.page.findChildren(Card)
    assert len(cards) >= 3
    assert all(card.width() <= dlg.page.CONTENT_MAX_WIDTH for card in cards)
    assert dlg.language_combo.width() <= 320
    dlg.close()


def test_preferences_dialog_saves_on_accept(env, monkeypatch, tmp_path):
    refreshed: list[int] = []
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: refreshed.append(1) or {})
    custom = tmp_path / "mon_ffmpeg"
    dlg = PreferencesDialog()
    dlg.theme_combo.setCurrentIndex(dlg.theme_combo.findData("light"))
    dlg.zoom_combo.setCurrentIndex(3)
    dlg.icon_combo.setCurrentIndex(dlg.icon_combo.findData("animated"))
    dlg.fields["ffmpeg"].set_path(str(custom))
    assert env.theme.theme() == "dark"                       # rien n'est écrit avant la validation
    dlg.accept()
    assert env.theme.theme() == "light" and env.theme.zoom() == 1.25
    assert env.theme.icon_theme() == "animated"
    assert env.prefs.get("zoom") == 1.25 and env.prefs.get("icon_theme") == "animated"
    assert not env.prefs.contains("theme")                   # le thème n'est pas mémorisé
    assert env.prefs.get("binaries.ffmpeg") == str(custom)
    assert refreshed == [1]


def test_preferences_dialog_reject_writes_nothing(env):
    dlg = PreferencesDialog()
    dlg.theme_combo.setCurrentIndex(dlg.theme_combo.findData("light"))
    dlg.fields["ffmpeg"].set_path("/quelque/part")
    dlg.reject()
    assert env.theme.theme() == "dark" and not env.prefs.get("binaries.ffmpeg")


def test_preferences_dialog_saves_accessibility_flags(env):
    dlg = PreferencesDialog()
    dlg.reduce_motion.setChecked(True)
    dlg.reduce_transparency.setChecked(True)
    dlg.high_contrast.setChecked(True)
    dlg.accept()
    assert env.prefs.get("reduce_motion") is True
    assert env.prefs.get("reduce_transparency") is True
    assert env.prefs.get("high_contrast") is True
    assert env.theme.reduce_motion() and env.theme.reduce_transparency() and env.theme.high_contrast()


def test_preferences_dialog_detect_button(env, monkeypatch):
    calls: list[int] = []
    infos = {"ffmpeg": BinaryInfo("ffmpeg", Path("/usr/bin/ffmpeg"), "system", "7.0"),
             "ffprobe": BinaryInfo("ffprobe", None, "missing", ""),
             "exiftool": BinaryInfo("exiftool", Path("/x/exiftool"), "custom", "12.76")}
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: calls.append(1) or infos)
    dlg = PreferencesDialog()
    dlg.detect_buttons["ffmpeg"].click()
    assert calls == [1]
    assert dlg.chips["ffmpeg"].text() == "système · 7.0"
    assert dlg.chips["ffprobe"].text() == "introuvable" and dlg.chips["ffprobe"].property("level") == "err"
    assert dlg.chips["exiftool"].text() == "personnalisé · 12.76"
    assert all(button.isEnabled() for button in dlg.detect_buttons.values())


def test_describe_binary_warns_about_outdated_version():
    info = BinaryInfo("exiftool", Path("/usr/bin/exiftool"), "system", "12.20", outdated=True)
    text, level, tip = describe_binary(info)
    assert text == "⚠ système · 12.20" and level == "warn"
    # Chemin rendu par pathlib : les séparateurs suivent la plateforme.
    assert "12.24" in tip and str(Path("/usr/bin/exiftool")) in tip


def test_preferences_dialog_detect_shows_outdated_version(env, monkeypatch):
    infos = {"ffmpeg": BinaryInfo("ffmpeg", Path("/usr/bin/ffmpeg"), "system", "5.1"),
             "ffprobe": BinaryInfo("ffprobe", None, "missing", ""),
             "exiftool": BinaryInfo("exiftool", Path("/x/exiftool"), "system", "12.20", outdated=True)}
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: infos)
    dlg = PreferencesDialog()
    dlg.detect_buttons["exiftool"].click()
    chip = dlg.chips["exiftool"]
    assert chip.text() == "système · 12.20" and chip.property("level") == "warn"
    assert "minimum recommandé (12.24)" in chip.toolTip()


def test_preferences_dialog_detect_failure_is_reported(env, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("lecture impossible")
    monkeypatch.setattr(binaries, "refresh", boom)
    dlg = PreferencesDialog()
    dlg.detect_buttons["ffmpeg"].click()
    assert all(chip.property("level") == "err" for chip in dlg.chips.values())
    assert "détection impossible" in dlg.chips["ffmpeg"].text()
    assert all(button.isEnabled() for button in dlg.detect_buttons.values())


def test_preferences_dialog_typed_chip_resets_when_the_field_changes(env, monkeypatch):
    infos = {"ffmpeg": BinaryInfo("ffmpeg", Path("/usr/bin/ffmpeg"), "system", "7.0"),
             "ffprobe": BinaryInfo("ffprobe", None, "missing", ""),
             "exiftool": BinaryInfo("exiftool", Path("/x/exiftool"), "custom", "12.76")}
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: infos)
    dlg = PreferencesDialog()
    dlg.fields["ffmpeg"].set_path("/opt/mon-ffmpeg")       # jamais vérifié dans cette boîte
    assert dlg.chips["ffmpeg"].text() == "… non vérifié"
    assert dlg.chips["ffmpeg"].property("level") == "neutre"
    dlg.fields["ffmpeg"].set_path("")                      # retour au chemin enregistré (vide)
    assert dlg.chips["ffmpeg"].text() == "embarqué · 7.1"


def test_preferences_dialog_keeps_typed_chip_of_other_rows(env, monkeypatch):
    infos = {"ffmpeg": BinaryInfo("ffmpeg", Path("/usr/bin/ffmpeg"), "system", "7.0"),
             "ffprobe": BinaryInfo("ffprobe", None, "missing", ""),
             "exiftool": BinaryInfo("exiftool", Path("/x/exiftool"), "custom", "12.76")}
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: infos)
    dlg = PreferencesDialog()
    dlg.fields["exiftool"].set_path("/opt/exiftool")
    dlg.detect_buttons["ffmpeg"].click()                    # détecter une autre ligne
    assert dlg.chips["ffmpeg"].text() == "système · 7.0"
    assert dlg.chips["exiftool"].text() == "… non vérifié"


def test_preferences_dialog_detect_typed_path_follows_resolver_rules(env, monkeypatch, tmp_path, qt_app):
    """Le chemin saisi est vérifié comme le résolveur : ~ développé, relatif refusé."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    exe = tmp_path / "mon-ffmpeg"
    exe.write_text("#!/bin/sh\necho 'ffmpeg version 8.0'\n", encoding="utf-8")
    exe.chmod(0o755)
    infos = {"ffmpeg": BinaryInfo("ffmpeg", None, "missing", ""),
             "ffprobe": BinaryInfo("ffprobe", None, "missing", ""),
             "exiftool": BinaryInfo("exiftool", None, "missing", "")}
    monkeypatch.setattr(binaries, "refresh", lambda *a, **k: infos)
    monkeypatch.setattr(binaries, "probe_version", lambda name, path: "8.0")

    dlg = PreferencesDialog()
    dlg.fields["ffmpeg"].set_path("~/mon-ffmpeg")
    dlg.detect_buttons["ffmpeg"].click()
    assert wait_until(qt_app, lambda: dlg.chips["ffmpeg"].text() == "saisi · 8.0")
    assert dlg.chips["ffmpeg"].property("level") == "info"

    dlg.fields["ffmpeg"].set_path("mon-ffmpeg")          # relatif : dépendrait du dossier courant
    dlg.detect_buttons["ffmpeg"].click()
    assert dlg.chips["ffmpeg"].text() == "chemin saisi inutilisable"
    assert dlg.chips["ffmpeg"].property("level") == "warn"


# ── Langue ──────────────────────────────────────────────────────────────────
def test_preferences_dialog_language_list(env):
    dlg = PreferencesDialog()
    labels = [dlg.language_combo.itemText(i) for i in range(dlg.language_combo.count())]
    assert dlg.language_combo.currentData() == "auto"               # automatique par défaut
    assert labels[0].startswith("Automatique")
    assert len(labels) > 20                                         # plus de vingt langues offertes
    assert "English" in labels                                      # catalogue présent : pas de mention
    assert not any("traduction à venir" in label for label in labels)  # toutes les langues sont livrées


def test_preferences_dialog_flags_languages_without_catalog(env, monkeypatch):
    from azimut.core import i18n
    from azimut.ui import preferences_dialog

    monkeypatch.setattr(i18n, "is_available", lambda code: code != "ja")
    dlg = PreferencesDialog()
    label = dlg.language_combo.itemText(dlg.language_combo.findData("ja"))
    assert label == "日本語" + preferences_dialog.pending_suffix()


def test_preferences_dialog_language_is_saved(env):
    dlg = PreferencesDialog()
    index = dlg.language_combo.findData("de")
    assert index > 0
    dlg.language_combo.setCurrentIndex(index)
    dlg.accept()
    assert env.prefs.get("language") == "de"
    assert PreferencesDialog().language_combo.currentData() == "de"  # sélection relue


def test_preferences_dialog_save_button_follows_language_change(env):
    dlg = PreferencesDialog(allow_restart=True)
    assert dlg.save_button.text() == "Enregistrer"
    original = dlg.language_combo.currentIndex()

    dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("de"))
    assert dlg.save_button.text() == "Enregistrer et relancer"

    dlg.language_combo.setCurrentIndex(original)              # retour à la langue enregistrée
    assert dlg.save_button.text() == "Enregistrer"
    assert PreferencesDialog().save_button.text() == "Enregistrer"  # relance non permise


def test_preferences_dialog_save_and_restart(env):
    dlg = PreferencesDialog(allow_restart=True)
    dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("en"))
    dlg.save_button.click()
    assert dlg.restart_requested and not dlg.isVisible()
    assert env.prefs.get("language") == "en"


def test_preferences_dialog_save_without_language_change_does_not_restart(env):
    dlg = PreferencesDialog(allow_restart=True)
    dlg.zoom_combo.setCurrentIndex((dlg.zoom_combo.currentIndex() + 1) % dlg.zoom_combo.count())
    dlg.save_button.click()
    assert not dlg.restart_requested and not dlg.isVisible()


def test_preferences_dialog_save_button_requires_a_change(env):
    """« Enregistrer » reste inactif tant qu'aucun réglage ne diffère de l'état enregistré."""
    dlg = PreferencesDialog(allow_restart=True)
    assert not dlg.save_button.isEnabled()
    dlg.reduce_motion.setChecked(True)
    assert dlg.save_button.isEnabled() and dlg.save_button.text() == "Enregistrer"
    dlg.reduce_motion.setChecked(False)                      # retour à l'état enregistré
    assert not dlg.save_button.isEnabled()
    dlg.zoom_combo.setCurrentIndex((dlg.zoom_combo.currentIndex() + 1) % dlg.zoom_combo.count())
    assert dlg.save_button.isEnabled()
    dlg.icon_combo.setCurrentIndex(dlg.icon_combo.findData("animated"))
    assert dlg.save_button.isEnabled()
    dlg.icon_combo.setCurrentIndex(dlg.icon_combo.findData("papicons"))  # retour au thème d'icônes
    assert dlg.save_button.isEnabled()                       # le zoom a quand même changé
    dlg.save_button.click()                                  # l'enregistrement remet l'état à zéro
    assert not dlg.save_button.isEnabled()


def test_preferences_dialog_save_button_follows_a_typed_path(env):
    """Un chemin tapé active « Enregistrer » avant même la validation du champ."""
    dlg = PreferencesDialog()
    dlg.fields["ffmpeg"].edit.setText("/opt/autre-ffmpeg")
    assert dlg.save_button.isEnabled()
    dlg.fields["ffmpeg"].edit.clear()
    assert not dlg.save_button.isEnabled()


def test_preferences_page_leaves_the_window_background_visible(env):
    """Comme « Journaux et historique » : aucun fond propre, seules les cartes sont des zones."""
    page = PreferencesPage()                                 # page de l'onglet, pas l'enveloppe
    assert not page.autoFillBackground()
    assert not page.scroll.autoFillBackground()
    assert not page.scroll.viewport().autoFillBackground()
    assert not page.scroll.widget().autoFillBackground()
    env.theme.set_theme("light")                             # la bascule ne pose pas de fond
    assert not page.autoFillBackground()
    assert not page.scroll.viewport().autoFillBackground()
    assert not PreferencesDialog().page.autoFillBackground()  # la boîte de dialogue garde son verre


def test_preferences_dialog_saves_with_a_visible_parent(env):
    """La feuille de verre mémorise son thème dans `_sheet_theme` : avec un parent visible,
    elle ne doit pas écraser le gestionnaire de thème du dialogue (« Enregistrer » muet)."""
    parent = QMainWindow()
    parent.setCentralWidget(QWidget())
    parent.resize(900, 700)
    parent.show()
    try:
        dlg = PreferencesDialog(parent, allow_restart=True)
        dlg.show()
        QApplication.processEvents()
        dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("de"))
        dlg.save_button.click()
        assert not dlg.isVisible()
        assert env.prefs.get("language") == "de"
    finally:
        parent.close()


def test_module_chrome_and_tagline_follow_language(env, qt_app):
    from azimut.core import i18n
    spec = ModuleSpec(id="fake2", name="Azimut Fake",
                      tagline="Inscrire la date de prise de vue dans le nom de vos fichiers",
                      icon="🧪", entry="x:Y")
    assert i18n.install(qt_app, "en").active == "en"
    try:
        window = ProbeWindow(spec)
        assert window.run_button.text() == "Start"
        assert window.tagline_label.text() == "Write the capture date into your file names"
    finally:
        i18n.uninstall(qt_app)
    assert ProbeWindow(spec).run_button.text() == "Démarrer"        # retour à la langue source
