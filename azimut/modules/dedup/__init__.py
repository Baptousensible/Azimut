"""Azimut Dedup : doublons exacts, images similaires, Live Photos, suppression sûre et réversible."""
from __future__ import annotations

from azimut.core.module_spec import ModuleSpec

MODULE: ModuleSpec = ModuleSpec(
    id="dedup",
    name="Azimut Dedup",
    tagline="Trouver et écarter en sécurité les doublons de vos dossiers",
    icon="🧹",
    entry="azimut.modules.dedup.window:DedupWindow",
    optional=("ffmpeg", "ffprobe"),
    order=30,
)
