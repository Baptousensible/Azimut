"""Codecs de sortie d'Azimut Convert et leurs valeurs de départ (données pures, sans Qt).

Trois catalogues : ``IMAGE_CODECS``, ``VIDEO_CODECS`` et ``AUDIO_CODECS``. Pour la vidéo, le codec
d'image et le codec audio sont choisis séparément dans l'interface : ``compose_video`` assemble le
profil effectif (encodeur vidéo + encodeur audio) au moment de la conversion.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QT_TRANSLATE_NOOP

# Extensions d'entrée par famille (minuscules, avec le point)
IMAGE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".heic", ".heif",
    ".jxl", ".jp2", ".qoi", ".tga", ".jpe", ".jfif",
})
# RAW : ffmpeg ne sait pas les décoder → état « Non pris en charge »
RAW_EXTS: frozenset[str] = frozenset({
    ".nef", ".cr2", ".cr3", ".dng", ".arw", ".orf", ".raf", ".rw2",
    ".pef", ".srw", ".x3f", ".3fr", ".mef", ".nrw", ".rwl", ".iiq",
})
VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mxf", ".ts", ".mts", ".m2ts",
    ".vob", ".mpg", ".mpeg", ".m2v", ".3gp", ".3g2", ".f4v", ".asf", ".ogv", ".webm",
    ".qt", ".m2t", ".dv", ".ogm", ".divx", ".rm", ".rmvb",
})
AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".amr", ".aiff", ".aif", ".mka",
    ".ac3", ".mp2", ".wv",
    # Conteneurs audio courants que FFmpeg sait lire : sonneries iPhone (.m4r), livres audio (.m4b), Ogg, Monkey's, etc.
    ".m4r", ".m4b", ".oga", ".spx", ".ape", ".mpc", ".caf", ".au", ".3ga", ".weba", ".dts", ".tta", ".mp1", ".mpga",
})
ALL_EXTS: frozenset[str] = IMAGE_EXTS | RAW_EXTS | VIDEO_EXTS | AUDIO_EXTS

KINDS: tuple[str, ...] = ("image", "video", "audio")
OTHER_KIND = "other"                       # fichier ni image, ni vidéo, ni audio (PDF, texte…) : copié tel quel
KIND_LABELS: dict[str, str] = {"image": "Image", "video": "Vidéo", "audio": "Audio", OTHER_KIND: "Autre"}

AV1_ENCODERS = ("libsvtav1", "libaom-av1")        # par ordre de préférence
OPUS_FILTER = "aformat=channel_layouts=7.1|5.1|quad|stereo|mono"   # évite les disposition 5.1(side) refusées


@dataclass(frozen=True)
class Profile:
    """Un profil de conversion. Les encodeurs sont des candidats, du préféré au repli.

    Un profil vidéo porte aussi un audio par défaut (utilisé seul, sans choix explicite) ; l'interface
    le remplace par le codec audio choisi via ``compose_video``.
    """
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
    quality: int | None = None                  # -q:v (JPEG, MPEG…), -quality (WebP) ou -distance (JXL)
    audio_bitrate: str = ""
    audio_filter: str = ""
    even_dims: bool = False                     # dimensions paires (AV1 / H.264 / VP9…)
    extra_out: tuple[str, ...] = ()
    guard_growth: bool = False                  # anti-grossissement (image et vidéo)
    threads: int = 0                            # threads de l'encodeur (0 = laisser l'encodeur décider)
    sub_codec: str = ""                         # sous-titres texte conservés : webvtt (WebM), mov_text (MP4), copy
    hdr_mode: str = "T"                         # vidéos HDR : « T » tone-mapping vers SDR, « H » HDR natif (AV1)
    only_if_encoder: bool = False               # proposé seulement si FFmpeg expose l'un des encodeurs


# Profil des fichiers « autres » : aucune conversion, simple copie (option « Copier aussi les autres fichiers »).
COPY_PROFILE = Profile(
    id="copy_other", label="Copie telle quelle", kind=OTHER_KIND, out_ext="",
    description=QT_TRANSLATE_NOOP("Profiles", "Fichier ni image, ni vidéo, ni audio : copié sans modification dans le dossier de sortie."),
    in_exts=frozenset())

# Un profil par codec : ses valeurs sont les réglages de départ, que l'utilisateur ajuste avec les curseurs
# du panneau « Format et qualité » (voir ``advanced.apply_overrides``). Pas de préréglages nommés.
IMAGE_CODECS: tuple[Profile, ...] = (
    Profile("image_avif", "AVIF", "image", ".avif",
            QT_TRANSLATE_NOOP("Profiles", "AVIF (AV1) : très compact, excellente qualité visuelle ; encodage assez lent."),
            IMAGE_EXTS, video_encoders=AV1_ENCODERS, crf=39, preset="4", even_dims=True, guard_growth=True),
    Profile("image_heic", "HEIC", "image", ".heic",
            QT_TRANSLATE_NOOP("Profiles", "HEIC : format Apple compact ; nécessite pillow-heif. La qualité se règle de 0 à 100."),
            IMAGE_EXTS, quality=80, guard_growth=True),
    Profile("image_webp", "WebP", "image", ".webp",
            QT_TRANSLATE_NOOP("Profiles", "WebP : bon compromis taille / qualité, largement pris en charge."),
            IMAGE_EXTS, video_encoders=("libwebp",), quality=80, guard_growth=True),
    Profile("image_jpeg", "JPEG", "image", ".jpg",
            QT_TRANSLATE_NOOP("Profiles", "JPEG : lisible partout, compression avec perte."),
            IMAGE_EXTS, video_encoders=("mjpeg",), quality=3, guard_growth=True),
    Profile("image_png", "PNG", "image", ".png",
            QT_TRANSLATE_NOOP("Profiles", "PNG : sans perte, idéal pour les captures et les aplats ; le niveau règle la compression."),
            IMAGE_EXTS, video_encoders=("png",), preset="6", guard_growth=True),
    Profile("image_jxl", "JPEG XL", "image", ".jxl",
            QT_TRANSLATE_NOOP("Profiles", "JPEG XL : très bonne qualité à taille réduite, sans perte possible (distance 0)."),
            IMAGE_EXTS, video_encoders=("libjxl",), quality=1, preset="7", guard_growth=True),
    Profile("image_jp2", "JPEG 2000", "image", ".jp2",
            QT_TRANSLATE_NOOP("Profiles", "JPEG 2000 : sans perte, apprécié en archivage et en imagerie professionnelle."),
            IMAGE_EXTS, video_encoders=("libopenjpeg",), guard_growth=True),
    Profile("image_tiff", "TIFF", "image", ".tiff",
            QT_TRANSLATE_NOOP("Profiles", "TIFF : sans perte, pour l'impression et l'archivage ; la compression se choisit."),
            IMAGE_EXTS, video_encoders=("tiff",), preset="deflate", guard_growth=True),
    Profile("image_bmp", "BMP", "image", ".bmp",
            QT_TRANSLATE_NOOP("Profiles", "BMP : sans compression, universel sous Windows ; fichiers volumineux."),
            IMAGE_EXTS, video_encoders=("bmp",), guard_growth=True),
    Profile("image_qoi", "QOI", "image", ".qoi",
            QT_TRANSLATE_NOOP("Profiles", "QOI : sans perte, très rapide à lire et à écrire ; format récent, peu répandu."),
            IMAGE_EXTS, video_encoders=("qoi",), guard_growth=True),
)

VIDEO_CODECS: tuple[Profile, ...] = (
    Profile("video_webm_av1", "AV1 (WebM)", "video", ".webm",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo AV1 dans un WebM : très compact, encodage lent."),
            VIDEO_EXTS, video_encoders=AV1_ENCODERS, audio_encoders=("libopus",), crf=40, preset="4",
            audio_bitrate="128k", audio_filter=OPUS_FILTER, even_dims=True, guard_growth=True,
            sub_codec="webvtt"),
    Profile("video_mp4_av1", "AV1 (MP4)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo AV1 dans un MP4 : très compact, plus compatible que l'AV1 en WebM sur certains lecteurs."),
            VIDEO_EXTS, video_encoders=AV1_ENCODERS, audio_encoders=("aac",), crf=40, preset="4",
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "av01"), guard_growth=True, sub_codec="mov_text"),
    Profile("video_mkv_h264", "H.264 (MKV)", "video", ".mkv",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo H.264 dans un MKV : conteneur souple, sous-titres texte copiés et audio varié."),
            VIDEO_EXTS, video_encoders=("libx264",), audio_encoders=("aac",), crf=23, preset="medium",
            audio_bitrate="160k", even_dims=True, guard_growth=True, sub_codec="copy"),
    Profile("video_mp4_h264", "H.264 (MP4)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo H.264 dans un MP4 : lisible partout."),
            VIDEO_EXTS, video_encoders=("libx264",), audio_encoders=("aac",), crf=23, preset="medium",
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True,
            sub_codec="mov_text"),
    Profile("video_mp4_h265", "H.265 / HEVC (MP4)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo H.265 dans un MP4 : meilleure compression que H.264, un peu moins universel."),
            VIDEO_EXTS, video_encoders=("libx265",), audio_encoders=("aac",), crf=26, preset="medium",
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "hvc1"), guard_growth=True, sub_codec="mov_text"),
    Profile("video_mp4_h264_nvenc", "H.264 NVIDIA (NVENC)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "H.264 accéléré par NVIDIA NVENC. Nécessite un GPU et le pilote NVIDIA compatibles."),
            VIDEO_EXTS, video_encoders=("h264_nvenc",), audio_encoders=("aac",), crf=23, preset="p4",
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True,
            sub_codec="mov_text"),
    Profile("video_mp4_h265_nvenc", "H.265 NVIDIA (NVENC)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "H.265 accéléré par NVIDIA NVENC. Nécessite un GPU et le pilote NVIDIA compatibles."),
            VIDEO_EXTS, video_encoders=("hevc_nvenc",), audio_encoders=("aac",), crf=26, preset="p4",
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "hvc1"), guard_growth=True, sub_codec="mov_text"),
    Profile("video_mp4_av1_nvenc", "AV1 NVIDIA (NVENC)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "AV1 accéléré par NVIDIA NVENC (RTX 40 et plus). Proposé seulement si FFmpeg "
            "expose av1_nvenc ; nécessite un GPU et le pilote NVIDIA compatibles."),
            VIDEO_EXTS, video_encoders=("av1_nvenc",), audio_encoders=("aac",), crf=32, preset="p4",
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "av01"), guard_growth=True, sub_codec="mov_text",
            only_if_encoder=True),
    Profile("video_mp4_h264_qsv", "H.264 Intel (QSV)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "H.264 accéléré par Intel Quick Sync Video. Nécessite un processeur et un pilote compatibles."),
            VIDEO_EXTS, video_encoders=("h264_qsv",), audio_encoders=("aac",), crf=23, preset="medium",
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True,
            sub_codec="mov_text"),
    Profile("video_mp4_h265_qsv", "H.265 Intel (QSV)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "H.265 accéléré par Intel Quick Sync Video. Nécessite un processeur et un pilote compatibles."),
            VIDEO_EXTS, video_encoders=("hevc_qsv",), audio_encoders=("aac",), crf=26, preset="medium",
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "hvc1"), guard_growth=True, sub_codec="mov_text"),
    Profile("video_mp4_av1_qsv", "AV1 Intel (QSV)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "AV1 accéléré par Intel Quick Sync Video (GPU Intel Arc et suivants). "
            "Proposé seulement si FFmpeg expose av1_qsv ; nécessite un pilote compatible."),
            VIDEO_EXTS, video_encoders=("av1_qsv",), audio_encoders=("aac",), crf=32, preset="medium",
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "av01"), guard_growth=True, sub_codec="mov_text",
            only_if_encoder=True),
    Profile("video_mp4_h264_vaapi", "H.264 VA-API", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "H.264 accéléré par VA-API (Linux). Nécessite un périphérique /dev/dri/renderD* compatible."),
            VIDEO_EXTS, video_encoders=("h264_vaapi",), audio_encoders=("aac",), crf=23,
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True,
            sub_codec="mov_text"),
    Profile("video_mp4_h265_vaapi", "H.265 VA-API", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "H.265 accéléré par VA-API (Linux). Nécessite un périphérique /dev/dri/renderD* compatible."),
            VIDEO_EXTS, video_encoders=("hevc_vaapi",), audio_encoders=("aac",), crf=26,
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "hvc1"), guard_growth=True, sub_codec="mov_text"),
    Profile("video_mp4_av1_vaapi", "AV1 VA-API", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "AV1 accéléré par VA-API (Linux, Mesa récent). Proposé seulement si FFmpeg "
            "expose av1_vaapi ; nécessite un périphérique /dev/dri/renderD* qui sait encoder l'AV1."),
            VIDEO_EXTS, video_encoders=("av1_vaapi",), audio_encoders=("aac",), crf=32,
            audio_bitrate="160k", even_dims=True,
            extra_out=("-movflags", "+faststart", "-tag:v", "av01"), guard_growth=True, sub_codec="mov_text",
            only_if_encoder=True),
    Profile("video_webm_vp9", "VP9 (WebM)", "video", ".webm",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo VP9 dans un WebM : bon compromis, bien pris en charge par les navigateurs."),
            VIDEO_EXTS, video_encoders=("libvpx-vp9",), audio_encoders=("libopus",), crf=32, preset="2",
            audio_bitrate="128k", audio_filter=OPUS_FILTER, even_dims=True, guard_growth=True,
            sub_codec="webvtt"),
    Profile("video_mp4_mpeg4", "MPEG-4 (MP4)", "video", ".mp4",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo MPEG-4 partie 2 dans un MP4 : ancien format, très compatible avec les lecteurs logiciels."),
            VIDEO_EXTS, video_encoders=("mpeg4",), audio_encoders=("aac",), quality=4,
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True,
            sub_codec="mov_text"),
    Profile("video_mpg_mpeg2", "MPEG-2 (MPG)", "video", ".mpg",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo MPEG-2 et audio MP2 : pour la télévision, les DVD et les appareils anciens."),
            VIDEO_EXTS, video_encoders=("mpeg2video",), audio_encoders=("mp2",), quality=4,
            audio_bitrate="192k", even_dims=True, guard_growth=True),
    Profile("video_ogv_theora", "Theora (OGV)", "video", ".ogv",
            QT_TRANSLATE_NOOP("Profiles", "Vidéo Theora et audio Vorbis dans un OGV : libre, lisible par les lecteurs Ogg."),
            VIDEO_EXTS, video_encoders=("libtheora",), audio_encoders=("libvorbis",), quality=6,
            audio_bitrate="160k", even_dims=True, guard_growth=True),
    Profile("video_mov_prores", "ProRes (MOV)", "video", ".mov",
            QT_TRANSLATE_NOOP("Profiles", "ProRes dans un MOV : montage vidéo, audio PCM 24 bits par défaut ; fichiers volumineux "
            "mais faciles à décoder."),
            VIDEO_EXTS, video_encoders=("prores_ks",), audio_encoders=("aac",), preset="hq",
            audio_bitrate="160k", even_dims=True, extra_out=("-movflags", "+faststart"), guard_growth=True,
            sub_codec="mov_text"),
)

AUDIO_CODECS: tuple[Profile, ...] = (
    Profile("audio_mp3", "MP3", "audio", ".mp3",
            QT_TRANSLATE_NOOP("Profiles", "MP3 : compatible avec tous les lecteurs."), AUDIO_EXTS, audio_encoders=("libmp3lame",),
            audio_bitrate="192k"),
    Profile("audio_aac", "AAC (M4A)", "audio", ".m4a",
            QT_TRANSLATE_NOOP("Profiles", "AAC dans un conteneur M4A."), AUDIO_EXTS, audio_encoders=("aac",), audio_bitrate="160k",
            extra_out=("-movflags", "+faststart")),
    Profile("audio_opus", "Opus", "audio", ".opus",
            QT_TRANSLATE_NOOP("Profiles", "Opus : excellent rapport qualité / taille."),
            AUDIO_EXTS, audio_encoders=("libopus",), audio_bitrate="128k", audio_filter=OPUS_FILTER),
    # FLAC : sans perte ; « preset » porte ici le niveau de compression (-compression_level, 0 à 12)
    Profile("audio_flac", "FLAC (sans perte)", "audio", ".flac",
            QT_TRANSLATE_NOOP("Profiles", "FLAC : compression sans perte (le curseur ne change que la taille et la vitesse)."),
            AUDIO_EXTS, audio_encoders=("flac",), preset="5"),
    Profile("audio_vorbis", "Vorbis (OGG)", "audio", ".ogg",
            QT_TRANSLATE_NOOP("Profiles", "Vorbis dans un OGG : libre, bon rapport qualité / taille."),
            AUDIO_EXTS, audio_encoders=("libvorbis",), audio_bitrate="160k"),
    Profile("audio_alac", "ALAC (sans perte)", "audio", ".m4a",
            QT_TRANSLATE_NOOP("Profiles", "ALAC : compression sans perte Apple, dans un conteneur M4A."),
            AUDIO_EXTS, audio_encoders=("alac",), extra_out=("-movflags", "+faststart")),
    Profile("audio_wav", "WAV (PCM 16 bits)", "audio", ".wav",
            QT_TRANSLATE_NOOP("Profiles", "WAV : audio non compressé, fichier volumineux."),
            AUDIO_EXTS, audio_encoders=("pcm_s16le",)),
    Profile("audio_ac3", "AC-3", "audio", ".ac3",
            QT_TRANSLATE_NOOP("Profiles", "AC-3 : utilisé par les DVD et la télévision."),
            AUDIO_EXTS, audio_encoders=("ac3",), audio_bitrate="192k"),
    Profile("audio_mp2", "MP2", "audio", ".mp2",
            QT_TRANSLATE_NOOP("Profiles", "MP2 : audio des flux MPEG-2 (télévision, DVD)."),
            AUDIO_EXTS, audio_encoders=("mp2",), audio_bitrate="192k"),
    Profile("audio_aiff", "AIFF (PCM 16 bits)", "audio", ".aiff",
            QT_TRANSLATE_NOOP("Profiles", "AIFF : PCM 16 bits non compressé, pour le montage et l'archivage sur macOS."),
            AUDIO_EXTS, audio_encoders=("pcm_s16be",)),
    Profile("audio_pcm24", "WAV (PCM 24 bits)", "audio", ".wav",
            QT_TRANSLATE_NOOP("Profiles", "WAV : PCM 24 bits non compressé, pour le montage et l'archivage audio."),
            AUDIO_EXTS, audio_encoders=("pcm_s24le",)),
)

PROFILES: tuple[Profile, ...] = IMAGE_CODECS + VIDEO_CODECS + AUDIO_CODECS

# Codecs audio proposés par conteneur vidéo (premier = défaut). Repli quand le codec vidéo n'a pas
# de liste propre ci-dessous.
VIDEO_AUDIO_IDS: dict[str, tuple[str, ...]] = {
    ".webm": ("audio_opus", "audio_vorbis"),
    ".mp4": ("audio_aac", "audio_mp3", "audio_ac3", "audio_alac"),
    ".mkv": ("audio_aac", "audio_opus", "audio_mp3", "audio_vorbis", "audio_flac", "audio_ac3",
             "audio_alac", "audio_wav", "audio_pcm24", "audio_mp2"),
    ".mpg": ("audio_mp2", "audio_ac3"),
    ".ogv": ("audio_vorbis", "audio_opus"),
    ".mov": ("audio_aac", "audio_alac", "audio_wav", "audio_pcm24", "audio_mp3"),
}

# Codecs audio propres à un codec vidéo (premier = défaut), prioritaires sur le conteneur : un MP4 AV1
# accepte Opus, un MPEG-4 partie 2 se contente d'AAC/MP3, un ProRes privilégie le PCM pour le montage.
AV1_MP4_AUDIO = ("audio_aac", "audio_opus", "audio_mp3")
VIDEO_AUDIO_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "video_mp4_av1": AV1_MP4_AUDIO,
    "video_mp4_av1_nvenc": AV1_MP4_AUDIO,
    "video_mp4_av1_qsv": AV1_MP4_AUDIO,
    "video_mp4_av1_vaapi": AV1_MP4_AUDIO,
    "video_mp4_mpeg4": ("audio_aac", "audio_mp3"),
    "video_ogv_theora": ("audio_vorbis",),
    "video_mov_prores": ("audio_pcm24", "audio_wav", "audio_aac", "audio_alac"),
}

# Anciens profils fusionnés : identifiant → (nouveau profil, réglages à reporter). Sert à ne pas perdre le
# choix et les valeurs enregistrés par l'utilisateur (voir ``advanced.migrate_legacy``).
LEGACY_PROFILES: dict[str, tuple[str, dict[str, object]]] = {
    "image_avif_small": ("image_avif", {}),
    "image_avif_hq": ("image_avif", {"crf": 25}),
    "image_avif_max": ("image_avif", {"preset": "2"}),
    "video_webm_hq": ("video_webm_av1", {"crf": 30}),
    "video_webm_max": ("video_webm_av1", {"preset": "2"}),
    "video_fix_mono": ("video_webm_av1", {}),
}


def profiles_for(kind: str) -> list[Profile]:
    """Codecs d'une famille, dans l'ordre d'affichage."""
    if kind == "video":
        return list(VIDEO_CODECS)
    if kind == "image":
        return list(IMAGE_CODECS)
    if kind == "audio":
        return list(AUDIO_CODECS)
    return []


def get_profile(profile_id: str) -> Profile:
    """Profil par identifiant ; KeyError si inconnu."""
    for p in PROFILES:
        if p.id == profile_id:
            return p
    raise KeyError(profile_id)


def default_profile(kind: str) -> Profile:
    """Premier codec de la famille."""
    return profiles_for(kind)[0]


def audio_codecs_for(video: Profile | str) -> list[Profile]:
    """Codecs audio compatibles avec un codec vidéo (ou un conteneur), défaut en tête."""
    if isinstance(video, Profile):
        ids = VIDEO_AUDIO_BY_PROFILE.get(video.id) or VIDEO_AUDIO_IDS.get(video.out_ext.lower())
    else:
        ids = VIDEO_AUDIO_IDS.get(video.lower())
    if not ids:
        ids = tuple(p.id for p in AUDIO_CODECS)
    out: list[Profile] = []
    for pid in ids:
        try:
            out.append(get_profile(pid))
        except KeyError:
            continue
    return out or list(AUDIO_CODECS)


def audio_fits(video: Profile | str, audio_id: str) -> bool:
    """Vrai si ce codec audio peut être accouplé à ce codec vidéo (ou conteneur)."""
    return any(p.id == audio_id for p in audio_codecs_for(video))


def compose_video(video: Profile, audio: Profile) -> Profile:
    """Profil vidéo effectif : encodeurs et débit audio du codec audio choisi, réglages vidéo inchangés."""
    return replace(video, label=f"{video.label} + {audio.label}",
                   audio_encoders=audio.audio_encoders, audio_bitrate=audio.audio_bitrate,
                   audio_filter=audio.audio_filter)
