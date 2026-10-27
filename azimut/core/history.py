"""Historique des actions des modules, stocké via ``StorageBackend``."""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime

from azimut.core.logs import get_logger
from azimut.core.storage import StorageBackend, get_storage

_log = get_logger("history")

_COLLECTION = "history"
_KEY = "entries"


@dataclass
class HistoryEntry:
    module: str                  # id du module
    action: str                  # « Conversion », « Analyse »…
    summary: str                 # une phrase pour l'utilisateur
    ok: int = 0
    errors: int = 0
    timestamp: str = ""          # ISO 8601 local ; rempli par add() s'il est vide
    details: dict = field(default_factory=dict)


def _entry_from_dict(raw: object) -> HistoryEntry | None:
    """Reconstruit une entrée ; ``None`` si le document est inexploitable."""
    if not isinstance(raw, dict):
        return None
    try:
        details = raw.get("details")
        return HistoryEntry(
            module=str(raw["module"]),
            action=str(raw.get("action", "")),
            summary=str(raw.get("summary", "")),
            ok=int(raw.get("ok", 0)),
            errors=int(raw.get("errors", 0)),
            timestamp=str(raw.get("timestamp", "")),
            details=details if isinstance(details, dict) else {},
        )
    except (KeyError, TypeError, ValueError):
        return None


class HistoryStore:
    """Un unique document ``{"entries": [...]}`` dans la collection « history » (plus récent en dernier)."""

    def __init__(self, storage: StorageBackend | None = None, max_entries: int = 500) -> None:
        self._storage = storage
        self._max = max(1, int(max_entries))
        self._lock = threading.RLock()

    @property
    def storage(self) -> StorageBackend:
        """Backend explicite, sinon le singleton courant (suit donc ``set_storage``)."""
        return self._storage if self._storage is not None else get_storage()

    def _read_raw(self) -> list:
        doc = self.storage.load(_COLLECTION, _KEY)
        raw = doc.get("entries") if doc else None
        return list(raw) if isinstance(raw, list) else []

    def _write_raw(self, raw: list) -> None:
        self.storage.save(_COLLECTION, _KEY, {"entries": raw})

    def add(self, entry: HistoryEntry) -> None:
        """Ajoute une entrée (horodatée si besoin) et tronque aux ``max_entries`` plus récentes."""
        if not entry.timestamp:
            entry.timestamp = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            raw = self._read_raw()
            raw.append(asdict(entry))
            try:
                self._write_raw(raw[-self._max:])
            except OSError as e:
                _log.warning("Historique non enregistré : %s", e)

    def recent(self, limit: int = 100, module: str | None = None) -> list[HistoryEntry]:
        """Entrées les plus récentes d'abord, éventuellement filtrées par module."""
        if limit <= 0:
            return []
        with self._lock:
            raw = self._read_raw()
        result: list[HistoryEntry] = []
        for item in reversed(raw):
            entry = _entry_from_dict(item)
            if entry is None or (module is not None and entry.module != module):
                continue
            result.append(entry)
            if len(result) >= limit:
                break
        return result

    def clear(self, module: str | None = None) -> None:
        """Efface tout l'historique, ou seulement celui d'un module."""
        with self._lock:
            if module is None:
                kept: list = []
            else:
                kept = [r for r in self._read_raw()
                        if not (isinstance(r, dict) and r.get("module") == module)]
            try:
                self._write_raw(kept)
            except OSError as e:
                _log.warning("Historique non effacé : %s", e)
