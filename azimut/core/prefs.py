"""Préférences utilisateur : un dictionnaire plat à clés pointées, enregistré en JSON."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from azimut.core.fsutil import atomic_write_text
from azimut.core.logs import get_logger
from azimut.core.paths import user_config_dir

_log = get_logger("prefs")

THEMES = ("dark", "light")
ZOOM_MIN, ZOOM_MAX = 0.8, 1.6
_STR_KEYS = ("binaries.ffmpeg", "binaries.ffprobe", "binaries.exiftool", "last_dir")
DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "zoom": 1.0,
    "binaries.ffmpeg": "",
    "binaries.ffprobe": "",
    "binaries.exiftool": "",
    "last_dir": "",
}


def _normalize(key: str, value: Any) -> Any:
    """Valide les clés standard à la lecture ; les autres clés sont renvoyées telles quelles."""
    if key == "theme":
        return value if value in THEMES else DEFAULTS["theme"]
    if key == "zoom":
        try:
            z = float(value)
        except (TypeError, ValueError):
            return DEFAULTS["zoom"]
        if z != z:                                   # NaN
            return DEFAULTS["zoom"]
        return min(ZOOM_MAX, max(ZOOM_MIN, z))
    if key in _STR_KEYS:
        return value if isinstance(value, str) else ""
    return value


class Preferences(QObject):
    """Préférences globales. Signal ``changed(clé, nouvelle valeur)``."""

    changed = Signal(str, object)

    _instance: "Preferences | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path) if path is not None else user_config_dir() / "preferences.json"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._load()

    @classmethod
    def instance(cls) -> "Preferences":
        """Singleton paresseux."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        """Lecture tolérante : absent, illisible ou mal formé → dictionnaire vide."""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        return dict(raw) if isinstance(raw, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return _normalize(key, self._data[key])
        return default if default is not None else DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        """Enregistre (écriture atomique) puis émet ``changed``."""
        with self._lock:
            snapshot = {**self._data, key: value}
            text = json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True)   # TypeError avant toute mutation
            self._data[key] = value
            try:
                atomic_write_text(self._path, text)
            except OSError as e:
                _log.warning("Préférences non enregistrées (%s) : %s", self._path, e)
            stored = _normalize(key, value)
        self.changed.emit(key, stored)

    def module(self, module_id: str) -> "ModulePrefs":
        return ModulePrefs(self, module_id)


class ModulePrefs:
    """Préférences d'un module : clés stockées sous « modules.<id>.<clé> »."""

    def __init__(self, prefs: Preferences, module_id: str) -> None:
        self._prefs = prefs
        self._id = module_id

    def _key(self, key: str) -> str:
        return f"modules.{self._id}.{key}"

    def get(self, key: str, default: Any = None) -> Any:
        return self._prefs.get(self._key(key), default)

    def set(self, key: str, value: Any) -> None:
        self._prefs.set(self._key(key), value)
