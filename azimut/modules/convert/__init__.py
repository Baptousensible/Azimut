"""Azimut Convert : conversion audio, vidéo et images en lots, avec format et qualité réglables."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="convert",
    name="Convert",
    tagline=QT_TRANSLATE_NOOP("Modules", "Convertir audio, vidéo et images en lots, format et qualité réglables"),
    icon="gallery",
    entry="azimut.modules.convert.window:ConvertWindow",
    requires=("ffmpeg", "ffprobe"),
    order=30,
)
