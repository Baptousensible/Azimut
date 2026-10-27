"""Application des suppressions d'Azimut Dedup : quarantaine, journal, annulation, rapport (sans Qt)."""
from __future__ import annotations

import csv
import io
import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from azimut.core.fsutil import atomic_write_text, unique_path
from azimut.modules.dedup.engine import Hooks
from azimut.modules.dedup.model import (
    ACTION_DELETE,
    ACTION_KEEP,
    KIND_EXACT,
    FileItem,
    Group,
)

MODE_QUARANTINE = "quarantine"
MODE_TRASH = "trash"
MODE_DELETE = "delete"

JOURNAL_NAME = "journal.csv"
JOURNAL_COLUMNS = ("chemin_original", "chemin_quarantaine", "cote", "taille")
REPORT_COLUMNS = ("groupe", "type", "cote", "chemin", "taille", "date", "dimensions", "empreinte", "action")
MTIME_TOLERANCE = 2.0   # secondes


# ── Utilitaires ───────────────────────────────────────────────────────

def trash_available() -> bool:
    """True si `send2trash` est installé (le mode Corbeille est masqué sinon)."""
    try:
        import send2trash  # noqa: F401
    except ImportError:
        return False
    return True


def default_quarantine_parent(folder_a: Path) -> Path:
    """Emplacement par défaut de la quarantaine : le dossier parent de A."""
    return Path(folder_a).absolute().parent


def quarantine_dir_name(now: datetime | None = None) -> str:
    return "Quarantaine_" + (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def plan_deletions(groups: Iterable[Group]) -> list[tuple[Group, FileItem]]:
    """Fichiers qui seraient touchés : proposés à la suppression, hors dossier protégé,
    dans un groupe qui garde au moins un exemplaire."""
    plan: list[tuple[Group, FileItem]] = []
    for g in groups:
        if not g.kept():
            continue
        plan.extend((g, i) for i in g.to_delete() if not i.protected)
    return plan


# ── Application ───────────────────────────────────────────────────────

@dataclass
class ApplyOptions:
    mode: str = MODE_QUARANTINE
    quarantine_parent: Path | None = None   # le dossier Quarantaine_<horodatage> y est créé
    remove_empty_dirs: bool = False


@dataclass
class ApplyReport:
    mode: str = MODE_QUARANTINE
    done: list[FileItem] = field(default_factory=list)      # fichiers retirés de leur place
    skipped: list[FileItem] = field(default_factory=list)   # ignorés par sécurité
    errors: int = 0
    freed: int = 0
    removed_dirs: int = 0
    quarantine_dir: Path | None = None
    journal: Path | None = None
    cancelled: bool = False

    @property
    def moved(self) -> int:
        return len(self.done)


def _regular_stat(path: Path) -> os.stat_result | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def check_item(group: Group, item: FileItem) -> str | None:
    """Raison de ne pas toucher `item` (None si tout est en ordre)."""
    if item.protected:
        return "dossier protégé"
    st = _regular_stat(item.path)
    if st is None:
        return "fichier introuvable"
    if st.st_size != item.size or abs(st.st_mtime - item.mtime) > MTIME_TOLERANCE:
        return "modifié depuis l'analyse"
    for kept in group.kept():
        if kept is item or kept.path == item.path:
            continue
        kst = _regular_stat(kept.path)
        if kst is None or kst.st_size != kept.size:
            continue
        if group.kind == KIND_EXACT and kst.st_size != item.size:
            continue
        return None
    return "aucun exemplaire conservé valide"


def _fsync(fh) -> None:
    fh.flush()
    try:
        os.fsync(fh.fileno())
    except OSError:
        pass


def _remove_empty_dirs(items: Iterable[FileItem]) -> int:
    dirs: set[Path] = set()
    for it in items:
        p = it.path.parent
        while p != it.root and it.root in p.parents:
            dirs.add(p)
            p = p.parent
    removed = 0
    for d in sorted(dirs, key=lambda x: len(x.parts), reverse=True):
        try:
            if not os.path.islink(d):
                os.rmdir(d)
                removed += 1
        except OSError:
            pass   # non vide : on laisse
    return removed


def apply_groups(groups: Iterable[Group], options: ApplyOptions, hooks: Hooks | None = None) -> ApplyReport:
    """Retire les fichiers « Supprimer » selon le mode. Chaque fichier est revérifié juste avant.
    Annulation : arrêt entre deux fichiers (`report.cancelled`), rien d'autre n'est touché."""
    hooks = hooks or Hooks()
    report = ApplyReport(mode=options.mode)
    if options.mode not in (MODE_QUARANTINE, MODE_TRASH, MODE_DELETE):
        raise ValueError(f"Mode inconnu : {options.mode}")
    if options.mode == MODE_TRASH and not trash_available():
        raise RuntimeError("Le mode Corbeille nécessite le module send2trash.")

    todo: list[tuple[Group, FileItem]] = []
    for g in groups:
        pending = [i for i in g.items if i.action == ACTION_DELETE]
        if pending and not g.kept():
            hooks.log("ERR", f"Groupe {g.id} sans exemplaire gardé : {len(pending)} fichier(s) ignoré(s).")
            report.skipped.extend(pending)
            continue
        todo.extend((g, i) for i in pending)
    total = len(todo)
    if total == 0:
        hooks.log("INFO", "Aucun fichier à traiter.")
        return report

    fh = None
    writer = None
    qdir: Path | None = None
    if options.mode == MODE_QUARANTINE:
        if options.quarantine_parent is None:
            raise ValueError("Dossier de quarantaine non défini.")
        qdir = unique_path(Path(options.quarantine_parent) / quarantine_dir_name())
        qdir.mkdir(parents=True, exist_ok=False)
        report.quarantine_dir = qdir
        report.journal = qdir / JOURNAL_NAME
        fh = open(report.journal, "w", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        writer.writerow(JOURNAL_COLUMNS)
        _fsync(fh)
        hooks.log("INFO", f"Quarantaine : {qdir}")

    try:
        for n, (group, item) in enumerate(todo, start=1):
            if hooks.cancel():
                report.cancelled = True
                hooks.log("WARN", "Application annulée : les fichiers restants n'ont pas été touchés.")
                break
            reason = check_item(group, item)
            if reason:
                report.skipped.append(item)
                hooks.log("WARN", f"Ignoré ({reason}) : {item.path}")
            else:
                try:
                    if options.mode == MODE_QUARANTINE:
                        assert qdir is not None and writer is not None and fh is not None
                        dest = unique_path(qdir / item.side / item.relative_path)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        writer.writerow([str(item.path), str(dest), item.side, item.size])
                        _fsync(fh)   # le journal est sur disque AVANT le déplacement
                        shutil.move(str(item.path), str(dest))
                    elif options.mode == MODE_TRASH:
                        from send2trash import send2trash
                        send2trash(str(item.path))
                    else:
                        os.remove(item.path)
                    report.done.append(item)
                    report.freed += item.size
                except Exception as exc:  # noqa: BLE001 - un échec n'arrête pas le lot
                    report.errors += 1
                    hooks.log("ERR", f"Échec sur {item.path} : {exc}")
            hooks.progress(n, total, item.path.name)
    finally:
        if fh is not None:
            fh.close()

    if options.remove_empty_dirs and report.done:
        report.removed_dirs = _remove_empty_dirs(report.done)
    hooks.log("OK" if not report.errors else "WARN",
              f"{report.moved} fichier(s) traité(s), {len(report.skipped)} ignoré(s), {report.errors} erreur(s).")
    return report


# ── Annulation d'une application ──────────────────────────────────────

@dataclass
class UndoReport:
    restored: int = 0
    skipped: int = 0
    errors: int = 0
    cancelled: bool = False


def _locate_in_quarantine(stored: str, journal: Path) -> Path:
    """Chemin de quarantaine ; si le dossier a été déplacé, on le retrouve à côté du journal."""
    path = Path(stored)
    if path.exists():
        return path
    parts = path.parts
    name = journal.parent.name
    if name in parts:
        idx = len(parts) - 1 - parts[::-1].index(name)
        return journal.parent.joinpath(*parts[idx + 1:])
    return path


def undo_journal(journal: Path, hooks: Hooks | None = None) -> UndoReport:
    """Remet chaque fichier du journal à sa place. Ne remplace jamais un fichier existant."""
    hooks = hooks or Hooks()
    journal = Path(journal)
    with open(journal, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not {"chemin_original", "chemin_quarantaine"} <= set(reader.fieldnames):
            raise ValueError("Journal invalide : colonnes attendues absentes.")
        rows = list(reader)
    report = UndoReport()
    total = len(rows)
    for n, row in enumerate(rows, start=1):
        if hooks.cancel():
            report.cancelled = True
            hooks.log("WARN", "Annulation interrompue.")
            break
        original = Path(row["chemin_original"])
        stored = _locate_in_quarantine(row["chemin_quarantaine"], journal)
        if not stored.exists():
            report.skipped += 1
            if original.exists():
                hooks.log("INFO", f"Déjà en place : {original}")
            else:
                hooks.log("WARN", f"Introuvable en quarantaine : {stored}")
        elif original.exists():
            report.errors += 1
            hooks.log("ERR", f"Existe déjà, non remplacé : {original}")
        else:
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(stored), str(original))
                report.restored += 1
            except OSError as exc:
                report.errors += 1
                hooks.log("ERR", f"Échec de la restauration de {original} : {exc}")
        hooks.progress(n, total, original.name)

    # Nettoie les sous-dossiers de quarantaine devenus vides (le journal reste).
    for dirpath, _dirs, _files in os.walk(journal.parent, topdown=False):
        if Path(dirpath) != journal.parent:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    hooks.log("OK" if not report.errors else "WARN",
              f"{report.restored} fichier(s) restauré(s), {report.skipped} ignoré(s), {report.errors} erreur(s).")
    return report


# ── Rapport CSV ───────────────────────────────────────────────────────

def export_report(groups: Iterable[Group], path: Path) -> int:
    """Écrit le rapport complet (UTF-8 avec BOM, lisible par Excel). Renvoie le nombre de lignes."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(REPORT_COLUMNS)
    rows = 0
    for g in groups:
        for i in g.items:
            date = datetime.fromtimestamp(i.mtime).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([g.id, g.kind, i.side, str(i.path), i.size, date,
                             i.dimensions_text, i.digest, i.action])
            rows += 1
    atomic_write_text(Path(path), buf.getvalue(), encoding="utf-8-sig")
    return rows
