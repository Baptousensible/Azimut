"""Azimut Convert : conversion audio, vidéo et images en lots, avec profils prédéfinis."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE = ModuleSpec(
    id="convert",
    name="Azimut Convert",
    tagline="Convertir audio, vidéo et images en lots, avec profils prédéfinis",
    icon="🎞️",
    entry="azimut.modules.convert.window:ConvertWindow",
    requires=("ffmpeg", "ffprobe"),
    order=10,
)
