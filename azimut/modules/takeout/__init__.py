"""Azimut Takeout : rétablit dates et positions GPS d'un export Google Photos (JSON compagnons → EXIF/QuickTime)."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="takeout",
    name="Azimut Takeout",
    tagline="Réinjecter dates et positions GPS d'un export Google Photos dans vos fichiers",
    icon="📦",
    entry="azimut.modules.takeout.window:TakeoutWindow",
    requires=("exiftool",),
    order=25,
)

__all__ = ["MODULE"]
