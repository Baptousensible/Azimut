"""Fenêtre d'Azimut Dedup : dossiers A/B, options, arbre de groupes, comparaison, application."""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from azimut.core import binaries
from azimut.core.fsutil import format_size
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.modules.dedup.actions import (
    JOURNAL_META_NAME,
    MODE_DELETE,
    MODE_QUARANTINE,
    MODE_TRASH,
    ApplyOptions,
    ApplyReport,
    default_quarantine_parent,
    export_report,
    plan_deletions,
    trash_available,
)
from azimut.modules.dedup.engine import MEDIA_EXTS, ScanOptions, ScanResult
from azimut.modules.dedup.livephoto import DEFAULT_MAX_SECONDS
from azimut.modules.dedup.model import (
    ACTION_DELETE,
    ACTION_KEEP,
    KIND_EXACT,
    KIND_LABELS,
    KIND_LIVE,
    KIND_MODIFIED,
    KIND_SIMILAR,
    STRATEGY_LABELS,
    FileItem,
    Group,
    Strategy,
    keep_only,
    resolve_selection,
    set_actions,
    strategy_from_key,
    totals,
)
from azimut.modules.dedup.workers import ApplyWorker, ScanWorker, UndoWorker
from azimut.ui import icons
from azimut.ui.controls import CheckBox, SegmentedControl, set_action_available
from azimut.ui.file_dialog import choose_directory, choose_file, choose_save_file
from azimut.ui.flow_layout import FlowLayout
from azimut.ui.module_window import ModuleWindow
from azimut.ui.theme import ThemeManager, scaled, token
from azimut.ui.widgets import Card, MediaPreview, PathDropZone, PathField

# Extensions qu'un double-clic peut ouvrir sans risque ; tout le reste est seulement montré dans son dossier.
OPENABLE_EXTS = MEDIA_EXTS | {".pdf", ".txt"}

ROLE = Qt.ItemDataRole.UserRole
COLUMNS = (QCoreApplication.translate("DedupWindow", "Chemin"), QCoreApplication.translate("DedupWindow", "Côté"), QCoreApplication.translate("DedupWindow", "Taille"), QCoreApplication.translate("DedupWindow", "Date"), QCoreApplication.translate("DedupWindow", "Dimensions"), QCoreApplication.translate("DedupWindow", "Action"))
TOLERANCES = ((QCoreApplication.translate("DedupWindow", "Strict (1)"), 1), (QCoreApplication.translate("DedupWindow", "Équilibré (3)"), 3),
              (QCoreApplication.translate("DedupWindow", "Large (5)"), 5), (QCoreApplication.translate("DedupWindow", "Très large (7)"), 7))
FILTERS = (QCoreApplication.translate("DedupWindow", "Tous"), QCoreApplication.translate("DedupWindow", "Doublons exacts"), QCoreApplication.translate("DedupWindow", "Images similaires"),
           QCoreApplication.translate("DedupWindow", "Live Photos"), QCoreApplication.translate("DedupWindow", "Même chemin, contenu différent"),
           QCoreApplication.translate("DedupWindow", "Proposés à la suppression"))
FILTER_KINDS = {1: KIND_EXACT, 2: KIND_SIMILAR, 3: KIND_LIVE, 4: KIND_MODIFIED}
KIND_TEXT = {
    KIND_EXACT: QCoreApplication.translate("DedupWindow", "doublons exacts"),
    KIND_SIMILAR: QCoreApplication.translate("DedupWindow", "images similaires"),
    KIND_MODIFIED: QCoreApplication.translate("DedupWindow", "même chemin, contenu différent"),
    KIND_LIVE: QCoreApplication.translate("DedupWindow", "Live Photo (photo + vidéo)"),
}
ACTION_TEXT = {
    ACTION_KEEP: QCoreApplication.translate("DedupWindow", "Garder"),
    ACTION_DELETE: QCoreApplication.translate("DedupWindow", "Supprimer"),
}
STRATEGY_TEXT = {
    Strategy.A_FIRST: QCoreApplication.translate("DedupWindow", "A prioritaire"),
    Strategy.B_FIRST: QCoreApplication.translate("DedupWindow", "B prioritaire"),
    Strategy.OLDEST: QCoreApplication.translate("DedupWindow", "Le plus ancien"),
    Strategy.NEWEST: QCoreApplication.translate("DedupWindow", "Le plus récent"),
    Strategy.BEST_QUALITY: QCoreApplication.translate("DedupWindow", "Meilleure qualité (résolution, taille)"),
    Strategy.CLEANEST_NAME: QCoreApplication.translate("DedupWindow", "Nom le plus propre (sans « copie », « (1) »)"),
    Strategy.SHORTEST_PATH: QCoreApplication.translate("DedupWindow", "Chemin le plus court"),
}
LIVE_DURATIONS = ((QCoreApplication.translate("DedupWindow", "3 s (strict)"), 3.0), (QCoreApplication.translate("DedupWindow", "5 s (équilibré)"), DEFAULT_MAX_SECONDS),
                  (QCoreApplication.translate("DedupWindow", "10 s (large)"), 10.0))
MODE_LABELS = {
    MODE_QUARANTINE: QCoreApplication.translate("DedupWindow", "Quarantaine (réversible, par défaut)"),
    MODE_TRASH: QCoreApplication.translate("DedupWindow", "Corbeille système"),
    MODE_DELETE: "Suppression définitive",
}
HINT_DOUBLE = ("A est la référence et reste protégé par défaut : seuls les fichiers de B "
               "sont proposés à la suppression. Cliquez sur le cadenas de A pour changer cela.")


class _ResultsTree(QTreeView):
    """Arbre des groupes ; Espace et Suppr sont relayés par des signaux."""

    toggleRequested = Signal()
    deleteRequested = Signal()
    PATH_COLUMN = 0
    PATH_DEFAULT_WIDTH = 700

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Par défaut, Qt étire la dernière section (« Action », la plus étroite) quand la
        # fenêtre s'agrandit ; la zone Chemin, elle, restait figée à sa largeur initiale.
        self.header().setStretchLastSection(False)

    def fit_path_column(self) -> None:
        """Allonge Chemin sur la largeur restante, sans le réduire sous sa taille par défaut."""
        model = self.model()
        if model is None:
            return
        others = sum(self.columnWidth(col)
                     for col in range(self.PATH_COLUMN + 1, model.columnCount()))
        self.setColumnWidth(
            self.PATH_COLUMN,
            max(self.PATH_DEFAULT_WIDTH, self.viewport().width() - others))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self.fit_path_column()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.toggleRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _cell(text: str = "") -> QStandardItem:
    item = QStandardItem(text)
    item.setEditable(False)
    return item


def _same_dir(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _blend(a: QColor, b: QColor, amount: float) -> QColor:
    """Mélange opaque entre deux couleurs, pour les transitions du cadenas."""
    t = max(0.0, min(1.0, float(amount)))
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


class _ProtectOrb(QAbstractButton):
    """Cadenas rond logé dans le renflement d'une zone d'import.

    Il reprend l'API d'une case à cocher (``isChecked``, ``setChecked``, ``clicked``,
    ``toggled``) : les connexions et la sauvegarde des préférences restent inchangées.
    """

    DIAMETER = 48

    def __init__(self, side: str, label: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._side = side
        self._diameter = scaled(self.DIAMETER)
        self._hover = 0.0
        self._check = 0.0
        self.setCheckable(True)
        self.setFixedSize(self._diameter, self._diameter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setText(label)
        self.setToolTip(tooltip)
        self.setAccessibleName(label)
        self.setAccessibleDescription(tooltip)
        self.toggled.connect(self._animate_check)
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(160)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._set_hover)
        self._check_anim = QVariantAnimation(self)
        self._check_anim.setDuration(260)
        self._check_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._check_anim.valueChanged.connect(self._set_check)
        ThemeManager.instance().changed.connect(self._on_theme)
        ThemeManager.instance().accessibility_changed.connect(self.update)

    # ── Animations ──────────────────────────────────────────────────────────
    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def _set_hover(self, value: object) -> None:
        self._hover = max(0.0, min(1.0, float(value)))
        self.update()

    def _set_check(self, value: object) -> None:
        self._check = max(0.0, min(1.0, float(value)))
        self.update()

    def _animate(self, animation: QVariantAnimation, current: float, target: float) -> None:
        animation.stop()
        if not self.isVisible() or ThemeManager.instance().reduce_motion():
            animation.setStartValue(target)
            animation.setEndValue(target)
            if animation is self._hover_anim:
                self._set_hover(target)
            else:
                self._set_check(target)
            return
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.start()

    def _animate_check(self, checked: bool) -> None:
        self._animate(self._check_anim, self._check, 1.0 if checked else 0.0)

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().enterEvent(event)
        if self.isEnabled():
            self._animate(self._hover_anim, self._hover, 1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().leaveEvent(event)
        self._animate(self._hover_anim, self._hover, 0.0)

    def focusInEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().focusOutEvent(event)
        self.update()

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Peinture ────────────────────────────────────────────────────────────
    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return QSize(self._diameter, self._diameter)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        hover, check = self._hover, self._check
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = self._diameter / 2.0 - scaled(2)

        # Halo de survol : le renflement s'allume sans changer de taille.
        if hover > 0.01:
            halo = QColor(token("focus"))
            halo.setAlphaF(0.30 * hover)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, radius + scaled(3), radius + scaled(3))

        fill = _blend(QColor(token("surface_alt")), QColor(token("accent")), check)
        if hover > 0.01:
            fill = _blend(fill, QColor(token("accent_hover")), 0.32 * hover * check
                          + 0.06 * hover * (1.0 - check))
        border = _blend(QColor(token("border")), QColor(token("accent")), check)
        if hover > 0.01:
            border = _blend(border, QColor(token("focus")), 0.55 * hover)
        painter.setPen(QPen(border, max(1.0, scaled(1))))
        painter.setBrush(fill)
        painter.drawEllipse(center, radius, radius)

        ink = _blend(QColor(token("muted")), QColor("#ffffff"), check)
        if hover > 0.01 and check < 0.5:
            ink = _blend(ink, QColor(token("text")), hover)
        self._paint_padlock(painter, QPointF(center.x(), center.y() - scaled(4)), ink, fill, check)
        font = QFont(self.font())
        font.setPixelSize(max(7, scaled(9)))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(int(0), int(center.y() + scaled(6)), self.width(), scaled(14),
                         int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), self._side)

        if self.hasFocus() and self.property("keyboardFocus") is True:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(token("focus")), max(2.0, scaled(2))))
            painter.drawEllipse(center, radius + scaled(1), radius + scaled(1))
        painter.end()

    @staticmethod
    def _shackle_angle(check: float) -> float:
        """Angle de l'anse : positive quand le cadenas est ouvert, nulle quand il est fermé."""
        return 34.0 * (1.0 - max(0.0, min(1.0, check)))

    @staticmethod
    def _paint_padlock(painter: QPainter, center: QPointF, ink: QColor,
                       background: QColor, check: float) -> None:
        """Cadenas vectoriel : l'anse se relève à l'ouverture et retombe à la fermeture."""
        painter.save()
        painter.translate(center)
        painter.scale(scaled(18) / 18.0, scaled(18) / 18.0)
        # Petit rebond au passage, sans déformer le cadenas au repos.
        pulse = 1.0 + 0.07 * math.sin(math.pi * check)
        painter.scale(pulse, pulse)

        # Anse : le pied droit reste sur le corps, le pied gauche se relève à l'ouverture.
        # La rotation positive soulève l'anse ; la fermeture la fait redescendre dans le corps.
        shackle = QPainterPath()
        shackle.moveTo(-3.5, 0.6)
        shackle.lineTo(-3.5, -3.4)
        shackle.arcTo(QRectF(-3.5, -7.0, 7.0, 7.0), 180.0, -180.0)
        shackle.lineTo(3.5, 0.6)
        painter.save()
        pivot = QPointF(3.5, 0.6)
        painter.translate(pivot)
        painter.rotate(_ProtectOrb._shackle_angle(check))
        painter.translate(-pivot)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ink, 1.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(shackle)
        painter.restore()

        # Corps et trou de serrure découpé dans la couleur du fond de l'orbe.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(-6.0, 0.4, 12.0, 8.0), 2.3, 2.3)
        painter.setBrush(background)
        painter.drawEllipse(QPointF(0.0, 3.8), 1.25, 1.25)
        keyhole = QPainterPath()
        keyhole.moveTo(-0.6, 4.5)
        keyhole.lineTo(-1.0, 6.7)
        keyhole.lineTo(1.0, 6.7)
        keyhole.lineTo(0.6, 4.5)
        keyhole.closeSubpath()
        painter.drawPath(keyhole)
        painter.restore()


class _ProtectBulge(QWidget):
    """Zone d'import avec un renflement circulaire sur son bord extérieur.

    Le cadenas chevauche la moitié du bord : la zone garde sa forme rectangulaire,
    mais l'ensemble se lit comme une excroissance qui contient le bouton de protection.
    """

    def __init__(self, field: PathDropZone, orb: _ProtectOrb, side: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._field = field
        self._orb = orb
        self._side = side
        self._field.setParent(self)
        self._orb.setParent(self)
        self._field.show()
        self._orb.show()
        self._orb.raise_()
        self.setMinimumHeight(field.minimumHeight())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = self._field.sizeHint()
        return QSize(hint.width() + self._orb.width() // 2,
                     max(hint.height(), self._orb.height()))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = self._field.minimumSizeHint()
        return QSize(hint.width() + self._orb.width() // 2,
                     max(hint.height(), self._orb.height()))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        overlap = self._orb.width() // 2
        field_width = max(1, self.width() - overlap)
        orb_y = max(0, (self.height() - self._orb.height()) // 2)
        if self._side == "left":
            self._field.setGeometry(overlap, 0, field_width, self.height())
            self._orb.move(0, orb_y)
        else:
            self._field.setGeometry(0, 0, field_width, self.height())
            self._orb.move(max(0, self.width() - self._orb.width()), orb_y)


class DedupWindow(ModuleWindow):
    """Fenêtre du module Dedup."""

    # Hauteur réservée aux légendes : elle ne doit pas suivre la longueur du chemin
    # ni des métadonnées, sinon « Corrections manuelles » se déplace à chaque sélection.
    CAPTION_HEIGHT = 90

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._groups: list[Group] = []
        self._result: ScanResult | None = None
        self._group_rows: dict[Group, QStandardItem] = {}
        self._item_rows: dict[FileItem, list[QStandardItem]] = {}
        self._item_group: dict[FileItem, Group] = {}
        self._dd_worker = None
        self._last_journal: Path | None = None
        self._protect_a_touched = False
        self._quarantine_touched = False
        self._programmatic = False
        self.set_run_label(self.tr("Démarrer l'analyse"))

        self._build_ui()
        self._load_prefs()
        self._update_hint()
        self._update_stats()

        self.btn_apply = self.add_action_button(
            self.tr("Appliquer les modifications"), self._on_apply, kind="success",
            tooltip=self.tr("Retire les fichiers marqués « Supprimer » selon le mode choisi"))
        self.btn_undo = self.add_action_button(
            self.tr("Annuler une application…"), self._on_undo, kind="secondary",
            tooltip=self.tr("Remet en place les fichiers d'une quarantaine à partir de son journal.csv"))
        self.btn_export = self.add_action_button(
            self.tr("Exporter le rapport…"), self._on_export, kind="secondary",
            tooltip=self.tr("Enregistre le rapport complet des groupes en CSV"))
        self.busyChanged.connect(self._update_action_states)
        self._update_action_states()
        self._refresh_run_state()
        self.show_binary_status(("ffmpeg", "ffprobe"))
        # Méthode liée : la connexion tombe avec la fenêtre, sans garder un lambda en vie.
        ThemeManager.instance().changed.connect(self._refresh_colors)
        self.resize(1250, 800)

    # ── Construction de l'interface ───────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Dossiers : A à gauche, B à droite, avec inversion au centre comme dans Scope.
        folders = Card(self.tr("Dossiers"))
        self.field_a = PathDropZone(self.tr("Dossier A :"))
        self.field_b = PathDropZone(self.tr("Dossier B (facultatif) :"))
        self.chk_protect_a = _ProtectOrb(
            "A", self.tr("Protéger A (référence)"),
            self.tr("Protéger A (référence)\nFermé, les fichiers de A ne sont jamais proposés à la "
                      "suppression.\nCliquez pour ouvrir ou fermer le cadenas."))
        self.chk_protect_b = _ProtectOrb(
            "B", self.tr("Protéger B (comparaison)"),
            self.tr("Protéger B (comparaison)\nFermé, les fichiers de B ne sont jamais proposés à la "
                      "suppression.\nCliquez pour ouvrir ou fermer le cadenas."))
        fields = QHBoxLayout()
        fields.setSpacing(10)
        # Le cadenas occupe un renflement sur le bord extérieur de sa zone : à gauche pour A,
        # à droite pour B. Les deux zones restent dans le même parent pour l'animation d'échange.
        self.bulge_a = _ProtectBulge(self.field_a, self.chk_protect_a, "left")
        self.bulge_b = _ProtectBulge(self.field_b, self.chk_protect_b, "right")

        fields.addWidget(self.bulge_a, 1)
        self.btn_swap = QPushButton()
        self.btn_swap.setObjectName("SwapButton")
        self.btn_swap.setProperty("kind", "secondary")
        self.btn_swap.setIcon(icons.icon("swap-horizontal", "text", scaled(18)))
        self.btn_swap.setIconSize(QSize(scaled(18), scaled(18)))
        self.btn_swap.setFixedSize(scaled(38), scaled(38))
        self.btn_swap.setToolTip(self.tr("Échange les dossiers A et B"))
        self.btn_swap.setAccessibleName(self.tr("Inverser les dossiers A et B"))
        self.btn_swap.clicked.connect(self._swap)
        fields.addWidget(self.btn_swap, 0, Qt.AlignmentFlag.AlignVCenter)
        fields.addWidget(self.bulge_b, 1)
        folders.body_layout.addLayout(fields)
        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        folders.body_layout.addWidget(self.lbl_hint)
        self.field_a.pathChanged.connect(self._on_a_changed)
        self.field_b.pathChanged.connect(self._on_b_changed)
        self.chk_protect_a.clicked.connect(self._on_protect_a_clicked)
        self.chk_protect_a.toggled.connect(lambda _c: self._update_hint())
        self.chk_protect_b.toggled.connect(lambda _c: self._update_hint())
        layout.addWidget(folders)

        # Deux pages au-dessus des options, avec le sélecteur à capsule des thèmes :
        # la configuration à gauche, les résultats (zone Chemin) à droite.
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(4)
        self._subtab_strip = SegmentedControl()
        for label in (self.tr("Options"), self.tr("Résultats")):
            self._subtab_strip.addItem(label)
        self._subtab_strip.currentIndexChanged.connect(self._show_subtab)
        nav.addWidget(self._subtab_strip)
        nav.addStretch(1)
        layout.addLayout(nav)

        # Options : chaque famille de réglages est regroupée dans une zone visuelle.
        options = Card(self.tr("Options"))

        # Les rangées d'options passent à la ligne dans une fenêtre étroite :
        # aucune case ni valeur n'est coupée, la page défile verticalement.
        scope_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.chk_recursive = CheckBox(self.tr("Inclure les sous-dossiers"))
        self.chk_recursive.setChecked(True)
        self.chk_media = CheckBox(self.tr("Photos, vidéos et audios uniquement"))
        self.chk_hidden = CheckBox(self.tr("Ignorer les fichiers cachés"))
        self.chk_hidden.setChecked(True)
        for w in (self.chk_recursive, self.chk_media, self.chk_hidden):
            scope_row.addWidget(w)
        options.body_layout.addWidget(self._option_group(self.tr("Portée de l'analyse"), scope_row))

        detect_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.chk_exact = CheckBox(self.tr("Doublons exacts"))
        self.chk_exact.setChecked(True)
        self.chk_exact.setToolTip(self.tr("Contenu identique, quel que soit le nom (empreinte BLAKE2b)."))
        self.chk_modified = CheckBox(self.tr("Fichiers modifiés"))
        self.chk_modified.setChecked(True)
        self.chk_modified.setToolTip(self.tr("Même chemin relatif dans A et B, mais contenu différent. "
                                             "Jamais proposés à la suppression."))
        self.chk_similar = CheckBox(self.tr("Similaires"))
        self.chk_similar.setToolTip(self.tr("Détecter les images visuellement proches (dHash) ; la tolérance règle l'écart accepté."))
        self.cmb_tolerance = QComboBox()
        for label, value in TOLERANCES:
            self.cmb_tolerance.addItem(label, value)
        self.cmb_tolerance.setCurrentIndex(1)
        self.cmb_tolerance.setEnabled(False)
        self.chk_similar.toggled.connect(self.cmb_tolerance.setEnabled)
        for w in (self.chk_exact, self.chk_similar):
            detect_row.addWidget(w)
        detect_row.addWidget(QLabel(self.tr("Tolérance :")))
        detect_row.addWidget(self.cmb_tolerance)
        detect_row.addWidget(self.chk_modified)

        live_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.chk_live = CheckBox(self.tr("Live Photos"))
        self.chk_live.setToolTip(self.tr("Repère les paires IMG_1234.HEIC + IMG_1234.MOV (ou les noms convertis "
                                 "IMG_1234_heic.avif + IMG_1234_mov.webm). Elles sont seulement signalées, "
                                 "sauf si vous demandez d'écarter la vidéo."))
        self.cmb_live_max = QComboBox()
        for label, value in LIVE_DURATIONS:
            self.cmb_live_max.addItem(label, value)
        self.cmb_live_max.setCurrentIndex(1)
        self.cmb_live_max.setEnabled(False)
        self.chk_live_drop = CheckBox(self.tr("Écarter la vidéo"))
        self.chk_live_drop.setEnabled(False)
        self.chk_live_drop.setToolTip(self.tr("Proposer la vidéo à la suppression ; la photo est conservée."))
        self.chk_live.toggled.connect(self.cmb_live_max.setEnabled)
        self.chk_live.toggled.connect(self.chk_live_drop.setEnabled)
        for check in (self.chk_exact, self.chk_similar, self.chk_live):
            check.toggled.connect(self._refresh_run_state)
        live_row.addWidget(self.chk_live)
        live_row.addWidget(QLabel(self.tr("Durée max :")))
        live_row.addWidget(self.cmb_live_max)
        live_row.addWidget(self.chk_live_drop)
        options.body_layout.addWidget(self._option_group(self.tr("Détection"), detect_row, live_row))

        strategy_row = FlowLayout(h_spacing=10, v_spacing=6)
        strategy_row.addWidget(QLabel(self.tr("Proposition automatique :")))
        self.cmb_strategy = QComboBox()
        for strat in STRATEGY_LABELS:
            self.cmb_strategy.addItem(STRATEGY_TEXT.get(strat, STRATEGY_LABELS[strat]), strat.value)
        strategy_row.addWidget(self.cmb_strategy)

        mode_row = FlowLayout(h_spacing=10, v_spacing=6)
        mode_row.addWidget(QLabel(self.tr("Mode de suppression :")))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem(MODE_LABELS[MODE_QUARANTINE], MODE_QUARANTINE)
        if trash_available():
            self.cmb_mode.addItem(MODE_LABELS[MODE_TRASH], MODE_TRASH)
        self.cmb_mode.addItem(MODE_LABELS[MODE_DELETE], MODE_DELETE)
        self.cmb_mode.currentIndexChanged.connect(lambda _i: self._update_mode_widgets())
        mode_row.addWidget(self.cmb_mode)
        self.chk_empty_dirs = CheckBox(self.tr("Dossiers vides"))
        self.chk_empty_dirs.setToolTip(self.tr("Supprimer les dossiers devenus vides après les suppressions."))
        mode_row.addWidget(self.chk_empty_dirs)

        self.field_quarantine = PathField(self.tr("Dossier de quarantaine :"), mode="dir")
        self.field_quarantine.pathChanged.connect(self._on_quarantine_changed)
        options.body_layout.addWidget(
            self._option_group(self.tr("Traitement"), strategy_row, mode_row, self.field_quarantine))
        options_page = QWidget()
        options_layout = QVBoxLayout(options_page)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(0)
        options_layout.addWidget(options)
        options_layout.addStretch(1)
        options_scroll = QScrollArea()
        options_scroll.setWidgetResizable(True)
        options_scroll.setFrameShape(QFrame.Shape.NoFrame)
        options_scroll.setWidget(options_page)
        options_page.setAutoFillBackground(False)
        options_scroll.viewport().setAutoFillBackground(False)
        self._page_scroll = options_scroll
        self.subtabs = QStackedWidget()
        self.subtabs.addWidget(options_scroll)

        # Résultats
        bar = QHBoxLayout()
        bar.addWidget(QLabel(self.tr("Afficher :")))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(FILTERS)
        self.cmb_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        bar.addWidget(self.cmb_filter)
        bar.addStretch(1)
        self.lbl_stats = QLabel()
        bar.addWidget(self.lbl_stats)
        # Dans Dedup, la progression se lit sous les actions d'analyse, pas au-dessus.
        action_layout = self.action_bar.layout()
        action_layout.removeWidget(self.progress_bar)
        action_layout.addWidget(self.progress_bar)

        self._tree_model = QStandardItemModel(self)
        self._tree_model.setHorizontalHeaderLabels(list(COLUMNS))
        self.tree = _ResultsTree()
        self.tree.setModel(self._tree_model)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        for col, width in enumerate((self.tree.PATH_DEFAULT_WIDTH, 70, 90, 130, 110, 90)):
            self.tree.setColumnWidth(col, width)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._on_selection_changed())
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.toggleRequested.connect(self._toggle_selection)
        self.tree.deleteRequested.connect(lambda: self._set_action(ACTION_DELETE))
        self.tree.setMinimumHeight(240)
        results_page = QWidget()
        results_layout = QVBoxLayout(results_page)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(10)
        results_layout.addLayout(bar)
        results_layout.addWidget(self.tree, 1)
        results_page.setAutoFillBackground(False)
        self.subtabs.addWidget(results_page)
        # La barre d'actions reste hors des onglets : Démarrer, Annuler, Appliquer et
        # Exporter sont accessibles depuis les deux pages, comme dans Tags.
        layout.addWidget(self.subtabs, 1)
        layout.addWidget(self.action_bar)

        # La page Options est la seule à défiler ; les onglets et la barre d'actions
        # restent fixes quand la fenêtre est basse.
        root.setAutoFillBackground(False)
        self.set_content(root)
        self._show_subtab(0)

        self._build_side_panel()
        self._update_mode_widgets()
        self._update_swap_enabled()

    def _show_subtab(self, index: int) -> None:
        """Affiche Options ou Résultats et synchronise le sélecteur."""
        if not 0 <= index < self.subtabs.count():
            return
        self.subtabs.setCurrentIndex(index)
        self._subtab_strip.setCurrentIndex(index)

    @staticmethod
    def _option_group(title: str, *content: QLayout | QWidget) -> QFrame:
        """Zone discrète qui relie visuellement les réglages d'une même catégorie."""
        group = QFrame()
        group.setObjectName("OptionGroup")
        lay = QVBoxLayout(group)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("OptionGroupTitle")
        lay.addWidget(heading)
        for item in content:
            if isinstance(item, QLayout):
                lay.addLayout(item)
            else:
                lay.addWidget(item)
        return group

    def _build_side_panel(self) -> None:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(8)

        previews = QHBoxLayout()
        self.preview_a = MediaPreview(size=230, volume_controls=True)     # image, ou lecteur audio/vidéo intégré
        self.preview_b = MediaPreview(size=230, volume_controls=True)
        self.preview_a.playbackStarted.connect(self.preview_b.pause)     # comparer : un seul son à la fois
        self.preview_b.playbackStarted.connect(self.preview_a.pause)
        self.caption_a = QLabel()
        self.caption_a.setTextFormat(Qt.TextFormat.PlainText)
        self.caption_b = QLabel()
        self.caption_b.setTextFormat(Qt.TextFormat.PlainText)
        self.box_a, self.box_b = QWidget(), QWidget()
        for box, preview, caption in ((self.box_a, self.preview_a, self.caption_a),
                                      (self.box_b, self.preview_b, self.caption_b)):
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            caption.setWordWrap(True)
            caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            # La légende ne participe pas au calcul de la largeur : un chemin sans
            # espace ne peut plus élargir le panneau latéral.
            caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            caption.setMinimumWidth(0)
            caption.setFixedHeight(scaled(self.CAPTION_HEIGHT))
            caption.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            lay.addWidget(preview)
            lay.addWidget(caption)
            lay.addStretch(1)
            previews.addWidget(box)
        # Chaque aperçu porte son petit bouton d'ouverture, sous la timeline du lecteur :
        # le double-clic existait déjà, ce bouton le rend visible.
        self.open_a, self.open_b = self.preview_a.open_btn, self.preview_b.open_btn
        for btn in (self.open_a, self.open_b):
            btn.failed.connect(lambda msg: self._say("WARN", msg))
        self.box_b.setVisible(False)
        col.addLayout(previews)
        # Un peu d'air sous les aperçus : la zone des corrections descend et respire.
        col.addSpacing(scaled(18))

        title = QLabel(self.tr("Corrections manuelles"))
        title.setStyleSheet("font-weight: 500;")
        col.addWidget(title)
        self._correction_buttons: list[QPushButton] = []
        for text, callback, tip in (
            (self.tr("Garder"), lambda: self._set_action(ACTION_KEEP), self.tr("Garder la sélection")),
            (self.tr("Supprimer"), lambda: self._set_action(ACTION_DELETE), self.tr("Proposer la sélection à la suppression (Suppr)")),
            (self.tr("Tout supprimer"), self._delete_all,
             self.tr("Proposer tous les fichiers du groupe sélectionné à la suppression (les fichiers protégés sont conservés)")),
            (self.tr("Garder uniquement celui-ci"), self._keep_only_selected, self.tr("Garder ce fichier, proposer les autres du groupe")),
            (self.tr("Proposition auto pour la sélection"), self._auto_selection, self.tr("Appliquer la stratégie choisie à la sélection")),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(callback)
            col.addWidget(btn)
            self._correction_buttons.append(btn)
        hint = QLabel(self.tr("Espace : bascule Garder / Supprimer · Suppr : Supprimer · "
                              "Double-clic : lire (audio, vidéo) ou ouvrir le fichier"))
        hint.setWordWrap(True)
        col.addWidget(hint)
        col.addStretch(1)
        self.set_side_panel(panel, self.tr("Aperçu et corrections"))

    # ── Préférences ───────────────────────────────────────────────────

    def _load_prefs(self) -> None:
        p = self.prefs
        self.chk_recursive.setChecked(bool(p.get("recursive", True)))
        self.chk_media.setChecked(bool(p.get("media_only", False)))
        self.chk_hidden.setChecked(bool(p.get("skip_hidden", True)))
        self.chk_similar.setChecked(bool(p.get("similar", False)))
        self.chk_exact.setChecked(bool(p.get("exact", True)))
        self.chk_live.setChecked(bool(p.get("live", False)))
        self.chk_live_drop.setChecked(bool(p.get("live_drop_video", False)))
        self.chk_modified.setChecked(bool(p.get("find_modified", True)))
        idx = self.cmb_live_max.findData(p.get("live_max_seconds", DEFAULT_MAX_SECONDS))
        if idx >= 0:
            self.cmb_live_max.setCurrentIndex(idx)
        self.chk_empty_dirs.setChecked(bool(p.get("remove_empty", False)))
        idx = self.cmb_tolerance.findData(p.get("tolerance", 3))
        if idx >= 0:
            self.cmb_tolerance.setCurrentIndex(idx)
        idx = self.cmb_strategy.findData(p.get("strategy", Strategy.A_FIRST.value))
        if idx >= 0:
            self.cmb_strategy.setCurrentIndex(idx)
        idx = self.cmb_mode.findData(p.get("mode", MODE_QUARANTINE))
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)

    def _save_prefs(self) -> None:
        p = self.prefs
        p.set("recursive", self.chk_recursive.isChecked())
        p.set("media_only", self.chk_media.isChecked())
        p.set("skip_hidden", self.chk_hidden.isChecked())
        p.set("similar", self.chk_similar.isChecked())
        p.set("exact", self.chk_exact.isChecked())
        p.set("live", self.chk_live.isChecked())
        p.set("live_drop_video", self.chk_live_drop.isChecked())
        p.set("live_max_seconds", self.cmb_live_max.currentData())
        p.set("find_modified", self.chk_modified.isChecked())
        p.set("tolerance", self.cmb_tolerance.currentData())
        p.set("strategy", self.cmb_strategy.currentData())
        p.set("mode", self.cmb_mode.currentData())
        p.set("remove_empty", self.chk_empty_dirs.isChecked())

    # ── Dossiers et protection ────────────────────────────────────────

    def open_paths(self, paths: list[str]) -> None:
        """Le premier chemin devient A, le second B (un fichier est remplacé par son dossier)."""
        dirs = []
        for raw in paths[:2]:
            p = Path(raw)
            dirs.append(str(p if p.is_dir() else p.parent))
        if dirs:
            self.field_a.set_path(dirs[0])
        if len(dirs) > 1:
            self.field_b.set_path(dirs[1])

    def on_open(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = choose_directory(self, self.tr("Importer un dossier"), start)
        if not folder:
            return
        Preferences.instance().set("last_dir", folder)
        (self.field_b if self.field_a.path().strip() else self.field_a).set_path(folder)

    def _swap(self) -> None:
        """Échange A et B, sans toucher aux protections choisies."""
        if self.is_busy():
            return
        a, b = self.field_a.path(), self.field_b.path()
        if not a or not b:
            return
        self.field_a.animate_swap(self.field_b)
        # Les renflements restent au-dessus des copies animées des zones.
        self.bulge_a.raise_()
        self.bulge_b.raise_()

    def _update_swap_enabled(self) -> None:
        self.btn_swap.setEnabled(bool(self.field_a.path().strip() and self.field_b.path().strip())
                                 and not self.is_busy())

    def _refresh_run_state(self, *_args: object) -> None:
        """« Démarrer l'analyse » exige un dossier A valide et au moins une détection."""
        a = self.field_a.path().strip()
        b = self.field_b.path().strip()
        detection = self.chk_exact.isChecked() or self.chk_similar.isChecked() or self.chk_live.isChecked()
        valid = bool(a) and Path(a).is_dir() and (not b or Path(b).is_dir())
        self.set_run_enabled(valid and detection)

    def _on_a_changed(self, path: str) -> None:
        if not self._quarantine_touched and path.strip():
            self._programmatic = True
            self.field_quarantine.set_path(str(default_quarantine_parent(Path(path.strip()))))
            self._programmatic = False
        self._update_swap_enabled()
        self._refresh_run_state()

    def _on_b_changed(self, _path: str) -> None:
        self._update_protection()
        self._update_swap_enabled()
        self._refresh_run_state()

    def _on_quarantine_changed(self, _path: str) -> None:
        if not self._programmatic:
            self._quarantine_touched = True

    def _on_protect_a_clicked(self) -> None:
        self._protect_a_touched = True

    def _update_protection(self) -> None:
        """Avec deux dossiers, A (référence) est protégé par défaut ; avec un seul, il ne l'est pas."""
        if not self._protect_a_touched:
            self.chk_protect_a.setChecked(bool(self.field_b.path().strip()))
        self._update_hint()

    def _update_hint(self) -> None:
        two = bool(self.field_b.path().strip())
        # Le message mono-dossier est superflu : masquer la ligne rend la carte Dossiers
        # plus compacte et libère de la hauteur pour les résultats.
        self.lbl_hint.setVisible(two)
        self.lbl_hint.setText(HINT_DOUBLE if two else "")

    def _update_mode_widgets(self) -> None:
        self.field_quarantine.setVisible(self.cmb_mode.currentData() == MODE_QUARANTINE)

    def _scroll_to_top(self) -> None:
        """Garde la page au début quand une analyse démarre."""
        bar = self._page_scroll.verticalScrollBar()
        bar.setValue(bar.minimum())

    def _lockable(self) -> list[QWidget]:
        return [self.field_a, self.field_b, self.btn_swap, self.chk_protect_a, self.chk_protect_b,
                self.chk_recursive, self.chk_media, self.chk_hidden, self.chk_similar, self.cmb_tolerance,
                self.cmb_strategy, self.chk_exact, self.chk_live, self.cmb_live_max, self.chk_live_drop,
                self.chk_modified, self.cmb_mode, self.chk_empty_dirs, self.field_quarantine]

    def _say(self, level: str, text: str) -> None:
        self.log_panel.append_log(level, text)

    def _release_players(self) -> None:
        """Coupe le son et lâche les fichiers des deux aperçus (déplacements, suppressions, fermeture)."""
        for preview in (self.preview_a, self.preview_b):
            preview.release()

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._release_players()
        super().closeEvent(event)

    # ── Analyse ───────────────────────────────────────────────────────

    def _collect_options(self) -> ScanOptions | None:
        a_txt, b_txt = self.field_a.path().strip(), self.field_b.path().strip()
        if not a_txt:
            self._say("ERR", "Choisissez le dossier A.")
            return None
        folder_a = Path(a_txt)
        if not folder_a.is_dir():
            self._say("ERR", f"Dossier A introuvable : {folder_a}")
            return None
        folder_b: Path | None = None
        if b_txt:
            folder_b = Path(b_txt)
            if not folder_b.is_dir():
                self._say("ERR", f"Dossier B introuvable : {folder_b}")
                return None
            if _same_dir(folder_a, folder_b):
                self._say("WARN", "A et B sont le même dossier : recherche dans A seulement.")
                folder_b = None
        protect_a = self.chk_protect_a.isChecked()
        protect_b = self.chk_protect_b.isChecked() and folder_b is not None
        if protect_a and folder_b is None:
            self._say("WARN", "A est protégé et B est vide : aucun fichier ne sera proposé à la suppression.")
        elif protect_a and protect_b:
            self._say("WARN", "A et B sont protégés : aucun fichier ne sera proposé à la suppression.")
        if not (self.chk_exact.isChecked() or self.chk_similar.isChecked() or self.chk_live.isChecked()):
            self._say("ERR", "Cochez au moins une détection : doublons exacts, images similaires ou Live Photos.")
            return None
        ffprobe = None
        if self.chk_live.isChecked():
            try:
                ffprobe = binaries.resolve("ffprobe").path
            except Exception:                                  # noqa: BLE001 - détection défaillante : sans durée
                ffprobe = None
        return ScanOptions(
            folder_a=folder_a, folder_b=folder_b, protect_a=protect_a, protect_b=protect_b,
            recursive=self.chk_recursive.isChecked(), media_only=self.chk_media.isChecked(),
            skip_hidden=self.chk_hidden.isChecked(), similar=self.chk_similar.isChecked(),
            tolerance=int(self.cmb_tolerance.currentData() or 3),
            strategy=strategy_from_key(self.cmb_strategy.currentData()),
            exact=self.chk_exact.isChecked(), find_modified=self.chk_modified.isChecked(),
            live=self.chk_live.isChecked(), live_max_seconds=float(self.cmb_live_max.currentData()),
            live_drop_video=self.chk_live_drop.isChecked(), ffprobe=ffprobe,
        )

    def on_run(self) -> None:
        """Bouton « Démarrer l'analyse »."""
        if self.is_busy():
            return
        options = self._collect_options()
        if options is None:
            return
        self._save_prefs()
        self._release_players()
        self._say("INFO", "Analyse en cours…")
        worker = ScanWorker(options, self)
        self._dd_worker = worker
        progresses = [self.field_a.import_progress]
        if self.field_b.path().strip():
            progresses.append(self.field_b.import_progress)
        self.run_worker(worker, on_completed=self._on_scan_done,
                        on_failed=lambda msg: self._say("ERR", f"Échec de l'analyse : {msg}"),
                        on_cancelled=lambda: self._say("WARN", "Analyse annulée."),
                        lock_widgets=self._lockable(), import_progress=progresses,
                        history_action="Analyse")
        self._show_subtab(1)                              # les résultats s'ouvrent au démarrage
        self._scroll_to_top()
        QTimer.singleShot(0, self._scroll_to_top)     # après le changement d'état des boutons

    def _on_scan_done(self, result: object) -> None:
        if not isinstance(result, ScanResult):
            return
        self._result = result
        self._groups = result.groups
        self._populate()
        if not self._groups:
            self._say("OK", "Aucun doublon trouvé.")

    # ── Arbre des résultats ───────────────────────────────────────────

    def _populate(self) -> None:
        self.tree.setUpdatesEnabled(False)
        self._tree_model.clear()
        self._tree_model.setHorizontalHeaderLabels(list(COLUMNS))
        self._group_rows.clear()
        self._item_rows.clear()
        self._item_group.clear()
        for g in self._groups:
            head = _cell()
            head.setData(g, ROLE)
            self._tree_model.appendRow([head] + [_cell() for _ in range(len(COLUMNS) - 1)])
            self._group_rows[g] = head
            for it in g.items:
                cells = [_cell(str(it.path)), _cell(), _cell(format_size(it.size)),
                         _cell(datetime.fromtimestamp(it.mtime).strftime("%Y-%m-%d %H:%M")),
                         _cell(it.dimensions_text), _cell()]
                cells[0].setData(it, ROLE)
                head.appendRow(cells)
                self._item_rows[it] = cells
                self._item_group[it] = g
            self._refresh_group(g)
        if len(self._groups) <= 500:
            self.tree.expandAll()
        self.tree.setUpdatesEnabled(True)
        self._apply_filter()
        self._update_stats()
        self._on_selection_changed()
        self._update_action_states()

    def _update_action_states(self, *_args: object) -> None:
        """N'allume que les actions qui ont réellement quelque chose à traiter."""
        busy = self.is_busy()
        self.btn_apply.setEnabled(not busy and bool(plan_deletions(self._groups)))
        self.btn_export.setEnabled(not busy and bool(self._groups))
        self.btn_undo.setEnabled(not busy)
        selected = bool(self._selected_objects())
        for button in getattr(self, "_correction_buttons", []):
            set_action_available(button, selected and not busy)

    def _refresh_group(self, g: Group) -> None:
        head = self._group_rows.get(g)
        if head is None:
            return
        kind = KIND_TEXT.get(g.kind, KIND_LABELS.get(g.kind, g.kind))
        head.setText(self.tr("Groupe {id} · {kind} · {count} fichiers · gain : {gain}").format(
            id=g.id, kind=kind, count=len(g.items), gain=format_size(g.reclaimable)))
        ok_brush = QBrush(QColor(token("ok_bg")))
        err_brush = QBrush(QColor(token("err_bg")))
        for it in g.items:
            cells = self._item_rows.get(it)
            if not cells:
                continue
            cells[1].setText(it.side + (self.tr(" · protégé") if it.protected else ""))
            cells[5].setText(ACTION_TEXT.get(it.action, it.action))
            brush = ok_brush if it.action == ACTION_KEEP else err_brush
            for cell in cells:
                cell.setBackground(brush)

    def _refresh_colors(self, _name: str = "") -> None:
        for g in self._groups:
            self._refresh_group(g)

    def _update_stats(self) -> None:
        files, count, gain = totals(self._groups)
        if self._result is None:
            self.lbl_stats.setText(self.tr("Aucune analyse effectuée."))
            return
        self.lbl_stats.setText(self.tr(
            "{scanned} fichier(s) analysé(s) · {count} groupe(s) · {files} fichier(s) concerné(s) · "
            "gain libérable : {gain}").format(scanned=self._result.stats.scanned, count=count, files=files,
                                               gain=format_size(gain)))

    def _apply_filter(self) -> None:
        mode = self.cmb_filter.currentIndex()
        wanted = FILTER_KINDS.get(mode)
        for row, g in enumerate(self._groups):
            if wanted is not None:
                hidden = g.kind != wanted
            elif mode == len(FILTERS) - 1:
                hidden = not g.to_delete()
            else:
                hidden = False
            self.tree.setRowHidden(row, QModelIndex(), hidden)

    # ── Sélection, aperçu, comparaison ────────────────────────────────

    def _selected_objects(self) -> list[object]:
        return [idx.data(ROLE) for idx in self.tree.selectionModel().selectedRows(0)]

    def _selected_items(self) -> list[FileItem]:
        """Fichiers sélectionnés ; une ligne de groupe compte pour tous ses fichiers."""
        out: list[FileItem] = []
        for obj in self._selected_objects():
            members = obj.items if isinstance(obj, Group) else [obj] if isinstance(obj, FileItem) else []
            for it in members:
                if it not in out:
                    out.append(it)
        return out

    def _by_group(self, items: list[FileItem]) -> dict[Group, list[FileItem]]:
        grouped: dict[Group, list[FileItem]] = {}
        for it in items:
            g = self._item_group.get(it)
            if g is not None:
                grouped.setdefault(g, []).append(it)
        return grouped

    def _describe(self, it: FileItem) -> str:
        g = self._item_group.get(it)
        lines = [it.name, str(it.path), f"Côté {it.side}" + (" (protégé)" if it.protected else ""),
                 f"Taille : {format_size(it.size)}",
                 f"Modifié : {datetime.fromtimestamp(it.mtime).strftime('%Y-%m-%d %H:%M:%S')}"]
        if it.dimensions_text:
            lines.append(f"Dimensions : {it.dimensions_text}")
        if it.duration is not None:
            lines.append(f"Durée : {it.duration:.1f} s")
        if it.digest:
            lines.append(f"Empreinte : {it.digest[:16]}…")
        lines.append(f"Action : {it.action}")
        if g is not None:
            lines.append(f"Groupe {g.id} ({KIND_LABELS.get(g.kind, g.kind)})")
        return "\n".join(lines)

    def _on_selection_changed(self) -> None:
        objs = self._selected_objects()
        files = [o for o in objs if isinstance(o, FileItem)]
        groups = [o for o in objs if isinstance(o, Group)]
        self.box_b.setVisible(False)
        self.open_a.set_path(None)
        self.open_b.set_path(None)
        two_files = (len(files) == 2 and not groups
                     and self._item_group.get(files[0]) is self._item_group.get(files[1]))
        if not two_files:
            self.preview_b.release()                      # l'aperçu caché ne doit ni jouer ni garder le fichier ouvert
        if len(files) == 1 and not groups:
            self.preview_a.show_file(files[0].path)
            self.open_a.set_path(files[0].path)
            self.caption_a.setText(self._describe(files[0]))
        elif two_files:
            self.box_b.setVisible(True)
            self.preview_a.show_file(files[0].path)
            self.preview_b.show_file(files[1].path)
            self.open_a.set_path(files[0].path)
            self.open_b.set_path(files[1].path)
            self.caption_a.setText(self._describe(files[0]))
            self.caption_b.setText(self._describe(files[1]))
        elif len(groups) == 1 and not files:
            g = groups[0]
            self.preview_a.show_text(self.tr("Groupe {id}").format(id=g.id))
            self.caption_a.setText(self.tr("{count} fichiers · {kind}\nTaille totale : {size}\n"
                                           "Gain libérable : {gain}").format(
                count=len(g.items), kind=KIND_TEXT.get(g.kind, KIND_LABELS.get(g.kind, g.kind)),
                size=format_size(g.total_size), gain=format_size(g.reclaimable)))
        elif objs:
            self.preview_a.show_text(self.tr(
                "Sélectionnez un fichier, ou deux fichiers du même groupe pour les comparer."))
            self.caption_a.setText(self.tr("{count} fichier(s) sélectionné(s)").format(
                count=len(self._selected_items())))
        else:
            self.preview_a.clear()
            self.caption_a.setText("")
        # Le texte complet reste accessible lorsque la hauteur réservée le tronque.
        for caption in (self.caption_a, self.caption_b):
            caption.setToolTip(caption.text())
        # Une ligne qui vient d'être sélectionnée rend les corrections manuelles utilisables.
        # Sans cette remise à jour, les boutons restaient désactivés après un clic dans l'arbre
        # (seuls un scan ou une action précédente les réactivaient).
        if hasattr(self, "btn_apply"):
            self._update_action_states()

    def _on_double_click(self, index: QModelIndex) -> None:
        obj = index.siblingAtColumn(0).data(ROLE)
        if isinstance(obj, FileItem):
            if MediaPreview.is_playable(obj.path):
                for preview in (self.preview_a, self.preview_b):      # audio/vidéo : lecture intégrée, sans application externe
                    if preview.can_play() and preview.current_path() == obj.path:
                        preview.toggle_playback()
                        return
            # Un type inconnu (.exe, .bat, .lnk, .desktop, script…) n'est jamais lancé : le gestionnaire du
            # système l'exécuterait. On montre alors seulement le dossier qui le contient (AZ-006).
            target = obj.path if obj.path.suffix.lower() in OPENABLE_EXTS and obj.path.is_file() else obj.path.parent
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
                self._say("WARN", f"Impossible d'ouvrir : {target}")
        elif isinstance(obj, Group):
            self.tree.setExpanded(index.siblingAtColumn(0), not self.tree.isExpanded(index.siblingAtColumn(0)))

    # ── Corrections manuelles ─────────────────────────────────────────

    def _after_change(self, groups: set[Group] | list[Group]) -> None:
        for g in groups:
            self._refresh_group(g)
        self._apply_filter()
        self._update_stats()
        self._on_selection_changed()
        self._update_action_states()

    def _set_action(self, action: str) -> None:
        if self.is_busy() or not self._groups:
            return
        changed: list[Group] = []
        for g, items in self._by_group(self._selected_items()).items():
            ok, message = set_actions(g, items, action)
            if ok:
                changed.append(g)
            else:
                self._say("WARN", message)
        self._after_change(changed)

    def _delete_all(self) -> None:
        """Bouton « Tout supprimer » : propose tous les fichiers des groupes sélectionnés.

        Une ligne de groupe compte pour tout le groupe ; un fichier sélectionné fait
        traiter tout son groupe. Les fichiers des dossiers protégés restent gardés.
        Seules les actions sont modifiées : rien n'est supprimé avant « Appliquer les
        modifications », qui demande alors confirmation (et signale les groupes dont plus
        aucun fichier ne serait conservé).
        """
        if self.is_busy() or not self._groups:
            return
        groups = list(self._by_group(self._selected_items()))
        if not groups:
            self._say("WARN", "Sélectionnez un groupe (ou un fichier de ce groupe) pour tout y supprimer.")
            return
        targets = [it for g in groups for it in g.items if not it.protected]
        if not targets:
            self._say("WARN", "Aucun fichier à supprimer : les fichiers de ce groupe sont protégés.")
            return
        changed: list[Group] = []
        for g in groups:
            items = [it for it in g.items if not it.protected]
            if items and set_actions(g, items, ACTION_DELETE)[0]:
                changed.append(g)
        self._after_change(changed)
        message = self.tr("{count} fichier(s) proposé(s) à la suppression dans {groups} groupe(s).").format(
            count=len(targets), groups=len(groups))
        protected = sum(1 for g in groups for it in g.items if it.protected)
        if protected:
            message += " " + self.tr("{count} fichier(s) protégé(s) conservé(s).").format(count=protected)
        self._say("INFO", message)

    def _toggle_selection(self) -> None:
        if self.is_busy() or not self._groups:
            return
        changed: list[Group] = []
        for g, items in self._by_group(self._selected_items()).items():
            target = ACTION_DELETE if items[0].action == ACTION_KEEP else ACTION_KEEP
            ok, message = set_actions(g, items, target)
            if ok:
                changed.append(g)
            else:
                self._say("WARN", message)
        self._after_change(changed)

    def _keep_only_selected(self) -> None:
        if self.is_busy():
            return
        items = [o for o in self._selected_objects() if isinstance(o, FileItem)]
        if len(items) != 1:
            self._say("WARN", "Sélectionnez un seul fichier pour « Garder uniquement celui-ci ».")
            return
        g = self._item_group[items[0]]
        ok, message = keep_only(g, items[0])
        self._say("INFO" if ok else "WARN", message)
        self._after_change([g] if ok else [])

    def _auto_selection(self) -> None:
        if self.is_busy() or not self._groups:
            return
        strategy = strategy_from_key(self.cmb_strategy.currentData())
        changed = []
        for g, items in self._by_group(self._selected_items()).items():
            resolve_selection(g, items, strategy, self.chk_live_drop.isChecked() and self.chk_live.isChecked())
            changed.append(g)
        self._after_change(changed)

    # ── Application ───────────────────────────────────────────────────

    def _ask(self, title: str, text: str, danger: bool = False) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_apply(self) -> None:
        """Bouton « Appliquer les modifications »."""
        if self.is_busy():
            return
        plan = plan_deletions(self._groups)
        if not plan:
            self._say("INFO", "Aucun fichier proposé à la suppression.")
            return
        count, size = len(plan), sum(it.size for _, it in plan)
        # Un groupe dont tous les fichiers sont proposés ne doit pas passer inaperçu.
        keeperless = [g for g in self._groups if g.to_delete() and not g.kept()]
        warning = ""
        if keeperless:
            warning = self.tr(
                "Aucun fichier ne sera conservé dans {groups} groupe(s) ({files} fichier(s)).\n"
                "Tous les fichiers de ces groupes seront supprimés.\n\n").format(
                    groups=len(keeperless), files=sum(len(g.to_delete()) for g in keeperless))
        mode = self.cmb_mode.currentData()
        quarantine_parent: Path | None = None
        if mode == MODE_QUARANTINE:
            text = self.field_quarantine.path().strip()
            if not text and self._result is not None and self._result.options is not None:
                text = str(default_quarantine_parent(self._result.options.folder_a))
            if not text:
                self._say("ERR", "Choisissez le dossier de quarantaine.")
                return
            quarantine_parent = Path(text)
            question = (f"{count} fichier(s) ({format_size(size)}) vont être déplacés dans un dossier "
                        f"« Quarantaine_… » créé dans :\n{quarantine_parent}\n\n"
                        "L'opération est réversible grâce au journal.csv.")
        elif mode == MODE_TRASH:
            question = f"{count} fichier(s) ({format_size(size)}) vont être envoyés à la corbeille du système."
        else:
            question = (f"{count} fichier(s) ({format_size(size)}) vont être supprimés DÉFINITIVEMENT.\n"
                        "Cette opération est irréversible.")
        title = self.tr("Aucun fichier conservé") if keeperless else self.tr("Appliquer les modifications")
        if not self._ask(title, warning + question + "\n\nContinuer ?",
                         danger=mode == MODE_DELETE or bool(keeperless)):
            return
        if mode == MODE_DELETE and not self._ask(
                "Dernière confirmation",
                f"Supprimer définitivement {count} fichier(s) sans passer par la corbeille ?", danger=True):
            return
        self._save_prefs()
        options = ApplyOptions(mode=mode, quarantine_parent=quarantine_parent,
                               remove_empty_dirs=self.chk_empty_dirs.isChecked(),
                               tolerance=int(self.cmb_tolerance.currentData() or 3),
                               allow_keeperless=bool(keeperless))
        self._release_players()                              # un fichier lu reste ouvert : Windows refuserait de le supprimer
        worker = ApplyWorker(self._groups, options, self)
        self._dd_worker = worker
        self.run_worker(worker, on_completed=lambda r: self._after_apply(r if isinstance(r, ApplyReport) else None),
                        on_failed=lambda msg: self._say("ERR", f"Échec de l'application : {msg}"),
                        on_cancelled=lambda: self._after_apply(worker.report),
                        lock_widgets=self._lockable(), history_action="Application")

    def _after_apply(self, report: ApplyReport | None) -> None:
        if report is None:
            return
        done = set(report.done)
        if done:
            for g in self._groups:
                g.items = [i for i in g.items if i not in done]
            self._groups = [g for g in self._groups if len(g.items) >= 2]
            self._populate()
        if report.journal is not None:
            self._last_journal = report.journal
            self._say("OK", f"Journal de quarantaine : {report.journal}")
        if report.removed_dirs:
            self._say("INFO", f"{report.removed_dirs} dossier(s) vidé(s) supprimé(s).")
        if report.mode == MODE_DELETE:
            self._say("INFO", f"Libéré : {format_size(report.freed)}. Relancez une analyse pour actualiser "
                              "les résultats.")
        elif report.mode == MODE_TRASH:
            self._say("INFO", f"Envoyé à la corbeille : {format_size(report.quarantined)} (pas encore libéré). "
                              "Relancez une analyse pour actualiser les résultats.")
        else:
            self._say("INFO", f"Déplacé en quarantaine : {format_size(report.quarantined)} (pas encore libéré). "
                              "Relancez une analyse pour actualiser les résultats.")

    # ── Annulation d'une application ──────────────────────────────────

    def _on_undo(self) -> None:
        """Bouton « Annuler une application… »."""
        if self.is_busy():
            return
        start = str(self._last_journal.parent if self._last_journal else
                    Preferences.instance().get("last_dir", "") or "")
        path = choose_file(self, self.tr("Choisir le journal d'une quarantaine"), start,
                           self.tr("Journal (journal.csv);;Fichiers CSV (*.csv)"))
        if not path:
            return
        journal = Path(path)
        allow_foreign = False
        if not (journal.parent / JOURNAL_META_NAME).is_file():
            # Journal importé ou antérieur : ses chemins de destination ne sont pas vérifiables.
            answer = QMessageBox.question(
                self, self.tr("Journal non vérifiable"),
                self.tr("Ce journal ne contient pas la liste des dossiers analysés (journal importé ou antérieur).\n\n"
                        "La restauration déplacera des fichiers vers les emplacements qu'il indique. "
                        "Ne continuez que si vous faites entièrement confiance à ce journal.\n\nRestaurer ?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._say("WARN", "Restauration annulée : journal non vérifiable.")
                return
            allow_foreign = True
        self._release_players()
        worker = UndoWorker(journal, self, allow_foreign=allow_foreign)
        self._dd_worker = worker
        self.run_worker(worker, on_completed=lambda _r: self._say(
                            "INFO", "Restauration terminée. Relancez une analyse pour actualiser les résultats."),
                        on_failed=lambda msg: self._say("ERR", f"Échec de la restauration : {msg}"),
                        on_cancelled=lambda: self._say("WARN", "Restauration interrompue."),
                        lock_widgets=self._lockable(), history_action="Annulation")

    # ── Export ────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        """Bouton « Exporter le rapport… »."""
        if self.is_busy():
            return
        if not self._groups:
            self._say("INFO", "Aucun résultat à exporter : lancez d'abord une analyse.")
            return
        start = str(Path(str(Preferences.instance().get("last_dir", "") or "")) / "rapport_doublons.csv")
        path = choose_save_file(self, self.tr("Exporter le rapport"), start,
                                self.tr("Fichiers CSV (*.csv)"))
        if not path:
            return
        try:
            rows = export_report(self._groups, Path(path))
        except OSError as exc:
            self._say("ERR", f"Export impossible : {exc}")
            return
        self._say("OK", f"Rapport exporté ({rows} ligne(s)) : {path}")
