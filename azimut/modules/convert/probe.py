"""Sonde ffprobe d'Azimut Convert (logique pure) : HDR, pistes audio utilisables, sous-titres texte, durée."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Collection

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
)
AVCHD_PREFIX = ("-analyzeduration", "100M", "-probesize", "100M")


@dataclass
class MediaProbe:
    duration: float | None = None
    hdr_trc: str | None = None                          # « smpte2084 » (HDR10) ou « arib-std-b67 » (HLG)
    audio: list[dict] = field(default_factory=list)     # pistes audio décodables
    subs: list[dict] = field(default_factory=list)      # sous-titres texte (hors images)
    audio_total: int = 0                                # pistes audio, décodables ou non
    video_streams: int = 0

    @property
    def max_channels(self) -> int:
        return max((int(s.get("channels") or 0) for s in self.audio), default=0)

    @property
    def unusable_audio(self) -> int:
        return self.audio_total - len(self.audio)


def build_media_probe_command(ffprobe: str | Path, source: str | Path) -> list[str]:
    return [str(ffprobe), "-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,codec_tag_string,channels,color_transfer:format=duration",
            "-of", "json", str(source)]


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
    except (TypeError, ValueError):
        pass
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


def input_flags(attempt: int, source: Path | str) -> list[str]:
    """Options d'entrée de la tentative n° `attempt` (0, 1, 2) ; AVCHD : analyse plus longue."""
    flags = list(READ_STRATEGIES[min(attempt, len(READ_STRATEGIES) - 1)])
    if Path(str(source)).suffix.lower() in AVCHD_EXTS:
        flags = [*AVCHD_PREFIX, *flags]
    return flags
