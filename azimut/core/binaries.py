"""Localisation de FFmpeg, FFprobe et ExifTool : embarqué, chemin personnalisé, puis PATH système."""
from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from azimut.core.logs import get_logger
from azimut.core.paths import app_root
from azimut.core.prefs import Preferences

_log = get_logger("binaries")

KNOWN_BINARIES: tuple[str, ...] = ("ffmpeg", "ffprobe", "exiftool")

_SOURCE_LABELS = {"bundled": "embarqué", "custom": "personnalisé", "system": "système"}
_VERSION_TIMEOUT = 10.0


class BinaryNotFoundError(RuntimeError):
    """Un binaire obligatoire est introuvable."""


@dataclass(frozen=True)
class BinaryInfo:
    name: str
    path: Path | None
    source: str                 # "bundled" | "custom" | "system" | "missing"
    version: str                # « 7.1 », « 12.76 »… ; "" si inconnue

    @property
    def available(self) -> bool:
        return self.path is not None


_cache: dict[str, BinaryInfo] = {}
_cache_lock = threading.RLock()


def platform_tag() -> str:
    """« windows-x64 », « linux-x64 », « macos-arm64 » ou « macos-x64 »."""
    if sys.platform == "win32":
        return "windows-x64"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
    return "linux-x64"


def bundled_dir() -> Path:
    """Dossier des binaires embarqués : ``<app_root>/bin/<platform_tag>``."""
    return app_root() / "bin" / platform_tag()


def _exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    return sys.platform == "win32" or os.access(path, os.X_OK)


def _bundled_candidate(name: str) -> Path | None:
    path = bundled_dir() / _exe_name(name)
    if not path.is_file():
        return None
    if _is_executable(path):
        return path
    try:                                           # fichier extrait sans bit d'exécution
        os.chmod(path, path.stat().st_mode | stat.S_IXUSR)
    except OSError:
        pass
    if _is_executable(path):
        return path
    _log.warning("Le binaire embarqué « %s » n'est pas exécutable : ignoré (%s).", name, path)
    return None


def _custom_candidate(name: str, prefs: Preferences) -> Path | None:
    value = prefs.get(f"binaries.{name}", "")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if _is_executable(path):
        return path
    _log.warning("Chemin personnalisé invalide pour « %s » (absent ou non exécutable) : %s", name, path)
    return None


def _locate(name: str, prefs: Preferences) -> tuple[Path | None, str]:
    path = _bundled_candidate(name)
    if path is not None:
        return path, "bundled"
    path = _custom_candidate(name, prefs)
    if path is not None:
        return path, "custom"
    found = shutil.which(name)
    if found:
        return Path(found), "system"
    return None, "missing"


def probe_version(name: str, path: Path) -> str:
    """Version du binaire (``-version`` pour FFmpeg/FFprobe, ``-ver`` pour ExifTool) ; "" en cas d'échec."""
    if name in ("ffmpeg", "ffprobe"):
        args = [str(path), "-version"]
    elif name == "exiftool":
        args = [str(path), "-ver"]
    else:
        return ""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=_VERSION_TIMEOUT, **kwargs)
        if result.returncode != 0:
            return ""
        lines = (result.stdout or "").strip().splitlines()
        first = lines[0].strip() if lines else ""
        if name == "exiftool":
            return first
        match = re.search(r"version\s+(\S+)", first, re.IGNORECASE)
        return match.group(1) if match else ""
    except Exception:                              # délai, binaire illisible, etc.
        return ""


def resolve(name: str, prefs: Preferences | None = None) -> BinaryInfo:
    """Localise un binaire (résultat mis en cache jusqu'au prochain ``refresh()``).

    Ordre : 1) embarqué ; 2) chemin personnalisé (préférence « binaries.<name> ») ; 3) PATH système.
    """
    with _cache_lock:
        cached = _cache.get(name)
    if cached is not None:
        return cached
    if prefs is None:
        prefs = Preferences.instance()
    path, source = _locate(name, prefs)
    version = probe_version(name, path) if path is not None else ""
    info = BinaryInfo(name=name, path=path, source=source, version=version)
    with _cache_lock:
        return _cache.setdefault(name, info)


def require(name: str, prefs: Preferences | None = None) -> Path:
    """Chemin du binaire, ou ``BinaryNotFoundError`` (message en français)."""
    info = resolve(name, prefs)
    if info.path is None:
        raise BinaryNotFoundError(
            f"« {name} » est introuvable. Installez-le, placez-le dans « bin/{platform_tag()} » "
            "ou indiquez son chemin dans les préférences.")
    return info.path


def refresh(prefs: Preferences | None = None) -> dict[str, BinaryInfo]:
    """Vide le cache, re-détecte les binaires connus et journalise le résultat."""
    with _cache_lock:
        _cache.clear()
    found: dict[str, BinaryInfo] = {}
    for name in KNOWN_BINARIES:
        info = resolve(name, prefs)
        found[name] = info
        if info.path is None:
            _log.warning("%s introuvable (ni embarqué, ni dans les préférences, ni dans le PATH)", name)
        else:
            label = f"{name} {info.version}".strip() if info.version else f"{name} (version inconnue)"
            _log.info("%s — %s : %s", label, _SOURCE_LABELS.get(info.source, info.source), info.path)
    return found
