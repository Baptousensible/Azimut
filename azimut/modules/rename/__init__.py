"""Azimut Rename : inscrit la date de prise de vue dans le nom des photos, vidéos et fichiers audio."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="rename",
    name="Rename",
    tagline=QT_TRANSLATE_NOOP("Modules", "Inscrire la date de prise de vue dans le nom de vos fichiers"),
    icon="pen-alt",
    entry="azimut.modules.rename.window:RenameWindow",
    optional=("exiftool", "ffmpeg"),
    order=40,
)

__all__ = ["MODULE"]
