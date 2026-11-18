"""Azimut Takeout : rétablit dates et positions GPS d'un export Google Photos (JSON compagnons → EXIF/QuickTime)."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="takeout",
    name="Takeout",
    tagline=QT_TRANSLATE_NOOP("Modules", "Réinjecter dates et positions GPS d'un export Google Photos dans vos fichiers"),
    icon="map-pin",
    entry="azimut.modules.takeout.window:TakeoutWindow",
    requires=("exiftool",),
    order=10,
)

__all__ = ["MODULE"]
