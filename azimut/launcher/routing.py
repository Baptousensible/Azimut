"""Dépôt intelligent d'Atlas : à partir de ce qui est déposé, propose les modules les plus adaptés.

Fonctions pures (aucun Qt, aucune écriture) : l'analyse se limite à l'extension des fichiers et à un
coup d'œil borné dans les dossiers (trois niveaux, quelques centaines d'entrées au plus).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".avif",
                        ".heic", ".heif", ".jxl"})
RAW_EXTS = frozenset({".nef", ".cr2", ".cr3", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".m2ts", ".wmv", ".flv", ".3gp",
                        ".mpg", ".mpeg"})
AUDIO_EXTS = frozenset({".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".amr"})
JSON_EXTS = frozenset({".json"})

SCAN_DEPTH = 3                  # niveaux de dossiers examinés (Takeout : Takeout/Google Photos/Album)
SCAN_LIMIT = 400                # entrées examinées par dossier déposé
TAKEOUT_MARKERS = ("takeout", "google photos", "google photos")


@dataclass
class DropSummary:
    """Ce qui a été déposé, en quelques compteurs."""

    images: int = 0
    raws: int = 0
    videos: int = 0
    audios: int = 0
    jsons: int = 0
    others: int = 0
    folders: int = 0
    takeout: bool = False       # un export Google Photos a l'air d'être présent
    truncated: bool = False     # l'examen d'un dossier a été borné
    examples: list[str] = field(default_factory=list)

    @property
    def media(self) -> int:
        return self.images + self.raws + self.videos + self.audios

    @property
    def total_files(self) -> int:
        return self.media + self.jsons + self.others


@dataclass(frozen=True)
class Suggestion:
    module_id: str
    reason: str                 # phrase courte, en français, pour le choix
    score: int                  # 0–100 : plus haut = plus pertinent


def _count_file(name: str, summary: DropSummary) -> None:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXTS:
        summary.images += 1
    elif ext in RAW_EXTS:
        summary.raws += 1
    elif ext in VIDEO_EXTS:
        summary.videos += 1
    elif ext in AUDIO_EXTS:
        summary.audios += 1
    elif ext in JSON_EXTS:
        summary.jsons += 1
    else:
        summary.others += 1
    low = name.lower()
    if low.endswith(".supplemental-metadata.json") or low.endswith(".supplemental-metadat.json"):
        summary.takeout = True


def _scan_folder(root: str, summary: DropSummary) -> None:
    seen = 0
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    seen += 1
                    if seen > SCAN_LIMIT:
                        summary.truncated = True
                        return
                    if entry.name.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if depth + 1 < SCAN_DEPTH:
                            stack.append((entry.path, depth + 1))
                    else:
                        _count_file(entry.name, summary)
        except OSError:
            continue


def summarize(paths: Iterable[str]) -> DropSummary:
    """Compte ce qui est déposé : fichiers par famille, dossiers, indice d'export Takeout."""
    summary = DropSummary()
    for raw in paths:
        path = str(raw)
        if len(summary.examples) < 3:
            summary.examples.append(os.path.basename(path.rstrip("/\\")) or path)
        if os.path.isdir(path):
            summary.folders += 1
            low = path.lower()
            if any(marker in low for marker in TAKEOUT_MARKERS):
                summary.takeout = True
            _scan_folder(path, summary)
        elif os.path.exists(path):
            _count_file(os.path.basename(path), summary)
    return summary


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


def suggest(paths: Iterable[str], available: Iterable[str] | None = None) -> list[Suggestion]:
    """Modules proposés pour ce dépôt, du plus au moins pertinent (uniquement ceux de `available`)."""
    paths = [str(p) for p in paths]
    s = summarize(paths)
    out: dict[str, Suggestion] = {}

    def add(module_id: str, score: int, reason: str) -> None:
        if module_id not in out or out[module_id].score < score:
            out[module_id] = Suggestion(module_id, reason, score)

    two_folders = s.folders == 2 and len(paths) == 2
    if s.takeout:
        add("takeout", 96, "Un export Google Photos est détecté : réinjecter les dates et positions GPS.")
    if two_folders:
        add("scope", 92, "Deux dossiers : vérifier qu'ils contiennent bien la même chose.")
    if s.folders:
        add("dedup", 88 if two_folders else 72, "Chercher les doublons dans "
            + ("ces dossiers." if s.folders > 1 else "ce dossier."))
    parts = []
    if s.images + s.raws:
        parts.append(_plural(s.images + s.raws, "image", "images"))
    if s.videos:
        parts.append(_plural(s.videos, "vidéo", "vidéos"))
    if s.audios:
        parts.append(_plural(s.audios, "fichier audio", "fichiers audio"))
    if s.media:
        what = ", ".join(parts)
        add("convert", 86 if (s.videos or s.raws) else 80, f"Convertir ou alléger {what}.")
        add("rename", 58, "Inscrire la date de prise de vue dans les noms.")
        add("tags", 62 if s.folders == 0 else 54, "Lire, modifier ou effacer les métadonnées (EXIF, GPS…).")
    if s.images:
        add("press", 66, "Réduire le poids des images sans changer de format.")
    if s.jsons and not s.media:
        add("press", 84, f"Minifier ou formater {_plural(s.jsons, 'fichier JSON', 'fichiers JSON')}.")
    ranked = sorted(out.values(), key=lambda x: (-x.score, x.module_id))
    if available is not None:
        keep = set(available)
        ranked = [x for x in ranked if x.module_id in keep]
    return ranked


def describe(summary: DropSummary) -> str:
    """Résumé lisible du dépôt (« 12 images, 3 vidéos et 2 dossiers »)."""
    bits: list[str] = []
    if summary.images + summary.raws:
        bits.append(_plural(summary.images + summary.raws, "image", "images"))
    if summary.videos:
        bits.append(_plural(summary.videos, "vidéo", "vidéos"))
    if summary.audios:
        bits.append(_plural(summary.audios, "fichier audio", "fichiers audio"))
    if summary.jsons:
        bits.append(_plural(summary.jsons, "fichier JSON", "fichiers JSON"))
    if summary.others:
        bits.append(_plural(summary.others, "autre fichier", "autres fichiers"))
    if summary.folders:
        bits.append(_plural(summary.folders, "dossier", "dossiers"))
    text = " · ".join(bits) if bits else "aucun fichier reconnu"
    return text + (" (aperçu limité)" if summary.truncated else "")
