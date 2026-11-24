"""Composants graphiques réutilisables de la suite Azimut."""
from __future__ import annotations

import html
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import (
    QAbstractTableModel,
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QMimeData,
    QModelIndex,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QVariantAnimation,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QDesktopServices,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QImage,
    QLinearGradient,
    QImageReader,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from azimut.core import binaries
from azimut.core.ffmpeg_args import SAFE_INPUT_FLAGS, drop_neutral_input, input_spec, neutral_input
from azimut.core.logs import get_logger
from azimut.core.paths import user_cache_dir
from azimut.core.prefs import Preferences
from azimut.core.workers import LEVELS
from azimut.ui import icons
from azimut.ui.file_dialog import choose_directory, choose_file, choose_files, choose_save_file
from azimut.ui.glass import GlassFrame
from azimut.ui.icon_motion import HoverPixmap

try:                                                  # PySide6-Addons : lecture vidéo intégrée
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    MULTIMEDIA_AVAILABLE = True
except Exception:                                     # PySide6-Essentials seul : pas de lecteur
    MULTIMEDIA_AVAILABLE = False
from azimut.ui.theme import ThemeManager, caption_point_size, repolish, scaled, token

log = get_logger(__name__)

CHIP_LEVELS: tuple[str, ...] = ("ok", "warn", "err", "info", "neutre")


def _unavailable() -> str:
    """Texte de repli de l'aperçu."""
    return QCoreApplication.translate("Widgets", "Aperçu indisponible")


def plain_message(parent: QWidget | None, icon: QMessageBox.Icon, title: str, text: str,
                  buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
                  default: QMessageBox.StandardButton | None = None) -> int:
    """QMessageBox dont le texte reste littéral (nom de fichier, chemin, tag externe)."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    return box.exec()


def plain_tooltip(text: str) -> str:
    """Infobulle littérale : Qt n'a pas de format d'infobulle, <qt> + échappement fige le rendu."""
    return f"<qt>{html.escape(str(text))}</qt>"


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
class IconLabel(QLabel):
    """QLabel précédé d'une icône SVG (Papicons) : le texte reste du texte, l'icône est dessinée à gauche.

    `color` : token du thème (« muted », « warn_txt »…) ou code hexadécimal ; suit le thème à chaud.
    """

    GAP = 6

    def __init__(self, text: str = "", icon: str = "", color: str = "text",
                 parent: QWidget | None = None, *, animate: bool = True) -> None:
        """``animate=False`` fige le pictogramme, même au survol (Conseils, en-têtes)."""
        super().__init__(text, parent)
        self._animate = bool(animate)
        self._icon_name = ""
        self._icon_color = color
        self._hover = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(180)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_frame)
        ThemeManager.instance().changed.connect(self._on_theme_changed)
        if icon:
            self.set_icon(icon, color)

    def icon_name(self) -> str:
        return self._icon_name

    def set_icon(self, name: str, color: str | None = None) -> None:
        """Affiche l'icône `name` (chaîne vide ou icône inconnue : aucune icône)."""
        self._hover_anim.stop()
        self._hover = 0.0
        self._icon_name = name if icons.available(name) else ""
        if color:
            self._icon_color = color
        self._sync_layout()
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().enterEvent(event)
        if self._animate and self._icon_name and icons.motion_enabled():
            self._animate_hover(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().leaveEvent(event)
        if self._animate:
            self._animate_hover(0.0)

    def _animate_hover(self, target: float) -> None:
        """Grandit légèrement l'icône au survol (thème animé uniquement)."""
        if abs(self._hover - target) < 0.001:
            return
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(float(target))
        self._hover_anim.start()

    def _on_hover_frame(self, value: object) -> None:
        try:
            self._hover = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        self.update()

    def _on_theme_changed(self, _theme: str = "") -> None:
        self._hover_anim.stop()
        self._hover = 0.0
        self.update()

    def _icon_color_token(self) -> str:
        return self._icon_color

    def _icon_size(self) -> int:
        return max(12, round(self.fontMetrics().height() * 0.95))

    def _sync_layout(self) -> None:
        if self._icon_name:
            self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.setIndent(self._icon_size() + self.GAP)
        else:
            self.setIndent(-1)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and self._icon_name:
            self._sync_layout()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._icon_name:
            return
        size = self._icon_size()
        pm = icons.pixmap(self._icon_name, size, _themed(self._icon_color_token()))
        rect = self.contentsRect()
        if self.wordWrap():
            y = rect.top() + (self.fontMetrics().height() - size) / 2
        else:
            y = rect.top() + (rect.height() - size) / 2
        painter = QPainter(self)
        if self._hover > 0.001:
            # Le mouvement reste dans la case : le texte et la mise en page ne bougent pas.
            pm = icons.hovered_pixmap(pm, self._icon_name, self._hover)
        painter.drawPixmap(QPointF(rect.left(), y), pm)
        painter.end()


def _themed(color: str) -> str:
    """Token du thème → hexadécimal (un code hexadécimal passe tel quel)."""
    try:
        return token(color)
    except KeyError:
        return color


class DialogHeader(QWidget):
    """En-tête de boîte de dialogue : icône neutre, titre et sous-titre."""

    def __init__(self, title: str, subtitle: str = "", icon_name: str = "info",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._icon_name = icon_name if icons.available(icon_name) else ""
        self.icon_label = QLabel()
        self.icon_label.setObjectName("DialogIcon")
        self.icon_label.setFixedSize(40, 40)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_icon()
        self._icon_hover = HoverPixmap(
            self.icon_label, self.icon_label.pixmap, self.icon_label.setPixmap,
            lambda: self._icon_name)
        ThemeManager.instance().changed.connect(self._refresh_icon)
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("DialogTitle")
        text.addWidget(self.title_label)
        self.subtitle_label: QLabel | None = None
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("DialogSubtitle")
            self.subtitle_label.setWordWrap(True)
            text.addWidget(self.subtitle_label)
        row.addLayout(text, 1)

    def _refresh_icon(self, _theme: str = "") -> None:
        if self._icon_name:
            self.icon_label.setPixmap(icons.pixmap(self._icon_name, 20, "muted"))
        else:
            self.icon_label.clear()


_CHIP_ICON_TOKEN = {"ok": "ok_txt", "warn": "warn_txt", "err": "err_txt", "info": "info_txt", "neutre": "muted"}


class StatusChip(IconLabel):
    """Pastille d'état colorée (le style vient du QSS, selon la propriété « level »).

    Un texte commençant par ✔, ✖ ou ⚠ affiche l'icône SVG correspondante (plus de dépendance à une
    police d'emojis) ; `text()` ne renvoie alors que le texte, sans le glyphe.
    """

    def __init__(self, text: str = "", level: str = "neutre", parent: QWidget | None = None) -> None:
        super().__init__("", "", "muted", parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setObjectName("StatusChip")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.set_state(text, level)

    def _icon_color_token(self) -> str:
        return _CHIP_ICON_TOKEN.get(str(self.property("level")), "muted")

    def set_state(self, text: str, level: str) -> None:
        if level not in CHIP_LEVELS:
            level = "neutre"
        name, rest = icons.split_glyph(text)
        if name and icons.available(name):
            self.setText(rest)
            self.set_icon(name)
        else:
            self.setText(text)                       # glyphe inconnu ou icône absente : texte tel quel
            self.set_icon("")
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setProperty("level", level)
        # L'état n'est porté que par la couleur et l'icône : on le donne aussi en clair.
        meaning = {"ok": QCoreApplication.translate("Widgets", "disponible"), "warn": QCoreApplication.translate("Widgets", "facultatif ou incomplet"),
                   "err": QCoreApplication.translate("Widgets", "introuvable ou en erreur"), "info": QCoreApplication.translate("Widgets", "information"),
                   "neutre": QCoreApplication.translate("Widgets", "état inconnu")}.get(level, "")
        self.setAccessibleName(f"{self.text()} — {meaning}".strip())
        repolish(self)


# Nombre d'or (1,618…) : clé des proportions de l'interface, notamment le format des
# cartes de l'Atlas (hauteur = largeur / φ).
GOLDEN_RATIO = (1.0 + 5.0 ** 0.5) / 2.0


class Card(QFrame):
    """Bloc de contenu opaque avec titre optionnel ; ajouter les widgets dans `body_layout`.

    Le verre est réservé au chrome (en-tête, onglets, menus) : une carte de contenu
    reste une surface pleine, plus lisible pour les tableaux et les formulaires.
    """

    body_layout: QVBoxLayout

    def __init__(self, title: str = "", parent: QWidget | None = None, *,
                 interactive: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("ContentCard")
        self.setProperty("interactive", bool(interactive))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
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
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.label_widget = QLabel(label)
        self.edit = QLineEdit()
        self.edit.setAcceptDrops(False)               # le dépôt est géré par le widget parent
        # Texte court : les libellés longs étaient tronqués dans les champs étroits.
        self.edit.setPlaceholderText(
            self.tr("Glissez un dossier ici…") if mode == "dir" else self.tr("Glissez un fichier ici…"))
        self.edit.setToolTip(
            self.tr("Glissez un dossier ici ou cliquez sur « Parcourir… »") if mode == "dir"
            else self.tr("Glissez un fichier ici ou cliquez sur « Parcourir… »"))
        self.button = QPushButton(self.tr("Parcourir…"))
        self.button.setProperty("kind", "secondary")
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
        title = self.tr("Choisir : {label}").format(label=self._label_text)
        flt = self._filter or self.tr("Tous les fichiers (*)")
        if self._mode == "dir":
            chosen = choose_directory(self, title, start)
        elif self._mode == "file":
            chosen = choose_file(self, title, start, flt)
        else:
            chosen = choose_save_file(self, title, start, flt)
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
# Progression d'import
# ─────────────────────────────────────────────────────────────────────────────
class _MorphIcon(QWidget):
    """Icône dossier → coche : rotation, changement d'échelle et petit saut."""

    SIZE = 36
    ART = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._folder = QPixmap()
        self._check = QPixmap()
        self._progress = 0.0
        self.hide()

    def set_icons(self, folder: QPixmap, check: QPixmap) -> None:
        self._folder = folder
        self._check = check
        self.update()

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    def progress(self) -> float:
        return self._progress

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if self._folder.isNull() and self._check.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        progress = self._progress
        center = QPointF(self.rect().center()) + QPointF(0.0, -5.0 * math.sin(math.pi * progress))
        self._paint_icon(painter, self._folder, 1.0 - progress,
                         1.0 - 0.30 * progress, -16.0 * progress, center)
        check_scale = 0.45 + 0.55 * progress + 0.10 * math.sin(math.pi * progress)
        self._paint_icon(painter, self._check, progress,
                         check_scale, 14.0 * (1.0 - progress), center)
        painter.end()

    def _paint_icon(self, painter: QPainter, pixmap: QPixmap, opacity: float,
                    scale: float, rotation: float, center: QPointF) -> None:
        if pixmap.isNull() or opacity <= 0.0:
            return
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.translate(center)
        painter.rotate(rotation)
        painter.scale(scale, scale)
        painter.drawPixmap(QPointF(-self.ART / 2.0, -self.ART / 2.0), pixmap)
        painter.restore()


class LiquidProgressBar(QProgressBar):
    """Barre de progression remplie par un liquide visqueux.

    Le front de remplissage est une diagonale légèrement courbe, sans vague horizontale :
    le bas du liquide avance un peu avant le haut, puis le front se redresse à 100 %.
    La bande d'activité (total inconnu) est un bloc liquide à bords diagonaux qui traverse
    la piste. Le mouvement s'arrête quand la barre est cachée, pleine, ou quand
    « Réduire les animations » est actif.
    """

    FRAME_MS = 40
    HEIGHT = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(True)
        self.setFormat("%p %")
        self.setFixedHeight(self.HEIGHT)
        self._phase = 0.0
        self._segments: list[tuple[float, str]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._advance)
        self.valueChanged.connect(self._sync_motion)
        ThemeManager.instance().changed.connect(self._refresh)
        ThemeManager.instance().accessibility_changed.connect(self._sync_motion)

    def _indeterminate(self) -> bool:
        return self.maximum() == self.minimum()

    def set_segments(self, segments: Iterable[tuple[float, str]]) -> None:
        """Découpe la piste en segments proportionnels ``(part, jeton de couleur)``.

        Les parts sont normalisées : ``[(3, "seg_image"), (7, "seg_video")]`` donne
        30 % puis 70 % de la largeur. Un appel sans part positive revient à une piste uniforme.
        """
        parts = [(float(fraction), color) for fraction, color in segments]
        total = sum(part for part, _color in parts)
        self._segments = ([(part / total, color) for part, color in parts] if total > 0.0 else [])
        self.update()

    def clear_segments(self) -> None:
        """Revient à une piste uniforme (hors conversion)."""
        if self._segments:
            self._segments = []
            self.update()

    def _segment_pieces(self, rect: QRectF) -> Iterable[tuple[QRectF, str]]:
        """Rectangles des segments, dans l'ordre de la piste."""
        start = 0.0
        for fraction, color in self._segments:
            left = rect.left() + rect.width() * start
            yield QRectF(left, rect.top(), rect.width() * fraction, rect.height()), color
            start += fraction

    def _sync_motion(self, *_args: object) -> None:
        moving = (self.isVisible() and not ThemeManager.instance().reduce_motion()
                  and (self._indeterminate() or self.minimum() < self.value() < self.maximum()))
        if moving and not self._timer.isActive():
            self._timer.start()
        elif not moving:
            self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.22) % (2.0 * math.pi)
        self.update()

    def _refresh(self, *_args: object) -> None:
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_motion()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    # ── Peinture ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        if rect.width() <= 0 or rect.height() <= 0:
            painter.end()
            return
        radius = min(6.0, rect.height() / 2.0)
        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        self._paint_track(painter, track, rect)
        painter.setPen(QPen(QColor(token("border")), 1.0))
        painter.drawPath(track)
        painter.save()
        painter.setClipPath(track)
        if self._indeterminate():
            self._paint_indeterminate(painter, rect)
        else:
            span = self.maximum() - self.minimum()
            ratio = (self.value() - self.minimum()) / span if span > 0 else 0.0
            if ratio > 0.0:
                self._paint_fill(painter, rect, max(0.0, min(1.0, ratio)))
        painter.restore()
        self._paint_text(painter, rect)
        painter.end()

    def _paint_track(self, painter: QPainter, track: QPainterPath, rect: QRectF) -> None:
        painter.fillPath(track, QColor(token("surface_alt")))
        if not self._segments:
            return
        painter.save()
        painter.setClipPath(track)
        for piece, color in self._segment_pieces(rect):
            tint = QColor(token(color))
            tint.setAlpha(52)                           # repère la famille sans concurrencer le remplissage
            painter.fillRect(piece, tint)
        painter.setPen(QPen(QColor(token("border")), 1.0))
        edge = rect.left()
        for fraction, _color in self._segments[:-1]:
            edge += rect.width() * fraction
            painter.drawLine(QPointF(edge, rect.top()), QPointF(edge, rect.bottom()))
        painter.restore()

    def _paint_text(self, painter: QPainter, rect: QRectF) -> None:
        if not self.isTextVisible():
            return
        text = self.text()
        painter.save()
        painter.setFont(self.font())
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text)
        height = metrics.height()
        box = QRectF(rect.center().x() - width / 2.0 - 7.0, rect.center().y() - height / 2.0 - 1.0,
                     width + 14.0, height + 2.0)
        if self._segments:
            background = QColor(token("bg"))            # pastille discrète : le pourcentage reste lisible
            background.setAlpha(205)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(box, 5.0, 5.0)
        painter.setPen(QColor(token("text")))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _fill_brush(self, rect: QRectF) -> QBrush:
        gradient = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        gradient.setColorAt(0.0, QColor(token("primary")))
        gradient.setColorAt(1.0, QColor(token("primary_hover")))
        return QBrush(gradient)

    def _segment_brush(self, rect: QRectF, color: str) -> QBrush:
        base = QColor(token(color))
        gradient = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        gradient.setColorAt(0.0, base.lighter(112))
        gradient.setColorAt(1.0, base.darker(108))
        return QBrush(gradient)

    def _liquid_edge(self, rect: QRectF, ratio: float) -> tuple[QPainterPath, float, float]:
        """Front liquide diagonal ; renvoie aussi ses abscisses haute et basse pour le reflet."""
        height = rect.height()
        front = rect.left() + rect.width() * ratio
        slant = min(8.0, height * 0.8)
        # Très léger flottement du front : il reste une diagonale, jamais une vague.
        wobble = math.sin(self._phase) * min(0.8, height * 0.05)
        top_x = front - slant * 0.45 + wobble
        bottom_x = front + slant * 0.55 + wobble
        edge = QPainterPath()
        edge.moveTo(rect.left(), rect.bottom())
        edge.lineTo(rect.left(), rect.top())
        edge.lineTo(top_x, rect.top())
        edge.quadTo((top_x + bottom_x) / 2.0 + 1.2, rect.center().y(), bottom_x, rect.bottom())
        edge.lineTo(rect.left(), rect.bottom())
        edge.closeSubpath()
        return edge, top_x, bottom_x

    def _paint_fill(self, painter: QPainter, rect: QRectF, ratio: float) -> None:
        if self._segments:
            self._paint_segmented_fill(painter, rect, ratio)
            return
        brush = self._fill_brush(rect)
        if ratio >= 0.999:
            painter.fillRect(rect, brush)               # 100 % : plus aucun coin vide
            return
        edge, top_x, bottom_x = self._liquid_edge(rect, ratio)
        painter.fillPath(edge, brush)

        # Reflet qui suit le front diagonal, à l'intérieur du liquide.
        highlight = QPainterPath()
        highlight.moveTo(top_x - 1.0, rect.top())
        highlight.quadTo((top_x + bottom_x) / 2.0 + 0.2, rect.center().y(), bottom_x - 1.0, rect.bottom())
        painter.setPen(QPen(QColor(255, 255, 255, 85), 1.0))
        painter.drawPath(highlight)

    def _paint_segmented_fill(self, painter: QPainter, rect: QRectF, ratio: float) -> None:
        """Remplit chaque famille avec sa couleur, dans l'ordre de la piste."""
        if ratio >= 0.999:
            for piece, color in self._segment_pieces(rect):
                painter.fillRect(piece, self._segment_brush(rect, color))
            return
        edge, top_x, bottom_x = self._liquid_edge(rect, ratio)
        painter.save()
        painter.setClipPath(edge)
        for piece, color in self._segment_pieces(rect):
            painter.fillRect(piece, self._segment_brush(rect, color))
        painter.restore()

        highlight = QPainterPath()
        highlight.moveTo(top_x - 1.0, rect.top())
        highlight.quadTo((top_x + bottom_x) / 2.0 + 0.2, rect.center().y(), bottom_x - 1.0, rect.bottom())
        painter.setPen(QPen(QColor(255, 255, 255, 85), 1.0))
        painter.drawPath(highlight)

    def _paint_indeterminate(self, painter: QPainter, rect: QRectF) -> None:
        band = max(rect.height() * 2.2, rect.width() * 0.26)
        # Départ visible même sans animation (phase 0) : sinon la bande serait hors piste.
        travel = (self._phase / (2.0 * math.pi) + 0.35) % 1.0
        left = rect.left() - band + (rect.width() + band) * travel
        right = left + band
        slant = min(8.0, rect.height() * 0.8)
        path = QPainterPath()
        path.moveTo(left + slant, rect.top())
        path.lineTo(right, rect.top())
        path.quadTo(right - slant * 0.25, rect.center().y(), right - slant, rect.bottom())
        path.lineTo(left, rect.bottom())
        path.quadTo(left + slant * 0.25, rect.center().y(), left + slant, rect.top())
        path.closeSubpath()
        painter.fillPath(path, self._fill_brush(rect))

        lead = QPainterPath()
        lead.moveTo(right - 1.0, rect.top())
        lead.quadTo(right - slant * 0.25 - 1.0, rect.center().y(), right - slant - 1.0, rect.bottom())
        painter.setPen(QPen(QColor(255, 255, 255, 75), 1.0))
        painter.drawPath(lead)


class ImportProgress(QWidget):
    """Progression d'import avec pourcentage et état de fin temporaire.

    En mode normal, le panneau s'insère dans une carte (Dedup, Scope). En mode
    ``overlay``, il recouvre une DropZone : l'icône se déplace vers la barre,
    puis se transforme en coche avant de rendre la zone à son état initial.
    """

    HEIGHT = 26
    ICON_SIZE = 28
    MORPH_SIZE = _MorphIcon.SIZE
    BAR_HEIGHT = 20
    GAP = 12
    MARGIN = 16
    BAR_MAX_WIDTH = 420
    COMPLETION_HOLD_MS = 3000
    MIN_PROGRESS_MS = 1000          # la barre reste visible même si l'import est instantané
    VALUE_FRAME_MS = 16
    VALUE_EASE = 0.22

    started = Signal()
    restored = Signal()

    def __init__(self, parent: QWidget | None = None, *, icon: str = "", overlay: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("ImportProgress")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay = overlay
        self._icon_name = icon if icons.available(icon) else ""
        self._active = False
        self._state = "idle"
        self._indeterminate = False
        self._icon_start = QPoint()
        self._icon_target = QPoint()
        self._bar_final_width = 0.0
        self._entry_active = False
        self.entry_ms = 680
        self.morph_ms = 520
        self.restore_ms = 380
        self.completion_ms = self.COMPLETION_HOLD_MS
        self.min_progress_ms = self.MIN_PROGRESS_MS
        self.value_ms = 260
        self._started_at = 0.0
        self._target_value = 0
        self._entry: QVariantAnimation | None = None
        self._morph_anim: QVariantAnimation | None = None
        self._done_anim: QPropertyAnimation | None = None
        self._done_effect: QGraphicsOpacityEffect | None = None
        self._restore_group: QParallelAnimationGroup | None = None
        self._exit_effect: QGraphicsOpacityEffect | None = None

        self.bar = LiquidProgressBar(self)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p %")
        self.bar.setFixedHeight(LiquidProgressBar.HEIGHT)
        self.bar.setRange(0, 100)
        self.bar.hide()
        self._value_timer = QTimer(self)
        self._value_timer.setInterval(self.VALUE_FRAME_MS)
        self._value_timer.timeout.connect(self._step_value)

        self.done_label = QLabel(self.tr("Importation terminée !"), self)
        self.done_label.setObjectName("ImportDone")
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.done_label.hide()

        self.morph = _MorphIcon(self)
        for widget in (self.bar, self.done_label, self.morph):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        if overlay:
            # Positionnement manuel : l'icône peut se déplacer librement.
            self.bar.setParent(self)
            self.done_label.setParent(self)
        else:
            self.setFixedHeight(self.HEIGHT)
            lay = QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            lay.addWidget(self.bar, 1)
            lay.addWidget(self.done_label, 0)

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._restore)
        self._end_timer = QTimer(self)
        self._end_timer.setSingleShot(True)
        self._end_timer.timeout.connect(self._finish_import)
        ThemeManager.instance().changed.connect(self._refresh_style)
        self._refresh_style()
        self.hide()

    def _refresh_style(self, _theme: str = "") -> None:
        self.done_label.setStyleSheet(f"color: {token('ok_txt')}; font-weight: 600;")
        if self._icon_name:
            self.morph.set_icons(icons.pixmap(self._icon_name, self.ICON_SIZE, "muted"),
                                 icons.pixmap("check", self.ICON_SIZE, "ok_txt"))

    def is_importing(self) -> bool:
        return self._active

    def is_completed(self) -> bool:
        """Vrai pendant les 3 secondes où le message de fin est affiché."""
        return self._state == "done"

    def begin_import(self) -> None:
        """Affiche la barre et fait entrer l'icône dans le groupe centré."""
        self._hold_timer.stop()
        self._end_timer.stop()
        self._stop_animations()
        self._active = True
        self._state = "progress"
        self._indeterminate = True
        self._started_at = time.monotonic()
        self._target_value = 0
        self._reset_icon_visual()
        self.done_label.hide()
        self.bar.show()
        self.bar.setRange(0, 0)                 # une seule barre : indicateur d'activité continu
        self.bar.setValue(0)
        if self._overlay:
            self._icon_start = self._overlay_icon_start()
            self._bar_final_width = self._final_bar_width()
            self._apply_entry(0.0)
        self.started.emit()
        self.show()
        self.raise_()
        if self._overlay:
            if self._icon_name:
                self.morph.show()
            self._start_entry()

    def set_import_progress(self, done: int, total: int, label: str = "") -> None:
        """Total connu : barre déterminée ; total inconnu : indicateur d'activité continu."""
        if not self._active:
            return
        if total <= 0:
            # Ne jamais repartir de zéro après une progression connue : cela donnait
            # l'impression que la barre se rechargeait au milieu de l'import.
            if not self._indeterminate and self.bar.maximum() == 0:
                self._indeterminate = True
                self.bar.setRange(0, 0)
                self.bar.setValue(0)
            return
        self._indeterminate = False
        total = max(1, int(total))
        target = max(0, min(int(done), total))
        self.bar.setRange(0, total)
        self._animate_to(target)

    def _animate_to(self, target: int) -> None:
        """Fait suivre la valeur cible sans à-coups, même quand les signaux arrivent vite."""
        target = max(self.bar.minimum(), min(int(target), self.bar.maximum()))
        self._target_value = target
        if target <= self.bar.value():
            self._value_timer.stop()
            self.bar.setValue(target)
            return
        if self.value_ms <= 0:
            self._value_timer.stop()
            self.bar.setValue(target)
            return
        if not self._value_timer.isActive():
            self._value_timer.start()

    def _step_value(self) -> None:
        current = self.bar.value()
        target = self._target_value
        if current >= target:
            self._value_timer.stop()
            return
        step = max(1, int(math.ceil((target - current) * self.VALUE_EASE)))
        self.bar.setValue(min(target, current + step))
        if self.bar.value() >= target:
            self._value_timer.stop()

    def end_import(self) -> None:
        """Termine l'import sans escamoter la barre : durée minimale puis état de fin."""
        if not self._active:
            return
        self._active = False
        if self._state in ("finishing", "done"):
            return
        self._state = "finishing"
        if self.bar.maximum() <= self.bar.minimum():
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
        self._indeterminate = False
        self._animate_to(self.bar.maximum())
        elapsed_ms = (time.monotonic() - self._started_at) * 1000.0
        delay = max(self.min_progress_ms - elapsed_ms, self.value_ms if self.value_ms > 0 else 0.0)
        if delay <= 0:
            self._finish_import()
        else:
            self._end_timer.start(int(delay))

    def _finish_import(self) -> None:
        """Passe à l'état de fin une fois la progression montrée assez longtemps."""
        if self._state != "finishing":
            return
        self._state = "done"
        self._value_timer.stop()
        self.bar.setValue(self.bar.maximum())
        self.bar.hide()
        self.done_label.show()
        if self._overlay:
            self._entry_active = False
            if self._entry is not None:
                self._entry.stop()
            self._layout_overlay()
            self._animate_check()
            self._animate_done()
        self._hold_timer.start(self.completion_ms)

    def _overlay_icon_start(self) -> QPoint:
        """Centre de l'icône au repos, converti pour le canevas de transformation."""
        parent = self.parent()
        start = getattr(parent, "import_icon_start", None)
        if callable(start):
            center = start()
        else:
            center = QPoint(self.width() // 2, self.height() // 2)
        return QPoint(center.x() - self.MORPH_SIZE // 2,
                      center.y() - self.MORPH_SIZE // 2)

    def _max_bar_width(self) -> float:
        icon_width = self.MORPH_SIZE if self._icon_name else 0
        gap = self.GAP if icon_width else 0
        return max(0.0, float(self.width() - 2 * self.MARGIN - icon_width - gap))

    def _final_bar_width(self) -> float:
        available = self._max_bar_width()
        if available <= 0:
            return 0.0
        preferred = max(120.0, self.width() * 0.55)
        return min(float(self.BAR_MAX_WIDTH), preferred, available)

    def _overlay_geometry(self, bar_width: float) -> tuple[QPoint, QRect]:
        """Groupe icône + barre centré horizontalement, sur un même axe vertical."""
        icon_width = self.MORPH_SIZE if self._icon_name else 0
        gap = self.GAP if icon_width else 0
        bar_width = max(0.0, min(float(bar_width), self._max_bar_width()))
        group_width = icon_width + gap + bar_width
        x = max(0.0, (self.width() - group_width) / 2.0)
        icon_y = max(0.0, (self.height() - self.MORPH_SIZE) / 2.0)
        bar_x = x + icon_width + gap
        bar_y = max(0.0, (self.height() - self.BAR_HEIGHT) / 2.0)
        return (QPoint(round(x), round(icon_y)),
                QRect(round(bar_x), round(bar_y), round(bar_width), self.BAR_HEIGHT))

    def _layout_done(self, bar_rect: QRect) -> None:
        available = max(40, self.width() - bar_rect.x() - self.MARGIN)
        width = min(available, max(bar_rect.width(), 260))
        self.done_label.setGeometry(
            bar_rect.x(), max(0, (self.height() - self.HEIGHT) // 2), width, self.HEIGHT)

    def _layout_overlay(self) -> None:
        """Place le groupe centré, la barre et le message dans la zone de dépôt."""
        if not self._overlay:
            return
        self._bar_final_width = self._final_bar_width()
        target, bar_rect = self._overlay_geometry(self._bar_final_width)
        self._icon_target = target
        if self._icon_name:
            self.morph.move(target)
        self.bar.setGeometry(bar_rect)
        self._layout_done(bar_rect)

    def _apply_entry(self, value: float) -> None:
        """Déplace l'icône et étire la barre en gardant le groupe centré."""
        raw = float(value)
        progress = max(0.0, min(1.0, raw))
        growth = max(0.0, min(1.08, raw))          # léger dépassement de l'OutBack
        target, bar_rect = self._overlay_geometry(self._bar_final_width * growth)
        if self._icon_name:
            x = self._icon_start.x() + (target.x() - self._icon_start.x()) * progress
            y = self._icon_start.y() + (target.y() - self._icon_start.y()) * progress
            y -= 7.0 * math.sin(math.pi * progress)  # petit saut pendant l'entrée
            self.morph.move(round(x), round(y))
        self._icon_target = target
        self.bar.setGeometry(bar_rect)
        self._layout_done(bar_rect)

    def _start_entry(self) -> None:
        entry = QVariantAnimation(self)
        entry.setDuration(self.entry_ms)
        entry.setStartValue(0.0)
        entry.setEndValue(1.0)
        entry.setEasingCurve(QEasingCurve.Type.OutBack)
        entry.valueChanged.connect(self._apply_entry)
        entry.finished.connect(self._finish_entry)
        self._entry = entry
        self._entry_active = True
        entry.start()

    def _finish_entry(self) -> None:
        self._entry_active = False
        self._apply_entry(1.0)

    def _animate_check(self) -> None:
        """Transforme le dossier en coche : rotation, échelle et petit saut."""
        if not self._icon_name:
            return
        self._stop_animations()
        self._layout_overlay()
        self.morph.set_progress(0.0)
        self.morph.show()
        self.morph.raise_()
        anim = QVariantAnimation(self)
        anim.setDuration(self.morph_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self.morph.set_progress)
        self._morph_anim = anim
        anim.start()

    def _animate_done(self) -> None:
        """Fait apparaître le message de fin en fondu par-dessus la barre disparue."""
        self.done_label.raise_()
        if self._done_effect is not None:
            self.done_label.setGraphicsEffect(None)
        self._done_effect = QGraphicsOpacityEffect(self.done_label)
        self._done_effect.setOpacity(0.0)
        self.done_label.setGraphicsEffect(self._done_effect)
        anim = QPropertyAnimation(self._done_effect, b"opacity", self)
        anim.setDuration(420)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._done_anim = anim
        anim.start()

    def _stop_animations(self) -> None:
        self._value_timer.stop()
        for animation in (self._entry, self._morph_anim, self._done_anim):
            if animation is not None:
                animation.stop()
        if self._restore_group is not None:
            self._restore_group.stop()
            self._restore_group = None
        self._entry_active = False

    def _reset_icon_visual(self) -> None:
        self._stop_animations()
        self.morph.set_progress(0.0)
        self.morph.hide()
        if self._exit_effect is not None:
            self.morph.setGraphicsEffect(None)
            self._exit_effect = None
        if self._done_effect is not None:
            self.done_label.setGraphicsEffect(None)
            self._done_effect = None

    def _restore(self) -> None:
        """Fait disparaître le message de fin avant de rendre la zone d'origine."""
        self._hold_timer.stop()
        self._active = False
        if self._state == "restoring":
            return
        self._state = "restoring"
        if not self._overlay or self.restore_ms <= 0 or not self.done_label.isVisible():
            self._finish_restore()
            return
        group = QParallelAnimationGroup(self)
        if self._done_effect is None:
            self._done_effect = QGraphicsOpacityEffect(self.done_label)
            self.done_label.setGraphicsEffect(self._done_effect)
        self._done_effect.setOpacity(1.0)
        done_anim = QPropertyAnimation(self._done_effect, b"opacity", self)
        done_anim.setDuration(self.restore_ms)
        done_anim.setStartValue(1.0)
        done_anim.setEndValue(0.0)
        done_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        group.addAnimation(done_anim)
        if self._icon_name and self.morph.isVisible():
            self._exit_effect = QGraphicsOpacityEffect(self.morph)
            self._exit_effect.setOpacity(1.0)
            self.morph.setGraphicsEffect(self._exit_effect)
            icon_anim = QPropertyAnimation(self._exit_effect, b"opacity", self)
            icon_anim.setDuration(self.restore_ms)
            icon_anim.setStartValue(1.0)
            icon_anim.setEndValue(0.0)
            icon_anim.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(icon_anim)
        group.finished.connect(self._finish_restore)
        self._restore_group = group
        group.start()

    def _finish_restore(self) -> None:
        self._restore_group = None
        self._reset_icon_visual()
        self.bar.hide()
        self.done_label.hide()
        self.hide()
        self._state = "idle"
        self.restored.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._overlay:
            return
        if self._entry_active:
            if self._entry is not None:
                self._entry.stop()
            self._entry_active = False
        self._layout_overlay()


# ─────────────────────────────────────────────────────────────────────────────
# Zone de glisser-déposer
# ─────────────────────────────────────────────────────────────────────────────
class DropZone(GlassFrame):
    """Grande zone de dépôt en verre, cliquable (menu « Choisir des fichiers… / Choisir un dossier… »)."""

    pathsDropped = Signal(list)
    JUMP = 8        # hauteur du petit saut du pictogramme au survol
    MIN_HEIGHT = 96  # hauteur au repos, avant mise à l'échelle du zoom

    def __init__(self, text: str, *, accept_dirs: bool = True, accept_files: bool = True,
                 extensions: Iterable[str] | None = None, icon: str = "folder",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=8, interactive=True)
        self.setObjectName("DropZone")
        self._accept_dirs = accept_dirs
        self._accept_files = accept_files
        self._extensions = _normalize_extensions(extensions)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(scaled(self.MIN_HEIGHT))
        self.setProperty("dragOver", False)
        self._import_visual_active = False
        self._held_visual_policies: list[tuple[QWidget, QSizePolicy]] = []
        self.import_progress = ImportProgress(self, icon=icon, overlay=True)
        self.import_progress.started.connect(self._on_import_started)
        self.import_progress.restored.connect(self._on_import_restored)
        lay = QVBoxLayout(self)
        # la marge haute cède la place réservée au saut : au repos, le pictogramme
        # occupe exactement la même position qu'avant.
        lay.setContentsMargins(16, 16 - self.JUMP, 16, 16)
        lay.setSpacing(6)
        self._icon_box = QWidget()
        self._icon_box.setFixedSize(28, 28 + self.JUMP)
        self._icon_box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._icon_label = QLabel(self._icon_box)
        self._icon_label.setFixedSize(28, 28)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._icon_label.move(0, self.JUMP)
        lay.addWidget(self._icon_box, 0, Qt.AlignmentFlag.AlignHCenter)
        self._bounce_anim: QPropertyAnimation | None = None
        self._restore_group: QParallelAnimationGroup | None = None
        self._restore_effects: list[tuple[QWidget, QGraphicsOpacityEffect]] = []
        self.restore_visual_ms = 320
        self.bounce_ms = 1000
        self._bouncing = False
        self._icon_name = ""
        self.set_icon(icon)
        ThemeManager.instance().changed.connect(self._refresh_icon)
        self._label = QLabel(text)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._label)

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def set_icon(self, name: str) -> None:
        """Pictogramme affiché au-dessus du texte (« folder », « check »… ; vide = aucun)."""
        self._icon_name = name if icons.available(name) else ""
        self._refresh_icon()

    def _refresh_icon(self, _theme: str = "") -> None:
        self.setMinimumHeight(scaled(self.MIN_HEIGHT))     # le texte grandit avec le zoom
        if self._import_visual_active:
            self._icon_label.hide()
            self._icon_box.hide()
            return
        if self._icon_name:
            self._icon_label.setPixmap(icons.pixmap(self._icon_name, 28, "muted"))
            self._icon_label.show()
            self._icon_box.show()
        else:
            self._icon_label.clear()
            self._icon_label.hide()
            self._icon_box.hide()
            self._stop_bounce()

    def import_icon_start(self) -> QPoint:
        """Centre du pictogramme au repos, pour y faire naître l'icône animée."""
        return self._icon_label.mapTo(self, self._icon_label.rect().center())

    def _hold_visual_space(self, hold: bool) -> None:
        """Réserve la place du texte et du pictogramme masqués pendant l'import.

        Sans cette empreinte, masquer les enfants réduisait le sizeHint de la zone :
        la barre de progression faisait alors sauter la hauteur du bloc de dépôt.
        """
        if hold:
            self._held_visual_policies = []
            for widget in (self._icon_box, self._label):
                if widget.isHidden():
                    continue
                policy = QSizePolicy(widget.sizePolicy())
                self._held_visual_policies.append((widget, policy))
                retained = QSizePolicy(policy)
                retained.setRetainSizeWhenHidden(True)
                widget.setSizePolicy(retained)
            return
        for widget, policy in self._held_visual_policies:
            widget.setSizePolicy(policy)
        self._held_visual_policies.clear()

    def _on_import_started(self) -> None:
        """Efface le texte d'invite et l'icône du repos sans changer la taille du bloc."""
        self._clear_restore_animation()
        self._import_visual_active = True
        self._stop_bounce()
        self._hold_visual_space(True)
        self._icon_box.hide()
        self._label.hide()

    def _on_import_restored(self) -> None:
        """Rétablit la zone d'import avec un fondu d'entrée."""
        self._import_visual_active = False
        self._label.show()
        self._refresh_icon()
        self._hold_visual_space(False)
        self._reset_icon_position()
        self._animate_original_in()
        self._schedule_hover_check()

    def _animate_original_in(self) -> None:
        """Ramène l'invite et le pictogramme en fondu après l'overlay d'import."""
        self._clear_restore_animation()
        if self.restore_visual_ms <= 0:
            return
        group = QParallelAnimationGroup(self)
        for widget in (self._icon_box, self._label):
            if widget.isHidden():
                continue
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(self.restore_visual_ms)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(anim)
            self._restore_effects.append((widget, effect))
        if not self._restore_effects:
            return
        group.finished.connect(self._finish_original_in)
        self._restore_group = group
        group.start()

    def _finish_original_in(self) -> None:
        self._restore_group = None
        for widget, effect in self._restore_effects:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        self._restore_effects.clear()

    def _clear_restore_animation(self) -> None:
        if self._restore_group is not None:
            self._restore_group.stop()
            self._restore_group = None
        for widget, effect in self._restore_effects:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        self._restore_effects.clear()

    # ── Survol : rebond continu du pictogramme ───────────────────────────────
    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._start_bounce()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_bounce()
        super().hideEvent(event)

    def _sync_hover(self) -> None:
        """Recale le rebond après un Leave passé à un enfant ou à une modale."""
        super()._sync_hover()
        self._sync_bounce()

    def _sync_bounce(self) -> None:
        """Maintient le rebond tant que le curseur est dans la zone, enfants compris."""
        if not self.isVisible() or QApplication.activeModalWidget() is not None:
            self._stop_bounce()
            return
        under = QApplication.widgetAt(QCursor.pos())
        if under is not None and (under is self or self.isAncestorOf(under)):
            self._start_bounce()
        else:
            self._stop_bounce()

    def _start_bounce(self) -> None:
        """Fait rebondir le pictogramme de `JUMP` pixels, en boucle, au survol.

        La boîte garde la hauteur du rebond : ni le texte ni les widgets voisins ne bougent.
        Un cycle dure ``bounce_ms`` et se répète tant que la zone reste survolée.
        """
        if self._bouncing or not self._icon_name or self._icon_box.isHidden():
            return
        rest = self._icon_box.height() - self._icon_label.height()
        if self._bounce_anim is None:
            anim = QPropertyAnimation(self._icon_label, b"pos", self)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._bounce_anim = anim
        anim = self._bounce_anim
        anim.stop()
        anim.setDuration(self.bounce_ms)
        anim.setLoopCount(-1)
        anim.setKeyValueAt(0.0, QPoint(0, rest))
        anim.setKeyValueAt(0.35, QPoint(0, 0))       # sommet du rebond
        anim.setKeyValueAt(1.0, QPoint(0, rest))
        self._icon_label.move(0, rest)
        self._bouncing = True
        anim.start()

    def _stop_bounce(self) -> None:
        """Arrête le rebond et repose le pictogramme."""
        if self._bounce_anim is not None:
            self._bounce_anim.stop()
        self._bouncing = False
        self._reset_icon_position()

    def _reset_icon_position(self) -> None:
        self._icon_label.move(0, self._icon_box.height() - self._icon_label.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "import_progress"):
            self.import_progress.setGeometry(0, 0, self.width(), self.height())
            self.import_progress.raise_()

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
            # Le sélecteur est modal : vérifier le reflet pendant puis après son ouverture
            # évite qu'un Leave perdu ne le laisse allumé au retour.
            self._schedule_hover_check()
            self._open_chooser()
            self._schedule_hover_check()
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
            act_files = menu.addAction(self.tr("Choisir des fichiers…"))
            act_dir = menu.addAction(self.tr("Choisir un dossier…"))
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
            flt = self.tr("Fichiers acceptés ({masks});;Tous les fichiers (*)").format(masks=masks)
        else:
            flt = self.tr("Tous les fichiers (*)")
        files = choose_files(self, self.tr("Choisir des fichiers"), _start_dir(), flt)
        if files:
            _remember_dir(files[0])
            paths = self.filter_paths(os.path.abspath(f) for f in files)
            if paths:
                self.pathsDropped.emit(paths)

    def _choose_dir(self) -> None:
        d = choose_directory(self, self.tr("Choisir un dossier"), _start_dir())
        if d:
            _remember_dir(d)
            self.pathsDropped.emit([os.path.abspath(d)])


class PathDropZone(DropZone):
    """Zone de dépôt qui mémorise un dossier et expose l'API d'un ``PathField``.

    Dedup et Scope comparent deux dossiers : chaque zone garde son propre chemin,
    affiche le dossier retenu et anime l'import comme les zones des autres outils.
    """

    pathChanged = Signal(str)
    swap_ms = 520
    SWAP_PORTAL_WIDTH = 0.055
    SWAP_PORTAL_HEIGHT = 1.08
    SWAP_BLUR = 16
    MIN_HEIGHT = 104

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        self._title = title
        self._path = ""
        self._swap_group: QVariantAnimation | None = None
        self._swap_overlays: list[QLabel] = []
        self._swap_tracks: list[tuple[QLabel, QRect, QRect, bool]] = []
        self._swap_hidden_sources: list[PathDropZone] = []
        super().__init__(f"{title}\n{self.tr('Glissez un dossier ici…')}",
                         accept_dirs=True, accept_files=True, icon="folder", parent=parent)
        self.setAccessibleName(title)
        # Le chemin reste sur une ligne (il est élidé au milieu), mais l'invite peut
        # se replier : à 150 %, deux zones côte à côte ne font plus que ~160 px.
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.pathsDropped.connect(self._on_paths_dropped)

        self._clear = QToolButton(self)
        self._clear.setText("×")
        self._clear.setAutoRaise(True)
        self._clear.setFixedSize(22, 22)
        self._clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear.setToolTip(self.tr("Effacer le dossier"))
        self._clear.setAccessibleName(self.tr("Effacer le dossier"))
        self._clear.clicked.connect(self.clear)
        self._clear.hide()
        self._refresh_label()

    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        """Change le dossier retenu ; un chemin vide efface la sélection."""
        value = str(path or "").strip()
        if value == self._path:
            return
        self._path = value
        self._refresh_label()
        self.pathChanged.emit(value)

    def clear(self) -> None:
        self.set_path("")

    def animate_swap(self, other: PathDropZone) -> None:
        """Échange les chemins en faisant converger les zones vers un point d'inversion.

        Les anciens contenus se compriment et se floutent vers le centre, puis les
        nouveaux contenus, déjà échangés, émergent du même point et reprennent leur
        place. Le passage central masque le changement de texte et donne l'illusion
        d'une inversion physique des deux zones.
        """
        current, other_path = self.path(), other.path()
        if current == other_path:
            return
        # Les deux zones peuvent être dans des conteneurs différents (un renflement autour
        # de chacune) : on remonte jusqu'à leur plus proche ancêtre commun pour les copies.
        parent = self.parentWidget()
        while parent is not None and not parent.isAncestorOf(other):
            parent = parent.parentWidget()
        if (not self.isVisible() or not other.isVisible() or parent is None
                or not parent.isAncestorOf(self) or self.width() < 2 or other.width() < 2):
            self.set_path(other_path)
            other.set_path(current)
            return

        first_pixmap, second_pixmap = self.grab(), other.grab()
        if first_pixmap.isNull() or second_pixmap.isNull():
            self.set_path(other_path)
            other.set_path(current)
            return

        self._clear_swap_animation()
        first_rect = QRect(self.mapTo(parent, QPoint(0, 0)), self.size())
        second_rect = QRect(other.mapTo(parent, QPoint(0, 0)), other.size())
        portal = self._swap_portal(first_rect, second_rect)

        # Les anciennes copies couvrent encore les zones pendant l'échange des chemins.
        old_first = self._swap_overlay(parent, first_pixmap, first_rect)
        old_second = self._swap_overlay(parent, second_pixmap, second_rect)
        self._swap_overlays = [old_first, old_second]
        self.set_path(other_path)
        other.set_path(current)
        new_first_pixmap, new_second_pixmap = self.grab(), other.grab()
        if new_first_pixmap.isNull() or new_second_pixmap.isNull():
            self._finish_swap_animation()
            return

        # Les nouvelles copies repartiront du centre vers leur zone définitive.
        new_first = self._swap_overlay(parent, new_first_pixmap, first_rect)
        new_second = self._swap_overlay(parent, new_second_pixmap, second_rect)
        new_first.hide()
        new_second.hide()
        self._swap_overlays = [old_first, old_second, new_first, new_second]
        # Sans cela, les zones réelles restent visibles sous les copies et donnent
        # l'impression que d'anciennes zones fixes subsistent en arrière-plan.
        self._hide_swap_sources(other)
        self._swap_tracks = [
            (old_first, first_rect, portal, False),
            (old_second, second_rect, portal, False),
            (new_first, portal, first_rect, True),
            (new_second, portal, second_rect, True),
        ]

        animation = QVariantAnimation(self)
        animation.setDuration(self.swap_ms)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.valueChanged.connect(lambda value: self._apply_swap_frame(float(value)))
        animation.finished.connect(self._finish_swap_animation)
        self._swap_group = animation
        self._apply_swap_frame(0.0)
        animation.start()

    @classmethod
    def _swap_portal(cls, first: QRect, second: QRect) -> QRect:
        """Rectangle étroit et étiré où les deux contenus se rejoignent."""
        center_x = (first.center().x() + second.center().x()) / 2.0
        center_y = (first.center().y() + second.center().y()) / 2.0
        width = max(scaled(12), round(min(first.width(), second.width()) * cls.SWAP_PORTAL_WIDTH))
        height = max(scaled(24), round((first.height() + second.height()) / 2.0 * cls.SWAP_PORTAL_HEIGHT))
        return QRect(round(center_x - width / 2.0), round(center_y - height / 2.0), width, height)

    @staticmethod
    def _lerp_rect(start: QRect, end: QRect, progress: float) -> QRect:
        """Interpole un rectangle pour la traversée des copies."""
        progress = max(0.0, min(1.0, progress))

        def mix(a: int, b: int) -> int:
            return round(a + (b - a) * progress)

        return QRect(mix(start.x(), end.x()), mix(start.y(), end.y()),
                     max(1, mix(start.width(), end.width())), max(1, mix(start.height(), end.height())))

    def _apply_swap_frame(self, progress: float) -> None:
        """Fait converger les anciens contenus, puis éclore les nouveaux."""
        progress = max(0.0, min(1.0, progress))
        incoming = progress >= 0.5
        phase = (progress - 0.5) / 0.5 if incoming else progress / 0.5
        phase = max(0.0, min(1.0, phase))
        for overlay, start, end, is_incoming in self._swap_tracks:
            if is_incoming != incoming:
                overlay.hide()
                continue
            if overlay.isHidden():
                overlay.show()
                overlay.raise_()
            overlay.setGeometry(self._lerp_rect(start, end, phase))
            effect = overlay.graphicsEffect()
            if isinstance(effect, QGraphicsBlurEffect):
                # Le flou culmine au point d'inversion, quand la copie est la plus comprimée.
                strength = math.cos(phase * math.pi / 2) if is_incoming else math.sin(phase * math.pi / 2)
                effect.setBlurRadius(scaled(self.SWAP_BLUR) * strength)

    @staticmethod
    def _swap_overlay(parent: QWidget, pixmap: QPixmap, rect: QRect) -> QLabel:
        """Copie visuelle d'une zone, posée au-dessus du formulaire pendant l'échange."""
        overlay = QLabel(parent)
        overlay.setPixmap(pixmap)
        overlay.setScaledContents(True)
        overlay.setGeometry(rect)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        effect = QGraphicsBlurEffect(overlay)
        effect.setBlurRadius(0.0)
        overlay.setGraphicsEffect(effect)
        overlay.show()
        overlay.raise_()
        return overlay

    def _hide_swap_sources(self, other: PathDropZone) -> None:
        """Rend les zones réelles invisibles sous leurs copies animées, sans les retirer du layout."""
        for zone in (self, other):
            effect = QGraphicsOpacityEffect(zone)
            effect.setOpacity(0.0)
            zone.setGraphicsEffect(effect)
        self._swap_hidden_sources = [self, other]

    def _show_swap_sources(self) -> None:
        for zone in self._swap_hidden_sources:
            zone.setGraphicsEffect(None)
        self._swap_hidden_sources = []

    def _clear_swap_animation(self) -> None:
        self._show_swap_sources()
        if self._swap_group is not None:
            self._swap_group.stop()
            self._swap_group.deleteLater()
            self._swap_group = None
        self._swap_tracks = []
        for overlay in self._swap_overlays:
            overlay.hide()
            overlay.deleteLater()
        self._swap_overlays = []

    def _finish_swap_animation(self) -> None:
        # Restaure les zones avant de retirer les copies pour éviter un flash vide.
        self._show_swap_sources()
        if self._swap_group is not None:
            self._swap_group.deleteLater()
            self._swap_group = None
        self._swap_tracks = []
        for overlay in self._swap_overlays:
            overlay.hide()
            overlay.deleteLater()
        self._swap_overlays = []

    def _on_paths_dropped(self, paths: list[str]) -> None:
        """Un fichier déposé désigne son dossier parent, comme dans les autres zones."""
        if not paths:
            return
        first = Path(paths[0])
        self.set_path(str(first if first.is_dir() else first.parent))

    def _refresh_label(self) -> None:
        if self._path:
            self._label.setWordWrap(False)
            width = max(80, self.width() - 2 * scaled(16))
            shown = self._label.fontMetrics().elidedText(
                self._path, Qt.TextElideMode.ElideMiddle, width)
            self._label.setText(f"{self._title}\n{shown}")
            self.setToolTip(plain_tooltip(f"{self._title}\n{self._path}"))
            self.setAccessibleName(f"{self._title} — {self._path}")
        else:
            self._label.setWordWrap(True)
            self._label.setText(f"{self._title}\n{self.tr('Glissez un dossier ici…')}")
            self.setToolTip(self.tr("Glissez un dossier ici ou cliquez pour parcourir"))
            self.setAccessibleName(self._title)
        self._sync_clear()

    def _sync_clear(self) -> None:
        if hasattr(self, "_clear"):
            self._clear.setVisible(bool(self._path) and not self._import_visual_active)

    def _on_import_started(self) -> None:
        super()._on_import_started()
        self._clear.hide()

    def _on_import_restored(self) -> None:
        super()._on_import_restored()
        self._refresh_label()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "_clear"):
            return
        self._clear.move(self.width() - self._clear.width() - scaled(10), scaled(10))
        self._clear.raise_()
        self._refresh_label()


# ─────────────────────────────────────────────────────────────────────────────
# Progression et journal
# ─────────────────────────────────────────────────────────────────────────────
class ProgressPanel(QWidget):
    """Barre de progression + « n / total (x %) » + libellé courant + statut."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProgressStrip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.bar = LiquidProgressBar()
        self.bar.setRange(0, 100)
        self.bar.hide()                              # au repos, une piste vide ressemble à une barre de défilement
        self.count_label = QLabel()
        self.count_label.setProperty("role", "muted")
        top.addWidget(self.bar, 1)
        top.addWidget(self.count_label)
        lay.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.file_label = QLabel()
        self.file_label.setTextFormat(Qt.TextFormat.PlainText)
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
            self.bar.hide()
            pct = 0
            self.hide()
        else:
            done = max(0, min(done, total))
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.bar.show()
            self.show()
            pct = round(100 * done / total)
        self.count_label.setText(f"{done} / {total} ({pct} %)")
        self.file_label.setText(label)
        self.file_label.setToolTip(plain_tooltip(label))

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def reset(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.hide()
        self.count_label.setText("0 / 0 (0 %)")
        self.file_label.setText("")
        self.file_label.setToolTip("")
        self.status_label.setText("")
        self.hide()


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
    """Modèle de lignes (dict {clé de colonne: valeur}) ; la clé spéciale « _tag » colore la ligne,
    et « _sort_<clé> » fournit la valeur brute utilisée pour trier une colonne affichée en texte."""

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
        if role == Qt.ItemDataRole.DisplayRole or (
                role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal):
            if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._columns):
                # Le titre complet reste lisible en infobulle quand la colonne est
                # resserrée et que son en-tête est ellipsé.
                return self._columns[section].title
            if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Vertical:
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
            raw = row.get(f"_sort_{col.key}", value)
            if raw is None:
                return ""
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return raw
            return str(raw)
        if role == Qt.ItemDataRole.DecorationRole:   # icône de cellule : clé « _icon_<colonne> » = nom d'icône
            name = row.get(f"_icon_{col.key}")
            return icons.icon(str(name), "text", 16) if name else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN.get(col.align, _ALIGN["left"])
        if role == Qt.ItemDataRole.ForegroundRole:
            color = row.get("_fg")
            return QBrush(QColor(_themed(str(color)))) if color else None
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
        self._columns = list(columns)
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
        self.verticalHeader().setDefaultSectionSize(scaled(28))

        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        # Une colonne peut descendre jusqu'à 28 px : de quoi faire tenir toutes les
        # colonnes d'un tableau étroit sans barre de défilement horizontale. Cette
        # largeur et la hauteur des lignes suivent le zoom, sinon le texte de 150 %
        # restait tassé dans une grille de 100 %.
        header.setMinimumSectionSize(max(28, scaled(28)))
        header.setTextElideMode(Qt.TextElideMode.ElideRight)        # en-tête tronqué avec « … »
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # pas de tri initial
        # Toutes les colonnes sont pilotées par `_fit_columns` : le mode Stretch de Qt
        # ignorait les largeurs calculées et laissait la 1re colonne s'effondrer.
        for i, col in enumerate(columns):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(i, scaled(col.width))
        self.setSortingEnabled(True)

        self.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionRowsChanged.emit(self.selected_rows()))
        self.doubleClicked.connect(self._on_double_clicked)
        ThemeManager.instance().changed.connect(self._on_theme_changed)

        self._empty_text = QCoreApplication.translate("Widgets", "Aucun élément")
        self._empty_label = QLabel(self.viewport())
        self._empty_label.setObjectName("EmptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._empty_label.setText(self._empty_text)
        self._empty_label.hide()
        for sig in (self.data_model.modelReset, self.data_model.rowsInserted,
                    self.data_model.rowsRemoved, self._proxy.layoutChanged):
            sig.connect(self._sync_empty_state)
        self._fit_columns()

    # ── API ──────────────────────────────────────────────────────────────────
    def selected_rows(self) -> list[int]:
        """Indices (modèle source, croissants) des lignes sélectionnées, quel que soit le tri."""
        sel = self.selectionModel()
        if sel is None:
            return []
        return sorted({self._proxy.mapToSource(i).row() for i in sel.selectedRows()})

    def set_filter_text(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        self._sync_empty_state()

    def set_empty_text(self, text: str) -> None:
        """Texte affiché au centre quand le tableau n'a aucune ligne."""
        self._empty_text = text or ""
        self._empty_label.setText(self._empty_text)
        self._sync_empty_state()

    # ── Mise en page ──────────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "_empty_label"):       # événement reçu pendant la construction
            return
        self._fit_columns()
        self._sync_empty_state()

    def _sync_empty_state(self, *_args: object) -> None:
        """Affiche le texte d'attente tant que la vue est vide (filtre compris)."""
        empty = self._proxy.rowCount() == 0
        self._empty_label.setVisible(empty and bool(self._empty_text))
        if empty:
            self._empty_label.setGeometry(self.viewport().rect())
            self._empty_label.raise_()

    def _fit_columns(self) -> None:
        """Répartit la largeur disponible sans jamais réduire les en-têtes à « … ».

        Les largeurs déclarées servent de proportions ; la colonne marquée `stretch`
        absorbe le surplus. Quand la largeur manque, les colonnes se resserrent tant que
        leurs en-têtes entiers tiennent ; au-delà, elles gardent une largeur lisible et
        une barre horizontale apparaît dans le tableau.
        """
        model = self.model()
        if model is None or model.columnCount() == 0:
            return
        available = self.viewport().width()
        if available <= 0:
            return
        header = self.horizontalHeader()
        minimum = max(1, header.minimumSectionSize())
        count = model.columnCount()
        # Les largeurs déclarées sont des largeurs de référence à 100 % : elles suivent
        # le zoom comme le reste de la grille. Les en-têtes, eux, ont aussi besoin de
        # leur padding horizontal ($p8 de chaque côté) pour ne pas finir en « Réus… ».
        bases = [max(minimum, scaled(self._columns[i].width)) for i in range(count)]
        metrics = header.fontMetrics()
        # QHeaderView ne reprend pas toujours la police QSS agrandie dans son
        # fontMetrics : on applique explicitement le zoom au texte mesuré.
        text_scale = ThemeManager.instance().zoom()
        floors = []
        for i in range(count):
            text = self._proxy.headerData(i, Qt.Orientation.Horizontal,
                                          Qt.ItemDataRole.DisplayRole)
            needed = math.ceil(metrics.horizontalAdvance(str(text)) * text_scale) if text else 0
            floors.append(max(minimum, needed + 2 * scaled(8) + scaled(2)))
        preferred = [max(base, floor) for base, floor in zip(bases, floors, strict=False)]
        total = sum(preferred)
        if total <= available:
            widths = preferred
            target = next((i for i in range(count - 1, -1, -1) if self._columns[i].stretch), count - 1)
            widths[target] += available - total
        else:
            # Rétrécissement : chaque colonne garde d'abord de quoi lire son en-tête,
            # le reste est réparti au prorata des largeurs déclarées. Si les en-têtes
            # ne tiennent plus, un défilement horizontal apparaît plutôt que de les
            # couper au milieu d'un mot.
            extra = available - sum(floors)
            if extra > 0:
                widths = [floor + int(extra * base / total)
                          for floor, base in zip(floors, bases, strict=False)]
                widths[-1] += available - sum(widths)
            else:
                # Même les en-têtes entiers ne tiennent pas : ils gardent leur largeur
                # lisible et la vue ajoute une barre horizontale. Les réduire encore
                # transformait chaque titre en « … » (Tags et Rename à 150 %).
                widths = floors
        for i, width in enumerate(widths):
            if self.columnWidth(i) != width:
                self.setColumnWidth(i, width)

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
        self.verticalHeader().setDefaultSectionSize(scaled(28))
        self.horizontalHeader().setMinimumSectionSize(max(28, scaled(28)))
        self._fit_columns()
        self.viewport().update()


# ─────────────────────────────────────────────────────────────────────────────
# Aperçu d'image / de vidéo
# ─────────────────────────────────────────────────────────────────────────────
VIDEO_EXT = frozenset({".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mxf", ".ts", ".mts",
                       ".m2ts", ".vob", ".mpg", ".mpeg", ".m2v", ".3gp", ".3g2", ".f4v", ".asf",
                       ".ogv", ".webm", ".dv", ".qt"})
AUDIO_EXT = frozenset({".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus",
                       ".wma", ".mka", ".aiff", ".aif", ".aifc", ".amr", ".ac3", ".mp2", ".wv",
                       ".caf", ".au", ".spx"})
TEXT_EXT = frozenset({".json", ".geojson", ".txt", ".md", ".csv", ".tsv", ".xml", ".html", ".htm", ".yml",
                      ".yaml", ".ini", ".log", ".srt", ".vtt", ".nfo", ".xmp"})
# Types qu'on accepte de lancer ; tout le reste (exécutable, script, type inconnu) n'est jamais lancé.
OPENABLE_EXTS = VIDEO_EXT | AUDIO_EXT | TEXT_EXT | {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".raw", ".cr2", ".nef", ".arw", ".dng",
}
TEXT_PREVIEW_MARK = "\x00text\x00"          # préfixe : l'aperçu est du texte brut (police fixe, aligné à gauche)
TEXT_PREVIEW_BYTES = 16384
TEXT_PREVIEW_LINES = 24
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
    """Active HEIC/HEIF si pillow-heif est installé (une seule fois)."""
    global _PLUGINS_DONE
    if _PLUGINS_DONE:
        return
    _PLUGINS_DONE = True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:                                  # module absent ou inutilisable : on s'en passe
        log.debug("pillow-heif indisponible : HEIC/HEIF seulement si Pillow les gère nativement")
    try:
        import pillow_jxl  # noqa: F401  (greffon facultatif : JPEG XL)
    except Exception:
        log.debug("pillow-jxl indisponible : JPEG XL décodé par ffmpeg à défaut")


def _load_image_file(path: Path, size: int) -> QImage | None:
    """Image → miniature QImage via Pillow (orientation EXIF respectée) ; None si illisible."""
    try:
        from PIL import Image, ImageOps
        _register_pillow_plugins()
        with Image.open(path) as im:
            try:
                im.draft("RGB", (size * 2, size * 2))   # JPEG : décodage réduit
            except Exception as exc:                     # toute erreur Pillow : décodage complet
                log.debug("draft() ignoré pour cet aperçu : %s", exc)
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            rgba = im.convert("RGBA")
        w, h = rgba.size
        data = rgba.tobytes("raw", "RGBA")
        return QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    except Exception as exc:
        log.debug("Aperçu image impossible (%s) : %s", path, exc)
        return None


_PREVIEW_POLL = 0.1                                  # granularité d'annulation
_PREVIEW_KILL_GRACE = 1.0                            # délai après SIGTERM avant SIGKILL
_PREVIEW_TASKKILL_TIMEOUT = 5.0                      # délai laissé à taskkill sous Windows


def _kill_preview_tree(proc: subprocess.Popen) -> None:
    """Arrête l'outil d'aperçu et son sous-arbre : un wrapper a pu lancer le vrai binaire."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], timeout=_PREVIEW_TASKKILL_TIMEOUT,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("Aperçu : taskkill inopérant (%s)", exc)
        try:
            proc.wait(timeout=_PREVIEW_KILL_GRACE)
            return
        except subprocess.TimeoutExpired:
            log.debug("Aperçu : l'outil ne répond pas à taskkill")
        try:
            proc.kill()
        except OSError as exc:
            log.debug("Aperçu : arrêt de l'outil impossible (%s)", exc)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError as exc:
        log.debug("Aperçu : SIGTERM non transmis au groupe (%s)", exc)
    else:
        try:
            proc.wait(timeout=_PREVIEW_KILL_GRACE)
            return
        except subprocess.TimeoutExpired:
            log.debug("Aperçu : l'outil ignore SIGTERM")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError as exc:
            log.warning("Aperçu : l'outil survit à SIGKILL (%s)", exc)


def _run_preview(cmd: list[str], *, timeout: float = 20.0,
                 is_current: Callable[[], bool] = lambda: True) -> bool:
    """Lance un outil d'aperçu sans capturer sa sortie et l'arrête dès que l'aperçu n'est plus courant.

    Les images sont écrites dans un fichier temporaire : stdout/stderr ne servent à rien et sont jetés, ce
    qui borne la mémoire (SUBPROC-003). True si l'outil a pu être lancé.
    """
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **kwargs)
    except OSError as exc:
        log.debug("Aperçu : lancement impossible (%s)", exc)
        return False
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if not is_current():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_PREVIEW_POLL, remaining))
    finally:
        if proc.poll() is None:
            _kill_preview_tree(proc)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                log.debug("Aperçu : l'outil s'arrête lentement après arrêt forcé")
    return True


def _load_ffmpeg_still(path: Path, size: int, label: str,
                       is_current: Callable[[], bool] = lambda: True) -> tuple[QImage | None, str]:
    """Décode une image avec ffmpeg quand Pillow/Qt n'ont pas le décodeur (AVIF, JPEG XL, RAW exotiques…)."""
    info = binaries.resolve("ffmpeg")
    if not info.available:
        return None, QCoreApplication.translate("Widgets", "{label}\n{name}\n(ffmpeg requis pour l'aperçu)").format(label=label, name=path.name)
    tmp = user_cache_dir() / f"preview_{uuid.uuid4().hex}.png"
    image = QImage()
    scratch: Path | None = None
    try:
        try:
            source, scratch = neutral_input(path)         # nom contenant « % » : motif de séquence pour FFmpeg
        except OSError as exc:
            log.debug("Aperçu : lecture impossible (%s) : %s", path, exc)
            return None, f"{_unavailable()}\n{path.name}"
        cmd = [str(info.path), "-y", "-loglevel", "error", *SAFE_INPUT_FLAGS, "-i", input_spec(source),
               "-frames:v", "1", "-vf",
               f"scale={size}:{size}:force_original_aspect_ratio=decrease", str(tmp)]
        _run_preview(cmd, is_current=is_current)
        if tmp.exists() and tmp.stat().st_size > 0:
            image = QImage(str(tmp))
    finally:
        drop_neutral_input(scratch)
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Fichier temporaire non supprimé (%s) : %s", tmp, exc)
    if image.isNull():
        return None, f"{_unavailable()}\n{path.name}"
    return image, ""


def _load_avif_frame(path: Path, size: int) -> tuple[QImage | None, str]:
    """Conservé pour compatibilité : AVIF via ffmpeg."""
    return _load_ffmpeg_still(path, size, "AVIF")


def _load_qt_image(path: Path, size: int) -> QImage | None:
    """Dernier recours : les lecteurs d'image de Qt (selon les plugins installés)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return None
    return image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _render_pdf_qtpdf(path: Path, size: int) -> QImage | None:
    """Première page via QtPdf, quand la roue PySide6 complète est installée."""
    try:
        from PySide6.QtCore import QSize
        from PySide6.QtPdf import QPdfDocument
    except Exception:                                  # PySide6-Essentials : pas de QtPdf
        return None
    doc = QPdfDocument()
    try:
        if doc.load(str(path)) != QPdfDocument.Error.None_ or doc.pageCount() < 1:
            return None
        page = doc.pagePointSize(0)
        ratio = min(size / max(page.width(), 1.0), size / max(page.height(), 1.0))
        target = QSize(max(1, round(page.width() * ratio)), max(1, round(page.height() * ratio)))
        image = doc.render(0, target)
        return None if image.isNull() else image
    except Exception as exc:
        log.debug("Aperçu PDF (QtPdf) impossible (%s) : %s", path, exc)
        return None
    finally:
        doc.close()


def _render_pdf_external(path: Path, size: int,
                         is_current: Callable[[], bool] = lambda: True) -> QImage | None:
    """Première page via un outil système : QtPdf n'est pas livré avec PySide6-Essentials.

    Poppler puis Ghostscript puis ImageMagick, dans cet ordre ; le premier outil disponible
    qui produit une image gagne. Aucun n'est requis pour le reste de l'application.
    """
    tmp_png = user_cache_dir() / f"preview_{uuid.uuid4().hex}.png"
    prefix = tmp_png.with_suffix("")
    commands: tuple[tuple[str, list[str]], ...] = (
        ("pdftoppm", ["-f", "1", "-l", "1", "-scale-to", str(size), "-png", "-singlefile",
                      str(path), str(prefix)]),
        ("pdftocairo", ["-f", "1", "-l", "1", "-scale-to", str(size), "-png", "-singlefile",
                        str(path), str(prefix)]),
        ("gs", ["-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", "-dFirstPage=1",
                "-dLastPage=1", "-r96", f"-sOutputFile={tmp_png}", str(path)]),
        ("magick", ["-density", "96", f"{path}[0]", "-background", "white", "-flatten", str(tmp_png)]),
    )
    try:
        for name, args in commands:
            if not is_current():
                return None
            exe = shutil.which(name)
            if exe is None:
                continue
            if not _run_preview([exe, *args], is_current=is_current):
                continue
            if tmp_png.exists() and tmp_png.stat().st_size > 0:
                image = QImage(str(tmp_png))
                if not image.isNull():
                    return image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
    finally:
        for leftover in (tmp_png, prefix.with_suffix(".png")):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as exc:
                log.debug("Image temporaire non supprimée (%s) : %s", leftover, exc)
    return None


def _load_pdf_page(path: Path, size: int,
                   is_current: Callable[[], bool] = lambda: True) -> tuple[QImage | None, str]:
    """Première page d'un PDF : QtPdf si présent, sinon un moteur système."""
    image = _render_pdf_qtpdf(path, size)
    if image is None or image.isNull():
        image = _render_pdf_external(path, size, is_current)
    if image is None or image.isNull():
        return None, QCoreApplication.translate("Widgets", "PDF\n{name}\n(aperçu indisponible : installez poppler ou Ghostscript)").format(
            name=path.name)
    flat = QImage(image.size(), QImage.Format.Format_ARGB32)
    flat.fill(Qt.GlobalColor.white)                      # les PDF sont transparents : fond blanc
    painter = QPainter(flat)
    painter.drawImage(0, 0, image)
    painter.end()
    return flat, ""


def _load_audio_cover(path: Path, size: int,
                      is_current: Callable[[], bool] = lambda: True) -> tuple[QImage | None, str]:
    """Pochette intégrée d'un fichier audio (ffmpeg) ; sans pochette, repli sur le nom."""
    if binaries.resolve("ffmpeg").available:
        image, _ = _load_ffmpeg_still(path, size, "Audio", is_current)
        if image is not None and not image.isNull():
            return image, ""
    return None, QCoreApplication.translate("Widgets", "Audio\n{name}").format(name=path.name)


def _load_text_snippet(path: Path) -> tuple[QImage | None, str]:
    """Début d'un fichier texte (JSON mis en forme) ; le préfixe demande un affichage monospace aligné à gauche."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(TEXT_PREVIEW_BYTES + 1)
    except OSError:
        return None, f"{_unavailable()}\n{path.name}"
    truncated = len(raw) > TEXT_PREVIEW_BYTES
    text = raw[:TEXT_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if path.suffix.lower() in (".json", ".geojson") and not truncated:
        try:
            import json
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError as exc:                      # JSON invalide : on montre le texte tel quel
            log.debug("JSON d'aperçu invalide, texte brut conservé : %s", exc)
    lines = text.splitlines()
    shown = lines[:TEXT_PREVIEW_LINES]
    shown = [ln if len(ln) <= 90 else ln[:89] + "…" for ln in shown]
    if truncated or len(lines) > len(shown):
        shown.append("…")
    return None, TEXT_PREVIEW_MARK + "\n".join(shown)


def _load_video_frame(path: Path, size: int, is_current: Callable[[], bool]) -> tuple[QImage | None, str]:
    """Une image extraite avec ffmpeg (exception assumée à la règle « pas de subprocess direct »)."""
    info = binaries.resolve("ffmpeg")
    if not info.available:
        return None, QCoreApplication.translate("Widgets", "Vidéo\n{name}\n(ffmpeg requis pour l'aperçu)").format(name=path.name)
    tmp = user_cache_dir() / f"preview_{uuid.uuid4().hex}.png"
    try:
        for seek in (["-ss", "1"], []):               # 1 s ; sinon première image (vidéo très courte)
            if not is_current():
                return None, ""
            cmd = [str(info.path), "-y", "-loglevel", "error", *SAFE_INPUT_FLAGS, *seek,
                   "-i", input_spec(path), "-frames:v", "1", "-vf",
                   f"scale={size}:{size}:force_original_aspect_ratio=decrease", str(tmp)]
            if not _run_preview(cmd, is_current=is_current):
                break
            if not is_current():
                return None, ""
            if tmp.exists() and tmp.stat().st_size > 0:
                break
        image = QImage(str(tmp)) if tmp.exists() else QImage()
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Fichier temporaire non supprimé (%s) : %s", tmp, exc)
    if image.isNull():
        return None, QCoreApplication.translate("Widgets", "Vidéo\n{name}\n({reason})").format(name=path.name, reason=_unavailable().lower())
    return image, ""


def load_preview(path: Path, size: int,
                 is_current: Callable[[], bool] = lambda: True) -> tuple[QImage | None, str]:
    """Charge l'aperçu d'un fichier : (image, texte de repli). Sans Qt GUI : utilisable hors du thread principal."""
    path = Path(path)
    ext = path.suffix.lower()
    if not path.is_file():
        return None, _unavailable()
    if ext in VIDEO_EXT:
        return _load_video_frame(path, size, is_current)
    if ext in AUDIO_EXT:
        return _load_audio_cover(path, size, is_current)
    if ext == ".pdf":
        return _load_pdf_page(path, size, is_current)
    if ext in TEXT_EXT:
        return _load_text_snippet(path)
    image = _load_image_file(path, size)
    if image is None or image.isNull():
        image = _load_qt_image(path, size)
    if image is None or image.isNull():
        if not is_current():
            return None, ""
        # Pillow et Qt n'ont pas su : ffmpeg décode la plupart des formats (AVIF, JPEG XL, TIFF exotiques…)
        return _load_ffmpeg_still(path, size, ext.lstrip(".").upper() or QCoreApplication.translate("Widgets", "Image"), is_current)
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
        except RuntimeError as exc:                    # widget détruit entre-temps
            log.debug("Émission d'aperçu ignorée : %s", exc)


def _clock(ms: int) -> str:
    """Durée en mm:ss (heures au-delà de 60 minutes)."""
    seconds = max(0, int(ms)) // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


class SeekSlider(QSlider):
    """Curseur de position : un clic n'importe où sur la piste place la lecture à cet endroit.

    QSlider ne réagit par défaut qu'aux flèches et aux pages ; ici, la position du clic est
    convertie en temps, puis le glissement continue normalement.
    """

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            self.setSliderDown(True)                 # émet sliderPressed → la lecture se fige
            self.setValue(self._value_at(event.position().x()))
            self.sliderMoved.emit(self.value())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.isSliderDown() and self.maximum() > self.minimum():
            self.setValue(self._value_at(event.position().x()))
            self.sliderMoved.emit(self.value())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            self.setSliderDown(False)                # émet sliderReleased → la lecture reprend ici
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _value_at(self, x: float) -> int:
        """Temps correspondant à une abscisse, borné aux extrémités de la piste."""
        span = self.maximum() - self.minimum()
        ratio = min(1.0, max(0.0, x / max(1, self.width())))
        return self.minimum() + round(span * ratio)


_SeekSlider = SeekSlider                                   # ancien nom, conservé pour les importations existantes


class _AudioBus(QObject):
    """Volume et sourdine communs à tous les lecteurs : les deux aperçus d'une comparaison restent d'accord."""

    changed = Signal()
    _instance: "_AudioBus | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.level = 100                                  # volume maximal par défaut
        self.muted = False

    @classmethod
    def instance(cls) -> "_AudioBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_level(self, level: int) -> None:
        level = max(0, min(100, int(level)))
        if level != self.level:
            self.level = level
            self.changed.emit()

    def set_muted(self, muted: bool) -> None:
        if bool(muted) != self.muted:
            self.muted = bool(muted)
            self.changed.emit()

    @property
    def gain(self) -> float:
        """Gain linéaire envoyé à Qt : la courbe carrée suit mieux l'oreille qu'un curseur linéaire."""
        return (self.level / 100.0) ** 2


class VideoPreview(QWidget):
    """Lecteur audio/vidéo dans la même case carrée que l'aperçu d'une photo.

    L'image est letterboxée dans le cadre ; les commandes sont posées en bas, en surimpression,
    si bien que les deux aperçus occupent exactement le même rectangle — aucun décalage quand on
    passe d'une photo à un média. Les fichiers audio partagent le même lecteur et la même
    timeline (QtMultimedia gère les deux) ; sans ce module, le widget affiche une explication
    au lieu de faire échouer la fenêtre entière.

    `volume_controls=True` ajoute un rail vertical « sourdine + volume » à droite de la case
    (Dedup, Tags, Rename) ; la valeur par défaut garde un lecteur sans rail. Volume et sourdine
    sont communs à tous les lecteurs.
    """

    BAR = 36                                          # hauteur des commandes en surimpression
    VOLUME_WIDTH = 22                                 # largeur du rail de volume, à droite de la case
    VOLUME_MAX_HEIGHT = 128                           # le curseur reste compact, même sur un grand aperçu
    ROW_SPACING = 3
    playbackStarted = Signal()                        # la lecture démarre : l'autre lecteur doit se mettre en pause

    def __init__(self, size: int = 260, parent: QWidget | None = None, *, volume_controls: bool = False) -> None:
        super().__init__(parent)
        self._size = size
        self._dragging = False
        self._source: Path | None = None
        self._is_audio = False
        self._got_frame = False
        self._failed = False
        self._cover_token = 0
        self._fallback_token = 0
        self._player = self._audio = self._sink = None
        # Image fixe de repli quand Qt ne décode aucune image (AV1 notamment).
        self._poster_signals = _PreviewSignals(self)
        self._poster_signals.done.connect(self._on_poster_loaded)
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._check_video_frames)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.ROW_SPACING)
        # La case reste centrée dans la colonne, y compris avec le rail de volume : une marge
        # gauche de la largeur du rail compense exactement le rail placé à droite.
        lay.addStretch(1)
        if volume_controls:
            lay.addSpacing(self.VOLUME_WIDTH + self.ROW_SPACING)
        self._box = QFrame(self)
        self._box.setObjectName("VideoPreview")
        self._box.setFixedSize(size, size)
        lay.addWidget(self._box, 0, Qt.AlignmentFlag.AlignTop)
        self.view = QLabel(self._box)
        self.view.setTextFormat(Qt.TextFormat.PlainText)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setWordWrap(True)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Commandes en surimpression : l'image garde toute la case et rien ne se décale.
        self._bar = QWidget(self._box)
        self._bar.setObjectName("VideoBar")
        bar = QHBoxLayout(self._bar)
        bar.setContentsMargins(4, 2, 6, 2)
        bar.setSpacing(6)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(scaled(32))
        self.play_btn.setToolTip(self.tr("Lire ou mettre en pause"))
        self.slider = SeekSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setToolTip(self.tr("Position dans le média"))
        self.time_label = QLabel("0:00 / 0:00")
        bar.addWidget(self.play_btn)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        self._bar.hide()

        # Rail de volume (facultatif), à droite de la case : la case garde sa taille exacte.
        self._vol_panel: QWidget | None = None
        self.mute_btn: QPushButton | None = None
        self.volume_slider: QSlider | None = None
        if volume_controls:
            self._vol_panel = QWidget(self)
            self._vol_panel.setFixedSize(self.VOLUME_WIDTH, size)
            # Même caché, le rail réserve sa place : passer d'une photo à un média ne décale rien.
            policy = self._vol_panel.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self._vol_panel.setSizePolicy(policy)
            rail = QVBoxLayout(self._vol_panel)
            rail.setContentsMargins(0, 0, 0, 0)
            rail.setSpacing(4)
            self.mute_btn = QPushButton(self._vol_panel)
            self.mute_btn.setCheckable(True)
            self.mute_btn.setFixedSize(self.VOLUME_WIDTH, self.VOLUME_WIDTH)
            self.mute_btn.setIconSize(QSize(16, 16))
            self.mute_btn.setToolTip(self.tr("Couper ou rétablir le son"))
            self.volume_slider = QSlider(Qt.Orientation.Vertical, self._vol_panel)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setToolTip(self.tr("Volume"))
            self.volume_slider.setFixedWidth(self.VOLUME_WIDTH - 2)
            available = max(24.0, size - self.VOLUME_WIDTH - 4.0)
            volume_height = max(24.0, min(float(scaled(self.VOLUME_MAX_HEIGHT)), size * 0.52,
                                          available))
            self.volume_slider.setFixedHeight(round(volume_height))
            self.volume_slider.setValue(_AudioBus.instance().level)
            self.volume_slider.setAccessibleName(self.tr("Volume"))
            rail.addWidget(self.mute_btn, 0, Qt.AlignmentFlag.AlignHCenter)
            rail.addWidget(self.volume_slider, 0, Qt.AlignmentFlag.AlignHCenter)
            rail.addStretch(1)
            lay.addWidget(self._vol_panel, 0, Qt.AlignmentFlag.AlignTop)
            self.volume_slider.valueChanged.connect(self._sync_volume_icon)
            self.mute_btn.toggled.connect(self._sync_volume_icon)
            self._sync_volume_icon()
            self._vol_panel.hide()
        lay.addStretch(1)

        # Petit bandeau d'explication posé sur l'image de repli (codec non décodé par Qt).
        self._notice = QLabel(self._box)
        self._notice.setObjectName("VideoNotice")
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._notice.setWordWrap(True)
        self._notice.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._notice.hide()

        if MULTIMEDIA_AVAILABLE:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._sink = QVideoSink(self)
            self._player.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._on_frame)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.errorOccurred.connect(self._on_error)
            self.play_btn.clicked.connect(self.toggle)
            self.slider.sliderPressed.connect(self._drag_start)
            self.slider.sliderReleased.connect(self._drag_end)
            self.slider.sliderMoved.connect(self._seek)
            self._cover_signals = _PreviewSignals(self)
            self._cover_signals.done.connect(self._on_cover)
            bus = _AudioBus.instance()
            bus.changed.connect(self._sync_audio)
            if self.mute_btn is not None and self.volume_slider is not None:
                self.volume_slider.valueChanged.connect(bus.set_level)
                self.mute_btn.toggled.connect(bus.set_muted)
            self._sync_audio()
        else:
            self.play_btn.setEnabled(False)
            self.slider.setEnabled(False)
            self.view.setText(self.tr("Lecture vidéo indisponible\n(installez PySide6-Addons)"))

        self._style()
        # Méthode liée : la connexion tombe avec le widget, sans garder un lambda en vie.
        ThemeManager.instance().changed.connect(self._style)
        self._place()

    # ── API ──────────────────────────────────────────────────────────────────
    def source(self) -> Path | None:
        return self._source

    def has_failed(self) -> bool:
        """Vrai si la lecture du fichier courant a échoué (codec absent, fichier illisible…)."""
        return self._failed

    def is_playing(self) -> bool:
        return self._player is not None and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def pause(self) -> None:
        if self._player is not None and self.is_playing():
            self._player.pause()

    def set_source(self, path: Path | str | None) -> None:
        """Charge le média et affiche sa première image (ou son nom), sans lancer la lecture."""
        if self._player is None:
            return
        if not path:
            self.stop()
            return
        self._source = Path(path)
        self._is_audio = self._source.suffix.lower() in AUDIO_EXT
        self._failed = False                              # une erreur précédente ne doit pas bloquer le fichier suivant
        self._fallback_token += 1
        self._cover_token += 1
        self._got_frame = False
        self._frame_timer.stop()
        self._notice.hide()
        self.play_btn.setEnabled(True)
        self.view.setPixmap(QPixmap())
        self.view.clear()
        # Un fichier audio n'a pas d'image : son nom occupe la case jusqu'à la pochette éventuelle.
        self.view.setText(f"♪\n{self._source.name}" if self._is_audio else self.tr("Chargement…"))
        self.slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self._player.setSource(QUrl.fromLocalFile(str(self._source)))
        self._player.pause()
        if self._is_audio:
            token = self._cover_token                     # pochette intégrée, chargée sans bloquer l'interface
            _pool().start(_PreviewTask(token, self._source, self._size, self._cover_signals,
                                       lambda t=token: self._cover_token == t))
        else:
            # Laisse le temps à Qt de produire une première image ; sinon on passe au repli ffmpeg.
            self._frame_timer.start(2000)
        self._bar.show()
        self._bar.raise_()
        if self._vol_panel is not None:
            self._vol_panel.show()

    def toggle(self) -> None:
        if self._player is None or self._source is None or self._failed:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            if self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self._player.setPosition(0)
            self._player.play()

    def stop(self) -> None:
        """Arrête la lecture, coupe le son et vide l'image (le fichier change)."""
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self._source = None
        self._is_audio = False
        self._failed = False
        self._got_frame = False
        self._fallback_token += 1
        self._cover_token += 1                            # une pochette en cours de chargement devient périmée
        self._frame_timer.stop()
        self._notice.hide()
        self.view.setPixmap(QPixmap())
        self.view.clear()
        self._bar.hide()
        if self._vol_panel is not None:
            self._vol_panel.hide()
        self.play_btn.setEnabled(self._player is not None)   # sans QtMultimedia, la lecture reste indisponible
        self.play_btn.setText("▶")
        self.slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")

    def hideEvent(self, event) -> None:  # noqa: N802
        """Quitter l'onglet ou l'aperçu coupe le son : pas de lecture invisible."""
        if self._player is not None:
            self._player.pause()
        super().hideEvent(event)

    # ── Interne ──────────────────────────────────────────────────────────────
    def _style(self, _name: str = "") -> None:
        self._box.setStyleSheet(
            f"QFrame#VideoPreview {{ background-color: {token('surface_alt')}; "
            f"border: 1px solid {token('border')}; border-radius: 10px; }}")
        self.view.setStyleSheet(f"color: {token('muted')};")
        self._notice.setStyleSheet(
            f"QLabel#VideoNotice {{ background-color: rgba(0, 0, 0, 160); color: #ffffff; "
            f"border-radius: 6px; padding: 4px 6px; font-size: {caption_point_size():g}pt; }}")
        self._bar.setStyleSheet(
            "QWidget#VideoBar { background-color: rgba(0, 0, 0, 150); "
            "border-top-left-radius: 3px; border-top-right-radius: 3px; "
            "border-bottom-left-radius: 9px; border-bottom-right-radius: 9px; }"
            "QWidget#VideoBar QLabel { color: #ffffff; }"
            "QWidget#VideoBar QPushButton { color: #ffffff; background: transparent; border: none; "
            f"padding: 0; min-width: 32px; max-width: 32px; font-size: {caption_point_size():g}pt; }}"
            "QWidget#VideoBar QPushButton:hover { background: rgba(255, 255, 255, 50); border-radius: 4px; }")
        if self.mute_btn is not None:
            # Icône seule, sans cadre : le rail de volume reste discret à côté de l'image.
            self.mute_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 6px; padding: 0; }"
                f"QPushButton:hover {{ background-color: {token('secondary')}; }}")
        self._sync_volume_icon()

    def _place(self) -> None:
        """L'image remplit la case, les commandes se posent en bas."""
        rect = self._box.rect().adjusted(1, 1, -1, -1)
        self.view.setGeometry(rect)
        self._notice.setGeometry(rect.left() + 8, rect.top() + 8, max(0, rect.width() - 16), 56)
        self._bar.setGeometry(rect.left(), rect.bottom() + 1 - self.BAR, rect.width(), self.BAR)
        self._notice.raise_()
        self._bar.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()

    def _sync_audio(self) -> None:
        """Applique volume et sourdine communs ; met à jour la ligne de volume sans boucle de signaux."""
        bus = _AudioBus.instance()
        if self._audio is not None:
            self._audio.setVolume(bus.gain)
            self._audio.setMuted(bus.muted)
        if self.volume_slider is not None and self.mute_btn is not None:
            self.volume_slider.blockSignals(True)
            self.mute_btn.blockSignals(True)
            self.volume_slider.setValue(bus.level)
            self.mute_btn.setChecked(bus.muted)
            self.volume_slider.blockSignals(False)
            self.mute_btn.blockSignals(False)
            self._sync_volume_icon()

    def _volume_icon_name(self) -> str:
        """Icône du rail : haut-parleur, moitié ou barré selon le niveau et la sourdine."""
        if self.mute_btn is not None and self.mute_btn.isChecked():
            return "volume-cross"
        level = self.volume_slider.value() if self.volume_slider is not None else 100
        if level == 0:
            return "volume-cross"
        return "volume-half" if level < 50 else "volume"

    def _sync_volume_icon(self) -> None:
        if self.mute_btn is not None:
            self.mute_btn.setIcon(icons.icon(self._volume_icon_name(), "text", 16))

    def _on_cover(self, token_: int, image: object, _text: str) -> None:
        if token_ != self._cover_token or not self._is_audio or self._failed:
            return                                        # résultat périmé
        if isinstance(image, QImage) and not image.isNull():
            fitted = image.scaled(self.view.width(), self.view.height(),
                                  Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.view.setText("")
            self.view.setPixmap(QPixmap.fromImage(fitted))
        # sans pochette : le nom du fichier reste affiché

    def _on_frame(self, frame: object) -> None:
        if self._is_audio:
            return                                        # la pochette reste affichée
        image = frame.toImage()
        if image.isNull():
            return
        self._got_frame = True
        self._frame_timer.stop()
        self._notice.hide()
        # Même largeur que la vignette d'une photo : l'image remplit la case (letterbox 16:9),
        # sans marge interne qui la faisait paraître plus petite que la photo.
        scaled = image.scaled(self.view.width(), self.view.height(),
                              Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.view.setPixmap(QPixmap.fromImage(scaled))

    def _check_video_frames(self) -> None:
        """Aucune image reçue : Qt ne décode pas ce codec, on tente une image fixe ffmpeg."""
        if self._player is None or self._source is None or self._is_audio or self._got_frame:
            return
        # Même si Qt reste bloqué en « StalledMedia » (AV1), ffmpeg peut lire une image.
        # On retire tout de suite « Chargement… » : au pire l'extraction échoue et ce texte reste.
        self.view.setText(_unavailable())
        token = self._fallback_token
        task = _PreviewTask(token, self._source, self._size, self._poster_signals,
                            lambda: token == self._fallback_token and not self._got_frame)
        _pool().start(task)

    def _on_poster_loaded(self, token_: int, image: object, text: str) -> None:
        """Repli : image fixe ffmpeg + explication, au lieu d'un « Chargement… » sans fin."""
        if token_ != self._fallback_token or self._got_frame or self._source is None:
            return
        if isinstance(image, QImage) and not image.isNull():
            scaled = image.scaled(self.view.width(), self.view.height(),
                                  Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.view.setPixmap(QPixmap.fromImage(scaled))
            self._notice.setText(self.tr("Aperçu vidéo limité (codec non pris en charge)"))
            self._notice.show()
            self._notice.raise_()
            self._bar.raise_()
            return
        self.view.setText(text or _unavailable())

    def _on_position(self, ms: int) -> None:
        if not self._dragging:
            self.slider.setValue(ms)
        self.time_label.setText(f"{_clock(ms)} / {_clock(self.slider.maximum())}")

    def _on_duration(self, ms: int) -> None:
        self.slider.setRange(0, max(0, ms))
        self.time_label.setText(f"{_clock(self.slider.value())} / {_clock(ms)}")

    def _on_state(self, state: object) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("❚❚" if playing else "▶")
        self.play_btn.setToolTip(self.tr("Mettre en pause") if playing else self.tr("Lire"))
        if playing:
            self.playbackStarted.emit()

    def _on_error(self, _error: object, message: str) -> None:
        self._failed = True
        self.play_btn.setEnabled(False)
        self.view.setPixmap(QPixmap())
        self._frame_timer.stop()
        self._notice.hide()
        self.view.setText(self.tr("Lecture impossible\n{reason}").format(reason=message or ""))
        log.warning("Aperçu média (%s) : %s", self._source, message)

    def _drag_start(self) -> None:
        self._dragging = True

    def _drag_end(self) -> None:
        self._dragging = False
        if self._player is not None:
            self._player.setPosition(self.slider.value())

    def _seek(self, value: int) -> None:
        self.time_label.setText(f"{_clock(value)} / {_clock(self.slider.maximum())}")


class OpenFileButton(QPushButton):
    """Ouvre le média courant dans l'application associée du système.

    « Ouvrir dans une application » pour un fichier audio, vidéo, image ou document,
    « Afficher le dossier » pour un type qu'on ne lance pas (exécutable, script, inconnu) :
    ouvrir un .exe reviendrait à exécuter un programme choisi par le fichier lui-même.
    """

    failed = Signal(str)                                  # message à journaliser par la fenêtre

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._target: Path | None = None
        self.setToolTip(self.tr("Ouvre le fichier sélectionné dans l'application associée du système "
                                "(lecteur vidéo, visionneuse d'images, lecteur PDF…)."))
        self.clicked.connect(self._open)
        self.set_path(None)

    def path(self) -> Path | None:
        return self._path

    def set_path(self, path: Path | str | None) -> None:
        """Fichier à ouvrir ; `None` grise le bouton."""
        self._path = Path(path) if path else None
        self._target = self._resolve(self._path)
        self.setEnabled(self._target is not None)
        if self._target is not None and self._target != self._path:
            self.setText(self.tr("Afficher le dossier"))
        else:
            self.setText(self.tr("Ouvrir dans une application"))

    @staticmethod
    def _resolve(path: Path | None) -> Path | None:
        if path is None:
            return None
        if path.is_file() and path.suffix.lower() in OPENABLE_EXTS:
            return path
        return path.parent if path.parent.is_dir() else None

    def _open(self) -> None:
        if self._target is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._target))):
            self.failed.emit(self.tr("Impossible d'ouvrir : {target}").format(target=self._target))
            plain_message(self.parentWidget(), QMessageBox.Icon.Warning,
                          self.tr("Ouverture impossible"),
                          self.tr("Aucune application n'est associée à ce fichier :\n{name}").format(
                              name=self._target.name))


class ImagePreview(QWidget):
    """Aperçu fluide : chargement asynchrone, le précédent est invalidé, l'interface ne se fige jamais."""

    def __init__(self, size: int = 260, parent: QWidget | None = None, *, framed: bool = True) -> None:
        super().__init__(parent)
        self._size = size
        self._framed = framed
        self._token = 0
        self._signals = _PreviewSignals(self)
        self._signals.done.connect(self._on_loaded)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setFixedSize(size, size)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 0, Qt.AlignmentFlag.AlignCenter)
        self._apply_style()
        # Méthode liée : la connexion tombe avec le widget, sans garder un lambda en vie.
        ThemeManager.instance().changed.connect(self._apply_style)

    def _apply_style(self, _name: str = "") -> None:
        if not self._framed:
            # Sans cadre : l'aperçu se fond dans le panneau (Dedup) au lieu d'une plaque grise.
            self.label.setStyleSheet(
                f"QLabel {{ background: transparent; border: none; color: {token('muted')}; padding: 8px; }}")
            return
        self.label.setStyleSheet(
            f"QLabel {{ background-color: {token('surface_alt')}; color: {token('muted')}; "
            f"border: 1px solid {token('border')}; border-radius: 10px; padding: 8px; }}")

    # ── API ──────────────────────────────────────────────────────────────────
    def show_image(self, path: str | Path) -> None:
        self._token += 1
        tok = self._token
        p = Path(path)
        self._reset_label_style()
        self.label.setPixmap(QPixmap())
        if not p.is_file():
            self.label.setText(_unavailable())
            return
        self.label.setText(self.tr("Chargement…"))
        task = _PreviewTask(tok, p, self._size, self._signals, lambda t=tok: self._token == t)
        _pool().start(task)

    def show_text(self, text: str) -> None:
        self._token += 1                               # invalide tout chargement en cours
        self._reset_label_style()
        self.label.setPixmap(QPixmap())
        self.label.setText(text)

    def clear(self) -> None:
        self.show_text("")

    # ── Interne ──────────────────────────────────────────────────────────────
    def _on_loaded(self, token_: int, image: object, text: str) -> None:
        if token_ != self._token:
            return                                     # résultat périmé
        if isinstance(image, QImage) and not image.isNull():
            self.label.setText("")
            self.label.setPixmap(QPixmap.fromImage(image))
        elif text.startswith(TEXT_PREVIEW_MARK):
            self._show_snippet(text[len(TEXT_PREVIEW_MARK):])
        else:
            self._reset_label_style()
            self.label.setPixmap(QPixmap())
            self.label.setText(text or _unavailable())

    def _show_snippet(self, text: str) -> None:
        """Texte brut : police fixe, aligné en haut à gauche, sans retour à la ligne automatique."""
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        self.label.setFont(font)
        self.label.setWordWrap(False)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.label.setPixmap(QPixmap())
        self.label.setText(text)

    def _reset_label_style(self) -> None:
        self.label.setFont(QFont())
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)


PLAYABLE_EXT = VIDEO_EXT | AUDIO_EXT                   # ce que le lecteur intégré tente de lire


class MediaPreview(QWidget):
    """Aperçu d'un média : lecteur audio/vidéo (ou vignette) et ouverture externe.

    Le lecteur est celui de Tags, partagé par tous les outils : images et documents passent par
    la vignette, l'audio et la vidéo par la timeline. Le bouton « Ouvrir dans une application »
    est volontairement petit et posé juste sous la timeline, dans la zone de l'aperçu. Sans
    QtMultimedia (PySide6-Essentials seul), tout passe par la vignette.

    Demander le même fichier deux fois de suite ne relance pas le lecteur : Dedup rafraîchit son
    aperçu à chaque changement d'action (Garder / Supprimer) et la lecture en cours ne doit pas être
    coupée pour autant. `release()` arrête la lecture et lâche le fichier : à appeler avant de le
    déplacer ou le supprimer (Windows refuse de supprimer un fichier encore ouvert).
    """

    playbackStarted = Signal()                          # relayé du lecteur : l'autre aperçu se met en pause

    def __init__(self, size: int = 260, parent: QWidget | None = None, *, framed: bool = True,
                 volume_controls: bool = False) -> None:
        super().__init__(parent)
        self._current: Path | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.image = ImagePreview(size, framed=framed)
        self.video = VideoPreview(size, volume_controls=volume_controls)
        self._image = self.image
        self._player: VideoPreview | None = self.video if MULTIMEDIA_AVAILABLE else None
        self._stack = QStackedWidget()
        self._stack.addWidget(self.image)
        self._stack.addWidget(self.video)
        lay.addWidget(self._stack)
        self.video.playbackStarted.connect(self.playbackStarted)
        self.open_btn = OpenFileButton(self)
        self.open_btn.setMaximumWidth(200)             # petit : sous la timeline, pas un bouton d'action
        self.open_btn.setStyleSheet(
            f"QPushButton {{ font-size: {caption_point_size():g}pt; padding: 2px 8px; }}")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        lay.addLayout(row)

    # ── API ──────────────────────────────────────────────────────────────────
    @staticmethod
    def is_playable(path: Path | str) -> bool:
        return Path(path).suffix.lower() in PLAYABLE_EXT

    def current_path(self) -> Path | None:
        return self._current

    def can_play(self) -> bool:
        """Vrai si le fichier affiché est dans le lecteur intégré et qu'il n'a pas échoué."""
        return (self._player is not None and self._stack.currentWidget() is self.video
                and self.video.source() is not None and not self.video.has_failed())

    def is_playing(self) -> bool:
        return self._player is not None and self.video.is_playing()

    def set_path(self, path: Path | str | None) -> None:
        """Audio et vidéo → lecteur intégré ; image, document ou autre → vignette."""
        if path is None:
            self.clear()
            return
        self.show_file(path)

    def show_file(self, path: str | Path) -> None:
        p = Path(path)
        self.open_btn.set_path(p)
        if self._player is not None and self.is_playable(p) and p.is_file():
            if self._current != p or self.video.source() != p:
                self.video.set_source(p)
            self._current = p
            self.image.clear()
            self._stack.setCurrentWidget(self.video)
            return
        self.video.stop()
        self._current = p
        self._stack.setCurrentWidget(self.image)
        self.image.show_image(p)

    show_image = show_file                              # même point d'entrée que l'aperçu image

    def show_text(self, text: str) -> None:
        self.video.stop()
        self._current = None
        self._stack.setCurrentWidget(self.image)
        self.image.show_text(text)
        self.open_btn.set_path(None)

    def clear(self) -> None:
        self.show_text("")

    def toggle_playback(self) -> None:
        if self.can_play():
            self.video.toggle()

    def pause(self) -> None:
        if self._player is not None:
            self.video.pause()

    def release(self) -> None:
        """Arrête la lecture et lâche le fichier (avant de le déplacer, le supprimer ou fermer la fenêtre)."""
        self.video.stop()
        self._current = None
        self._stack.setCurrentWidget(self.image)
        self.image.clear()
        self.open_btn.set_path(None)
