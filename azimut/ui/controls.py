"""Commandes animées d'Azimut : interrupteurs, cases à cocher, sélecteur segmenté, bulle de curseur.

Les formules de mouvement viennent des études de ``azimut.ui.motion`` (ressorts amortis,
écrasement aux extrémités, fusion des gouttes, pouce ressorti). Ici, les couleurs suivent
les jetons du thème et le réglage « Réduire les animations » coupe toute animation.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    QEasingCurve,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)

from azimut.ui import icons
from azimut.ui.glass import LiquidButton
from azimut.ui.motion import (
    bump,
    clamp01,
    color,
    ease,
    mix,
    rounded_path,
    soft_ellipse,
    spring,
)
from azimut.ui.theme import ThemeManager, scaled, token



def fit_combo_to_panel(combo: QComboBox, length: int = 10) -> QComboBox:
    """Liste déroulante dont la largeur au repos ne suit pas son item le plus long.

    Un libellé long (« AAAA-MM-JJ_HH-MM-SS   (2008-06-15_14-30-00) », « AV1 (WebM) »…)
    élargissait sinon le panneau latéral au-delà de sa largeur type. Le menu ouvert
    affiche toujours les textes entiers : seule la largeur au repos est bornée.
    """
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(length)
    return combo


FRAME_MS = 16
SETTLE_S = 1.2          # au-delà, le ressort est posé : on arrête l'horloge
INK_ON_ACCENT = "#10171a"
# Graisses des libellés du sélecteur segmenté : régulier au repos, médium pour le
# segment actif. Le pouce et la couleur portent déjà la sélection, le gras n'a pas
# à l'appuyer davantage.
SEGMENT_TEXT_WEIGHT = QFont.Weight.Normal
SEGMENT_ACTIVE_WEIGHT = QFont.Weight.Medium


def _radius(rect: QRectF, value: float) -> QPainterPath:
    return rounded_path(rect, value)


def set_action_available(button: QAbstractButton, available: bool, *,
                         active: str = "secondary") -> None:
    """Action contextuelle : discrète quand elle est inutile, nette quand elle agit.

    Le bouton reste en place pour ne pas faire sauter la barre d'outils ; il passe en
    « ghost » désactivé sans sélection ou sans contenu, puis reprend son style actif.
    """
    available = bool(available)
    button.setEnabled(available)
    kind = active if available else "ghost"
    if button.property("kind") != kind:
        button.setProperty("kind", kind)
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()


# ─────────────────────────────────────────────────────────────────────────────
# Interrupteur : goutte liquide, traînée et fusion, à l'échelle d'un réglage
# ─────────────────────────────────────────────────────────────────────────────
class Switch(QAbstractButton):
    """Interrupteur liquide : bouton étiré, gouttes en retard et fusion « gooey ».

    C'est un vrai ``QAbstractButton`` : ``setChecked``, ``toggled``, la barre d'espace et
    l'anneau de focus se comportent comme pour une case à cocher.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._p = 0.0
        self._from = 0.0
        self._target = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._apply_metrics()
        self.toggled.connect(self._on_toggled)
        ThemeManager.instance().changed.connect(self._on_theme)
        ThemeManager.instance().accessibility_changed.connect(self.update)

    # ── Géométrie ───────────────────────────────────────────────────────────
    def _apply_metrics(self) -> None:
        self._height = float(scaled(24))
        self._width = float(scaled(44))
        self._pad = max(2.0, round(self._height * 0.10))
        self._radius = self._height / 2.0 - self._pad
        self.setFixedSize(int(self._width), int(self._height))

    def _on_theme(self, _name: str = "") -> None:
        self._apply_metrics()
        self.update()

    # ── Animation ───────────────────────────────────────────────────────────
    def _on_toggled(self, checked: bool) -> None:
        if not self.isVisible() or ThemeManager.instance().reduce_motion():
            self._timer.stop()
            self._p = self._target = 1.0 if checked else 0.0
            self.update()
            return
        self._from = self._p
        self._target = 1.0 if checked else 0.0
        self._clock.restart()
        self._timer.start()

    def _value_at(self, seconds: float) -> float:
        if seconds <= 0.0:
            return self._from
        return self._from + (self._target - self._from) * spring(16.0, 0.58, seconds)

    def _tick(self) -> None:
        t = self._clock.elapsed() / 1000.0
        self._p = self._value_at(t)
        if t > SETTLE_S and abs(self._p - self._target) < 0.002:
            self._p = self._target
            self._timer.stop()
        self.update()

    def _delayed(self, delay: float) -> float:
        """Position du ressort en retard de ``delay`` secondes (traînée liquide)."""
        if not self._timer.isActive():
            return self._p
        return self._value_at(self._clock.elapsed() / 1000.0 - delay)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()
        self._p = self._target
        self.update()

    def _position(self, value: float) -> float:
        travel = self._width - 2.0 * self._pad - 2.0 * self._radius
        return self._pad + self._radius + travel * clamp01(value)

    @staticmethod
    def _theme_color(name: str, alpha: float | None = None) -> QColor:
        return color(token(name), alpha)

    # ── Peinture ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = self._p
        q = clamp01(p)
        height, width = self._height, self._width
        radius = height / 2.0
        centre_y = height / 2.0

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        track = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
        painter.setClipPath(_radius(track, radius - 0.5))

        dark = ThemeManager.instance().theme() == "dark"
        # Deux dégradés distincts : le rail éteint garde une matière sombre,
        # le rail allumé prend une teinte orange plus lumineuse en haut.
        off_top = mix(token("surface_alt"), "#ffffff", 0.10 if dark else 0.30)
        off_bottom = mix(token("surface_alt"), "#000000", 0.24 if dark else 0.04)
        on_top = QColor(token("accent_hover" if dark else "accent"))
        on_bottom = QColor(token("accent" if dark else "accent_hover"))
        track_gradient = QLinearGradient(0.0, 0.0, 0.0, height)
        track_gradient.setColorAt(0.0, mix(off_top, on_top, q))
        track_gradient.setColorAt(1.0, mix(off_bottom, on_bottom, q))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track_gradient))
        painter.drawRoundedRect(track, radius, radius)
        # Reflet vertical : donne du relief au rail même lorsqu'il est éteint.
        sheen = QLinearGradient(0.0, 0.0, 0.0, height)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 30 if dark else 24))
        sheen.setColorAt(0.45, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.0, QColor(0, 0, 0, 42 if dark else 16))
        painter.setBrush(QBrush(sheen))
        painter.drawRoundedRect(track, radius, radius)

        self._paint_knob(painter, q, centre_y)
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._theme_color("border", 0.85 if dark else 0.55), 1.0))
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius - 1.0, radius - 1.0)
        if self.hasFocus() and self.property("keyboardFocus") is True:
            painter.setPen(QPen(self._theme_color("focus"), 2.0))
            painter.drawRoundedRect(QRectF(1.0, 1.0, width - 2.0, height - 2.0),
                                    radius - 1.0, radius - 1.0)
        painter.end()

    def _paint_knob(self, painter: QPainter, q: float, centre_y: float) -> None:
        radius = self._radius
        overshoot = abs(self._p - q)
        squash_x = 1.0 - 0.9 * overshoot
        squash_y = 1.0 + 0.6 * overshoot
        shift = radius * (1.0 - squash_x)
        centre_x = self._position(self._p)
        if self._p > 1.0:
            centre_x += shift
        elif self._p < 0.0:
            centre_x -= shift

        speed = abs(self._p - self._delayed(0.016)) / 0.016 if self._timer.isActive() else 0.0
        stretch = min(speed * 0.004, 0.42)
        scale_x = max(0.04, squash_x * (1.0 + stretch))
        scale_y = max(0.04, squash_y / math.sqrt(1.0 + stretch))
        soft_ellipse(painter, centre_x, centre_y + 1.5, radius * scale_x, radius * scale_y,
                     QColor(0, 0, 0, 70), 5.0, layers=6)
        self._paint_goo(painter, centre_x, centre_y, scale_x, scale_y)

    def _paint_goo(self, painter: QPainter, cx: float, cy: float, sx: float, sy: float) -> None:
        """Bouton étiré et deux gouttes en retard, fusionnés par un trait épais."""
        radius = self._radius
        shapes = [(cx, radius * sx, radius * sy)]
        for factor, delay in ((0.58, 0.045), (0.36, 0.095)):
            delayed = self._delayed(delay)
            over = max(delayed - 1.0, -delayed, 0.0)
            rx = radius * factor * max(0.10, 1.0 - 1.6 * over)
            ry = radius * factor * (1.0 + 1.1 * over)
            shapes.append((self._position(delayed), rx, ry))
        painter.save()
        pen = QPen(QColor(255, 255, 255), max(1.0, radius * 0.14),
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        knob = QLinearGradient(0.0, cy - radius, 0.0, cy + radius)
        knob.setColorAt(0.0, QColor("#FFFFFF"))
        knob.setColorAt(1.0, mix("#FFFFFF", token("accent"), 0.16 * clamp01(self._p)))
        painter.setBrush(QBrush(knob))
        for shape_x, rx, ry in shapes:
            painter.drawEllipse(QRectF(shape_x - rx, cy - ry, 2 * rx, 2 * ry))
        painter.restore()


class SwitchRow(QWidget):
    """Libellé et interrupteur sur une ligne ; API d'une case à cocher pour le reste."""

    toggled = Signal(bool)

    def __init__(self, text: str, parent: QWidget | None = None, *,
                 align_right: bool = False) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(10)
        self.label = QLabel(text)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.switch = Switch()
        self.switch.toggled.connect(self.toggled.emit)
        self.switch.setAccessibleName(text)
        # Le libellé d'un réglage aligné à droite occupe la largeur libre : sinon
        # QLabel, qui active le retour à la ligne, se rabat sur la largeur de son mot
        # le plus long et coupe « Réduire les animations » dès 80 %.
        row.addWidget(self.label, 1 if align_right else 0)
        if align_right:
            # Réglages en colonne : tous les interrupteurs s'alignent sur le bord droit.
            row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            # L'interrupteur suit son libellé au lieu d'être repoussé au bord de la ligne.
            row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── API de QCheckBox utilisée par les écrans ────────────────────────────
    def isChecked(self) -> bool:  # noqa: N802
        return self.switch.isChecked()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self.switch.setChecked(bool(checked))

    def text(self) -> str:
        return self.label.text()

    def setText(self, text: str) -> None:  # noqa: N802
        self.label.setText(text)
        self.switch.setAccessibleName(text)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self.label.setEnabled(enabled)
        self.switch.setEnabled(enabled)

    def setToolTip(self, text: str) -> None:  # noqa: N802
        super().setToolTip(text)
        self.label.setToolTip(text)
        self.switch.setToolTip(text)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton and self.isEnabled()
                and not self.switch.geometry().contains(event.position().toPoint())):
            self.switch.toggle()
        super().mouseReleaseEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Case à cocher : la goutte d'accent remplit le cadre pendant que la coche se trace
# ─────────────────────────────────────────────────────────────────────────────
class CheckBox(QCheckBox):
    """Case à cocher liquide : même ressort que ``Switch``, à l'échelle d'une option.

    La coche se dessine d'un trait pendant qu'une goutte d'accent gonfle depuis le centre
    du cadre ; le dépassement du ressort écrase brièvement la case, comme le pouce de
    l'interrupteur. « Réduire les animations » pose la case immédiatement.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._p = 1.0 if self.isChecked() else 0.0
        self._from = self._p
        self._target = self._p
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._apply_metrics()
        self.toggled.connect(self._on_toggled)
        ThemeManager.instance().changed.connect(self._on_theme)
        ThemeManager.instance().accessibility_changed.connect(self.update)

    # ── Géométrie ───────────────────────────────────────────────────────────
    def _apply_metrics(self) -> None:
        self._side = float(scaled(16))                     # côté de la case, comme le QSS
        self._border = float(max(2, scaled(2)))
        self._gap = float(scaled(8))
        self._radius = max(3.0, round(self._side * 0.31))  # $ind_r
        self._box = self._side + 2.0 * self._border

    def _on_theme(self, _name: str = "") -> None:
        self._apply_metrics()
        self.updateGeometry()
        self.update()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetricsF(self.font())
        text = self.text()
        width = metrics.horizontalAdvance(text) if text else 0.0
        gap = self._gap if text else 0.0
        return QSize(math.ceil(self._box + gap + width) + scaled(2),
                     math.ceil(max(self._box, metrics.height())) + scaled(2))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    # ── Animation ───────────────────────────────────────────────────────────
    def _on_toggled(self, checked: bool) -> None:
        if not self.isVisible() or ThemeManager.instance().reduce_motion():
            self._timer.stop()
            self._p = self._target = 1.0 if checked else 0.0
            self.update()
            return
        self._from = self._p
        self._target = 1.0 if checked else 0.0
        self._clock.restart()
        self._timer.start()

    def _value_at(self, seconds: float) -> float:
        if seconds <= 0.0:
            return self._from
        return self._from + (self._target - self._from) * spring(16.0, 0.58, seconds)

    def _tick(self) -> None:
        t = self._clock.elapsed() / 1000.0
        self._p = self._value_at(t)
        if t > SETTLE_S and abs(self._p - self._target) < 0.002:
            self._p = self._target
            self._timer.stop()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()
        self._p = self._target
        self.update()

    # ── Peinture ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        enabled = self.isEnabled()
        hovered = enabled and self.underMouse()
        accent = token("accent") if enabled else token("border")
        if hovered and self.isChecked():
            accent = token("accent_hover")

        box = QRectF(0.0, (self.height() - self._box) / 2.0, self._box, self._box)
        inset = box.adjusted(self._border / 2.0, self._border / 2.0,
                             -self._border / 2.0, -self._border / 2.0)
        path = rounded_path(inset, self._radius)

        # Goutte d'accent : un disque gonfle depuis le centre et remplit le cadre.
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(token("surface")))
        painter.drawPath(path)
        growth = clamp01(self._p * 1.15)
        if growth > 0.02:
            painter.setClipPath(path)
            radius = box.width() * 0.72 * growth
            painter.setBrush(color(accent))
            painter.drawEllipse(box.center(), radius, radius)
            if growth > 0.04:
                q = clamp01(self._p)
                sheen = QLinearGradient(0.0, box.top(), 0.0, box.bottom())
                sheen.setColorAt(0.0, QColor(255, 255, 255, round(46 * q)))
                sheen.setColorAt(1.0, QColor(0, 0, 0, round(26 * q)))
                painter.setBrush(QBrush(sheen))
                painter.drawPath(path)
        painter.restore()

        # Écrasement du ressort : la case s'étire un instant puis revient.
        painter.save()
        overshoot = abs(self._p - clamp01(self._p))
        if overshoot > 0.001:
            centre = box.center()
            painter.translate(centre)
            painter.scale(1.0 - 0.16 * overshoot, 1.0 + 0.12 * overshoot)
            painter.translate(-centre.x(), -centre.y())

        rest = token("border") if not enabled else (token("accent") if hovered else token("muted"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(mix(rest, accent, clamp01(self._p)), self._border))
        painter.drawPath(path)

        if clamp01(self._p) > 0.02:
            self._paint_check(painter, box)
        painter.restore()

        if self.hasFocus() and self.property("keyboardFocus"):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color(token("focus")), 2.0))
            ring = box.adjusted(-1.5, -1.5, 1.5, 1.5)
            painter.drawRoundedRect(ring, self._radius + 2.0, self._radius + 2.0)

        text_rect = QRectF(box.right() + self._gap, 0.0,
                           max(0.0, self.width() - box.right() - self._gap),
                           float(self.height()))
        painter.setFont(self.font())
        painter.setPen(color(token("text") if enabled else token("muted")))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.text())
        painter.end()

    def _paint_check(self, painter: QPainter, box: QRectF) -> None:
        """Coche blanche du thème, tracée progressivement et ressortie à l'échelle."""
        content = box.adjusted(self._border, self._border, -self._border, -self._border)
        scale_k = content.width() / 16.0                     # tracé défini pour une case de 16 px
        a = QPointF(content.left() + 3.6 * scale_k, content.top() + 8.6 * scale_k)
        b = QPointF(content.left() + 6.7 * scale_k, content.top() + 11.6 * scale_k)
        c = QPointF(content.left() + 12.4 * scale_k, content.top() + 4.9 * scale_k)
        draw = ease(clamp01((self._p - 0.12) / 0.78))
        first = math.hypot(b.x() - a.x(), b.y() - a.y())
        second = math.hypot(c.x() - b.x(), c.y() - b.y())
        travelled = draw * (first + second)
        if travelled <= 0.0:
            return

        pop = 0.80 + 0.20 * ease(clamp01(self._p / 0.6)) + 0.08 * bump(self._p, 0.2, 1.0)
        painter.save()
        centre = content.center()
        painter.translate(centre)
        painter.scale(pop, pop)
        painter.translate(-centre.x(), -centre.y())
        pen = QPen(QColor("#FFFFFF"), max(1.4, 2.4 * scale_k),
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(a)
        if travelled <= first:
            path.lineTo(a + (b - a) * (travelled / first))
        else:
            path.lineTo(b)
            path.lineTo(b + (c - b) * ((travelled - first) / second))
        painter.drawPath(path)
        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# Sélecteur segmenté : pouce ressorti et libellés en fondu croisé
# ─────────────────────────────────────────────────────────────────────────────
class SegmentedControl(QWidget):
    """Choix exclusif en segments, avec un pouce qui ressort vers le choix fait."""

    currentIndexChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("focusRing", True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._items: list[tuple[str, object]] = []
        self._index = 0
        self._position = 0.0
        self._from = 0.0
        self._target = 0.0
        self._segment = scaled(78)
        self._height = scaled(34)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._hover_index = -1
        self._hover_paint_index = -1
        self._hover_value = 0.0
        self._hover_pointer = QPointF()
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(200)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation.valueChanged.connect(self._on_hover_frame)
        self._hover_animation.finished.connect(self._on_hover_finished)
        ThemeManager.instance().changed.connect(self._on_theme)

    # ── Contenu et API de la liste déroulante remplacée ─────────────────────
    def addItem(self, text: str, data: object = None) -> None:  # noqa: N802
        self._items.append((text, data))
        self._apply_metrics()

    def setItemText(self, index: int, text: str) -> None:  # noqa: N802
        """Met à jour un libellé et recalcule la largeur des segments."""
        if not 0 <= index < len(self._items):
            return
        _old_text, data = self._items[index]
        self._items[index] = (text, data)
        self._apply_metrics()
        self.update()

    def itemText(self, index: int) -> str:  # noqa: N802
        return self._items[index][0] if 0 <= index < len(self._items) else ""

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def currentData(self) -> object:  # noqa: N802
        return self._items[self._index][1] if self._items else None

    def currentText(self) -> str:  # noqa: N802
        return self._items[self._index][0] if self._items else ""

    def findData(self, data: object) -> int:  # noqa: N802
        for index, (_text, value) in enumerate(self._items):
            if value == data:
                return index
        return -1

    def setCurrentIndex(self, index: int, *, animate: bool = True) -> None:  # noqa: N802
        if not 0 <= index < len(self._items) or index == self._index:
            return
        self._index = index
        if not animate or not self.isVisible() or ThemeManager.instance().reduce_motion():
            self._timer.stop()
            self._position = self._target = float(index)
            self.update()
        else:
            self._from = self._position
            self._target = float(index)
            self._clock.restart()
            self._timer.start()
        self.currentIndexChanged.emit(index)

    # ── Géométrie, horloge, peinture ────────────────────────────────────────
    def _label_font(self, weight: QFont.Weight) -> QFont:
        font = QFont(self.font())
        # Taille calculée à chaque appel : figée à la construction, elle gardait l'échelle
        # de l'ouverture (150 % → 100 %) et les libellés restaient trop grands.
        font.setPixelSize(max(11, round(scaled(13))))
        font.setWeight(weight)
        return font

    def _apply_metrics(self) -> None:
        # Le segment actif est un cran plus appuyé : la largeur doit être calculée avec
        # cette graisse, sinon les libellés longs touchent le bord de la capsule.
        widest = max((QFontMetricsF(self._label_font(SEGMENT_ACTIVE_WEIGHT)).horizontalAdvance(text)
                      for text, _data in self._items), default=0.0)
        self._segment = max(scaled(78), round(widest + scaled(30)))
        self._height = scaled(34)
        self.setFixedSize(self._segment * max(1, len(self._items)), self._height)
        if not self._timer.isActive():
            self._position = self._target = float(self._index)

    def _on_theme(self, _name: str = "") -> None:
        self._apply_metrics()
        self.update()

    def _on_hover_frame(self, value: object) -> None:
        self._hover_value = float(value)
        self.update()

    def _on_hover_finished(self) -> None:
        if self._hover_value <= 0.001:
            self._hover_paint_index = -1
            self._hover_value = 0.0
            self.update()

    def _animate_hover(self, target: float) -> None:
        if ThemeManager.instance().reduce_motion():
            self._hover_animation.stop()
            self._hover_value = float(target)
            if target <= 0.0:
                self._hover_paint_index = -1
            self.update()
            return
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_value)
        self._hover_animation.setEndValue(float(target))
        self._hover_animation.start()

    def _hover_segment_at(self, x: float) -> int:
        if not self._items or not 0.0 <= x < self.width():
            return -1
        return max(0, min(len(self._items) - 1, int(x // self._segment)))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._hover_segment_at(event.position().x())
        if index != self._hover_index:
            self._hover_index = index
            if index >= 0:
                self._hover_paint_index = index
                self._animate_hover(1.0)
            else:
                self._animate_hover(0.0)
        self._hover_pointer = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_index = -1
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover_animation.stop()
        self._hover_index = -1
        self._hover_paint_index = -1
        self._hover_value = 0.0
        super().hideEvent(event)

    def _tick(self) -> None:
        t = self._clock.elapsed() / 1000.0
        self._position = self._from + (self._target - self._from) * spring(16.0, 0.62, t)
        if t > SETTLE_S and abs(self._position - self._target) < 0.002:
            self._position = self._target
            self._timer.stop()
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._timer.isActive():
            self._position = self._target = float(self._index)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if not self._items:
            return
        dark = ThemeManager.instance().theme() == "dark"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        track = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = self.height() / 2.0 - 0.5
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(token("surface_alt")))
        painter.drawRoundedRect(track, radius, radius)

        # Pouce : écrêté dans la piste, liseré clair et ombre courte.
        position = max(0.0, min(float(len(self._items) - 1), self._position))
        thumb = QRectF(self._segment * position + 3.0, 3.5,
                       self._segment - 6.0, self.height() - 7.0)
        soft_ellipse(painter, thumb.center().x(), thumb.center().y() + 2.0,
                     thumb.width() / 2.0, thumb.height() / 3.0, QColor(0, 0, 0, 60),
                     6.0, layers=6)
        base = mix(token("surface_alt"), token("text"), 0.12 if dark else 0.04)
        painter.setBrush(mix(base, "#FFFFFF", 0.16 if dark else 0.72))
        painter.drawRoundedRect(thumb, thumb.height() / 2.0, thumb.height() / 2.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color(token("border"), 0.45), 1.0))
        painter.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5),
                                thumb.height() / 2.0, thumb.height() / 2.0)

        # Reflet de survol : le même halo que les boutons Liquide, limité au segment.
        hover_index = self._hover_paint_index
        if (self._hover_value > 0.01 and 0 <= hover_index < len(self._items)
                and not ThemeManager.instance().reduce_transparency()):
            box = QRectF(self._segment * hover_index, 0.0,
                         float(self._segment), float(self.height()))
            painter.save()
            painter.setClipPath(rounded_path(box.adjusted(2.0, 2.0, -2.0, -2.0), radius - 1.0))
            tint = QColor(255, 255, 255) if dark else QColor(token("text"))
            peak = int((22 if dark else 30) * self._hover_value)
            glow = QRadialGradient(self._hover_pointer, max(box.width(), box.height()) * 0.9)
            glow.setColorAt(0.0, QColor(tint.red(), tint.green(), tint.blue(), peak))
            glow.setColorAt(0.5, QColor(tint.red(), tint.green(), tint.blue(), max(1, peak // 3)))
            glow.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
            painter.fillRect(box, QBrush(glow))
            if dark:
                painter.setPen(QPen(QColor(255, 255, 255, int(34 * self._hover_value)), 1.0))
                painter.drawLine(QPointF(box.left() + radius, box.top() + 0.7),
                                 QPointF(box.right() - radius, box.top() + 0.7))
            painter.restore()

        # Libellés : gris régulier → texte un peu plus appuyé à mesure que le pouce arrive.
        for index, (text, _data) in enumerate(self._items):
            contrast = clamp01(1.0 - abs(position - index))
            box = QRectF(self._segment * index, 0.0, float(self._segment), float(self.height()))
            if contrast < 1.0:
                painter.setOpacity(1.0 - contrast)
                painter.setFont(self._label_font(SEGMENT_TEXT_WEIGHT))
                painter.setPen(color(token("muted")))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
            if contrast > 0.0:
                painter.setOpacity(contrast)
                painter.setFont(self._label_font(SEGMENT_ACTIVE_WEIGHT))
                painter.setPen(color(token("text")))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
            painter.setOpacity(1.0)
        if self.hasFocus() and self.property("keyboardFocus") is True:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color(token("focus")), 2.0))
            painter.drawRoundedRect(QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0),
                                    radius - 1.0, radius - 1.0)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._items and event.button() == Qt.MouseButton.LeftButton:
            index = max(0, min(len(self._items) - 1, int(event.position().x() // self._segment)))
            self.setCurrentIndex(index)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if event.key() == Qt.Key.Key_Left else 1
            self.setCurrentIndex(max(0, min(len(self._items) - 1, self._index + step)))
            return
        super().keyPressEvent(event)


class AnimatedButtonStrip(QWidget):
    """Boutons existants avec un pouce qui glisse, sans changer leur forme ni leur survol.

    Le pouce reprend la course ressortie du sélecteur de thèmes ; les boutons restent
    au-dessus, avec leur feuille de style, leur rayon et leur reflet ``LiquidButton``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        # Marge extérieure : les coins du pouce ne touchent pas le bord du widget.
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(4)
        self._buttons: list[QWidget] = []
        self._index = 0
        self._rect = QRectF()
        self._from = QRectF()
        self._to = QRectF()
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ThemeManager.instance().changed.connect(self._on_theme)

    def add_button(self, button: QWidget) -> None:
        self._buttons.append(button)
        self._layout.addWidget(button)
        if not self._timer.isActive():
            self._snap()

    def current_index(self) -> int:
        return self._index

    def refresh(self) -> None:
        """Recalcule la largeur après un changement de libellé d'un bouton."""
        self.updateGeometry()
        self._layout.invalidate()
        self._layout.activate()
        if self._timer.isActive():
            target = self._button_rect(self._index)
            if not target.isNull():
                self._to = QRectF(target)
        else:
            self._sync_rect()
        self.update()

    def _button_rect(self, index: int) -> QRectF:
        if not 0 <= index < len(self._buttons):
            return QRectF()
        button = self._buttons[index]
        if button.width() <= 0 or button.height() <= 0:
            return QRectF()
        return QRectF(button.geometry())

    def _snap(self) -> None:
        self._timer.stop()
        target = self._button_rect(self._index)
        self._rect = QRectF(target)
        self._from = QRectF(target)
        self._to = QRectF(target)
        self.update()

    def set_current(self, index: int, *, animate: bool = True) -> None:
        """Déplace le pouce vers le bouton ``index`` avec le ressort des thèmes."""
        if not 0 <= index < len(self._buttons):
            return
        self._index = index
        target = self._button_rect(index)
        if target.isNull():
            self._snap()
            return
        if (not animate or not self.isVisible() or ThemeManager.instance().reduce_motion()
                or self._rect.isNull()):
            self._rect = QRectF(target)
            self._from = QRectF(target)
            self._to = QRectF(target)
            self._timer.stop()
            self.update()
            return
        self._from = QRectF(self._rect)
        self._to = QRectF(target)
        self._clock.restart()
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        t = self._clock.elapsed() / 1000.0
        progress = spring(16.0, 0.62, t)
        self._rect = QRectF(
            self._from.x() + (self._to.x() - self._from.x()) * progress,
            self._from.y() + (self._to.y() - self._from.y()) * progress,
            self._from.width() + (self._to.width() - self._from.width()) * progress,
            self._from.height() + (self._to.height() - self._from.height()) * progress,
        )
        if t > SETTLE_S and abs(progress - 1.0) < 0.002:
            self._rect = QRectF(self._to)
            self._timer.stop()
        self.update()

    def _sync_rect(self) -> None:
        if self._timer.isActive():
            return
        target = self._button_rect(self._index)
        if not target.isNull() and target != self._rect:
            self._rect = QRectF(target)
            self._from = QRectF(target)
            self._to = QRectF(target)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        self._sync_rect()
        if self._rect.isNull() or self._rect.width() <= 2.0:
            return
        dark = ThemeManager.instance().theme() == "dark"
        rect = QRectF(self._rect)
        radius = min(6.0, rect.height() / 2.0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(self.rect())
        shadow = rect.translated(0.0, 1.2).adjusted(0.2, 0.2, -0.2, 0.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 20 if dark else 12))
        painter.drawRoundedRect(shadow, radius, radius)
        base = mix(token("surface_alt"), token("text"), 0.12 if dark else 0.04)
        painter.setBrush(mix(base, "#FFFFFF", 0.16 if dark else 0.72))
        painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color(token("border"), 0.45), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.end()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._snap()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._timer.isActive():
            target = self._button_rect(self._index)
            if not target.isNull():
                self._to = QRectF(target)
            return
        self._sync_rect()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Bulle de valeur : elle accompagne le curseur pendant le glissement
# ─────────────────────────────────────────────────────────────────────────────
class ValueBubble(QLabel):
    """Petite bulle posée au-dessus du curseur, visible pendant le glissement."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ValueBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"QLabel#ValueBubble {{ background-color: {token('accent')};"
            f" color: {INK_ON_ACCENT}; border-radius: {scaled(9)}px;"
            f" padding: {scaled(2)}px {scaled(8)}px; font-weight: 700; }}")


class _BubbleLink(QObject):
    """Crée la bulle au premier glissement et la cache quand le curseur disparaît."""

    def __init__(self, slider: QSlider, formatter) -> None:
        super().__init__(slider)
        self._slider = slider
        self._formatter = formatter
        self._bubble: ValueBubble | None = None

    def _ensure(self) -> ValueBubble:
        if self._bubble is None:
            self._bubble = ValueBubble(self._slider.window())
        return self._bubble

    def show(self) -> None:
        bubble = self._ensure()
        bubble.setText(str(self._formatter()))
        self._position(bubble)
        bubble.show()
        bubble.raise_()

    def follow(self, _value: int) -> None:
        if self._bubble is not None and self._bubble.isVisible():
            self._bubble.setText(str(self._formatter()))
            self._position(self._bubble)

    def hide(self) -> None:
        if self._bubble is not None:
            self._bubble.hide()

    def _position(self, bubble: ValueBubble) -> None:
        slider = self._slider
        window = slider.window()
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        handle = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider)
        hint = bubble.sizeHint()
        bubble.resize(max(hint.width(), scaled(34)), hint.height())
        centre = slider.mapTo(window, handle.center())
        top = slider.mapTo(window, QPoint(0, handle.top())).y()
        x = max(0, min(window.width() - bubble.width(), centre.x() - bubble.width() // 2))
        bubble.move(x, max(0, top - bubble.height() - scaled(3)))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._slider and event.type() in (QEvent.Type.Hide, QEvent.Type.UngrabMouse):
            self.hide()
        return False


def attach_value_bubble(slider: QSlider, formatter) -> _BubbleLink:
    """Colle une bulle de valeur au curseur ; elle suit le pouce pendant le glissement."""
    link = _BubbleLink(slider, formatter)
    slider.sliderPressed.connect(link.show)
    slider.valueChanged.connect(link.follow)
    slider.sliderReleased.connect(link.hide)
    slider.installEventFilter(link)
    return link


# ─────────────────────────────────────────────────────────────────────────────
# Bouton Copier : balayage puis face « Copié », retour à l'état de repos
# ─────────────────────────────────────────────────────────────────────────────
class CopyFeedbackButton(LiquidButton):
    """Bouton d'action dont la face devient « Copié » après une copie réussie."""

    HOLD_MS = 1100

    def __init__(self, text: str, parent: QWidget | None = None, *,
                 copied_text: str | None = None) -> None:
        super().__init__(text, parent)
        self._base_text = text
        self._copied_text = copied_text or self.tr("Copié")
        self._base_icon: QIcon = self.icon()
        self._sweep = 0.0
        self._sweep_anim = QVariantAnimation(self)
        self._sweep_anim.setStartValue(0.0)
        self._sweep_anim.setEndValue(1.0)
        self._sweep_anim.setDuration(380)
        self._sweep_anim.valueChanged.connect(self._on_sweep)
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore)
        self.clicked.connect(self._start_feedback)

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        width = max(self.fontMetrics().horizontalAdvance(self._base_text),
                    self.fontMetrics().horizontalAdvance(self._copied_text) + scaled(22))
        hint.setWidth(hint.width() + max(0, width - self.fontMetrics().horizontalAdvance(
            self._base_text)))
        return hint

    def _start_feedback(self) -> None:
        self._restore_timer.stop()
        self._restore_timer.start(self.HOLD_MS)
        if ThemeManager.instance().reduce_motion():
            self._switch_face()
            return
        self._sweep = 0.0
        self._sweep_anim.stop()
        self._sweep_anim.start()
        QTimer.singleShot(110, self._switch_face)

    def _switch_face(self) -> None:
        if not self._restore_timer.isActive():
            return
        self.setText(self._copied_text)
        if icons.available("check"):
            self.setIcon(icons.icon("check", "text", scaled(16)))

    def _on_sweep(self, value: object) -> None:
        self._sweep = float(value)
        self.update()

    def _restore(self) -> None:
        self.setText(self._base_text)
        self.setIcon(self._base_icon)
        self._sweep = 0.0
        self._sweep_anim.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not 0.0 < self._sweep < 1.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(_radius(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                    scaled(8)))
        band = self.width() * 0.45
        x = -band + (self.width() + 2 * band) * ease(self._sweep)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(token("accent"), 0.35 * math.sin(math.pi * self._sweep)))
        painter.drawRect(QRectF(x - band / 2.0, 0.0, band, float(self.height())))
        painter.end()
