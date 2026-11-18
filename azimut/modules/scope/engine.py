"""Moteur d'Azimut Scope : comparaison de deux arborescences, dossier par dossier (lecture seule, sans Qt).

Pour chaque dossier (chemin relatif à la racine), on compte les fichiers retenus par le filtre choisi. Le filtre
historique ignore les fichiers texte, JSON, scripts et sous-titres ; l'interface peut aussi compter tous les types
de fichiers ou seulement une famille (images, vidéos, audio, documents). Les éléments cachés et les fichiers
système restent toujours écartés. On vérifie ainsi qu'une copie ou une conversion a produit les mêmes fichiers,
sans rien modifier.
"""
from __future__ import annotations

import os
import platform
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_EXTS = frozenset({
    ".txt", ".md", ".log", ".csv", ".ini", ".json", ".xml", ".yaml", ".yml", ".cfg", ".conf", ".bak", ".html",
    ".css", ".js", ".sh", ".py", ".rst", ".toml", ".nfo", ".srt", ".vtt", ".sub", ".tex",
})
SYSTEM_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store", ".localized"})

# Filtres de fichiers proposés dans la fenêtre. La valeur « default » conserve le comportement historique
# du moteur (tous les fichiers sauf documents, code, sous-titres et fichiers système).
FILE_KIND_DEFAULT = "default"
FILE_KIND_ALL = "all"
FILE_KIND_MEDIA = "media"
FILE_KIND_IMAGES = "images"
FILE_KIND_VIDEOS = "videos"
FILE_KIND_AUDIO = "audio"
FILE_KIND_DOCUMENTS = "documents"
FILE_KIND_OTHER = "other"                 # extensions inconnues, fichiers sans suffixe
FILE_KINDS = frozenset({
    FILE_KIND_DEFAULT, FILE_KIND_ALL, FILE_KIND_MEDIA, FILE_KIND_IMAGES,
    FILE_KIND_VIDEOS, FILE_KIND_AUDIO, FILE_KIND_DOCUMENTS, FILE_KIND_OTHER,
})

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg",
    ".heic", ".heif", ".avif", ".jxl", ".jp2", ".j2k", ".qoi", ".tga", ".psd", ".mpo", ".raw",
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw",
    ".x3f", ".3fr", ".mef", ".rwl", ".iiq",
})
VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp", ".3g2", ".m4v", ".mts", ".m2ts", ".vob",
    ".vro", ".mpg", ".mpeg", ".m2v", ".flv", ".f4v", ".wmv", ".ts", ".mxf", ".asf", ".ogv",
})
AUDIO_EXTS = frozenset({
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".amr", ".wma", ".mka", ".aiff",
    ".aif", ".ac3", ".mp2", ".wv",
})
DOCUMENT_EXTS = EXCLUDED_EXTS | frozenset({
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp",
    ".epub", ".mobi", ".azw3",
})

STATE_EQUAL = "Identique"
STATE_DIFF = "Différent"
STATE_ONLY_A = "Absent de la fille"
STATE_ONLY_B = "Absent de la mère"
# Un dossier absent d'un côté est signalé comme une différence : rouge, au même titre qu'un écart.
STATE_TAGS = {STATE_EQUAL: "ok", STATE_DIFF: "err", STATE_ONLY_A: "err", STATE_ONLY_B: "err"}


def clean_path(raw: str) -> str:
    """Nettoie un chemin glissé-déposé : accolades, guillemets, file:// (avec %XX).

    Le décodage des pourcentages n'est appliqué **qu'aux URL** ``file://`` : un dossier nommé « 100%20Mix »
    doit rester « 100%20Mix », sinon l'analyse porte sur un autre chemin (AZ-013).
    """
    p = raw.strip()
    if p.startswith("{") and p.endswith("}"):
        p = p[1:-1].strip()
    p = p.strip('"').strip("'")
    if p.lower().startswith("file://"):
        p = urllib.parse.unquote(p[7:])
        if platform.system() == "Windows" and len(p) > 2 and p[0] == "/" and p[2] == ":":
            p = p[1:]
    return p.strip()


def normalize_file_kinds(file_kind: str | Iterable[str]) -> frozenset[str]:
    """Normalise un filtre unique ou un ensemble de filtres, en refusant les valeurs inconnues."""
    kinds = frozenset({file_kind}) if isinstance(file_kind, str) else frozenset(file_kind)
    unknown = kinds - FILE_KINDS
    if unknown:
        raise ValueError(f"Filtre de fichiers inconnu : {sorted(unknown)!r}")
    return kinds


def _matches_kinds(name: str, kinds: frozenset[str]) -> bool:
    """Vrai si le fichier correspond à au moins un des filtres déjà normalisés."""
    if name.startswith(".") or name.lower() in SYSTEM_FILES:
        return False
    if not kinds:
        return False
    if FILE_KIND_ALL in kinds:
        return True
    suffix = Path(name).suffix.lower()
    if FILE_KIND_DEFAULT in kinds and suffix not in EXCLUDED_EXTS:
        return True
    if FILE_KIND_MEDIA in kinds and (
            suffix in IMAGE_EXTS or suffix in VIDEO_EXTS or suffix in AUDIO_EXTS):
        return True
    if FILE_KIND_IMAGES in kinds and suffix in IMAGE_EXTS:
        return True
    if FILE_KIND_VIDEOS in kinds and suffix in VIDEO_EXTS:
        return True
    if FILE_KIND_AUDIO in kinds and suffix in AUDIO_EXTS:
        return True
    if FILE_KIND_DOCUMENTS in kinds and suffix in DOCUMENT_EXTS:
        return True
    if FILE_KIND_OTHER in kinds:
        known = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOCUMENT_EXTS
        return suffix not in known
    return False


def is_counted_kinds(name: str, file_kinds: str | Iterable[str]) -> bool:
    """Vrai si le fichier correspond à au moins un des filtres demandés."""
    return _matches_kinds(name, normalize_file_kinds(file_kinds))


def is_counted_file(name: str, file_kind: str = FILE_KIND_DEFAULT) -> bool:
    """Vrai si le fichier entre dans le filtre demandé.

    Les fichiers cachés et les fichiers système sont toujours ignorés, quel que soit le filtre.
    """
    return is_counted_kinds(name, file_kind)


def is_valid_file(name: str) -> bool:
    """Filtre historique : tous les fichiers sauf textes, code, sous-titres et système."""
    return is_counted_file(name, FILE_KIND_DEFAULT)


@dataclass
class Structure:
    """Nombre de fichiers valides par dossier (clé = chemin relatif POSIX, « . » pour la racine)."""
    root: Path
    folders: int = 0
    files: int = 0
    counts: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.counts is None:
            self.counts = {}


def scan_structure(root: Path | str, cancel: Callable[[], bool] | None = None,
                   file_kind: str = FILE_KIND_DEFAULT, *,
                   file_kinds: Iterable[str] | None = None) -> Structure:
    """Parcourt une arborescence et compte, par dossier, les fichiers des filtres demandés."""
    kinds = normalize_file_kinds(file_kinds if file_kinds is not None else file_kind)
    base = Path(clean_path(str(root)))
    if not base.exists():
        raise ValueError(f"Dossier introuvable : {base}")
    if not base.is_dir():
        raise ValueError(f"Ce n'est pas un dossier : {base}")
    result = Structure(root=base)
    for current, dirs, files in os.walk(base, followlinks=False, onerror=lambda _e: None):
        if cancel is not None and cancel():
            break
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        try:
            rel = Path(current).relative_to(base).as_posix()
        except ValueError:
            continue
        valid = sum(1 for f in files if _matches_kinds(f, kinds))
        result.counts[rel] = valid
        result.files += valid
        if rel != ".":
            result.folders += 1
    return result


@dataclass(frozen=True)
class Row:
    folder: str
    count_a: int | None
    count_b: int | None

    @property
    def state(self) -> str:
        if self.count_a is None:
            return STATE_ONLY_B
        if self.count_b is None:
            return STATE_ONLY_A
        return STATE_EQUAL if self.count_a == self.count_b else STATE_DIFF

    @property
    def delta(self) -> int:
        return (self.count_b or 0) - (self.count_a or 0)


@dataclass
class Comparison:
    a: Structure
    b: Structure
    rows: list[Row]

    @property
    def folder_delta(self) -> int:
        return self.b.folders - self.a.folders

    @property
    def file_delta(self) -> int:
        return self.b.files - self.a.files

    @property
    def identical(self) -> bool:
        return self.folder_delta == 0 and self.file_delta == 0 and all(r.state == STATE_EQUAL for r in self.rows)

    def differences(self) -> list[Row]:
        return [r for r in self.rows if r.state != STATE_EQUAL]


def compare(a: Structure, b: Structure) -> Comparison:
    keys = sorted(set(a.counts) | set(b.counts))
    return Comparison(a, b, [Row(k, a.counts.get(k), b.counts.get(k)) for k in keys])


@dataclass(frozen=True)
class TreeRow:
    """Une ligne d'arborescence : dossier, préfixe de connecteurs déjà calculé, effectifs."""
    folder: str
    name: str
    prefix: str          # « │  ├─ », « └─ »… ; vide pour une racine
    depth: int
    row: Row


def _parent_of(folder: str) -> str | None:
    if folder == ".":
        return None
    parent = Path(folder).parent.as_posix()
    return parent if parent not in ("", ".") else "."


def tree_rows(cmp: Comparison, only_differences: bool = False) -> list[TreeRow]:
    """Lignes de l'arborescence, dans l'ordre d'affichage (profondeur d'abord, comme « tree »).

    Chaque ligne porte son préfixe de connecteurs (« │  », « ├─ », « └─ »), calculé à partir des frères et
    sœurs : la sortie reste lisible même sur une grande arborescence, sans disposition en largeur. Les
    dossiers sont triés par nom ; un dossier dont le parent n'est pas listé (filtre « différences ») devient
    une racine. Avec `only_differences`, seuls les dossiers différents et leurs ancêtres sont gardés.
    """
    by_folder = {r.folder: r for r in cmp.rows}
    if not by_folder:
        return []
    if only_differences:
        keep: set[str] = set()
        for r in cmp.rows:
            if r.state != STATE_EQUAL:
                node: str | None = r.folder
                while node is not None and node not in keep:
                    keep.add(node)
                    node = _parent_of(node)
        if not keep:
            return []
        by_folder = {k: v for k, v in by_folder.items() if k in keep}
    children: dict[str, list[str]] = {k: [] for k in by_folder}
    roots: list[str] = []
    for key in sorted(by_folder, key=lambda k: (0 if k == "." else 1, len(Path(k).parts), k.lower())):
        parent = _parent_of(key)
        if parent is not None and parent in children:
            children[parent].append(key)
        else:
            roots.append(key)          # « . », ou dossier dont le parent n'est pas listé
    for kids in children.values():
        kids.sort(key=lambda k: (Path(k).name.lower(), k.lower()))
    rows: list[TreeRow] = []

    def walk(key: str, depth: int, bars: str, is_last: bool | None) -> None:
        prefix = "" if is_last is None else bars + ("└─ " if is_last else "├─ ")
        name = "." if key == "." else Path(key).name
        rows.append(TreeRow(key, name, prefix, depth, by_folder[key]))
        kids = children[key]
        child_bars = "" if is_last is None else bars + ("   " if is_last else "│  ")
        for i, kid in enumerate(kids):
            walk(kid, depth + 1, child_bars, i == len(kids) - 1)

    for root in roots:
        walk(root, 0, "", None)
    return rows


def tree_label(folder: str) -> str:
    if folder == ".":
        return "."
    p = Path(folder)
    return "  " * (len(p.parts) - 1) + "└── " + p.name


def report_text(cmp: Comparison, only_differences: bool = False) -> str:
    """Rapport texte (copie / export)."""
    sep = "═" * 96
    lines = [sep, "  RÉSUMÉ GLOBAL", sep,
             f"  MÈRE   →  {cmp.a.folders} sous-dossiers  |  {cmp.a.files} fichiers valides",
             f"  FILLE  →  {cmp.b.folders} sous-dossiers  |  {cmp.b.files} fichiers valides",
             f"  ÉCART  →  {cmp.folder_delta:+d} sous-dossiers  |  {cmp.file_delta:+d} fichiers", "", sep, ""]
    lines.append(f"{'  STRUCTURE MÈRE'.ljust(60)} │  STRUCTURE FILLE")
    lines.append("─" * 120)
    shown = 0
    for r in cmp.rows:
        if only_differences and r.state == STATE_EQUAL:
            continue
        label = tree_label(r.folder)
        left = f"{label} ({r.count_a} fich.)" if r.count_a is not None else f"⚠  ABSENT  {label}"
        right = f"{label} ({r.count_b} fich.)" if r.count_b is not None else f"⚠  ABSENT  {label}"
        lines.append(f"  {left.ljust(58)} │  {right}")
        shown += 1
    if only_differences and not shown:
        lines.append("  ✔ Aucune différence : tous les dossiers contiennent le même nombre de fichiers.")
    return "\n".join(lines) + "\n"
