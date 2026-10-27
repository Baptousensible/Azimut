"""Profils de conversion prédéfinis (données pures, sans Qt)."""
from __future__ import annotations

from dataclasses import dataclass

# Extensions d'entrée par famille (minuscules, avec le point)
IMAGE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".heic", ".heif",
})
# RAW : ffmpeg ne sait pas les décoder → état « Non pris en charge »
RAW_EXTS: frozenset[str] = frozenset({
    ".nef", ".cr2", ".cr3", ".dng", ".arw", ".orf", ".raf", ".rw2",
    ".pef", ".srw", ".x3f", ".3fr", ".mef", ".nrw", ".rwl", ".iiq",
})
VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mxf", ".ts", ".mts", ".m2ts",
    ".vob", ".mpg", ".mpeg", ".m2v", ".3gp", ".3g2", ".f4v", ".asf", ".ogv", ".webm",
})
AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".amr", ".aiff", ".aif", ".mka",
})
ALL_EXTS: frozenset[str] = IMAGE_EXTS | RAW_EXTS | VIDEO_EXTS | AUDIO_EXTS

KINDS: tuple[str, ...] = ("image", "video", "audio")
KIND_LABELS: dict[str, str] = {"image": "Image", "video": "Vidéo", "audio": "Audio"}

AV1_ENCODERS = ("libsvtav1", "libaom-av1")        # par ordre de préférence
OPUS_FILTER = "aformat=channel_layouts=7.1|5.1|quad|stereo|mono"   # évite les disposition 5.1(side) refusées


@dataclass(frozen=True)
class Profile:
    """Un profil de conversion. Les encodeurs sont des candidats, du préféré au repli."""
    id: str
    label: str
    kind: str                                   # image | video | audio
    out_ext: str                                # avec le point
    description: str
    in_exts: frozenset[str]
    video_encoders: tuple[str, ...] = ()
    audio_encoders: tuple[str, ...] = ()
    crf: int | None = None
    preset: str = ""
    quality: int | None = None                  # -q:v (JPEG) ou -quality (WebP)
    audio_bitrate: str = ""
    audio_filter: str = ""
    even_dims: bool = False                     # dimensions paires (AV1 / H.264)
    extra_out: tuple[str, ...] = ()
    guard_growth: bool = False                  # anti-grossissement (image et vidéo)


PROFILES: tuple[Profile, ...] = (
    # ── Images ──
    Profile("image_avif_small", "AVIF — gain de place", "image", ".avif",
            "AVIF très compact (CRF 39) : gros gain de place, qualité visuelle correcte.",
            IMAGE_EXTS, video_encoders=AV1_ENCODERS, crf=39, preset="4", even_dims=True, guard_growth=True),
    Profile("image_avif_hq", "AVIF — haute qualité", "image", ".avif",
            "AVIF de haute qualité (CRF 25) : fichiers plus gros, détails préservés.",
            IMAGE_EXTS, video_encoders=AV1_ENCODERS, crf=25, preset="4", even_dims=True, guard_growth=True),
    Profile("image_jpeg", "JPEG — compatible", "image", ".jpg",
            "JPEG lisible partout (qualité -q:v 3).",
            IMAGE_EXTS, video_encoders=("mjpeg",), quality=3, guard_growth=True),
    Profile("image_webp", "WebP — équilibré", "image", ".webp",
            "WebP de qualité 80 : bon compromis taille / qualité.",
            IMAGE_EXTS, video_encoders=("libwebp",), quality=80, guard_growth=True),
    # ── Vidéos ──
    Profile("video_webm_av1", "WebM AV1 + Opus — gain de place", "video", ".webm",
            "Vidéo AV1 (CRF 40, preset 4) et audio Opus 128 kbit/s : très compact, encodage lent.",
            VIDEO_EXTS, video_encoders=AV1_ENCODERS, audio_encoders=("libopus",), crf=40, preset="4",
            audio_bitrate="128k", audio_filter=OPUS_FILTER, even_dims=True, guard_growth=True),
    Profile("video_mp4_h264", "MP4 H.264 + AAC — compatible", "video", ".mp4",
            "H.264 (CRF 23, preset medium) et AAC 160 kbit/s : lisible partout.",
            VIDEO_EXTS, video_encoders=("libx264",), audio_encoders=("aac",), crf=23, preset="medium",
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True),
    # ── Audio ──
    Profile("audio_mp3", "MP3 192 kbit/s", "audio", ".mp3",
            "MP3 compatible (192 kbit/s).", AUDIO_EXTS, audio_encoders=("libmp3lame",), audio_bitrate="192k"),
    Profile("audio_aac", "AAC / M4A 160 kbit/s", "audio", ".m4a",
            "AAC dans un conteneur M4A (160 kbit/s).", AUDIO_EXTS, audio_encoders=("aac",), audio_bitrate="160k"),
    Profile("audio_opus", "Opus 128 kbit/s", "audio", ".opus",
            "Opus : excellent rapport qualité / taille (128 kbit/s).",
            AUDIO_EXTS, audio_encoders=("libopus",), audio_bitrate="128k", audio_filter=OPUS_FILTER),
    Profile("audio_flac", "FLAC sans perte", "audio", ".flac",
            "FLAC : compression sans perte.", AUDIO_EXTS, audio_encoders=("flac",)),
)


def profiles_for(kind: str) -> list[Profile]:
    """Profils d'une famille, dans l'ordre d'affichage."""
    return [p for p in PROFILES if p.kind == kind]


def get_profile(profile_id: str) -> Profile:
    """Profil par identifiant ; KeyError si inconnu."""
    for p in PROFILES:
        if p.id == profile_id:
            return p
    raise KeyError(profile_id)


def default_profile(kind: str) -> Profile:
    """Premier profil de la famille."""
    return profiles_for(kind)[0]
