"""Fichiers et dossiers ouverts récemment depuis Atlas (préférences utilisateur, sans réseau)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from azimut.core.prefs import ModulePrefs, Preferences

MAX_RECENTS = 12
KEY = "recent"


@dataclass(frozen=True)
class Recent:
    path: str
    module: str
    timestamp: float

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/\\")) or self.path


class RecentStore:
    """Liste des derniers chemins ouverts (plus récent d'abord, sans doublon de chemin)."""

    def __init__(self, prefs: ModulePrefs | None = None) -> None:
        self._prefs = prefs if prefs is not None else Preferences.instance().module("atlas")

    def items(self) -> list[Recent]:
        raw = self._prefs.get(KEY, [])
        out: list[Recent] = []
        if not isinstance(raw, list):
            return out
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str) and isinstance(entry.get("module"), str):
                try:
                    stamp = float(entry.get("ts", 0.0))
                except (TypeError, ValueError):
                    stamp = 0.0
                out.append(Recent(entry["path"], entry["module"], stamp))
        return out[:MAX_RECENTS]

    def add(self, paths: list[str], module: str) -> None:
        now = time.time()
        known = {r.path: r for r in self.items()}
        for path in paths:
            known.pop(str(path), None)
        fresh = [Recent(str(p), module, now) for p in paths]
        merged = [*fresh, *known.values()][:MAX_RECENTS]
        self._prefs.set(KEY, [{"path": r.path, "module": r.module, "ts": r.timestamp} for r in merged])

    def clear(self) -> None:
        self._prefs.set(KEY, [])


def ago(timestamp: float, now: float | None = None) -> str:
    """« à l'instant », « il y a 5 min », « il y a 3 h », « il y a 2 j »."""
    delta = max(0.0, (time.time() if now is None else now) - timestamp)
    if delta < 60:
        return "à l'instant"
    if delta < 3600:
        return f"il y a {int(delta // 60)} min"
    if delta < 86400:
        return f"il y a {int(delta // 3600)} h"
    return f"il y a {int(delta // 86400)} j"
