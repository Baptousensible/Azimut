"""Composants graphiques réutilisables de la suite Azimut."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QObject,
    QRunnable,
    QSortFilterProxyModel,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from azimut.core import binaries
from azimut.core.logs import get_logger
from azimut.core.paths import user_cache_dir
from azimut.core.prefs import Preferences
from azimut.core.workers import LEVELS
from azimut.ui.theme import ThemeManager, repolish, token

log = get_logger(__name__)

LONG_LABEL = 40                         # au-delà, le libellé d'un PathField passe au-dessus du champ
CHIP_LEVELS: tuple[str, ...] = ("ok", "warn", "err", "info", "neutre")


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires internes
# ─────────────────────────────────────────────────────────────────────────────
def _paths_from_mime(mime: QMimeData | None) -> list[str]:
    """Chemins locaux absolus contenus dans les URL d'un QMimeData (sans doublon, ordre conservé)."""
    if mime is None or not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if url.isLocalFile():
            p = os.path.abspath(url.toLocalFile())
            if p not in out:
                out.append(p)
    return out


def _start_dir() -> str:
    """Dernier dossier utilisé (préférence « last_dir ») s'il existe encore."""
    d = str(Preferences.instance().get("last_dir", "") or "")
    return d if d and os.path.isdir(d) else ""


def _remember_dir(path: str) -> None:
    p = path if os.path.isdir(path) else os.path.dirname(path)
    if p and os.path.isdir(p):
        Preferences.instance().set("last_dir", p)


def _normalize_extensions(extensions: Iterable[str] | None) -> frozenset[str] | None:
    if extensions is None:
        return None
    return frozenset(("." + e.lower().lstrip(".")) for e in extensions if e)


# ─────────────────────────────────────────────────────────────────────────────
# Pastille, carte
# ─────────────────────────────────────────────────────────────────────────────
def tame_combos(root: QWidget, min_chars: int = 12) -> None:
    """Empêche les listes déroulantes d'imposer la largeur de leur plus long libellé (panneaux rognés)."""
    for combo in root.findChildren(QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(min_chars)
        combo.setMinimumWidth(max(combo.minimumWidth(), min_chars * 10))      # minimumSizeHint() resterait au libellé le plus long
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class StatusChip(QLabel):
    """Pastille d'état colorée (le style vient du QSS, selon la propriété « level »)."""

    def __init__(self, text: str = "", level: str = "neutre", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.set_state(text, level)

    def set_state(self, text: str, level: str) -> None:
        if level not in CHIP_LEVELS:
            level = "neutre"
        self.setText(text)
        self.setProperty("level", level)
        repolish(self)


class Card(QFrame):
    """Bloc avec titre optionnel ; ajouter les widgets dans `body_layout`."""

    body_layout: QVBoxLayout

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(8)
        self.title_label: QLabel | None = None
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("CardTitle")
            outer.addWidget(self.title_label)
        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        outer.addLayout(self.body_layout)


class CollapsibleCard(Card):
    """Carte dont le corps se replie ; un résumé d'une ligne reste visible une fois repliée."""

    toggled = Signal(bool)                 # True : déplié

    def __init__(self, title: str, *, expanded: bool = True, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        outer = self.layout()
        self._button = QPushButton()
        self._button.setProperty("kind", "ghost")
        self._button.setCheckable(True)
        self._button.setStyleSheet("QPushButton { text-align: left; font-weight: 700; padding: 2px 4px; }")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title = title
        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "muted")
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._button)
        head.addWidget(self.summary_label, 1)
        outer.insertLayout(0, head)                        # au-dessus du corps
        outer.removeItem(self.body_layout)                 # le corps devient un widget masquable
        self.body_layout.deleteLater()
        self._body = QWidget()
        self.body_layout = QVBoxLayout(self._body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        outer.addWidget(self._body)
        self._button.toggled.connect(self._on_toggled)
        self._button.setChecked(expanded)
        self._on_toggled(expanded)

    def is_expanded(self) -> bool:
        return self._button.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._button.setChecked(bool(expanded))

    def set_summary(self, text: str) -> None:
        self.summary_label.setText(text)
        self.summary_label.setToolTip(text)

    def _on_toggled(self, on: bool) -> None:
        self._body.setVisible(on)
        self.summary_label.setVisible(not on)
        self._button.setText(("▾  " if on else "▸  ") + self._title)
        self.toggled.emit(bool(on))


# ─────────────────────────────────────────────────────────────────────────────
# Curseur avec valeur
# ─────────────────────────────────────────────────────────────────────────────
class SliderField(QWidget):
    """Curseur + valeur modifiable au clavier, avec repères aux extrémités (« meilleure qualité » … « plus petit »).

    API volontairement proche de QSpinBox (``value``, ``setValue``, ``setRange``, ``setSuffix``,
    ``setSpecialValueText``, ``valueChanged``) pour remplacer un QSpinBox sans toucher au reste du code.

    * ``valueChanged(int)`` n'est émis qu'une fois la valeur validée : au relâchement du curseur, à la
      touche Entrée dans la case, ou à chaque cran au clavier / à la molette. Pendant le glissement,
      la case suit en direct et ``dragging(int)`` est émis (pas d'écriture de préférences à chaque pixel).
    """

    valueChanged = Signal(int)
    dragging = Signal(int)

    def __init__(self, minimum: int = 0, maximum: int = 100, value: int | None = None, *, suffix: str = "",
                 special_text: str = "", low_hint: str = "", high_hint: str = "", step: int = 1,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setTracking(False)                 # valueChanged seulement au relâchement
        self.slider.setSingleStep(step)
        self.slider.setPageStep(max(step, (maximum - minimum) // 10 or 1))
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.spin = QSpinBox()
        self.spin.setKeyboardTracking(False)           # validé à Entrée / perte du focus
        self.spin.setSingleStep(step)
        self.spin.setMinimumWidth(78)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setStyleSheet("QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }")   # flèches clavier / curseur suffisent
        self._value = minimum
        self.setRange(minimum, maximum)
        if suffix:
            self.spin.setSuffix(suffix)
        if special_text:
            self.spin.setSpecialValueText(special_text)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        top.addWidget(self.slider, 1)
        top.addWidget(self.spin)
        self._low = QLabel(low_hint)
        self._high = QLabel(high_hint)
        for lab in (self._low, self._high):
            lab.setProperty("role", "muted")
            lab.setStyleSheet("font-size: 11px;")
        self._high.setAlignment(Qt.AlignmentFlag.AlignRight)
        hints = QHBoxLayout()
        hints.setContentsMargins(0, 0, 0, 0)
        hints.addWidget(self._low)
        hints.addStretch(1)
        hints.addWidget(self._high)
        self._hints_box = QWidget()
        self._hints_box.setLayout(hints)
        self._hints_box.setVisible(bool(low_hint or high_hint))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self._hints_box)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusProxy(self.slider)

        self.slider.sliderMoved.connect(self._on_dragged)
        self.slider.valueChanged.connect(lambda v: self._apply(int(v)))
        self.spin.valueChanged.connect(lambda v: self._apply(int(v)))
        if value is not None:
            self.setValue(value)

    # ── API façon QSpinBox ──
    def value(self) -> int:
        return self._value

    def minimum(self) -> int:
        return self.spin.minimum()

    def maximum(self) -> int:
        return self.spin.maximum()

    def setRange(self, minimum: int, maximum: int) -> None:
        for w in (self.slider, self.spin):
            w.blockSignals(True)
            w.setRange(int(minimum), int(maximum))
            w.blockSignals(False)
        self._set_silent(max(int(minimum), min(int(maximum), self._value)))

    def setValue(self, value: int) -> None:
        self._apply(int(value))

    def setSuffix(self, text: str) -> None:
        self.spin.setSuffix(text)

    def setSpecialValueText(self, text: str) -> None:
        self.spin.setSpecialValueText(text)

    def setHints(self, low: str = "", high: str = "") -> None:
        self._low.setText(low)
        self._high.setText(high)
        self._hints_box.setVisible(bool(low or high))

    def setToolTip(self, text: str) -> None:  # noqa: N802 - convention Qt
        super().setToolTip(text)
        self.slider.setToolTip(text)
        self.spin.setToolTip(text)

    # ── interne ──
    def _set_silent(self, value: int) -> None:
        value = max(self.spin.minimum(), min(self.spin.maximum(), int(value)))
        for w in (self.slider, self.spin):
            w.blockSignals(True)
            w.setValue(value)
            w.blockSignals(False)
        self._value = value

    def _apply(self, value: int) -> None:
        value = max(self.spin.minimum(), min(self.spin.maximum(), value))
        changed = value != self._value
        self._set_silent(value)
        if changed:
            self.valueChanged.emit(value)

    def _on_dragged(self, value: int) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(int(value))                  # la case suit le curseur en direct
        self.spin.blockSignals(False)
        self.dragging.emit(int(value))


# ─────────────────────────────────────────────────────────────────────────────
# Champ de chemin
# ─────────────────────────────────────────────────────────────────────────────
class PathField(QWidget):
    """Libellé + champ + bouton « Parcourir… » ; accepte le glisser-déposer d'un chemin."""

    pathChanged = Signal(str)

    def __init__(self, label: str, *, mode: str = "dir", filter: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if mode not in ("dir", "file", "save"):
            raise ValueError(f"Mode inconnu : {mode!r}")
        self._mode = mode
        self._filter = filter
        self._label_text = label
        self._value = ""

        self.setAcceptDrops(True)
        stacked = len(label) > LONG_LABEL            # libellé long : au-dessus du champ, à la ligne
        lay = QVBoxLayout(self) if stacked else QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4 if stacked else 8)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.label_widget = QLabel(label)
        self.label_widget.setWordWrap(stacked)
        self.edit = QLineEdit()
        self.edit.setAcceptDrops(False)               # le dépôt est géré par le widget parent
        self.edit.setPlaceholderText("Glissez un dossier ici ou cliquez sur « Parcourir… »"
                                     if mode == "dir" else
                                     "Glissez un fichier ici ou cliquez sur « Parcourir… »")
        self.button = QPushButton("Parcourir…")
        self.button.setProperty("kind", "secondary")
        if stacked:
            lay.addWidget(self.label_widget)
            row.addWidget(self.edit, 1)
            row.addWidget(self.button)
            lay.addLayout(row)
        else:
            lay.addWidget(self.label_widget)
            lay.addWidget(self.edit, 1)
            lay.addWidget(self.button)
        self.edit.editingFinished.connect(lambda: self._commit(self.edit.text().strip()))
        self.button.clicked.connect(self._browse)

    # ── API ──────────────────────────────────────────────────────────────────
    def path(self) -> str:
        return self.edit.text().strip()

    def set_path(self, path: str) -> None:
        self.edit.setText(path)
        self._commit(path.strip())

    # ── Interne ──────────────────────────────────────────────────────────────
    def _commit(self, value: str) -> None:
        if value != self._value:
            self._value = value
            self.pathChanged.emit(value)

    def _browse(self) -> None:
        current = self.path()
        start = current if current else _start_dir()
        if current and not os.path.exists(current):
            start = os.path.dirname(current) or _start_dir()
        title = f"Choisir : {self._label_text}"
        flt = self._filter or "Tous les fichiers (*)"
        if self._mode == "dir":
            chosen = QFileDialog.getExistingDirectory(self, title, start)
        elif self._mode == "file":
            chosen, _ = QFileDialog.getOpenFileName(self, title, start, flt)
        else:
            chosen, _ = QFileDialog.getSaveFileName(self, title, start, flt)
        if chosen:
            _remember_dir(chosen)
            self.set_path(chosen)

    def handle_drop(self, mime: QMimeData | None) -> bool:
        """Applique le premier chemin déposé qui convient au mode ; renvoie True si pris en compte."""
        for p in _paths_from_mime(mime):
            if self._mode == "dir":
                self.set_path(p if os.path.isdir(p) else os.path.dirname(p))
                return True
            if os.path.isfile(p) or (self._mode == "save" and not os.path.isdir(p)):
                self.set_path(p)
                return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self.handle_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()


# ─────────────────────────────────────────────────────────────────────────────
# Zone de glisser-déposer
# ─────────────────────────────────────────────────────────────────────────────
class DropZone(QFrame):
    """Grande zone de dépôt, cliquable (menu « Choisir des fichiers… / Choisir un dossier… »)."""

    pathsDropped = Signal(list)

    def __init__(self, text: str, *, accept_dirs: bool = True, accept_files: bool = True,
                 extensions: Iterable[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self._accept_dirs = accept_dirs
        self._accept_files = accept_files
        self._extensions = _normalize_extensions(extensions)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(96)
        self.setProperty("dragOver", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self._label = QLabel(text)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._label)

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    # ── Filtrage ─────────────────────────────────────────────────────────────
    def filter_paths(self, paths: Iterable[str]) -> list[str]:
        """Garde les chemins existants acceptés (dossiers, fichiers, extensions)."""
        out: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                if self._accept_dirs:
                    out.append(p)
            elif os.path.isfile(p) and self._accept_files:
                if self._extensions is None or Path(p).suffix.lower() in self._extensions:
                    out.append(p)
        return out

    def handle_drop(self, mime: QMimeData | None) -> list[str]:
        """Gestionnaire de dépôt : émet `pathsDropped` avec les chemins acceptés et les renvoie."""
        paths = self.filter_paths(_paths_from_mime(mime))
        if paths:
            self.pathsDropped.emit(paths)
        return paths

    # ── Glisser-déposer ──────────────────────────────────────────────────────
    def _set_drag_over(self, on: bool) -> None:
        if bool(self.property("dragOver")) != on:
            self.setProperty("dragOver", on)
            repolish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.filter_paths(_paths_from_mime(event.mimeData())):
            self._set_drag_over(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if bool(self.property("dragOver")):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drag_over(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drag_over(False)
        if self.handle_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── Clic : sélecteur ─────────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_chooser()
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._open_chooser()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _open_chooser(self) -> None:
        if self._accept_files and self._accept_dirs:
            menu = QMenu(self)
            act_files = menu.addAction("Choisir des fichiers…")
            act_dir = menu.addAction("Choisir un dossier…")
            chosen = menu.exec(self.mapToGlobal(self.rect().center()))
            if chosen is None:
                return
            if chosen == act_files:
                self._choose_files()
            elif chosen == act_dir:
                self._choose_dir()
        elif self._accept_files:
            self._choose_files()
        elif self._accept_dirs:
            self._choose_dir()

    def _choose_files(self) -> None:
        if self._extensions:
            masks = " ".join(f"*{e}" for e in sorted(self._extensions))
            flt = f"Fichiers acceptés ({masks});;Tous les fichiers (*)"
        else:
            flt = "Tous les fichiers (*)"
        files, _ = QFileDialog.getOpenFileNames(self, "Choisir des fichiers", _start_dir(), flt)
        if files:
            _remember_dir(files[0])
            paths = self.filter_paths(os.path.abspath(f) for f in files)
            if paths:
                self.pathsDropped.emit(paths)

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choisir un dossier", _start_dir())
        if d:
            _remember_dir(d)
            self.pathsDropped.emit([os.path.abspath(d)])


# ─────────────────────────────────────────────────────────────────────────────
# Progression et journal
# ─────────────────────────────────────────────────────────────────────────────
class ProgressPanel(QWidget):
    """Barre de progression + « n / total (x %) » + libellé courant + statut."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.count_label = QLabel()
        self.count_label.setProperty("role", "muted")
        top.addWidget(self.bar, 1)
        top.addWidget(self.count_label)
        lay.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.file_label = QLabel()
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_label = QLabel()
        self.status_label.setProperty("role", "muted")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.file_label, 1)
        bottom.addWidget(self.status_label)
        lay.addLayout(bottom)
        self.reset()

    def set_progress(self, done: int, total: int, label: str = "") -> None:
        if total <= 0:
            done, total = 0, 0
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            pct = 0
        else:
            done = max(0, min(done, total))
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            pct = round(100 * done / total)
        self.count_label.setText(f"{done} / {total} ({pct} %)")
        self.file_label.setText(label)
        self.file_label.setToolTip(label)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def reset(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.count_label.setText("0 / 0 (0 %)")
        self.file_label.setText("")
        self.file_label.setToolTip("")
        self.status_label.setText("")


class LogPanel(QPlainTextEdit):
    """Journal en lecture seule : couleurs par niveau, horodatage HH:MM:SS, défilement automatique."""

    MAX_BLOCKS = 5000
    _LEVEL_TOKENS = {"INFO": "info_txt", "OK": "ok_txt", "WARN": "warn_txt", "ERR": "err_txt"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setMaximumBlockCount(self.MAX_BLOCKS)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        font = QFont()                                  # seule la famille est fixée : la taille suit le zoom
        font.setFamily(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self._entries: deque[tuple[str, str, str]] = deque(maxlen=self.MAX_BLOCKS)
        ThemeManager.instance().changed.connect(self._on_theme_changed)

    def append_log(self, level: str, text: str) -> None:
        if level not in LEVELS:
            level = "INFO"
        entry = (datetime.now().strftime("%H:%M:%S"), level, text)
        self._entries.append(entry)
        self._insert(entry, first=self.document().isEmpty())
        self._scroll_to_end()

    def clear_log(self) -> None:
        self._entries.clear()
        self.clear()

    # ── Interne ──────────────────────────────────────────────────────────────
    def _insert(self, entry: tuple[str, str, str], *, first: bool) -> None:
        ts, level, text = entry
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(token(self._LEVEL_TOKENS[level])))
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not first:
            cursor.insertBlock()
        cursor.insertText(f"[{ts}] {text}", fmt)

    def _scroll_to_end(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_theme_changed(self, _theme: str) -> None:
        """Recolore tout le journal selon le nouveau thème."""
        entries = list(self._entries)
        self.clear()
        for i, entry in enumerate(entries):
            self._insert(entry, first=(i == 0))
        self._scroll_to_end()


# ─────────────────────────────────────────────────────────────────────────────
# Tableau
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Column:
    key: str
    title: str
    width: int = 120
    stretch: bool = False
    align: str = "left"                         # "left" | "center" | "right"


_ALIGN = {
    "left": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    "center": Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
    "right": Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}


class RowTableModel(QAbstractTableModel):
    """Modèle de lignes (dict {clé de colonne: valeur}) ; la clé spéciale « _tag » colore la ligne."""

    def __init__(self, columns: list[Column], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._rows: list[dict] = []

    # ── API ──────────────────────────────────────────────────────────────────
    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = [dict(r) for r in rows]
        self.endResetModel()

    def rows(self) -> list[dict]:
        return list(self._rows)

    def append_row(self, row: dict) -> int:
        index = len(self._rows)
        self.beginInsertRows(QModelIndex(), index, index)
        self._rows.append(dict(row))
        self.endInsertRows()
        return index

    def update_row(self, index: int, **values: Any) -> None:
        if not 0 <= index < len(self._rows):
            raise IndexError(f"Ligne inexistante : {index}")
        self._rows[index].update(values)
        if self._columns:
            self.dataChanged.emit(self.index(index, 0), self.index(index, len(self._columns) - 1))

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self.endResetModel()

    def refresh_colors(self) -> None:
        """Notifie la vue que les couleurs de fond doivent être relues (changement de thème)."""
        if self._rows and self._columns:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._rows) - 1, len(self._columns) - 1),
                                  [Qt.ItemDataRole.BackgroundRole])

    # ── QAbstractTableModel ──────────────────────────────────────────────────
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._columns):
                return self._columns[section].title
            if orientation == Qt.Orientation.Vertical:
                return section + 1
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)) \
                or not (0 <= index.column() < len(self._columns)):
            return None
        row = self._rows[index.row()]
        col = self._columns[index.column()]
        value = row.get(col.key)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.UserRole:         # valeur brute : tri numérique correct
            if value is None:
                return ""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            return str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN.get(col.align, _ALIGN["left"])
        if role == Qt.ItemDataRole.BackgroundRole:
            tag = row.get("_tag")
            if tag in ("ok", "warn", "err", "info"):
                return QBrush(QColor(token(f"{tag}_bg")))
            return None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class DataTable(QTableView):
    """Tableau triable, à sélection étendue ; tous les indices exposés sont ceux du modèle source."""

    rowActivated = Signal(int)
    selectionRowsChanged = Signal(list)

    data_model: RowTableModel

    def __init__(self, columns: list[Column], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data_model = RowTableModel(columns, self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self.data_model)
        self._proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)             # toutes les colonnes
        self.setModel(self._proxy)

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(28)

        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # pas de tri initial
        for i, col in enumerate(columns):
            if col.stretch:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(i, col.width)
        self.setSortingEnabled(True)

        self.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionRowsChanged.emit(self.selected_rows()))
        self.doubleClicked.connect(self._on_double_clicked)
        ThemeManager.instance().changed.connect(self._on_theme_changed)

    # ── API ──────────────────────────────────────────────────────────────────
    def selected_rows(self) -> list[int]:
        """Indices (modèle source, croissants) des lignes sélectionnées, quel que soit le tri."""
        sel = self.selectionModel()
        if sel is None:
            return []
        return sorted({self._proxy.mapToSource(i).row() for i in sel.selectedRows()})

    def set_filter_text(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    # ── Interne ──────────────────────────────────────────────────────────────
    def _on_double_clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self.rowActivated.emit(self._proxy.mapToSource(index).row())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self.currentIndex()
            if current.isValid():
                self.rowActivated.emit(self._proxy.mapToSource(current).row())
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_theme_changed(self, _theme: str) -> None:
        self.data_model.refresh_colors()
        self.viewport().update()


# ─────────────────────────────────────────────────────────────────────────────
# Aperçu d'image / de vidéo
# ─────────────────────────────────────────────────────────────────────────────
VIDEO_EXT = frozenset({".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mxf", ".ts", ".mts",
                       ".m2ts", ".vob", ".mpg", ".mpeg", ".m2v", ".3gp", ".3g2", ".f4v", ".asf",
                       ".ogv", ".webm", ".dv", ".qt"})
AUDIO_EXT = frozenset({".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".mka",
                       ".aiff", ".amr"})
_UNAVAILABLE = "Aperçu indisponible"
_PLUGINS_DONE = False
_POOL: QThreadPool | None = None


def _pool() -> QThreadPool:
    """Pool de threads partagé par tous les aperçus (2 chargements simultanés au plus)."""
    global _POOL
    if _POOL is None:
        _POOL = QThreadPool()
        _POOL.setMaxThreadCount(2)
    return _POOL


def _register_pillow_plugins() -> None:
    """Active HEIC/AVIF si pillow-heif est installé (une seule fois)."""
    global _PLUGINS_DONE
    if _PLUGINS_DONE:
        return
    _PLUGINS_DONE = True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:                                  # module absent ou inutilisable : on s'en passe
        log.debug("pillow-heif indisponible : HEIC/AVIF seulement si Pillow les gère nativement")


def _load_image_file(path: Path, size: int) -> QImage | None:
    """Image → miniature QImage via Pillow (orientation EXIF respectée) ; None si illisible."""
    try:
        from PIL import Image, ImageOps
        _register_pillow_plugins()
        with Image.open(path) as im:
            try:
                im.draft("RGB", (size * 2, size * 2))   # JPEG : décodage réduit
            except Exception:
                pass
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            rgba = im.convert("RGBA")
        w, h = rgba.size
        data = rgba.tobytes("raw", "RGBA")
        return QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    except Exception as exc:
        log.debug("Aperçu image impossible (%s) : %s", path, exc)
        return None


def _load_video_frame(path: Path, size: int, is_current: Callable[[], bool]) -> tuple[QImage | None, str]:
    """Une image extraite avec ffmpeg (exception assumée à la règle « pas de subprocess direct »)."""
    info = binaries.resolve("ffmpeg")
    if not info.available:
        return None, f"🎬 Vidéo\n{path.name}\n(ffmpeg requis pour l'aperçu)"
    tmp = user_cache_dir() / f"preview_{uuid.uuid4().hex}.png"
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        for seek in (["-ss", "1"], []):               # 1 s ; sinon première image (vidéo très courte)
            if not is_current():
                return None, ""
            cmd = [str(info.path), "-y", "-loglevel", "error", *seek, "-i", str(path),
                   "-frames:v", "1", "-vf",
                   f"scale={size}:{size}:force_original_aspect_ratio=decrease", str(tmp)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=20, check=False, **kwargs)
            except (subprocess.TimeoutExpired, OSError) as exc:
                log.debug("ffmpeg (aperçu) : %s", exc)
                break
            if tmp.exists() and tmp.stat().st_size > 0:
                break
        image = QImage(str(tmp)) if tmp.exists() else QImage()
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    if image.isNull():
        return None, f"🎬 Vidéo\n{path.name}\n({_UNAVAILABLE.lower()})"
    return image, ""


def load_preview(path: Path, size: int,
                 is_current: Callable[[], bool] = lambda: True) -> tuple[QImage | None, str]:
    """Charge l'aperçu d'un fichier : (image, texte de repli). Sans Qt GUI : utilisable hors du thread principal."""
    path = Path(path)
    ext = path.suffix.lower()
    if not path.is_file():
        return None, _UNAVAILABLE
    if ext in VIDEO_EXT:
        return _load_video_frame(path, size, is_current)
    if ext in AUDIO_EXT:
        return None, f"🎵 Audio\n{path.name}"
    image = _load_image_file(path, size)
    if image is None or image.isNull():
        return None, f"{_UNAVAILABLE}\n{path.name}"
    return image, ""


class _PreviewSignals(QObject):
    done = Signal(int, object, str)                   # (jeton, QImage | None, texte)


class _PreviewTask(QRunnable):
    def __init__(self, token_: int, path: Path, size: int, signals: _PreviewSignals,
                 is_current: Callable[[], bool]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._token, self._path, self._size = token_, path, size
        self._signals, self._is_current = signals, is_current

    def run(self) -> None:
        if not self._is_current():
            return
        image, text = load_preview(self._path, self._size, self._is_current)
        if not self._is_current():
            return
        try:
            self._signals.done.emit(self._token, image, text)
        except RuntimeError:                           # widget détruit entre-temps
            pass


class ImagePreview(QWidget):
    """Aperçu fluide et adaptatif : chargement asynchrone, redimensionné à la place disponible.

    * `size` : taille de confort (côté, en px) ; l'aperçu s'agrandit si la place le permet et ne passe
      jamais sous `min_size`.
    * L'image est décodée à `load_size` (≥ taille affichée) puis remise à l'échelle à chaque
      redimensionnement : rien n'est tronqué, les proportions sont conservées.
    * Un clic ouvre la visionneuse plein format (désactivable avec `zoomable=False`).
    """

    clicked = Signal()

    def __init__(self, size: int = 260, parent: QWidget | None = None, *, min_size: int = 140,
                 load_size: int = 720, zoomable: bool = True) -> None:
        super().__init__(parent)
        self._size = size
        self._min = min(min_size, size)
        self._load = max(load_size, size)
        self._zoomable = zoomable
        self._token = 0
        self._image: QImage | None = None
        self._path: Path | None = None
        self._signals = _PreviewSignals(self)
        self._signals.done.connect(self._on_loaded)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setMinimumSize(self._min, self._min)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(self._min)                   # jamais écrasé par une colonne trop pleine
        self._apply_style()
        ThemeManager.instance().changed.connect(lambda _t: self._apply_style())

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._min, self._min)

    def _apply_style(self) -> None:
        self.label.setStyleSheet(
            f"QLabel {{ background-color: {token('surface_alt')}; color: {token('muted')}; "
            f"border: 1px solid {token('border')}; border-radius: 10px; padding: 6px; }}")

    # ── API ──────────────────────────────────────────────────────────────────
    def current_path(self) -> Path | None:
        return self._path

    def show_image(self, path: str | Path) -> None:
        self._token += 1
        tok = self._token
        p = Path(path)
        self._path, self._image = p, None
        self.label.setPixmap(QPixmap())
        self.label.setToolTip(str(p))
        self._set_cursor()
        if not p.is_file():
            self.label.setText(_UNAVAILABLE)
            return
        self.label.setText("Chargement…")
        task = _PreviewTask(tok, p, self._load, self._signals, lambda t=tok: self._token == t)
        _pool().start(task)

    def show_text(self, text: str) -> None:
        self._token += 1                               # invalide tout chargement en cours
        self._image, self._path = None, None
        self.label.setPixmap(QPixmap())
        self.label.setText(text)
        self.label.setToolTip(text)
        self._set_cursor()

    def clear(self) -> None:
        self.show_text("")

    # ── Interne ──────────────────────────────────────────────────────────────
    def _set_cursor(self) -> None:
        zoom = self._zoomable and self._image is not None
        self.label.setCursor(Qt.CursorShape.PointingHandCursor if zoom else Qt.CursorShape.ArrowCursor)

    def _on_loaded(self, token_: int, image: object, text: str) -> None:
        if token_ != self._token:
            return                                     # résultat périmé
        if isinstance(image, QImage) and not image.isNull():
            self._image = image
            self.label.setText("")
            self._rescale()
        else:
            self._image = None
            self.label.setPixmap(QPixmap())
            self.label.setText(text or _UNAVAILABLE)
        self._set_cursor()

    def _rescale(self) -> None:
        if self._image is None or self._image.isNull():
            return
        box = self.label.contentsRect().size() - QSize(12, 12)        # marge intérieure du style
        box = box.expandedTo(QSize(32, 32))
        scaled = self._image.scaled(box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(QPixmap.fromImage(scaled))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._image is not None:
            self.clicked.emit()
            if self._zoomable and self._path is not None:
                open_image_viewer(self._path, self)


def open_image_viewer(path: Path, parent: QWidget | None = None) -> None:
    """Visionneuse plein format (redimensionnable) pour un fichier image ou une image de vidéo."""
    from PySide6.QtWidgets import QDialog

    dlg = QDialog(parent)
    dlg.setWindowTitle(Path(path).name)
    dlg.resize(960, 720)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(8, 8, 8, 8)
    view = ImagePreview(size=900, min_size=300, load_size=2400, zoomable=False)
    lay.addWidget(view, 1)
    view.show_image(path)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dlg.show()
