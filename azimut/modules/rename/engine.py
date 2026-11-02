"""Moteur d'Azimut Rename : date de prise de vue → nom de fichier (logique pure, sans Qt).

Nom final : ``<IMG|VID|AUD>_<préfixe personnalisé>_<date>.<ext>``, par exemple
« IMG_Vacances Corse_2008-06-15_14-30-00.jpg » (sans préfixe : « IMG_2008-06-15_14-30-00.jpg »).
L'acronyme dépend du type réel du fichier ; les noms d'extension collés à l'ancien nom (« _jpg », « .mp4 »)
sont retirés et jamais gardés en préfixe. Un renommage ne modifie ni le contenu, ni les métadonnées, ni la date
de modification du fichier.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from azimut.core.fsutil import iter_files

UNDO_COLLECTION = "rename_undo"

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".tif", ".tiff", ".webp", ".bmp",
                        ".jp2", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".gif"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp", ".3g2", ".m4v", ".mts", ".m2ts", ".vob",
                        ".vro", ".mpg", ".mpeg", ".flv", ".wmv", ".ts", ".mxf", ".asf", ".ogv"})
AUDIO_EXTS = frozenset({".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".amr", ".wma", ".mka", ".aiff"})
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS

ACRONYM_IMAGE, ACRONYM_VIDEO, ACRONYM_AUDIO = "IMG", "VID", "AUD"

# Tags de date lus, par ordre de priorité (le premier valide fait foi)
DATE_TAGS = ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate", "DateUTC")
_EXIF_DATE_RE = re.compile(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
FAKE_DATES = frozenset({date(1970, 1, 1), date(1969, 12, 31), date(1904, 1, 1)})
READ_CHUNK = 100

DATE_FORMATS: dict[str, tuple[str, str]] = {
    "full": ("AAAA-MM-JJ_HH-MM-SS   (2008-06-15_14-30-00)", "%Y-%m-%d_%H-%M-%S"),
    "compact": ("AAAAMMJJ_HHMMSS   (20080615_143000)", "%Y%m%d_%H%M%S"),
    "day": ("AAAA-MM-JJ   (jour seul, 2008-06-15)", "%Y-%m-%d"),
}
DEFAULT_FORMAT = "full"

STATUS_RENAME = "À renommer"
STATUS_OK = "Déjà conforme"
STATUS_SKIPPED = "Ignoré (décoché)"
STATUS_NO_DATE = "Sans date — ignoré"
SOURCE_META = "métadonnées"
SOURCE_MTIME = "date de modification (à vérifier)"
TAG_BY_STATUS = {STATUS_RENAME: "ok", STATUS_OK: "info", STATUS_SKIPPED: "", STATUS_NO_DATE: "err"}

TEMP_PREFIX = ".azimut_tmp_"
_PARASITE_PREFIXES = ("._", TEMP_PREFIX)


# ── Lecture des dates (ExifTool) ──────────────────────────────────────

def read_command(exiftool: str | Path, argfile: str | Path) -> list[str]:
    """Commande de lecture des seuls tags de date, pour une liste de fichiers (fichier d'arguments UTF-8)."""
    return [str(exiftool), "-json", "-m", "-q", "-charset", "filename=utf8", "-charset", "exiftool=utf8",
            *[f"-{t}" for t in DATE_TAGS], "-@", str(argfile)]


def parse_json_records(text: str) -> list[dict] | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, list) and all(isinstance(r, dict) for r in data) else None


def date_from_record(record: dict[str, Any]) -> datetime | None:
    """Première date valide parmi DATE_TAGS ; None si absente, illisible ou factice (1970, 1904…)."""
    for tag in DATE_TAGS:
        value = record.get(tag)
        if not value:
            continue
        m = _EXIF_DATE_RE.search(str(value))
        if not m:
            continue
        try:
            d = datetime(*(int(x) for x in m.groups()))
        except ValueError:
            continue
        if d.year < 1900 or d.date() in FAKE_DATES:
            continue
        return d
    return None


# ── Préfixe personnalisé ──────────────────────────────────────────────

# Fin de nom déjà au format produit par Azimut (idempotence, compteur _2 inclus)
_NAME_END = re.compile(r"[\s_\-]*(?:\d{4}-\d{2}-\d{2}(?:_\d{2}-\d{2}-\d{2})?|\d{8}_\d{6})(?:_\d{1,3})?$")
_EXT_NAMES = sorted((e[1:] for e in SUPPORTED_EXTS), key=len, reverse=True)
# Nom d'extension resté collé à la fin d'un ancien nom : « …_jpg », « … mp4 », « photo.jpeg »
_TRAILING_EXT = re.compile(r"[\s_.\-]+(?:" + "|".join(_EXT_NAMES) + r")$", re.IGNORECASE)
_TOKENS = [
    re.compile(r"(?<!\d)\d{4}[-_.]\d{2}[-_.]\d{2}[ _T-]\d{2}[-_.:]\d{2}[-_.:]\d{2}(?!\d)"),
    re.compile(r"(?<!\d)\d{2}[-_.]\d{2}[-_.]\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{4}[-_.]\d{2}[-_.]\d{2}(?!\d)"),
    re.compile(r"(?<!\d)\d{2}[.:]\d{2}[.:]\d{2}(?!\d)"),
    re.compile(r"(?<![A-Za-z])WA\d+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])Screenshot(?:\s+at)?(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])Capture\s+d[’']?[ée]cran(?:\s+à)?(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])(?:FB_IMG|IMGP|IMG|VID|MOV|MVI|AUD|PTT|DSCN|DSCF|DSC|PXL|PANO|MVIMG|BURST|signal)"
               r"(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])P(?=\d{6,7}(?!\d))"),
    re.compile(r"\d{4,}"),                      # compteurs d'appareil, epoch, années isolées…
    re.compile(r"\(\d+\)"),                     # « (1) » des doublons
]
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_prefix(text: str) -> str:
    """Rend un préfixe utilisable comme début de nom de fichier (tous systèmes)."""
    s = _ILLEGAL.sub("_", text or "")
    s = re.sub(r"\s{2,}", " ", s).strip(" _-.,")
    if not any(c.isalnum() for c in s):
        return ""
    return s[:100].rstrip(" .")


def extract_prefix(stem: str) -> str:
    """Partie « personnalisée » d'un nom : ce qui reste une fois retirés dates et noms génériques d'appareils."""
    s = stem
    while True:                                   # retire dates déjà posées et noms d'extension en fin de nom
        new = _TRAILING_EXT.sub("", _NAME_END.sub("", s))
        if new == s:
            break
        s = new
    if s.lower() in _EXT_NAMES:
        return ""
    for rx in _TOKENS:
        s = rx.sub(" ", s)
    s = re.sub(r"_{2,}", "_", s)
    if re.fullmatch(r"[\d\s_\-.]*", s):          # il ne reste qu'un compteur (« IMG_12 », « IMG_001 »)
        return ""
    return sanitize_prefix(s)


def type_acronym(ext: str) -> str:
    """IMG / VID / AUD selon l'extension réelle du fichier."""
    ext = ext.lower()
    if ext in VIDEO_EXTS:
        return ACRONYM_VIDEO
    if ext in AUDIO_EXTS:
        return ACRONYM_AUDIO
    return ACRONYM_IMAGE


def build_base(prefix: str, when: datetime, fmt: str, acronym: str = "") -> str:
    """[ACRONYME_][préfixe_]date"""
    return "_".join(p for p in (acronym, prefix, when.strftime(fmt)) if p)


# ── Lignes et calcul des nouveaux noms ────────────────────────────────

@dataclass
class RenameRow:
    path: Path
    old_name: str
    date_meta: datetime | None = None
    date_mtime: datetime | None = None
    auto_prefix: str = ""
    prefix: str = ""
    checked: bool = True
    date: datetime | None = None
    source: str = ""
    new_name: str = ""
    status: str = ""
    base: str = field(default="", repr=False)
    ext: str = field(default="", repr=False)

    @property
    def folder(self) -> Path:
        return self.path.parent

    @property
    def new_path(self) -> Path:
        return self.folder / (self.new_name or self.old_name)


@dataclass(frozen=True)
class Options:
    fmt: str = DEFAULT_FORMAT
    keep_prefix: bool = True
    add_type: bool = True
    use_mtime: bool = False


def compute_names(rows: Sequence[RenameRow], entries: dict[str, set[str]], options: Options) -> None:
    """Met à jour en place date, source, new_name et status de chaque ligne.

    Les collisions (même nom dans le même dossier) reçoivent un compteur _2, _3… ; un nom déjà pris par un
    fichier qui ne bouge pas n'est jamais écrasé. ``entries`` : noms (en minuscules) présents par dossier.
    """
    fmt = DATE_FORMATS.get(options.fmt, DATE_FORMATS[DEFAULT_FORMAT])[1]
    by_folder: dict[str, list[RenameRow]] = defaultdict(list)
    for r in rows:
        r.new_name = r.old_name
        if r.date_meta:
            r.date, r.source = r.date_meta, SOURCE_META
        elif options.use_mtime and r.date_mtime:
            r.date, r.source = r.date_mtime, SOURCE_MTIME
        else:
            r.date, r.source, r.status = None, "", STATUS_NO_DATE
            continue
        r.ext = Path(r.old_name).suffix
        prefix = sanitize_prefix(r.prefix) if options.keep_prefix else ""
        acronym = type_acronym(r.ext) if options.add_type else ""
        r.base = build_base(prefix, r.date, fmt, acronym)
        if re.fullmatch(re.escape(r.base) + r"(?:_\d{1,3})?" + re.escape(r.ext), r.old_name):
            r.status = STATUS_OK                    # déjà conforme : nom identique, ou identique + compteur _N
        elif not r.checked:
            r.status = STATUS_SKIPPED
        else:
            r.status = STATUS_RENAME
            by_folder[str(r.folder)].append(r)

    for folder, todo in by_folder.items():
        taken = set(entries.get(folder, ()))
        for r in todo:                              # ces fichiers vont libérer leur nom
            taken.discard(r.old_name.lower())
        for r in sorted(todo, key=lambda x: (x.date or datetime.min, x.old_name.lower())):
            n, name = 1, r.base + r.ext
            while name.lower() in taken:
                n += 1
                name = f"{r.base}_{n}{r.ext}"
            taken.add(name.lower())
            r.new_name = name
            if name == r.old_name:
                r.status = STATUS_OK


# ── Inventaire ────────────────────────────────────────────────────────

@dataclass
class Inventory:
    files: list[Path] = field(default_factory=list)
    ignored: dict[str, int] = field(default_factory=dict)        # extension → nombre
    parasites: int = 0


def list_files(folder: Path, recursive: bool = True, cancel: Callable[[], bool] | None = None) -> Inventory:
    inv = Inventory()
    for path in iter_files(folder, recursive=recursive, extensions=None, skip_hidden=False, cancel=cancel):
        if path.name.startswith(_PARASITE_PREFIXES):
            inv.parasites += 1
        elif path.suffix.lower() in SUPPORTED_EXTS:
            inv.files.append(path)
        else:
            key = path.suffix.lower() or "(sans extension)"
            inv.ignored[key] = inv.ignored.get(key, 0) + 1
    inv.files.sort()
    return inv


def folder_entries(rows: Iterable[RenameRow]) -> dict[str, set[str]]:
    """Noms (minuscules) présents dans chaque dossier concerné, tous types de fichiers confondus."""
    entries: dict[str, set[str]] = {}
    for r in rows:
        key = str(r.folder)
        if key not in entries:
            try:
                entries[key] = {n.lower() for n in os.listdir(r.folder)}
            except OSError:
                entries[key] = set()
    return entries


def make_row(path: Path, meta: datetime | None) -> RenameRow:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
    except (OSError, OverflowError, ValueError):
        mtime = None
    prefix = extract_prefix(path.stem)
    return RenameRow(path=path, old_name=path.name, date_meta=meta, date_mtime=mtime, auto_prefix=prefix,
                     prefix=prefix, checked=meta is not None, new_name=path.name)


# ── Exécution sûre des renommages ─────────────────────────────────────

def resolve_path(path: Path) -> Path | None:
    """Le chemin s'il existe (essai NFC/NFD pour les accents), sinon None."""
    if path.exists():
        return path
    for form in ("NFC", "NFD"):
        alt = Path(unicodedata.normalize(form, str(path)))
        if alt != path and alt.exists():
            return alt
    return None


def _noop_log(_level: str, _text: str) -> None: ...


def _noop_progress(_done: int, _total: int, _name: str) -> None: ...


def execute_renames(pairs: Sequence[tuple[int, Path, Path]], *, cancel: Callable[[], bool] = lambda: False,
                    log: Callable[[str, str], None] = _noop_log,
                    progress: Callable[[int, int, str], None] = _noop_progress) -> dict[int, tuple[bool, str]]:
    """Renomme sans jamais écraser. ``pairs`` : (id, source, destination), destination dans le même dossier.

    Si la destination est occupée par un fichier qui doit lui-même être renommé (chaîne, permutation), on passe
    par un nom temporaire ; une situation sans issue remet les noms d'origine. Retourne {id: (ok, erreur)}.
    """
    results: dict[int, tuple[bool, str]] = {}
    waiting: list[list] = []                       # [id, source, destination, passé par un temporaire ?, origine]
    for idx, src, dst in pairs:
        resolved = resolve_path(src)
        if resolved is None:
            results[idx] = (False, "fichier introuvable")
            log("ERR", f"Introuvable : {src}")
            continue
        waiting.append([idx, resolved, dst, False, resolved])
    total, done = len(waiting), 0

    def finish(idx: int, ok: bool, err: str, name: str) -> None:
        nonlocal done
        results[idx] = (ok, err)
        done += 1
        progress(done, total, name)

    while waiting and not cancel():
        remaining, advanced = [], False
        for it in waiting:
            if cancel():
                remaining.append(it)
                continue
            idx, src, dst = it[0], it[1], it[2]
            if os.path.lexists(dst) or str(src).lower() == str(dst).lower():
                remaining.append(it)               # destination occupée / simple changement de casse
                continue
            try:
                os.rename(src, dst)
                log("OK", f"{it[4].name}  →  {dst.name}")
                finish(idx, True, "", dst.name)
            except OSError as exc:
                log("ERR", f"{it[4].name} : {exc}")
                finish(idx, False, str(exc), it[4].name)
            advanced = True
        waiting = remaining
        if waiting and not advanced and not cancel():
            candidate = next((it for it in waiting if not it[3]), None)
            if candidate is None:                  # tout est déjà passé par un temporaire : situation bloquée
                for it in waiting:
                    try:
                        os.rename(it[1], it[4])    # on remet le nom d'origine
                    except OSError:
                        pass
                    log("ERR", f"{it[4].name} : destination déjà occupée ({it[2].name})")
                    finish(it[0], False, "destination déjà occupée", it[4].name)
                waiting = []
                break
            tmp = candidate[1].with_name(f"{TEMP_PREFIX}{uuid.uuid4().hex[:10]}{candidate[1].suffix}")
            try:
                os.rename(candidate[1], tmp)
                candidate[1], candidate[3] = tmp, True
            except OSError as exc:
                waiting.remove(candidate)
                log("ERR", f"{candidate[4].name} : {exc}")
                finish(candidate[0], False, str(exc), candidate[4].name)
    for it in waiting:                             # arrêt demandé : on remet les temporaires
        if it[3]:
            try:
                os.rename(it[1], it[4])
            except OSError:
                pass
    return results


# ── Journal d'annulation ──────────────────────────────────────────────

def undo_record(pairs: Sequence[tuple[Path, Path]], timestamp: str) -> dict:
    """Enregistrement stockable : (ancien chemin, nouveau chemin) de chaque renommage prévu ou effectué."""
    return {"timestamp": timestamp, "pairs": [{"old": str(o), "new": str(n)} for o, n in pairs]}


def plan_undo(record: dict) -> tuple[list[tuple[int, Path, Path]], list[str]]:
    """Renommages inverses (id, nouveau → ancien), du dernier au premier, et liste des paires ignorées.

    Une paire est ignorée quand le « nouveau » fichier n'existe pas (le renommage n'a pas eu lieu, ou le fichier
    a été déplacé depuis) : on ne touche jamais à ce qui n'est pas là.
    """
    plan: list[tuple[int, Path, Path]] = []
    skipped: list[str] = []
    entries = [e for e in record.get("pairs", []) if isinstance(e, dict) and e.get("old") and e.get("new")]
    for i, entry in enumerate(reversed(entries)):
        old, new = Path(entry["old"]), Path(entry["new"])
        if resolve_path(new) is None:
            skipped.append(new.name)
            continue
        plan.append((i, new, old))
    return plan, skipped


def plan_csv_lines(rows: Iterable[RenameRow]) -> list[list[str]]:
    header = ["chemin", "ancien_nom", "nouveau_nom", "date", "source", "statut"]
    out = [header]
    for r in rows:
        out.append([str(r.path), r.old_name, r.new_name, r.date.isoformat(sep=" ") if r.date else "",
                    r.source, r.status])
    return out
