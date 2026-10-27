"""Fenêtre de base commune à tous les modules : en-tête, barre d'outils, journal, historique, progression."""
from __future__ import annotations

import html
import os
from typing import Callable, Iterable

from PySide6.QtCore import QCoreApplication, QMimeData, Qt
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from azimut import APP_NAME, __version__
from azimut.core import binaries
from azimut.core.binaries import BinaryInfo
from azimut.core.history import HistoryEntry, HistoryStore
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import ModulePrefs, Preferences
from azimut.core.workers import BaseWorker
from azimut.ui.preferences_dialog import SOURCE_LABELS, PreferencesDialog
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import Column, DataTable, LogPanel, ProgressPanel, StatusChip

log = get_logger(__name__)

BUTTON_KINDS: tuple[str, ...] = ("primary", "success", "danger", "accent", "secondary", "ghost")
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+O", "Ouvrir…"),
    ("Ctrl+Entrée", "Lancer le traitement"),
    ("Échap", "Annuler le traitement en cours"),
    ("Ctrl+L", "Afficher / masquer le journal"),
    ("Ctrl+T", "Basculer entre thème sombre et clair"),
    ("Ctrl+,", "Préférences…"),
    ("F1", "Aide"),
)
HISTORY_COLUMNS = [
    Column("time", "Date", width=150),
    Column("action", "Action", width=130),
    Column("summary", "Résumé", stretch=True),
    Column("ok", "Réussis", width=70, align="right"),
    Column("errors", "Erreurs", width=70, align="right"),
]
_STATUS_TEXT = {"completed": "Terminé", "cancelled": "Annulé", "failed": "Échec"}


def _mime_paths(mime: QMimeData | None) -> list[str]:
    """Chemins locaux absolus d'un QMimeData (sans doublon)."""
    if mime is None or not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if url.isLocalFile():
            p = os.path.abspath(url.toLocalFile())
            if p not in out:
                out.append(p)
    return out


class ModuleWindow(QMainWindow):
    """Classe mère de la fenêtre de chaque module (mêmes barre d'outils, raccourcis et organisation)."""

    spec: ModuleSpec
    prefs: ModulePrefs
    log_panel: LogPanel
    progress_panel: ProgressPanel

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.prefs = Preferences.instance().module(spec.id)

        self._worker: BaseWorker | None = None
        self._last_worker: BaseWorker | None = None      # garde la dernière référence (évite une destruction prématurée)
        self._connections: list[tuple[object, Callable]] = []
        self._callbacks: dict[str, Callable | None] = {}
        self._locked: list[tuple[QWidget, bool]] = []
        self._history_action = ""
        self._outcome: str | None = None
        self._result: object = None
        self._failure = ""
        self._cancel_requested = False
        self._run_enabled = True
        self._binary_names: tuple[str, ...] = ()
        self.binary_chips: list[StatusChip] = []
        self.side_dock: QDockWidget | None = None
        self._content_widget: QWidget | None = None

        self.setWindowTitle(spec.name)
        self.setMinimumSize(960, 640)
        self.resize(1180, 780)
        self.setAcceptDrops(True)

        self._build_central()
        self._build_toolbar()
        self._build_bottom()

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        self._on_theme_changed(ThemeManager.instance().theme())
        self.show_binary_status((*spec.requires, *spec.optional))
        self.refresh_history()
        self._update_buttons()

    # ═══════════════════════════════════════════════════════════════════════
    # Construction
    # ═══════════════════════════════════════════════════════════════════════
    def _build_central(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 12, 18, 12)
        outer.setSpacing(12)

        self.header = QFrame()
        self.header.setObjectName("Card")
        hl = QVBoxLayout(self.header)
        hl.setContentsMargins(18, 14, 18, 14)
        hl.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.title_label = QLabel(
            f'<span style="font-size:x-large; font-weight:700">'
            f'{html.escape(self.spec.icon)}&nbsp;&nbsp;{html.escape(self.spec.name)}</span>')
        self.title_label.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.title_label)
        top.addStretch(1)
        self.chips_layout = QHBoxLayout()
        self.chips_layout.setSpacing(6)
        top.addLayout(self.chips_layout)
        hl.addLayout(top)
        self.tagline_label = QLabel(self.spec.tagline)
        self.tagline_label.setProperty("role", "muted")
        self.tagline_label.setWordWrap(True)
        hl.addWidget(self.tagline_label)
        outer.addWidget(self.header)

        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._content_layout, 1)
        self.setCentralWidget(central)

        self.progress_panel = ProgressPanel()
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.addPermanentWidget(self.progress_panel, 1)

    def _make_button(self, text: str, callback: Callable[[], None], kind: str = "secondary",
                     shortcut: str | None = None, tooltip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("kind", kind if kind in BUTTON_KINDS else "secondary")
        tip = tooltip
        if shortcut:
            seq = QKeySequence(shortcut)
            btn.setShortcut(seq)
            shown = seq.toString(QKeySequence.SequenceFormat.NativeText)
            tip = f"{tooltip} ({shown})" if tooltip else f"({shown})"
        if tip:
            btn.setToolTip(tip)
        btn.clicked.connect(lambda _checked=False: callback())
        return btn

    @staticmethod
    def _set_text_keep_shortcut(btn: QPushButton, text: str) -> None:
        """setText() remplace le raccourci par le mnémonique du texte (vide ici) : on le conserve."""
        seq = btn.shortcut()
        btn.setText(text)
        btn.setShortcut(seq)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Principale", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.toolbar = tb
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        self.open_button = self._make_button("Ouvrir…", lambda: self.on_open(), "secondary", "Ctrl+O",
                                             "Ouvrir des fichiers ou un dossier")
        self.run_button = self._make_button("Lancer", lambda: self.on_run(), "primary", "Ctrl+Return",
                                            "Lancer le traitement")
        self.cancel_button = self._make_button("Annuler", self.cancel_current, "danger", "Esc",
                                               "Annuler le traitement en cours")
        for b in (self.open_button, self.run_button, self.cancel_button):
            tb.addWidget(b)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=lambda: self.run_button.click())

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._spacer_action = tb.addWidget(spacer)           # les boutons du module s'insèrent avant

        self.log_button = self._make_button("Journal", self._toggle_log, "ghost", "Ctrl+L",
                                            "Afficher ou masquer le journal")
        self.theme_button = self._make_button("", lambda: ThemeManager.instance().toggle(), "ghost", "Ctrl+T",
                                              "Changer de thème")
        self.prefs_button = self._make_button("Préférences…", self._open_preferences, "ghost", "Ctrl+,",
                                              "Thème, taille du texte, outils externes")
        self.help_button = self._make_button("Aide", self._show_help, "ghost", "F1", "Aide et raccourcis")
        for b in (self.log_button, self.theme_button, self.prefs_button, self.help_button):
            tb.addWidget(b)

    def _build_bottom(self) -> None:
        self.log_panel = LogPanel()
        self.history_table = DataTable(HISTORY_COLUMNS)
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.log_panel, "Journal")
        self.bottom_tabs.addTab(self.history_table, "Historique")
        self.bottom_tabs.setMinimumHeight(150)
        clear_btn = self._make_button("Effacer le journal", self.log_panel.clear_log, "ghost")
        self.bottom_tabs.setCornerWidget(clear_btn, Qt.Corner.TopRightCorner)
        self.bottom_tabs.currentChanged.connect(self._on_tab_changed)

        dock = QDockWidget("Journal", self)
        dock.setObjectName("LogDock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        dock.setTitleBarWidget(QWidget())                   # pas de barre de titre : l'onglet suffit
        dock.setWidget(self.bottom_tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.log_dock = dock

    # ═══════════════════════════════════════════════════════════════════════
    # Mise en page
    # ═══════════════════════════════════════════════════════════════════════
    def set_content(self, widget: QWidget) -> None:
        old = self._content_widget
        if old is widget:
            return
        if old is not None:
            self._content_layout.removeWidget(old)
            old.hide()
            old.setParent(None)
        self._content_widget = widget
        self._content_layout.addWidget(widget, 1)
        widget.show()

    def set_side_panel(self, widget: QWidget | None, title: str = "") -> None:
        if widget is None:
            if self.side_dock is not None:
                self.side_dock.hide()
            return
        if self.side_dock is None:
            dock = QDockWidget(self)
            dock.setObjectName("SideDock")
            dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)   # non flottant, non fermable
            dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            self.side_dock = dock
        dock = self.side_dock
        dock.setWindowTitle(title)
        dock.setTitleBarWidget(None if title else QWidget())
        dock.setWidget(widget)
        dock.show()
        widget.show()

    def add_toolbar_button(self, text: str, callback: Callable[[], None], *, kind: str = "secondary",
                           shortcut: str | None = None, tooltip: str = "") -> QPushButton:
        btn = self._make_button(text, callback, kind, shortcut, tooltip)
        self.toolbar.insertWidget(self._spacer_action, btn)
        return btn

    def set_run_label(self, text: str) -> None:
        self._set_text_keep_shortcut(self.run_button, text)

    def set_run_enabled(self, enabled: bool) -> None:
        self._run_enabled = bool(enabled)
        self._update_buttons()

    def show_binary_status(self, names: Iterable[str]) -> None:
        """Pastilles ✔ / ✖ dans l'en-tête ; version et origine en infobulle."""
        self._binary_names = tuple(names)
        for chip in self.binary_chips:
            self.chips_layout.removeWidget(chip)
            chip.deleteLater()
        self.binary_chips = []
        for name in self._binary_names:
            try:
                info = binaries.resolve(name)
            except Exception as exc:                       # détection défaillante : traité comme manquant
                log.warning("Détection de %s impossible : %s", name, exc)
                info = BinaryInfo(name, None, "missing", "")
            if info.available:
                chip = StatusChip(f"✔ {name}", "ok")
                origin = SOURCE_LABELS.get(info.source, info.source)
                chip.setToolTip(f"{name} {info.version}".strip() + f"\n{origin} : {info.path}")
            else:
                level = "err" if name in self.spec.requires else "warn"
                chip = StatusChip(f"✖ {name}", level)
                chip.setToolTip(f"{name} est introuvable.\nInstallez-le ou indiquez son chemin dans les Préférences.")
            self.chips_layout.addWidget(chip)
            self.binary_chips.append(chip)

    # ═══════════════════════════════════════════════════════════════════════
    # Crochets à surcharger
    # ═══════════════════════════════════════════════════════════════════════
    def open_paths(self, paths: list[str]) -> None:
        """Chemins reçus par glisser-déposer, ligne de commande ou Atlas (à surcharger)."""

    def on_open(self) -> None:
        """Bouton « Ouvrir… » (à surcharger)."""

    def on_run(self) -> None:
        """Bouton « Lancer » (à surcharger)."""

    # ═══════════════════════════════════════════════════════════════════════
    # Exécution d'un traitement
    # ═══════════════════════════════════════════════════════════════════════
    def is_busy(self) -> bool:
        return self._worker is not None

    def run_worker(self, worker: BaseWorker, *,
                   on_completed: Callable[[object], None] | None = None,
                   on_failed: Callable[[str], None] | None = None,
                   on_cancelled: Callable[[], None] | None = None,
                   lock_widgets: Iterable[QWidget] = (),
                   history_action: str = "") -> None:
        if self._worker is not None:
            self.log_panel.append_log("WARN", "Un traitement est déjà en cours : nouvelle demande ignorée.")
            return
        self._worker = worker
        self._callbacks = {"completed": on_completed, "failed": on_failed, "cancelled": on_cancelled}
        self._history_action = history_action
        self._outcome, self._result, self._failure = None, None, ""
        self._cancel_requested = False

        self._locked = []
        for w in lock_widgets:
            self._locked.append((w, w.isEnabled()))
            w.setEnabled(False)

        self.progress_panel.reset()
        self.progress_panel.set_status("En cours…")
        for signal, slot in (
            (worker.progress, self.progress_panel.set_progress),
            (worker.status, self.progress_panel.set_status),
            (worker.log, self.log_panel.append_log),
            (worker.completed, self._on_worker_completed),
            (worker.cancelled, self._on_worker_cancelled),
            (worker.failed, self._on_worker_failed),
            (worker.finished, self._on_worker_finished),
        ):
            signal.connect(slot)
            self._connections.append((signal, slot))
        self._update_buttons()
        worker.start()

    def cancel_current(self) -> None:
        worker = self._worker
        if worker is None or self._cancel_requested:
            return
        self._cancel_requested = True
        worker.cancel()
        self.progress_panel.set_status("Annulation en cours…")
        self.log_panel.append_log("WARN", "Annulation demandée…")
        self._update_buttons()

    def _on_worker_completed(self, result: object) -> None:
        self._outcome, self._result = "completed", result
        summary = getattr(self._worker, "summary", "") if self._worker is not None else ""
        self.log_panel.append_log("OK", summary or "Traitement terminé.")

    def _on_worker_cancelled(self) -> None:
        self._outcome = "cancelled"
        self.log_panel.append_log("WARN", "Traitement annulé.")

    def _on_worker_failed(self, message: str) -> None:
        self._outcome, self._failure = "failed", message
        self.log_panel.append_log("ERR", f"Échec du traitement : {message}")

    def _on_worker_finished(self) -> None:
        worker = self._worker
        if worker is None:
            return
        worker.wait(2000)                                   # le fil est vraiment terminé avant de lâcher la référence
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections = []

        outcome, result, failure = self._outcome, self._result, self._failure
        callbacks, locked, action = self._callbacks, self._locked, self._history_action
        self._worker, self._last_worker = None, worker
        self._callbacks, self._locked, self._history_action = {}, [], ""
        self._outcome, self._result, self._failure, self._cancel_requested = None, None, "", False

        for widget, was_enabled in locked:
            if was_enabled:
                try:
                    widget.setEnabled(True)
                except RuntimeError:                         # widget détruit entre-temps
                    pass
        self.progress_panel.set_status(_STATUS_TEXT.get(outcome or "", ""))
        self._update_buttons()
        if action:
            self._record_history(worker, action, outcome, failure)
        self.refresh_history()

        cb = callbacks.get(outcome or "")
        if cb is not None:
            try:
                if outcome == "completed":
                    cb(result)
                elif outcome == "failed":
                    cb(failure)
                else:
                    cb()
            except Exception as exc:                         # un rappel défaillant ne doit pas casser la fenêtre
                log.exception("Erreur dans le rappel de fin de traitement")
                self.log_panel.append_log("ERR", f"Erreur interne : {exc}")

    def _record_history(self, worker: BaseWorker, action: str, outcome: str | None, failure: str) -> None:
        summary = getattr(worker, "summary", "") or ""
        if not summary:
            summary = {"cancelled": "Traitement annulé.", "failed": f"Échec : {failure}"}.get(outcome or "", "")
        entry = HistoryEntry(module=self.spec.id, action=action, summary=summary,
                             ok=int(getattr(worker, "ok_count", 0) or 0),
                             errors=int(getattr(worker, "error_count", 0) or 0))
        try:
            HistoryStore().add(entry)
        except Exception as exc:
            log.warning("Historique non enregistré : %s", exc)

    # ═══════════════════════════════════════════════════════════════════════
    # Historique
    # ═══════════════════════════════════════════════════════════════════════
    def refresh_history(self) -> None:
        try:
            entries = HistoryStore().recent(limit=200, module=self.spec.id)
        except Exception as exc:
            log.warning("Historique illisible : %s", exc)
            entries = []
        rows = []
        for e in entries:
            tag = "err" if e.errors else ("ok" if e.ok else "neutre")
            rows.append({"time": e.timestamp.replace("T", " ")[:19], "action": e.action, "summary": e.summary,
                         "ok": e.ok, "errors": e.errors, "_tag": tag})
        self.history_table.data_model.set_rows(rows)

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self.refresh_history()

    # ═══════════════════════════════════════════════════════════════════════
    # Actions de la barre d'outils
    # ═══════════════════════════════════════════════════════════════════════
    def _update_buttons(self) -> None:
        busy = self.is_busy()
        self.run_button.setEnabled(self._run_enabled and not busy)
        self.open_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and not self._cancel_requested)

    def _toggle_log(self) -> None:
        self.log_dock.setVisible(self.log_dock.isHidden())

    def _on_theme_changed(self, _theme: str) -> None:
        dark = ThemeManager.instance().theme() == "dark"
        self._set_text_keep_shortcut(self.theme_button, "☀️ Clair" if dark else "🌙 Sombre")

    def _open_preferences(self) -> None:
        PreferencesDialog(self).exec()
        self.show_binary_status(self._binary_names)

    def _build_help_dialog(self) -> QDialog:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Aide — {self.spec.name}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        title = QLabel(f'<span style="font-size:x-large; font-weight:700">'
                       f'{html.escape(self.spec.icon)}&nbsp;&nbsp;{html.escape(self.spec.name)}</span>')
        title.setTextFormat(Qt.TextFormat.RichText)
        tagline = QLabel(self.spec.tagline)
        tagline.setWordWrap(True)
        version = QLabel(f"{APP_NAME} · version {__version__}")
        version.setProperty("role", "muted")
        rows = "".join(f"<tr><td><b>{html.escape(k)}</b>&nbsp;&nbsp;&nbsp;</td><td>{html.escape(v)}</td></tr>"
                       for k, v in SHORTCUTS)
        shortcuts = QLabel(f"<p><b>Raccourcis clavier</b></p><table cellspacing='4'>{rows}</table>")
        shortcuts.setTextFormat(Qt.TextFormat.RichText)
        for w in (title, tagline, version, shortcuts):
            lay.addWidget(w)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        return dlg

    def _show_help(self) -> None:
        self._build_help_dialog().exec()

    # ═══════════════════════════════════════════════════════════════════════
    # Glisser-déposer et fermeture
    # ═══════════════════════════════════════════════════════════════════════
    def handle_drop(self, mime: QMimeData | None) -> list[str]:
        """Transmet les chemins déposés à `open_paths` (refusé pendant un traitement)."""
        paths = _mime_paths(mime)
        if not paths:
            return []
        if self.is_busy():
            self.log_panel.append_log("WARN", "Un traitement est en cours : dépôt ignoré.")
            return []
        self.open_paths(paths)
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _mime_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self.handle_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def closeEvent(self, event: QCloseEvent) -> None:
        worker = self._worker
        if worker is not None:
            self.cancel_current()
            if worker.wait(3000):
                QCoreApplication.processEvents()             # traite les signaux en attente (historique, rappels)
            else:
                log.warning("Le traitement ne s'est pas arrêté dans les 3 s : fermeture forcée.")
        event.accept()
