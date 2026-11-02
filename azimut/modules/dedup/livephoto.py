"""Repérage des « Live Photos » Apple (logique pure, sans Qt).

Une Live Photo exportée par Google Takeout donne deux fichiers de même nom : une photo (IMG_1234.HEIC ou .JPG)
et une courte vidéo (IMG_1234.MOV ou .MP4, ≈ 1,5 à 3 s). Après Azimut Convert (option « nom + extension
d'origine »), les noms deviennent « IMG_1234_heic.avif » et « IMG_1234_mov.webm » : les deux formes sont reconnues.

Critères : même dossier (et même côté A/B), même nom d'origine, une photo + UNE vidéo, vidéo courte (durée lue
avec ffprobe, seuil réglable). Les métadonnées Apple qui identifient formellement une Live Photo disparaissent à
la conversion : le nom et la durée sont donc les seuls indices fiables.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

PHOTO_EXTS = frozenset({"heic", "heif", "jpg", "jpeg", "png"})       # partie « photo » (sans point)
VIDEO_EXTS = frozenset({"mov", "mp4", "m4v"})                        # partie « vidéo »
DEFAULT_MAX_SECONDS = 5.0                                            # une Live Photo dure ≈ 1,5 à 3,5 s

_CONVERTED = re.compile(r"^(?P<stem>.+)_(?P<ext>[A-Za-z0-9]{2,5})\.(?:avif|webm)$", re.IGNORECASE)
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).lower()


def original_name(filename: str) -> tuple[str, str]:
    """('IMG_1234', 'heic') depuis « IMG_1234_heic.avif » (converti) ou « IMG_1234.HEIC » (brut)."""
    m = _CONVERTED.match(filename)
    if m and m.group("ext").lower() in PHOTO_EXTS | VIDEO_EXTS:
        return m.group("stem"), m.group("ext").lower()
    stem, dot, ext = filename.rpartition(".")
    return (stem, ext.lower()) if dot else (filename, "")


def is_photo_name(filename: str) -> bool:
    return original_name(filename)[1] in PHOTO_EXTS


def is_video_name(filename: str) -> bool:
    return original_name(filename)[1] in VIDEO_EXTS


def pair_up(entries: Sequence[tuple[str, str]]) -> tuple[list[tuple[list[int], int]], int]:
    """``entries`` : (dossier, nom de fichier) pour chaque fichier.

    Retourne ([(indices des photos, indice de la vidéo)], nombre de cas ambigus). Ambigu = plusieurs vidéos de
    même nom dans le dossier (ex. .MOV et .MP4) : ignoré, car on ne sait pas laquelle est la vidéo de la photo.
    """
    by_base: dict[tuple[str, str], tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))
    for i, (folder, name) in enumerate(entries):
        stem, ext = original_name(name)
        if ext in PHOTO_EXTS:
            by_base[(_fold(folder), _fold(stem))][0].append(i)
        elif ext in VIDEO_EXTS:
            by_base[(_fold(folder), _fold(stem))][1].append(i)
    pairs: list[tuple[list[int], int]] = []
    ambiguous = 0
    for photos, videos in by_base.values():
        if not photos or not videos:
            continue
        if len(videos) > 1:
            ambiguous += 1
            continue
        pairs.append((photos, videos[0]))
    return pairs, ambiguous


def parse_duration(output: str) -> float | None:
    """Durée en secondes depuis la sortie de ffprobe (une seule valeur), ou None."""
    try:
        value = float(output.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def video_duration(ffprobe: Path | str, path: Path, timeout: float = 30) -> float | None:
    """Durée d'une vidéo (ffprobe) ; None si illisible ou si ffprobe est absent."""
    cmd = [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_duration(res.stdout) if res.returncode == 0 else None
