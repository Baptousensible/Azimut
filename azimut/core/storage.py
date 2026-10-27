"""Stockage abstrait de documents JSON (collection / clé) et implémentation locale."""
from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from azimut.core.fsutil import atomic_write_text
from azimut.core.logs import get_logger
from azimut.core.paths import user_data_dir

_log = get_logger("storage")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_NAME = 150


class StorageBackend(ABC):
    """Interface de stockage : toute future synchronisation Cloud s'y branchera."""

    @abstractmethod
    def load(self, collection: str, key: str) -> dict | None: ...

    @abstractmethod
    def save(self, collection: str, key: str, data: dict) -> None: ...

    @abstractmethod
    def delete(self, collection: str, key: str) -> None: ...

    @abstractmethod
    def list_keys(self, collection: str) -> list[str]: ...


def _sanitize(name: str) -> str:
    """Nom sûr : seuls ``[A-Za-z0-9._-]`` sont conservés, jamais de « . » / « .. » seuls."""
    safe = _UNSAFE.sub("_", str(name))[:_MAX_NAME]
    if not safe.strip("."):                       # vide, « . », « .. », « ... »
        safe = safe.replace(".", "_") or "_"
    return safe


class LocalJsonStorage(StorageBackend):
    """``<root>/<collection>/<key>.json`` : écriture atomique, lecture tolérante."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else user_data_dir() / "storage"
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root

    def _dir(self, collection: str) -> Path:
        return self._root / _sanitize(collection)

    def _file(self, collection: str, key: str) -> Path:
        return self._dir(collection) / (_sanitize(key) + ".json")

    def load(self, collection: str, key: str) -> dict | None:
        """Document ou ``None`` (absent, illisible, corrompu ou qui n'est pas un objet JSON)."""
        path = self._file(collection, key)
        with self._lock:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return None
        return data if isinstance(data, dict) else None

    def save(self, collection: str, key: str, data: dict) -> None:
        if not isinstance(data, dict):
            raise TypeError("Le document à enregistrer doit être un dictionnaire.")
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        with self._lock:
            atomic_write_text(self._file(collection, key), text)

    def delete(self, collection: str, key: str) -> None:
        """Supprime le document ; sans effet s'il n'existe pas."""
        path = self._file(collection, key)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                _log.warning("Suppression impossible (%s) : %s", path, e)

    def list_keys(self, collection: str) -> list[str]:
        """Clés (assainies) de la collection, triées ; liste vide si la collection n'existe pas."""
        folder = self._dir(collection)
        with self._lock:
            try:
                return sorted(p.stem for p in folder.glob("*.json") if p.is_file())
            except OSError:
                return []


_storage: StorageBackend | None = None
_storage_lock = threading.Lock()


def get_storage() -> StorageBackend:
    """Singleton paresseux (``LocalJsonStorage`` par défaut)."""
    global _storage
    with _storage_lock:
        if _storage is None:
            _storage = LocalJsonStorage()
        return _storage


def set_storage(backend: StorageBackend) -> None:
    """Remplace le singleton (tests, futur Cloud)."""
    global _storage
    with _storage_lock:
        _storage = backend
