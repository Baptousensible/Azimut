"""Azimut Press — compression et optimisation d'images et de fichiers JSON."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="press",
    name="Azimut Press",
    tagline="Compresser et optimiser vos images et fichiers JSON",
    icon="📦",
    entry="azimut.modules.press.window:PressWindow",
    order=40,
)
