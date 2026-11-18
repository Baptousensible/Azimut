"""Animations de survol des icônes animées (lucide-animated).

Les SVG sont statiques une fois rendus par ``QSvgRenderer``. Ce module anime donc les
pixmaps existants : rotation, translation, échelle et opacité propres à chaque icône
(voir ``icons.hovered_pixmap`` et :mod:`azimut.ui.icon_animation`). Il respecte
« Réduire les animations » et n'anime que les QIcon créés par :mod:`azimut.ui.icons`.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QSize, QVariantAnimation
from PySide6.QtGui import QCursor, QIcon, QPixmap
from PySide6.QtWidgets import QTabBar, QWidget

from azimut.ui import icons
from azimut.ui.theme import ThemeManager


class _HoverAnimation(QObject):
    """Horloge commune : progression 0 → 1 à l'entrée, retour à 0 à la sortie."""

    DURATION_MS = 260

    def __init__(self, watch: QWidget) -> None:
        super().__init__(watch)
        self._watch = watch
        self._progress = 0.0
        self._active = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_frame)
        self._anim.finished.connect(self._on_finished)
        watch.installEventFilter(self)
        manager = ThemeManager.instance()
        manager.changed.connect(self._on_environment_changed)
        manager.accessibility_changed.connect(self._on_environment_changed)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is not self._watch:
            return False
        if event.type() == QEvent.Type.Enter:
            self._to(1.0)
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._to(0.0)
        return False

    def _to(self, target: float) -> None:
        if target > 0.0:
            if not icons.motion_enabled():
                self._restore()
                return
            if not self._active and not self._capture():
                return
            if self._active and self._progress >= 0.999:
                return
            start = self._progress
        else:
            if not self._active:
                return
            start = self._progress
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(float(target))
        self._anim.start()

    def _on_frame(self, value: object) -> None:
        try:
            self._progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        self._apply(self._progress)

    def _on_finished(self) -> None:
        if self._progress <= 0.001:
            self._restore()

    def _on_environment_changed(self, *_args: object) -> None:
        """Le thème ou l'accessibilité change : on repose immédiatement l'icône."""
        self._anim.stop()
        self._restore()

    def _restore(self) -> None:
        if self._active:
            self._apply(0.0)
        self._active = False
        self._progress = 0.0

    def _capture(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def _apply(self, progress: float) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class HoverIcon(_HoverAnimation):
    """Fait respirer l'icône d'un bouton ou d'un autre widget à QIcon."""

    def __init__(self, widget: QWidget, get_icon: Callable[[], QIcon],
                 set_icon: Callable[[QIcon], None]) -> None:
        self._get_icon = get_icon
        self._set_icon = set_icon
        self._name = ""
        self._base_icon = QIcon()
        self._base_pixmap = QPixmap()
        self._disabled_pixmap = QPixmap()
        super().__init__(widget)

    def _capture(self) -> bool:
        icon = self._get_icon()
        if icon.isNull():
            return False
        name = icons.icon_name(icon)
        if not name:
            return False
        # Le bouton peut demander une taille d'icône différente de celle du QIcon
        # (souvent 16 px alors que le pictogramme est rendu en 18 px) : garder la
        # taille native évite de le rétrécir pendant l'animation.
        sizes = [size for size in icon.availableSizes() if size.isValid()]
        size = sizes[0] if sizes else self._watch.iconSize()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            size = QSize(18, 18)
        base = icon.pixmap(size, QIcon.Mode.Normal)
        if base.isNull():
            return False
        self._name = name
        self._base_icon = QIcon(icon)
        self._base_pixmap = base
        self._disabled_pixmap = icon.pixmap(size, QIcon.Mode.Disabled)
        self._anim.setDuration(icons.hover_duration(name, self.DURATION_MS))
        self._active = True
        return True

    def _apply(self, progress: float) -> None:
        if not self._active:
            return
        if progress <= 0.001:
            self._set_icon(self._base_icon)
            return
        pixmap = icons.hovered_pixmap(self._base_pixmap, self._name, progress)
        animated = QIcon()
        animated.addPixmap(pixmap, QIcon.Mode.Normal)
        if not self._disabled_pixmap.isNull():
            animated.addPixmap(self._disabled_pixmap, QIcon.Mode.Disabled)
        self._set_icon(animated)


class HoverPixmap(_HoverAnimation):
    """Anime un pixmap affiché par un QLabel.

    ``watch`` peut être le libellé ou un parent (zone de dépôt) qui reçoit le
    survol ; ``name_provider`` permet de suivre un nom d'icône qui change.
    """

    def __init__(self, watch: QWidget, get_pixmap: Callable[[], QPixmap],
                 set_pixmap: Callable[[QPixmap], None],
                 name_provider: Callable[[], str]) -> None:
        self._get_pixmap = get_pixmap
        self._set_pixmap = set_pixmap
        self._name_provider = name_provider
        self._name = ""
        self._base_pixmap = QPixmap()
        super().__init__(watch)

    def _capture(self) -> bool:
        pixmap = self._get_pixmap()
        if pixmap.isNull():
            return False
        self._name = str(self._name_provider() or "")
        self._base_pixmap = QPixmap(pixmap)
        self._anim.setDuration(icons.hover_duration(self._name, self.DURATION_MS))
        self._active = True
        return True

    def _apply(self, progress: float) -> None:
        if not self._active:
            return
        if progress <= 0.001:
            self._set_pixmap(self._base_pixmap)
            return
        self._set_pixmap(icons.hovered_pixmap(self._base_pixmap, self._name, progress))


class HoverTabIcons(QObject):
    """Anime l'icône de l'onglet survolé d'une QTabBar."""

    DURATION_MS = 260

    def __init__(self, bar: QTabBar) -> None:
        super().__init__(bar)
        self._bar = bar
        self._index = -1
        self._progress = 0.0
        self._base_icon = QIcon()
        self._base_pixmap = QPixmap()
        self._disabled_pixmap = QPixmap()
        self._name = ""
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_frame)
        self._anim.finished.connect(self._on_finished)
        bar.setMouseTracking(True)
        bar.installEventFilter(self)
        manager = ThemeManager.instance()
        manager.changed.connect(self._restore)
        manager.accessibility_changed.connect(self._restore)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is not self._bar:
            return False
        if event.type() == QEvent.Type.MouseMove:
            self._hover_at(self._bar.tabAt(event.position().toPoint()))
        elif event.type() == QEvent.Type.Enter:
            self._hover_at(self._bar.tabAt(self._bar.mapFromGlobal(QCursor.pos())))
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._leave()
        return False

    def _hover_at(self, index: int) -> None:
        if index == self._index:
            return
        self._restore()
        if index < 0 or not icons.motion_enabled():
            return
        icon = self._bar.tabIcon(index)
        if icon.isNull():
            return
        name = icons.icon_name(icon)
        if not name:
            return
        sizes = [size for size in icon.availableSizes() if size.isValid()]
        size = sizes[0] if sizes else self._bar.iconSize()
        if not size.isValid() or size.width() <= 0:
            size = QSize(18, 18)
        base = icon.pixmap(size, QIcon.Mode.Normal)
        if base.isNull():
            return
        self._index = index
        self._name = name
        self._base_icon = QIcon(icon)
        self._base_pixmap = base
        self._disabled_pixmap = icon.pixmap(size, QIcon.Mode.Disabled)
        self._progress = 0.0
        self._anim.stop()
        self._anim.setDuration(icons.hover_duration(name, self.DURATION_MS))
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _leave(self) -> None:
        if self._index < 0:
            return
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_frame(self, value: object) -> None:
        if self._index < 0:
            return
        try:
            self._progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        if not 0 <= self._index < self._bar.count():
            self._restore()
            return
        if self._progress <= 0.001:
            self._bar.setTabIcon(self._index, self._base_icon)
            return
        animated = QIcon()
        animated.addPixmap(
            icons.hovered_pixmap(self._base_pixmap, self._name, self._progress),
            QIcon.Mode.Normal)
        if not self._disabled_pixmap.isNull():
            animated.addPixmap(self._disabled_pixmap, QIcon.Mode.Disabled)
        self._bar.setTabIcon(self._index, animated)

    def _on_finished(self) -> None:
        if self._progress <= 0.001:
            self._restore()

    def _restore(self, *_args: object) -> None:
        if self._index >= 0 and self._index < self._bar.count() and not self._base_icon.isNull():
            self._bar.setTabIcon(self._index, self._base_icon)
        self._anim.stop()
        self._index = -1
        self._progress = 0.0
        self._base_icon = QIcon()
        self._base_pixmap = QPixmap()
        self._disabled_pixmap = QPixmap()
