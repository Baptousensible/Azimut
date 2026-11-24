"""Réglages de qualité d'Azimut Convert (logique pure, sans Qt) : CRF, preset, qualité, débit audio, threads.

Un profil (un par codec) fournit les valeurs de départ ; l'utilisateur les surcharge (``Overrides``) avec les
curseurs de l'arborescence « Format et qualité » sans jamais modifier les profils eux-mêmes.
``apply_overrides`` renvoie un nouveau ``Profile`` validé et borné.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Collection, Mapping

from azimut.modules.convert.profiles import LEGACY_PROFILES, Profile

H264_PRESETS: tuple[str, ...] = ("ultrafast", "superfast", "veryfast", "faster", "fast",
                                 "medium", "slow", "slower", "veryslow")
SVTAV1_PRESETS: tuple[str, ...] = tuple(str(n) for n in range(0, 14))     # 0 (lent) … 13 (rapide)
LIBAOM_PRESETS: tuple[str, ...] = tuple(str(n) for n in range(0, 9))      # -cpu-used 0 … 8
VP9_PRESETS: tuple[str, ...] = tuple(str(n) for n in range(0, 9))         # -cpu-used 0 (lent) … 8 (rapide)
PNG_LEVELS: tuple[str, ...] = tuple(str(n) for n in range(0, 10))         # -compression_level 0 … 9
JXL_EFFORT: tuple[str, ...] = tuple(str(n) for n in range(1, 10))         # -effort 1 (rapide) … 9 (lent)
TIFF_COMPRESSION: tuple[str, ...] = ("packbits", "lzw", "deflate")
PRORES_PROFILES: tuple[str, ...] = ("proxy", "lt", "standard", "hq", "4444")
NVENC_PRESETS: tuple[str, ...] = tuple(f"p{n}" for n in range(1, 8))
QSV_PRESETS: tuple[str, ...] = ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")

FLAC_LEVELS: tuple[str, ...] = tuple(str(n) for n in range(0, 13))        # -compression_level : 0 (rapide) … 12

CRF_RANGE = {"av1": (0, 63), "h264": (0, 51), "h265": (0, 51), "vp9": (0, 63)}
# quality : plus la valeur est haute, meilleure est la qualité, sauf mjpeg / jxl / mpeg4 / mpeg2 (inversés)
QUALITY_RANGE = {"mjpeg": (2, 31), "webp": (0, 100), "jxl": (0, 15), "mpeg4": (1, 31), "mpeg2": (1, 31),
                 "theora": (0, 10), "heic": (0, 100)}
REVERSED_QUALITY = frozenset({"mjpeg", "jxl", "mpeg4", "mpeg2"})
REVERSED_PRESETS = frozenset({"av1", "vp9"})               # 0 = lent : le curseur va de droite à gauche
BITRATE_RANGE = {"mp3": (32, 320), "aac": (32, 320), "opus": (16, 512), "vorbis": (32, 512),
                 "ac3": (64, 512), "mp2": (32, 384)}                      # kbit/s
BITRATE_STEPS: tuple[int, ...] = (16, 24, 32, 48, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512)
MAX_THREADS = 64
MAX_PARALLEL = 16
MAX_PARALLEL_VIDEO = 4              # une vidéo utilise déjà tous les cœurs et beaucoup de RAM
PARALLEL_KINDS: tuple[str, ...] = ("copy", "image", "audio", "video")
VIDEO_AUDIO_PREFIX = "video_audio:"       # réglages du codec audio choisi pour la vidéo, distincts de la famille Audio


def video_audio_key(profile_id: str) -> str:
    """Clé de préférences du codec audio utilisé dans une vidéo (indépendante du même codec en audio seul)."""
    return VIDEO_AUDIO_PREFIX + profile_id


_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("libsvtav1", "libaom-av1", "av1_nvenc", "av1_qsv", "av1_vaapi"), "av1"),
    (("libx264", "h264_nvenc", "h264_qsv", "h264_vaapi"), "h264"),
    (("libx265", "hevc_nvenc", "hevc_qsv", "hevc_vaapi"), "h265"),
    (("libvpx-vp9",), "vp9"),
    (("mpeg4",), "mpeg4"),
    (("mpeg2video",), "mpeg2"),
    (("libtheora",), "theora"),
    (("prores_ks",), "prores"),
    (("mjpeg",), "mjpeg"),
    (("libwebp",), "webp"),
    (("png",), "png"),
    (("libjxl",), "jxl"),
    (("libopenjpeg",), "jp2"),
    (("tiff",), "tiff"),
    (("bmp",), "bmp"),
    (("qoi",), "qoi"),
)


def hardware_kind(profile: Profile) -> str:
    """Backend matériel du profil : nvenc | qsv | vaapi | '' (logiciel ou audio)."""
    for encoder in profile.video_encoders:
        for suffix in ("nvenc", "qsv", "vaapi"):
            if encoder.endswith("_" + suffix):
                return suffix
    return ""


def family(profile: Profile) -> str:
    """Famille de l'encodeur image/vidéo du profil (av1, h264, h265, vp9, png, jxl…), ou '' (audio)."""
    if profile.id == "image_heic":
        return "heic"
    enc = profile.video_encoders
    for names, fam in _FAMILIES:
        if any(e in enc for e in names):
            return fam
    return ""


def audio_family(profile: Profile) -> str:
    """Famille de l'encodeur audio : mp3 | aac | opus | flac | vorbis | alac | pcm | ac3 | mp2 | ''."""
    enc = profile.audio_encoders
    for name, fam in (("libmp3lame", "mp3"), ("aac", "aac"), ("libopus", "opus"), ("flac", "flac"),
                      ("libvorbis", "vorbis"), ("alac", "alac"),
                      ("pcm_s16le", "pcm"), ("pcm_s16be", "pcm"), ("pcm_s24le", "pcm"),
                      ("ac3", "ac3"), ("mp2", "mp2")):
        if name in enc:
            return fam
    return ""


def bitrate_kbps(profile: Profile) -> int | None:
    """Débit audio du profil en kbit/s (« 128k » → 128) ; None s'il n'y en a pas."""
    text = profile.audio_bitrate
    return int(text[:-1]) if text.endswith("k") and text[:-1].isdigit() else None


def bitrate_range(profile: Profile) -> tuple[int, int] | None:
    """Plage du curseur de débit audio ; None pour FLAC (sans perte) et les profils sans débit."""
    if bitrate_kbps(profile) is None:
        return None
    return BITRATE_RANGE.get(audio_family(profile))


def crf_range(profile: Profile) -> tuple[int, int] | None:
    return CRF_RANGE.get(family(profile)) if profile.crf is not None else None


def quality_range(profile: Profile) -> tuple[int, int] | None:
    return QUALITY_RANGE.get(family(profile)) if profile.quality is not None else None


def preset_choices(profile: Profile, encoders: Collection[str] | None = None) -> tuple[str, ...]:
    """Presets valides pour l'encodeur qui sera réellement utilisé (SVT-AV1 par défaut si inconnu)."""
    if profile.kind == "audio":                         # FLAC : le « preset » est le niveau de compression
        return FLAC_LEVELS if audio_family(profile) == "flac" else ()
    hw = hardware_kind(profile)
    if hw == "nvenc":
        return NVENC_PRESETS
    if hw == "qsv":
        return QSV_PRESETS
    if hw == "vaapi":
        return ()
    fam = family(profile)
    if fam in ("h264", "h265"):
        return H264_PRESETS
    if fam == "av1":
        if encoders is not None and "libsvtav1" not in encoders and "libaom-av1" in encoders:
            return LIBAOM_PRESETS
        return SVTAV1_PRESETS
    if fam == "vp9":
        return VP9_PRESETS
    if fam == "png":
        return PNG_LEVELS
    if fam == "jxl":
        return JXL_EFFORT
    if fam == "tiff":
        return TIFF_COMPRESSION
    if fam == "prores":
        return PRORES_PROFILES
    return ()


@dataclass(frozen=True)
class Overrides:
    """Valeurs choisies par l'utilisateur ; ``None`` = garder celle du profil."""

    crf: int | None = None
    preset: str | None = None
    quality: int | None = None
    bitrate: int | None = None                  # débit audio, en kbit/s

    def is_empty(self) -> bool:
        return all(v is None for v in (self.crf, self.preset, self.quality, self.bitrate))

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in (("crf", self.crf), ("preset", self.preset), ("quality", self.quality),
                                  ("bitrate", self.bitrate)) if v is not None}

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
                   quality=as_int(data.get("quality")), bitrate=as_int(data.get("bitrate")))


def clamp_threads(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_THREADS, n))


def max_parallel(kind: str) -> int:
    """Nombre maximal de fichiers d'une famille traités en même temps."""
    return MAX_PARALLEL_VIDEO if kind == "video" else MAX_PARALLEL


def clamp_parallel(value: Any, kind: str = "image") -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(max_parallel(kind), n))


def auto_parallel(kind: str, threads: int = 0, cpu_count: int | None = None,
                  hardware_video: bool = False) -> int:
    """Fichiers d'une famille traités en même temps en mode automatique.

    Les familles sont traitées l'une après l'autre : chacune peut donc utiliser tout le budget de cœurs.
    ``threads`` est la valeur du réglage « Threads » (0 = Auto) ; elle sert à ne pas lancer plus d'encodages
    que la machine ne peut en nourrir. Un encodeur vidéo logiciel est déjà multi-thread et sa mémoire croît
    avec les threads : une seule vidéo à la fois. Avec un encodeur matériel (``hardware_video``), le GPU
    encode et le processeur ne fait que décoder et filtrer : deux vidéos peuvent avancer ensemble.
    """
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    if kind == "copy":
        return max(2, min(4, cpus // 2))                 # entrées/sorties : peu de processeur
    if kind == "audio":
        return max(1, min(4, cpus // 2))                 # encodeurs audio presque mono-thread
    if kind == "video":
        return 2 if hardware_video else 1
    per_file = threads if threads > 0 else max(2, min(4, cpus // 4))
    return max(1, min(max_parallel("image"), cpus // max(1, per_file)))


def parallel_counts(mode: str, values: Mapping[str, Any] | None = None, threads: int = 0,
                    cpu_count: int | None = None, hardware_video: bool = False) -> dict[str, int]:
    """Parallélisme effectif par famille : « auto » suit les cœurs, le réglage Threads et l'encodeur vidéo.

    En manuel, ``hardware_video`` n'intervient pas : les valeurs choisies par l'utilisateur sont respectées.
    """
    if mode != "manual":
        t = clamp_threads(threads)
        return {kind: auto_parallel(kind, t, cpu_count, hardware_video) for kind in PARALLEL_KINDS}
    data = values or {}
    return {kind: clamp_parallel(data.get(kind, 1), kind) for kind in PARALLEL_KINDS}


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
    """Profil effectif : valeurs du profil, surchargées et bornées. Un profil sans CRF/preset/qualité/débit
    ignore le réglage correspondant (``describe`` résume les valeurs retenues)."""
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
    b = bitrate_range(profile)
    if b and ov.bitrate is not None and ov.bitrate != bitrate_kbps(profile):
        changes["audio_bitrate"] = f"{max(b[0], min(b[1], ov.bitrate))}k"
    n = clamp_threads(threads)
    if n and profile.kind in ("image", "video"):
        changes["threads"] = n
    return replace(profile, **changes) if changes else profile


def summarize(profile: Profile, base: Profile) -> str:
    """Résumé des écarts par rapport aux valeurs de départ du profil (vide si aucun)."""
    parts: list[str] = []
    if profile.crf != base.crf:
        parts.append(f"CRF {profile.crf}")
    if profile.preset != base.preset:
        parts.append(f"{_preset_word(profile)} {profile.preset}")
    if profile.quality != base.quality:
        parts.append(f"{quality_word(profile)} {profile.quality}")
    if profile.audio_bitrate != base.audio_bitrate:
        parts.append(f"{bitrate_kbps(profile)} kbit/s")
    if profile.threads != base.threads:
        parts.append(f"{profile.threads} thread(s)")
    return ", ".join(parts)


def preset_word(profile: Profile) -> str:
    """Nom du réglage « preset » dans cette famille : preset, niveau, effort, compression ou profil."""
    if profile.kind == "audio":
        return "niveau"
    return {"png": "niveau", "jxl": "effort", "tiff": "compression", "prores": "profil"}.get(
        family(profile), "preset")


def _preset_word(profile: Profile) -> str:
    return preset_word(profile)


def quality_word(profile: Profile) -> str:
    """Nom du réglage de qualité : « qualité » ou « distance » (JPEG XL)."""
    return "distance" if family(profile) == "jxl" else "qualité"


def describe(profile: Profile) -> str:
    """Réglages effectifs d'un profil, pour la file d'attente : « CRF 39 · preset 4 », « 192 kbit/s »…"""
    parts: list[str] = []
    if profile.crf is not None:
        parts.append(f"CRF {profile.crf}")
    if profile.preset:
        parts.append(f"{_preset_word(profile)} {profile.preset}")
    if profile.quality is not None:
        parts.append(f"{quality_word(profile)} {profile.quality}")
    if bitrate_range(profile) is not None:
        parts.append(f"{bitrate_kbps(profile)} kbit/s")
    return " · ".join(parts)


def overrides_diff(base: Profile, overrides: Overrides) -> dict[str, Any]:
    """Seules les valeurs qui diffèrent de celles du profil sont conservées (ce qui est enregistré)."""
    start = {"crf": base.crf, "preset": base.preset, "quality": base.quality, "bitrate": bitrate_kbps(base)}
    return {k: v for k, v in overrides.to_dict().items() if start.get(k) != v}


def migrate_legacy(selected: Mapping[str, Any], store: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Convertit les préférences enregistrées avec les anciens profils fusionnés (AVIF « HQ », WebM « max »…).

    ``selected`` : famille → identifiant du profil choisi ; ``store`` : identifiant → réglages enregistrés.
    Renvoie (``selected`` et ``store`` à jour). Seul le profil réellement choisi dans chaque famille est
    reporté, avec les valeurs qu'il impliquait (ex. « AVIF haute qualité » → AVIF, CRF 25), puis ses
    réglages personnels par-dessus. Les réglages des autres anciens profils sont abandonnés.
    """
    new_sel = {k: str(v) for k, v in selected.items()}
    new_store = {k: dict(v) for k, v in store.items() if k not in LEGACY_PROFILES and isinstance(v, Mapping)}
    for kind, pid in selected.items():
        if pid not in LEGACY_PROFILES:
            continue
        target, implied = LEGACY_PROFILES[pid]
        new_sel[kind] = target
        old = store.get(pid)
        merged = {**implied, **(dict(old) if isinstance(old, Mapping) else {})}
        if merged:
            new_store[target] = {**new_store.get(target, {}), **merged}
    return new_sel, new_store
