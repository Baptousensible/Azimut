"""Logique pure d'Azimut Takeout (sans Qt) : JSON compagnons de Google Photos, appariement, modifications.

Un export Google Takeout contient, à côté de chaque média, un fichier JSON (« photo.jpg.json » ou
« photo.jpg.supplemental-metadata.json », parfois tronqué) avec la date de prise de vue (``photoTakenTime``,
en UTC) et la position (``geoData``). Ce module apparie médias et JSON, puis prépare les modifications
ExifTool à écrire (réutilise les types d'Azimut Tags).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from azimut.core.fsutil import iter_files
from azimut.modules.tags import engine as tags

UNDO_COLLECTION = "takeout_undo"

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif", ".avif", ".jxl",
    ".dng", ".nef", ".cr2", ".cr3", ".arw", ".orf", ".raf", ".rw2", ".pef", ".srw",
})
VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg",
    ".wmv", ".flv",
})
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# États du tableau
STATE_READY = "Prêt"
STATE_NO_JSON = "Sans JSON"
STATE_NO_DATA = "JSON sans date ni GPS"
STATE_DONE = "Mis à jour"
STATE_UNCHANGED = "Déjà renseigné"
STATE_MTIME_ONLY = "Date du fichier seule"
STATE_ERROR = "Erreur"
STATE_TAGS = {STATE_READY: "info", STATE_NO_JSON: "warn", STATE_NO_DATA: "warn", STATE_DONE: "ok",
              STATE_UNCHANGED: "neutre", STATE_MTIME_ONLY: "ok", STATE_ERROR: "err"}

TZ_LOCAL, TZ_UTC = "local", "utc"
_OFFSET_TAGS = ("OffsetTime", "OffsetTimeOriginal", "OffsetTimeDigitized")

_EDITED_RE = re.compile(r"-(?:edited|modifié|modifie|bearbeitet|editado|modificato|bewerkt)$", re.I)
_DUP_RE = re.compile(r"^(?P<base>.*?)(?P<dup>\(\d+\))$")
_CONVERTED_RE = re.compile(r"^(?P<base>.+)_(?P<ext>[A-Za-z0-9]{2,5})$")
_SUPP = ".supplemental-metadata"
_MIN_TRUNCATED_TITLE = 30        # un titre tronqué par Google est long ; évite les faux appariements courts


def state_tag(state: str) -> str:
    return STATE_TAGS.get(state, "neutre")


def is_media(path: Path | str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS


# ── JSON compagnon ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Sidecar:
    path: Path
    title: str = ""
    taken: datetime | None = None          # aware, UTC
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None


def _timestamp(block: object) -> datetime | None:
    if not isinstance(block, dict):
        return None
    try:
        ts = int(float(block.get("timestamp")))                # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    # Google peut fournir un timestamp négatif (photo scannée avant 1970) ; seul 0 est le marqueur « inconnu ».
    if ts == 0:
        return None
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt


def _gps(block: object) -> tuple[float, float, float | None] | None:
    if not isinstance(block, dict):
        return None
    try:
        lat, lon = float(block.get("latitude")), float(block.get("longitude"))       # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if lat == 0.0 and lon == 0.0:                       # Google écrit 0,0 quand la position est inconnue
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    try:
        alt: float | None = float(block.get("altitude"))                              # type: ignore[arg-type]
    except (TypeError, ValueError):
        alt = None
    if alt is not None and alt != alt:
        alt = None
    return lat, lon, alt


def parse_sidecar(data: object, path: Path) -> Sidecar | None:
    """Sidecar d'après le contenu JSON ; None si le document n'est pas un JSON Google Photos."""
    if not isinstance(data, dict):
        return None
    taken = _timestamp(data.get("photoTakenTime")) or _timestamp(data.get("creationTime"))
    gps = _gps(data.get("geoData")) or _gps(data.get("geoDataExif"))
    title = data.get("title")
    return Sidecar(path=path, title=title if isinstance(title, str) else "", taken=taken,
                   lat=gps[0] if gps else None, lon=gps[1] if gps else None, alt=gps[2] if gps else None)


def load_sidecar(path: Path) -> Sidecar | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return parse_sidecar(json.load(fh), path)
    except (OSError, ValueError, RecursionError):     # JSON trop imbriqué : illisible, jamais fatal
        return None


# ── Appariement ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _JsonName:
    path: Path
    title: str          # nom du média d'origine (éventuellement tronqué)
    dup: str            # « (1) » ou ""


def _json_name(path: Path) -> _JsonName | None:
    name = path.name
    if not name.lower().endswith(".json"):
        return None
    stem = name[:-5]
    dup = ""
    m = re.search(r"(\(\d+\))$", stem)
    if m:
        dup, stem = m.group(1), stem[:m.start()]
    # suffixe « .supplemental-metadata » complet ou tronqué par Google
    m = re.search(r"\.(s[a-z\-]*)$", stem)
    if m and _SUPP[1:].startswith(m.group(1)):
        stem = stem[:m.start()]
    if not stem:
        return None
    return _JsonName(path, stem, dup)


def name_key(text: str) -> str:
    """Clé de comparaison d'un nom : Unicode NFC puis minuscules (AZ-C10).

    Un « é » s'écrit en un caractère (NFC, Google Takeout, Windows) ou en deux, « e » + accent combinant (NFD,
    HFS+ et certains outils d'extraction sous macOS et Linux). Deux fichiers qui s'affichent à l'identique
    peuvent donc porter des noms différents octet pour octet : sans normalisation, le JSON d'une photo « été.jpg »
    n'était pas trouvé.
    """
    return unicodedata.normalize("NFC", text).lower()


class DirIndex:
    """JSON d'un dossier, indexés par titre (nom d'origine) et par « radical » (titre sans extension)."""

    def __init__(self, folder: Path, names: Iterable[str] | None = None,
                 media_names: Iterable[str] | None = None) -> None:
        self.by_title: dict[str, list[_JsonName]] = {}
        self.by_stem: dict[str, list[_JsonName]] = {}
        self.used: set[Path] = set()
        self._content_titles: dict[Path, str] = {}
        if names is None:
            listing = _listdir(folder)                      # un seul parcours : JSON et médias du dossier
            entries = [n for n in listing if n.lower().endswith(".json")]
            media_names = [n for n in listing if is_media(n)]
        else:
            entries = names
        self._media_keys = {name_key(n) for n in (media_names or ())}
        for n in entries:
            jn = _json_name(folder / n)
            if jn is None:
                continue
            self.by_title.setdefault(name_key(jn.title), []).append(jn)
            self.by_stem.setdefault(name_key(Path(jn.title).stem), []).append(jn)
        self.titles = sorted(self.by_title)

    def _pick(self, found: Sequence[_JsonName], dup: str) -> Path | None:
        paths = {jn.path for jn in found if jn.dup == dup}
        return paths.pop() if len(paths) == 1 else None

    def _content_title(self, js: Path) -> str:
        """Titre écrit DANS le JSON (nom d'origine complet du média), lu une seule fois ; « » s'il manque."""
        if js not in self._content_titles:
            sc = load_sidecar(js)
            self._content_titles[js] = sc.title if sc is not None else ""
        return self._content_titles[js]

    def _belongs_to_sibling(self, js: Path, media_name: str) -> bool:
        """Vrai si le titre complet écrit dans `js` désigne un AUTRE média du dossier que `media_name`.

        Google tronque le nom du JSON à 46 caractères : deux médias dont le nom commence pareil visent le même JSON
        tronqué. Le titre complet, lui, est dans le contenu : s'il nomme exactement un voisin, le JSON est le sien et
        ne doit pas donner sa date et sa position à celui-ci (LOG-008). Sans titre, ou si le titre ne nomme aucun
        média présent (fichier renommé, déplacé), l'appariement par préfixe reste celui d'origine.
        """
        if not self._media_keys:
            return False
        title = self._content_title(js)
        if not title:
            return False
        key = name_key(title)
        own = {name_key(t) for t, _dup in candidates(media_name)}
        return key not in own and key in self._media_keys

    def find(self, media: Path) -> Path | None:
        """Meilleur JSON pour `media`, ou None. Voir `candidates` pour les noms essayés."""
        for title, dup in candidates(media.name):
            hit = self._pick(self.by_title.get(name_key(title), ()), dup)
            if hit:
                return hit
        # titre tronqué par Google : le nom du JSON est un préfixe long du titre recherché
        for title, dup in candidates(media.name):
            low = name_key(title)
            for t in self.titles:
                if len(t) >= _MIN_TRUNCATED_TITLE and low.startswith(t) and low != t:
                    hit = self._pick(self.by_title[t], dup)
                    if hit and not self._belongs_to_sibling(hit, media.name):
                        return hit
        # fichier converti sans extension d'origine (« photo.avif », « clip.webm »)
        stem = media.stem
        for base, dup in _stem_variants(stem):
            hit = self._pick(self.by_stem.get(name_key(base), ()), dup)
            if hit:
                return hit
        return None


def _listdir(folder: Path) -> list[str]:
    try:
        return os.listdir(folder)
    except OSError:
        return []


def _list_names(folder: Path) -> list[str]:
    return [n for n in _listdir(folder) if n.lower().endswith(".json")]


def _stem_variants(stem: str) -> list[tuple[str, str]]:
    """(radical, doublon) possibles pour un nom sans extension : « IMG(1) » → (« IMG », « (1) »)."""
    out = [(stem, "")]
    m = _DUP_RE.match(stem)
    if m and m.group("base"):
        out.insert(0, (m.group("base"), m.group("dup")))
    return out


def candidates(media_name: str) -> list[tuple[str, str]]:
    """Titres d'origine plausibles (titre, doublon) pour un nom de média, du plus au moins probable.

    Gère « photo-edited.jpg », « photo(1).jpg » (JSON « photo.jpg(1).json ») et le nommage de conversion
    « photo_jpg.avif » → « photo.jpg ».
    """
    p = Path(media_name)
    stem, ext = p.stem, p.suffix
    out: list[tuple[str, str]] = []

    def add(title: str, dup: str = "") -> None:
        if (title, dup) not in out:
            out.append((title, dup))

    bases: list[tuple[str, str, str]] = []                  # (radical, extension, doublon)
    m = _DUP_RE.match(stem)
    if m and m.group("base"):
        bases.append((m.group("base"), ext, m.group("dup")))
    bases.append((stem, ext, ""))
    for base, e, dup in list(bases):
        stripped = _EDITED_RE.sub("", base)
        if stripped != base:
            bases.insert(0, (stripped, e, dup))
    for base, e, dup in bases:
        add(base + e, dup)
        conv = _CONVERTED_RE.match(base)
        if conv and f".{conv.group('ext').lower()}" in MEDIA_EXTS:
            add(f"{conv.group('base')}.{conv.group('ext')}", dup)
    return out


# ── Analyse d'un dossier ─────────────────────────────────────────────────────
@dataclass
class TakeoutItem:
    path: Path
    sidecar: Sidecar | None = None
    state: str = STATE_NO_JSON
    message: str = ""


@dataclass
class Analysis:
    items: list[TakeoutItem] = field(default_factory=list)
    orphan_json: int = 0

    def count(self, state: str) -> int:
        return sum(1 for i in self.items if i.state == state)


def analyze(folder: Path, *, recursive: bool = True, cancel: Callable[[], bool] | None = None) -> Analysis:
    """Parcourt `folder`, apparie chaque média à son JSON et lit celui-ci (aucune écriture)."""
    folder = Path(folder)
    result = Analysis()
    by_dir: dict[Path, list[Path]] = {}
    json_dirs: set[Path] = set()                  # dossiers où l'on a vu un JSON, même sans média (LOG-009)
    for f in iter_files(folder, recursive=recursive, extensions=MEDIA_EXTS | {".json"}, skip_hidden=True,
                        cancel=cancel):
        if f.suffix.lower() == ".json":
            json_dirs.add(f.parent)
        else:
            by_dir.setdefault(f.parent, []).append(f)
    used: set[Path] = set()
    for d in sorted(by_dir, key=lambda x: str(x).lower()):
        if cancel is not None and cancel():
            break
        index = DirIndex(d)
        for media in sorted(by_dir[d], key=lambda x: x.name.lower()):
            item = TakeoutItem(media)
            js = index.find(media)
            if js is None:
                item.message = "Aucun JSON compagnon trouvé."
            else:
                used.add(js)
                sc = load_sidecar(js)
                if sc is None:
                    item.message = f"JSON illisible : {js.name}"
                elif sc.taken is None and not sc.has_gps:
                    item.sidecar = sc
                    item.state = STATE_NO_DATA
                    item.message = "Le JSON ne contient ni date ni position."
                else:
                    item.sidecar, item.state = sc, STATE_READY
            result.items.append(item)
    total_json = 0
    for d in by_dir.keys() | json_dirs:
        total_json += len(_list_names(d))
    result.orphan_json = max(0, total_json - len(used))
    return result


# ── Modifications à écrire ───────────────────────────────────────────────────
_DISK_MARGIN = 16 * 1024 * 1024      # espace libre exigé en plus de la taille du fichier


def write_space_problem(path: Path) -> str | None:
    """Raison courte qui empêche la réécriture de `path`, ou None si elle est possible.

    ExifTool réécrit le fichier via une copie temporaire dans le même dossier : sans cet espace,
    l'échec tombe au milieu du lot avec un message opaque. Un dossier non inscriptible est refusé
    d'emblée, avant toute écriture.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return "Fichier introuvable."
    if not os.access(path.parent, os.W_OK):
        return "Dossier en lecture seule : aucune écriture."
    try:
        free = shutil.disk_usage(path.parent).free
    except OSError:
        return None                     # non mesurable : ExifTool tranchera
    if free < size + _DISK_MARGIN:
        return "Espace disque insuffisant pour réécrire le fichier."
    return None


@dataclass(frozen=True)
class Options:
    write_dates: bool = True
    write_gps: bool = True
    overwrite: bool = False             # écraser les valeurs déjà présentes dans le fichier
    align_mtime: bool = True            # date de modification du fichier = date de prise de vue
    tz: str = TZ_UTC                    # fuseau des dates EXIF des images : UTC (reproductible) ou heure locale
    backup: bool = False                # conserver l'original sous « nom.ext_original » avant écriture


@dataclass
class Plan:
    changes: list[tags.TagChange] = field(default_factory=list)
    mtime: datetime | None = None       # date absolue à donner au fichier (aware)
    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.changes and self.mtime is None


_ZERO_DATE_RE = re.compile(r"^[0:\-\s T]*$")
_IMAGE_DATE_TAGS = ("DateTimeOriginal", "CreateDate", "ModifyDate")
_VIDEO_DATE_TAGS = ("CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate",
                    "MediaCreateDate", "MediaModifyDate")


def _has(entries: Iterable[tags.TagEntry], names: Iterable[str]) -> bool:
    wanted = {n.lower() for n in names}
    for e in entries:
        if e.name.lower() in wanted and e.value.strip() and not _ZERO_DATE_RE.match(e.value):
            return True
    return False


def local_naive(dt: datetime) -> datetime:
    """Heure locale du système, sans fuseau."""
    return dt.astimezone().replace(tzinfo=None)


def utc_offset_text(dt: datetime, tz: str) -> str:
    """Décalage « +HH:MM » écrit dans OffsetTime* : « +00:00 » en UTC, décalage du système en heure locale."""
    if tz == TZ_UTC:
        return "+00:00"
    off = dt.astimezone().utcoffset()
    minutes = int(off.total_seconds() // 60) if off is not None else 0
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def build_plan(path: Path, sidecar: Sidecar, entries: Sequence[tags.TagEntry], options: Options) -> Plan:
    """Ce qu'il faut écrire dans `path` d'après son JSON, ses tags actuels (`entries`) et les options."""
    plan = Plan()
    ext = path.suffix.lower()
    writable = tags.write_refusal(path) is None
    is_qt = ext in tags.QT_EXTENSIONS
    if options.align_mtime and sidecar.taken is not None:
        plan.mtime = sidecar.taken
    if not writable:
        if plan.mtime is not None:
            plan.notes.append("Format non modifiable ici : seule la date du fichier est mise à jour.")
        return plan
    if options.write_dates and sidecar.taken is not None:
        if is_qt:
            present = _has(entries, ("CreateDate", "MediaCreateDate", "TrackCreateDate"))
            moment = sidecar.taken.astimezone(timezone.utc).replace(tzinfo=None)      # QuickTime = UTC
            names = _VIDEO_DATE_TAGS
        else:
            present = _has(entries, ("DateTimeOriginal",))
            moment = (sidecar.taken.astimezone(timezone.utc).replace(tzinfo=None) if options.tz == TZ_UTC
                      else local_naive(sidecar.taken))
            names = _IMAGE_DATE_TAGS
        offset = None if is_qt else utc_offset_text(sidecar.taken, options.tz)
        if present and not options.overwrite:
            plan.notes.append("Date déjà présente : conservée.")
        else:
            value = moment.strftime(tags.EXIF_DATE_FORMAT)
            missing = names if options.overwrite else tuple(n for n in names if not _has(entries, (n,)))
            plan.changes += [tags.TagChange("set", n, value) for n in missing]
            if len(missing) < len(names):
                plan.notes.append("Certaines dates déjà présentes : conservées.")
            if offset:      # le fuseau est écrit explicitement : la date reste exacte quel que soit l'ordinateur
                offsets = _OFFSET_TAGS if options.overwrite else tuple(n for n in _OFFSET_TAGS
                                                                      if not _has(entries, (n,)))
                plan.changes += [tags.TagChange("set", n, offset) for n in offsets]
    if options.write_gps and sidecar.has_gps:
        present = _has(entries, ("GPSLatitude", "GPSCoordinates", "GPSPosition"))
        if present and not options.overwrite:
            plan.notes.append("Position déjà présente : conservée.")
        else:
            la, lo, al = tags.validate_gps(sidecar.lat, sidecar.lon, sidecar.alt)
            plan.changes += tags.preset_set_gps(la, lo, al)
    return plan


def describe(sidecar: Sidecar | None, options: Options) -> tuple[str, str]:
    """(texte date, texte GPS) affichés dans le tableau."""
    if sidecar is None:
        return "", ""
    date = ""
    if sidecar.taken is not None:
        moment = (sidecar.taken.astimezone(timezone.utc).replace(tzinfo=None) if options.tz == TZ_UTC
                  else local_naive(sidecar.taken))
        date = moment.strftime("%Y-%m-%d %H:%M:%S")
    gps = f"{sidecar.lat:.5f}, {sidecar.lon:.5f}" if sidecar.has_gps else ""
    return date, gps
