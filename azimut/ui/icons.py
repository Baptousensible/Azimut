"""Icônes d'Azimut : SVG Papicons + Phosphor Fill (tous deux MIT), recolorés à la volée selon le thème.

Papicons est le jeu principal, mais il ne compte qu'une centaine de pictogrammes (pensés pour une app
scolaire) : il n'a ni vidéo, ni musique, ni copie, ni étiquette… Ce qui manque est complété par une
sélection de Phosphor Fill (``resources/icons/phosphor``), au trait et au poids très proches. À nom égal,
Papicons l'emporte.

Pourquoi pas des emojis ? Ils dépendent d'une police d'emojis installée sur le système : présente sur
Linux Mint, souvent absente d'une session Fedora/KDE (les cartes s'affichent alors vides ou en
« tofu »). Les SVG sont embarqués dans ``azimut/resources/icons`` et rendus par Qt : le résultat est
identique partout.

Les icônes sont monochromes (fill noir) : on remplace simplement la couleur avant le rendu.
`color` accepte un code hexadécimal (« #aabbcc ») ou le nom d'un token du thème (« muted », « ok_txt »…).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from azimut.core.logs import get_logger
from azimut.core.paths import resource_path

log = get_logger(__name__)

# Glyphes texte historiques → nom d'icône (voir StatusChip et ModuleWindow).
GLYPH_ICONS: dict[str, str] = {"✔": "check", "✖": "cross", "⚠": "alert-triangle"}

_FILL = re.compile(r'fill="(?:black|#000(?:000)?)"', re.IGNORECASE)


# Dossiers de recherche, par ordre de priorité (Papicons, puis le complément Phosphor).
_ICON_DIRS: tuple[tuple[str, ...], ...] = (("icons",), ("icons", "phosphor"))


def _find(name: str) -> Path | None:
    """Fichier SVG de l'icône `name`, ou None (un emoji ou un texte quelconque n'en a pas)."""
    if not name or not name.replace("-", "").isalnum():
        return None
    for parts in _ICON_DIRS:
        path = resource_path(*parts, f"{name}.svg")
        if path.is_file():
            return path
    return None


def available(name: str) -> bool:
    """Vrai si une icône SVG porte ce nom (un emoji ou un texte quelconque renvoie False)."""
    return _find(name) is not None


def _resolve_color(color: str | QColor | None) -> str:
    """Token du thème, code hexadécimal ou QColor → « #rrggbb » ; défaut : couleur de texte du thème."""
    if isinstance(color, QColor):
        return color.name()
    from azimut.ui.theme import palette, ThemeManager   # import tardif : theme n'importe pas icons
    pal = palette(ThemeManager.instance().theme())
    if not color:
        return pal["text"]
    return pal.get(color, color)


@lru_cache(maxsize=256)
def _svg_source(name: str) -> str:
    path = _find(name)
    return path.read_text(encoding="utf-8") if path else ""


def _device_pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(1.0, float(screen.devicePixelRatio())) if screen is not None else 1.0


@lru_cache(maxsize=512)
def _render(name: str, color_hex: str, size: int, dpr: float) -> QPixmap:
    side = max(1, round(size * dpr))
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    svg = _FILL.sub(f'fill="{color_hex}"', _svg_source(name))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def pixmap(name: str, size: int = 20, color: str | QColor | None = None) -> QPixmap:
    """Pixmap net (écrans HiDPI inclus) ; pixmap vide si l'icône n'existe pas."""
    if not available(name):
        log.debug("Icône introuvable : %s", name)
        return QPixmap()
    return _render(name, _resolve_color(color), int(size), _device_pixel_ratio())


def icon(name: str, color: str | QColor | None = None, size: int = 24) -> QIcon:
    """QIcon (état normal + désactivé grisé) ; QIcon vide si l'icône n'existe pas."""
    result = QIcon()
    if not available(name):
        return result
    result.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, size, "muted"), QIcon.Mode.Disabled)
    return result


def tile_icon(name: str, background: str = "#2f6fed", foreground: str = "#ffffff", size: int = 256) -> QIcon:
    """Icône d'application : pictogramme blanc sur tuile arrondie colorée (fenêtre, barre des tâches)."""
    if not available(name):
        return QIcon()
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    inner = size * 0.58
    offset = (size - inner) / 2
    svg = _FILL.sub(f'fill="{foreground}"', _svg_source(name))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        renderer.render(painter, QRectF(offset, offset, inner, inner))
    painter.end()
    return QIcon(QPixmap.fromImage(image))


def split_glyph(text: str) -> tuple[str, str]:
    """« ✔ ffmpeg » → (« check », « ffmpeg ») ; texte sans glyphe connu → (« », texte inchangé)."""
    if text and text[0] in GLYPH_ICONS:
        return GLYPH_ICONS[text[0]], text[1:].lstrip()
    return "", text


def clear_cache() -> None:
    _render.cache_clear()
