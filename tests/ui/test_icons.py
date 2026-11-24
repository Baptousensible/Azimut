"""Icônes SVG (Papicons) : présence, recoloration, repli et intégration aux composants."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from azimut.launcher.registry import discover_with_errors
from azimut.core.prefs import Preferences
from azimut.ui import icons
from azimut.ui.glass import GlassFrame, GlassTabBar, LiquidButton
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import DropZone, IconLabel, StatusChip


@pytest.fixture(autouse=True)
def manager(qapp, tmp_path, monkeypatch):
    tm = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", tm)
    icons.clear_cache()
    return tm


@pytest.fixture(autouse=True)
def _delete_top_level_widgets(qapp):
    """Détruit les widgets orphelins avant le test suivant.

    Les IconLabel, StatusChip et DropZone de ces tests n'ont pas de parent. Sans ce
    nettoyage, ils peuvent être détruits par le ramasse-miettes pendant qu'un test de
    thème remplace la feuille de style globale (QApplication.setStyleSheet), ce qui
    a déjà provoqué un segfault dans la suite combinée.
    """
    yield
    for widget in list(QApplication.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _dominant_rgb(pm) -> tuple[int, int, int]:
    image: QImage = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    for x in range(image.width()):
        for y in range(image.height()):
            c = QColor.fromRgba(image.pixel(x, y))
            if c.alpha() == 255:
                return c.red(), c.green(), c.blue()
    raise AssertionError("aucun pixel opaque")


def _opaque_pixels(pm) -> int:
    image: QImage = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return sum(1 for x in range(image.width()) for y in range(image.height())
               if QColor.fromRgba(image.pixel(x, y)).alpha())


def _opaque_extent(pm) -> int:
    """Plus grande dimension du dessin opaque, en pixels physiques."""
    image: QImage = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if QColor.fromRgba(image.pixel(x, y)).alpha():
                xs.append(x)
                ys.append(y)
    return max(max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if xs else 0


def test_scaled_pixmap_keeps_its_canvas_and_shrinks_the_drawing():
    normal = icons.pixmap("check", 16, "#ff0000")
    small = icons.pixmap("check", 16, "#ff0000", scale=0.7)
    assert normal.size() == small.size()
    assert 0 < _opaque_pixels(small) < _opaque_pixels(normal)
    assert not icons.icon("check", "#ff0000", 16, scale=0.7).isNull()


def test_small_icons_are_supersampled_without_changing_their_logical_size():
    pm = icons.pixmap("camera", 16, "#ffffff")
    assert pm.devicePixelRatio() >= 2.0
    assert round(pm.width() / pm.devicePixelRatio()) == 16


def test_available_and_unknown_names():
    assert icons.available("check") and icons.available("alert-triangle")
    assert icons.available("swap-horizontal") and not icons.pixmap("swap-horizontal").isNull()
    assert not icons.available("") and not icons.available("🧪")
    assert not icons.available("../secret") and not icons.available("n'existe-pas")


def test_pixmap_is_recolored():
    assert _dominant_rgb(icons.pixmap("check", 32, "#ff0000")) == (255, 0, 0)
    assert _dominant_rgb(icons.pixmap("crown", 32, "#00ff00")) == (0, 255, 0)    # fill="#000" aussi


def test_pixmap_accepts_theme_token(manager):
    from azimut.ui.theme import palette
    expected = QColor(palette("dark")["ok_txt"])
    assert _dominant_rgb(icons.pixmap("check", 32, "ok_txt")) == (expected.red(), expected.green(), expected.blue())


def test_disabled_icon_can_keep_an_explicit_color(manager):
    """Une couleur explicite peut rester lisible quand le bouton est désactivé."""
    from azimut.ui.theme import palette
    expected = QColor(palette("dark")["accent"])
    pm = icons.icon("check", "accent", 32,
                    disabled_color="accent").pixmap(32, QIcon.Mode.Disabled)
    image: QImage = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    colors = {QColor.fromRgba(image.pixel(x, y)).rgba()
              for x in range(image.width()) for y in range(image.height())
              if QColor.fromRgba(image.pixel(x, y)).alpha() == 255}
    assert expected.rgba() in colors


def test_unknown_icon_gives_empty_pixmap_and_icon():
    assert icons.pixmap("nope").isNull() and icons.icon("nope").isNull()


def test_split_glyph():
    assert icons.split_glyph("✔ ffmpeg") == ("check", "ffmpeg")
    assert icons.split_glyph("✖ introuvable") == ("cross", "introuvable")
    assert icons.split_glyph("ffmpeg") == ("", "ffmpeg")


def test_tile_icon_not_empty():
    assert not icons.tile_icon("globe").isNull()
    assert icons.tile_icon("nope").isNull()


def test_application_and_brand_assets():
    assert not icons.app_icon().isNull()
    assert not icons.brand_pixmap("azimut_wordmark", 64).isNull()
    assert icons.brand_pixmap("absent", 64).isNull()


def test_brand_pixmap_keeps_its_logical_size_on_a_retina_screen(monkeypatch):
    """Sur un écran 2×, la hauteur demandée reste une hauteur logique."""
    monkeypatch.setattr(icons, "_device_pixel_ratio", lambda: 2.0)
    icons.clear_cache()
    pixmap = icons.brand_pixmap("azimut_wordmark", 64)
    assert pixmap.devicePixelRatio() == pytest.approx(2.0)
    assert round(pixmap.width() / pixmap.devicePixelRatio()) == 64
    assert round(pixmap.height() / pixmap.devicePixelRatio()) == 64


def test_every_module_uses_an_existing_icon():
    specs, errors = discover_with_errors()
    assert specs and not errors
    for spec in specs:
        assert icons.available(spec.icon), f"{spec.id} : icône « {spec.icon} » introuvable dans resources/icons"


def test_available_and_pixmap_follow_the_icon_theme(manager):
    """Les deux thèmes ont « check », mais available() et pixmap() lisent des sources distinctes."""
    manager.set_icon_theme("papicons")
    papicons_path = icons._source_path("check")
    papicons_pixmap = icons.pixmap("check", 32, "#ffffff")

    manager.set_icon_theme("animated")
    animated_path = icons._source_path("check")
    animated_pixmap = icons.pixmap("check", 32, "#ffffff")

    assert papicons_path is not None and papicons_path.parent.name == "icons"
    assert animated_path is not None and animated_path.parent.name == "icons_animated"
    assert papicons_path != animated_path
    assert icons.available("check")
    assert not papicons_pixmap.isNull() and not animated_pixmap.isNull()
    assert (bytes(papicons_pixmap.toImage().constBits())
            != bytes(animated_pixmap.toImage().constBits()))


def test_animated_theme_falls_back_to_papicons_for_an_icon_it_lacks(manager):
    """Une icône absente du dossier animé garde son rendu Papicons."""
    papicons_source = Path(icons.__file__).parent.parent / "resources" / "icons" / "camera.svg"
    animated_source = Path(icons.__file__).parent.parent / "resources" / "icons_animated" / "camera.svg"
    assert papicons_source.is_file()
    assert not animated_source.exists()

    manager.set_icon_theme("papicons")
    papicons_pixmap = icons.pixmap("camera", 32, "#ffffff")
    manager.set_icon_theme("animated")
    fallback_path = icons._source_path("camera")

    assert fallback_path is not None
    assert fallback_path.resolve() == papicons_source.resolve()
    assert fallback_path.parent.name == "icons"
    assert icons.available("camera")
    assert not icons.pixmap("camera", 32, "#ffffff").isNull()
    assert (bytes(papicons_pixmap.toImage().constBits())
            == bytes(icons.pixmap("camera", 32, "#ffffff").toImage().constBits()))


def test_animated_theme_renders_colored_icons(manager):
    """Le thème animé est un jeu de SVG séparé, recoloré comme Papicons."""
    manager.set_icon_theme("animated")
    assert icons.available("check") and icons.available("arrow-up")
    assert _dominant_rgb(icons.pixmap("check", 32, "#ff0000")) == (255, 0, 0)
    assert _opaque_pixels(icons.pixmap("check", 32, "#ffffff")) > 0
    manager.set_icon_theme("papicons")
    assert icons.available("check") and not icons.pixmap("check", 32, "#ffffff").isNull()


def test_animated_drawing_is_scaled_to_match_papicons(manager):
    """Chaque tracé animé reprend la surface de son homologue Papicons."""
    manager.set_icon_theme("papicons")
    papicons_extent = _opaque_extent(icons.pixmap("globe", 32, "#ffffff"))

    manager.set_icon_theme("animated")
    animated_extent = _opaque_extent(icons.pixmap("globe", 32, "#ffffff"))

    assert animated_extent <= papicons_extent + 1
    assert icons._draw_scale("globe", 1.0) == pytest.approx(0.90)
    # Calibrage propre à chaque icône : les aplats Papicons n'ont pas la même surface.
    assert icons._draw_scale("info", 1.0) == pytest.approx(0.80)
    assert icons._draw_scale("pen-alt", 1.0) == pytest.approx(0.72)
    assert icons._draw_scale("chevron-right", 1.0) == pytest.approx(1.16)
    assert icons._draw_scale("camera", 1.0) == 1.0


def test_all_animated_icons_match_papicons_footprint(manager):
    """Les 33 icônes animées ont, à taille égale, l'aire de leur homologue Papicons."""
    import math

    from azimut.core.paths import resource_path

    names = sorted(path.stem for path in resource_path("icons_animated").glob("*.svg"))
    assert len(names) == 33
    assert set(names) == set(icons._PAPICON_MATCH)

    for name in names:
        assert (resource_path("icons") / f"{name}.svg").is_file(), name
        manager.set_icon_theme("papicons")
        icons.clear_cache()
        px0, py0, px1, py1 = _opaque_bounds(icons.pixmap(name, 32, "#ffffff"))
        manager.set_icon_theme("animated")
        icons.clear_cache()
        ax0, ay0, ax1, ay1 = _opaque_bounds(icons.pixmap(name, 32, "#ffffff"))

        papicons_area = math.sqrt((px1 - px0 + 1) * (py1 - py0 + 1))
        animated_area = math.sqrt((ax1 - ax0 + 1) * (ay1 - ay0 + 1))
        assert abs(animated_area - papicons_area) <= 2.0, name


def test_animated_motion_follows_reduce_motion(manager):
    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    assert icons.motion_enabled()
    manager.set_reduce_motion(True)
    assert not icons.motion_enabled()
    manager.set_icon_theme("papicons")
    manager.set_reduce_motion(False)
    assert not icons.motion_enabled()


def test_animated_gears_rotation_transforms_the_icon(manager):
    """L'engrenage tourne au survol, sans changer la case du pixmap."""
    manager.set_icon_theme("animated")
    base = icons.pixmap("gears", 32, "#ffffff")
    hovered = icons.hovered_pixmap(base, "gears", 1.0)

    assert hovered.size() == base.size()
    assert bytes(hovered.toImage().constBits()) != bytes(base.toImage().constBits())


def test_every_animated_icon_declares_its_movement(manager):
    """Les 33 icônes du thème ont un mouvement, propre ou générique, et une durée de survol."""
    from azimut.ui.icon_animation import ANIMATIONS

    manager.set_icon_theme("animated")
    names = sorted(path.stem for path in Path(icons.__file__).parent.parent
                   .joinpath("resources", "icons_animated").glob("*.svg"))
    assert len(names) == 33
    assert set(names) == set(ANIMATIONS)
    assert icons.hover_duration("search") == 900
    assert icons.hover_duration("camera") == 260


def test_animated_grid_moves_each_tile(manager):
    """La grille anime chaque case séparément (quatre couches distinctes)."""
    manager.set_icon_theme("animated")
    base = icons.pixmap("grid", 32, "#ffffff")
    assert len(icons._layers_for("grid", base)) == 4

    moved = icons.hovered_pixmap(base, "grid", 0.5)
    assert moved.size() == base.size()
    assert bytes(moved.toImage().constBits()) != bytes(base.toImage().constBits())


def test_animated_icon_keeps_its_layers_through_a_qicon(manager):
    """QIcon recopie son pixmap : les couches doivent être retrouvées malgré la nouvelle clé."""
    manager.set_icon_theme("animated")
    icon = icons.icon("grid", "#ffffff", 18)
    pixmap = icon.pixmap(icon.availableSizes()[0])

    assert pixmap.cacheKey() != icons.pixmap("grid", 18, "#ffffff").cacheKey()
    assert len(icons._layers_for("grid", pixmap)) == 4
    assert bytes(icons.hovered_pixmap(pixmap, "grid", 0.5).toImage().constBits()) \
        != bytes(pixmap.toImage().constBits())


def _opaque_bounds(pixmap) -> tuple[int, int, int, int]:
    image = pixmap.toImage()
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(image.width()):
            if (image.pixel(x, y) >> 24) & 0xFF:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def test_shrunk_icons_keep_visible_motion(manager):
    """sparkles, pen-alt et gallery restent nettement animées malgré leur facteur réduit."""
    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    icons.clear_cache()
    for name, minimum in (("sparkles", 300), ("pen-alt", 200), ("gallery", 300)):
        base = icons.pixmap(name, 20, "#ffffff")
        still = base.toImage()
        best = 0
        for step in range(1, 10):
            frame = icons.hovered_pixmap(base, name, step / 10).toImage()
            best = max(best, sum(
                1 for y in range(still.height()) for x in range(still.width())
                if frame.pixel(x, y) != still.pixel(x, y)))
        assert best >= minimum, (name, best)


class _FakeScreen:
    def __init__(self, dpr: float) -> None:
        self._dpr = dpr

    def devicePixelRatio(self) -> float:  # noqa: N802 - API Qt
        return self._dpr


def test_icon_density_follows_the_active_screen_not_the_primary(monkeypatch):
    """AZ-C08 : le facteur HiDPI vient de l'écran de la fenêtre, pas de la primaire seule."""
    monkeypatch.setattr(icons, "_active_screen", lambda: _FakeScreen(3.0))
    assert icons._render_scale() == pytest.approx(3.0)
    pixmap = icons.pixmap("globe", 20, "muted")
    assert pixmap.devicePixelRatio() == pytest.approx(3.0)


def test_active_screen_prefers_the_window_over_the_primary(monkeypatch):
    """La primaire n'est qu'un repli : la fenêtre active décide de la densité."""

    class _ScreenName(str):
        """Nom d'écran qui répond aussi à l'API Qt lue par la sélection d'écran."""

        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            return 2.0

    class _Window:
        def screen(self):
            return _ScreenName("écran-secondaire")

        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            return 1.0

    class _App:
        @staticmethod
        def focusWindow():
            return _Window()

        @staticmethod
        def topLevelWindows():
            return []

        @staticmethod
        def primaryScreen():
            return _ScreenName("écran-principal")

    monkeypatch.setattr(icons, "QGuiApplication", _App)
    assert icons._active_screen() == "écran-secondaire"

    class _NoWindow(_App):
        @staticmethod
        def focusWindow():
            return None

    monkeypatch.setattr(icons, "QGuiApplication", _NoWindow)
    assert icons._active_screen() == "écran-principal"


def test_icon_density_ignores_a_dead_qscreen_wrapper(monkeypatch):
    """AZ-C09 : macOS recrée ses QScreen à l'affichage ; un wrapper mort ne fige pas le DPR.

    Mesuré sur MacBook Air Retina : après le premier changement de zoom, `window.screen()`
    rendait un objet QScreen détruit, `_device_pixel_ratio()` retombait à 1 et l'emblème
    était rastérisé à 80 px physiques pour 80 px logiques — flou sur un écran 2×.
    """
    from PySide6.QtCore import QRect

    class _DeadScreen:
        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            raise RuntimeError("libshiboken: Internal C++ object already deleted.")

    class _LiveScreen:
        def __init__(self, dpr: float, name: str = "Écran Retina intégré") -> None:
            self._dpr, self._name = dpr, name

        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            return self._dpr

        def name(self) -> str:                     # noqa: N802 - API Qt
            return self._name

    class _Window:
        def screen(self):
            return _DeadScreen()

        def geometry(self):                        # noqa: N802 - API Qt
            return QRect(0, 0, 1200, 800)

        def isVisible(self) -> bool:               # noqa: N802 - API Qt
            return True

        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            return 1.0

    class _App:
        @staticmethod
        def focusWindow():
            return _Window()

        @staticmethod
        def topLevelWindows():
            return []

        @staticmethod
        def screenAt(_point):
            return _LiveScreen(2.0)

        @staticmethod
        def screens():
            return [_LiveScreen(2.0)]

        @staticmethod
        def primaryScreen():
            return _LiveScreen(2.0)

    monkeypatch.setattr(icons, "QGuiApplication", _App)
    icons.clear_cache()
    assert icons._device_pixel_ratio() == pytest.approx(2.0)
    assert icons._render_scale() == pytest.approx(2.0)

    pixmap = icons.brand_pixmap("azimut_wordmark", 64)
    assert pixmap.devicePixelRatio() == pytest.approx(2.0)
    assert round(pixmap.width() / pixmap.devicePixelRatio()) == 64
    assert round(pixmap.height() / pixmap.devicePixelRatio()) == 64


def test_icon_density_prefers_the_window_when_the_screen_announces_1x(monkeypatch):
    """AZ-C09 : l'écran reste vivant mais annonce 1.0 pendant que la fenêtre peint en 2×.

    Cas exactement reproduit sur le MacBook Air juste après le premier changement de zoom :
    aucun wrapper n'est mort, l'écran rapporte simplement une densité fausse le temps que le
    WindowServer se stabilise (mesuré : écran 1.0, fenêtre 2.0). Le repli sur un wrapper mort
    ne suffit donc pas ; c'est la règle du maximum qui doit rendre l'emblème net.
    """
    from PySide6.QtCore import QRect

    class _Screen:
        def __init__(self, dpr: float) -> None:
            self._dpr = dpr

        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            return self._dpr

        def name(self) -> str:                     # noqa: N802 - API Qt
            return "Écran Retina intégré"

    class _Window:
        def __init__(self, dpr: float) -> None:
            self._dpr = dpr

        def screen(self):
            return _Screen(1.0)                    # l'écran ment, mais reste vivant

        def geometry(self):                        # noqa: N802 - API Qt
            return QRect(0, 0, 1200, 800)

        def isVisible(self) -> bool:               # noqa: N802 - API Qt
            return True

        def devicePixelRatio(self) -> float:       # noqa: N802 - API Qt
            return self._dpr

    def fake_app(window_dpr: float):
        window = _Window(window_dpr)

        class _App:
            @staticmethod
            def focusWindow():
                return window

            @staticmethod
            def topLevelWindows():
                return []

            @staticmethod
            def screenAt(_point):
                return _Screen(1.0)

            @staticmethod
            def screens():
                return [_Screen(1.0)]

            @staticmethod
            def primaryScreen():
                return _Screen(1.0)

        return _App

    # La fenêtre est déjà peinte en 2× : l'écran à 1.0 ne doit pas abaisser la densité.
    monkeypatch.setattr(icons, "QGuiApplication", fake_app(2.0))
    icons.clear_cache()
    assert icons._screen_pixel_ratio(icons._active_screen()) == pytest.approx(1.0)
    assert icons._window_pixel_ratio() == pytest.approx(2.0)
    assert icons._device_pixel_ratio() == pytest.approx(2.0)
    assert icons._render_scale() == pytest.approx(2.0)
    pixmap = icons.brand_pixmap("azimut_wordmark", 64)
    assert pixmap.devicePixelRatio() == pytest.approx(2.0)
    assert round(pixmap.width() / pixmap.devicePixelRatio()) == 64

    # Sur un vrai écran 1×, rien n'est inventé : la densité logique reste 1.
    monkeypatch.setattr(icons, "QGuiApplication", fake_app(1.0))
    icons.clear_cache()
    assert icons._device_pixel_ratio() == pytest.approx(1.0)
    assert icons._render_scale() == pytest.approx(icons._MIN_RENDER_DPR)


def test_hidpi_animation_keeps_the_icon_centred(manager):
    """Sur un pixmap 2×, les couches composées ne doivent pas rétrécir dans un coin."""
    manager.set_icon_theme("animated")
    base = icons.pixmap("gears", 20, "#ffffff")
    assert base.devicePixelRatio() == 2.0
    x0, y0, x1, y1 = _opaque_bounds(base)

    moved = icons.hovered_pixmap(base, "gears", 0.5)
    mx0, my0, mx1, my1 = _opaque_bounds(moved)
    assert abs((mx0 + mx1) - (x0 + x1)) <= 2
    assert abs((my0 + my1) - (y0 + y1)) <= 2
    assert mx1 - mx0 > (x1 - x0) * 0.9


def test_liquid_button_icon_animates_on_hover(manager):
    """Un tracé animé (la grille) passe bien par le QIcon du bouton, puis revient au repos."""
    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    button = LiquidButton("Test")
    button.setIcon(icons.icon("grid", "text", 18))
    button.resize(140, 36)
    button.show()
    QApplication.processEvents()
    size = button.icon().availableSizes()[0]
    base = button.icon().pixmap(size)
    hover = button._icon_hover

    QApplication.sendEvent(button, QEvent(QEvent.Type.Enter))
    hover._anim.stop()
    hover._anim.setCurrentTime(hover._anim.duration() // 2)
    QApplication.processEvents()
    animated = button.icon().pixmap(size)
    assert bytes(animated.toImage().constBits()) != bytes(base.toImage().constBits())

    QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
    hover._anim.stop()
    hover._anim.setCurrentTime(hover._anim.duration())
    QApplication.processEvents()
    restored = button.icon().pixmap(size)
    assert bytes(restored.toImage().constBits()) == bytes(base.toImage().constBits())


def test_module_card_icon_loops_while_hovered(manager):
    """Le mouvement de l'icône se répète tant que la carte est survolée."""
    from azimut.core.module_spec import ModuleSpec
    from azimut.launcher.atlas_window import ModuleCard

    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    spec = ModuleSpec(id="dedup", name="Dedup", tagline="Doublons", icon="sparkles",
                      entry="azimut.modules.dedup:Window")
    card = ModuleCard(spec)
    card.resize(320, 200)
    card.show()
    QApplication.processEvents()
    base = card.icon_label.pixmap()

    QApplication.sendEvent(card, QEvent(QEvent.Type.Enter))
    assert card._icon_anim.duration() == round(
        icons.hover_duration("sparkles") * card.ICON_LOOP_FACTOR)
    assert card._icon_anim.loopCount() == -1
    card._icon_anim.stop()
    card._icon_clock.invalidate()
    card._icon_anim.setCurrentTime(card._icon_anim.duration() // 2)
    QApplication.processEvents()
    assert bytes(card.icon_label.pixmap().toImage().constBits()) \
        != bytes(base.toImage().constBits())

    QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))
    assert card._icon_anim.loopCount() == 1
    card._icon_anim.stop()
    card._icon_anim.setCurrentTime(card._icon_anim.duration())
    QApplication.processEvents()
    assert bytes(card.icon_label.pixmap().toImage().constBits()) \
        == bytes(base.toImage().constBits())


def test_takeout_card_icon_jump_is_not_clipped(manager):
    """L'épingle de Takeout monte dans la boîte de 28 px, pas dans les 20 px du dessin."""
    from azimut.core.module_spec import ModuleSpec
    from azimut.launcher.atlas_window import ModuleCard

    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    icons.clear_cache()
    spec = ModuleSpec(id="takeout", name="Takeout", tagline="t", icon="map-pin", entry="x:Y")
    card = ModuleCard(spec)
    card.resize(320, 200)
    card.show()
    QApplication.processEvents()

    QApplication.sendEvent(card, QEvent(QEvent.Type.Enter))
    card._icon_anim.stop()
    card._icon_clock.invalidate()
    card._icon_anim.setCurrentTime(round(card._icon_anim.duration() * 0.6))
    QApplication.processEvents()

    peak = card.icon_label.pixmap()
    assert peak.deviceIndependentSize().toSize().width() == 28
    assert _opaque_bounds(peak)[1] > 0            # rien n'est coupé en haut


def test_map_pin_hop_keeps_its_top_margin(manager):
    """Avec la marge de la boîte, le sommet du saut reste dans le pixmap."""
    manager.set_icon_theme("animated")
    icons.clear_cache()
    base = icons.pixmap("map-pin", 20, "#ffffff")
    still = icons.hovered_pixmap(base, "map-pin", 0.001, padding=4)
    peak = icons.hovered_pixmap(base, "map-pin", 0.6, padding=4)
    pad = round(4 * base.devicePixelRatio())
    assert peak.size() == still.size() == base.size() + QSize(2 * pad, 2 * pad)
    assert _opaque_bounds(peak)[1] > 0
    assert _opaque_bounds(peak)[1] < _opaque_bounds(still)[1]


def test_hidden_card_stops_its_icon_loop(manager):
    """Une carte cachée (page quittée) ne doit pas animer en arrière-plan."""
    from azimut.core.module_spec import ModuleSpec
    from azimut.launcher.atlas_window import ModuleCard

    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    spec = ModuleSpec(id="hidden", name="Hidden", tagline="t", icon="sparkles", entry="x:Y")
    card = ModuleCard(spec)
    card.show()
    QApplication.processEvents()
    QApplication.sendEvent(card, QEvent(QEvent.Type.Enter))
    assert card._icon_anim.state() == card._icon_anim.State.Running

    card.hide()
    assert card._icon_anim.state() != card._icon_anim.State.Running
    assert card._icon_progress == 0.0


def test_glass_pointer_moves_are_coalesced(manager):
    """Un trackpad bavard ne doit pas repeindre la carte à chaque événement."""
    frame = GlassFrame(interactive=True)
    frame.resize(220, 140)
    frame.show()
    QApplication.processEvents()
    frame._hover.value = 1.0

    class _Paints(QObject):
        def __init__(self):
            super().__init__()
            self.count = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                self.count += 1
            return False

    counter = _Paints()
    frame.installEventFilter(counter)
    for step in range(40):
        event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(step, step), QPointF(step, step),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(frame, event)
        QApplication.processEvents()

    assert frame._pointer_timer.isActive()
    assert counter.count <= 2


def test_glass_pointer_ignores_trackpad_jitter(manager):
    """Un doigt immobile ne doit pas déclencher de repaint du reflet."""
    frame = GlassFrame(interactive=True)
    frame.resize(220, 140)
    frame.show()
    QApplication.processEvents()
    frame._hover.value = 1.0
    frame._glass_pointer = QPointF(50.0, 50.0)
    frame._glass_pointer_set = True

    def move(x: float, y: float) -> None:
        event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(frame, event)

    for step in range(40):
        move(50.0 + 0.2 * (step % 3), 50.0 + 0.2 * (step % 2))
    assert not frame._pointer_timer.isActive()

    move(60.0, 60.0)
    assert frame._pointer_timer.isActive()


def test_tab_bar_can_disable_icon_hover(manager):
    """Les onglets d'outils et de Conseils figent le pictogramme au survol."""
    manager.set_icon_theme("animated")
    bar = GlassTabBar(animate_icons=False)
    bar.addTab(icons.icon("search", "text", 18), "A")

    assert bar._icon_hover is None


def test_tab_icon_animates_on_hover(manager):
    """Les onglets ne sont pas des boutons : leur QIcon est animé par GlassTabBar."""
    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    bar = GlassTabBar()
    bar.addTab(icons.icon("gears", "text", 18), "A")
    bar.resize(240, 40)
    bar.show()
    QApplication.processEvents()
    base = bar.tabIcon(0).pixmap(18)

    bar._icon_hover._hover_at(0)
    bar._icon_hover._anim.stop()
    bar._icon_hover._anim.setCurrentTime(bar._icon_hover._anim.duration() // 2)
    QApplication.processEvents()
    animated = bar.tabIcon(0).pixmap(18)
    assert bytes(animated.toImage().constBits()) != bytes(base.toImage().constBits())

    bar._icon_hover._leave()
    bar._icon_hover._anim.stop()
    bar._icon_hover._anim.setCurrentTime(bar._icon_hover._anim.duration())
    QApplication.processEvents()
    restored = bar.tabIcon(0).pixmap(18)
    assert bytes(restored.toImage().constBits()) == bytes(base.toImage().constBits())


def test_icon_label_hover_animation(manager):
    from PySide6.QtCore import QAbstractAnimation

    manager.set_icon_theme("animated")
    manager.set_reduce_motion(False)
    label = IconLabel("Attention", "alert-triangle")
    label._animate_hover(1.0)
    assert label._hover_anim.state() == QAbstractAnimation.State.Running
    label._hover_anim.setCurrentTime(label._hover_anim.duration())
    assert label._hover > 0.9
    label._animate_hover(0.0)
    label._hover_anim.setCurrentTime(label._hover_anim.duration())
    assert label._hover < 0.1


def test_status_chip_turns_glyph_into_icon():
    chip = StatusChip("✔ ffmpeg", "ok")
    assert chip.text() == "ffmpeg" and chip.icon_name() == "check" and chip.property("level") == "ok"
    chip.set_state("✖ ffmpeg", "err")
    assert chip.icon_name() == "cross"
    chip.set_state("Prêt", "ok")
    assert chip.text() == "Prêt" and chip.icon_name() == ""
    chip.grab()                                     # le rendu ne doit pas lever d'exception


def test_icon_label_and_drop_zone_render():
    label = IconLabel("Attention", "alert-triangle", "warn_txt")
    assert label.icon_name() == "alert-triangle" and label.indent() > 0
    label.set_icon("")
    assert label.icon_name() == ""
    zone = DropZone("Déposez ici", icon="folder")
    assert not zone._icon_label.pixmap().isNull()
    zone.set_icon("")
    assert zone._icon_label.isHidden()
    zone.set_icon("check")
    assert not zone._icon_label.pixmap().isNull()
