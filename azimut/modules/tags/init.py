"""Azimut Tags : lecture, modification et suppression des métadonnées EXIF, IPTC et XMP."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="tags",
    name="Azimut Tags",
    tagline="Lire, modifier et supprimer les métadonnées EXIF, IPTC et XMP",
    icon="🏷️",
    entry="azimut.modules.tags.window:TagsWindow",
    requires=("exiftool",),
    order=20,
)

__all__ = ["MODULE"]
