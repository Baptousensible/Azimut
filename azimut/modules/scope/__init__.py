"""Azimut Scope : inspecter des dossiers (pour l'instant : comparer deux arborescences, en lecture seule)."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="scope",
    name="Azimut Scope",
    tagline="Comparer deux dossiers : même arborescence, même nombre de fichiers ?",
    icon="⚖️",
    entry="azimut.modules.scope.window:ScopeWindow",
    order=60,
)

__all__ = ["MODULE"]
