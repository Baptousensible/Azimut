"""Logique pure d'Azimut Tags (sans Qt) : analyse ExifTool, arguments, préréglages, annulation."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# ── Constantes ───────────────────────────────────────────────────────────────
READ_CHUNK = 200                                   # fichiers par appel ExifTool (lecture)
EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"
INPUT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
QT_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".3gp"})
MATROSKA_EXTENSIONS = frozenset({".mkv", ".webm"})
DATE_TAGS = ("DateTimeOriginal", "CreateDate", "ModifyDate", "MediaCreateDate",
             "MediaModifyDate", "TrackCreateDate", "TrackModifyDate")
WRITE_OPTIONS = ("-m", "-overwrite_original", "-P")
UNDO_COLLECTION = "tags_undo"
UNDO_KEEP = 20                                     # applications annulables conservées
BINARY_PREFIX = "(Binary data"
SUPPORTED_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif", ".avif",
    ".jp2", ".jxl", ".nef", ".cr2", ".cr3", ".dng", ".arw", ".orf", ".raf", ".rw2", ".pef", ".srw",
    ".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".mpg",
    ".mpeg", ".wmv", ".flv", ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma",
    ".aiff", ".pdf",
})

_SNAPSHOT_SKIP = frozenset({"file", "system", "composite", "exiftool"})
_EDITABLE_GROUP_RE = re.compile(
    r"^(?:exif|exififd|gps|interopifd|ifd\d+|subifd\d*|iptc\w*|xmp(?:-[\w\-]+)?)$", re.I)
_TAG_RE = re.compile(r"^(?:[\w\-]+:)?[\w\-\.]+$")
_DUP_SUFFIX = re.compile(r"\s*\(\d+\)$")
_DT_RE = re.compile(r"^\s*(\d{4})[-:](\d{2})[-:](\d{2})[ T](\d{2}):(\d{2}):(\d{2})\s*$")
_WRITE_OK_RE = re.compile(r"\b[1-9]\d*\s+(?:image\s+)?files?\s+(?:updated|unchanged)", re.I)


# ── Modèle ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class TagEntry:
    """Un tag lu dans un fichier (valeur lisible par l'humain)."""
    group: str
    name: str
    value: str
    writable: bool = True
    items: tuple[str, ...] = field(default=(), compare=False)   # éléments si le tag est une liste

    @property
    def full_name(self) -> str:
        return f"{self.group}:{self.name}" if self.group else self.name


@dataclass(frozen=True, slots=True)
class TagChange:
    """Une modification en attente : op « set » ou « delete »."""
    op: str
    tag: str
    value: str = ""


@dataclass
class FileTags:
    path: Path
    size: int = 0
    entries: list[TagEntry] = field(default_factory=list)
    error: str = ""


# ── Utilitaires ──────────────────────────────────────────────────────────────
def _ext(path: str | Path) -> str:
    """Extension en minuscules ; accepte « .jpg » comme « photo.jpg »."""
    p = str(path)
    suffix = Path(p).suffix.lower()
    return suffix or (p.lower() if p.startswith(".") else "")


def path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def chunks(seq: Sequence[Any], size: int = READ_CHUNK) -> Iterator[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def value_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(value_text(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def format_number(x: float) -> str:
    """48.85 → « 48.85 », 35.0 → « 35 »."""
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _one_line(value: str) -> str:
    """Un fichier d'arguments ExifTool est une valeur par ligne : les retours à la ligne deviennent des espaces."""
    return " ".join(str(value).splitlines())


# ── Groupes modifiables / formats ────────────────────────────────────────────
def is_group_editable(group: str) -> bool:
    return bool(_EDITABLE_GROUP_RE.match(group.strip()))


def group_refusal(group: str) -> str | None:
    if is_group_editable(group):
        return None
    return (f"Le groupe « {group} » est en lecture seule : seules les familles EXIF, IPTC et XMP "
            "sont modifiables.")


def entry_refusal(entry: TagEntry) -> str | None:
    r = group_refusal(entry.group)
    if r:
        return r
    if entry.value.startswith(BINARY_PREFIX):
        return "Valeur binaire : non modifiable ici."
    return None


def write_refusal(path: str | Path) -> str | None:
    if _ext(path) in MATROSKA_EXTENSIONS:
        nom = Path(str(path)).name
        prefixe = f"{nom} : " if nom and not str(path).startswith(".") else ""
        return f"{prefixe}format non modifiable ici (WebM / Matroska)."
    return None


# ── Lecture ──────────────────────────────────────────────────────────────────
def parse_entries(record: dict) -> list[TagEntry]:
    """Transforme un enregistrement JSON d'ExifTool (-G1) en liste de TagEntry."""
    out: list[TagEntry] = []
    for key, val in record.items():
        if key == "SourceFile":
            continue
        group, _, name = str(key).partition(":")
        if not name:
            group, name = "", group
        name = _DUP_SUFFIX.sub("", name)
        text = value_text(val)
        items = tuple(value_text(x) for x in val) if isinstance(val, list) else ()
        writable = is_group_editable(group) and not text.startswith(BINARY_PREFIX)
        out.append(TagEntry(group, name, text, writable, items))
    return out


def parse_json_output(text: str) -> list[dict] | None:
    """Sortie `-json` d'ExifTool → liste d'enregistrements, ou None si inexploitable."""
    try:
        data = json.loads((text or "").strip())
    except ValueError:
        return None
    if isinstance(data, list) and all(isinstance(r, dict) for r in data):
        return data
    return None


def match_records(paths: Sequence[Path], records: list[dict]) -> list[dict]:
    """Aligne les enregistrements sur `paths` (par ordre, sinon par SourceFile)."""
    if len(records) == len(paths):
        return list(records)
    index = {path_key(r.get("SourceFile", "")): r for r in records}
    return [index.get(path_key(p), {}) for p in paths]


def write_argfile(lines: Iterable[str]) -> Path:
    """Fichier d'arguments `-@` en UTF-8 (l'appelant le supprime)."""
    fd, name = tempfile.mkstemp(suffix=".args", prefix="azimut_tags_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for line in lines:
                f.write(line + "\n")
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def read_command(exiftool: str | Path, argfile: str | Path, *, numeric: bool = False) -> list[str]:
    cmd = [str(exiftool), "-json", "-G1", "-a", "-s", "-charset", "exiftool=utf8",
           "-charset", "filename=utf8", "-m", "-q"]
    if numeric:                                   # valeurs brutes : instantané fidèle pour l'annulation
        cmd.append("-n")
    return [*cmd, "-@", str(argfile)]


def write_command(exiftool: str | Path, argfile: str | Path, *, numeric: bool = False) -> list[str]:
    cmd = [str(exiftool), "-charset", "exiftool=utf8", "-charset", "filename=utf8", *WRITE_OPTIONS]
    if numeric:
        cmd.append("-n")
    return [*cmd, "-@", str(argfile)]


# ── Écriture ─────────────────────────────────────────────────────────────────
def change_args(changes: Iterable[TagChange]) -> list[str]:
    """`-Tag=valeur` pour « set », `-Tag=` pour « delete »."""
    out: list[str] = []
    for ch in changes:
        if ch.op == "delete":
            out.append(f"-{ch.tag}=")
        elif ch.op == "set":
            out.append(f"-{ch.tag}={_one_line(ch.value)}")
        else:
            raise ValueError(f"Opération inconnue : {ch.op}")
    return out


def gps_image_changes(lat: float, lon: float, alt: float) -> list[TagChange]:
    return [
        TagChange("set", "GPSLatitude", format_number(abs(lat))),
        TagChange("set", "GPSLatitudeRef", "N" if lat >= 0 else "S"),
        TagChange("set", "GPSLongitude", format_number(abs(lon))),
        TagChange("set", "GPSLongitudeRef", "E" if lon >= 0 else "W"),
        TagChange("set", "GPSAltitude", format_number(abs(alt))),
        TagChange("set", "GPSAltitudeRef", "0" if alt >= 0 else "1"),
    ]


def resolve_changes(path_or_ext: str | Path, changes: Iterable[TagChange]) -> list[TagChange]:
    """Adapte les modifications « virtuelles » au format du fichier (GPS image ou QuickTime)."""
    qt = _ext(path_or_ext) in QT_EXTENSIONS
    out: list[TagChange] = []
    for ch in changes:
        low = ch.tag.lower()
        if ch.op == "delete" and low == "gps:all":
            out.append(TagChange("delete", "GPSCoordinates" if qt else "GPS:All"))
        elif ch.op == "set" and low == "gpscoordinates" and not qt:
            out.extend(gps_image_changes(*parse_coordinates_text(ch.value)))
        else:
            out.append(ch)
    return out


def build_tag_args(path_or_ext: str | Path, changes: Iterable[TagChange]) -> list[str]:
    return change_args(resolve_changes(path_or_ext, changes))


def parse_write_result(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    """(succès, message). Le message peut contenir des avertissements même en cas de succès."""
    text = f"{stdout}\n{stderr}"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.lower().startswith("error")]
    warnings = [ln for ln in lines if ln.lower().startswith("warning")]
    if errors:
        return False, errors[0][:300]
    if re.search(r"weren't updated due to errors", text, re.I):
        return False, (warnings[0] if warnings else "écriture refusée par ExifTool")[:300]
    if _WRITE_OK_RE.search(text):
        return True, "; ".join(warnings)[:300]
    if warnings:
        return False, warnings[0][:300]
    return False, (f"aucune écriture (code de sortie {returncode})" if returncode else "aucune écriture effectuée")


def validate_changes(changes: Iterable[TagChange]) -> list[str]:
    """Messages de refus (liste vide si tout est acceptable)."""
    msgs: list[str] = []
    for ch in changes:
        if ch.op not in ("set", "delete"):
            msgs.append(f"Opération inconnue : {ch.op}.")
        elif not _TAG_RE.match(ch.tag):
            msgs.append(f"Nom de tag invalide : {ch.tag!r}.")
        elif ":" in ch.tag:
            r = group_refusal(ch.tag.split(":", 1)[0])
            if r:
                msgs.append(r)
    return list(dict.fromkeys(msgs))


# ── Date et GPS ──────────────────────────────────────────────────────────────
def parse_datetime(text: str, now: datetime | None = None) -> datetime:
    m = _DT_RE.match(text or "")
    if not m:
        raise ValueError("Date invalide : utilisez le format AAAA-MM-JJ HH:MM:SS.")
    try:
        dt = datetime(*(int(x) for x in m.groups()))
    except ValueError:
        raise ValueError("Date invalide : ce jour ou cette heure n'existe pas.") from None
    if dt.year < 1900 or dt > (now or datetime.now()) + timedelta(days=2):
        raise ValueError("Date invalide : elle doit être comprise entre 1900 et aujourd'hui.")
    return dt


def _to_float(v: Any, label: str) -> float:
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} invalide.") from None
    if not math.isfinite(f):
        raise ValueError(f"{label} invalide.")
    return f


def validate_gps(lat: Any, lon: Any, alt: Any = None) -> tuple[float, float, float]:
    la, lo = _to_float(lat, "Latitude"), _to_float(lon, "Longitude")
    al = 0.0 if alt is None or str(alt).strip() == "" else _to_float(alt, "Altitude")
    if abs(la) > 90:
        raise ValueError("La latitude doit être comprise entre -90 et 90.")
    if abs(lo) > 180:
        raise ValueError("La longitude doit être comprise entre -180 et 180.")
    if la == 0.0 and lo == 0.0:
        raise ValueError("La position 0, 0 est refusée (valeur factice).")
    return la, lo, al


def parse_coordinates_text(text: str) -> tuple[float, float, float]:
    """« lat, lon[, alt] » → (lat, lon, alt) validés."""
    parts = [p for p in re.split(r"[,\s]+", (text or "").strip()) if p]
    if len(parts) not in (2, 3):
        raise ValueError("Coordonnées invalides : attendu « latitude, longitude[, altitude] ».")
    return validate_gps(*parts)


def preset_delete_gps() -> list[TagChange]:
    return [TagChange("delete", "GPS:All")]            # résolu par fichier (image / QuickTime)


def preset_delete_all() -> list[TagChange]:
    return [TagChange("delete", "All")]


def preset_set_date(text: str) -> list[TagChange]:
    value = parse_datetime(text).strftime(EXIF_DATE_FORMAT)
    return [TagChange("set", tag, value) for tag in DATE_TAGS]


def preset_set_gps(lat: Any, lon: Any, alt: Any = None) -> list[TagChange]:
    la, lo, al = validate_gps(lat, lon, alt)
    return [TagChange("set", "GPSCoordinates", f"{format_number(la)}, {format_number(lo)}, {format_number(al)}")]


def date_from_changes(changes: Iterable[TagChange]) -> datetime | None:
    """Date de prise de vue écrite par les modifications (pour aligner la date du fichier)."""
    for ch in changes:
        if ch.op == "set" and ch.tag.rsplit(":", 1)[-1].lower() == "datetimeoriginal":
            try:
                return datetime.strptime(ch.value, EXIF_DATE_FORMAT)
            except ValueError:
                return None
    return None


def merge_pending(pending: Sequence[TagChange], new: Iterable[TagChange]) -> list[TagChange]:
    """Ajoute `new` ; une nouvelle modification remplace les anciennes portant sur le même tag."""
    new = list(new)
    tags = {c.tag.lower() for c in new}
    return [c for c in pending if c.tag.lower() not in tags] + new


# ── Instantané et annulation ─────────────────────────────────────────────────
def entry_matches(entry: TagEntry, tag: str) -> bool:
    t = tag.lower()
    if t == "all":
        return is_group_editable(entry.group)
    group, sep, name = t.partition(":")
    if not sep:
        return entry.name.lower() == t and entry.group.lower() not in _SNAPSHOT_SKIP
    if name == "all":
        return entry.group.lower() == group
    return entry.group.lower() == group and entry.name.lower() == name


def _is_wildcard(tag: str) -> bool:
    t = tag.lower()
    return t == "all" or t.endswith(":all")


def snapshot_before(entries: Sequence[TagEntry], changes: Sequence[TagChange]) -> tuple[dict, list[str]]:
    """État précédent des tags concernés : {« Groupe:Nom » : valeur | liste | None (absent)}.

    `entries` doit provenir d'une lecture `-n` (valeurs brutes). Retourne aussi les tags non restaurables (binaires).
    """
    before: dict[str, Any] = {}
    unrestorable: list[str] = []
    for ch in changes:
        matched = [e for e in entries if entry_matches(e, ch.tag)]
        for e in matched:
            spec = e.full_name
            if spec in before or spec in unrestorable:
                continue
            if e.value.startswith(BINARY_PREFIX):
                unrestorable.append(spec)
            else:
                before[spec] = list(e.items) if e.items else e.value
        if not matched and ch.op == "set" and not _is_wildcard(ch.tag):
            before.setdefault(ch.tag, None)           # absent à l'origine : sera supprimé à l'annulation
    return before, unrestorable


def restoration_changes(before: dict) -> list[TagChange]:
    out: list[TagChange] = []
    for tag, val in before.items():
        if val is None or val == []:
            out.append(TagChange("delete", tag))
        elif isinstance(val, list):
            out.extend(TagChange("set", tag, str(v)) for v in val)
        else:
            out.append(TagChange("set", tag, str(val)))
    return out


def snapshot_record(path: Path, mtime: float | None, before: dict, unrestorable: list[str]) -> dict:
    return {"path": str(path), "mtime": mtime, "before": before, "unrestorable": unrestorable}


def undo_record(files: list[dict], changes: Sequence[TagChange], now: datetime | None = None) -> dict:
    return {"version": 1, "timestamp": (now or datetime.now()).isoformat(timespec="seconds"),
            "changes": [{"op": c.op, "tag": c.tag, "value": c.value} for c in changes],
            "files": files}


def new_undo_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S-%f")


def latest_undo_key(keys: Iterable[str]) -> str | None:
    keys = sorted(keys)
    return keys[-1] if keys else None


def keys_to_prune(keys: Iterable[str], keep: int = UNDO_KEEP) -> list[str]:
    keys = sorted(keys)
    return keys[:-keep] if len(keys) > keep else []
