"""Réglages avancés d'Azimut Convert (logique pure, sans Qt) : CRF, preset, qualité, threads.

Un profil fournit des valeurs par défaut ; l'utilisateur peut les surcharger (``Overrides``) sans jamais
modifier les profils prédéfinis. ``apply_overrides`` renvoie un nouveau ``Profile`` validé et borné.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Collection, Mapping

from azimut.modules.convert.profiles import Profile

H264_PRESETS: tuple[str, ...] = ("ultrafast", "superfast", "veryfast", "faster", "fast",
                                 "medium", "slow", "slower", "veryslow")
SVTAV1_PRESETS: tuple[str, ...] = tuple(str(n) for n in range(0, 14))     # 0 (lent) … 13 (rapide)
LIBAOM_PRESETS: tuple[str, ...] = tuple(str(n) for n in range(0, 9))      # -cpu-used 0 … 8

CRF_RANGE = {"av1": (0, 63), "h264": (0, 51)}
QUALITY_RANGE = {"mjpeg": (2, 31), "webp": (0, 100)}        # mjpeg : 2 = meilleur ; webp : 100 = meilleur
MAX_THREADS = 64
MAX_PARALLEL = 16


def family(profile: Profile) -> str:
    """Famille d'encodeur vidéo du profil : av1 | h264 | mjpeg | webp | '' (audio ou inconnu)."""
    enc = profile.video_encoders
    if any(e in enc for e in ("libsvtav1", "libaom-av1")):
        return "av1"
    if "libx264" in enc:
        return "h264"
    if "mjpeg" in enc:
        return "mjpeg"
    if "libwebp" in enc:
        return "webp"
    return ""


def crf_range(profile: Profile) -> tuple[int, int] | None:
    return CRF_RANGE.get(family(profile)) if profile.crf is not None else None


def quality_range(profile: Profile) -> tuple[int, int] | None:
    return QUALITY_RANGE.get(family(profile)) if profile.quality is not None else None


def preset_choices(profile: Profile, encoders: Collection[str] | None = None) -> tuple[str, ...]:
    """Presets valides pour l'encodeur qui sera réellement utilisé (SVT-AV1 par défaut si inconnu)."""
    fam = family(profile)
    if fam == "h264":
        return H264_PRESETS
    if fam == "av1":
        if encoders is not None and "libsvtav1" not in encoders and "libaom-av1" in encoders:
            return LIBAOM_PRESETS
        return SVTAV1_PRESETS
    return ()


@dataclass(frozen=True)
class Overrides:
    """Valeurs choisies par l'utilisateur ; ``None`` = garder celle du profil."""

    crf: int | None = None
    preset: str | None = None
    quality: int | None = None

    def is_empty(self) -> bool:
        return self.crf is None and self.preset is None and self.quality is None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in (("crf", self.crf), ("preset", self.preset), ("quality", self.quality))
                if v is not None}

    @classmethod
    def from_dict(cls, data: Any) -> "Overrides":
        """Lecture tolérante d'un dictionnaire (préférences éventuellement corrompues)."""
        if not isinstance(data, Mapping):
            return cls()

        def as_int(v: Any) -> int | None:
            if isinstance(v, bool):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        preset = data.get("preset")
        return cls(crf=as_int(data.get("crf")), preset=str(preset) if preset not in (None, "") else None,
                   quality=as_int(data.get("quality")))


def clamp_threads(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_THREADS, n))


def clamp_parallel(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_PARALLEL, n))


def auto_threads(parallel: int, cpu_count: int | None = None) -> int:
    """Threads par encodage quand l'utilisateur laisse « auto » : on se partage les cœurs."""
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, cpus // max(1, parallel))


def default_video_threads(cpu_count: int | None = None) -> int:
    """Threads d'un encodage vidéo en « Auto » : bornés (2 à 6), car la RAM d'un encodeur AV1 croît avec eux."""
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    return max(2, min(6, cpus // 2))


def apply_overrides(profile: Profile, overrides: Overrides | None = None, threads: int = 0,
                    encoders: Collection[str] | None = None) -> Profile:
    """Profil effectif : valeurs du profil, surchargées et bornées. Un profil sans CRF/preset/qualité
    ignore le réglage correspondant. Le libellé indique « (personnalisé) » dès qu'une valeur diffère."""
    ov = overrides or Overrides()
    changes: dict[str, Any] = {}
    r = crf_range(profile)
    if r and ov.crf is not None and ov.crf != profile.crf:
        changes["crf"] = max(r[0], min(r[1], ov.crf))
    presets = preset_choices(profile, encoders)
    if presets and ov.preset is not None and ov.preset != profile.preset and ov.preset in presets:
        changes["preset"] = ov.preset
    q = quality_range(profile)
    if q and ov.quality is not None and ov.quality != profile.quality:
        changes["quality"] = max(q[0], min(q[1], ov.quality))
    n = clamp_threads(threads)
    if n and profile.kind in ("image", "video"):
        changes["threads"] = n
    if not changes:
        return profile
    custom = any(k in changes for k in ("crf", "preset", "quality"))
    if custom:
        changes["label"] = f"{profile.label} (personnalisé)"
    return replace(profile, **changes)


def summarize(profile: Profile, base: Profile) -> str:
    """Résumé des écarts par rapport au profil d'origine (vide si aucun)."""
    parts: list[str] = []
    if profile.crf != base.crf:
        parts.append(f"CRF {profile.crf}")
    if profile.preset != base.preset:
        parts.append(f"preset {profile.preset}")
    if profile.quality != base.quality:
        parts.append(f"qualité {profile.quality}")
    if profile.threads != base.threads:
        parts.append(f"{profile.threads} thread(s)")
    return ", ".join(parts)
