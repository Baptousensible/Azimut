"""Pile de pages à défilement horizontal, sur un ressort amorti.

Les pages réelles restent côte à côte, comme les panneaux d'un carrousel : la page
quittée sort, la page choisie entre, et la position commune suit le ressort
``ω₀ = 13, ζ = 0.82`` de la spécification. Aucune capture d'écran n'est nécessaire,
donc les champs, les positions de défilement et les animations internes des pages
conservent leur état pendant la transition.

L'API reprend le sous-ensemble de ``QStackedWidget`` utilisé par Azimut
(``addWidget``, ``count``, ``currentIndex``, ``currentWidget``, ``setCurrentIndex``),
ce qui permet de remplacer la pile sans modifier les pages.
"""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QRect, QSize, QTimer, Signal
from PySide6.QtWidgets import QSizePolicy, QWidget

from azimut.ui.motion import spring
from azimut.ui.theme import ThemeManager


class SlidingPageStack(QWidget):
    """Pages empilées logiquement, mais qui glissent horizontalement à la sélection."""

    currentChanged = Signal(int)

    # Le glissement déplace deux pages entières de verre : à 60 Hz il repeint plus de
    # mille widgets par transition et peut faire sauter le curseur. 30 Hz suffit.
    FRAME_MS = 33
    SETTLE_S = 0.8          # le ressort est alors à moins d'un pixel de sa cible
    SETTLE_EPS = 0.002
    OMEGA0 = 13.0
    ZETA = 0.82

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pages: list[QWidget] = []
        self._index = -1
        self._from = 0.0
        self._to = 0.0
        self._pg = 0.0
        self._animating = False
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)

    # ── API compatible avec QStackedWidget ─────────────────────────────────
    def addWidget(self, page: QWidget) -> int:  # noqa: N802 - API Qt
        page.setParent(self)
        page.hide()
        self._pages.append(page)
        index = len(self._pages) - 1
        if self._index < 0:
            self._index = 0
            self._pg = 0.0
            page.show()
            page.raise_()
            self.currentChanged.emit(0)
        self._layout_pages()
        self.updateGeometry()
        return index

    def replaceWidget(self, index: int, page: QWidget) -> None:  # noqa: N802 - API Qt
        """Remplace la page d'un index sans changer sa place (construction différée)."""
        if not 0 <= index < len(self._pages) or page is self._pages[index]:
            return
        previous = self._pages[index]
        page.setParent(self)
        page.hide()
        self._pages[index] = page
        if previous is not None:
            previous.hide()
            previous.setParent(None)
            previous.deleteLater()
        self._layout_pages()
        self.updateGeometry()
        if index == self._index and not self._animating:
            page.setGeometry(self._rect_for(index))
            page.show()
            page.raise_()
        self.update()

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> QWidget | None:
        return self._pages[index] if 0 <= index < len(self._pages) else None

    def indexOf(self, page: QWidget) -> int:  # noqa: N802 - API Qt
        try:
            return self._pages.index(page)
        except ValueError:
            return -1

    def currentIndex(self) -> int:  # noqa: N802 - API Qt
        return self._index

    def currentWidget(self) -> QWidget | None:  # noqa: N802 - API Qt
        return self.widget(self._index)

    def setCurrentIndex(self, index: int, *, animate: bool = True) -> None:  # noqa: N802 - API Qt
        if not 0 <= index < len(self._pages):
            return
        previous = self._index
        if index == previous:
            if not self._animating:
                self._finish_at(index)
            return
        self._index = index
        self.currentChanged.emit(index)
        target = self._pages[index]
        if (not animate or previous < 0 or not self.isVisible()
                or ThemeManager.instance().reduce_motion()
                or self.width() <= 0 or self.height() <= 0):
            self._finish_at(index)
            return

        # Une nouvelle sélection pendant un trajet repart de la position visible :
        # pas de saut en arrière vers l'onglet logiquement courant.
        self._from = self._pg if self._animating else float(previous)
        self._to = float(index)
        self._pg = self._from
        self._animating = True
        for page in self._pages:
            page.show()
        target.raise_()
        self._clock.restart()
        self._timer.start()
        self._layout_pages()

    def setCurrentWidget(self, page: QWidget, *, animate: bool = True) -> None:  # noqa: N802 - API Qt
        index = self.indexOf(page)
        if index >= 0:
            self.setCurrentIndex(index, animate=animate)

    # ── Ressort et géométrie ───────────────────────────────────────────────
    def is_animating(self) -> bool:
        return self._animating

    def position(self) -> float:
        """Position courante en unités de page (0 = première page)."""
        return self._pg

    def _rect_for(self, index: int) -> QRect:
        return QRect(round((index - self._pg) * self.width()), 0, self.width(), self.height())

    def _layout_pages(self) -> None:
        for index, page in enumerate(self._pages):
            page.setGeometry(self._rect_for(index))

    def _tick(self) -> None:
        if not self._animating:
            self._timer.stop()
            return
        elapsed = self._clock.elapsed() / 1000.0
        progress = spring(self.OMEGA0, self.ZETA, elapsed)
        self._pg = self._from + (self._to - self._from) * progress
        self._layout_pages()
        if elapsed >= self.SETTLE_S and abs(progress - 1.0) <= self.SETTLE_EPS:
            self._finish_at(self._index)

    def _finish_at(self, index: int) -> None:
        """Pose la pile sur ``index`` et masque toutes les autres pages."""
        self._timer.stop()
        self._animating = False
        self._index = index
        self._from = self._to = self._pg = float(index)
        self._layout_pages()
        for page_index, page in enumerate(self._pages):
            page.setVisible(page_index == index)
        page = self.widget(index)
        if page is not None:
            page.raise_()
        self.update()

    # ── Événements ─────────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._layout_pages()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        if self._index >= 0 and not self._animating:
            self._finish_at(self._index)

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._animating:
            self._finish_at(self._index)
        super().hideEvent(event)

    # ── Dimensions ─────────────────────────────────────────────────────────
    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = QSize(0, 0)
        for page in self._pages:
            hint = hint.expandedTo(page.sizeHint())
        return hint if not hint.isEmpty() else super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = QSize(0, 0)
        for page in self._pages:
            hint = hint.expandedTo(page.minimumSizeHint())
        return hint
