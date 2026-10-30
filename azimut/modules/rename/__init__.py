"""Azimut Rename : inscrit la date de prise de vue dans le nom des photos, vidéos et fichiers audio."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="rename",
    name="Azimut Rename",
    tagline="Inscrire la date de prise de vue dans le nom de vos fichiers",
    icon="🏷️",
    entry="azimut.modules.rename.window:RenameWindow",
    optional=("exiftool", "ffmpeg"),
    order=50,
)

__all__ = ["MODULE"]
