"""Logique pure d'Azimut Convert (sans Qt) : travaux, commandes ffmpeg, progression."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Collection, NamedTuple

from azimut.core.fsutil import iter_files, safe_relative
from azimut.modules.convert.profiles import (
    ALL_EXTS, AUDIO_EXTS, IMAGE_EXTS, RAW_EXTS, VIDEO_EXTS, Profile,
)

# ── États affichés dans le tableau ───────────────────────────────────────────
STATE_PENDING = "En attente"
STATE_RUNNING = "En cours"
STATE_DONE = "Terminé"
STATE_SKIPPED = "Déjà converti"
STATE_NO_ENCODER = "Encodeur manquant"
STATE_UNSUPPORTED = "Non pris en charge"
STATE_ERROR = "Erreur"
STATE_CANCELLED = "Annulé"
STATE_KEPT = "Source conservée"

STATE_TAGS = {
    STATE_PENDING: "neutre", STATE_RUNNING: "info", STATE_DONE: "ok", STATE_SKIPPED: "neutre",
    STATE_NO_ENCODER: "err", STATE_UNSUPPORTED: "warn", STATE_ERROR: "err",
    STATE_CANCELLED: "warn", STATE_KEPT: "warn",
}

EVEN_VF = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"


class EncoderMissingError(RuntimeError):
    """Aucun encodeur candidat n'est disponible dans ce ffmpeg."""


def state_tag(state: str) -> str:
    """Couleur de ligne (« _tag » du tableau) associée à un état."""
    return STATE_TAGS.get(state, "neutre")


# ── Entrées ──────────────────────────────────────────────────────────────────
def classify(path: Path | str) -> str | None:
    """Famille d'un fichier d'après son extension : image | video | audio | None."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS or ext in RAW_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def path_key(path: Path | str) -> str:
    """Clé de déduplication d'un chemin."""
    return os.path.normcase(os.path.abspath(str(path)))


@dataclass(frozen=True)
class SourceItem:
    """Un fichier à convertir. `root` : dossier ajouté (None pour un fichier isolé)."""
    path: Path
    root: Path | None
    kind: str
    size: int

    @property
    def rel(self) -> Path:
        return Path(self.path.name) if self.root is None else safe_relative(self.path, self.root)


class ScanResult(NamedTuple):
    items: list[SourceItem]
    ignored: list[str]


def scan_inputs(paths: Collection[str | Path], recursive: bool = True,
                cancel: Callable[[], bool] | None = None) -> ScanResult:
    """Transforme fichiers et dossiers en liste de SourceItem (dédoublonnée, triée)."""
    items: list[SourceItem] = []
    ignored: list[str] = []
    seen: set[str] = set()

    def add(path: Path, root: Path | None) -> None:
        kind = classify(path)
        if kind is None:
            ignored.append(f"{path} (extension non gérée)")
            return
        key = path_key(path)
        if key in seen:
            return
        try:
            size = path.stat().st_size
        except OSError:
            ignored.append(f"{path} (illisible)")
            return
        seen.add(key)
        items.append(SourceItem(path, root, kind, size))

    for raw in paths:
        if cancel is not None and cancel():
            break
        p = Path(raw)
        if p.is_dir():
            for f in iter_files(p, recursive=recursive, extensions=ALL_EXTS, cancel=cancel):
                add(f, p)
        elif p.is_file():
            add(p, None)
        else:
            ignored.append(f"{p} (introuvable)")
    items.sort(key=lambda i: (str(i.root or "").lower(), str(i.path).lower()))
    return ScanResult(items, ignored)


# ── Encodeurs ────────────────────────────────────────────────────────────────
_ENC_RE = re.compile(r"^\s*[VAS][F.][S.][X.][B.][D.]\s+(\S+)")


def parse_encoders(text: str) -> frozenset[str]:
    """Noms d'encodeurs d'après la sortie de `ffmpeg -encoders`."""
    names: set[str] = set()
    has_sep = any(line.strip().startswith("---") for line in text.splitlines())
    started = not has_sep
    for line in text.splitlines():
        if not started:
            started = line.strip().startswith("---")
            continue
        m = _ENC_RE.match(line)
        if m and m.group(1) != "=":
            names.add(m.group(1))
    return frozenset(names)


def pick_encoder(candidates: tuple[str, ...], encoders: Collection[str] | None) -> str:
    """Premier encodeur candidat disponible (le premier si la liste est inconnue)."""
    if not candidates:
        return ""
    if encoders is None:
        return candidates[0]
    for c in candidates:
        if c in encoders:
            return c
    raise EncoderMissingError(" ou ".join(candidates))


def missing_encoder_message(profile: Profile, encoders: Collection[str] | None) -> str | None:
    """Message explicite si le profil est impossible avec ce ffmpeg, sinon None."""
    if encoders is None:
        return None
    manque = [" ou ".join(c) for c in (profile.video_encoders, profile.audio_encoders)
              if c and not any(e in encoders for e in c)]
    if not manque:
        return None
    return f"Profil « {profile.label} » impossible avec ce FFmpeg : encodeur manquant ({' ; '.join(manque)})."


# ── Travaux et chemins de sortie ─────────────────────────────────────────────
@dataclass
class Job:
    index: int                      # indice dans la file (= ligne du tableau)
    item: SourceItem
    profile: Profile
    dest: Path | None = None        # None : dossier de sortie à choisir
    copy_dest: Path | None = None   # emplacement de la copie de la source (anti-grossissement)
    state: str = STATE_PENDING
    message: str = ""


def default_output_dir(root: Path) -> Path:
    """<source>_Converted, voisin de la source."""
    return root.parent / f"{root.name}_Converted"


def part_path(dest: Path) -> Path:
    """Fichier temporaire `.<nom>.part.<ext>` (l'extension réelle permet à ffmpeg de choisir le format)."""
    return dest.with_name(f".{dest.stem}.part{dest.suffix}")


def initial_state(item: SourceItem, profile: Profile, encoders: Collection[str] | None) -> str:
    """État prévisible sans toucher au disque."""
    if item.path.suffix.lower() in RAW_EXTS:
        return STATE_UNSUPPORTED
    if missing_encoder_message(profile, encoders):
        return STATE_NO_ENCODER
    return STATE_PENDING


def _output_base(item: SourceItem, out_dir: Path | None, multi_roots: bool) -> Path | None:
    if item.root is None:
        return out_dir
    if out_dir is None:
        return default_output_dir(item.root)
    return out_dir / item.root.name if multi_roots else out_dir


def _claim(dest: Path, taken: set[str]) -> Path:
    """Premier nom libre dans le lot (même schéma que unique_path : nom__1.ext)."""
    cand, i = dest, 0
    while path_key(cand) in taken:
        i += 1
        cand = dest.with_name(f"{dest.stem}__{i}{dest.suffix}")
    taken.add(path_key(cand))
    return cand


def _already_done(job: Job) -> bool:
    try:
        if job.dest is not None and job.dest.exists() and job.dest.stat().st_size > 0:
            return True
        cp = job.copy_dest
        if (job.profile.guard_growth and cp is not None and path_key(cp) != path_key(job.item.path)
                and cp.exists() and cp.stat().st_size == job.item.size):
            return True     # source déjà conservée lors d'un passage précédent
    except OSError:
        pass
    return False


def plan_jobs(items: list[SourceItem], profiles: dict[str, Profile], out_dir: Path | None,
              encoders: Collection[str] | None = None, *, check_disk: bool = True) -> list[Job]:
    """Une liste de Job alignée sur `items` : chemins de sortie, collisions, états initiaux.

    Les collisions de noms dans un lot sont résolues de façon déterministe (ordre alphabétique des sources),
    donc un second passage retrouve les mêmes noms et reconnaît les fichiers « Déjà converti ».
    """
    multi = len({str(i.root) for i in items if i.root is not None}) > 1
    taken: set[str] = {path_key(i.path) for i in items}
    jobs: list[Job] = [Job(n, it, profiles[it.kind]) for n, it in enumerate(items)]
    for n in sorted(range(len(items)), key=lambda k: str(items[k].path).lower()):
        job = jobs[n]
        state = initial_state(job.item, job.profile, encoders)
        if state != STATE_PENDING:
            job.state = state
            job.message = (missing_encoder_message(job.profile, encoders) or "") if state == STATE_NO_ENCODER \
                else "Format non pris en charge par FFmpeg (RAW)."
            continue
        base = _output_base(job.item, out_dir, multi)
        if base is None:
            continue
        dest = _claim(base / job.item.rel.with_suffix(job.profile.out_ext), taken)
        job.dest = dest
        job.copy_dest = dest.with_name(job.item.path.name)
        if check_disk and _already_done(job):
            job.state = STATE_SKIPPED
    return jobs


# ── Commandes ffmpeg / ffprobe ───────────────────────────────────────────────
def build_encoders_command(ffmpeg: str | Path) -> list[str]:
    return [str(ffmpeg), "-hide_banner", "-encoders"]


def build_probe_command(ffprobe: str | Path, source: str | Path) -> list[str]:
    return [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source)]


def _video_quality_args(profile: Profile, venc: str, *, still: bool) -> list[str]:
    n = profile.threads if profile.threads > 0 else 0
    if venc == "libsvtav1":
        args = ["-crf", str(profile.crf), "-preset", profile.preset]
        if still:
            args += ["-b:v", "0"]
        return args + (["-svtav1-params", f"lp={n}"] if n else [])      # lp = niveau de parallélisme
    if venc == "libaom-av1":
        cpu = str(min(int(profile.preset or 4), 8))
        return (["-crf", str(profile.crf), "-b:v", "0", "-cpu-used", cpu] + ([] if still else ["-row-mt", "1"])
                + (["-threads", str(n)] if n else []))
    if venc == "libx264":
        return ["-crf", str(profile.crf), "-preset", profile.preset] + (["-threads", str(n)] if n else [])
    if venc == "mjpeg":
        return ["-q:v", str(profile.quality)]
    if venc == "libwebp":
        return ["-quality", str(profile.quality)]
    return []


def _audio_args(profile: Profile, aenc: str) -> list[str]:
    args = ["-c:a", aenc]
    if profile.audio_bitrate:
        args += ["-b:a", profile.audio_bitrate]
    if profile.audio_filter:
        args += ["-af", profile.audio_filter]
    return args


def build_command(profile: Profile, source: Path, output: Path, *, ffmpeg: str | Path = "ffmpeg",
                  encoders: Collection[str] | None = None) -> list[str]:
    """Commande ffmpeg complète (progression sur stdout). `output` est normalement part_path(dest)."""
    venc = pick_encoder(profile.video_encoders, encoders)
    aenc = pick_encoder(profile.audio_encoders, encoders)
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source)]
    if profile.kind == "image":
        cmd += ["-map", "0:v:0", "-frames:v", "1"]
        if profile.even_dims:
            cmd += ["-vf", EVEN_VF]
        cmd += ["-c:v", venc, *_video_quality_args(profile, venc, still=True)]
    elif profile.kind == "video":
        cmd += ["-map", "0:V:0", "-map", "0:a?", "-vf", EVEN_VF if profile.even_dims else "format=yuv420p",
                "-c:v", venc, *_video_quality_args(profile, venc, still=False),
                *_audio_args(profile, aenc), "-sn", "-dn"]
    else:
        cmd += ["-vn", "-sn", "-dn", "-map", "0:a:0", *_audio_args(profile, aenc)]
    cmd += ["-map_metadata", "0", *profile.extra_out, "-progress", "pipe:1", "-nostats", str(output)]
    return cmd


# ── Lecture des sorties ──────────────────────────────────────────────────────
def parse_duration(text: str) -> float | None:
    """Durée en secondes depuis la sortie de ffprobe, ou None."""
    for line in (text or "").splitlines():
        try:
            d = float(line.strip())
        except ValueError:
            continue
        return d if d > 0 else None
    return None


def parse_progress_line(line: str, duration: float | None) -> int | None:
    """Pourcentage (0–100) depuis une ligne `-progress pipe:1`. out_time_ms est en réalité en microsecondes."""
    line = line.strip()
    if line == "progress=end":
        return 100
    for prefix in ("out_time_us=", "out_time_ms="):
        if line.startswith(prefix):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                return None
            if us < 0 or not duration or duration <= 0:
                return None
            return max(0, min(99, int(us / 1_000_000 / duration * 100)))
    return None


def grew(size_in: int, size_out: int) -> bool:
    """Vrai si le résultat est plus gros que la source (anti-grossissement)."""
    return size_in > 0 and size_out > size_in


def classify_failure(stderr: str, ext: str) -> str:
    """État à donner à un échec ffmpeg : encodeur manquant, format non pris en charge ou erreur."""
    low = (stderr or "").lower()
    if "unknown encoder" in low or "encoder not found" in low:
        return STATE_NO_ENCODER
    if ext.lower() in (".heic", ".heif"):
        return STATE_UNSUPPORTED
    if any(k in low for k in ("invalid data found when processing input", "no decoder", "decoder not found",
                              "unknown decoder", "unsupported codec", "could not find codec parameters")):
        return STATE_UNSUPPORTED
    return STATE_ERROR


def stderr_tail(text: str, n: int = 400) -> str:
    s = (text or "").strip()
    return s[-n:]
