"""Moteur de mouvement partagé par les commandes animées d'Azimut.

Ressorts amortis, écrasements, fusion de gouttes et géométrie des rectangles arrondis :
ces primitives sont utilisées par ``azimut.ui.controls`` pour les interrupteurs, les
bulles de curseur et le bouton Copier. Le thème,
la palette et le réglage « Réduire les animations » restent gérés par les contrôles et
``ThemeManager``.
"""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)



# ─────────────────────────────────────────────────────────────────────────────
# Géométrie, couleurs, texte
# ─────────────────────────────────────────────────────────────────────────────
def clamp01(value: float) -> float:
    """Valeur ramenée dans 0–1."""
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k


def seg(x: float, a: float, b: float) -> float:
    """Position de ``x`` dans l'intervalle ``a..b``, ramenée dans 0–1."""
    if b == a:
        return 1.0
    return clamp01((x - a) / (b - a))


def ease(k: float) -> float:
    k = clamp01(k)
    return 1.0 - (1.0 - k) ** 3


def ease_in(k: float) -> float:
    k = clamp01(k)
    return k ** 3


def ease_io(k: float) -> float:
    k = clamp01(k)
    return 4.0 * k ** 3 if k < 0.5 else 1.0 - (-2.0 * k + 2.0) ** 3 / 2.0


def ease_back(k: float) -> float:
    k = clamp01(k)
    return 1.0 + 2.9 * (k - 1.0) ** 3 + 1.9 * (k - 1.0) ** 2


def bump(x: float, a: float, b: float) -> float:
    return math.sin(math.pi * seg(x, a, b))


def spring(omega0: float, zeta: float, t: float) -> float:
    """Réponse d'un ressort amorti : 0 avant le déclenchement, puis 0 → 1 avec dépassement."""
    if t <= 0.0:
        return 0.0
    omega_d = omega0 * math.sqrt(max(1e-9, 1.0 - zeta * zeta))
    decay = math.exp(-zeta * omega0 * t)
    return 1.0 - decay * (math.cos(omega_d * t) + (zeta * omega0 / omega_d) * math.sin(omega_d * t))


def color(value: str | QColor, alpha: float | None = None) -> QColor:
    """Couleur depuis un hexadécimal, avec alpha éventuel (0–1)."""
    result = QColor(value) if isinstance(value, str) else QColor(value)
    if alpha is not None:
        result.setAlpha(max(0, min(255, round(alpha * 255))))
    return result


def mix(a: str | QColor, b: str | QColor, k: float) -> QColor:
    """Mélange linéaire par canal, arrondi (comme ``mix`` des spécifications)."""
    k = clamp01(k)
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * k),
        round(ca.green() + (cb.green() - ca.green()) * k),
        round(ca.blue() + (cb.blue() - ca.blue()) * k),
    )


def mix_hex(a: str, b: str, k: float) -> str:
    return mix(a, b, k).name()


@lru_cache(maxsize=96)
def _font(size: int, weight: int) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "sans-serif"])
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(max(1, int(size)))
    font.setWeight(QFont.Weight(int(weight)))
    return font


@lru_cache(maxsize=512)
def _advance(value: str, size: int, weight: int) -> float:
    return QFontMetricsF(_font(size, weight)).horizontalAdvance(value)


def text_width(value: str, size: int, weight: int = 500) -> float:
    return _advance(value, int(size), int(weight))


def draw_text(painter: QPainter, value: str, x: float, y: float, size: int,
              weight: int, fill: str | QColor, align: str = "left") -> None:
    """Texte à sa ligne de base ; ``align`` vaut left, center ou right."""
    font = _font(int(size), int(weight))
    painter.setFont(font)
    painter.setPen(QColor(fill) if isinstance(fill, str) else fill)
    width = text_width(value, size, weight)
    if align == "center":
        x -= width / 2.0
    elif align == "right":
        x -= width
    painter.drawText(QPointF(x, y), value)


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    limit = min(rect.width(), rect.height()) / 2.0
    path = QPainterPath()
    path.addRoundedRect(rect, max(0.0, min(radius, limit)), max(0.0, min(radius, limit)))
    return path


def top_rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rectangle dont seuls les deux coins supérieurs sont arrondis."""
    r = max(0.0, min(radius, rect.width() / 2.0, rect.height()))
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + r)
    path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
    path.lineTo(rect.right() - r, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path


def rr(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    return rounded_path(QRectF(x, y, w, h), r)


def fill_rr(painter: QPainter, x: float, y: float, w: float, h: float, r: float,
            brush: QBrush | QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush if isinstance(brush, QBrush) else QBrush(brush))
    painter.drawRoundedRect(QRectF(x, y, w, h), min(r, w / 2, h / 2), min(r, w / 2, h / 2))


def stroke_rr(painter: QPainter, x: float, y: float, w: float, h: float, r: float,
              pen: QPen) -> None:
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(x, y, w, h), min(r, w / 2, h / 2), min(r, w / 2, h / 2))


# ─────────────────────────────────────────────────────────────────────────────
# Ombres douces (QPainter n'a pas de shadowBlur : on empile des formes dilatées)
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=48)
def _rrect_shadow(w: int, h: int, radius: int, blur: int, argb: int,
                  layers: int) -> QImage:
    """Ombre d'un rectangle arrondi, rendue une fois par géométrie et par couleur."""
    margin = int(math.ceil(blur * 1.4)) + 2
    image = QImage(int(w) + 2 * margin, int(h) + 2 * margin,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    base = QColor.fromRgba(argb)
    alpha = base.alpha()
    for i in range(layers, 0, -1):
        t = i / layers
        grow = blur * t
        layer = QColor(base)
        layer.setAlpha(max(1, round(alpha * 0.10 * (1.0 - t) ** 1.4)))
        painter.setBrush(layer)
        painter.drawRoundedRect(
            QRectF(margin - grow, margin - grow, w + 2 * grow, h + 2 * grow),
            radius + grow, radius + grow)
    painter.end()
    return image


def soft_rrect(painter: QPainter, x: float, y: float, w: float, h: float, r: float,
               fill: QColor, blur: float, dx: float = 0.0, dy: float = 0.0,
               layers: int = 12) -> None:
    base = QColor(fill)
    alpha = base.alpha()
    if alpha <= 0:
        return
    margin = int(math.ceil(blur * 1.4)) + 2
    image = _rrect_shadow(max(1, round(w)), max(1, round(h)), max(0, round(r)),
                          max(1, round(blur)), base.rgba(), int(layers))
    painter.drawImage(QPointF(x - margin + dx, y - margin + dy), image)


def soft_ellipse(painter: QPainter, cx: float, cy: float, rx: float, ry: float,
                 fill: QColor, blur: float, dy: float = 0.0, layers: int = 12) -> None:
    base = QColor(fill)
    alpha = base.alpha()
    if alpha <= 0 or rx <= 0 or ry <= 0:
        return
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(layers, 0, -1):
        t = i / layers
        grow = blur * t
        layer = QColor(base)
        layer.setAlpha(max(1, round(alpha * 0.10 * (1.0 - t) ** 1.4)))
        painter.setBrush(layer)
        painter.drawEllipse(QPointF(cx, cy + dy), rx + grow, ry + grow)
    painter.restore()


def gradient_glow(painter: QPainter, x: float, y: float, w: float, h: float, r: float,
                  start: str, end: str, alpha: float, blur: float, dy: float = 0.0,
                  layers: int = 12) -> None:
    """Halo d'un rectangle arrondi, dégradé horizontal, sans filtre de flou."""
    if alpha <= 0.0:
        return
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(x, y + dy, w, h)
    for i in range(layers, 0, -1):
        t = i / layers
        grow = blur * t
        grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        a = max(1, round(alpha * 255 * 0.12 * (1.0 - t) ** 1.4))
        grad.setColorAt(0.0, color(start, a / 255))
        grad.setColorAt(1.0, color(end, a / 255))
        pen = QPen(QBrush(grad), 2.0 * grow + 1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, r, r)
    painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# Indicateur de frappe (le doigt des maquettes)
# ─────────────────────────────────────────────────────────────────────────────
TAP_RADIUS = 22.575


def tap_state(a: float) -> tuple[float, float] | None:
    """Échelle et opacité du doigt à ``a`` secondes de la frappe, ou ``None``."""
    if a < -0.42 or a >= 0.4:
        return None
    if a < -0.1:
        scale = lerp(1.25, 1.0, ease((a + 0.42) / 0.32))
    elif a < 0.0:
        scale = lerp(1.0, 0.8, (a + 0.1) / 0.1)
    else:
        scale = lerp(0.8, 1.0, ease(a / 0.2))
    if a < 0.0:
        opacity = clamp01((a + 0.42) / 0.18)
    else:
        opacity = clamp01(1.0 - (a - 0.12) / 0.25)
    return scale, opacity


def draw_tap(painter: QPainter, x: float, y: float, a: float, spread: float = 3.2) -> None:
    """Cercle de frappe et son onde, centrés sur (x, y), à ``a`` secondes de la frappe."""
    state = tap_state(a)
    if state is None:
        return
    scale, opacity = state
    if 0.0 <= a < 0.4:
        radius = TAP_RADIUS * (1.0 + spread * a)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, round(255 * 0.7 * (1.0 - a / 0.4))), 3.0))
        painter.drawEllipse(QPointF(x, y), radius, radius)
        painter.restore()

    painter.save()
    painter.translate(x, y)
    painter.scale(scale, scale)
    painter.setOpacity(opacity)
    soft_ellipse(painter, 0.0, 0.0, TAP_RADIUS, TAP_RADIUS, QColor(0, 0, 0, 89), 10.0,
                 dy=0.0, layers=8)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(215, 215, 230, 97))                 # rgba(215,215,230,0.38)
    painter.drawEllipse(QPointF(0.0, 0.0), TAP_RADIUS, TAP_RADIUS)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor(255, 255, 255, 235), 3.5)                  # white 92 %
    painter.setPen(pen)
    painter.drawEllipse(QPointF(0.0, 0.0), TAP_RADIUS, TAP_RADIUS)
    painter.restore()
