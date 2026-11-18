"""Azimut Tags : lecture, modification et suppression des métadonnées EXIF, IPTC et XMP."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="tags",
    name="Tags",
    tagline=QT_TRANSLATE_NOOP("Modules", "Lire, modifier et supprimer les métadonnées EXIF, IPTC et XMP"),
    icon="bookmark",
    entry="azimut.modules.tags.window:TagsWindow",
    requires=("exiftool",),
    order=60,
)

__all__ = ["MODULE"]
