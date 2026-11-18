"""Fenêtre principale d'Azimut : page des outils et page des journaux."""
from __future__ import annotations

import html
import math
from functools import partial

from PySide6.QtCore import (
    QCoreApplication, QEasingCurve, QElapsedTimer, QEvent, QPoint, QPointF, QRectF, QSize, Qt,
    QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QGuiApplication, QKeySequence, QMouseEvent, QPainter, QPalette, QPen,
    QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from azimut import APP_NAME, __version__
from azimut.core.history import HistoryStore
from azimut.core.logs import get_logger
from azimut.core.prefs import Preferences
from azimut.core.module_spec import ModuleSpec, load_window_class
from azimut.core.screenfit import fit_to_screen
from azimut.launcher.dashboard import Dashboard, safe_resolve
from azimut.launcher.personal_note import personal_note_text
from azimut.launcher.registry import discover_with_errors
from azimut.launcher.tips_dialog import TipsPage
from azimut.ui import icons
from azimut.ui.app import restart_application
from azimut.ui.fonts import brand_font
from azimut.ui.glass import (
    AuroraPanel,
    AuroraWindowMixin,
    GlassFrame,
    GlassSegmentedBar,
    GlassTabBar,
    HoverIcon,
    LiquidButton,
)
from azimut.ui.page_stack import SlidingPageStack
from azimut.ui.notification import notify_persistent
from azimut.ui.preferences_dialog import PreferencesPage
from azimut.ui.theme import ThemeManager, caption_point_size, repolish, scaled, token
from azimut.ui.widgets import GOLDEN_RATIO, Card, IconLabel, StatusChip, plain_tooltip

log = get_logger(__name__)


class ModuleCard(Card):
    """Carte d'un module : icône, nom, tagline, pastilles de binaires, bouton « Ouvrir »."""

    # Format au nombre d'or : la hauteur vaut la largeur divisée par φ (1,618…).
    # Les bornes gardent la carte lisible sans la laisser devenir un bandeau sur un
    # écran très large, ni une vignette écrasée sur un écran court.
    ASPECT = GOLDEN_RATIO
    CARD_MIN_WIDTH = 220
    CARD_MAX_WIDTH = 520
    CARD_MIN_HEIGHT = 148
    CARD_MAX_HEIGHT = 320
    # La carte étant survolée longtemps, sa boucle respire plus lentement que les
    # animations de boutons : 1,5× la durée propre de l'icône (630 à 1500 ms).
    ICON_LOOP_FACTOR = 1.5
    # 30 images/s suffisent pour ces petits tracés et laissent le curseur fluide ;
    # les images identiques sont en plus sautées dans ``_on_icon_frame``.
    ICON_FRAME_MS = 33
    # Tolérance avant de considérer une carte trop courte : quelques pixels se
    # rattrapent sur les gouttières internes, sans faire défiler toute la page.
    CONTENT_SLACK = 4

    openRequested = Signal(str)        # id du module
    backgroundOpenRequested = Signal(str)   # id du module, à ouvrir sans activer l'onglet
    pathsDropped = Signal(str, list)   # id du module, chemins déposés

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__("", parent, interactive=True)
        self.spec = spec
        self._error = ""
        self.chips: dict[str, StatusChip] = {}
        self.setAcceptDrops(True)

        self._icon_base = QPixmap()
        self._icon_progress = 0.0
        self._icon_frame: bytes | None = None
        # Horodatage du dernier dessin : la boucle est plafonnée à ICON_FRAME_MS, ce qui
        # évite de repeindre le verre à chaque tick de l'animation (60 Hz).
        self._icon_clock = QElapsedTimer()
        self._icon_anim = QVariantAnimation(self)
        self._icon_anim.setDuration(180)
        self._icon_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._icon_anim.valueChanged.connect(self._on_icon_frame)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(28, 28)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_icon()
        self.name_label = QLabel(spec.name)
        self.name_label.setObjectName("ModuleName")
        head = QHBoxLayout()
        head.addWidget(self.icon_label)
        head.addWidget(self.name_label, 1)
        self.body_layout.addLayout(head)

        # La tagline est traduite à l'affichage : la spec peut avoir été importée avant
        # l'installation du traducteur (c'est le cas de `python main.py <module>`).
        self.tagline_label = QLabel(QCoreApplication.translate("Modules", spec.tagline))
        self.tagline_label.setWordWrap(True)
        self.body_layout.addWidget(self.tagline_label)

        self.chips_layout = QHBoxLayout()
        for name in spec.requires:
            chip = StatusChip(name, "neutre")
            chip.setProperty("quiet", True)
            self.chips[name] = chip
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        self.body_layout.addLayout(self.chips_layout)

        self.error_label = QLabel("")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self.body_layout.addWidget(self.error_label)

        self.open_button = QPushButton(self.tr("Ouvrir"))
        self.open_button.setProperty("kind", "ghost")   # six CTA primaires se neutralisaient
        self.open_button.installEventFilter(self)
        self.open_button.clicked.connect(self._on_open_button_clicked)
        self._button_ctrl = False
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.open_button)
        self.body_layout.addStretch(1)            # contenu aligné en haut quand la carte voisine est plus haute
        self.body_layout.addLayout(foot)
        # Carte cliquable et utilisable au clavier : la cible ne se limite plus au bouton.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(spec.name)
        self._card_size: tuple[int, int] | None = None

        self.refresh_binaries()
        self.restyle()
        ThemeManager.instance().changed.connect(self.restyle)

    # ── état ──
    def is_failed(self) -> bool:
        return bool(self._error)

    def set_error(self, message: str) -> None:
        """Désactive la carte et affiche l'erreur."""
        self._error = message or self.tr("erreur inconnue")
        short = self._error if len(self._error) <= 240 else self._error[:237] + "…"
        self.error_label.setText(short)
        self.error_label.setToolTip(plain_tooltip(self._error))
        self.error_label.show()
        if "__error__" not in self.chips:
            chip = StatusChip(self.tr("✖ indisponible"), "err")
            chip.setProperty("quiet", True)
            chip.setToolTip(plain_tooltip(self._error))
            self.chips["__error__"] = chip
            self.chips_layout.insertWidget(0, chip)
        self.open_button.setEnabled(False)
        self.setAcceptDrops(False)
        self.restyle()

    def refresh_binaries(self) -> None:
        for name, chip in self.chips.items():
            if name == "__error__":
                continue
            info = safe_resolve(name)
            if info is not None and info.available:
                chip.set_state(f"✔ {name}", "ok")
                chip.setToolTip(plain_tooltip(f"{name} {info.version}".strip() + f"\n{info.path}"))
            else:
                chip.set_state(f"✖ {name}", "err")
                chip.setToolTip(self.tr("Introuvable : voir « Préférences… » ou le dossier bin/."))

    def _apply_icon(self) -> None:
        """Pictogramme SVG sur une tuile teintée ; un texte (ou emoji) sans SVG correspondant sert de repli."""
        self._icon_anim.stop()
        self._icon_anim.setLoopCount(1)
        self._icon_progress = 0.0
        self._icon_frame = None
        self._icon_clock.invalidate()
        if icons.available(self.spec.icon):
            self._icon_base = icons.pixmap(self.spec.icon, 20, token("muted"))
            self.icon_label.setText("")
            self.icon_label.setPixmap(self._icon_base)
            self.icon_label.setStyleSheet("background: transparent;")
        else:
            self._icon_base = QPixmap()
            self.icon_label.setPixmap(QPixmap())
            self.icon_label.setText(self.spec.icon)
            self.icon_label.setStyleSheet("font-size: 20px; background: transparent; color: "
                                         f"{token('muted')};")

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().enterEvent(event)
        if not self._icon_base.isNull() and icons.motion_enabled():
            self._start_icon_loop()

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().leaveEvent(event)
        self._stop_icon_loop()

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Une carte cachée ne continue pas à animer en arrière-plan."""
        self._icon_anim.stop()
        self._icon_anim.setLoopCount(1)
        self._icon_progress = 0.0
        if not self._icon_base.isNull():
            self._icon_frame = None
            self.icon_label.setPixmap(self._icon_base)
        super().hideEvent(event)

    def _start_icon_loop(self) -> None:
        """Rejoue le mouvement de l'icône tant que la carte reste survolée."""
        self._icon_anim.stop()
        # La carte suit la durée propre à l'icône : à 180 ms fixes, les tracés
        # élaborés (sparkles, search…) défilaient trop vite pour être perçus.
        self._icon_anim.setDuration(
            round(icons.hover_duration(self.spec.icon) * self.ICON_LOOP_FACTOR))
        self._icon_clock.start()
        self._icon_anim.setLoopCount(-1)
        self._icon_anim.setStartValue(0.0)
        self._icon_anim.setEndValue(1.0)
        self._icon_anim.start()

    def _stop_icon_loop(self) -> None:
        """Interrompt la boucle et ramène l'icône au repos sans à-coup."""
        self._icon_anim.stop()
        self._icon_anim.setLoopCount(1)
        if self._icon_progress <= 0.001:
            return
        self._icon_anim.setDuration(180)
        self._icon_anim.setStartValue(self._icon_progress)
        self._icon_anim.setEndValue(0.0)
        self._icon_anim.start()

    def _on_icon_frame(self, value: object) -> None:
        try:
            self._icon_progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        if self._icon_base.isNull():
            return
        if (0.001 < self._icon_progress < 0.999 and self._icon_clock.isValid()
                and self._icon_clock.elapsed() < self.ICON_FRAME_MS):
            return
        self._icon_clock.restart()
        # Le libellé fait 28 px pour une icône de 20 : composer dans cette boîte donne
        # 4 px de marge transparente et le saut de l'épingle Takeout ne touche plus le bord.
        base_side = round(self._icon_base.width() / max(1.0, self._icon_base.devicePixelRatio()))
        padding = max(0.0, (self.icon_label.width() - base_side) / 2.0)
        pixmap = icons.hovered_pixmap(self._icon_base, self.spec.icon, self._icon_progress,
                                     padding=padding)
        # Beaucoup d'images intermédiaires sont identiques à 20 px : ne pas les reposer
        # évite un repaint du verre pour rien.
        frame = bytes(pixmap.toImage().constBits())
        if frame == self._icon_frame:
            return
        self._icon_frame = frame
        self.icon_label.setPixmap(pixmap)


    def set_card_geometry(self, width: int, height: int) -> None:
        """Applique la taille au nombre d'or calculée par la page des outils.

        La carte ne descend jamais sous la hauteur que son contenu réclame : si la police
        du thème, une traduction ou le zoom ont agrandi le texte après le calcul, mieux
        vaut une rangée plus haute (et une page qui défile) qu'un bouton rogné.
        """
        width = max(1, int(width))
        roomy = self.content_height(width) - scaled(self.CONTENT_SLACK)
        self._card_size = (width, max(1, int(height), roomy))
        self.setFixedSize(*self._card_size)

    def content_height(self, width: int) -> int:
        """Hauteur réellement réclamée par le contenu à cette largeur (0 si inconnue)."""
        needed = self.layout().totalHeightForWidth(width)
        return needed if needed > 0 else 0

    def restyle(self, _name: str = "") -> None:
        if self._card_size is None:
            self.setMinimumHeight(scaled(self.CARD_MIN_HEIGHT))
        else:
            self.setFixedSize(*self._card_size)
        self._apply_icon()
        muted = f"color: {token('muted')};"
        self.tagline_label.setStyleSheet(muted)
        self.error_label.setStyleSheet(f"color: {token('err_txt')};")

    # ── ouverture (souris, clavier) ──
    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        """Retient le Ctrl du clic sur « Ouvrir » : `clicked` ne transporte pas les modificateurs."""
        if watched is self.open_button:
            if event.type() == QEvent.Type.MouseButtonPress:
                self._button_ctrl = False
            elif event.type() == QEvent.Type.MouseButtonRelease:
                inside = self.open_button.rect().contains(event.position().toPoint())
                self._button_ctrl = inside and bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        return super().eventFilter(watched, event)

    def _on_open_button_clicked(self) -> None:
        if self._button_ctrl:
            self._button_ctrl = False
            self.backgroundOpenRequested.emit(self.spec.id)
        else:
            self.openRequested.emit(self.spec.id)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.is_failed():
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+clic : charger l'outil sans quitter l'onglet courant.
                self.backgroundOpenRequested.emit(self.spec.id)
            else:
                self.openRequested.emit(self.spec.id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self.is_failed() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.openRequested.emit(self.spec.id)
            event.accept()
            return
        super().keyPressEvent(event)

    # ── glisser-déposer ──
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if not self.is_failed() and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(self.spec.id, paths)


class _ToolsTabBar(GlassTabBar):
    """Onglets ajustés au libellé, à l'icône et à la croix de fermeture.

    La largeur calculée par Qt ajoutait une centaine de pixels par onglet : « Tags »
    occupait 216 px pour 113 px de texte. On la calcule ici à partir du contenu réel, ce qui
    garde des onglets proportionnés à leur nom et à la croix. Le libellé est aligné sur le
    padding gauche de l'onglet ; la croix réserve sa place à droite, au même retrait.
    """

    PAD = 14            # respiration du libellé quand l'onglet n'a pas de croix
    ICON_GAP = 6
    CROSS_GAP = 12      # entre la fin du libellé et la croix
    EDGE = 11           # retrait horizontal de l'onglet : $p10 + bordure 1 px
    MIN_WIDTH = 72
    SELECT_DURATION = 180
    # Durée du glissement des onglets déplacés en groupe : même ordre de grandeur que
    # l'animation native d'un onglet seul (250 ms), un peu plus vive car répétée.
    MOVE_DURATION = 200

    selectionChanged = Signal()
    closeSelectedRequested = Signal()
    closeCurrentRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        # Un onglet d'outil ouvert garde son icône fixe : seule la carte de la page
        # d'accueil anime le pictogramme au survol.
        super().__init__(parent, animate_icons=False)
        # La barre reçoit le focus au clic : Suppr ferme alors l'onglet actif
        # ou les onglets sélectionnés, sans dépendre de la page affichée.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._placing_close_buttons = False
        self._pressed_tab = -1
        self._home_is_pressed = False
        self._selected_ids: set[str] = set()
        self._anchor_id: str | None = None
        self._selection_rect = QRectF()
        self._selection_from = QRectF()
        self._selection_to = QRectF()
        self._selection_progress = 1.0
        self._selection_animating = False
        self._selection_animation = QVariantAnimation(self)
        self._selection_animation.setDuration(self.SELECT_DURATION)
        self._selection_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._selection_animation.setStartValue(0.0)
        self._selection_animation.setEndValue(1.0)
        self._selection_animation.valueChanged.connect(self._on_selection_frame)
        self._selection_animation.finished.connect(self._on_selection_finished)
        # Qt n'anime que le déplacement d'un onglet glissé seul (décalage privé). Pour
        # un groupe, on peint les onglets à des positions interpolées le temps du trajet.
        self._move_from: dict[str, QRectF] = {}
        self._move_to: dict[str, QRectF] = {}
        self._move_shots: dict[str, QPixmap] = {}
        self._suppress_band = False       # capture des onglets : ni bandeau ni capsule
        self._move_progress = 1.0
        self._move_animating = False
        self._move_animation = QVariantAnimation(self)
        self._move_animation.setDuration(self.MOVE_DURATION)
        self._move_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._move_animation.setStartValue(0.0)
        self._move_animation.setEndValue(1.0)
        self._move_animation.valueChanged.connect(self._on_move_frame)
        self._move_animation.finished.connect(self._on_move_finished)
        self._shift_selecting = False
        self._group_drag_pending = False
        self._group_drag_moved = False
        self._group_drag_current_id: str | None = None
        self._group_drag_start = QPointF()
        self._group_drop_index = -1
        self._group_order: list[str] = []
        self._collapse_selection_on_release = False
        self._arranging_group = False
        self.tabMoved.connect(self._note_group_drag)

    # ── sélection multiple ──
    def _tab_id(self, index: int) -> str:
        """Identifiant stable d'un onglet ; vide pour « Tous les outils »."""
        if not 0 <= index < self.count():
            return ""
        data = self.tabData(index)
        return data if isinstance(data, str) else ""

    def _index_of_id(self, tab_id: str) -> int:
        for index in range(1, self.count()):
            if self._tab_id(index) == tab_id:
                return index
        return -1

    def selected_ids(self) -> list[str]:
        ids = [(index, self._tab_id(index)) for index in range(1, self.count())]
        return [tab_id for index, tab_id in ids if tab_id in self._selected_ids]

    def is_selected(self, index: int) -> bool:
        return index > 0 and self._tab_id(index) in self._selected_ids

    def selection_count(self) -> int:
        return len(self.selected_ids())

    def _selection_target(self) -> QRectF:
        indexes = [index for index in range(1, self.count()) if self.is_selected(index)]
        if not indexes:
            return QRectF()
        target = self._tab_rect(indexes[0]).united(self._tab_rect(indexes[-1]))
        return target.adjusted(0.0, 0.0, 0.0, 0.5)

    def _snap_selection(self, rect: QRectF) -> None:
        self._selection_animation.stop()
        self._selection_animating = False
        self._selection_progress = 1.0
        self._selection_rect = QRectF(rect)
        self._selection_from = QRectF(rect)
        self._selection_to = QRectF(rect)
        self.update()

    def _glide_selection(self, before: QRectF, target: QRectF) -> None:
        """Amène le bandeau de sélection à sa nouvelle place en glissant."""
        if (before.isNull() or target.isNull()
                or ThemeManager.instance().reduce_motion() or not self.isVisible()):
            self._snap_selection(target)
            return
        self._selection_from = QRectF(before)
        self._selection_to = QRectF(target)
        self._selection_progress = 0.0
        self._selection_animating = True
        self._selection_animation.stop()
        self._selection_animation.start()
        self.update()

    def _set_selected_ids(self, ids: set[str], *, origin: int = -1,
                          animate: bool = True) -> None:
        ids = {tab_id for tab_id in ids if tab_id}
        changed = ids != self._selected_ids
        self._selected_ids = ids
        target = self._selection_target()
        if target.isNull():
            self._selection_animation.stop()
            self._selection_animating = False
            self._selection_rect = QRectF()
            self._selection_from = QRectF()
            self._selection_to = QRectF()
            self.update()
        elif (animate and changed and not self._selection_animating
              and not ThemeManager.instance().reduce_motion() and self.isVisible()):
            start = QRectF(self._selection_rect)
            if start.isNull() and origin > 0:
                start = self._tab_rect(origin)
            if start.isNull():
                self._snap_selection(target)
            else:
                self._selection_from = start
                self._selection_to = target
                self._selection_progress = 0.0
                self._selection_animating = True
                self._selection_animation.stop()
                self._selection_animation.start()
                self.update()
        elif not self._selection_animating:
            self._snap_selection(target)
        else:
            self._selection_to = target
        if changed:
            self.selectionChanged.emit()

    def _select_range_to(self, index: int) -> None:
        if index <= 0:
            self.clear_selection()
            return
        anchor_id = self._anchor_id
        if anchor_id is None:
            anchor_id = self._tab_id(self.currentIndex())
            if not anchor_id:
                anchor_id = self._tab_id(index)
        anchor_index = self._index_of_id(anchor_id)
        if anchor_index < 0:
            anchor_index = index
            anchor_id = self._tab_id(index)
        self._anchor_id = anchor_id
        first, last = sorted((anchor_index, index))
        ids = {self._tab_id(tab) for tab in range(first, last + 1)}
        ids.discard("")
        self._set_selected_ids(ids, origin=index)

    def clear_selection(self) -> None:
        """Retire la sélection multiple ; l'onglet actif n'est pas modifié."""
        self._anchor_id = None
        if self._selected_ids:
            self._set_selected_ids(set(), animate=False)
        else:
            self._snap_selection(QRectF())

    def prune_selection(self) -> None:
        """Oublie les onglets fermés et garde la sélection sur les autres."""
        existing = {self._tab_id(index) for index in range(1, self.count())}
        kept = self._selected_ids & existing
        if self._anchor_id and self._anchor_id not in existing:
            self._anchor_id = None
        if kept != self._selected_ids:
            self._set_selected_ids(kept, animate=False)
        elif kept:
            # Les index ont pu glisser : la croix et le bandeau doivent suivre.
            self.selectionChanged.emit()
            self._snap_selection(self._selection_target())

    def _on_selection_frame(self, value: object) -> None:
        progress = float(value)
        self._selection_progress = max(0.0, min(1.0, progress))
        self._selection_rect = QRectF(
            self._selection_from.x() + (self._selection_to.x() - self._selection_from.x()) * progress,
            self._selection_from.y() + (self._selection_to.y() - self._selection_from.y()) * progress,
            self._selection_from.width()
            + (self._selection_to.width() - self._selection_from.width()) * progress,
            self._selection_from.height()
            + (self._selection_to.height() - self._selection_from.height()) * progress,
        )
        self.update()

    def _on_selection_finished(self) -> None:
        self._selection_animating = False
        self._selection_progress = 1.0
        self._selection_rect = QRectF(self._selection_to)
        self.update()

    def _sync_selection_rect(self) -> None:
        if self._selection_animating or not self._selected_ids:
            return
        target = self._selection_target()
        if not target.isNull() and target != self._selection_rect:
            self._selection_rect = target
            self._selection_from = QRectF(target)
            self._selection_to = QRectF(target)

    # ── animation du déplacement groupé ──
    def _tab_cells(self) -> dict[str, QRectF]:
        """Cellule de chaque onglet, indexée par identifiant stable (« » pour l'accueil)."""
        return {self._tab_id(index): QRectF(self.tabRect(index))
                for index in range(self.count())}

    def _displayed_cells(self) -> dict[str, QRectF]:
        """Position visible de chaque onglet : cible finale, ou point du trajet en cours."""
        if not self._move_animating:
            return self._tab_cells()
        progress = self._move_progress
        cells: dict[str, QRectF] = {}
        for tab_id, target in self._move_to.items():
            start = self._move_from.get(tab_id, target)
            cells[tab_id] = QRectF(
                start.x() + (target.x() - start.x()) * progress,
                start.y() + (target.y() - start.y()) * progress,
                start.width() + (target.width() - start.width()) * progress,
                start.height() + (target.height() - start.height()) * progress,
            )
        return cells

    def _tab_shots(self) -> dict[str, QPixmap]:
        """Image de chaque onglet, sans le bandeau ni la capsule.

        Le style Qt repeint le libellé à la case réelle de l'onglet, même quand on lui
        donne un rectangle décalé : pour animer, on dessine donc l'instantané de l'onglet
        à la position voulue.
        """
        band, indicator = self._selection_rect, self._indicator
        selection_animating, move_animating = self._selection_animating, self._move_animating
        self._selection_rect, self._indicator = QRectF(), QRectF()
        self._selection_animating = self._move_animating = False
        self._suppress_band = True
        try:
            snapshot = self.grab()          # un seul rendu, puis découpe par onglet
            return {self._tab_id(index): snapshot.copy(self.tabRect(index))
                    for index in range(self.count())}
        finally:
            self._suppress_band = False
            self._selection_rect, self._indicator = band, indicator
            self._selection_animating, self._move_animating = selection_animating, move_animating

    def _start_move_animation(self, before: dict[str, QRectF], after: dict[str, QRectF],
                              shots: dict[str, QPixmap]) -> None:
        """Fait glisser les onglets de leur ancienne position vers la nouvelle."""
        if (before == after or not shots or ThemeManager.instance().reduce_motion()
                or not self.isVisible()):
            self._snap_move(after)
            return
        self._move_shots = shots
        self._move_from = {tab_id: QRectF(before.get(tab_id, rect))
                           for tab_id, rect in after.items()}
        self._move_to = {tab_id: QRectF(rect) for tab_id, rect in after.items()}
        self._move_progress = 0.0
        self._move_animating = True
        for index in range(1, self.count()):        # les croix sont dans les instantanés
            button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if button is not None:
                button.setVisible(False)
        self._move_animation.stop()
        self._move_animation.start()
        self.update()

    def _snap_move(self, cells: dict[str, QRectF]) -> None:
        self._move_animation.stop()
        self._move_animating = False
        self._move_progress = 1.0
        self._move_shots = {}
        self._move_from = {tab_id: QRectF(rect) for tab_id, rect in cells.items()}
        self._move_to = {tab_id: QRectF(rect) for tab_id, rect in cells.items()}
        for index in range(1, self.count()):
            button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if button is not None and button.isHidden():
                button.setVisible(True)
        self.place_close_buttons()

    def _on_move_frame(self, value: object) -> None:
        self._move_progress = max(0.0, min(1.0, float(value)))
        self.place_close_buttons()          # les croix suivent leur onglet
        self.update()

    def _on_move_finished(self) -> None:
        self._move_animating = False
        self._move_progress = 1.0
        self._move_shots = {}
        for index in range(1, self.count()):
            button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if button is not None and button.isHidden():
                button.setVisible(True)
        self.place_close_buttons()
        self.update()

    def _paint_tabs(self) -> None:
        """Peint les onglets à leur position animée, d'après leurs instantanés."""
        cells = self._displayed_cells()
        group = self._selected_ids
        order = [tab_id for tab_id in cells if tab_id not in group]
        order += [tab_id for tab_id in cells if tab_id in group]   # le bloc passe au-dessus
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        for tab_id in order:
            shot = self._move_shots.get(tab_id)
            if shot is None or shot.isNull():
                continue
            cell = cells[tab_id]
            painter.drawPixmap(QPoint(round(cell.x()), round(cell.y())), shot)
        painter.end()

    def _note_group_drag(self, _from: int, _to: int) -> None:
        """Marque le groupe comme déplacé dès qu'un onglet change de place."""
        if self._group_drag_pending:
            self._group_drag_moved = True

    def _begin_group_drag(self) -> None:
        """Démarre le glisser du bloc après le seuil de déplacement de Qt."""
        self._group_drag_moved = True
        self._group_drop_index = -1
        # La capsule de l'onglet actif donnerait l'impression qu'un seul onglet
        # glisse : on la masque au profit du bandeau orange du groupe.
        self._start_timer.stop()
        self._animation.stop()
        self._animating = False
        self._indicator = QRectF()

    def _group_drop_slot(self, x: float, direction: int) -> int:
        """Nombre d'onglets non sélectionnés à laisser avant le groupe.

        Vers la droite, un onglet dont le centre est dépassé passe avant le groupe ;
        vers la gauche, il faut aller au-delà de son centre pour le laisser derrière.
        Ce sens évite qu'un dépôt au centre du dernier onglet place le groupe avant lui.
        """
        group = set(self._group_order)
        slot = 0
        for index in range(1, self.count()):
            tab_id = self._tab_id(index)
            if tab_id in group:
                continue
            center = self.tabRect(index).center().x()
            if center < x or (direction > 0 and center == x):
                slot += 1
        return slot

    def _on_tab_moved(self, _from: int, _to: int) -> None:
        """Ne fait pas réapparaître la capsule active pendant le glisser groupé."""
        if self._group_drag_pending and self._group_drag_moved:
            return
        super()._on_tab_moved(_from, _to)

    def _sync_indicator(self) -> None:
        """Gèle la capsule active le temps du déplacement du groupe."""
        if (self._group_drag_pending and self._group_drag_moved) or self._move_animating:
            return
        super()._sync_indicator()

    def _move_group_to(self, slot: int) -> None:
        """Place le bloc sélectionné après les ``slot`` premiers onglets libres."""
        group = [tab_id for tab_id in self._group_order if self._index_of_id(tab_id) > 0]
        if not group:
            return
        selected = set(group)
        rest = [self._tab_id(index) for index in range(1, self.count())
                if self._tab_id(index) not in selected]
        slot = max(0, min(len(rest), slot))
        desired = [""] + rest[:slot] + group + rest[slot:]
        before = self._displayed_cells()
        band_before = QRectF(self._selection_rect)
        # Les onglets ne changent pas d'apparence pendant le geste : leurs instantanés
        # sont réutilisés d'un franchissement à l'autre.
        shots = (self._move_shots if self._move_animating
                 else self._tab_shots() if self.isVisible() else {})
        self._arranging_group = True
        try:
            for target, tab_id in enumerate(desired):
                current = self._index_of_id(tab_id) if tab_id else 0
                if current >= 0 and current != target:
                    self.moveTab(current, target)
        finally:
            self._arranging_group = False
        # Qt décale l'index courant quand on déplace des onglets : on remet la page
        # affichée sur le module d'origine pour que le glisser ne change pas d'outil.
        if self._group_drag_current_id:
            current = self._index_of_id(self._group_drag_current_id)
            if current > 0 and current != self.currentIndex():
                self.setCurrentIndex(current)
        # Le bandeau et les onglets rejoignent leur nouvelle place en glissant, au lieu
        # de sauter d'un coup comme le faisait `moveTab` seul.
        self._glide_selection(band_before, self._selection_target())
        self._start_move_animation(before, self._tab_cells(), shots)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Delete:
            if self._selected_ids:
                self.closeSelectedRequested.emit()
            elif self.currentIndex() > 0:
                self.closeCurrentRequested.emit()
            event.accept()
            return
        if (event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                and self.count() > 1):
            # Maj+flèches étend la sélection depuis l'onglet d'ancrage, comme
            # Maj+clic, pour pouvoir fermer plusieurs onglets sans souris.
            step = -1 if event.key() == Qt.Key.Key_Left else 1
            target = max(1, min(self.count() - 1, self.currentIndex() + step))
            if target != self.currentIndex():
                self._select_range_to(target)
                self.setCurrentIndex(target)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._selected_ids:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Mémorise l'onglet saisi : le premier onglet ne doit jamais être déplacé."""
        if event.button() == Qt.MouseButton.LeftButton and (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            index = self.tabAt(event.position().toPoint())
            if index > 0:
                # Maj+clic sélectionne sans changer l'onglet actif : on peut préparer
                # un groupe sans quitter la page en cours.
                self._select_range_to(index)
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self._shift_selecting = True
                event.accept()
                return
            self.clear_selection()
            self._shift_selecting = False
            super().mousePressEvent(event)
            return

        self._shift_selecting = False
        self._pressed_tab = self.tabAt(event.position().toPoint())
        self._home_is_pressed = (
            event.button() == Qt.MouseButton.LeftButton and self._pressed_tab == 0)
        self._collapse_selection_on_release = False
        self._group_drag_moved = False
        self._group_drag_pending = (
            event.button() == Qt.MouseButton.LeftButton
            and self._pressed_tab > 0
            and self.is_selected(self._pressed_tab)
            and self.selection_count() > 1)
        self._group_drag_start = QPointF(event.position()) if self._group_drag_pending else QPointF()
        self._group_order = self.selected_ids() if self._group_drag_pending else []
        if event.button() == Qt.MouseButton.LeftButton:
            if self._pressed_tab <= 0 or not self.is_selected(self._pressed_tab):
                self.clear_selection()
                self._anchor_id = self._tab_id(self._pressed_tab) or None
            else:
                # Un clic simple sur un onglet déjà sélectionné referme le groupe ;
                # on attend le relâchement pour laisser le glisser-déposer groupé agir.
                self._collapse_selection_on_release = True
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            # Sans cela, le focus restait sur la page et la touche Suppr n'atteignait
            # jamais la barre d'onglets après un clic simple.
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            # Mémorisé après le clic : Qt a pu faire de l'onglet saisi l'onglet actif.
            self._group_drag_current_id = (
                self._tab_id(self.currentIndex()) or None) if self._group_drag_pending else None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Déplace le bloc sélectionné d'un seul tenant, sans fantôme d'onglet."""
        if self._group_drag_pending and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._group_drag_moved:
                if (event.position() - self._group_drag_start).manhattanLength() \
                        < QApplication.startDragDistance():
                    event.accept()
                    return
                self._begin_group_drag()
            direction = 1 if event.position().x() >= self._group_drag_start.x() else -1
            slot = self._group_drop_slot(event.position().x(), direction)
            if slot != self._group_drop_index:
                self._group_drop_index = slot
                self._move_group_to(slot)
            event.accept()
            return
        if self._home_is_pressed:
            event.accept()
            return
        if self._pressed_tab > 0 and event.buttons() & Qt.MouseButton.LeftButton:
            home = self.tabRect(0)
            if home.isValid() and event.position().x() <= home.right() + scaled(3):
                # On garde le curseur juste après l'onglet fixe : un module peut être
                # réordonné après lui, mais jamais avant.
                point = QPointF(home.right() + scaled(4), event.position().y())
                event = QMouseEvent(
                    event.type(), point, QPointF(self.mapToGlobal(point.toPoint())),
                    event.button(), event.buttons(), event.modifiers())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._shift_selecting:
            self._shift_selecting = False
            event.accept()
            return
        if self._group_drag_pending and self._group_drag_moved:
            super().mouseReleaseEvent(event)
            direction = 1 if event.position().x() >= self._group_drag_start.x() else -1
            slot = self._group_drop_slot(event.position().x(), direction)
            self._group_drop_index = slot
            self._move_group_to(slot)
            self._pressed_tab = -1
            self._home_is_pressed = False
            self._group_drag_pending = False
            self._collapse_selection_on_release = False
            self._group_drag_current_id = None
            self._group_drag_start = QPointF()
            self._group_drop_index = -1
            self._group_order = []
            self.update()
            event.accept()
            return
        self._pressed_tab = -1
        self._home_is_pressed = False
        super().mouseReleaseEvent(event)
        if self._collapse_selection_on_release:
            self.clear_selection()
        self._group_drag_pending = False
        self._collapse_selection_on_release = False
        self._group_drag_current_id = None
        self._group_drag_start = QPointF()
        self._group_drop_index = -1
        self._group_order = []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        """Peint le bandeau de sélection multiple sous les onglets et leur texte."""
        if self._suppress_band:                 # capture d'un onglet pour l'animation
            super().paintEvent(event)
            return
        self._sync_selection_rect()
        if self._group_drag_pending and self._group_drag_moved:
            # La capsule active est masquée pendant le glisser groupé (voir
            # _sync_indicator) : on efface aussi son ancienne géométrie.
            self._indicator = QRectF()
        if not self._selection_rect.isNull():
            rect = QRectF(self._selection_rect)
            if self._selection_animating:
                energy = math.sin(math.pi * self._selection_progress)
                stretch = energy * min(8.0, rect.width() * 0.06)
                rect = rect.adjusted(-stretch / 2.0, 0.0, stretch / 2.0, 0.0)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setClipRect(self.rect())
            accent = QColor(token("accent"))
            fill = QColor(accent)
            fill.setAlpha(66 if ThemeManager.instance().theme() == "dark" else 50)
            stroke = QColor(accent)
            stroke.setAlpha(140 if ThemeManager.instance().theme() == "dark" else 120)
            painter.setBrush(fill)
            painter.setPen(QPen(stroke, 1.0))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 9.0, 9.0)
            painter.end()
        if self._move_animating:
            # Pendant le trajet, les onglets sont peints à leur position interpolée.
            self._paint_tabs()
        else:
            super().paintEvent(event)
        if self._group_drag_pending and self._group_drag_moved and not self._selection_rect.isNull():
            # Contour net autour du bloc en cours de déplacement : le groupe reste
            # lisible même lorsque la souris est sur un de ses onglets.
            rect = QRectF(self._selection_rect).adjusted(0.5, 0.5, -0.5, -0.5)
            accent = QColor(token("accent"))
            glow = QColor(accent)
            glow.setAlpha(60)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setClipRect(self.rect())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glow, 5.0))
            painter.drawRoundedRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0), 10.0, 10.0)
            painter.setPen(QPen(accent, 1.5))
            painter.drawRoundedRect(rect, 9.0, 9.0)
            painter.end()

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802
        base = super().tabSizeHint(index)
        width = self.fontMetrics().horizontalAdvance(self.tabText(index))
        if not self.tabIcon(index).isNull():
            width += self.iconSize().width() + scaled(self.ICON_GAP)
        btn = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
        if index > 0 and btn is not None:
            # Largeur = retrait du libellé + libellé + écart + croix + même retrait :
            # la croix tombe ainsi exactement à l'aplomb du padding du texte.
            width += 2 * scaled(self.EDGE) + scaled(self.CROSS_GAP) + btn.width()
        else:
            width += 2 * scaled(self.PAD)
        return QSize(max(scaled(self.MIN_WIDTH), width), base.height())

    def _close_button_spot(self, index: int, button: QWidget,
                           cell: QRectF | None = None) -> QPoint | None:
        """Position voulue d'une croix : même retrait que le libellé, axe de la capsule."""
        if cell is None:
            cell = QRectF(self.tabRect(index))
        capsule = self._capsule_rect(cell)
        if cell.isEmpty() or capsule.isNull():
            return None
        return QPoint(round(cell.right()) - scaled(self.EDGE) - button.width(),
                      round(capsule.center().y() - button.height() / 2))

    def place_close_buttons(self) -> None:
        """Aligne les croix sur le retrait du libellé et sur l'axe de la capsule.

        Qt colle les boutons de côté au bord de l'onglet et les centre sur la case brute,
        alors que le libellé respire après le padding et que la capsule est décalée vers le
        bas : on replace chaque croix une fois la mise en page terminée. Un changement
        d'onglet fait aussi recalculer les boutons par Qt sans passer par
        ``tabLayoutChange`` ; la croix rappelle alors cette méthode (voir ``moveEvent``).
        """
        if self._placing_close_buttons:
            return
        self._placing_close_buttons = True
        try:
            cells = self._displayed_cells()
            for index in range(1, self.count()):
                button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
                if button is None:
                    continue
                spot = self._close_button_spot(index, button, cells.get(self._tab_id(index)))
                if spot is not None and button.pos() != spot:
                    button.move(spot)
        finally:
            self._placing_close_buttons = False

    def tabLayoutChange(self) -> None:  # noqa: N802
        """Recale les croix après chaque réorganisation des onglets."""
        super().tabLayoutChange()
        self.place_close_buttons()
        if not self._selection_animating:
            self._sync_selection_rect()

    def minimumTabSizeHint(self, index: int) -> QSize:  # noqa: N802
        return self.tabSizeHint(index)


class _TabCloseButton(QToolButton):
    """Croix de fermeture discrète, révélée au survol de son onglet.

    Le bouton reste dans la mise en page (largeur réservée) mais devient transparent et
    insensible aux clics tant qu'il est masqué : les onglets ne sautent donc pas quand la croix
    apparaît. La zone cliquable est tout le bouton (22 px) et la croix y est centrée : cliquer
    sur le fond rond qui s'allume au survol ferme bien l'onglet.
    """

    SIZE = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TabCloseButton")
        self.setFixedSize(scaled(self.SIZE), scaled(self.SIZE))
        self.setIconSize(QSize(scaled(12), scaled(12)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tr("Fermer l'onglet"))
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._icon_hover = HoverIcon(self, self.icon, self.setIcon)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def moveEvent(self, event) -> None:  # noqa: N802
        """Se recale si Qt replace le bouton à sa convenance (changement d'onglet)."""
        super().moveEvent(event)
        place = getattr(self.parentWidget(), "place_close_buttons", None)
        if callable(place):
            place()

    def set_active(self, active: bool) -> None:
        """Affiche ou masque la croix sans modifier l'empreinte de l'onglet."""
        active = bool(active)
        self._opacity.setOpacity(1.0 if active else 0.0)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not active)

    def is_active(self) -> bool:
        return self._opacity.opacity() > 0.0


def _placeholder_spec(module_id: str) -> ModuleSpec:
    """Spec factice pour afficher un module dont l'import a échoué."""
    return ModuleSpec(id=module_id, name=module_id.capitalize(),
                      tagline=QCoreApplication.translate("AtlasWindow", "Ce module n'a pas pu être chargé."), icon="alert-triangle",
                      entry="", order=1000)


class _CardsColumn(QWidget):
    """Colonne des cartes : le bloc au nombre d'or, au plus les trois quarts de la page.

    La largeur vient de la page des outils (largeur du bloc de cartes) ; à défaut, la
    colonne occupe les trois quarts de la page. Elle est recentrée par le parent, ce qui
    laisse respirer les cartes au lieu de les étirer jusqu'aux bords.
    """

    WIDTH_RATIO = 0.75

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._watching: QWidget | None = None
        self._preferred_width: int | None = None

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None and parent is not self._watching:
            if self._watching is not None:
                self._watching.removeEventFilter(self)
            parent.installEventFilter(self)
            self._watching = parent
        self._fit_to_parent()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802 - API Qt
        if watched is self._watching and event.type() == QEvent.Type.Resize:
            self._fit_to_parent()
        return super().eventFilter(watched, event)

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = max(1, round(parent.width() * self.WIDTH_RATIO))
        if self._preferred_width is not None:
            width = min(width, self._preferred_width)
        if self.width() != width or self.minimumWidth() != width:
            self.setFixedWidth(width)

    def set_preferred_width(self, width: int) -> None:
        """Rétrécit la colonne au bloc de cartes, sans jamais dépasser les trois quarts."""
        width = max(1, int(width))
        if width == self._preferred_width:
            return
        self._preferred_width = width
        self._fit_to_parent()


class _HeaderDetailLabel(QLabel):
    """Détail de l'en-tête : largeur naturelle, sans largeur minimale imposée.

    Une politique horizontale « Ignored » seule laissait la barre réduire le libellé à
    la largeur du titre « Azimut » : le sous-titre était coupé au milieu d'un mot même
    quand la place ne manquait pas. La politique « Preferred » rend au libellé sa
    largeur utile ; la largeur minimale ramenée à zéro permet à la fenêtre de rétrécir
    et au masquage automatique des détails de s'appliquer.
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class AtlasWindow(AuroraWindowMixin, QMainWindow):
    """Azimut — le couteau suisse pour convertir, dédoublonner et ranger vos médias."""

    COLUMNS = 2
    MAX_COLUMNS = 3                    # au-delà, les cartes ne tiennent plus leur contenu
    HEADER_WORDMARK = 80
    HEADER_COMPACT = 36
    HEADER_INSET = 24
    HEADER_COMPACT_SHRINK = 0.5       # largeur de la barre compacte : −1/2
    HEADER_VPAD = 21                  # marge verticale utile au repos (12 + 9 du layout)
    HEADER_VPAD_COMPACT = 13          # barre compacte : 36 + 2 × 13 = 62 px, soit −1/5
    HEADER_TITLE = 22
    HEADER_TITLE_COMPACT = 18
    # Même fin que la capsule d'onglet : 240 ms de glissement + 40 ms de stabilisation.
    HEADER_ANIMATION_MS = GlassTabBar.DURATION + GlassTabBar.PAGE_SETTLE_MS
    # Largeur d'ouverture : à 1200 px, le sélecteur Outils / Journaux
    # dispose encore de 26 px à gauche de la barre compacte « Azimut ».
    DEFAULT_WIDTH = 1200
    # 900 px : les trois rangées de cartes disposent de leur hauteur confortable
    # dès l'ouverture, avant le plafonnement éventuel à la taille de l'écran.
    DEFAULT_HEIGHT = 900
    TAB_ICON_SIZE = 16
    TAB_ZOOM_INTERVAL_MS = 90
    TAB_ZOOM_STEPS = 8
    TAB_ZOOM_MIN = 0.70
    PAGE_TOOLS = 0
    PAGE_ACTIVITY = 1
    PAGE_TIPS = 2
    PAGE_PREFERENCES = 3
    TOP_LAYOUT_MERGED = "merged"        # sélecteur de pages dans l'en-tête (défaut)
    TOP_LAYOUT_CLASSIC = "classic"      # sélecteur dans sa propre rangée
    AUTO_FALLBACK_MIN_ZOOM = 1.2        # sous ce zoom, le repli automatique n'est pas nécessaire
    AUTO_LAYOUT_GUTTER = 24             # marge avant de basculer vers l'ancien agencement
    AUTO_LAYOUT_RESTORE_MARGIN = 24     # hystérésis pour revenir à l'en-tête regroupé

    def __init__(self, specs: list[ModuleSpec] | None = None, errors: dict[str, str] | None = None,
                 *, history_store: HistoryStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if specs is None:
            specs, found = discover_with_errors()
            if errors is None:
                errors = found
        self._specs = sorted(specs, key=lambda s: (s.order, s.id))
        self._spec_by_id = {s.id: s for s in self._specs}
        self._errors = dict(errors or {})
        self._windows: dict[str, QMainWindow] = {}
        self._shutting_down = False
        self._tab_busy: set[str] = set()
        self._hovered_tab = -1
        self._restoring_tab_order = False
        self._tab_zoom_step = 0
        self._tab_icon_timer = QTimer(self)
        self._tab_icon_timer.setInterval(self.TAB_ZOOM_INTERVAL_MS)
        self._tab_icon_timer.timeout.connect(self._animate_busy_tabs)
        self._header_progress = 0.0
        self._header_compact = False
        self._header_animation = QVariantAnimation(self)
        self._header_animation.setDuration(self.HEADER_ANIMATION_MS)
        self._header_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._header_animation.valueChanged.connect(
            lambda value: self._apply_header_progress(float(value)))
        self._title_size = 0
        self._logo_size = self.HEADER_WORDMARK
        self._wordmark = QPixmap()
        self._wordmark_theme = ""
        self.cards: dict[str, ModuleCard] = {}
        self.page_buttons: dict[int, QPushButton] = {}
        self._top_layout = self._saved_top_layout()
        self._layout_preference = self._top_layout
        self._merged_layout_extra = 0
        self._layout_update_in_progress = False
        # Largeur demandée à l'ouverture : celle du constructeur, puis celle de tout
        # redimensionnement explicite avant le premier affichage (voir `resize`).
        self._initial_width_target: int | None = None

        self.setWindowTitle(self.tr("Azimut"))
        self.setWindowIcon(icons.app_icon())
        screen = self.screen() or QGuiApplication.primaryScreen()        # AZ-C03 : jamais plus grande que l'écran
        area = screen.availableGeometry() if screen is not None else None
        target = fit_to_screen(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT,
                               (area.width(), area.height()) if area is not None else None)
        self._initial_width_target = target[0]          # `resize` la met à jour si besoin
        self.resize(*target)

        self.pages = SlidingPageStack()
        self._build_pages(history_store)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 16)
        # Espacement explicite : la barre d'onglets doit toucher la barre de pages. Avec
        # l'espacement global, la bande de fond entre les deux ressemblait à une barre noire
        # au-dessus des onglets dès qu'un module était ouvert en arrière-plan.
        root.setSpacing(0)
        # Barre de navigation en verre : le fond coloré se lit à travers, les commandes
        # restent nettes. Les marges donnent au bloc la respiration d'une barre flottante.
        self.header_bar = GlassFrame(radius=8)
        self.header_bar.setContentsMargins(16, self.HEADER_VPAD, 16, self.HEADER_VPAD)
        self.header_bar.setLayout(self._build_header())
        # Hôte transparent : ses marges latérales animent la largeur du bloc d'en-tête,
        # sans toucher à celle de la barre d'onglets ni de la page des outils.
        self.header_host = QWidget()
        self._header_host_layout = QHBoxLayout(self.header_host)
        self._header_host_layout.setContentsMargins(0, 0, 0, 0)
        self._header_host_layout.setSpacing(0)
        self._header_host_layout.addWidget(self.header_bar)
        self.header_host.installEventFilter(self)
        root.addWidget(self.header_host)
        root.addSpacing(12)
        root.addWidget(self._build_page_bar())

        self.warning_banner = IconLabel("", "alert-triangle", "warn_txt")
        self.warning_banner.setWordWrap(True)
        self.warning_banner.hide()
        self.warning_host = QWidget()
        warning_layout = QVBoxLayout(self.warning_host)
        warning_layout.setContentsMargins(0, 12, 0, 12)
        warning_layout.setSpacing(0)
        warning_layout.addWidget(self.warning_banner)
        self.warning_host.hide()
        root.addWidget(self.warning_host)

        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self._apply_top_layout()
        self._restyle()
        ThemeManager.instance().changed.connect(self._restyle)
        ThemeManager.instance().accessibility_changed.connect(self._on_accessibility_changed)
        self._update_banner()
        self._sync_header(animate=False)
        self._install_module_shortcuts()
        self._build_menu()
        self._remember_merged_layout_extra()
        self._sync_top_layout_action()
        self._update_layout_fallback()
        ThemeManager.instance().zoomChanging.connect(self._on_zoom_changing)

    def _build_pages(self, history_store: HistoryStore | None) -> None:
        """Prépare la page des outils et celle de l'activité."""
        tools_content = AuroraPanel()
        tools_layout = QVBoxLayout(tools_content)
        # Même retrait horizontal que le contenu d'un module embarqué (16 px) : la grille
        # ne colle plus au bord de l'onglet. Le retrait supérieur évite que le contour
        # arrondi de la première carte ne touche le bas de l'onglet actif.
        tools_layout.setContentsMargins(16, 10, 16, 10)
        # Une seule consigne pour toute la page (elle était répétée sur les six cartes).
        self.tools_hint = QLabel(self.tr(
            "Ouvrez un module, ou déposez-y des fichiers ou des dossiers."))
        self.tools_hint.setWordWrap(True)
        self.tools_hint.setProperty("role", "muted")
        self.tools_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # La consigne occupe toute la colonne centrée : son texte ne se coupe plus
        # quand la colonne est réduite, et reste centré au-dessus des cartes.
        self.tools_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Colonne centrale : 3/4 de la largeur, gouttière élargie entre les deux
        # colonnes, puis centrage vertical dans l'espace restant.
        cards = self._build_cards()
        self._cards_center = _CardsColumn()
        center_layout = QVBoxLayout(self._cards_center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        center_layout.addWidget(self.tools_hint)
        center_layout.addLayout(cards)
        centered = QHBoxLayout()
        centered.setContentsMargins(0, 0, 0, 0)
        centered.setSpacing(0)
        centered.addStretch(1)
        centered.addWidget(self._cards_center, 6)
        centered.addStretch(1)
        tools_layout.addStretch(1)
        tools_layout.addLayout(centered)
        tools_layout.addStretch(1)

        self.tools_home = QScrollArea()
        self.tools_home.setWidgetResizable(True)
        self.tools_home.setFrameShape(QScrollArea.Shape.NoFrame)
        self.tools_home.setWidget(tools_content)
        self.tools_home.viewport().installEventFilter(self)
        self._paint_tools_surface()
        QTimer.singleShot(0, self._fit_tools_cards)

        self.tools_page = QTabWidget()
        self.tools_page.setObjectName("ToolsTabs")
        self.tools_page.setTabBar(_ToolsTabBar(self.tools_page))   # barre dimensionnée au contenu
        self.tools_page.setTabsClosable(True)
        self.tools_page.setMovable(True)                          # glisser un onglet pour le réordonner
        tab_bar = self.tools_page.tabBar()
        tab_bar.tabMoved.connect(self._on_tools_tab_moved)
        tab_bar.selectionChanged.connect(self._on_tab_selection_changed)
        tab_bar.closeSelectedRequested.connect(self._close_selected_tabs)
        tab_bar.closeCurrentRequested.connect(self._close_current_tab)
        self.tools_page.currentChanged.connect(self._on_tools_current_changed)
        # Le style dessine une ligne claire au-dessus de la barre quand plusieurs onglets
        # sont ouverts ; elle est visible dès qu'un module est ouvert en arrière-plan.
        tab_bar.setDrawBase(False)
        tab_bar.setMouseTracking(True)
        tab_bar.setToolTip(self.tr(
            "Maj+clic ou Maj+flèches : sélectionner plusieurs onglets. "
            "Suppr : fermer l'onglet actif ou la sélection. "
            "Glissez un onglet sélectionné pour déplacer le groupe."))
        tab_bar.installEventFilter(self)
        self.tools_page.addTab(self.tools_home, self.tr("Tous les outils"))
        tab_bar.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        self.tools_page.tabCloseRequested.connect(self._close_module_tab)
        self.pages.addWidget(self.tools_page)

        # Les trois pages secondaires ne sont construites qu'à leur première visite :
        # au démarrage, elles ne coûtent ni widgets ni mise en page (~0,25 s gagnées).
        # Une page vide garde leur place dans la pile jusqu'à leur remplacement.
        self._history_store = history_store
        self._dashboard: Dashboard | None = None
        self._tips_page: TipsPage | None = None
        self._preferences_page: PreferencesPage | None = None
        for _index in (self.PAGE_ACTIVITY, self.PAGE_TIPS, self.PAGE_PREFERENCES):
            self.pages.addWidget(QWidget())

    # ── pages secondaires (construites à la demande) ─────────────────────────
    @property
    def dashboard(self) -> Dashboard:
        """Page « Journaux et historique », construite à sa première ouverture."""
        if self._dashboard is None:
            page = Dashboard(self._specs, self._history_store)
            page.binariesRefreshed.connect(self._refresh_binary_state)
            self._dashboard = page
            self.pages.replaceWidget(self.PAGE_ACTIVITY, page)
        return self._dashboard

    @property
    def tips_page(self) -> TipsPage:
        """Page « Conseils », construite à sa première ouverture."""
        if self._tips_page is None:
            page = TipsPage(self._specs)
            self._tips_page = page
            self.pages.replaceWidget(self.PAGE_TIPS, page)
        return self._tips_page

    @property
    def preferences_page(self) -> PreferencesPage:
        """Page « Préférences », construite à sa première ouverture."""
        if self._preferences_page is None:
            page = PreferencesPage(allow_restart=True)
            page.saved.connect(self._on_preferences_saved)
            page.restartRequested.connect(self.restart)
            self._preferences_page = page
            self.pages.replaceWidget(self.PAGE_PREFERENCES, page)
        return self._preferences_page

    def _ensure_page(self, index: int) -> None:
        """Construit la page secondaire demandée avant de l'afficher."""
        if index == self.PAGE_ACTIVITY:
            _page = self.dashboard
        elif index == self.PAGE_TIPS:
            _page = self.tips_page
        elif index == self.PAGE_PREFERENCES:
            _page = self.preferences_page
        else:
            _page = None
        del _page

    # ── construction ──
    def _build_header(self) -> QHBoxLayout:
        self.logo_label = QLabel()
        self.logo_label.setObjectName("AtlasWordmark")
        self.logo_label.setFixedSize(self._logo_size, self._logo_size)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_wordmark()
        self.title_label = QLabel(self.tr("Azimut"))
        self.title_label.setObjectName("AtlasTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._set_title_font(self.HEADER_TITLE)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        for label in (self.logo_label, self.title_label):
            label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            label.installEventFilter(self)
        self.subtitle_label = _HeaderDetailLabel(
            self.tr("Le couteau suisse pour convertir, dédoublonner et ranger vos médias"))
        self.version_label = _HeaderDetailLabel(
            self.tr("Version {version}").format(version=__version__))
        # Les détails gardent leur largeur naturelle sans imposer de largeur minimale :
        # la fenêtre peut rétrécir assez pour déclencher leur masquage automatique, mais
        # la barre leur donne toute leur place quand elle existe.
        for label in (self.subtitle_label, self.version_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.titles_widget = QWidget()
        self._titles_layout = QVBoxLayout(self.titles_widget)
        self._titles_layout.setContentsMargins(0, 0, 0, 0)
        self._titles_layout.setSpacing(2)
        self._titles_layout.addWidget(self.title_label)
        self._titles_layout.addWidget(self.subtitle_label)
        self._titles_layout.addWidget(self.version_label)

        # Emplacement du sélecteur de pages : dans l'en-tête quand l'agencement est
        # regroupé, vide et masqué dans l'agencement classique.
        self.header_nav_host = QWidget()
        self._header_nav_layout = QHBoxLayout(self.header_nav_host)
        self._header_nav_layout.setContentsMargins(0, 0, 0, 0)
        self._header_nav_layout.setSpacing(0)
        self.header_nav_host.hide()

        # Zone centrale laissée libre entre le nom et la navigation. Les notifications
        # y naissent en capsule compacte au lieu de flotter au-dessus de l'en-tête ;
        # au repos elle est transparente et ne prend aucun relief.
        self.notification_lane = QWidget()
        self.notification_lane.setObjectName("AtlasNotificationLane")
        self.notification_lane.setMinimumSize(0, 0)
        self.notification_lane.setSizePolicy(QSizePolicy.Policy.Ignored,
                                             QSizePolicy.Policy.Expanding)
        self.notification_lane.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        header = QHBoxLayout()
        header.setContentsMargins(9, 0, 9, 0)
        header.addWidget(self.titles_widget, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.notification_lane, 1)
        header.addWidget(self.header_nav_host, 0, Qt.AlignmentFlag.AlignTop)
        header.insertWidget(0, self.logo_label, 0, Qt.AlignmentFlag.AlignTop)
        header.insertSpacing(1, 4)
        return header

    def _build_menu(self) -> None:
        """Menu d'application : les commandes d'Atlas ne vivent pas seulement dans l'en-tête."""
        bar = self.menuBar()
        app_menu = bar.addMenu(self.tr("Azimut"))
        about = app_menu.addAction(self.tr("À propos d'Azimut"))
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self._show_about)
        prefs = app_menu.addAction(self.tr("Préférences…"))
        prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs.setShortcut("Ctrl+,")
        prefs.triggered.connect(self._on_preferences)
        app_menu.addSeparator()
        quit_action = app_menu.addAction(self.tr("Quitter"))
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(lambda: QApplication.quit())

        file_menu = bar.addMenu(self.tr("Fichier"))
        close_action = file_menu.addAction(self.tr("Fermer la fenêtre"))
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)

        view_menu = bar.addMenu(self.tr("Présentation"))
        tools = view_menu.addAction(self.tr("Tous les outils"))
        tools.triggered.connect(lambda: self.show_page(self.PAGE_TOOLS))
        activity = view_menu.addAction(self.tr("Journaux et historique"))
        activity.triggered.connect(lambda: self.show_page(self.PAGE_ACTIVITY))
        view_menu.addSeparator()
        top_layout = view_menu.addAction(self.tr("Navigation dans l'en-tête"))
        top_layout.setCheckable(True)
        top_layout.setChecked(self._top_layout == self.TOP_LAYOUT_MERGED)
        top_layout.setToolTip(self.tr(
            "Regroupe « Outils » et « Journaux et historique » dans la barre supérieure. "
            "Décochez pour revenir à l'agencement précédent."))
        top_layout.toggled.connect(self._on_top_layout_toggled)
        self.top_layout_action = top_layout
        view_menu.addSeparator()
        tips = view_menu.addAction(self.tr("Conseils de démarrage…"))
        tips.triggered.connect(self._on_tips)
        theme = view_menu.addAction(self.tr("Basculer entre thème sombre et clair"))
        theme.setShortcut(QKeySequence("Ctrl+T"))
        theme.triggered.connect(lambda: ThemeManager.instance().toggle())
        view_menu.addSeparator()
        zoom_in = view_menu.addAction(self.tr("Agrandir le texte"))
        zoom_in.setShortcut(QKeySequence("Ctrl++"))
        zoom_in.triggered.connect(lambda: ThemeManager.instance().set_zoom(ThemeManager.instance().zoom() + 0.1))
        zoom_out = view_menu.addAction(self.tr("Réduire le texte"))
        zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out.triggered.connect(lambda: ThemeManager.instance().set_zoom(ThemeManager.instance().zoom() - 0.1))

        window_menu = bar.addMenu(self.tr("Fenêtre"))
        minimize = window_menu.addAction(self.tr("Réduire"))
        minimize.setShortcut(QKeySequence("Ctrl+M"))
        minimize.triggered.connect(self.showMinimized)
        zoom_window = window_menu.addAction(self.tr("Zoom"))
        zoom_window.triggered.connect(
            lambda: self.showNormal() if self.isMaximized() else self.showMaximized())

        help_menu = bar.addMenu(self.tr("Aide"))
        help_action = help_menu.addAction(self.tr("Conseils de démarrage"))
        help_action.triggered.connect(self._on_tips)

    def _show_about(self) -> None:
        # Hors du f-string : lupdate n'extrait pas un tr() imbriqué dans une f-string.
        about_text = self.tr("Le couteau suisse de vos médias.")
        QMessageBox.about(
            self, self.tr("À propos d'Azimut"),
            f"<b>{APP_NAME}</b> {__version__}<br>{about_text}")

    def _install_module_shortcuts(self) -> None:
        """Ctrl+1…9 ouvre le n-ième module ; Ctrl+Maj+1…9 l'ouvre en arrière-plan."""
        for position, spec in enumerate(self._specs[:9], start=1):
            QShortcut(QKeySequence(f"Ctrl+{position}"), self,
                      activated=lambda sid=spec.id: self.open_module(sid))
            QShortcut(QKeySequence(f"Ctrl+Shift+{position}"), self,
                      activated=lambda sid=spec.id: self.open_module(sid, background=True))

    def _apply_wordmark(self) -> None:
        """Affiche l'emblème seul ; le nom est porté par le titre à côté."""
        if self._header_progress >= 0.5:
            # En-tête compact : l'emblème linéaire est trop fin, on garde le pictogramme historique.
            self.logo_label.setPixmap(icons.app_icon().pixmap(self._logo_size, self._logo_size))
            return
        variant = ThemeManager.instance().theme()
        if self._wordmark.isNull() or self._wordmark_theme != variant:
            self._wordmark = icons.brand_pixmap("azimut_emblem", self.HEADER_WORDMARK, variant)
            self._wordmark_theme = variant
        if self._wordmark.isNull():
            self.logo_label.setPixmap(icons.pixmap("globe", min(34, self._logo_size), "primary"))
        else:
            self.logo_label.setPixmap(self._wordmark.scaled(
                self._logo_size, self._logo_size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _set_title_font(self, size: int) -> None:
        """Applique le lettrage du logotype au titre, à la taille courante."""
        if size == self._title_size:
            return
        self._title_size = size
        self.title_label.setFont(brand_font(size))

    def _apply_header_progress(self, progress: float) -> None:
        """Applique la taille compacte du logo et du titre pour une valeur de 0 à 1."""
        self._header_progress = max(0.0, min(1.0, progress))
        # Le padding vertical diminue avec la barre : la hauteur compacte perd
        # exactement un cinquième de sa hauteur, sans toucher au contenu.
        pad = round(self.HEADER_VPAD
                    + (self.HEADER_VPAD_COMPACT - self.HEADER_VPAD) * self._header_progress)
        if self.header_bar.contentsMargins().top() != pad:
            self.header_bar.setContentsMargins(16, pad, 16, pad)
        # Le nom reste « Azimut » : seule sa taille suit le compactage de l'en-tête.
        # En agencement regroupé, la barre garde toute la largeur pour la navigation.
        inset = (round(self._header_compact_inset() * self._header_progress)
                 if self._top_layout == self.TOP_LAYOUT_CLASSIC else 0)
        margins = self._header_host_layout.contentsMargins()
        if margins.left() != inset or margins.right() != inset:
            self._header_host_layout.setContentsMargins(inset, 0, inset, 0)
        size = round(self.HEADER_WORDMARK
                     + (self.HEADER_COMPACT - self.HEADER_WORDMARK) * self._header_progress)
        if size != self._logo_size:
            self._logo_size = size
            self.logo_label.setFixedSize(size, size)
            self._apply_wordmark()
        self._set_title_font(round(self.HEADER_TITLE
                                   + (self.HEADER_TITLE_COMPACT - self.HEADER_TITLE) * self._header_progress))
        # En-tête compact : « Azimut » se cale sur l'axe du logo réduit. Le retrait suit
        # la taille du logo, donc le titre glisse au lieu de sauter quand l'animation part.
        gap = max(0.0, (size - self.title_label.sizeHint().height()) / 2.0)
        self._titles_layout.setContentsMargins(0, round(gap * self._header_progress), 0, 0)
        show_details = self._header_progress <= 0.1 and self._header_details_fit()
        for label in (self.subtitle_label, self.version_label):
            if label.isHidden() == show_details:
                label.setVisible(show_details)

    def _header_details_fit(self) -> bool:
        """Le sous-titre et la version tiennent-ils à côté de la navigation ?"""
        layout = self.header_bar.layout()
        if layout is None:
            return False
        bar_margins = self.header_bar.contentsMargins()
        layout_margins = layout.contentsMargins()
        chrome = (bar_margins.left() + bar_margins.right()
                  + layout_margins.left() + layout_margins.right()
                  + layout.spacing() * 3 + scaled(4) + scaled(8))
        switch = (self.page_switch.sizeHint().width()
                  if self._top_layout == self.TOP_LAYOUT_MERGED else 0)
        labels = max(self.title_label.sizeHint().width(),
                     self.subtitle_label.sizeHint().width(),
                     self.version_label.sizeHint().width())
        needed = self.logo_label.width() + labels + switch + chrome
        return self.header_bar.width() >= needed
    def _header_compact_inset(self) -> int:
        """Marge latérale compacte : réduit de moitié la largeur de la barre."""
        base = scaled(self.HEADER_INSET)
        width = max(0, self.header_host.width() - 2 * base)
        return round(base + width * self.HEADER_COMPACT_SHRINK / 2.0)

    def _sync_header(self, animate: bool = True) -> None:
        """Compacte l'en-tête dès qu'un onglet de module est ouvert.

        Le sélecteur de pages reste ancré dans sa barre : le seul mouvement est
        celui de l'en-tête, ce qui évite le saut de mise en page du sélecteur
        lorsqu'il changeait de parent à la fin du compactage.
        """
        animate = animate and not ThemeManager.instance().reduce_motion()
        compact = self.tools_page.count() > 1
        target = 1.0 if compact else 0.0
        if compact == self._header_compact:
            if not animate:
                self._apply_header_progress(target)
            return
        self._header_compact = compact
        animation = self._header_animation
        animation.stop()
        if not animate:
            self._apply_header_progress(target)
            return
        animation.setStartValue(self._header_progress)
        animation.setEndValue(target)
        animation.start()

    def _build_page_bar(self) -> QWidget:
        """Boutons de navigation entre les pages d'Azimut."""
        self.page_bar_host = QWidget()
        bar = QHBoxLayout(self.page_bar_host)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self._page_bar_layout = bar
        self._page_bar_layout.setContentsMargins(0, 0, 0, 0)
        self.page_group = QButtonGroup(self)
        self.page_group.setExclusive(True)
        self.page_switch = GlassSegmentedBar()
        self.page_switch.setAccessibleName(self.tr("Navigation d'Azimut"))
        self._page_icons = {
            self.PAGE_TOOLS: "grid",
            self.PAGE_ACTIVITY: "list",
            self.PAGE_TIPS: "info",
            self.PAGE_PREFERENCES: "gears",
        }
        # Le catalogue existant connaît « Préférences… » (menu) ; pour l'onglet, on
        # retire l'ellipse afin de garder le libellé court et traduit.
        prefs_label = self.tr("Préférences…").removesuffix("…")
        for index, text, tip in (
            (self.PAGE_TOOLS, self.tr("Outils"), self.tr("Afficher les outils Azimut")),
            (self.PAGE_ACTIVITY, self.tr("Journaux et historique"),
             self.tr("Afficher l'historique, le journal et les binaires")),
            (self.PAGE_TIPS, self.tr("Conseils"),
             self.tr("Afficher l'ordre conseillé pour traiter un export Google Photos")),
            (self.PAGE_PREFERENCES, prefs_label,
             self.tr("Apparence, langue et outils externes")),
        ):
            button = LiquidButton(text)
            button.setObjectName("AtlasPageButton")
            button.setCheckable(True)
            button.setProperty("kind", "secondary")
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            self.page_group.addButton(button, index)
            self.page_buttons[index] = button
            self.page_switch.add_button(button)
        bar.addWidget(self.page_switch)
        bar.addStretch(1)
        self.show_page(self.PAGE_TOOLS)
        return self.page_bar_host

    def show_page(self, index: int) -> None:
        """Affiche l'une des quatre pages d'Azimut."""
        if not 0 <= index < self.pages.count():
            return
        self._ensure_page(index)
        self.pages.setCurrentIndex(index)
        if index == self.PAGE_PREFERENCES:
            self.preferences_page.refresh()
        self._refresh_page_buttons()

    # ── agencement du haut ──────────────────────────────────────────────────
    def _saved_top_layout(self) -> str:
        """Agencement mémorisé ; « merged » par défaut, valeur inconnue ignorée."""
        mode = Preferences.instance().get("top_layout", self.TOP_LAYOUT_MERGED)
        return mode if mode in (self.TOP_LAYOUT_MERGED, self.TOP_LAYOUT_CLASSIC) else self.TOP_LAYOUT_MERGED

    def _apply_top_layout(self) -> None:
        """Place le sélecteur de pages dans l'en-tête ou dans sa propre rangée."""
        merged = self._top_layout == self.TOP_LAYOUT_MERGED
        if merged:
            self._page_bar_layout.removeWidget(self.page_switch)
            self._header_nav_layout.addWidget(self.page_switch, 0, Qt.AlignmentFlag.AlignTop)
            # Relâcher les marges avant d'afficher : sinon la barre compacte, encore
            # rétrécie, imposerait sa largeur minimale et la fenêtre s'agrandirait.
            self._apply_header_progress(self._header_progress)
            self.header_nav_host.show()
            self.page_bar_host.hide()
        else:
            self._header_nav_layout.removeWidget(self.page_switch)
            self.page_bar_host.show()
            self._page_bar_layout.insertWidget(0, self.page_switch)
            self.header_nav_host.hide()
            self._apply_header_progress(self._header_progress)

    def _remember_merged_layout_extra(self) -> None:
        """Largeur fixe ajoutée par la navigation dans l'en-tête (hors sélecteur).

        Cette valeur reste stable quand le zoom change : le sélecteur, lui, s'élargit
        avec la police. On peut donc recalculer le seuil à chaque zoom sans remettre
        temporairement la navigation dans l'en-tête.
        """
        if self._top_layout != self.TOP_LAYOUT_MERGED or not hasattr(self, "page_switch"):
            return
        switch = self.page_switch.sizeHint().width()
        total = self.minimumSizeHint().width()
        if switch > 0 and total > switch:
            self._merged_layout_extra = total - switch

    def _merged_layout_breakpoint(self) -> int:
        """Largeur sous laquelle le sélecteur dans l'en-tête devient trop à l'étroit."""
        return (self.page_switch.sizeHint().width() + max(0, self._merged_layout_extra)
                + scaled(self.AUTO_LAYOUT_GUTTER))

    def _sync_top_layout_action(self) -> None:
        """Le menu reflète l'agencement effectif, y compris le repli automatique."""
        action = getattr(self, "top_layout_action", None)
        if action is None:
            return
        wanted = self._top_layout == self.TOP_LAYOUT_MERGED
        if action.isChecked() != wanted:
            action.blockSignals(True)
            action.setChecked(wanted)
            action.blockSignals(False)

    def _show_layout(self, mode: str) -> None:
        """Applique un agencement effectif sans toucher au choix enregistré."""
        if mode == self._top_layout:
            return
        self._top_layout = mode
        self._apply_top_layout()
        self._sync_header(animate=False)
        self._remember_merged_layout_extra()
        self._sync_top_layout_action()

    def _on_zoom_changing(self, zoom: float) -> None:
        """Anticipe un zoom qui rendrait l'en-tête trop large pour la fenêtre.

        La police et la feuille de style sont appliquées juste après ce signal. Si le
        sélecteur de pages doit s'élargir, on repasse avant cela à l'ancien agencement :
        la fenêtre étroite n'est ainsi pas agrandie de force pendant le changement.
        """
        if (not self.isVisible() or self._layout_preference != self.TOP_LAYOUT_MERGED
                or zoom < self.AUTO_FALLBACK_MIN_ZOOM):
            return
        current = max(0.01, ThemeManager.instance().zoom())
        switch = self.page_switch.sizeHint().width() * zoom / current
        estimated = (round(switch) + max(0, self._merged_layout_extra)
                     + scaled(self.AUTO_LAYOUT_GUTTER))
        if self.width() < estimated:
            self._show_layout(self.TOP_LAYOUT_CLASSIC)

    def _update_layout_fallback(self, *, force: bool = False, width: int | None = None) -> None:
        """Replie automatiquement la navigation quand l'en-tête manque de largeur.

        Le repli est temporaire : il ne modifie pas la préférence « Navigation dans
        l'en-tête ». Dès que la fenêtre retrouve assez de place, l'agencement regroupé
        revient, avec une petite hystérésis pour éviter les allers-retours au seuil.
        Une fenêtre cachée est ignorée : les anciennes fenêtres gardent leur géométrie
        et ne recalculent pas leur agencement à chaque changement de thème.

        `width` remplace la largeur courante : à l'ouverture, Qt a déjà pu élargir la
        fenêtre jusqu'au minimum de l'en-tête regroupé, et seule la largeur demandée
        dit si cet agencement tenait réellement.
        """
        if (not force and not self.isVisible()) or self._layout_update_in_progress \
                or not hasattr(self, "page_switch"):
            return
        preference = self._layout_preference
        if preference == self.TOP_LAYOUT_CLASSIC:
            return
        if self._top_layout == self.TOP_LAYOUT_MERGED:
            self._remember_merged_layout_extra()
        threshold = self._merged_layout_breakpoint()
        zoom = ThemeManager.instance().zoom()
        fallback_enabled = zoom >= self.AUTO_FALLBACK_MIN_ZOOM
        available = self.width() if width is None else width
        # À 100 %, le seuil du zoom ne s'applique pas : on ne se replie que si la place
        # demandée est plus étroite que l'en-tête regroupé lui-même. Mieux vaut l'ancien
        # agencement, qui tient dans 581 px, qu'une fenêtre élargie de force.
        merged_minimum = (self.minimumSizeHint().width()
                          if self._top_layout == self.TOP_LAYOUT_MERGED else 0)
        self._layout_update_in_progress = True
        try:
            if self._top_layout == self.TOP_LAYOUT_MERGED:
                if available < merged_minimum or (fallback_enabled and available < threshold):
                    self._show_layout(self.TOP_LAYOUT_CLASSIC)
            else:
                restore = threshold + (scaled(self.AUTO_LAYOUT_RESTORE_MARGIN)
                                       if fallback_enabled else 0)
                if self.width() >= restore:
                    self._show_layout(self.TOP_LAYOUT_MERGED)
        finally:
            self._layout_update_in_progress = False

    def _set_top_layout(self, mode: str, *, persist: bool = True) -> None:
        """Bascule entre l'agencement regroupé et l'ancien agencement (réversible)."""
        mode = mode if mode in (self.TOP_LAYOUT_MERGED, self.TOP_LAYOUT_CLASSIC) else self.TOP_LAYOUT_MERGED
        changed = mode != self._layout_preference
        self._layout_preference = mode
        self._top_layout = mode
        self._apply_top_layout()
        self._sync_header(animate=False)
        self._remember_merged_layout_extra()
        self._sync_top_layout_action()
        if persist and changed:
            Preferences.instance().set("top_layout", mode)
        self._update_layout_fallback()

    def _on_top_layout_toggled(self, checked: bool) -> None:
        self._set_top_layout(self.TOP_LAYOUT_MERGED if checked else self.TOP_LAYOUT_CLASSIC)

    def _refresh_page_buttons(self) -> None:
        """Synchronise l'état visuel des boutons avec la page affichée."""
        current = self.pages.currentIndex()
        for index, button in self.page_buttons.items():
            active = index == current
            button.setChecked(active)
            button.setProperty("kind", "primary" if active else "secondary")
            name = self._page_icons.get(index, "grid")
            button.setIcon(icons.icon(name, "text" if active else "muted", scaled(18)))
            repolish(button)
        switch = getattr(self, "page_switch", None)
        if switch is not None:
            switch.set_current(current)

    def _build_cards(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(12)
        self._cards_grid = grid
        self._card_order: list[ModuleCard] = []
        entries: list[tuple[ModuleSpec, str]] = [(s, "") for s in self._specs]
        entries += [(_placeholder_spec(mid), msg) for mid, msg in self._errors.items()
                    if mid not in self._spec_by_id]
        if not entries:
            grid.addWidget(QLabel(self.tr("Aucun module disponible.")), 0, 0)
        for spec, error in entries:
            card = ModuleCard(spec)
            if error:
                card.set_error(error)
            card.openRequested.connect(self.open_module)
            card.backgroundOpenRequested.connect(partial(self.open_module, background=True))
            card.pathsDropped.connect(self.open_module)
            self.cards[spec.id] = card
            self._card_order.append(card)
        self._card_columns = 0
        self._card_row_minimums = 0
        self._place_cards(self.COLUMNS)
        return grid

    def _place_cards(self, columns: int) -> None:
        """Range les cartes en `columns` colonnes, sans toucher à leur ordre."""
        grid = getattr(self, "_cards_grid", None)
        if grid is None or columns == self._card_columns:
            return
        while grid.count():
            grid.takeAt(0)
        for index, card in enumerate(self._card_order):
            # Pas d'alignement vertical : la hauteur de rangée vaut celle des cartes
            # (voir `_card_row_minimums`), sinon Qt centre sur un indice de taille
            # différent de la taille figée et décale les cartes de quelques pixels.
            grid.addWidget(card, index // columns, index % columns,
                           Qt.AlignmentFlag.AlignHCenter)
        # Aucune colonne ne s'étire : la largeur vient du format au nombre d'or.
        for column in range(max(columns, self._card_columns)):
            grid.setColumnStretch(column, 0)
        self._card_columns = columns

    # ── style ──
    def _paint_tools_surface(self) -> None:
        """Peint la page des outils avec la surface de l'onglet actif.

        Le contenu du défilement repeint sa propre palette par-dessus le viewport : on
        applique donc la surface aux deux, ce qui couvre aussi la bande de la barre de
        défilement et fait disparaître la rupture sous l'onglet.
        """
        tools_home = getattr(self, "tools_home", None)
        if tools_home is None:
            return
        surface = QColor(token("surface"))
        for target in (tools_home, tools_home.viewport(), tools_home.widget()):
            if target is None:
                continue
            target.setBackgroundRole(QPalette.ColorRole.Base)
            target.setAutoFillBackground(True)
            palette = target.palette()
            palette.setColor(QPalette.ColorRole.Base, surface)
            palette.setColor(QPalette.ColorRole.Window, surface)
            target.setPalette(palette)

    def _on_accessibility_changed(self) -> None:
        """Le verre et les animations changent sans changement de thème : on recale tout."""
        if ThemeManager.instance().reduce_motion():
            self._tab_icon_timer.stop()
            for module_id in list(self._tab_busy):
                self._set_tab_icon(module_id)          # icône à taille normale, sans respiration
        self._restyle()
        self._sync_header(animate=False)

    def _restyle(self, _name: str = "") -> None:
        self._apply_wordmark()
        self._paint_tools_surface()
        self._refresh_page_buttons()
        self._refresh_module_tab_icons()
        self._refresh_tab_close_icons()
        bar = self.tools_page.tabBar()
        for i in range(bar.count()):                 # le zoom change la largeur des onglets
            bar.setTabText(i, bar.tabText(i))
        self._refresh_tab_selection_text()
        muted = f"color: {token('muted')};"
        self.subtitle_label.setStyleSheet(muted)
        self.version_label.setStyleSheet(muted + f" font-size: {caption_point_size():g}pt;")
        self.warning_banner.setStyleSheet(
            f"background: {token('warn_bg')}; color: {token('warn_txt')}; "
            "border-radius: 8px; padding: 8px 12px;")
        self._apply_header_progress(self._header_progress)   # le zoom change la place disponible
        self._update_layout_fallback()
        QTimer.singleShot(0, self._fit_tools_cards)

    def _fit_tools_cards(self) -> None:
        """Donne aux cartes le format au nombre d'or qui tient dans la page.

        La hauteur suit l'espace vertical disponible et la largeur en découle
        (largeur / φ) : les cartes ne s'aplatissent plus en bandeau sur un grand écran
        et ne se tassent plus sur un écran court. Le nombre de colonnes augmente tant
        que la carte reste au-dessus de la largeur que réclame son contenu : six outils
        passent ainsi de trois rangées de deux à deux rangées de trois sur un grand écran.
        """
        # L'appel est différé : la fenêtre ou une carte peut avoir été détruite entre
        # l'émission du signal et l'exécution du callback (fermeture, changement de test).
        from shiboken6 import isValid

        tools_home = getattr(self, "tools_home", None)
        grid = getattr(self, "_cards_grid", None)
        center = getattr(self, "_cards_center", None)
        cards = getattr(self, "_card_order", None)
        if (not isValid(self) or tools_home is None or grid is None or center is None
                or not cards or not isinstance(cards, list)
                or not isValid(tools_home) or not isValid(grid) or not isValid(center)):
            return
        viewport_height = tools_home.viewport().height()
        page = tools_home.widget()
        if viewport_height <= 0 or page is None or page.width() <= 0:
            return
        margins = page.layout().contentsMargins()
        # La hauteur allouée comprend le padding du style, contrairement à
        # heightForWidth : on prend la hauteur réelle une fois la page posée.
        # Hauteur utile de la consigne : celle que le texte réclame, jamais un étirement.
        # Tant que la grille n'est pas posée, l'étiquette peut s'étirer et annoncer 250 px,
        # ce qui rétrécissait toutes les cartes (bouton « Ouvrir » rogné). Mais elle peut
        # aussi garder la hauteur de son texte mis en page avant un rétrécissement : on
        # borne donc la valeur lue par ce que le texte et sa largeur autorisent.
        hint_height = max(self.tools_hint.heightForWidth(self.tools_hint.width()),
                          self.tools_hint.sizeHint().height())
        space = (viewport_height - margins.top() - margins.bottom()
                 - hint_height - center.layout().spacing())
        geometry = self._golden_grid(len(cards), round(page.width() * _CardsColumn.WIDTH_RATIO), space)
        if geometry is None or geometry == getattr(self, "_fitted_cards", None):
            return
        self._fitted_cards = geometry
        columns, width, height = geometry
        self._place_cards(columns)
        center.set_preferred_width(columns * width + grid.horizontalSpacing() * (columns - 1))
        for card in cards:
            if isValid(card):
                card.set_card_geometry(width, height)
        # La grille est un layout imbriqué : sans hauteur de rangée explicite, la colonne
        # reçoit parfois moins que la hauteur des cartes (indice de taille périmé) et les
        # rangées se chevauchent, rognant le bouton « Ouvrir ». On fige donc les rangées à
        # la hauteur des cartes et on réserve la hauteur du bloc ; si elle dépasse la page,
        # le défilement prend le relais.
        rows = math.ceil(len(cards) / columns)
        for row in range(self._card_row_minimums):
            grid.setRowMinimumHeight(row, 0)             # colonnes changées : pas de rangée fantôme
        for row in range(rows):
            grid.setRowMinimumHeight(row, height)
        self._card_row_minimums = rows
        center.setMinimumHeight(hint_height + center.layout().spacing()
                                + rows * height + grid.verticalSpacing() * (rows - 1))
        center.layout().invalidate()
        center.layout().activate()

    def _golden_grid(self, count: int, cap: int, space: int) -> tuple[int, int, int] | None:
        """Choisit (colonnes, largeur, hauteur) au nombre d'or pour `count` cartes.

        La hauteur de chaque carte vient de l'espace vertical, sa largeur en découle :
        le format reste doré quelle que soit la fenêtre. Parmi les dispositions qui
        laissent les cartes au-dessus de leur largeur et de leur hauteur minimales, on
        garde celle qui remplit le plus la largeur, à condition qu'elle occupe au moins
        la moitié de la hauteur — sinon le bloc flotte au milieu d'une page vide. Si
        aucune ne tient (fenêtre courte, zoom élevé), on retombe sur les deux colonnes
        d'autrefois remplies par la hauteur : la page défile alors comme avant.
        """
        if count <= 0 or cap <= 0 or space <= 0:
            return None
        grid = self._cards_grid
        gap = grid.horizontalSpacing()
        row_gap = grid.verticalSpacing()
        max_width = scaled(ModuleCard.CARD_MAX_WIDTH)
        min_height = scaled(ModuleCard.CARD_MIN_HEIGHT)
        max_height = scaled(ModuleCard.CARD_MAX_HEIGHT)
        # Largeur plancher : le contenu de la carte la plus large, plus ses marges. Elle
        # suit le zoom et les textes traduits, sans figer un seuil arbitraire.
        content = max((card.minimumSizeHint().width() for card in self._card_order), default=0)
        min_width = max(scaled(ModuleCard.CARD_MIN_WIDTH), content + scaled(32))
        candidates: list[tuple[int, int, int, float]] = []
        for columns in range(1, min(self.MAX_COLUMNS, count) + 1):
            rows = math.ceil(count / columns)
            cell_height = (space - row_gap * (rows - 1)) // rows
            cell_width = (cap - gap * (columns - 1)) // columns
            width = min(cell_width, max_width, round(cell_height * GOLDEN_RATIO))
            height = min(cell_height, max_height, round(width / GOLDEN_RATIO))
            # Hauteur réellement réclamée par le contenu à cette largeur (les taglines
            # se replient) : elle grandit avec le zoom, contrairement à un seuil figé.
            needed = max((card.content_height(width) for card in self._card_order), default=0)
            if width < min_width or height < needed - scaled(ModuleCard.CONTENT_SLACK):
                continue
            block_height = rows * height + row_gap * (rows - 1)
            candidates.append((columns, width, height, block_height / space))
        if candidates:
            # Une grille large ne doit pas laisser la page à moitié vide en hauteur.
            dense = [candidate for candidate in candidates if candidate[3] >= 0.5]
            if dense:
                columns, width, height, _fill = max(dense, key=lambda c: c[0])
                return columns, width, height
            columns, width, height, _fill = max(candidates, key=lambda c: c[3])
            return columns, width, height
        columns = min(self.COLUMNS, count)
        rows = math.ceil(count / columns)
        width = max(1, min(max_width, (cap - gap * (columns - 1)) // columns))
        needed = max((card.content_height(width) for card in self._card_order), default=0)
        # Le repli ne comprime pas la carte sous ce que son contenu réclame : c'est la
        # page qui défile, jamais le texte qui se coupe.
        height = max(min_height, min(max_height,
                                    max(needed - scaled(ModuleCard.CONTENT_SLACK),
                                        (space - row_gap * (rows - 1)) // rows)))
        return columns, width, height

    # ── binaires ──
    def _update_banner(self) -> None:
        """Avertissement non bloquant si un binaire requis est absent."""
        missing: dict[str, list[str]] = {}
        for spec in self._specs:
            for name in spec.requires:
                info = safe_resolve(name)
                if info is None or not info.available:
                    missing.setdefault(name, []).append(spec.name)
        if not missing:
            self.warning_banner.hide()
            self.warning_host.hide()
            return
        parts = [f"{name} ({', '.join(users)})" for name, users in missing.items()]
        self.warning_banner.setText(self.tr(
            "Binaire(s) requis introuvable(s) : {list}. Indiquez leur chemin dans « Préférences… » "
            "ou déposez-les dans le dossier bin/.").format(list=" · ".join(parts)))
        self.warning_banner.show()
        self.warning_host.show()

    def _refresh_binary_state(self) -> None:
        for card in self.cards.values():
            card.refresh_binaries()
        self._update_banner()

    def _on_tips(self) -> None:
        """Affiche le guide de démarrage dans la navigation principale."""
        self.show_page(self.PAGE_TIPS)

    def _show_personal_note(self) -> None:
        """Affiche la note personnelle depuis le titre ou le logo, sans indice visuel.

        Le texte est long : il apparaît dans une notification permanente, dans le style
        des toasts d'Azimut, que l'utilisateur ferme lui-même.
        """
        text = personal_note_text()
        if not text:
            return
        notify_persistent(self, self.tr("Merci !"), text)

    def _on_preferences(self) -> None:
        """Affiche les préférences dans la navigation principale."""
        self.show_page(self.PAGE_PREFERENCES)

    def _on_preferences_saved(self) -> None:
        """Une sauvegarde dans l'onglet Préférences rafraîchit les états de binaires."""
        if self._dashboard is not None:
            self._dashboard.binaries_tab.reload()
        self._refresh_binary_state()

    # ── ouverture des modules ──
    def _module_tab_index(self, window: QWidget) -> int:
        """Index de l'onglet d'un module, ou -1 si la fenêtre n'est plus dans Azimut."""
        try:
            return self.tools_page.indexOf(window)
        except RuntimeError:                   # objet C++ déjà détruit
            return -1

    def _set_tab_icon(self, module_id: str, scale: float = 1.0) -> None:
        """Pose le pictogramme du module : orange sur l'onglet actif, gris en arrière-plan."""
        spec = self._spec_by_id.get(module_id)
        window = self._windows.get(module_id)
        if spec is None or window is None or not icons.available(spec.icon):
            return
        index = self._module_tab_index(window)
        if index < 0:
            return
        active = index == self.tools_page.currentIndex()
        color = "accent" if active or module_id in self._tab_busy else "muted"
        self.tools_page.setTabIcon(
            index, icons.icon(spec.icon, token(color), scaled(self.TAB_ICON_SIZE), scale=scale))

    def _on_module_busy(self, module_id: str, busy: bool) -> None:
        """Anime l'icône de l'onglet pendant l'exécution d'un traitement."""
        if self._shutting_down:
            return
        if busy:
            if module_id not in self._windows:
                return
            self._tab_busy.add(module_id)
            self._tab_zoom_step = 0
            self._set_tab_icon(module_id)
            if not ThemeManager.instance().reduce_motion() and not self._tab_icon_timer.isActive():
                self._tab_icon_timer.start()
        else:
            self._tab_busy.discard(module_id)
            self._set_tab_icon(module_id)
            if not self._tab_busy:
                self._tab_icon_timer.stop()

    def _animate_busy_tabs(self) -> None:
        """Fait respirer la taille des icônes des modules occupés, sans déplacer les onglets."""
        if not self._tab_busy:
            self._tab_icon_timer.stop()
            return
        self._tab_zoom_step = (self._tab_zoom_step + 1) % (self.TAB_ZOOM_STEPS + 1)
        half = self.TAB_ZOOM_STEPS / 2
        amount = 1.0 - abs(self._tab_zoom_step - half) / half
        scale = 1.0 - (1.0 - self.TAB_ZOOM_MIN) * amount
        for module_id in list(self._tab_busy):
            self._set_tab_icon(module_id, scale)

    def _forget_module_busy(self, module_id: str) -> None:
        """Oublie l'état d'animation d'un onglet retiré."""
        self._tab_busy.discard(module_id)
        if self._shutting_down:
            # Pendant la fermeture, le minuteur peut déjà être détruit par Qt.
            return
        if not self._tab_busy:
            self._tab_icon_timer.stop()

    def _refresh_module_tab_icons(self) -> None:
        """Réapplique l'icône du thème aux onglets ouverts."""
        for module_id in self._windows:
            self._set_tab_icon(module_id)

    def _set_tab_close_icon(self, button: _TabCloseButton) -> None:
        """Croix Papicons rouge du thème ; repli texte si l'icône manque."""
        button.setIcon(icons.icon("cross", "muted", scaled(12)))
        button.setText("" if icons.available("cross") else "×")

    def _install_tab_close_button(self, index: int) -> None:
        """Remplace la croix native de Qt par la croix thémée, masquée hors survol."""
        if index <= 0:
            return
        button = _TabCloseButton(self.tools_page.tabBar())
        self._set_tab_close_icon(button)
        button.installEventFilter(self)
        button.clicked.connect(partial(self._close_tab_from_button, button))
        bar = self.tools_page.tabBar()
        bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)
        if isinstance(bar, _ToolsTabBar):
            bar.place_close_buttons()
        selected = isinstance(bar, _ToolsTabBar) and bar.is_selected(index)
        button.set_active(index == self._hovered_tab or selected)
        self._update_tab_close_tooltip(button, index)

    def _refresh_tab_close_icons(self) -> None:
        """Réapplique la teinte rouge aux boutons de fermeture après un changement de thème."""
        bar = self.tools_page.tabBar()
        for index in range(1, bar.count()):
            button = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if isinstance(button, _TabCloseButton):
                button.setFixedSize(scaled(_TabCloseButton.SIZE), scaled(_TabCloseButton.SIZE))
                button.setIconSize(QSize(scaled(12), scaled(12)))
                self._set_tab_close_icon(button)
        if isinstance(bar, _ToolsTabBar):
            bar.place_close_buttons()
        self._refresh_tab_selection_text()

    def _tab_button_index(self, button: _TabCloseButton) -> int:
        bar = self.tools_page.tabBar()
        for index in range(bar.count()):
            if bar.tabButton(index, QTabBar.ButtonPosition.RightSide) is button:
                return index
        return -1

    def _close_tab_from_button(self, button: _TabCloseButton, *_checked: object) -> None:
        index = self._tab_button_index(button)
        if index > 0:
            bar = self.tools_page.tabBar()
            if isinstance(bar, _ToolsTabBar) and bar.is_selected(index) and bar.selection_count() > 1:
                self._close_selected_tabs()
            else:
                self._close_module_tab(index)

    def _on_tools_current_changed(self, _index: int) -> None:
        """Teinte l'icône de l'onglet actif et rend les autres à leur gris."""
        self._refresh_module_tab_icons()

    def _on_tools_tab_moved(self, _from: int, _to: int) -> None:
        """Garde « Tous les outils » en tête, même si un module est déposé avant lui."""
        if self._restoring_tab_order:
            return
        self._set_hovered_tab(-1)
        bar = self.tools_page.tabBar()
        home = self.tools_page.indexOf(self.tools_home)
        if home <= 0:
            return
        self._restoring_tab_order = True
        try:
            bar.moveTab(home, 0)
        finally:
            self._restoring_tab_order = False

    def _set_hovered_tab(self, index: int, *, force: bool = False) -> None:
        """Affiche la croix de l'onglet survolé et masque les autres."""
        bar = self.tools_page.tabBar()
        if index < 0 or index >= bar.count():
            index = -1
        if index == self._hovered_tab and not force:
            # Chaque micro-mouvement du trackpad passe ici : ne rien refaire tant que
            # l'onglet survolé ne change pas (les infobulles seraient retouchées aussi).
            return
        self._hovered_tab = index
        for tab_index in range(bar.count()):
            button = bar.tabButton(tab_index, QTabBar.ButtonPosition.RightSide)
            if isinstance(button, _TabCloseButton):
                selected = isinstance(bar, _ToolsTabBar) and bar.is_selected(tab_index)
                button.set_active(tab_index > 0 and (tab_index == index or selected))
                self._update_tab_close_tooltip(button, tab_index)

    def _update_tab_close_tooltip(self, button: _TabCloseButton, index: int) -> None:
        """Annonce la fermeture groupée quand l'onglet fait partie d'une sélection."""
        bar = self.tools_page.tabBar()
        count = bar.selection_count() if isinstance(bar, _ToolsTabBar) else 0
        if isinstance(bar, _ToolsTabBar) and count > 1 and bar.is_selected(index):
            button.setToolTip(self.tr("Fermer les {count} onglets sélectionnés").format(count=count))
        else:
            button.setToolTip(self.tr("Fermer l'onglet"))

    def _on_tab_selection_changed(self) -> None:
        """Synchronise les croix et donne le mode d'emploi du groupe sélectionné."""
        self._set_hovered_tab(self._hovered_tab, force=True)
        self._refresh_tab_selection_text()
        bar = self.tools_page.tabBar()
        count = bar.selection_count() if isinstance(bar, _ToolsTabBar) else 0
        if count > 1:
            self.statusBar().showMessage(
                self.tr("{count} onglets sélectionnés — glissez pour les déplacer, Suppr pour les fermer.")
                .format(count=count), 4000)

    def _refresh_tab_selection_text(self) -> None:
        """Donne aux onglets sélectionnés l'encre des onglets actifs."""
        bar = self.tools_page.tabBar()
        for index in range(1, bar.count()):
            selected = isinstance(bar, _ToolsTabBar) and bar.is_selected(index)
            bar.setTabTextColor(index, QColor(token("text")) if selected else QColor())

    def _refresh_hover_from_cursor(self) -> None:
        """Recalcule l'onglet sous le curseur, y compris quand la souris passe sur la croix."""
        if self._shutting_down:
            return
        bar = self.tools_page.tabBar()
        local = bar.mapFromGlobal(QCursor.pos())
        self._set_hovered_tab(bar.tabAt(local) if bar.rect().contains(local) else -1)

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Recale l'agencement dès l'affichage, avant que Qt n'applique sa largeur minimale."""
        requested = self._initial_width_target        # largeur demandée, avant tout élargissement
        super().showEvent(event)
        self._update_layout_fallback(force=True, width=requested)
        # La police du thème et la hauteur du défilement se stabilisent juste après
        # l'affichage : une passe différée évite de figer une géométrie calculée trop tôt.
        QTimer.singleShot(120, self._fit_tools_cards)
        if self._top_layout != self._layout_preference:
            # Laisser passer une frame : Qt recalcule la largeur minimale de l'ancien
            # agencement après le changement de parent du sélecteur.
            QTimer.singleShot(30, self._restore_initial_width)
        else:
            self._initial_width_target = None

    def _restore_initial_width(self) -> None:
        """Après un repli au démarrage, rend à la fenêtre sa largeur d'ouverture.

        Qt a pu l'élargir jusqu'à la largeur minimale de l'en-tête regroupé avant que
        le repli ne soit appliqué. Une fois l'ancien agencement en place, la fenêtre
        retrouve exactement la largeur demandée.
        """
        target = self._initial_width_target
        self._initial_width_target = None
        if target is None or self._top_layout == self._layout_preference:
            return
        if target < self.minimumWidth():
            # À 125 % et 150 %, l'ancien agencement réclame parfois quelques pixels de
            # plus que la fenêtre demandée : le repli existe pour ne pas l'élargir, donc
            # il compresse son contenu au lieu d'imposer une largeur supérieure.
            self.setMinimumWidth(target)
        if self.width() != target:
            self.resize(target, self.height())

    def resize(self, *args) -> None:  # noqa: N802 - API Qt
        """Retient la largeur demandée tant que la fenêtre n'est pas ouverte.

        Pendant ``show()``, Qt élargit la fenêtre jusqu'à la largeur minimale de l'en-tête
        regroupé avant même d'envoyer ``showEvent``. La largeur demandée par l'appelant
        est donc mémorisée ici, pour que le repli automatique puisse ensuite la rendre.
        """
        super().resize(*args)
        if self._initial_width_target is not None and not self.isVisible():
            self._initial_width_target = self.width()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Le repli automatique suit chaque nouvelle largeur disponible."""
        super().resizeEvent(event)
        self._update_layout_fallback()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        """Suit le survol des onglets et recale la barre compacte au redimensionnement."""
        if watched is getattr(self, "logo_label", None) or watched is getattr(self, "title_label", None):
            if (event.type() == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton
                    and watched.rect().contains(event.position().toPoint())):
                self._show_personal_note()
                return True
            return super().eventFilter(watched, event)
        host = getattr(self, "header_host", None)
        if host is not None and watched is host:
            if event.type() == QEvent.Type.Resize:
                self._apply_header_progress(self._header_progress)
            return super().eventFilter(watched, event)
        tools_home = getattr(self, "tools_home", None)
        if tools_home is not None and watched is tools_home.viewport():
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._fit_tools_cards)
            return super().eventFilter(watched, event)
        bar = self.tools_page.tabBar()
        if watched is bar:
            if event.type() == QEvent.Type.MouseMove:
                self._set_hovered_tab(bar.tabAt(event.position().toPoint()))
            elif event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
                QTimer.singleShot(0, self._refresh_hover_from_cursor)
        elif isinstance(watched, _TabCloseButton) and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            QTimer.singleShot(0, self._refresh_hover_from_cursor)
        return super().eventFilter(watched, event)

    def open_module(self, module_id: str, paths: list[str] | None = None, *,
                    background: bool = False) -> QMainWindow | None:
        """Ouvre le module dans un onglet d'Azimut (ou l'active) ; transmet les chemins.

        Avec ``background=True``, l'onglet est créé sans devenir courant : le module se charge
        pendant que l'utilisateur reste sur son onglet, sans changer de page ni de focus.
        """
        spec = self._spec_by_id.get(module_id)
        card = self.cards.get(module_id)
        if spec is None or card is None or card.is_failed():
            return None
        window = self._windows.get(module_id)
        if window is not None and self._module_tab_index(window) < 0:
            self._windows.pop(module_id, None)
            window = None
        if window is None:
            try:
                window = load_window_class(spec)(spec)
            except Exception as exc:
                message = f"{type(exc).__name__} : {exc}"
                log.exception("Ouverture de %s impossible", spec.name)
                card.set_error(message)
                self._update_banner()
                # Le test de fumée d'Azimut remplace QMessageBox par un double qui n'expose que
                # warning() : on garde cette API et on rend le texte littéral par échappement.
                text = self.tr("Impossible d'ouvrir {name}.\n\n{message}").format(
                    name=spec.name, message=message)
                QMessageBox.warning(self, spec.name,
                                    f"<qt>{html.escape(text).replace(chr(10), '<br>')}</qt>")
                return None
            set_embedded = getattr(window, "set_embedded", None)
            if callable(set_embedded):
                set_embedded(True)
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            window.destroyed.connect(partial(self._on_module_closed, module_id, id(window)))
            self._windows[module_id] = window
            index = self.tools_page.addTab(window, spec.name)
            self.tools_page.tabBar().setTabData(index, module_id)
            self.tools_page.setTabToolTip(index, QCoreApplication.translate("Modules", spec.tagline))
            self._install_tab_close_button(index)
            self._set_tab_icon(module_id)
            busy_changed = getattr(window, "busyChanged", None)
            if busy_changed is not None:
                busy_changed.connect(partial(self._on_module_busy, module_id))
        if not background:
            focused = QApplication.focusWidget()
            focus_was_on_card = focused is card or (focused is not None and card.isAncestorOf(focused))
            self.show_page(self.PAGE_TOOLS)
            self.tools_page.setCurrentWidget(window)
            if focus_was_on_card:
                # Le clic vient de la carte : sans ce déplacement, le focus restait sur un contrôle
                # devenu caché et son contour orange réapparaissait au retour sur les outils.
                self.tools_page.tabBar().setFocus(Qt.FocusReason.OtherFocusReason)
        self._sync_header()
        if paths:
            try:
                window.open_paths(list(paths))
            except Exception:
                log.exception("Transmission des chemins à %s impossible", spec.name)
                self.statusBar().showMessage(
                    self.tr("Les fichiers n'ont pas pu être transmis au module."), 5000)
        return window

    def _on_module_closed(self, module_id: str, window_token: int | None = None, *_: object) -> None:
        """Un module a été fermé : retirer son onglet et rafraîchir l'historique."""
        current = self._windows.get(module_id)
        if window_token is None or current is None or id(current) == window_token:
            self._windows.pop(module_id, None)
            if current is not None:
                self._remove_module_tab(current)
            self._forget_module_busy(module_id)
        if not self._shutting_down:
            self._sync_header()
            if self._dashboard is not None:
                try:
                    self._dashboard.refresh_history()
                except RuntimeError as exc:
                    log.debug("Rafraîchissement de l'historique ignoré : %s", exc)

    def _remove_module_tab(self, window: QWidget) -> None:
        index = self._module_tab_index(window)
        if index > 0:
            self.tools_page.removeTab(index)
            self._set_hovered_tab(-1)
            bar = self.tools_page.tabBar()
            if isinstance(bar, _ToolsTabBar):
                bar.prune_selection()

    def _close_module_tab(self, index: int) -> None:
        """Ferme l'onglet d'un module et libère sa fenêtre."""
        if index <= 0:
            return
        bar = self.tools_page.tabBar()
        if isinstance(bar, _ToolsTabBar) and bar.is_selected(index) and bar.selection_count() > 1:
            self._close_selected_tabs()
            return
        window = self.tools_page.widget(index)
        if window is None:
            return
        module_id = next((mid for mid, win in self._windows.items() if win is window), "")
        if module_id and module_id in self._tab_busy:
            if not self._confirm_close_with_busy({module_id}, closing_tab=True):
                return
        self.tools_page.removeTab(index)
        self._set_hovered_tab(-1)
        if isinstance(bar, _ToolsTabBar):
            bar.prune_selection()
        if module_id:
            self._windows.pop(module_id, None)
            self._forget_module_busy(module_id)
        try:
            window.close()
        except RuntimeError as exc:
            log.debug("Fermeture de fenêtre ignorée : %s", exc)
        if not self._shutting_down:
            self._sync_header()
            if self._dashboard is not None:
                try:
                    self._dashboard.refresh_history()
                except RuntimeError as exc:
                    log.debug("Rafraîchissement de l'historique ignoré : %s", exc)

    def _close_current_tab(self) -> None:
        """Ferme l'onglet actif depuis la barre avec la touche Suppr."""
        self._close_module_tab(self.tools_page.currentIndex())

    def _close_selected_tabs(self) -> None:
        """Ferme d'un coup les onglets sélectionnés avec Maj+clic."""
        bar = self.tools_page.tabBar()
        if not isinstance(bar, _ToolsTabBar):
            return
        module_ids = bar.selected_ids()
        if not module_ids:
            return
        busy = set(module_ids) & self._tab_busy
        if busy and not self._confirm_close_with_busy(
                busy, closing_tab=True, closing_tabs=len(module_ids)):
            return
        bar.clear_selection()
        for module_id in module_ids:
            window = self._windows.pop(module_id, None)
            if window is None:
                continue
            index = self._module_tab_index(window)
            if index > 0:
                self.tools_page.removeTab(index)
            self._forget_module_busy(module_id)
            try:
                window.close()
            except RuntimeError as exc:
                log.debug("Fermeture de fenêtre ignorée : %s", exc)
        self._set_hovered_tab(-1)
        if not self._shutting_down:
            self._sync_header()
            if self._dashboard is not None:
                try:
                    self._dashboard.refresh_history()
                except RuntimeError as exc:
                    log.debug("Rafraîchissement de l'historique ignoré : %s", exc)

    def restart(self) -> bool:
        """Relance Azimut dans une nouvelle instance (préférences déjà enregistrées)."""
        if self._tab_busy and not self._confirm_close_with_busy():
            return False
        if not restart_application():
            QMessageBox.warning(
                self, self.tr("Relance impossible"),
                self.tr("Azimut n'a pas pu être relancé automatiquement. Fermez puis rouvrez l'application."))
            return False
        self._shutting_down = True
        self.close()
        return True

    def _busy_module_names(self, module_ids: set[str] | None = None) -> list[str]:
        """Noms des modules dont un traitement tourne encore (tous, ou une sélection)."""
        ids = self._tab_busy if module_ids is None else self._tab_busy & set(module_ids)
        return [self._spec_by_id[module_id].name for module_id in sorted(ids)
                if module_id in self._spec_by_id]

    def _confirm_close_with_busy(self, module_ids: set[str] | None = None, *,
                                 closing_tab: bool = False, closing_tabs: int = 1) -> bool:
        """Demande confirmation avant d'interrompre les traitements en cours."""
        names = self._busy_module_names(module_ids)
        if not names:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Traitements en cours"))
        box.setText(self.tr("Traitement en cours dans :\n{modules}")
                    .format(modules="\n".join(f"• {name}" for name in names)))
        if closing_tab and closing_tabs > 1:
            warning = self.tr("Fermer ces onglets interrompra les traitements en cours.")
        elif closing_tab:
            warning = self.tr("Fermer cet onglet interrompra le traitement en cours.")
        else:
            warning = self.tr("Fermer Azimut interrompra les traitements en cours.")
        box.setInformativeText(warning)
        close_button = box.addButton(self.tr("Fermer quand même"), QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(self.tr("Annuler"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is close_button

    def closeEvent(self, event) -> None:  # noqa: N802
        # Ne pas interrompre un traitement sans accord (une fermeture programmée pendant
        # l'arrêt de l'application concerne une fenêtre déjà cachée : pas de dialogue).
        if not self._shutting_down and self.isVisible() and self._tab_busy:
            if not self._confirm_close_with_busy():
                event.ignore()
                return
        self._shutting_down = True
        self._tab_icon_timer.stop()
        self._tab_busy.clear()
        for window in list(self._windows.values()):
            try:
                window.close()
            except RuntimeError as exc:
                log.debug("Fermeture de fenêtre ignorée à l'arrêt : %s", exc)
        self._windows.clear()
        if self._dashboard is not None:
            self._dashboard.shutdown()
        super().closeEvent(event)
