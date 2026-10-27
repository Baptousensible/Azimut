"""Utilitaires fichiers : formats d'affichage, écriture atomique, parcours."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Iterator

_UNITS = ("o", "Ko", "Mo", "Go", "To")


def format_size(n: float) -> str:
    """Taille lisible : « 1,5 Mo » (base 1024, virgule décimale)."""
    value = max(0.0, float(n))
    i = 0
    while value >= 1024 and i < len(_UNITS) - 1:
        value /= 1024
        i += 1
    if i == 0:
        return f"{int(value)} o"
    text = f"{value:.1f}"
    if float(text) >= 1024 and i < len(_UNITS) - 1:      # 1023,96 Ko → « 1,0 Mo »
        value /= 1024
        i += 1
        text = f"{value:.1f}"
    return f"{text.replace('.', ',')} {_UNITS[i]}"


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
        except OSError:
            pass
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


def iter_files(root: Path, *, recursive: bool = True, extensions: Iterable[str] | None = None,
               skip_hidden: bool = True, follow_symlinks: bool = False,
               cancel: Callable[[], bool] | None = None) -> Iterator[Path]:
    """Parcourt ``root`` et produit les fichiers (ordre alphabétique par dossier).

    ``extensions`` : minuscules avec le point (« .jpg ») ; ``None`` = tous.
    S'arrête dès que ``cancel()`` renvoie True ; les erreurs d'accès sont ignorées.
    Sont « cachés » les noms commençant par un point (fichiers et dossiers).
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
                    if recursive:
                        subdirs.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=follow_symlinks):
                    continue
            except OSError:
                continue
            if wanted is not None and Path(name).suffix.lower() not in wanted:
                continue
            yield Path(entry.path)
        stack.extend(reversed(subdirs))
