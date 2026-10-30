"""Moteur d'Azimut Scope : comparaison de deux arborescences, dossier par dossier (lecture seule, sans Qt).

Pour chaque dossier (chemin relatif à la racine), on compte les fichiers « valides » : les fichiers texte,
JSON, scripts, sous-titres et fichiers système ne comptent pas, ni les éléments cachés. On vérifie ainsi qu'une
copie ou une conversion a bien produit autant de fichiers que l'original, sans rien modifier.
"""
from __future__ import annotations

import os
import platform
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_EXTS = frozenset({
    ".txt", ".md", ".log", ".csv", ".ini", ".json", ".xml", ".yaml", ".yml", ".cfg", ".conf", ".bak", ".html",
    ".css", ".js", ".sh", ".py", ".rst", ".toml", ".nfo", ".srt", ".vtt", ".sub", ".tex",
})
SYSTEM_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store", ".localized"})

STATE_EQUAL = "Identique"
STATE_DIFF = "Différent"
STATE_ONLY_A = "Absent de la fille"
STATE_ONLY_B = "Absent de la mère"
STATE_TAGS = {STATE_EQUAL: "ok", STATE_DIFF: "err", STATE_ONLY_A: "warn", STATE_ONLY_B: "warn"}


def clean_path(raw: str) -> str:
    """Nettoie un chemin glissé-déposé : accolades, guillemets, file://, pourcentages."""
    p = raw.strip()
    if p.startswith("{") and p.endswith("}"):
        p = p[1:-1].strip()
    p = p.strip('"').strip("'")
    if p.lower().startswith("file://"):
        p = urllib.parse.unquote(p[7:])
        if platform.system() == "Windows" and len(p) > 2 and p[0] == "/" and p[2] == ":":
            p = p[1:]
    else:
        p = urllib.parse.unquote(p)
    return p.strip()


def is_valid_file(name: str) -> bool:
    return (not name.startswith(".") and name.lower() not in SYSTEM_FILES
            and Path(name).suffix.lower() not in EXCLUDED_EXTS)


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


def scan_structure(root: Path | str, cancel: Callable[[], bool] | None = None) -> Structure:
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
        valid = sum(1 for f in files if is_valid_file(f))
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
