"""Images des indicateurs du thème : coche des cases, point des boutons radio, chevrons des arbres.

Le QSS ne sait pas dessiner une coche : une case « cochée » n'était donc qu'un aplat bleu, et une case
« décochée » un cadre vide, ce qui se lisait « blanc ou bleu » sans repère. Ces images sont dessinées une fois
par thème (PNG 1x et @2x pour les écrans HiDPI) dans le dossier de cache, puis référencées par ``image: url()``.

Aucun ``QApplication`` n'est nécessaire : seul ``QImage`` est utilisé.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer

from azimut.core.logs import get_logger
from azimut.core.paths import resource_path, user_cache_dir

log = get_logger(__name__)

BOX = 16            # côté (px logiques) des images de cases et de radios
CHEVRON = 12        # côté des chevrons


def _canvas(side: int, scale: int) -> tuple[QImage, QPainter]:
    image = QImage(side * scale, side * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    return image, painter


def check_image(color: str, scale: int = 1) -> QImage:
    """Coche (trait arrondi) sur fond transparent, à poser sur l'aplat coloré de la case."""
    image, p = _canvas(BOX, scale)
    pen = QPen(QColor(color), 2.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline([QPointF(3.6, 8.6), QPointF(6.7, 11.6), QPointF(12.4, 4.9)])
    p.end()
    return image


def radio_image(color: str, scale: int = 1) -> QImage:
    """Point plein centré d'un bouton radio sélectionné."""
    image, p = _canvas(BOX, scale)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(3.5, 3.5, BOX - 7, BOX - 7))
    p.end()
    return image


def chevron_image(name: str, color: str, scale: int = 1) -> QImage:
    """Chevron Papicons (``chevron-right`` / ``chevron-down``) recoloré."""
    side = CHEVRON * scale
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    svg = resource_path("icons", f"{name}.svg").read_text(encoding="utf-8")
    svg = svg.replace('fill="black"', f'fill="{color}"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()
    return image


def _target_dir(theme: str) -> Path:
    try:
        base = user_cache_dir()
    except OSError:
        base = Path(tempfile.gettempdir()) / "azimut"
    folder = base / "indicators" / theme          # un dossier par thème : Qt met les images en cache par chemin
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_images(theme: str, colors: Mapping[str, str]) -> dict[str, str]:
    """Dessine les images du thème et renvoie leurs chemins (séparateurs « / », prêts pour ``url()``).

    ``colors`` : jetons de la palette (``primary``, ``muted``). Renvoie un dictionnaire vide si l'écriture
    échoue : le QSS retombe alors sur ``image: none`` (les cases restent utilisables, sans coche).
    """
    try:
        folder = _target_dir(theme)
        out: dict[str, str] = {}
        jobs = {
            "check": lambda s: check_image("#ffffff", s),
            "radio": lambda s: radio_image(colors["primary"], s),
            "chevron_right": lambda s: chevron_image("chevron-right", colors["muted"], s),
            "chevron_down": lambda s: chevron_image("chevron-down", colors["muted"], s),
            "chevron_up": lambda s: chevron_image("chevron-up", colors["muted"], s),
        }
        for name, draw in jobs.items():
            for scale, suffix in ((1, ""), (2, "@2x")):
                path = folder / f"{name}{suffix}.png"
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                encoded = draw(scale).save(buffer, "PNG")
                buffer.close()
                if not encoded:
                    raise OSError(f"encodage impossible : {path}")
                # Réécrire un PNG identique userait le disque pour rien : on compare.
                if not path.exists() or path.read_bytes() != bytes(data):
                    path.write_bytes(bytes(data))
                if scale == 1:
                    out[name] = path.as_posix()
        return out
    except Exception as exc:                      # noqa: BLE001 - l'interface doit rester utilisable
        log.warning("Images d'indicateurs indisponibles (%s) : cases sans coche.", exc)
        return {}
