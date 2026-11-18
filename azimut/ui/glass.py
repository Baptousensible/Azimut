"""Effet « Liquid Glass » pour Qt : verre réfractant, liserés de lumière et reflets.

Qt ne connaît ni ``backdrop-filter`` ni ``feDisplacementMap``. L'effet est donc composé
avec QPainter, à partir des mêmes ingrédients que le composant React de référence
(rdev/liquid-glass-react) :

* un arrière-plan capturé, réduit et flouté (le « gel » du verre) ;
* un voile de couleur translucide qui préserve la lisibilité du texte ;
* un anneau de bord qui rééchantillonne le fond à une autre échelle (la réfraction) ;
* deux liserés de lumière, une ombre interne et un reflet qui suit le curseur ;
* des dialogues qui laissent voir la fenêtre parente, floutée, derrière leur feuille.

Aucune capture n'est lancée pendant une capture (garde ``_CAPTURE_DEPTH``) : sans cela,
le rendu de l'arrière-plan se rappellerait lui-même à l'infini. Les widgets en verre
peignent alors un aplat, ce qui suffit à l'image floutée.
"""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import (
    QEasingCurve, QElapsedTimer, QObject, QPoint, QPointF, QRectF, QTimer, QVariantAnimation, Qt,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QWidget,
)

from azimut.core.logs import get_logger
from azimut.core.screenfit import fit_to_screen
from azimut.ui.icon_motion import HoverIcon, HoverTabIcons
from azimut.ui.theme import ThemeManager, aurora_layers, glass_style, scaled, token

log = get_logger(__name__)

BACKDROP_SCALE = 3          # l'arrière-plan est capturé à 1/3 : flou bon marché, rendu lissé
BLUR_RADIUS = 6.0           # en pixels de l'image réduite (≈ 18 px à l'échelle réelle)
_CACHE_ATTR = "_azimut_backdrop"
_CAPTURE_DEPTH = 0          # > 0 : une capture est en cours, on ne capture pas à nouveau


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _theme() -> str:
    return ThemeManager.instance().theme()


@lru_cache(maxsize=4)
def _style_for(theme: str, reduce_transparency: bool) -> dict[str, str]:
    """Jetons du verre immuables pour un thème : évite de reconstruire le dictionnaire à chaque frame."""
    return glass_style(theme, reduce_transparency)


def _style() -> dict[str, str]:
    manager = ThemeManager.instance()
    return _style_for(manager.theme(), manager.reduce_transparency())


def _hover_tint() -> QColor:
    """Couleur du halo de survol : lumière sur fond sombre, encre sur fond clair."""
    return QColor(255, 255, 255) if _theme() == "dark" else QColor(token("text"))


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rectangle arrondi, rayon borné à la moitié de la plus petite dimension."""
    limit = min(rect.width(), rect.height()) / 2
    path = QPainterPath()
    path.addRoundedRect(rect, min(radius, limit), min(radius, limit))
    return path


def paint_glass_indicator(painter: QPainter, rect: QRectF, radius: float, *,
                          energy: float = 0.0, phase: float = 0.32) -> None:
    """Peint la capsule de sélection : une surface neutre et un reflet ténu.

    Le mouvement et l'étirement restent ceux d'un liquide ; la brillance et le halo
    sont volontairement réduits pour ne pas concurrencer le contenu. La capsule reste
    nue : aucun trait de couleur ne marque l'onglet, seul le texte le distingue.
    """
    if rect.width() <= 4.0 or rect.height() <= 4.0:
        return
    style = _style()
    dark = _theme() == "dark"
    energy = max(0.0, min(1.0, energy))
    phase = max(0.0, min(1.0, phase))
    radius = max(0.0, min(radius, min(rect.width(), rect.height()) / 2))

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    # Ombre courte, presque imperceptible : la capsule reste posée sur la barre.
    shadow = rect.translated(0.0, 1.2).adjusted(0.2, 0.2, -0.2, 0.0)
    painter.setBrush(QColor(0, 0, 0, 20 if dark else 12))
    painter.drawRoundedRect(shadow, radius, radius)

    # Surface neutre : plus de voile teinté ni de franges chaudes.
    painter.setBrush(QColor(style["glass_panel_alt"]))
    painter.drawRoundedRect(rect, radius, radius)

    painter.save()
    painter.setClipPath(rounded_path(rect, radius))
    shine = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.55))
    shine.setColorAt(0.0, QColor(255, 255, 255, 30 if dark else 46))
    shine.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(shine))

    # Reflet très doux qui accompagne le glissement.
    span = max(rect.height() * 1.8, 20.0)
    center = QPointF(rect.left() + rect.width() * (0.18 + 0.64 * phase),
                     rect.center().y() - rect.height() * 0.08)
    specular = QRadialGradient(center, span)
    specular.setColorAt(0.0, QColor(255, 255, 255, 8 + int(16 * energy)))
    specular.setColorAt(0.55, QColor(255, 255, 255, 3 + int(6 * energy)))
    specular.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(rect, QBrush(specular))
    painter.restore()

    # Liseré fin, sans arête blanche épaisse.
    rim = rounded_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(style["glass_stroke_soft"]), 1.0))
    painter.drawPath(rim)

    painter.restore()


def _cached(window: QWidget) -> "_BackdropCache":
    """Capture partagée par toutes les surfaces en verre d'une même fenêtre."""
    cache = getattr(window, _CACHE_ATTR, None)
    if cache is None:
        cache = _BackdropCache(window)
        setattr(window, _CACHE_ATTR, cache)
    return cache


def backdrop(window: QWidget) -> tuple[QImage, float] | None:
    """Image d'arrière-plan floutée de la fenêtre et son facteur de réduction."""
    return _cached(window).image()


# ─────────────────────────────────────────────────────────────────────────────
# Capture, flou, saturation
# ─────────────────────────────────────────────────────────────────────────────
class _BackdropCache:
    """Environnement flouté d'une fenêtre, recalculé s'il change de taille ou de thème.

    L'environnement est *peint* (dégradé + halos) plutôt que capturé avec ``window.render()``.
    Un rendu de widget depuis un ``paintEvent`` déclenche « Recursive repaint detected » et
    corrompt le tampon ; le verre échantillonne donc exactement le fond que la fenêtre
    affiche, sans récursion et sans dépendre de la visibilité réelle.
    """

    def __init__(self, window: QWidget) -> None:
        self.window = window
        self._image: QImage | None = None
        self._image_key: tuple[str, int, int] | None = None
        self._surface: QPixmap | None = None
        self._surface_key: tuple[str, int, int] | None = None

    def invalidate(self, *_args: object) -> None:
        self._image = None
        self._image_key = None
        self._surface = None
        self._surface_key = None

    def background(self) -> QPixmap | None:
        """Fond complet de la fenêtre, peint une fois par taille et par thème.

        Le fond aurore est riche en dégradés : le repeindre à chaque frame coûtait plusieurs
        millisecondes. Les surfaces en verre continuent d'échantillonner l'image floutée,
        mais les repaints de fenêtre et de page se contentent d'un blit de ce cache. Le cache
        reste à 1× : ces dégradés lisses ne justifient pas une surface Retina plein écran.
        """
        width, height = self.window.width(), self.window.height()
        if width < 16 or height < 16:
            return None
        key = (_theme(), width, height)
        if self._surface is not None and self._surface_key == key:
            return self._surface
        surface = QPixmap(width, height)
        surface.fill(Qt.GlobalColor.transparent)
        painter = QPainter(surface)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing
                               | QPainter.RenderHint.SmoothPixmapTransform)
        _paint_environment_raw(self.window, painter)
        painter.end()
        self._surface, self._surface_key = surface, key
        self._image, self._image_key = None, None      # l'image floutée dépend du fond
        return surface

    def image(self) -> tuple[QImage, float] | None:
        key = (_theme(), self.window.width(), self.window.height())
        if self._image is None or self._image_key != key:
            image = _environment_image(self.window)
            if image is None:
                return None
            self._image, self._image_key = image, key
        return self._image, float(BACKDROP_SCALE)


def _environment_image(window: QWidget) -> QImage | None:
    """Fond de la fenêtre (dégradé + aurore) réduit, flouté et saturé."""
    width, height = window.width(), window.height()
    if width < 16 or height < 16:
        return None
    scaled_width = max(1, width // BACKDROP_SCALE)
    scaled_height = max(1, height // BACKDROP_SCALE)
    image = QImage(scaled_width, scaled_height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(scaled_width / width, scaled_height / height)
    _paint_environment_raw(window, painter)
    painter.end()
    return _saturate(_blur(image, BLUR_RADIUS))


def capture_content(window: QWidget) -> QImage | None:
    """Fenêtre entière, floutée : le fond des dialogues (hors peinture, jamais réentrant)."""
    global _CAPTURE_DEPTH
    if _CAPTURE_DEPTH:
        return None
    width, height = window.width(), window.height()
    if width < 16 or height < 16 or not window.isVisible():
        return None
    scaled_width = max(1, width // BACKDROP_SCALE)
    scaled_height = max(1, height // BACKDROP_SCALE)
    image = QImage(scaled_width, scaled_height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                           | QPainter.RenderHint.TextAntialiasing)
    painter.scale(scaled_width / width, scaled_height / height)
    _CAPTURE_DEPTH += 1
    try:
        window.render(painter, QPoint(0, 0), QRegion(),
                      QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren)
    except RuntimeError as exc:
        log.debug("Capture du contenu impossible : %s", exc)
        return None
    finally:
        _CAPTURE_DEPTH -= 1
        painter.end()
    return _saturate(_blur(image, BLUR_RADIUS + 1.0))


def _blur(image: QImage, radius: float) -> QImage:
    """Flou gaussien qualité via QGraphicsBlurEffect, rendu hors scène."""
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(painter, QRectF(out.rect()), QRectF(image.rect()))
    painter.end()
    item.setGraphicsEffect(None)
    return out


def _saturate(image: QImage) -> QImage:
    """Relève saturation et contraste, comme le ``saturate()`` du verre Apple."""
    out = image.copy()
    painter = QPainter(out)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
    painter.setOpacity(0.18)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Arrière-plan « aurore » : ce que le verre a à montrer
# ─────────────────────────────────────────────────────────────────────────────
def paint_environment(widget: QWidget, painter: QPainter | None = None) -> None:
    """Peint le fond complet (dégradé + halos) depuis le cache de la fenêtre.

    La page, la fenêtre et le verre appellent tous cette fonction : l'intérieur d'une
    carte et le fond qu'elle recouvre ont donc exactement la même couleur, ce qui rend
    la réfraction crédible au lieu d'un simple aplat plus clair.
    """
    window = widget.window()
    surface = _cached(window).background()
    target = painter
    owned = target is None
    if target is None:
        target = QPainter(widget)
    if surface is None:                     # fenêtre minuscule : pas de cache, peinture directe
        _paint_environment_raw(widget, target)
    else:
        origin = QPoint(0, 0) if widget is window else widget.mapTo(window, QPoint(0, 0))
        target.save()
        target.setClipRect(widget.rect())
        target.translate(-origin.x(), -origin.y())
        target.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        target.drawPixmap(QPointF(0.0, 0.0), surface)
        target.restore()
    if owned:
        target.end()


def _paint_environment_raw(widget: QWidget, target: QPainter) -> None:
    """Peint le fond sans passer par le cache : utilisé pour le construire."""
    style = _style()
    window = widget.window()
    origin = QPoint(0, 0) if widget is window else widget.mapTo(window, QPoint(0, 0))
    width = max(1, window.width())
    height = max(1, window.height())
    target.save()
    target.setClipRect(widget.rect())
    target.translate(-origin.x(), -origin.y())
    gradient = QLinearGradient(QPointF(0.0, 0.0), QPointF(width * 0.35, float(height)))
    gradient.setColorAt(0.0, QColor(style["bg_top"]))
    gradient.setColorAt(0.55, QColor(style["bg"]))
    gradient.setColorAt(1.0, QColor(style["bg_bottom"]))
    target.fillRect(QRectF(0.0, 0.0, float(width), float(height)), QBrush(gradient))
    target.restore()
    paint_aurora(widget, target)


def paint_aurora(widget: QWidget, painter: QPainter | None = None) -> None:
    """Peint les halos colorés de l'arrière-plan, ancrés dans la fenêtre.

    Le repère est celui de la fenêtre, pas celui du widget : un panneau défilant,
    une barre et une carte voient donc le même halo se prolonger, sans couture.
    Avec « Réduire la transparence », le fond reste un dégradé opaque, sans halo.
    """
    if ThemeManager.instance().reduce_transparency():
        return
    target = painter
    owned = target is None
    if target is None:
        target = QPainter(widget)
    window = widget.window()
    origin = QPoint(0, 0) if widget is window else widget.mapTo(window, QPoint(0, 0))
    width = max(1, window.width())
    height = max(1, window.height())
    target.save()
    target.setRenderHint(QPainter.RenderHint.Antialiasing)
    target.setPen(Qt.PenStyle.NoPen)
    target.setClipRect(widget.rect())
    for fx, fy, fr, color, alpha in aurora_layers(_theme()):
        center = QPointF(origin.x() + fx * width, origin.y() + fy * height)
        gradient = QRadialGradient(center, max(1.0, fr * max(width, height)))
        core = QColor(color)
        core.setAlpha(alpha)
        middle = QColor(color)
        middle.setAlpha(max(1, alpha // 3))
        edge = QColor(color)
        edge.setAlpha(0)
        gradient.setColorAt(0.0, core)
        gradient.setColorAt(0.55, middle)
        gradient.setColorAt(1.0, edge)
        target.fillRect(widget.rect(), QBrush(gradient))
    target.restore()
    if owned:
        target.end()


class AuroraWindowMixin:
    """Fenêtre dont le fond porte les halos d'aurore, sous les widgets enfants."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        paint_environment(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cache = getattr(self, _CACHE_ATTR, None)
        if cache is not None:
            cache.invalidate()


class AuroraPanel(QWidget):
    """Page opaque (palette conservée pour la barre de défilement) au fond d'environnement."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        paint_environment(self)


# ─────────────────────────────────────────────────────────────────────────────
# Survol animé
# ─────────────────────────────────────────────────────────────────────────────
class _HoverAnimator(QObject):
    """Valeur 0 → 1 animée, utilisée par les reflets de survol."""

    # Le halo repeint toute la carte : 30 images/s suffisent pour un fondu de 220 ms.
    FRAME_MS = 33

    def __init__(self, widget: QWidget, duration: int = 220) -> None:
        super().__init__(widget)
        self.value = 0.0
        self._widget = widget
        self._clock = QElapsedTimer()
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(duration)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._apply)

    def _apply(self, value: object) -> None:
        self.value = float(value)
        if (self.value <= 0.001 or self.value >= 0.999 or not self._clock.isValid()
                or self._clock.elapsed() >= self.FRAME_MS):
            self._clock.restart()
            self._widget.update()

    def to(self, target: float) -> None:
        if ThemeManager.instance().reduce_motion():
            self._animation.stop()
            self.value = float(target)
            self._widget.update()
            return
        self._animation.stop()
        self._clock.invalidate()
        self._animation.setStartValue(self.value)
        self._animation.setEndValue(float(target))
        self._animation.start()

    def reset(self) -> None:
        """Éteint le reflet sans animation (widget masqué ou page quittée)."""
        self._animation.stop()
        self._clock.invalidate()
        self.value = 0.0
        self._widget.update()


# ─────────────────────────────────────────────────────────────────────────────
# Surface en verre
# ─────────────────────────────────────────────────────────────────────────────
class GlassFrame(QFrame):
    """Cadre en verre : arrière-plan réfracté, voile teinté, liserés et relief.

    ``interactive`` ajoute le reflet qui suit le curseur (cartes cliquables, zones de
    dépôt). Le cadre reste un QFrame : la feuille de style garde la main sur les
    bordures spécifiques (tirets d'une zone de dépôt, anneau de focus clavier).
    """

    def __init__(self, parent: QWidget | None = None, *, radius: float = 8,
                 interactive: bool = False) -> None:
        super().__init__(parent)
        self._glass_radius = float(radius)
        self._glass_interactive = bool(interactive)
        self._glass_pointer = QPointF()
        self._glass_pointer_set = False
        self._hover = _HoverAnimator(self)
        # Un trackpad peut envoyer bien plus de 60 déplacements par seconde, même avec un
        # doigt posé. Repeindre la carte entière à chaque événement affamait le curseur :
        # les reflets sont regroupés à une frame au plus toutes les 33 ms.
        self._pointer_timer = QTimer(self)
        self._pointer_timer.setSingleShot(True)
        self._pointer_timer.setInterval(33)
        self._pointer_timer.timeout.connect(self.update)
        self._hover_check = QTimer(self)
        self._hover_check.setSingleShot(True)
        self._hover_check.timeout.connect(self._sync_hover)
        ThemeManager.instance().accessibility_changed.connect(self.update)
        if interactive:
            self.setMouseTracking(True)

    # ── Survol ──────────────────────────────────────────────────────────────
    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if self._glass_interactive:
            self._hover_check.stop()
            self._hover.to(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        if not self._glass_interactive:
            return
        # Un Leave peut précéder l'ouverture d'une boîte modale ou le passage sur un
        # enfant : on diffère la décision pour ne pas laisser le reflet allumé.
        self._pointer_timer.stop()
        self._hover_check.start(0)

    def _schedule_hover_check(self) -> None:
        if self._glass_interactive:
            self._hover_check.start(0)

    def _sync_hover(self) -> None:
        """Recale le reflet sur le curseur réel après un clic ou un changement de fenêtre."""
        if not self._glass_interactive:
            return
        if not self.isVisible():
            self._hover.reset()
            self._glass_pointer_set = False
            return
        if QApplication.activeModalWidget() is not None:
            self._hover.to(0.0)
            return
        under = QApplication.widgetAt(QCursor.pos())
        if under is not None and (under is self or self.isAncestorOf(under)):
            self._hover.to(1.0)
        else:
            self._hover.to(0.0)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._schedule_hover_check()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._glass_interactive:
            position = event.position()
            # Un doigt posé fait trembler le trackpad de quelques dixièmes de pixel :
            # ces mouvements ne déplacent pas le reflet de façon visible.
            moved = not self._glass_pointer_set or (
                position - self._glass_pointer).manhattanLength() >= 1.0
            self._glass_pointer = position
            self._glass_pointer_set = True
            if moved and self._hover.value > 0.0 and not self._pointer_timer.isActive():
                self._pointer_timer.start()
        super().mouseMoveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        """Le changement d'onglet masque la page : couper le reflet au lieu de le laisser figé."""
        super().hideEvent(event)
        if self._glass_interactive:
            self._hover_check.stop()
            self._pointer_timer.stop()
            self._hover.reset()
            self._glass_pointer_set = False

    # ── Peinture ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802
        # En Azimut, l'en-tête de module se fond volontairement dans la surface.
        if self.property("embedded") is True or self.property("flat") is True:
            super().paintEvent(event)
            return
        if ThemeManager.instance().reduce_transparency():
            # Réglage système : une plaque opaque, sans capture ni flou.
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = rounded_path(rect, self._glass_radius)
            painter.fillPath(path, QColor(token("surface")))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(token("border")), 1.0))
            painter.drawPath(path)
            painter.end()
            super().paintEvent(event)
            return
        if not _CAPTURE_DEPTH:             # pendant une capture : transparent, pas de récursion
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._paint_glass(painter)
            finally:
                painter.end()
        super().paintEvent(event)          # bordures QSS (tirets, focus) par-dessus

    def _paint_glass(self, painter: QPainter) -> None:
        style = _style()
        dark = _theme() == "dark"
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self._glass_radius
        path = rounded_path(rect, radius)
        on_dialog = isinstance(self.window(), GlassDialog)
        source = None if on_dialog else backdrop(self.window())

        # 1. Le fond, capturé puis flouté, ou un dégradé de repli.
        painter.save()
        painter.setClipPath(path)
        if source is not None:
            image, scale = source
            origin = self.mapTo(self.window(), QPoint(0, 0))
            area = QRectF(origin.x() / scale, origin.y() / scale,
                          self.width() / scale, self.height() / scale)
            painter.drawImage(rect, image, area)
        elif not on_dialog:
            base = QLinearGradient(rect.topLeft(), rect.bottomRight())
            base.setColorAt(0.0, QColor(style["bg_top"]))
            base.setColorAt(1.0, QColor(style["bg_bottom"]))
            painter.fillPath(path, QBrush(base))

        # 2. Voile neutre : le verre laisse passer la lumière sans teinter le contenu.
        painter.fillPath(path, QColor(style["glass_panel"]))

        # 3. Réfraction : le fond est rééchantillonné dans l'anneau du bord.
        if source is not None:
            image, scale = source
            origin = self.mapTo(self.window(), QPoint(0, 0))
            area = QRectF(origin.x() / scale, origin.y() / scale,
                          self.width() / scale, self.height() / scale)
            inset = max(3.0, min(12.0, min(rect.width(), rect.height()) * 0.07))
            inner = rect.adjusted(inset, inset, -inset, -inset)
            ring = path.subtracted(rounded_path(inner, max(1.0, radius - inset)))
            painter.save()
            painter.setClipPath(ring, Qt.ClipOperation.IntersectClip)
            bend = rect.adjusted(-rect.width() * 0.012, -rect.height() * 0.012,
                                 rect.width() * 0.012, rect.height() * 0.012)
            painter.drawImage(bend, image, area)
            painter.restore()

        # 4. Relief : lumière en haut, ombre portée interne en bas, sans frange colorée.
        shade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shade.setColorAt(0.0, QColor(255, 255, 255, 18 if dark else 34))
        shade.setColorAt(0.30, QColor(255, 255, 255, 0))
        shade.setColorAt(0.70, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 28 if dark else 18))
        painter.fillPath(path, QBrush(shade))

        # 5. Reflet spéculaire qui suit le curseur, plus discret qu'une surface vitrée.
        if self._hover.value > 0.0:
            center = self._glass_pointer if self._glass_pointer_set else rect.center()
            glow = QRadialGradient(center, max(rect.width(), rect.height()) * 0.95)
            tint = _hover_tint()
            peak = int((38 if dark else 40) * self._hover.value)
            glow.setColorAt(0.0, QColor(tint.red(), tint.green(), tint.blue(), peak))
            glow.setColorAt(0.45, QColor(tint.red(), tint.green(), tint.blue(), max(1, peak // 4)))
            glow.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
            painter.fillPath(path, QBrush(glow))
        painter.restore()

        # 6. Un liseré extérieur et un trait intérieur, tous deux neutres.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(style["glass_stroke"]), 1.0))
        painter.drawPath(path)
        inner_line = rect.adjusted(1.2, 1.2, -1.2, -1.2)
        painter.setPen(QPen(QColor(255, 255, 255, 28 if dark else 48), 1.0))
        painter.drawPath(rounded_path(inner_line, max(1.0, radius - 1)))

        # 7. Anneau de focus clavier (le QSS ne peut plus le dessiner : fond transparent).
        if self.property("keyboardFocus") is True:
            painter.setPen(QPen(QColor(token("focus")), 2.0))
            painter.drawPath(rounded_path(rect.adjusted(1.0, 1.0, -1.0, -1.0),
                                          max(1.0, radius - 1)))


# ─────────────────────────────────────────────────────────────────────────────
# Dialoques en verre
# ─────────────────────────────────────────────────────────────────────────────
class GlassDialog(QDialog):
    """Boîte de dialogue dont la feuille laisse voir la fenêtre parente, floutée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("glass", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._sheet: QImage | None = None
        self._offset = QPoint(0, 0)
        # Nom du thème dont la feuille a été capturée. Préfixé pour ne pas entrer en
        # collision avec `_theme` chez les sous-classes (gestionnaire de thème).
        self._sheet_theme = ""
        ThemeManager.instance().changed.connect(self._on_theme)
        ThemeManager.instance().accessibility_changed.connect(self._on_accessibility)

    def _available_screen(self) -> tuple[int, int] | None:
        """Zone utile de l'écran de la boîte, ou None quand elle est inconnue."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        return (area.width(), area.height()) if area is not None else None

    def fit_to_content(self, scroll: QScrollArea, *, min_width: int = 480) -> None:
        """Taille par défaut : celle du contenu affiché, sans dépasser la zone utile de l'écran.

        La largeur suit le contenu (jamais moins de ``min_width``) et la hauteur est
        recalculée à cette largeur, car un texte plus étroit occupe plus de lignes. Le
        cadre — en-tête, onglets, boutons — se mesure comme la différence entre la boîte
        et sa zone défilante, sans dépendre de la mise en page d'une boîte particulière.
        """
        layout = self.layout()
        if layout is not None:
            layout.activate()
        content = scroll.widget()
        if content is None:
            return
        hint = self.sizeHint()
        scroll_hint = scroll.sizeHint()
        chrome_w = max(0, hint.width() - scroll_hint.width())
        chrome_h = max(0, hint.height() - scroll_hint.height())
        viewport_w = max(content.sizeHint().width(), int(min_width) - chrome_w)
        content_h = content.heightForWidth(viewport_w)
        if content_h <= 0:                       # mise en page sans hauteur-pour-largeur
            content_h = content.sizeHint().height()
        available = self._available_screen()
        self.setMinimumWidth(fit_to_screen(int(min_width), 0, available)[0])
        self.resize(*fit_to_screen(viewport_w + chrome_w, content_h + chrome_h, available))

    def _source(self) -> QWidget | None:
        parent = self.parentWidget()
        return parent.window() if parent is not None else None

    def _refresh_sheet(self) -> None:
        source = self._source()
        if source is None or not source.isVisible():
            self._sheet = None
            return
        self._sheet = capture_content(source)
        self._sheet_theme = _theme()
        self._offset = self.mapToGlobal(QPoint(0, 0)) - source.mapToGlobal(QPoint(0, 0))
        self.update()

    def _refresh_offset(self) -> None:
        """La fenêtre parente ne change pas quand la boîte se déplace : seul le décalage suit."""
        source = self._source()
        if source is None:
            return
        self._offset = self.mapToGlobal(QPoint(0, 0)) - source.mapToGlobal(QPoint(0, 0))
        self.update()

    def _on_theme(self, _name: str) -> None:
        self._sheet = None
        self._refresh_sheet()

    def _on_accessibility(self) -> None:
        self._sheet = None
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_sheet()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._refresh_offset()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_offset()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event                                   # fond de fenêtre uniforme : les cartes (surface) s'y détachent
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(token("bg")))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(token("border")), 1.0))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


# ─────────────────────────────────────────────────────────────────────────────
# Bouton à reflet
# ─────────────────────────────────────────────────────────────────────────────
class LiquidButton(QPushButton):
    """Bouton QSS ordinaire, plus un reflet de verre qui suit le curseur."""

    def __init__(self, *args: object, radius: float = 6, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._sheen_radius = float(radius)
        self._pointer = QPointF()
        self._pointer_set = False
        self._hover = _HoverAnimator(self, 200)
        self._icon_hover = HoverIcon(self, self.icon, self.setIcon)
        ThemeManager.instance().accessibility_changed.connect(self.update)
        self.setMouseTracking(True)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._hover.to(0.0)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._pointer = event.position()
        self._pointer_set = True
        if self._hover.value > 0.0:
            self.update()
        super().mouseMoveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        """Un bouton masqué (onglet, barre d'actions) ne doit pas garder son reflet."""
        super().hideEvent(event)
        self._hover.reset()
        self._pointer_set = False

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        # L'onglet actif est déjà marqué par la capsule : son reflet de survol resterait
        # figé à destination pendant que la capsule glisse, et dessinerait un second cadre.
        if (ThemeManager.instance().reduce_transparency() or self._hover.value <= 0.01
                or not self.isEnabled() or (self.isCheckable() and self.isChecked())):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded_path(rect, self._sheen_radius)
        painter.setClipPath(path)
        dark = _theme() == "dark"
        center = self._pointer if self._pointer_set else rect.center()
        glow = QRadialGradient(center, max(rect.width(), rect.height()) * 0.9)
        tint = _hover_tint()
        peak = int((22 if dark else 30) * self._hover.value)
        glow.setColorAt(0.0, QColor(tint.red(), tint.green(), tint.blue(), peak))
        glow.setColorAt(0.5, QColor(tint.red(), tint.green(), tint.blue(), max(1, peak // 3)))
        glow.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
        painter.fillRect(rect, QBrush(glow))
        if dark:                              # le trait de lumière n'a de sens que sur fond sombre
            painter.setPen(QPen(QColor(255, 255, 255, int(34 * self._hover.value)), 1.0))
            painter.drawLine(QPointF(rect.left() + self._sheen_radius, rect.top() + 0.7),
                             QPointF(rect.right() - self._sheen_radius, rect.top() + 0.7))
        painter.end()


# ─────────────────────────────────────────────────────────────────────────────
# Sélection glissante : un même langage pour les boutons et les onglets
# ─────────────────────────────────────────────────────────────────────────────
class GlassSegmentedBar(QWidget):
    """Groupe de boutons exclusifs avec une capsule de verre qui glisse au clic.

    Les boutons restent des QPushButton ordinaires : leur `QButtonGroup` et leurs
    signaux ne changent pas. Seul le fond de la sélection est peint ici, derrière
    les boutons, et il n'est déplacé que lorsque ``set_current`` est appelé — jamais
    au survol. La capsule s'étire légèrement pendant le trajet pour évoquer un
    liquide, puis se stabilise sans à-coup.
    """

    DURATION = 220        # bascule de page : action fréquente, donc vive (budget < 300 ms)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        self._buttons: list[QPushButton] = []
        self._index = 0
        self._indicator = QRectF()
        self._from = QRectF()
        self._to = QRectF()
        self._progress = 1.0
        self._phase = 0.32
        self._animating = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.DURATION)
        self._animation.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutBack))
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._on_frame)
        self._animation.finished.connect(self._on_finished)
        ThemeManager.instance().changed.connect(self._on_theme)
        ThemeManager.instance().accessibility_changed.connect(self._on_theme)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def add_button(self, button: QPushButton) -> None:
        """Ajoute un bouton à la rangée (l'exclusivité reste gérée par l'appelant)."""
        self._buttons.append(button)
        self._layout.addWidget(button)

    def current_index(self) -> int:
        return self._index

    def _button_rect(self, index: int) -> QRectF:
        if not 0 <= index < len(self._buttons):
            return QRectF()
        button = self._buttons[index]
        if button.width() <= 0 or button.height() <= 0:
            return QRectF()
        return QRectF(button.geometry())

    def set_current(self, index: int, *, animate: bool = True) -> None:
        """Place la capsule sur ``index`` ; l'animation ne part qu'au changement."""
        if not 0 <= index < len(self._buttons):
            return
        target = self._button_rect(index)
        self._index = index
        if target.isNull():
            self._indicator = QRectF()
            return
        if (not animate or ThemeManager.instance().reduce_motion() or not self.isVisible()
                or self._indicator.isNull() or target == self._indicator):
            self._animation.stop()
            self._animating = False
            self._progress = 1.0
            self._phase = 0.32
            self._indicator = QRectF(target)
            self._from = QRectF(target)
            self._to = QRectF(target)
            self.update()
            return
        self._from = QRectF(self._indicator)
        self._to = QRectF(target)
        self._progress = 0.0
        self._phase = 0.18
        self._animating = True
        self._animation.stop()
        self._animation.start()
        self.update()

    def _on_frame(self, value: object) -> None:
        # OutBack dépasse 1.0 : le pouce ressorti dépasse sa cible de quelques pixels,
        # puis revient se poser. On garde ce dépassement pour la géométrie.
        progress = float(value)
        self._progress = max(0.0, min(1.0, progress))
        self._indicator = QRectF(
            self._from.x() + (self._to.x() - self._from.x()) * progress,
            self._from.y() + (self._to.y() - self._from.y()) * progress,
            self._from.width() + (self._to.width() - self._from.width()) * progress,
            self._from.height() + (self._to.height() - self._from.height()) * progress,
        )
        self._phase = 0.18 + 0.64 * self._progress
        self.update()

    def _on_finished(self) -> None:
        self._animating = False
        self._progress = 1.0
        self._phase = 0.32
        self._indicator = QRectF(self._to)
        self.update()

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def _sync_indicator(self) -> None:
        if self._animating:
            return
        target = self._button_rect(self._index)
        if not target.isNull() and target != self._indicator:
            self._indicator = QRectF(target)
            self._from = QRectF(target)
            self._to = QRectF(target)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if not self._buttons:
            return
        self._sync_indicator()
        if self._indicator.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self._indicator)
        if self._animating:
            stretch = math.sin(math.pi * self._progress) * min(9.0, rect.width() * 0.10)
            rect = rect.adjusted(-stretch / 2.0, 0.0, stretch / 2.0, 0.0)
        # Onglets segmentés : le pouce ressorti passe derrière le libellé actif.
        dark = ThemeManager.instance().theme() == "dark"
        painter.setPen(QPen(QColor(token("border")), 1.0))
        painter.setBrush(QColor(token("secondary") if dark else token("surface")))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0)
        painter.end()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_indicator()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._animating:
            # Les icônes actives peuvent élargir un segment de quelques pixels :
            # on reprend le trajet depuis la position visible, sans le couper.
            target = self._button_rect(self._index)
            if not target.isNull():
                self._from = QRectF(self._indicator)
                self._to = QRectF(target)
                self._animation.stop()
                self._animation.start()
            self.update()
            return
        self._sync_indicator()
        self.update()


class GlassTabBar(QTabBar):
    """Barre d'onglets avec la même capsule de sélection animée.

    La capsule est peinte *avant* les onglets : le texte et les icônes restent
    dessinés par Qt par-dessus. Le fond de l'onglet sélectionné est rendu
    transparent dans la feuille de style d'Azimut pour laisser voir le verre.
    """

    DURATION = 240       # changement d'onglet : fréquent, donc plus court qu'une bascule de page
    TOP_GAP = 8          # doit rester aligné sur ``margin-top: $p8`` des onglets Azimut
    PAGE_SETTLE_MS = 40  # laisse la page affichée terminer sa mise en page avant le glissement

    def __init__(self, parent: QWidget | None = None, *, animate_icons: bool = True) -> None:
        """``animate_icons=False`` fige les icônes : onglets d'outils et de Conseils ainsi
        que leurs pages n'ont pas à animer le pictogramme de l'outil au survol."""
        super().__init__(parent)
        self._indicator = QRectF()
        self._from = QRectF()
        self._to = QRectF()
        self._icon_hover = HoverTabIcons(self) if animate_icons else None
        self._progress = 1.0
        self._phase = 0.32
        self._animating = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.DURATION)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._on_frame)
        self._animation.finished.connect(self._on_finished)
        # Le changement de page peut être coûteux (mise en page d'un module lourd) :
        # laisser Qt le terminer avant d'envoyer la première image de la capsule.
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.setInterval(self.PAGE_SETTLE_MS)
        self._start_timer.timeout.connect(self._start_animation)
        self.currentChanged.connect(self._on_current_changed)
        self.tabMoved.connect(self._on_tab_moved)
        ThemeManager.instance().changed.connect(self._on_theme)
        ThemeManager.instance().accessibility_changed.connect(self._on_theme)

    def _capsule_rect(self, cell: QRectF) -> QRectF:
        """Capsule d'un onglet pour une cellule donnée (cas d'une position animée)."""
        cell = QRectF(cell)
        if cell.width() <= 6.0 or cell.height() <= 6.0:
            return QRectF()
        # Le liseré de la capsule commence sous la marge haute de l'onglet :
        # la bande transparente au-dessus crée l'espace avec la barre de pages.
        return cell.adjusted(2.0, scaled(self.TOP_GAP) + 2.0, -2.0, -2.5)

    def _tab_rect(self, index: int) -> QRectF:
        if not 0 <= index < self.count():
            return QRectF()
        return self._capsule_rect(QRectF(self.tabRect(index)))

    def _on_tab_moved(self, _from: int, _to: int) -> None:
        """Recale la capsule quand l'utilisateur réordonne les onglets."""
        target = self._tab_rect(self.currentIndex())
        if not target.isNull():
            self._snap(target)

    def _snap(self, rect: QRectF) -> None:
        self._start_timer.stop()
        self._animation.stop()
        self._animating = False
        self._progress = 1.0
        self._phase = 0.32
        self._indicator = QRectF(rect)
        self._from = QRectF(rect)
        self._to = QRectF(rect)
        self.update()

    def _on_current_changed(self, index: int) -> None:
        target = self._tab_rect(index)
        if target.isNull():
            return
        if (ThemeManager.instance().reduce_motion() or not self.isVisible()
                or self._indicator.isNull() or target == self._indicator):
            self._snap(target)
            return
        self._from = QRectF(self._indicator)
        self._to = QRectF(target)
        self._progress = 0.0
        self._phase = 0.18
        self._animating = True
        self._animation.stop()
        self._start_timer.start()
        self.update()

    def _start_animation(self) -> None:
        """Démarre le coulissement après le changement de page, sur la cible à jour."""
        if not self._animating or not self.isVisible():
            return
        if ThemeManager.instance().reduce_motion():
            self._snap(self._tab_rect(self.currentIndex()))
            return
        target = self._tab_rect(self.currentIndex())
        if target.isNull():
            self._snap(QRectF())
            return
        self._to = QRectF(target)
        self._animation.start()

    def _on_frame(self, value: object) -> None:
        # OutBack dépasse 1.0 : le pouce ressorti dépasse sa cible de quelques pixels,
        # puis revient se poser. On garde ce dépassement pour la géométrie.
        progress = float(value)
        self._progress = max(0.0, min(1.0, progress))
        self._indicator = QRectF(
            self._from.x() + (self._to.x() - self._from.x()) * progress,
            self._from.y() + (self._to.y() - self._from.y()) * progress,
            self._from.width() + (self._to.width() - self._from.width()) * progress,
            self._from.height() + (self._to.height() - self._from.height()) * progress,
        )
        self._phase = 0.18 + 0.64 * self._progress
        self.update()

    def _on_finished(self) -> None:
        self._animating = False
        self._progress = 1.0
        self._phase = 0.32
        self._indicator = QRectF(self._to)
        self.update()

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def _sync_indicator(self) -> None:
        if self._animating:
            return
        target = self._tab_rect(self.currentIndex())
        if not target.isNull() and target != self._indicator:
            self._indicator = QRectF(target)
            self._from = QRectF(target)
            self._to = QRectF(target)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._sync_indicator()
        if not self._indicator.isNull():
            energy = 1.0 - abs(2.0 * self._progress - 1.0) if self._animating else 0.0
            rect = QRectF(self._indicator)
            if self._animating:
                stretch = math.sin(math.pi * self._progress) * min(10.0, rect.width() * 0.10)
                rect = rect.adjusted(-stretch / 2.0, 0.8 * energy,
                                     stretch / 2.0, -0.8 * energy)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setClipRect(self.rect())
            paint_glass_indicator(painter, rect, 9.0, energy=energy, phase=self._phase)
            painter.end()
        super().paintEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        target = self._tab_rect(self.currentIndex())
        if not target.isNull():
            self._snap(target)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        target = self._tab_rect(self.currentIndex())
        if target.isNull():
            return
        if self._animating:
            self._from = QRectF(self._indicator)
            self._to = QRectF(target)
            if self._start_timer.isActive():
                # La cible sera relue au démarrage effectif de l'animation.
                self.update()
                return
            self._animation.stop()
            self._animation.start()
            self.update()
            return
        self._snap(target)
