"""Application des suppressions d'Azimut Dedup : quarantaine, journal, annulation, rapport (sans Qt)."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from azimut.core.fsutil import atomic_write_text, move_no_replace, unique_path
from azimut.core.logs import get_logger
from azimut.modules.dedup.engine import MAX_TOLERANCE, Hooks, full_digest
from azimut.modules.dedup.model import (
    ACTION_DELETE,
    KIND_EXACT,
    KIND_SIMILAR,
    FileItem,
    Group,
)

_log = get_logger("dedup.actions")

MODE_QUARANTINE = "quarantine"
MODE_TRASH = "trash"
MODE_DELETE = "delete"

JOURNAL_NAME = "journal.csv"
JOURNAL_META_NAME = "journal.meta.json"      # dossiers réellement analysés (confiance à la restauration)
JOURNAL_COLUMNS = ("chemin_original", "chemin_quarantaine", "cote", "taille")
REPORT_COLUMNS = ("groupe", "type", "cote", "chemin", "taille", "date", "dimensions", "empreinte", "action")
MTIME_TOLERANCE = 2.0   # secondes

# Emplacements dont un fichier est exécuté automatiquement à la session suivante (liste noire de dernier
# recours à la restauration : elle ne remplace pas le confinement par les racines analysées).
_SENSITIVE_DIRS = frozenset({"autostart", "startup", "launchagents", "launchdaemons", "startupitems"})
_SENSITIVE_NAMES = frozenset({".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile", "authorized_keys"})


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
    """Fichiers qui seraient touchés : toutes les propositions de suppression, hors protégés.
    Un groupe peut être entièrement vide ; l'appelant doit alors demander une confirmation."""
    plan: list[tuple[Group, FileItem]] = []
    for g in groups:
        plan.extend((g, i) for i in g.to_delete() if not i.protected)
    return plan


# ── Application ───────────────────────────────────────────────────────

@dataclass
class ApplyOptions:
    mode: str = MODE_QUARANTINE
    quarantine_parent: Path | None = None   # le dossier Quarantaine_<horodatage> y est créé
    remove_empty_dirs: bool = False
    tolerance: int = MAX_TOLERANCE          # écart dHash maximal entre un supprimé et son gardien
    allow_keeperless: bool = False          # l'utilisateur a accepté un groupe sans fichier conservé


@dataclass
class ApplyReport:
    mode: str = MODE_QUARANTINE
    done: list[FileItem] = field(default_factory=list)      # fichiers retirés de leur place
    skipped: list[FileItem] = field(default_factory=list)   # ignorés par sécurité
    errors: int = 0
    freed: int = 0              # octets réellement supprimés du disque (mode définitif uniquement)
    quarantined: int = 0        # octets déplacés en quarantaine ou à la corbeille : pas encore libérés
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


def _write_journal_meta(qdir: Path, items: Iterable[FileItem]) -> None:
    """Enregistre les dossiers analysés à côté du journal (base du confinement à la restauration)."""
    roots = sorted({str(Path(it.root).resolve()) for it in items})
    try:
        atomic_write_text(qdir / JOURNAL_META_NAME, json.dumps({"version": 1, "roots": roots}, indent=2))
    except OSError as exc:
        _log.debug("Métadonnées du journal non écrites : %s", exc)


def _journal_roots(journal: Path) -> list[Path] | None:
    """Racines analysées enregistrées avec ce journal ; None si le journal n'en porte pas (donc non fiable)."""
    try:
        data = json.loads((journal.parent / JOURNAL_META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    roots = data.get("roots") if isinstance(data, dict) else None
    if not isinstance(roots, list):
        return None
    found: list[Path] = []
    for value in roots:
        if isinstance(value, str) and value:
            try:
                found.append(Path(value).resolve())
            except OSError:
                continue
    return found or None


def _is_sensitive_target(path: Path) -> bool:
    """Vrai si le chemin mène à un emplacement d'exécution automatique ou à un fichier de démarrage."""
    parts = {part.lower() for part in Path(path).parts}
    return bool(parts & _SENSITIVE_DIRS) or Path(path).name.lower() in _SENSITIVE_NAMES


def _undo_refusal_reason(stored: Path, dest: Path, quarantine_root: Path, roots: list[Path] | None,
                         expected_size: int | None = None) -> str | None:
    """Pourquoi refuser cette ligne de journal (None si la restauration est acceptable).

    Le journal est une entrée non fiable : la source doit être un fichier régulier **dans** le dossier de
    quarantaine du journal, et la destination doit rester dans un dossier réellement analysé.
    """
    try:
        st = os.lstat(stored)
    except OSError:
        st = None
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            return "lien symbolique"
        if not stat.S_ISREG(st.st_mode):
            return "source qui n'est pas un fichier"
        if expected_size is not None and st.st_size != expected_size:
            return "taille différente de celle du journal"
    try:
        inside = Path(stored).resolve().is_relative_to(quarantine_root)
    except (OSError, RuntimeError):
        inside = False
    if not inside:
        return "source hors du dossier de quarantaine"
    try:
        dest = Path(dest).resolve()
    except (OSError, RuntimeError):
        return "destination non résolvable"
    if roots is not None and not any(dest.is_relative_to(root) for root in roots):
        return "destination hors des dossiers analysés"
    if _is_sensitive_target(dest):
        return "destination dans un emplacement d'exécution automatique"
    return None


def check_item(group: Group, item: FileItem, tolerance: int = MAX_TOLERANCE, *,
               verify_content: bool = False, cancel: Callable[[], bool] | None = None,
               allow_keeperless: bool = False) -> str | None:
    """Raison de ne pas toucher `item` (None si tout est en ordre).

    `verify_content` revérifie le contenu de l'exemplaire à supprimer et de son gardien avant chaque
    suppression : indispensable en suppression définitive, où une réécriture à taille et date identiques
    ferait perdre le seul exemplaire d'origine ou une modification récente. Les fichiers illisibles ou
    annulés sont écartés. `allow_keeperless` autorise un groupe dont tous les fichiers sont supprimés.
    """
    if item.protected:
        return "dossier protégé"
    st = _regular_stat(item.path)
    if st is None:
        return "fichier introuvable"
    if st.st_size != item.size or abs(st.st_mtime - item.mtime) > MTIME_TOLERANCE:
        return "modifié depuis l'analyse"
    if verify_content and group.kind == KIND_EXACT and item.digest:
        try:
            current = full_digest(item.path, cancel) if cancel is not None else full_digest(item.path)
        except OSError:
            return "fichier illisible"
        if current is None:
            return "vérification annulée"
        if current != item.digest:
            return "contenu modifié depuis l'analyse"
    for kept in group.kept():
        if kept is item or kept.path == item.path:
            continue
        kst = _regular_stat(kept.path)
        if kst is None or kst.st_size != kept.size or abs(kst.st_mtime - kept.mtime) > MTIME_TOLERANCE:
            continue                              # exemplaire disparu ou réécrit depuis l'analyse : plus fiable
        if group.kind == KIND_EXACT and kst.st_size != item.size:
            continue
        if (group.kind == KIND_SIMILAR and item.similar_hash is not None
                and kept.similar_hash is not None
                and (item.similar_hash ^ kept.similar_hash).bit_count() > tolerance):
            continue
        if verify_content and group.kind == KIND_EXACT and kept.digest:
            try:
                current = full_digest(kept.path, cancel) if cancel is not None else full_digest(kept.path)
            except OSError:
                current = None
            if current is None or current != kept.digest:
                continue                          # contenu réécrit (même taille et date) : plus fiable
        return None
    return None if allow_keeperless else "aucun exemplaire conservé valide"


def _fsync(fh) -> None:
    fh.flush()
    try:
        os.fsync(fh.fileno())
    except OSError as exc:
        _log.debug("fsync du journal ignoré : %s", exc)


def _remove_empty_dirs(parents: Iterable[Path], roots: Iterable[Path]) -> int:
    """Supprime uniquement les dossiers vidés par l'opération (parents des fichiers retirés).

    Un dossier vide qui existait avant l'opération, hors du chemin d'un fichier supprimé, est conservé.
    """
    root_list = [Path(r) for r in roots]
    candidates: set[Path] = set()
    for parent in parents:
        parent = Path(parent)
        root = max((r for r in root_list if parent.is_relative_to(r)), key=lambda p: len(p.parts), default=None)
        if root is None:
            continue
        directory = parent
        while directory != root and directory != directory.parent:
            candidates.add(directory)
            directory = directory.parent
    removed = 0
    for directory in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
        try:
            if directory.is_dir() and not directory.is_symlink():
                os.rmdir(directory)
                removed += 1
        except OSError as exc:
            _log.debug("Dossier non supprimé (%s) : %s", directory, exc)   # non vide : on laisse
    return removed


def apply_groups(groups: Iterable[Group], options: ApplyOptions, hooks: Hooks | None = None) -> ApplyReport:
    """Retire les fichiers « Supprimer » selon le mode. Chaque fichier est revérifié juste avant.
    Un groupe sans exemplaire gardé est ignoré sauf si `options.allow_keeperless` est demandé.
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
        if pending and not g.kept() and not options.allow_keeperless:
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
    moved_from: list[Path] = []
    if options.mode == MODE_QUARANTINE:
        if options.quarantine_parent is None:
            raise ValueError("Dossier de quarantaine non défini.")
        qdir = unique_path(Path(options.quarantine_parent) / quarantine_dir_name())
        qdir.mkdir(parents=True, exist_ok=False)
        report.quarantine_dir = qdir
        report.journal = qdir / JOURNAL_NAME
        _write_journal_meta(qdir, [item for _group, item in todo])
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
            reason = check_item(group, item, options.tolerance,
                                verify_content=options.mode == MODE_DELETE,
                                cancel=hooks.cancel,
                                allow_keeperless=options.allow_keeperless)
            if reason:
                report.skipped.append(item)
                hooks.log("WARN", f"Ignoré ({reason}) : {item.path}")
            else:
                try:
                    if options.mode == MODE_QUARANTINE:
                        if qdir is None or writer is None or fh is None:
                            raise RuntimeError("Quarantaine non initialisée : fichier non déplacé.")
                        dest = unique_path(qdir / item.side / item.relative_path)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        writer.writerow([str(item.path), str(dest), item.side, item.size])
                        _fsync(fh)   # le journal est sur disque AVANT le déplacement
                        try:
                            move_no_replace(item.path, dest)
                        except FileExistsError:
                            raise
                        except OSError:
                            # Système sans liens physiques ou changement de volume : repli vérifié.
                            if os.path.lexists(dest):
                                raise FileExistsError(f"« {dest} » existe déjà.") from None
                            shutil.move(str(item.path), str(dest))
                    elif options.mode == MODE_TRASH:
                        from send2trash import send2trash
                        send2trash(str(item.path))
                    else:
                        os.remove(item.path)
                    report.done.append(item)
                    moved_from.append(item.path.parent)
                    if options.mode == MODE_DELETE:
                        report.freed += item.size
                    else:
                        report.quarantined += item.size
                except Exception as exc:  # noqa: BLE001 - un échec n'arrête pas le lot
                    report.errors += 1
                    hooks.log("ERR", f"Échec sur {item.path} : {exc}")
            hooks.progress(n, total, item.path.name)
    finally:
        if fh is not None:
            fh.close()

    if options.remove_empty_dirs and report.done:
        report.removed_dirs = _remove_empty_dirs(
            moved_from, {Path(it.root) for _group, it in todo})
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


def undo_journal(journal: Path, hooks: Hooks | None = None, *, roots: Iterable[Path] | None = None,
                 allow_foreign: bool = False) -> UndoReport:
    """Remet chaque fichier du journal à sa place. Ne remplace jamais un fichier existant.

    Un journal peut venir d'une archive décompressée : il est traité comme une **entrée non fiable**. La source
    est confinée au dossier de quarantaine du journal ; la destination doit être sous l'une des racines
    analysées, enregistrées à la création du journal (`journal.meta.json`). Un journal sans référence
    (`allow_foreign=False`) est refusé : l'appelant peut demander une confirmation explicite puis passer
    `allow_foreign=True`.
    """
    hooks = hooks or Hooks()
    journal = Path(journal)
    with open(journal, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not {"chemin_original", "chemin_quarantaine"} <= set(reader.fieldnames):
            raise ValueError("Journal invalide : colonnes attendues absentes.")
        rows = list(reader)
    report = UndoReport()
    total = len(rows)
    quarantine_root = journal.parent.resolve()
    allowed = [Path(r).resolve() for r in roots] if roots is not None else _journal_roots(journal)
    if allowed is None and not allow_foreign:
        hooks.log("ERR", "Journal sans liste de dossiers d'origine : restauration refusée par sécurité "
                          "(journal importé ou antérieur à cette version).")
        report.skipped = total
        return report
    for n, row in enumerate(rows, start=1):
        if hooks.cancel():
            report.cancelled = True
            hooks.log("WARN", "Annulation interrompue.")
            break
        original = Path(row["chemin_original"])
        dest = Path(os.path.abspath(str(original)))
        stored = _locate_in_quarantine(row["chemin_quarantaine"], journal)
        try:
            expected_size = int(row.get("taille", "") or 0)
        except ValueError:
            expected_size = None
        reason = _undo_refusal_reason(Path(stored), dest, quarantine_root, allowed,
                                      expected_size if expected_size else None)
        if reason:
            report.skipped += 1
            hooks.log("ERR", f"Refusé ({reason}) : {dest}")
        elif not stored.exists():
            report.skipped += 1
            if dest.exists():
                hooks.log("INFO", f"Déjà en place : {dest}")
            else:
                hooks.log("WARN", f"Introuvable en quarantaine : {stored}")
        elif dest.exists():
            report.errors += 1
            hooks.log("ERR", f"Existe déjà, non remplacé : {dest}")
        else:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    move_no_replace(stored, dest)
                except FileExistsError:
                    raise
                except OSError:
                    if os.path.lexists(dest):
                        raise FileExistsError(f"« {dest} » existe déjà.") from None
                    shutil.move(str(stored), str(dest))
                report.restored += 1
            except OSError as exc:
                report.errors += 1
                hooks.log("ERR", f"Échec de la restauration de {dest} : {exc}")
        hooks.progress(n, total, dest.name)

    # Nettoie les sous-dossiers de quarantaine devenus vides (le journal reste).
    for dirpath, _dirs, _files in os.walk(journal.parent, topdown=False):
        if Path(dirpath) != journal.parent:
            try:
                os.rmdir(dirpath)
            except OSError as exc:
                _log.debug("Sous-dossier de quarantaine non supprimé (%s) : %s", dirpath, exc)
    hooks.log("OK" if not report.errors else "WARN",
              f"{report.restored} fichier(s) restauré(s), {report.skipped} ignoré(s), {report.errors} erreur(s).")
    return report


def csv_safe(cell: str) -> str:
    """Neutralise une cellule CSV qui serait interprétée comme une formule (CWE-1236)."""
    return "'" + cell if cell.startswith(("=", "+", "-", "@", "\t", "\r")) else cell


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
            writer.writerow([g.id, g.kind, i.side, csv_safe(str(i.path)), i.size, date,
                             i.dimensions_text, i.digest, i.action])
            rows += 1
    atomic_write_text(Path(path), buf.getvalue(), encoding="utf-8-sig")
    return rows
