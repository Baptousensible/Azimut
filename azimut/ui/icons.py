"""Icônes d'Azimut : deux thèmes SVG embarqués, recolorés à la volée selon le thème.

- « Papicons » (MIT) : le thème historique, dans ``azimut/resources/icons`` ;
- « Lucide Animées » (MIT) : les icônes de lucide-animated (https://github.com/pqoqubbw/icons), dans
  ``azimut/resources/icons_animated``, dont les widgets animent le survol
  (voir ``motion_enabled``).

Pourquoi pas des emojis ? Ils dépendent d'une police d'emojis installée sur le système : présente sur
Linux Mint, souvent absente d'une session Fedora/KDE (les cartes s'affichent alors vides ou en
« tofu »). Les SVG sont embarqués et rendus par Qt : le résultat est identique partout.

Les deux familles sont monochromes : Papicons remplit les formes en noir, les icônes animées
dessinent des traits avec ``currentColor``. `color` accepte un code hexadécimal (« #aabbcc ») ou le
nom d'un token du thème (« muted », « ok_txt »…).
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from azimut.core.logs import get_logger
from azimut.core.paths import resource_path
from azimut.ui.icon_animation import ANIMATIONS, State

log = get_logger(__name__)

# Glyphes texte historiques → nom d'icône (voir StatusChip et ModuleWindow).
GLYPH_ICONS: dict[str, str] = {"✔": "check", "✖": "cross", "⚠": "alert-triangle"}

_FILL = re.compile(r'fill="(?:black|#000(?:000)?)"', re.IGNORECASE)

# Même à l'échelle 1×, les petits SVG perdent leurs traits fins s'ils sont rasterisés
# exactement à leur taille logique. On les rend donc au moins deux fois plus grands,
# puis Qt les ramène à leur dimension d'affichage : la taille ne change pas, les
# contours sont simplement plus nets.
_MIN_RENDER_DPR = 2.0

# Facteur de dessin de chaque icône animée, calibré sur son équivalent Papicons.
#
# Mesure, faite sur les SVG livrés dans ce dépôt :
#   1. rendre chaque icône seule, en blanc, sur une toile de 64 × 64 px et sans
#      réduction de dessin — ``_render(nom, "#ffffff", 64, 1.0, 1.0)`` ;
#   2. relever la boîte englobante des pixels d'alpha non nul, largeur × hauteur ;
#   3. facteur = racine carrée de (largeur Papicons / largeur animée) ×
#      (hauteur Papicons / hauteur animée), arrondi au centième.
#
# La moyenne géométrique égalise l'aire, pas chaque dimension. Quand les deux dessins
# n'ont pas le même rapport d'aspect (list : lignes nues contre carré plein), aucune
# mise à l'échelle ne peut superposer les deux boîtes sans déformer le tracé ; l'aire
# est le meilleur compromis optique. Un facteur unique laissait les unes trop grosses
# (pen-alt, gallery, info) et les autres trop fines (chevrons, croix).
#
# Les facteurs supérieurs à 1 sont normaux : ``_render`` ne doit donc pas plafonner
# l'échelle. Leur encombrement calibré reste dans la case, rien n'est rogné.
#
# À refaire si un SVG animé change de dessin. Le test
# ``test_all_animated_icons_match_papicons_footprint`` rend les 33 icônes à 32 px
# (64 px physiques) et échoue si leur aire s'écarte de plus de 2 px de celle de leur
# homologue Papicons.
_PAPICON_MATCH: dict[str, float] = {
    "alert-triangle": 0.86,
    "archive": 0.77,
    "arrow-left": 0.99,
    "arrow-right": 0.99,
    "arrow-up": 0.97,
    "bookmark": 0.87,
    "check": 1.12,
    "chevron-down": 1.16,
    "chevron-right": 1.16,
    "chevron-up": 1.16,
    "cross": 1.16,
    "folder": 0.86,
    "gallery": 0.80,
    "gears": 0.83,
    "ghost": 0.89,
    "globe": 0.90,
    "grid": 0.89,
    "info": 0.80,
    "list": 1.06,
    "lock": 0.83,
    "map-pin": 0.87,
    "pause": 0.92,
    "pen-alt": 0.72,
    "pie": 0.78,
    "play": 0.84,
    "search": 0.88,
    "sparkles": 0.77,
    "swap-horizontal": 0.92,
    "tasks": 0.98,
    "trash": 0.86,
    "volume": 0.92,
    "volume-cross": 0.91,
    "volume-half": 0.90,
}

# Repli pour une icône animée sans équivalent Papicons (nouvelle icône du thème).
_ANIMATED_DRAW_SCALE = 0.9

# Respiration appliquée aux rares pictogrammes sans mouvement propre.
_BREATHE_SCALE = 0.06
_ICON_NAMES: dict[int, str] = {}

# Couches d'un pictogramme animé, mémorisées par nom, taille et couleur du tracé.
# La clé interne d'un QPixmap ne convient pas : QIcon.pixmap() renvoie un pixmap
# recopié, dont la clé diffère de celle du rendu d'origine.
_ELEMENT_ID = re.compile(r'\sid="([^"]+)"')
_ELEMENT_TAG = re.compile(
    r'<(?:path|circle|rect|line|polyline|polygon|ellipse)\b[^>]*\sid="([^"]+)"[^>]*/>')
_LAYER_CACHE: dict[tuple[str, int, str], dict[str, QPixmap]] = {}
_LAYER_LIMIT = 192
_INK_CACHE: dict[int, str] = {}
_INK_LIMIT = 256


def hover_duration(name: str, default: int = 260) -> int:
    """Durée du survol propre à l'icône (les composants Motion vont de 320 à 1000 ms)."""
    animation = ANIMATIONS.get(name)
    return animation.duration_ms if animation is not None else default


def _current_theme() -> str:
    """Thème d'icônes choisi (« papicons » ou « animated »), sans cycle d'import."""
    try:
        from azimut.ui.theme import ThemeManager
        return ThemeManager.instance().icon_theme()
    except Exception:                                   # avant l'application : repli historique
        return "papicons"


def _source_path(name: str):
    """Fichier SVG du thème courant, ou repli Papicons si le thème animé ne couvre pas ce nom."""
    if _current_theme() == "animated":
        animated = resource_path("icons_animated", f"{name}.svg")
        if animated.is_file():
            return animated
    papicons = resource_path("icons", f"{name}.svg")
    return papicons if papicons.is_file() else None


def available(name: str) -> bool:
    """Vrai si une icône SVG porte ce nom dans le thème courant (emoji ou texte : False)."""
    return bool(name) and name.replace("-", "").isalnum() and _source_path(name) is not None


def motion_enabled() -> bool:
    """Vrai si le thème animé est actif et que les animations ne sont pas réduites."""
    try:
        from azimut.ui.theme import ThemeManager
        manager = ThemeManager.instance()
        return manager.icon_theme() == "animated" and not manager.reduce_motion()
    except Exception:
        return False


def _draw_scale(name: str, scale: float) -> float:
    """Échelle de dessin effective : calibre chaque icône animée sur son homologue Papicons.

    Le repli Papicons d'une icône absente du thème animé n'est pas concerné : son dessin
    a déjà l'encombrement historique.
    """
    path = _source_path(name)
    if path is not None and path.parent.name == "icons_animated":
        return scale * _PAPICON_MATCH.get(name, _ANIMATED_DRAW_SCALE)
    return scale


def _resolve_color(color: str | QColor | None) -> str:
    """Token du thème, code hexadécimal ou QColor → « #rrggbb » ; défaut : couleur de texte du thème."""
    if isinstance(color, QColor):
        return color.name()
    from azimut.ui.theme import palette, ThemeManager   # import tardif : theme n'importe pas icons
    pal = palette(ThemeManager.instance().theme())
    if not color:
        return pal["text"]
    return pal.get(color, color)


@lru_cache(maxsize=96)
def _svg_source(name: str) -> str:
    """Source SVG du thème courant (le cache est vidé quand le thème d'icônes change)."""
    path = _source_path(name)
    return path.read_text(encoding="utf-8") if path is not None else ""


def _colored_svg(name: str, color_hex: str) -> str:
    """SVG prêt à rendre : Papicons remplit en noir, les icônes animées dessinent avec currentColor."""
    svg = _svg_source(name)
    if "currentColor" in svg:
        return svg.replace("currentColor", color_hex)
    return _FILL.sub(f'fill="{color_hex}"', svg)


def _element_ids(svg: str) -> tuple[str, ...]:
    """Identifiants des tracés du SVG (p1, p2…), dans l'ordre du document."""
    return tuple(_ELEMENT_ID.findall(svg))


def _element_svg(svg: str, element: str) -> str:
    """SVG réduit à un seul tracé : Qt n'expose pas la visibilité par élément."""
    return _ELEMENT_TAG.sub(lambda match: match.group(0) if match.group(1) == element else "", svg)


def _layer_pixmaps(svg: str, side: int, inner: int, offset: float, dpr: float) -> dict[str, QPixmap]:
    """Rend chaque tracé séparément, à la même échelle que le pixmap complet."""
    layers: dict[str, QPixmap] = {}
    for element in _element_ids(svg):
        renderer = QSvgRenderer(QByteArray(_element_svg(svg, element).encode("utf-8")))
        if not renderer.isValid():
            continue
        image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(offset, offset, inner, inner))
        painter.end()
        layer = QPixmap.fromImage(image)
        layer.setDevicePixelRatio(dpr)
        layers[element] = layer
    return layers


def _ink_color(pixmap: QPixmap) -> str:
    """Couleur du trait d'une icône monochrome (pixel le plus opaque du rendu)."""
    key = pixmap.cacheKey()
    known = _INK_CACHE.get(key)
    if known is not None:
        return known
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    alpha, color = 0, "#ffffff"
    for y in range(image.height()):
        for x in range(image.width()):
            rgba = QColor.fromRgba(image.pixel(x, y))
            if rgba.alpha() > alpha:
                alpha, color = rgba.alpha(), rgba.name()
                if alpha == 255:
                    break
        if alpha == 255:
            break
    _INK_CACHE[key] = color
    while len(_INK_CACHE) > _INK_LIMIT:
        _INK_CACHE.pop(next(iter(_INK_CACHE)))
    return color


def _layers_for(name: str, pixmap: QPixmap) -> dict[str, QPixmap]:
    """Couches d'un pictogramme animé, rendues à la taille et à la couleur du pixmap."""
    side = pixmap.width()
    color = _ink_color(pixmap)
    key = (name, side, color)
    layers = _LAYER_CACHE.get(key)
    if layers is not None:
        return layers
    distance = _draw_scale(name, 1.0)
    inner = max(1, round(side * distance))
    offset = (side - inner) / 2.0
    layers = _layer_pixmaps(_colored_svg(name, color), side, inner, offset,
                            pixmap.devicePixelRatio())
    _LAYER_CACHE[key] = layers
    while len(_LAYER_CACHE) > _LAYER_LIMIT:
        _LAYER_CACHE.pop(next(iter(_LAYER_CACHE)))
    return layers


def _device_pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(1.0, float(screen.devicePixelRatio())) if screen is not None else 1.0


def _render_scale() -> float:
    """Densité de rendu des pictogrammes, au moins 2× pour les petits SVG."""
    return max(_MIN_RENDER_DPR, _device_pixel_ratio())


@lru_cache(maxsize=512)
def _render(name: str, color_hex: str, size: int, dpr: float, scale: float = 1.0) -> QPixmap:
    side = max(1, round(size * dpr))
    # Pas de plafond à 1 : le calibrage Papicons agrandit certaines icônes animées
    # (croix, coche, chevrons). Leur encombrement calibré reste dans la case.
    inner = max(1, round(side * max(0.0, scale)))
    offset = (side - inner) / 2
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    svg = _colored_svg(name, color_hex)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(offset, offset, inner, inner))
        painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def pixmap(name: str, size: int = 20, color: str | QColor | None = None,
          scale: float = 1.0) -> QPixmap:
    """Pixmap net (écrans HiDPI inclus) ; `scale` ajuste encore le dessin.

    Le rendu est suréchantillonné au moins 2× puis remis à sa taille logique par
    ``devicePixelRatio`` ; chaque SVG animé est calibré sur l'encombrement de son
    équivalent Papicons. Un pixmap vide est renvoyé si l'icône n'existe pas.
    """
    if not available(name):
        log.debug("Icône introuvable : %s", name)
        return QPixmap()
    return _render(name, _resolve_color(color), int(size), _render_scale(),
                   _draw_scale(name, scale))


def _draw_state(painter: QPainter, image: QImage, state: State, side: float,
                offset: int = 0) -> None:
    """Peint une couche avec sa translation, sa rotation, son échelle et son opacité.

    ``side`` reste la taille du pictogramme (les déplacements sont exprimés dans son
    viewBox 24×24) et ``offset`` la marge transparente ajoutée autour de lui.
    """
    if state.identity:
        painter.drawImage(offset, offset, image)
        return
    centre = offset + side / 2.0
    unit = side / 24.0                              # déplacements exprimés dans le viewBox 24×24
    painter.save()
    painter.setOpacity(max(0.0, min(1.0, state.opacity)))
    painter.translate(centre + state.x * unit, centre + state.y * unit)
    painter.rotate(state.rotate)
    painter.scale(state.scale_x, state.scale_y)
    painter.translate(-centre, -centre)
    painter.drawImage(offset, offset, image)
    painter.restore()


def _breathing_pixmap(pixmap: QPixmap, progress: float, *,
                      padding: float = 0.0) -> QPixmap:
    """Respiration discrète des pictogrammes sans mouvement propre."""
    scale = 1.0 + _BREATHE_SCALE * math.sin(math.pi * progress)
    dpr = pixmap.devicePixelRatio() or 1.0
    pad = max(0, round(float(padding) * dpr))
    image = QImage(pixmap.width() + 2 * pad, pixmap.height() + 2 * pad,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    source = pixmap.toImage()
    # Composition en pixels physiques : sinon une source 2× est dessinée à moitié taille.
    source.setDevicePixelRatio(1.0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = float(pixmap.width())
    painter.translate(pad + side / 2.0, pad + side / 2.0)
    painter.scale(scale, scale)
    painter.translate(-side / 2.0, -side / 2.0)
    painter.drawImage(pad, pad, source)
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    return result


def hovered_pixmap(pixmap: QPixmap, name: str, progress: float, *,
                   padding: float = 0.0) -> QPixmap:
    """Pixmap avec le mouvement de survol de l'icône à la progression donnée.

    Le dessin reste dans la même case : les widgets n'ont donc pas à recalculer
    leur mise en page pendant l'animation. ``progress`` vaut 0 au repos et 1 en
    fin de survol ; le mouvement reprend les variantes Motion de lucide-animated
    (rotation du pictogramme, déplacement d'un tracé, scale, opacité) et retombe
    sur une respiration discrète pour les icônes sans mouvement propre.

    ``padding`` ajoute une marge transparente (en pixels logiques) autour du
    pictogramme, sans changer sa taille ni son centre. Les sauts qui dépassent
    la case d'origine — l'épingle de map-pin monte de 5 unités — restent ainsi
    entiers quand le widget offre une boîte plus grande que l'icône.
    """
    if pixmap.isNull() or progress <= 0.0:
        return pixmap
    t = max(0.0, min(1.0, float(progress)))
    animation = ANIMATIONS.get(name)
    if animation is None:
        return _breathing_pixmap(pixmap, t, padding=padding)
    global_state = State()
    specific: dict[str, State] = {}
    for part in animation.parts:
        state = part.state(t)
        if part.element == "*":
            global_state = global_state.combine(state)
        else:
            specific[part.element] = specific.get(part.element, State()).combine(state)
    layers = _layers_for(name, pixmap) if specific else {}
    side = pixmap.width()
    dpr = pixmap.devicePixelRatio() or 1.0
    pad = max(0, round(float(padding) * dpr))
    image = QImage(pixmap.width() + 2 * pad, pixmap.height() + 2 * pad,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Toutes les images de couche gardent leur DPR (2× sur écran HiDPI) : on les
    # ramène en pixels physiques pour que translations et échelles restent justes.
    if not specific or not layers:
        source = pixmap.toImage()
        source.setDevicePixelRatio(1.0)
        _draw_state(painter, source, global_state, float(side), pad)
    else:
        for element, layer in layers.items():
            state = global_state.combine(specific.get(element, State()))
            source = layer.toImage()
            source.setDevicePixelRatio(1.0)
            _draw_state(painter, source, state, float(side), pad)
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    return result


def icon_name(icon: QIcon) -> str:
    """Nom Azimut d'un QIcon créé par :func:`icon` (vide si l'icône est étrangère)."""
    return _ICON_NAMES.get(icon.cacheKey(), "")


def icon(name: str, color: str | QColor | None = None, size: int = 24,
         scale: float = 1.0, disabled_color: str | QColor = "muted") -> QIcon:
    """QIcon (état normal + désactivé) ; `scale` réduit le dessin dans une case constante.

    `disabled_color` vaut « muted » par défaut ; une couleur explicite garde le
    pictogramme lisible quand le bouton est désactivé, sans dépendre du gris Qt.
    """
    result = QIcon()
    if not available(name):
        return result
    result.addPixmap(pixmap(name, size, color, scale), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, size, disabled_color, scale), QIcon.Mode.Disabled)
    _ICON_NAMES[result.cacheKey()] = name
    return result


def tile_icon(name: str, background: str = "#003c6c", foreground: str = "#ffffff", size: int = 256) -> QIcon:
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
    svg = _colored_svg(name, foreground)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        renderer.render(painter, QRectF(offset, offset, inner, inner))
    painter.end()
    return QIcon(QPixmap.fromImage(image))


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Icône de l'application (PNG embarqué), avec l'ancienne tuile en repli."""
    path = resource_path("azimut.png")
    if path.is_file():
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return tile_icon("globe")


@lru_cache(maxsize=8)
def brand_pixmap(name: str, height: int, variant: str = "") -> QPixmap:
    """Image de marque PNG (logo Azimut), variante de thème et échelle pour l'interface.

    ``variant`` vaut « dark » ou « light » : le fichier ``<name>_<variant>.png`` est
    utilisé s'il existe, sinon l'asset d'origine sert de repli.
    """
    path = resource_path(f"{name}_{variant}.png") if variant else None
    if path is None or not path.is_file():
        path = resource_path(f"{name}.png")
    if not path.is_file():
        return QPixmap()
    source = QPixmap(str(path))
    if source.isNull():
        return source
    dpr = _device_pixel_ratio()
    target = max(1, round(height * dpr))
    pixmap = source.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def split_glyph(text: str) -> tuple[str, str]:
    """« ✔ ffmpeg » → (« check », « ffmpeg ») ; texte sans glyphe connu → (« », texte inchangé)."""
    if text and text[0] in GLYPH_ICONS:
        return GLYPH_ICONS[text[0]], text[1:].lstrip()
    return "", text


def clear_cache() -> None:
    """Vide les rendus mémorisés après un changement de thème d'icônes ou de couleurs."""
    _svg_source.cache_clear()
    _render.cache_clear()
    _LAYER_CACHE.clear()
    _INK_CACHE.clear()
    app_icon.cache_clear()
    brand_pixmap.cache_clear()
