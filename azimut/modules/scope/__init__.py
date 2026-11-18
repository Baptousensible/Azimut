"""Azimut Scope : inspecter des dossiers (pour l'instant : comparer deux arborescences, en lecture seule)."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="scope",
    name="Scope",
    # Espace insécable avant « ? » : évite un point d'interrogation orphelin en fin de ligne.
    tagline=QT_TRANSLATE_NOOP("Modules", "Comparer deux dossiers : même arborescence, même nombre de fichiers\u00a0? · Lecture seule"),
    icon="search",
    entry="azimut.modules.scope.window:ScopeWindow",
    order=50,
)

__all__ = ["MODULE"]
