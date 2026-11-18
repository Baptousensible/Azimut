"""Sonde ffprobe d'Azimut Convert (logique pure) : HDR, pistes audio utilisables, sous-titres texte, durée."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Collection

from azimut.core.ffmpeg_args import SAFE_INPUT_FLAGS, input_spec
from azimut.core.logs import get_logger

_log = get_logger("convert.probe")

UNUSABLE_AUDIO = frozenset({"", "none", "unknown", "apac"})           # pistes que ffmpeg ne sait pas décoder
BITMAP_SUBS = frozenset({"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub", "dvb_subtitle",
                         "dvb_teletext", "xsub", "vobsub", "arib_caption"})
HDR_TRANSFERS = {"smpte2084": "smpte2084", "arib-std-b67": "arib-std-b67"}
AVCHD_EXTS = frozenset({".mts", ".m2ts"})

# Stratégies de lecture successives pour les sources abîmées (la première est la lecture normale).
READ_STRATEGIES: tuple[tuple[str, ...], ...] = (
    (),
    ("-fflags", "+genpts+igndts", "-avoid_negative_ts", "make_zero"),
    ("-fflags", "+genpts+igndts", "-avoid_negative_ts", "make_zero", "-err_detect", "ignore_err"),
    # Dernier recours (audio) : une liste d'édition MP4/M4A incohérente peut masquer tout le média et
    # laisser l'encodeur sans aucun échantillon (« Could not open encoder before EOF »).
    ("-fflags", "+genpts+igndts", "-err_detect", "ignore_err", "-ignore_editlist", "1"),
)
VIDEO_ATTEMPTS = 3                                # la vidéo est lourde : lecture normale + 2 reprises
EDITLIST_FLAG = "-ignore_editlist"                # option du démuxeur MOV/MP4 uniquement : ailleurs, ffmpeg refuse net
MOV_EXTS = frozenset({".m4a", ".m4b", ".m4p", ".m4r", ".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".qt", ".f4v"})
AVCHD_PREFIX = ("-analyzeduration", "100M", "-probesize", "100M")


@dataclass
class MediaProbe:
    duration: float | None = None
    hdr_trc: str | None = None                          # « smpte2084 » (HDR10) ou « arib-std-b67 » (HLG)
    audio: list[dict] = field(default_factory=list)     # pistes audio décodables
    subs: list[dict] = field(default_factory=list)      # sous-titres texte (hors images)
    audio_total: int = 0                                # pistes audio, décodables ou non
    video_streams: int = 0
    width: int = 0                                      # première piste vidéo (0 si inconnu)
    height: int = 0

    @property
    def max_channels(self) -> int:
        return max((int(s.get("channels") or 0) for s in self.audio), default=0)

    @property
    def unusable_audio(self) -> int:
        return self.audio_total - len(self.audio)


def build_media_probe_command(ffprobe: str | Path, source: str | Path) -> list[str]:
    return [str(ffprobe), *SAFE_INPUT_FLAGS, "-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,codec_tag_string,channels,color_transfer,width,height:format=duration",
            "-of", "json", input_spec(source)]


def parse_media_probe(text: str) -> MediaProbe | None:
    """MediaProbe d'après la sortie JSON de ffprobe ; None si elle est inexploitable."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    probe = MediaProbe()
    try:
        d = float((data.get("format") or {}).get("duration"))
        probe.duration = d if d > 0 else None
    except (TypeError, ValueError) as exc:
        _log.debug("Durée illisible dans la sonde : %s", exc)
    first_video = True
    for s in data.get("streams") or []:
        if not isinstance(s, dict):
            continue
        kind = s.get("codec_type")
        name = str(s.get("codec_name") or "").lower()
        if kind == "video":
            probe.video_streams += 1
            if first_video:
                first_video = False
                trc = str(s.get("color_transfer") or "").lower()
                probe.hdr_trc = HDR_TRANSFERS.get(trc)
                try:
                    probe.width = int(s.get("width") or 0)
                    probe.height = int(s.get("height") or 0)
                except (TypeError, ValueError):
                    probe.width = probe.height = 0
        elif kind == "audio":
            probe.audio_total += 1
            tag = str(s.get("codec_tag_string") or "").lower()
            if name not in UNUSABLE_AUDIO and tag != "apac":
                probe.audio.append(s)
        elif kind == "subtitle" and name not in BITMAP_SUBS:
            probe.subs.append(s)
    return probe


def hdr_to_sdr_filter(trc: str | None, filters: Collection[str] | None) -> str:
    """Tone-mapping HDR → SDR BT.709 (zscale + Hable) ; repli `colorspace` si zscale/tonemap manquent."""
    trc = trc or "arib-std-b67"
    if filters is not None and "zscale" in filters and "tonemap" in filters:
        return (f"zscale=tin={trc}:pin=bt2020:min=bt2020nc:rin=tv:t=linear:p=bt2020:npl=100,"
                "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    return "colorspace=all=bt709:iall=bt2020,format=yuv420p"



def parse_filters(text: str) -> frozenset[str]:
    """Noms de filtres d'après `ffmpeg -filters` (lignes « TSC name  V->V  description »)."""
    names: set[str] = set()
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def audio_attempts(source: Path | str) -> int:
    """Nombre de tentatives de lecture pour un audio : la dernière (liste d'édition ignorée) n'existe que
    pour les conteneurs MP4/M4A, les autres démuxeurs rejetant l'option."""
    mov = Path(str(source)).suffix.lower() in MOV_EXTS
    return len(READ_STRATEGIES) if mov else len(READ_STRATEGIES) - 1


def input_flags(attempt: int, source: Path | str) -> list[str]:
    """Options d'entrée de la tentative n° `attempt` (0, 1, 2, 3) ; AVCHD : analyse plus longue.

    La liste blanche de protocoles est toujours présente : aucun média non fiable ne doit pouvoir faire
    sortir FFmpeg sur le réseau ni lire un fichier arbitraire (voir `azimut.core.ffmpeg_args`).
    `-ignore_editlist` n'est jamais émis hors des conteneurs MP4 : sur un MP3 ou un WAV, ffmpeg répond
    « Option not found » et abandonne.
    """
    suffix = Path(str(source)).suffix.lower()
    flags = list(READ_STRATEGIES[min(max(attempt, 0), len(READ_STRATEGIES) - 1)])
    if EDITLIST_FLAG in flags and suffix not in MOV_EXTS:
        i = flags.index(EDITLIST_FLAG)
        del flags[i:i + 2]
    if suffix in AVCHD_EXTS:
        flags = [*AVCHD_PREFIX, *flags]
    return [*SAFE_INPUT_FLAGS, *flags]
