"""Notifications liquides, affichées dans la fenêtre active.

Deux présentations partagent la même file et le même langage visuel : le toast tombe du
bord supérieur d'une fenêtre de module ; dans Azimut, une capsule compacte naît au centre
de l'en-tête, entre le nom et la navigation. Dans les deux cas, le widget reste un enfant
de la fenêtre : pas de fenêtre flottante ni d'icône de zone de notification, donc aucun
comportement spécifique à Wayland à gérer.
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from azimut.ui import icons
from azimut.ui.motion import clamp01, gradient_glow, rounded_path, seg, soft_rrect, spring
from azimut.ui.theme import ThemeManager, token


INLINE_MIN_WIDTH = 120.0
INLINE_MIN_HEIGHT = 26.0
TOAST_MIN_WIDTH = 240.0


class LiquidNotification(QWidget):
    """Toast liquide, ou capsule compacte intégrée à une barre.

    Une seule notification est visible à la fois ; les suivantes attendent leur tour
    dans une petite file. Le widget laisse passer la souris et se retire tout seul.
    En mode ``inline``, la pilule naît au centre de son parent, s'y installe sans le
    dépasser, puis se rétracte ; elle est pensée pour la zone libre de l'en-tête.
    """

    finished = Signal()

    TARGET_WIDTH = 380
    TARGET_HEIGHT = 78
    TOP = 16
    INLINE_MAX_WIDTH = 380
    INLINE_HEIGHT = 34
    FRAME_MS = 16
    TOTAL_MS = 4000
    QUEUE_DELAY_MS = 140
    MAX_QUEUE = 3

    def __init__(self, parent: QWidget | None = None, *, inline: bool = False) -> None:
        super().__init__(parent)
        self._inline = bool(inline)
        self.setObjectName("LiquidNotification")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)
        self.hide()

        self._active = False
        self._reduced = False
        self._title = ""
        self._message = ""
        self._kind = "success"
        self._queue: deque[tuple[str, str, str]] = deque()
        self.total_ms = self.TOTAL_MS
        self._clock = QElapsedTimer()
        self._ticker = QTimer(self)
        self._ticker.setInterval(self.FRAME_MS)
        self._ticker.setTimerType(Qt.TimerType.PreciseTimer)
        self._ticker.timeout.connect(self._tick)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._finish)
        self._next_timer = QTimer(self)
        self._next_timer.setSingleShot(True)
        self._next_timer.timeout.connect(self._start_next)

        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())
        ThemeManager.instance().changed.connect(self.update)
        ThemeManager.instance().accessibility_changed.connect(self.update)

    # ── API ──────────────────────────────────────────────────────────────────
    def show_notification(self, title: str, message: str = "", *, kind: str = "success") -> None:
        """Affiche une notification, ou la met en attente si une autre est déjà visible."""
        title = " ".join(str(title or "Azimut").split())
        message = " ".join(str(message or "").split())
        kind = kind if kind in {"success", "error", "info"} else "success"
        if self._active:
            if len(self._queue) >= self.MAX_QUEUE:
                self._queue.popleft()
            self._queue.append((title, message, kind))
            return
        self._begin(title, message, kind)

    def dismiss(self) -> None:
        """Retire immédiatement la notification courante."""
        if self._active:
            self._finish()

    def title(self) -> str:
        return self._title

    def message(self) -> str:
        return self._message

    def kind(self) -> str:
        return self._kind

    def is_animating(self) -> bool:
        return self._active

    @property
    def inline(self) -> bool:
        """La capsule est-elle intégrée à une barre plutôt qu'un toast de fenêtre ?"""
        return self._inline

    # ── Cycle de vie ─────────────────────────────────────────────────────────
    def _begin(self, title: str, message: str, kind: str) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self._title, self._message, self._kind = title, message, kind
        self._reduced = bool(ThemeManager.instance().reduce_motion())
        self.setAccessibleName(title)
        self.setAccessibleDescription(message)
        self.setGeometry(parent.rect())
        self.update()
        self.show()
        self.raise_()
        self._active = True
        self._clock.restart()
        if self._reduced:
            self._ticker.stop()
            self._dismiss_timer.start(self.total_ms)
        else:
            self._dismiss_timer.stop()
            self._ticker.start()

    def _tick(self) -> None:
        if not self._active:
            self._ticker.stop()
            return
        if self._clock.elapsed() >= self.total_ms:
            self._finish()
            return
        self.update()

    def _finish(self) -> None:
        self._ticker.stop()
        self._dismiss_timer.stop()
        if not self._active:
            return
        self._active = False
        self.hide()
        self.finished.emit()
        if self._queue:
            self._next_timer.start(self.QUEUE_DELAY_MS)

    def _start_next(self) -> None:
        if self._active or not self._queue:
            return
        self._begin(*self._queue.popleft())

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(watched, event)

    # ── Géométrie et peinture ────────────────────────────────────────────────
    def _zoom(self) -> float:
        return ThemeManager.instance().zoom()

    def _metrics(self) -> tuple[float, float, float, float]:
        """Échelle, largeur cible, hauteur cible et centre au repos."""
        z = self._zoom()
        if self._inline:
            max_w = max(0.0, min(self.INLINE_MAX_WIDTH * z, self.width() - 8.0 * z))
            target_w = self._fitted_width(z, max_w, inline=True)
            target_h = min(self.INLINE_HEIGHT * z, max(18.0 * z, self.height() - 4.0 * z))
            return z, target_w, target_h, self.height() / 2.0
        max_w = min(self.TARGET_WIDTH * z, max(180.0 * z, self.width() - 32.0 * z))
        target_w = self._fitted_width(z, max_w, inline=False)
        target_h = self.TARGET_HEIGHT * z
        top = self.TOP * z
        return z, target_w, target_h, top + target_h / 2.0

    def _fitted_width(self, z: float, maximum: float, *, inline: bool) -> float:
        """Largeur utile pour le contenu, bornée par la place disponible."""
        if maximum <= 0.0:
            return 0.0
        title_font = QFont(self.font())
        title_font.setPixelSize(max(9, round((12.0 if inline else 14.0) * z)))
        title_font.setWeight(QFont.Weight.DemiBold)
        title_w = QFontMetrics(title_font).horizontalAdvance(self._title)
        # Un peu d'air à droite : évite qu'un texte calculé au pixel près soit
        # élidé pendant l'ouverture ressortie de la capsule.
        padding = (52.0 if inline else 76.0) * z
        desired = title_w
        if self._message:
            if inline:
                joined = f"{self._title} · {self._message}"
                joined_w = QFontMetrics(title_font).horizontalAdvance(joined)
                if padding + joined_w <= maximum:
                    desired = joined_w
            else:
                message_font = QFont(self.font())
                message_font.setPixelSize(max(9, round(12.0 * z)))
                message_w = QFontMetrics(message_font).horizontalAdvance(self._message)
                desired = max(desired, message_w)
        minimum = (INLINE_MIN_WIDTH if inline else TOAST_MIN_WIDTH) * z
        return min(maximum, max(minimum, padding + desired))

    def _layout(self, a: float) -> tuple[float, float, float, float, float]:
        """(largeur, hauteur, centre vertical, goutte arrière, opacité du texte)."""
        z, target_w, target_h, rest_y = self._metrics()
        if self._reduced:
            return target_w, target_h, rest_y, rest_y, 1.0
        if self._inline:
            return self._inline_layout(a, z, target_w, target_h, rest_y)
        drop_w = 40.0 * z
        drop_h = 40.0 * z

        if a < 3.36:
            k = seg(a, 0.0, 0.36)
            center_y = -20.0 * z + (rest_y + 20.0 * z) * k * k
            trail_y = -20.0 * z + (rest_y + 20.0 * z) * seg(a - 0.07, 0.0, 0.36) ** 2
        else:
            k = seg(a, 3.36, 3.66)
            center_y = rest_y - (rest_y + 20.0 * z) * k * k
            trail_y = rest_y - (rest_y + 20.0 * z) * seg(a + 0.07, 3.36, 3.66) ** 2

        if a < 3.15:
            openness = spring(13.0, 0.55, a - 0.33)
        else:
            openness = 1.0 - seg(a, 3.15, 3.36) ** 3
        openness = max(0.0, openness)
        width = drop_w + (target_w - drop_w) * openness
        height = drop_h + (target_h - drop_h) * clamp01(openness)

        if a < 0.5:
            content = 0.0
        elif a < 0.7:
            content = seg(a, 0.5, 0.7)
        elif a < 3.0:
            content = 1.0
        elif a < 3.12:
            content = 1.0 - seg(a, 3.0, 3.12)
        else:
            content = 0.0
        return width, height, center_y, trail_y, content

    def _inline_layout(self, a: float, z: float, target_w: float, target_h: float,
                       rest_y: float) -> tuple[float, float, float, float, float]:
        """Capsule compacte : elle s'ouvre depuis le centre, puis se rétracte."""
        if a < 3.45:
            opening = spring(14.0, 0.62, a - 0.02)
            center_y = rest_y
        else:
            k = seg(a, 3.45, 3.75)
            opening = 1.0 - k ** 3
            center_y = rest_y - 2.0 * z * k * k
        opening = clamp01(opening)
        start_w = max(18.0 * z, target_h * 0.55)
        width = start_w + (target_w - start_w) * opening
        height = target_h * 0.68 + (target_h - target_h * 0.68) * opening
        if a < 0.16:
            content = 0.0
        elif a < 0.38:
            content = seg(a, 0.16, 0.38)
        elif a < 3.30:
            content = 1.0
        elif a < 3.48:
            content = 1.0 - seg(a, 3.30, 3.48)
        else:
            content = 0.0
        return width, height, center_y, center_y, content

    def _colors(self) -> tuple[QColor, QColor]:
        if self._kind == "error":
            return QColor(token("danger")), QColor(token("accent"))
        return QColor(token("accent")), QColor(token("accent_hover"))

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._active:
            return
        z = self._zoom()
        a = self.total_ms / 1000.0 if self._reduced else min(
            self.total_ms / 1000.0, self._clock.elapsed() / 1000.0)
        width, height, center_y, trail_y, content_alpha = self._layout(a)
        if width < 1.0 or height < 1.0:
            return
        _, target_w, target_h, rest_y = self._metrics()
        center_x = self.width() / 2.0
        rect = QRectF(center_x - width / 2.0, center_y - height / 2.0, width, height)
        radius = height / 2.0
        start, end = self._colors()
        gradient = QLinearGradient(QPointF(center_x - target_w / 2.0, rect.top()),
                                   QPointF(center_x + target_w / 2.0, rect.bottom()))
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._inline and not self._reduced:
            painter.setOpacity(1.0 - seg(a, 3.80, 3.98))

        if not ThemeManager.instance().reduce_transparency():
            glow = 0.26 if self._inline else 0.40
            blur = 9.0 * z if self._inline else 18.0 * z
            gradient_glow(painter, rect.x(), rect.y(), rect.width(), rect.height(), radius,
                          start.name(), end.name(), glow, blur)
        shadow = 48 if self._inline else 72
        blur = 6.0 * z if self._inline else 10.0 * z
        soft_rrect(painter, rect.x(), rect.y(), rect.width(), rect.height(), radius,
                   QColor(0, 0, 0, shadow), blur, dy=(2.0 if self._inline else 4.0) * z)

        # Le bec reste accroché au bord supérieur du toast ; la capsule intégrée, elle,
        # naît directement au centre de sa barre, sans goutte extérieure.
        if not self._inline and not self._reduced:
            spout = QRectF(center_x - 44.0 * z, -24.0 * z, 88.0 * z, 32.0 * z)
            painter.fillPath(rounded_path(spout, 18.0 * z), QBrush(gradient))
            if a < 0.5 or a > 3.3:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(gradient))
                painter.drawEllipse(QPointF(center_x, trail_y), 7.0 * z, 7.0 * z)

        pill = rounded_path(rect, radius)
        painter.fillPath(pill, QBrush(gradient))
        painter.save()
        painter.setClipPath(pill)
        shine = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shine.setColorAt(0.0, QColor(255, 255, 255, 58))
        shine.setColorAt(0.55, QColor(255, 255, 255, 0))
        painter.fillRect(rect, shine)
        if self._inline and not self._reduced and 0.02 < a < 0.55:
            # Un reflet traverse la capsule pendant qu'elle s'ouvre : la matière
            # semble couler depuis le centre vers ses deux extrémités.
            k = seg(a, 0.02, 0.55)
            band = max(24.0 * z, width * 0.22)
            sweep_x = rect.left() - band + (width + 2.0 * band) * k
            sweep = QLinearGradient(sweep_x, rect.top(), sweep_x + band, rect.bottom())
            sweep.setColorAt(0.0, QColor(255, 255, 255, 0))
            sweep.setColorAt(0.5, QColor(255, 255, 255, 52))
            sweep.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(rect, sweep)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 95), 1.0))
        painter.drawPath(pill)

        minimum = 110.0 * z if self._inline else 190.0 * z
        if content_alpha > 0.01 and width >= minimum:
            self._paint_content(painter, rect, center_y, content_alpha, z)
        painter.end()

    def _paint_content(self, painter: QPainter, rect: QRectF, center_y: float,
                       alpha: float, z: float) -> None:
        if self._inline:
            self._paint_inline_content(painter, rect, center_y, alpha, z)
            return
        icon_center = QPointF(rect.left() + 30.0 * z, center_y)
        icon_radius = 18.0 * z
        icon_name = ("alert-triangle" if self._kind == "error"
                     else "info" if self._kind == "info" else "check")
        if not icons.available(icon_name):
            icon_name = "info"

        painter.save()
        painter.setOpacity(alpha)
        painter.setPen(QPen(QColor(255, 255, 255, 75), 1.0))
        painter.setBrush(QColor(255, 255, 255, 34))
        painter.drawEllipse(icon_center, icon_radius, icon_radius)
        pixmap = icons.pixmap(icon_name, max(12, round(18.0 * z)), "#ffffff")
        if not pixmap.isNull():
            side = 18.0 * z
            target = QRectF(icon_center.x() - side / 2.0, icon_center.y() - side / 2.0,
                            side, side)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))

        title_font = QFont(self.font())
        title_font.setPixelSize(max(10, round(14.0 * z)))
        title_font.setWeight(QFont.Weight.DemiBold)
        message_font = QFont(self.font())
        message_font.setPixelSize(max(9, round(12.0 * z)))
        text_left = rect.left() + 58.0 * z
        text_right = rect.right() - 18.0 * z
        available = max(1, round(text_right - text_left))
        title = QFontMetrics(title_font).elidedText(self._title, Qt.TextElideMode.ElideRight,
                                                    available)
        message = QFontMetrics(message_font).elidedText(self._message, Qt.TextElideMode.ElideRight,
                                                        available)
        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, 255))
        painter.drawText(QPointF(text_left, center_y - 2.0 * z), title)
        painter.setFont(message_font)
        painter.setPen(QColor(255, 255, 255, 218))
        painter.drawText(QPointF(text_left, center_y + 15.0 * z), message)
        painter.restore()

    def _paint_inline_content(self, painter: QPainter, rect: QRectF, center_y: float,
                              alpha: float, z: float) -> None:
        """Une seule ligne : l'icône, puis le titre et le message s'ils tiennent."""
        icon_name = ("alert-triangle" if self._kind == "error"
                     else "info" if self._kind == "info" else "check")
        if not icons.available(icon_name):
            icon_name = "info"
        icon_center = QPointF(rect.left() + 17.0 * z, center_y)
        icon_radius = min(10.0 * z, max(7.0 * z, (rect.height() - 4.0 * z) / 2.0))

        painter.save()
        painter.setOpacity(alpha)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
        painter.setBrush(QColor(255, 255, 255, 32))
        painter.drawEllipse(icon_center, icon_radius, icon_radius)
        pixmap = icons.pixmap(icon_name, max(10, round(13.0 * z)), "#ffffff")
        if not pixmap.isNull():
            side = 13.0 * z
            target = QRectF(icon_center.x() - side / 2.0, icon_center.y() - side / 2.0,
                            side, side)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))

        title_font = QFont(self.font())
        title_font.setPixelSize(max(9, round(12.0 * z)))
        title_font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(title_font)
        text_left = rect.left() + 32.0 * z
        text_right = rect.right() - 12.0 * z
        available = max(1, round(text_right - text_left))
        title = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, available)
        text = title
        if self._message:
            joined = f"{title} · {self._message}"
            if metrics.horizontalAdvance(joined) <= available:
                text = joined
        baseline = center_y + (metrics.ascent() - metrics.descent()) / 2.0
        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, 255))
        painter.drawText(QPointF(text_left, baseline), text)
        painter.restore()


class PersistentNotification(QWidget):
    """Notification permanente : la matière du toast, mais fermée à la main.

    Le toast disparaît seul et ne montre qu'une ligne ; cette carte porte un texte
    long, en plusieurs paragraphes, et reste posée au-dessus du contenu jusqu'à ce
    que l'utilisateur actionne son bouton de fermeture. Elle suit le thème, le zoom
    et le réglage « Réduire la transparence » comme les autres notifications.

    Ouverture et fermeture sont un splash : une goutte naît sous l'en-tête, s'élargit
    en carte, puis le texte apparaît en fondu ; à la fermeture, l'ordre s'inverse.
    """

    closed = Signal()

    WIDTH = 540          # largeur de lecture de la carte, avant mise à l'échelle
    MARGIN = 20          # retrait par rapport aux bords de la fenêtre
    TOP_GAP = 12         # respiration sous la barre d'en-tête
    RADIUS = 18
    PAD = 18             # place laissée au halo et à l'ombre autour de la carte
    OPEN_MS = 320
    CLOSE_MS = 220
    GROW_END = 0.85      # part de l'animation consacrée à la goutte ; le reste au fondu

    def __init__(self, parent: QWidget, title: str, message: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("PersistentNotification")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._title = title
        self._message = message
        self._pad = self.PAD
        self._progress = 1.0
        self._closing = False
        self._closed = False

        # Le contenu vit dans son propre conteneur : la carte peint la goutte du splash,
        # le contenu n'apparaît qu'une fois la carte ouverte, en fondu.
        self._content = QWidget(self)
        self._content.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._content.setAutoFillBackground(False)
        self._opacity = QGraphicsOpacityEffect(self._content)
        self._opacity.setOpacity(0.0)
        self._content.setGraphicsEffect(self._opacity)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._content)

        layout = QVBoxLayout(self._content)
        head = QHBoxLayout()
        self._icon = QLabel(self._content)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._title_label = QLabel(title, self._content)
        self._title_label.setTextFormat(Qt.TextFormat.PlainText)
        self._title_label.setWordWrap(True)
        head.addWidget(self._title_label, 1)
        self.close_button = QToolButton(self._content)
        self.close_button.setObjectName("NotificationClose")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setAccessibleName(self.tr("Fermer"))
        self.close_button.setToolTip(self.tr("Fermer la notification"))
        # Pas de raccourci Échap : les onglets d'outils s'en servent pour vider une sélection.
        self.close_button.clicked.connect(self.close_persistent)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self._body_label = QLabel(message, self._content)
        self._body_label.setTextFormat(Qt.TextFormat.PlainText)
        self._body_label.setWordWrap(True)
        layout.addWidget(self._body_label)

        self._animation = QVariantAnimation(self)
        self._animation.valueChanged.connect(self._on_frame)
        self._animation.finished.connect(self._on_animation_finished)

        parent.installEventFilter(self)
        # Un frère créé après la carte (repli de module, toast, pile de pages) naît
        # au-dessus d'elle : la carte repasse devant dès que la boucle est rendue.
        self._raise_timer = QTimer(self)
        self._raise_timer.setSingleShot(True)
        self._raise_timer.timeout.connect(self._raise_on_top)
        ThemeManager.instance().changed.connect(self._restyle)
        ThemeManager.instance().accessibility_changed.connect(self._restyle)
        self._restyle()

    # ── API ──────────────────────────────────────────────────────────────────
    def title(self) -> str:
        return self._title

    def message(self) -> str:
        return self._message

    def set_text(self, title: str, message: str = "") -> None:
        """Remplace le texte d'une carte déjà ouverte (un seul exemplaire par fenêtre)."""
        self._title, self._message = title, message
        self._title_label.setText(title)
        self._body_label.setText(message)
        self._body_label.setVisible(bool(message))
        self._place()

    def play_open(self) -> None:
        """Ouvre la carte en splash : goutte qui s'élargit, puis texte en fondu."""
        self._animation.stop()
        self._closing = False
        self._closed = False
        if ThemeManager.instance().reduce_motion():
            self._set_progress(1.0)
            return
        self._set_progress(0.0)
        self._animation.setDuration(self.OPEN_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def close_persistent(self) -> None:
        """Seul moyen de retirer la carte : le texte s'efface, la goutte se referme."""
        if self._closing or self._closed:
            return
        if ThemeManager.instance().reduce_motion() or not self.isVisible():
            self._finish_close()
            return
        self._closing = True
        self._animation.stop()
        self._animation.setDuration(self.CLOSE_MS)
        self._animation.setStartValue(self._progress)
        self._animation.setEndValue(0.0)
        self._animation.start()

    # ── Animation ──────────────────────────────────────────────────────────
    def _on_frame(self, value: object) -> None:
        self._set_progress(float(value))

    def _on_animation_finished(self) -> None:
        if self._closing:
            self._finish_close()

    def _set_progress(self, progress: float) -> None:
        self._progress = clamp01(progress)
        # Le texte n'apparaît qu'une fois la carte à sa taille : pendant la croissance
        # de la goutte, il déborderait de la surface peinte.
        if self._progress >= 1.0:
            self._opacity.setOpacity(1.0)
        else:
            span = 1.0 - self.GROW_END
            self._opacity.setOpacity(
                clamp01((self._progress - self.GROW_END) / span))
        self.update()

    def _finish_close(self) -> None:
        """Retire la carte une fois le splash refermé (ou tout de suite sans animation)."""
        if self._closed:
            return
        self._closed = True
        self._animation.stop()
        self._raise_timer.stop()
        host = self.window()
        if getattr(host, "_azimut_persistent_notification", None) is self:
            host._azimut_persistent_notification = None
        self.hide()
        self.closed.emit()
        self.deleteLater()

    # ── Style et géométrie ───────────────────────────────────────────────────
    def _restyle(self, _name: str = "") -> None:
        z = ThemeManager.instance().zoom()
        self._pad = max(10, round(self.PAD * z))

        icon_side = max(20, round(28 * z))
        self._icon.setFixedSize(icon_side, icon_side)
        self._icon.setStyleSheet(
            f"background-color: rgba(255, 255, 255, 38); border-radius: {icon_side // 2}px;")
        self._icon.setPixmap(icons.pixmap("info", max(12, round(15 * z)), "#ffffff"))

        title_font = QFont(self.font())
        title_font.setPixelSize(max(11, round(14.0 * z)))
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title_label.setFont(title_font)
        self._title_label.setStyleSheet("color: #ffffff; background: transparent;")

        body_font = QFont(self.font())
        body_font.setPixelSize(max(10, round(12.5 * z)))
        self._body_label.setFont(body_font)
        self._body_label.setStyleSheet("color: rgba(255, 255, 255, 235); background: transparent;")

        close_side = max(20, round(26 * z))
        icon_size = max(9, round(12 * z))
        self.close_button.setFixedSize(close_side, close_side)
        self.close_button.setIcon(icons.icon("cross", "#ffffff", icon_size))
        self.close_button.setIconSize(QSize(icon_size, icon_size))
        self.close_button.setStyleSheet(
            "QToolButton#NotificationClose { background-color: rgba(255, 255, 255, 34);"
            " border: 1px solid rgba(255, 255, 255, 90);"
            f" border-radius: {close_side // 2}px; }}"
            "QToolButton#NotificationClose:hover { background-color: rgba(255, 255, 255, 72); }")

        layout = self._content.layout()
        if layout is not None:
            inset_x = max(6, round(20 * z))
            layout.setContentsMargins(
                self._pad + inset_x,
                self._pad + max(6, round(14 * z)),
                self._pad + max(4, round(10 * z)),
                self._pad + max(6, round(16 * z)))
            layout.setSpacing(max(4, round(8 * z)))
        self.update()
        self._place()

    def _card_rect(self) -> QRectF:
        pad = float(self._pad)
        return QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)

    def _place(self) -> None:
        """Centre la carte sous l'en-tête et lui donne la hauteur de son texte."""
        parent = self.parentWidget()
        if parent is None:
            return
        z = ThemeManager.instance().zoom()
        margin = max(8, round(self.MARGIN * z))
        available = max(240, parent.width() - 2 * margin)
        card_width = min(max(280, round(self.WIDTH * z)), available)
        self.setFixedWidth(card_width + 2 * self._pad)
        layout = self._content.layout()
        height = layout.heightForWidth(self.width()) if layout is not None else -1
        if height <= 0 and layout is not None:
            height = layout.sizeHint().height()
        self.setFixedHeight(max(1, height))

        top = margin
        header = getattr(parent.window(), "header_host", None)
        if isinstance(header, QWidget) and header.parentWidget() is parent and header.isVisible():
            top = header.geometry().bottom() + max(6, round(self.TOP_GAP * z))
        top = max(margin, min(top, parent.height() - self.height() - margin))
        self.move(max(0, (parent.width() - self.width()) // 2), top)
        self.raise_()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget():
            kind = event.type()
            if kind in (QEvent.Type.Resize, QEvent.Type.Show) and self.isVisible():
                self._place()
            if kind in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
                self._raise_timer.start(0)
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.raise_()

    def _raise_on_top(self) -> None:
        """Repasse la carte devant le contenu tant qu'elle n'est pas fermée."""
        if self._closed or not self.isVisible():
            return
        self.raise_()

    def _splash_rect(self) -> QRectF:
        """Rectangle peint : une goutte posée sous l'en-tête qui s'élargit en carte."""
        full = self._card_rect()
        if self._progress >= self.GROW_END:
            return full
        z = ThemeManager.instance().zoom()
        drop = max(16.0, 30.0 * z)
        # Croissance en entrée : la goutte reste petite, puis la carte éclôt.
        grow = (self._progress / self.GROW_END) ** 2
        width = drop + (full.width() - drop) * grow
        height = drop + (full.height() - drop) * grow
        return QRectF(full.center().x() - width / 2.0, full.top(), width, height)

    def paintEvent(self, _event) -> None:  # noqa: N802
        rect = self._splash_rect()
        if rect.width() < 2.0 or rect.height() < 2.0:
            return
        z = ThemeManager.instance().zoom()
        full = self._card_rect()
        full_radius = max(6.0, self.RADIUS * z)
        # Goutte ronde au départ, coins de carte à l'arrivée : le rayon suit la croissance.
        shrink = max(0.0, min(1.0, rect.height() / full.height())) if full.height() > 0 else 1.0
        radius = full_radius + (min(rect.width(), rect.height()) / 2.0 - full_radius) * (1.0 - shrink)
        start, end = QColor(token("accent")), QColor(token("accent_hover"))
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not ThemeManager.instance().reduce_transparency():
            gradient_glow(painter, rect.x(), rect.y(), rect.width(), rect.height(), radius,
                          start.name(), end.name(), 0.34, 16.0 * z)
        soft_rrect(painter, rect.x(), rect.y(), rect.width(), rect.height(), radius,
                   QColor(0, 0, 0, 72), 10.0 * z, dy=4.0 * z)
        pill = rounded_path(rect, radius)
        painter.fillPath(pill, QBrush(gradient))
        painter.save()
        painter.setClipPath(pill)
        shine = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shine.setColorAt(0.0, QColor(255, 255, 255, 52))
        shine.setColorAt(0.55, QColor(255, 255, 255, 0))
        painter.fillRect(rect, shine)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 95), 1.0))
        painter.drawPath(pill)
        painter.end()


def notify_persistent(origin: QWidget, title: str,
                      message: str = "") -> PersistentNotification | None:
    """Affiche une notification permanente (une par fenêtre), fermée manuellement."""
    if origin is None:
        return None
    host = origin.window()
    parent = host.centralWidget() if isinstance(host, QMainWindow) else host
    if parent is None:
        parent = host
    card = getattr(host, "_azimut_persistent_notification", None)
    if isinstance(card, PersistentNotification) and not card._closing:
        card.set_text(title, message)
        card.show()
        card.raise_()
        card._place()
        return card
    card = PersistentNotification(parent, title, message)
    setattr(host, "_azimut_persistent_notification", card)
    card.show()
    card.raise_()
    card._place()
    card.play_open()
    return card


def notify_window(origin: QWidget, title: str, message: str = "",
                  kind: str = "success") -> LiquidNotification | None:
    """Affiche une notification dans la fenêtre de ``origin`` (une seule par fenêtre).

    Si la fenêtre expose une voie centrale assez large (l'en-tête d'Azimut), la
    notification y devient une capsule intégrée ; sinon elle reste un toast au-dessus
    du contenu, pour ne jamais sacrifier la lisibilité sur une fenêtre étroite.
    """
    if origin is None:
        return None
    host = origin.window()
    central = host.centralWidget() if isinstance(host, QMainWindow) else host
    lane = getattr(host, "notification_lane", None)
    zoom = ThemeManager.instance().zoom()
    inline = (isinstance(lane, QWidget) and lane.isVisible()
              and lane.width() >= INLINE_MIN_WIDTH * zoom
              and lane.height() >= INLINE_MIN_HEIGHT * zoom)
    parent = lane if inline else (central if central is not None else host)
    toast = getattr(host, "_azimut_notification", None)
    if (not isinstance(toast, LiquidNotification) or toast.parentWidget() is not parent
            or toast.inline != inline):
        if isinstance(toast, LiquidNotification):
            toast.dismiss()
            toast.deleteLater()
        toast = LiquidNotification(parent, inline=inline)
        setattr(host, "_azimut_notification", toast)
    toast.show_notification(title, message, kind=kind)
    return toast


__all__ = ["LiquidNotification", "PersistentNotification", "notify_persistent", "notify_window"]