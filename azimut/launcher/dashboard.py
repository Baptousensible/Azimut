"""Tableau de bord d'Azimut : historique, journal, binaires."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QCoreApplication, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from azimut.core import binaries
from azimut.core.history import HistoryEntry, HistoryStore
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.core.paths import user_log_dir
from azimut.ui.controls import set_action_available
from azimut.ui.glass import GlassTabBar
from azimut.ui.widgets import Column, DataTable

log = get_logger(__name__)

LOG_LINES = 500
LOG_NAME = "azimut.log"
def _plural(count: int, one: str, many: str) -> str:
    """« 1 entrée » / « 6 entrées » : plus de « (s) » dans les libellés."""
    return (one if count <= 1 else many).format(count=count)


def source_labels() -> dict[str, str]:
    """Origines possibles d'un binaire, traduites."""
    return {"bundled": QCoreApplication.translate("Dashboard", "Embarqué"), "custom": QCoreApplication.translate("Dashboard", "Personnalisé"),
            "system": QCoreApplication.translate("Dashboard", "Système"), "missing": QCoreApplication.translate("Dashboard", "Introuvable")}


def safe_resolve(name: str) -> binaries.BinaryInfo | None:
    """`binaries.resolve` sans exception (None si la détection échoue)."""
    try:
        return binaries.resolve(name)
    except Exception:
        log.exception("Détection de %s impossible", name)
        return None


def tail_lines(path: Path, max_lines: int = LOG_LINES) -> list[str]:
    """Dernières lignes d'un fichier texte ; liste vide si illisible."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\r\n") for line in deque(fh, maxlen=max_lines)]
    except OSError:
        return []


# ── Historique ───────────────────────────────────────────────────────────
class HistoryTab(QWidget):
    """Historique des traitements, filtrable par module."""

    LIMIT = 200

    def __init__(self, specs: Iterable[ModuleSpec], store: HistoryStore | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        specs = list(specs)
        self._store = store
        self._names = {s.id: s.name for s in specs}

        self.filter_box = QComboBox()
        self.filter_box.addItem(self.tr("Tous les modules"), "")
        for spec in specs:
            self.filter_box.addItem(spec.name, spec.id)
        self.filter_box.currentIndexChanged.connect(lambda _i: self.refresh())
        self.refresh_button = QPushButton(self.tr("Actualiser"))
        self.refresh_button.clicked.connect(lambda: self.refresh())
        self.clear_button = QPushButton(self.tr("Effacer l'historique"))
        # Seule action irréversible de la page : rouge dès qu'il y a une entrée à effacer.
        self.clear_button.clicked.connect(lambda: self._on_clear())
        self.count_label = QLabel("")

        self.table = DataTable([
            Column("date", self.tr("Date"), 150),
            Column("module", self.tr("Module"), 140),
            Column("action", self.tr("Action"), 130),
            Column("summary", self.tr("Résumé"), 400, stretch=True),
            Column("ok", self.tr("Réussis"), 80, align="right"),
            Column("errors", self.tr("Erreurs"), 80, align="right"),
        ])
        self.table.set_empty_text(self.tr("Aucun traitement enregistré pour l'instant."))

        bar = QHBoxLayout()
        bar.addWidget(QLabel(self.tr("Module :")))
        bar.addWidget(self.filter_box)
        bar.addWidget(self.refresh_button)
        bar.addWidget(self.clear_button)
        bar.addStretch(1)
        bar.addWidget(self.count_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 18, 9, 9)
        layout.addLayout(bar)
        layout.addWidget(self.table, 1)
        self.refresh()

    def store(self) -> HistoryStore:
        if self._store is None:
            self._store = HistoryStore()
        return self._store

    def _current_module(self) -> str | None:
        return self.filter_box.currentData() or None

    def _row(self, entry: HistoryEntry) -> dict:
        if entry.errors and not entry.ok:
            tag = "err"
        elif entry.errors:
            tag = "warn"
        else:
            tag = "neutre"
        return {
            "date": (entry.timestamp or "").replace("T", " ")[:16],
            "module": self._names.get(entry.module, entry.module),
            "action": entry.action,
            "summary": entry.summary,
            "ok": str(entry.ok),
            "errors": str(entry.errors),
            "_tag": tag,
        }

    def refresh(self) -> None:
        try:
            entries = self.store().recent(limit=self.LIMIT, module=self._current_module())
        except Exception:
            log.exception("Lecture de l'historique impossible")
            entries = []
        self.table.data_model.set_rows([self._row(e) for e in entries])
        set_action_available(self.clear_button, bool(entries), active="danger")
        self.count_label.setText(_plural(len(entries), self.tr("{count} entrée"),
                                         self.tr("{count} entrées")))

    def _on_clear(self) -> None:
        module = self._current_module()
        portee = (self.tr("du module « {name} »").format(name=self._names.get(module, module))
                  if module else self.tr("de tous les modules"))
        yes = QMessageBox.StandardButton.Yes
        answer = QMessageBox.question(
            self, self.tr("Effacer l'historique"),
            self.tr("Effacer définitivement l'historique {scope} ?").format(scope=portee),
            yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != yes:
            return
        try:
            self.store().clear(module=module)
        except Exception:
            log.exception("Effacement de l'historique impossible")
        self.refresh()


# ── Journal ──────────────────────────────────────────────────────────────
class LogTab(QWidget):
    """Dernières lignes du journal de l'application."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        refresh = QPushButton(self.tr("Actualiser"))
        refresh.clicked.connect(lambda: self.refresh())
        open_dir = QPushButton(self.tr("Ouvrir le dossier des journaux"))
        open_dir.clicked.connect(lambda: self.open_folder())
        self.info_label = QLabel("")
        bar = QHBoxLayout()
        bar.addWidget(refresh)
        bar.addWidget(open_dir)
        bar.addStretch(1)
        bar.addWidget(self.info_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 18, 9, 9)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)
        self.refresh()

    def refresh(self) -> None:
        lines = tail_lines(user_log_dir() / LOG_NAME)
        if lines:
            self.view.setPlainText("\n".join(lines))
            self.view.moveCursor(QTextCursor.MoveOperation.End)
            self.info_label.setText(_plural(len(lines), self.tr("{count} dernière ligne"),
                                           self.tr("{count} dernières lignes")))
        else:
            self.view.setPlainText(self.tr("(Le journal est vide ou introuvable.)"))
            self.info_label.setText("")

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_log_dir())))


# ── Binaires ─────────────────────────────────────────────────────────────
class _RefreshThread(QThread):
    """Détection des binaires hors du fil de l'interface."""

    resultReady = Signal(object)  # dict[str, BinaryInfo] ou None en cas d'échec

    def run(self) -> None:
        try:
            result = binaries.refresh()
        except Exception:
            log.exception("Détection des binaires impossible")
            result = None
        self.resultReady.emit(result)


class BinariesTab(QWidget):
    """État des binaires (ffmpeg, ffprobe, exiftool)."""

    refreshed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thread: _RefreshThread | None = None
        self.table = DataTable([
            Column("name", self.tr("Binaire"), 110),
            Column("source", self.tr("Source"), 120),
            Column("version", self.tr("Version"), 110),
            Column("path", self.tr("Chemin"), 420, stretch=True),
        ])
        self.detect_button = QPushButton(self.tr("Détecter"))
        self.detect_button.clicked.connect(lambda: self.detect())
        self.status_label = QLabel("")
        bar = QHBoxLayout()
        bar.addWidget(self.detect_button)
        bar.addWidget(self.status_label)
        bar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 18, 9, 9)
        layout.addLayout(bar)
        layout.addWidget(self.table, 1)
        self.reload()

    def _row(self, name: str, info: binaries.BinaryInfo | None) -> dict:
        labels = source_labels()
        if info is None or not info.available:
            return {"name": name, "source": labels["missing"], "version": "—", "path": "—", "_tag": "err"}
        return {"name": name, "source": labels.get(info.source, info.source),
                "version": info.version or "—", "path": str(info.path), "_tag": "ok"}

    def reload(self, infos: dict | None = None) -> None:
        rows = [self._row(n, infos.get(n) if infos is not None else safe_resolve(n))
                for n in binaries.KNOWN_BINARIES]
        self.table.data_model.set_rows(rows)

    def detect(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.detect_button.setEnabled(False)
        self.status_label.setText(self.tr("Détection en cours…"))
        thread = _RefreshThread(self)
        thread.resultReady.connect(self._on_detected)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _on_detected(self, result: object) -> None:
        self._thread = None
        self.detect_button.setEnabled(True)
        if result is None:
            self.status_label.setText(self.tr("La détection a échoué (voir le journal)."))
            return
        self.status_label.setText(self.tr("Détection terminée."))
        self.reload(result if isinstance(result, dict) else None)
        self.refreshed.emit()

    def shutdown(self) -> None:
        """Attend la fin d'une détection en cours (avant fermeture)."""
        thread = self._thread
        if thread is not None:
            try:
                thread.wait(15000)
            except RuntimeError as exc:
                log.debug("Attente de la détection interrompue : %s", exc)


# ── Ensemble ─────────────────────────────────────────────────────────────
class Dashboard(QTabWidget):
    """Onglets sous les cartes d'Azimut."""

    binariesRefreshed = Signal()

    def __init__(self, specs: Iterable[ModuleSpec], history_store: HistoryStore | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ActivityTabs")
        # Même barre que « Tous les outils » : la capsule de sélection glisse d'un onglet à l'autre.
        self.setTabBar(GlassTabBar(self))
        self.tabBar().setDrawBase(False)
        self.history_tab = HistoryTab(specs, history_store)
        self.log_tab = LogTab()
        self.binaries_tab = BinariesTab()
        self.addTab(self.history_tab, self.tr("Historique des traitements"))
        self.addTab(self.log_tab, self.tr("Journal"))
        self.addTab(self.binaries_tab, self.tr("Binaires"))
        self.binaries_tab.refreshed.connect(self.binariesRefreshed)
        self.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        page = self.widget(index)
        if page is self.history_tab:
            self.history_tab.refresh()
        elif page is self.log_tab:
            self.log_tab.refresh()

    def refresh_history(self) -> None:
        self.history_tab.refresh()

    def shutdown(self) -> None:
        self.binaries_tab.shutdown()
