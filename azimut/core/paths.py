"""Chemins de l'application et répertoires utilisateur (stdlib uniquement)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR_WIN_MAC = "Azimut"
_APP_DIR_LINUX = "azimut"


def is_frozen() -> bool:
    """True si exécuté depuis un exécutable figé (PyInstaller, Nuitka)."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def app_root() -> Path:
    """Racine de l'application : dépôt en développement, dossier d'extraction/exécutable en mode figé."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Chemin d'une ressource embarquée (``azimut/resources/<parts>``)."""
    if is_frozen():
        base = app_root() / "azimut" / "resources"
    else:
        base = Path(__file__).resolve().parents[1] / "resources"
    return base.joinpath(*parts)


def _xdg(var: str, fallback: Path) -> Path:
    """Valeur d'une variable XDG si elle est absolue, sinon le repli."""
    value = os.environ.get(var, "")
    if value and os.path.isabs(value):
        return Path(value)
    return fallback


def _base_dir(kind: str) -> Path:
    """Dossier de l'application, non créé. ``kind`` : « data » ou « config »."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / _APP_DIR_WIN_MAC
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_DIR_WIN_MAC
    if kind == "config":
        return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / _APP_DIR_LINUX
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share") / _APP_DIR_LINUX


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_data_dir() -> Path:
    """Données utilisateur (créé si absent)."""
    return _ensure(_base_dir("data"))


def user_config_dir() -> Path:
    """Configuration utilisateur (créé si absent)."""
    return _ensure(_base_dir("config"))


def user_log_dir() -> Path:
    """Journaux : ``<user_data_dir>/logs``."""
    return _ensure(user_data_dir() / "logs")


def user_cache_dir() -> Path:
    """Cache : ``<user_data_dir>/cache``."""
    return _ensure(user_data_dir() / "cache")
