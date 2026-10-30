"""Workers d'Azimut Tags : lecture, écriture et annulation (ExifTool via BaseWorker.run_process)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Sequence

from azimut.core.fsutil import iter_files
from azimut.core.storage import get_storage
from azimut.core.workers import BaseWorker, ProcessResult
from azimut.modules.tags import engine as eng


class _ExifToolWorker(BaseWorker):
    """Socle commun : exécution d'ExifTool avec fichier d'arguments UTF-8."""

    def __init__(self, exiftool: Path, parent=None) -> None:
        super().__init__(parent)
        self._exiftool = Path(exiftool)
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def _exec(self, build: Callable[[Path], list[str]], lines: list[str], timeout: float) -> ProcessResult:
        argfile = eng.write_argfile(lines)
        try:
            result = self.run_process(build(argfile), timeout=timeout)
        finally:
            try:
                argfile.unlink()
            except OSError:
                pass
        self.check_cancelled()
        return result

    def read_records(self, paths: Sequence[Path], *, numeric: bool = False) -> list[dict] | None:
        """Enregistrements alignés sur `paths`, ou None si la sortie est inexploitable."""
        res = self._exec(lambda a: eng.read_command(self._exiftool, a, numeric=numeric),
                         [str(p) for p in paths], max(120.0, 30.0 * len(paths)))
        if res.timed_out:
            self.emit_log("WARN", "ExifTool : délai dépassé pendant la lecture.")
            return None
        records = eng.parse_json_output(res.stdout)
        if records is None:
            self.emit_log("WARN", f"ExifTool : sortie inexploitable. {res.stderr.strip()[-200:]}")
            return None
        return eng.match_records(paths, records)

    def write_tags(self, path: Path, tag_args: list[str], *, numeric: bool = False) -> tuple[bool, str]:
        res = self._exec(lambda a: eng.write_command(self._exiftool, a, numeric=numeric),
                         [*tag_args, str(path)], 120.0)
        if res.timed_out:
            return False, "délai dépassé"
        return eng.parse_write_result(res.returncode, res.stdout, res.stderr)


# ═════════════════════════════════════════════════════════════════════════════
class ReadWorker(_ExifToolWorker):
    """Parcourt les chemins (dossiers inclus) et lit les métadonnées par lots de 200 fichiers.

    Résultat : list[FileTags].
    """

    def __init__(self, exiftool: Path, paths: Sequence[Path | str], recursive: bool = True, parent=None) -> None:
        super().__init__(exiftool, parent)
        self._paths = [Path(p) for p in paths]
        self._recursive = recursive

    def _collect(self) -> list[Path]:
        seen: set[str] = set()
        files: list[Path] = []

        def add(p: Path) -> None:
            k = eng.path_key(p)
            if k not in seen:
                seen.add(k)
                files.append(p)

        self.emit_status("Recherche des fichiers…")
        for p in self._paths:
            if p.is_dir():
                for f in iter_files(p, recursive=self._recursive, extensions=eng.SUPPORTED_EXTENSIONS,
                                    skip_hidden=True, cancel=self.is_cancelled):
                    add(f)
            elif p.is_file():
                add(p)
            else:
                self.emit_log("WARN", f"Introuvable : {p}")
        self.check_cancelled()
        files.sort(key=lambda f: str(f).lower())
        return files

    def do_work(self) -> list[eng.FileTags]:
        files = self._collect()
        total = len(files)
        results: list[eng.FileTags] = []
        self.emit_log("INFO", f"{total} fichier(s) à lire.")
        done = 0
        for chunk in eng.chunks(files, eng.READ_CHUNK):
            self.check_cancelled()
            records = self.read_records(chunk)
            if records is None and len(chunk) > 1:           # repli : fichier par fichier
                self.emit_log("WARN", "Lecture groupée impossible : lecture fichier par fichier.")
                records = []
                for p in chunk:
                    one = self.read_records([p])
                    records.append(one[0] if one else {})
            elif records is None:
                records = [{}]
            for p, rec in zip(chunk, records):
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                ft = eng.FileTags(path=p, size=size)
                if not rec:
                    ft.error = "lecture impossible"
                    self.error_count += 1
                    self.emit_log("ERR", f"{p.name} : lecture impossible.")
                else:
                    ft.entries = eng.parse_entries(rec)
                    err = rec.get("ExifTool:Error") or rec.get("Error")
                    if err:
                        ft.error = str(err)
                        self.emit_log("WARN", f"{p.name} : {err}")
                    self.ok_count += 1
                results.append(ft)
            done += len(chunk)
            self.emit_progress(done, total, chunk[-1].name)
        self.summary = f"{self.ok_count} fichier(s) lu(s), {self.error_count} erreur(s)"
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        return results


# ═════════════════════════════════════════════════════════════════════════════
class WriteWorker(_ExifToolWorker):
    """Applique les modifications à chaque fichier, après enregistrement de l'état précédent.

    Résultat : {"written": list[Path], "key": clé d'annulation | None}.
    """

    def __init__(self, exiftool: Path, files: Sequence[Path | str], changes: Sequence[eng.TagChange],
                 align_mtime: bool = False, parent=None) -> None:
        super().__init__(exiftool, parent)
        self._files = [Path(f) for f in files]
        self._changes = list(changes)
        self._align = align_mtime

    def do_work(self) -> dict:
        problems = eng.validate_changes(self._changes)
        if problems:
            for p in problems:
                self.emit_log("ERR", p)
            self.error_count = len(self._files)
            self.summary = f"Modifications refusées : {problems[0]}"
            return {"written": [], "key": None}

        when = eng.date_from_changes(self._changes) if self._align else None
        total = len(self._files)
        records: list[dict] = []
        written: list[Path] = []
        key: str | None = None
        try:
            for i, path in enumerate(self._files, 1):
                self.check_cancelled()
                self._one(path, when, records, written)
                self.emit_progress(i, total, path.name)
        finally:
            key = self._save_undo(records)
            self.summary = f"{self.ok_count} fichier(s) modifié(s), {self.error_count} erreur(s)"
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        return {"written": written, "key": key}

    def _one(self, path: Path, when, records: list[dict], written: list[Path]) -> None:
        refusal = eng.write_refusal(path)
        if refusal:
            self.error_count += 1
            self.emit_log("ERR", refusal)
            return
        if not path.is_file():
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : fichier introuvable.")
            return
        try:
            resolved = eng.resolve_changes(path, self._changes)
            tag_args = eng.change_args(resolved)
        except ValueError as exc:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : {exc}")
            return
        # Pas d'écriture sans instantané : sinon l'annulation serait impossible.
        recs = self.read_records([path], numeric=True)
        rec = recs[0] if recs else None
        if not rec:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : lecture préalable impossible, aucune écriture.")
            return
        before, unrestorable = eng.snapshot_before(eng.parse_entries(rec), resolved)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        ok, msg = self.write_tags(path, tag_args)
        if not ok:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : {msg}")
            return
        if msg:
            self.emit_log("WARN", f"{path.name} : {msg}")
        if when is not None:
            try:
                ts = when.timestamp()
                os.utime(path, (ts, ts))
            except (OSError, OverflowError, ValueError) as exc:
                self.emit_log("WARN", f"{path.name} : date de modification non alignée ({exc}).")
        if unrestorable:
            self.emit_log("WARN", f"{path.name} : {len(unrestorable)} valeur(s) binaire(s) non restaurables.")
        records.append(eng.snapshot_record(path, mtime, before, unrestorable))
        written.append(path)
        self.ok_count += 1
        self.emit_log("OK", f"{path.name} : {len(tag_args)} modification(s) écrite(s).")

    def _save_undo(self, records: list[dict]) -> str | None:
        useful = [r for r in records if r["before"]]
        if not useful:
            if records:
                self.emit_log("INFO", "Aucune valeur à restaurer : pas d'annulation enregistrée.")
            return None
        key = eng.new_undo_key()
        try:
            storage = get_storage()
            storage.save(eng.UNDO_COLLECTION, key, eng.undo_record(useful, self._changes))
            for old in eng.keys_to_prune(storage.list_keys(eng.UNDO_COLLECTION)):
                storage.delete(eng.UNDO_COLLECTION, old)
        except Exception as exc:                                  # le stockage ne doit pas faire échouer le lot
            self.emit_log("WARN", f"Sauvegarde d'annulation impossible : {exc}")
            return None
        return key


# ═════════════════════════════════════════════════════════════════════════════
class UndoWorker(_ExifToolWorker):
    """Rétablit l'état enregistré par une application (valeurs, ou suppression des tags absents).

    Résultat : {"restored": list[Path], "failed": int}.
    """

    def __init__(self, exiftool: Path, key: str, parent=None, *, collection: str = eng.UNDO_COLLECTION) -> None:
        super().__init__(exiftool, parent)
        self._key = key
        self._collection = collection

    def do_work(self) -> dict:
        storage = get_storage()
        rec = storage.load(self._collection, self._key)
        if not rec or not rec.get("files"):
            self.emit_log("ERR", "Sauvegarde d'annulation introuvable ou vide.")
            self.summary = "Aucune sauvegarde d'annulation exploitable"
            return {"restored": [], "failed": 0}
        files = rec["files"]
        restored: list[Path] = []
        failed: list[dict] = []
        total = len(files)
        try:
            for i, item in enumerate(files, 1):
                self.check_cancelled()
                path = Path(item.get("path", ""))
                if not path.is_file():
                    self.emit_log("ERR", f"{path.name or item.get('path')} : fichier introuvable.")
                    failed.append(item)
                else:
                    changes = eng.restoration_changes(item.get("before") or {})
                    ok, msg = True, ""
                    if changes:
                        ok, msg = self.write_tags(path, eng.change_args(changes), numeric=True)
                    if ok:
                        mt = item.get("mtime")
                        if mt is not None:
                            try:
                                os.utime(path, (mt, mt))
                            except OSError:
                                pass
                        restored.append(path)
                        self.emit_log("OK", f"Restauré : {path.name}")
                    else:
                        self.emit_log("ERR", f"{path.name} : {msg}")
                        failed.append(item)
                self.emit_progress(i, total, path.name)
        finally:
            self.ok_count, self.error_count = len(restored), len(failed)
            self.summary = f"{self.ok_count} fichier(s) restauré(s), {self.error_count} erreur(s)"
            try:
                if failed:                                       # on ne garde que ce qui reste à restaurer
                    storage.save(self._collection, self._key, {**rec, "files": failed})
                elif len(restored) == total:
                    storage.delete(self._collection, self._key)
            except Exception as exc:
                self.emit_log("WARN", f"Mise à jour de la sauvegarde d'annulation impossible : {exc}")
        self.emit_log("OK" if not failed else "WARN", self.summary)
        return {"restored": restored, "failed": len(failed)}
