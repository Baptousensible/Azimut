"""Repérage des « Live Photos » Apple (logique pure, sans Qt).

Une Live Photo exportée par Google Takeout donne deux fichiers de même nom : une photo (IMG_1234.HEIC ou .JPG)
et une courte vidéo (IMG_1234.MOV ou .MP4, ≈ 1,5 à 3 s). Après Azimut Convert (option « nom + extension
d'origine »), les noms deviennent « IMG_1234_heic.avif » et « IMG_1234_mov.webm » : les deux formes sont reconnues.

Critères : même dossier (et même côté A/B), même nom d'origine, une photo + UNE vidéo, vidéo courte (durée lue
avec ffprobe, seuil réglable). Les métadonnées Apple qui identifient formellement une Live Photo disparaissent à
la conversion : le nom et la durée sont donc les seuls indices fiables.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path

from azimut.core.ffmpeg_args import SAFE_INPUT_FLAGS, input_spec
from azimut.core.logs import get_logger

_log = get_logger("dedup.livephoto")

PHOTO_EXTS = frozenset({"heic", "heif", "jpg", "jpeg", "png"})       # partie « photo » (sans point)
VIDEO_EXTS = frozenset({"mov", "mp4", "m4v"})                        # partie « vidéo »
DEFAULT_MAX_SECONDS = 5.0                                            # une Live Photo dure ≈ 1,5 à 3,5 s

_CONVERTED = re.compile(r"^(?P<stem>.+)_(?P<ext>[A-Za-z0-9]{2,5})\.(?:avif|webm)$", re.IGNORECASE)
_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).lower()


def _fold_folder(text: str) -> str:
    """Clé d'un dossier : Unicode NFC, et la casse seulement là où le système l'ignore (Windows).

    Sur un système de fichiers sensible à la casse, « Album » et « album » sont deux dossiers : jumeler
    `Album/IMG_1.HEIC` avec `album/IMG_1.MOV` proposait une fausse Live Photo (LOG-012). Les deux fichiers d'une
    vraie paire sont dans le même dossier et en portent la même graphie.
    """
    return os.path.normcase(unicodedata.normalize("NFC", text))


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
            by_base[(_fold_folder(folder), _fold(stem))][0].append(i)
        elif ext in VIDEO_EXTS:
            by_base[(_fold_folder(folder), _fold(stem))][1].append(i)
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


_POLL_SECONDS = 0.1                                  # granularité d'annulation
_KILL_GRACE = 1.0                                    # délai laissé à ffprobe après SIGTERM
_DRAIN_SECONDS = 2.0                                 # attente maximale des lectures après un arrêt
_STDOUT_MAX = 64 * 1024                              # ffprobe n'écrit qu'une valeur : capture bornée
_CHUNK = 8192                                        # taille de lecture unitaire
_TASKKILL_TIMEOUT = 5.0                              # délai laissé à taskkill pour tuer le sous-arbre


def _kill_tree_windows(proc: subprocess.Popen) -> None:
    """Sous Windows, TerminateProcess ne tue que l'enfant direct : ``taskkill /T`` couvre le sous-arbre."""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], timeout=_TASKKILL_TIMEOUT,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, **_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.debug("taskkill a échoué pour la sonde %s : %s", proc.pid, exc)
    try:
        proc.wait(timeout=_KILL_GRACE)
        return
    except subprocess.TimeoutExpired as exc:
        _log.debug("La sonde %s ignore taskkill : %s", proc.pid, exc)
    try:
        proc.kill()
    except OSError as exc:
        _log.debug("kill() impossible sur la sonde %s : %s", proc.pid, exc)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Arrête ffprobe et son groupe : un wrapper a pu lancer le vrai binaire (DEDUP-004)."""
    if proc.poll() is not None:
        return
    if _IS_WINDOWS:
        _kill_tree_windows(proc)
        return
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError as exc:
            _log.debug("SIGTERM au groupe de la sonde %s impossible : %s", proc.pid, exc)
        else:
            try:
                proc.wait(timeout=_KILL_GRACE)
                return
            except subprocess.TimeoutExpired as exc:
                _log.debug("La sonde %s ignore le SIGTERM du groupe : %s", proc.pid, exc)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except OSError as exc:
            _log.debug("SIGKILL au groupe de la sonde %s impossible : %s", proc.pid, exc)
    try:
        proc.kill()
    except OSError as exc:
        _log.debug("kill() impossible après le groupe (%s) : %s", proc.pid, exc)


def _read_capped(stream, kept: bytearray, limit: int) -> None:
    """Vide un tuyau en continu en ne gardant que ``limit`` octets : la mémoire reste bornée (DEDUP-005)."""
    try:
        while chunk := stream.read1(_CHUNK):
            if len(kept) < limit:
                kept += chunk[: limit - len(kept)]
    except (OSError, ValueError) as exc:              # tuyau fermé par l'arrêt du processus
        _log.debug("Lecture du tuyau interrompue : %s", exc)


def video_duration(ffprobe: Path | str, path: Path, timeout: float = 30,
                   cancel: Callable[[], bool] | None = None) -> float | None:
    """Durée d'une vidéo (ffprobe) ; None si illisible, annulé ou si ffprobe est absent.

    ``cancel`` est consulté toutes les 0,1 s : une annulation tue ffprobe et son groupe sans attendre
    ``timeout`` (DEDUP-003), ce qui évite de laisser vivre le petit-fils d'un wrapper (DEDUP-004).
    La sortie est drainée en continu et seule tête de stdout est gardée : un ffprobe très bavard ne
    peut ni bloquer sur un tuyau plein ni saturer la mémoire (DEDUP-005).
    """
    cmd = [str(ffprobe), *SAFE_INPUT_FLAGS, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", input_spec(path)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=not _IS_WINDOWS, **_NO_WINDOW)
    except OSError:
        return None
    out = bytearray()
    readers = [threading.Thread(target=_read_capped, args=(proc.stdout, out, _STDOUT_MAX), daemon=True),
               threading.Thread(target=_read_capped, args=(proc.stderr, bytearray(), 0), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if cancel is not None and cancel():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_POLL_SECONDS, remaining))
    finally:
        if proc.poll() is None:                       # annulé, timeout ou sortie inattendue
            _kill_tree(proc)
        for reader in readers:
            reader.join(timeout=_DRAIN_SECONDS)
        try:
            proc.wait(timeout=_DRAIN_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _log.debug("ffprobe toujours vivant après la purge : %s", exc)
        if not any(reader.is_alive() for reader in readers):
            for stream in (proc.stdout, proc.stderr):   # communicate() les fermait pour nous
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError) as exc:
                    _log.debug("Fermeture de flux ignorée : %s", exc)
    if proc.returncode != 0:
        return None
    return parse_duration(bytes(out).decode("utf-8", errors="replace"))
