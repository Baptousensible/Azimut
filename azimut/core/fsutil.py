"""Utilitaires fichiers : formats d'affichage, écriture atomique, parcours."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Iterator

from azimut.core.logs import get_logger

_log = get_logger("fsutil")

_UNIT_COUNT = 5
_UNITS = ("o", "Ko", "Mo", "Go", "To")


def _unit(index: int) -> str:
    """Symbole d'unité traduit (« o/Ko/Mo… » en français, « B/KB/MB… » en anglais).

    Les littéraux sont écrits un par un : ``pyside6-lupdate`` n'extrait que les chaînes
    passées directement à ``translate()``. L'import de Qt reste local pour que ``fsutil``
    serve aussi aux scripts d'empaquetage, qui n'ont pas besoin de PySide6.
    """
    try:
        from PySide6.QtCore import QCoreApplication
    except ImportError:                                  # pragma: no cover - environnement sans Qt
        return _UNITS[index]
    labels = (
        QCoreApplication.translate("Units", "o"),
        QCoreApplication.translate("Units", "Ko"),
        QCoreApplication.translate("Units", "Mo"),
        QCoreApplication.translate("Units", "Go"),
        QCoreApplication.translate("Units", "To"),
    )
    return labels[index]


def _decimal_separator() -> str:
    """Séparateur décimal de la langue active (virgule en français, point en anglais)."""
    try:
        from PySide6.QtCore import QLocale
    except ImportError:                                  # pragma: no cover - environnement sans Qt
        return ","
    return QLocale().decimalPoint()


def format_size(n: float) -> str:
    """Taille lisible dans la langue active : « 1,5 Mo » (fr), « 1.5 MB » (en, base 1024)."""
    value = max(0.0, float(n))
    i = 0
    while value >= 1024 and i < _UNIT_COUNT - 1:
        value /= 1024
        i += 1
    if i == 0:
        return f"{int(value)} {_unit(0)}"
    text = f"{value:.1f}"
    if float(text) >= 1024 and i < _UNIT_COUNT - 1:      # 1023,96 Ko → « 1,0 Mo »
        value /= 1024
        i += 1
        text = f"{value:.1f}"
    return f"{text.replace('.', _decimal_separator())} {_unit(i)}"


def format_duration(seconds: float) -> str:
    """Durée lisible : « 12 s », « 3 min 05 s », « 1 h 02 min 03 s »."""
    total = max(0, int(round(seconds)))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min {s:02d} s"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Écrit via un fichier temporaire voisin puis ``os.replace`` (jamais de fichier à moitié écrit)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError as exc:
            _log.debug("Fichier temporaire non supprimé (%s) : %s", tmp_name, exc)
        raise


def unique_path(dest: Path) -> Path:
    """``dest`` s'il est libre, sinon « nom__1.ext », « nom__2.ext »…"""
    dest = Path(dest)
    if not os.path.lexists(dest):
        return dest
    i = 1
    while True:
        candidate = dest.with_name(f"{dest.stem}__{i}{dest.suffix}")
        if not os.path.lexists(candidate):
            return candidate
        i += 1


def publish_no_replace(part: Path, target: Path, attempts: int = 100) -> Path:
    """Publie ``part`` sous ``target`` **sans jamais écraser** (création exclusive).

    ``os.replace`` écrase silencieusement : un fichier apparu entre la vérification et l'appel serait perdu.
    ``os.link`` échoue si la destination existe (atomique) ; on réessaie alors avec un nom suffixé. Les systèmes
    de fichiers sans liens physiques (FAT/exFAT, partages réseau) retombent sur un ``os.replace`` vérifié.
    """
    part, target = Path(part), Path(target)
    for _ in range(attempts):
        candidate = unique_path(target)
        try:
            os.link(part, candidate)
        except FileExistsError:
            continue                                  # apparu entre-temps : nom suivant
        except OSError:
            if os.path.lexists(candidate):
                continue
            os.replace(part, candidate)
            return candidate
        os.unlink(part)
        return candidate
    raise OSError(f"Impossible de publier « {target} » sans écrasement.")


def move_no_replace(src: Path, dst: Path) -> None:
    """Déplace ``src`` vers ``dst`` en refusant d'écraser (``FileExistsError``).

    Sous POSIX, ``os.rename`` écrase silencieusement : on crée d'abord un lien physique exclusif. Repli
    ``os.rename`` uniquement si le système de fichiers n'a pas de liens physiques.
    """
    src, dst = Path(src), Path(dst)
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError:
        if os.path.lexists(dst):
            raise FileExistsError(f"« {dst} » existe déjà.") from None
        os.rename(src, dst)
        return
    os.unlink(src)


def safe_relative(path: Path, root: Path) -> Path:
    """Chemin relatif à ``root`` ; ``ValueError`` si ``path`` est hors de ``root``."""
    p = Path(os.path.abspath(str(path)))
    r = Path(os.path.abspath(str(root)))
    try:
        return p.relative_to(r)
    except ValueError:
        raise ValueError(f"« {path} » est en dehors de « {root} ».") from None


def _norm_ext(ext: str) -> str:
    ext = ext.lower()
    return ext if ext.startswith(".") else "." + ext


def _is_junction(entry: os.DirEntry) -> bool:
    """Jonction NTFS (Windows) : un dossier qui en cache un autre, parfois en boucle (« Application Data »).

    `DirEntry.is_dir(follow_symlinks=False)` la présente comme un dossier ordinaire et le parcours la suivrait
    sans fin. `is_junction` n'existe que depuis Python 3.12 ; avant, la protection est simplement absente.
    """
    if sys.platform != "win32":
        return False
    check = getattr(entry, "is_junction", None)
    try:
        return bool(check()) if check is not None else False
    except OSError:
        return False


def iter_files(root: Path, *, recursive: bool = True, extensions: Iterable[str] | None = None,
               skip_hidden: bool = True, follow_symlinks: bool = False,
               cancel: Callable[[], bool] | None = None,
               on_link: Callable[[Path], None] | None = None) -> Iterator[Path]:
    """Parcourt ``root`` et produit les fichiers (ordre alphabétique par dossier).

    ``extensions`` : minuscules avec le point (« .jpg ») ; ``None`` = tous.
    S'arrête dès que ``cancel()`` renvoie True ; les erreurs d'accès sont ignorées.
    Sont « cachés » les noms commençant par un point (fichiers et dossiers).
    ``on_link(chemin)`` est appelé pour chaque lien symbolique écarté (sans ``follow_symlinks``) dont l'extension
    est demandée : le compteur « liens ignorés » d'un journal repose dessus (LOG-011).
    """
    wanted = None if extensions is None else {_norm_ext(e) for e in extensions}
    stack: list[Path] = [Path(root)]
    seen: set[str] = set()
    while stack:
        if cancel is not None and cancel():
            return
        folder = stack.pop()
        if follow_symlinks:                      # évite les boucles de liens
            try:
                real = os.path.realpath(folder)
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        subdirs: list[Path] = []
        for entry in entries:
            if cancel is not None and cancel():
                return
            name = entry.name
            if skip_hidden and name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if recursive and (follow_symlinks or not _is_junction(entry)):
                        subdirs.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=follow_symlinks):
                    if on_link is not None and entry.is_symlink() and (
                            wanted is None or Path(name).suffix.lower() in wanted):
                        on_link(Path(entry.path))
                    continue
            except OSError:
                continue
            if wanted is not None and Path(name).suffix.lower() not in wanted:
                continue
            yield Path(entry.path)
        stack.extend(reversed(subdirs))
