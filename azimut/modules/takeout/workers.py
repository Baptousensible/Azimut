"""Workers d'Azimut Takeout : analyse des dossiers, application des dates et positions."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject

from azimut.core.storage import get_storage
from azimut.core.workers import BaseWorker
from azimut.modules.tags import engine as tags
from azimut.modules.tags.workers import UndoWorker, _ExifToolWorker
from azimut.modules.takeout import engine as eng

READ_BATCH = 50


class AnalyzeWorker(BaseWorker):                          # pas besoin d'ExifTool pour analyser
    """Apparie médias et JSON. Résultat : `engine.Analysis`."""

    def __init__(self, folders: Sequence[Path | str], recursive: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folders = [Path(f) for f in folders]
        self._recursive = recursive

    def do_work(self) -> eng.Analysis:
        self.emit_status("Recherche des médias et des JSON…")
        total = eng.Analysis()
        for folder in self._folders:
            self.check_cancelled()
            if not folder.is_dir():
                self.emit_log("WARN", f"Dossier introuvable : {folder}")
                continue
            part = eng.analyze(folder, recursive=self._recursive, cancel=self.is_cancelled)
            self.check_cancelled()
            total.items += part.items
            total.orphan_json += part.orphan_json
        ready = total.count(eng.STATE_READY)
        self.ok_count, self.error_count = ready, total.count(eng.STATE_NO_JSON)
        self.summary = (f"{len(total.items)} média(s) : {ready} prêt(s), {total.count(eng.STATE_NO_JSON)} sans JSON, "
                        f"{total.count(eng.STATE_NO_DATA)} sans données ; {total.orphan_json} JSON sans média")
        self.emit_log("INFO", self.summary)
        return total


class ApplyWorker(_ExifToolWorker):
    """Écrit dates et GPS fichier par fichier, avec instantané d'annulation.

    Résultat : {"states": {indice: (état, message)}, "key": clé d'annulation | None}.
    """

    def __init__(self, exiftool: Path, items: Sequence[eng.TakeoutItem], options: eng.Options,
                 parent: QObject | None = None) -> None:
        super().__init__(exiftool, parent)
        self._items = list(items)
        self._options = options

    def do_work(self) -> dict:
        todo = [(i, it) for i, it in enumerate(self._items) if it.state == eng.STATE_READY and it.sidecar]
        total = len(todo)
        states: dict[int, tuple[str, str]] = {}
        records: list[dict] = []
        try:
            for n, (idx, item) in enumerate(todo, 1):
                self.check_cancelled()
                states[idx] = self._one(item, records)
                self.emit_progress(n, total, item.path.name)
        finally:
            key = self._save_undo(records)
            self.summary = f"{self.ok_count} fichier(s) mis à jour, {self.error_count} erreur(s)"
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        return {"states": states, "key": key}

    def _one(self, item: eng.TakeoutItem, records: list[dict]) -> tuple[str, str]:
        path, sc = item.path, item.sidecar
        assert sc is not None
        if not path.is_file():
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : fichier introuvable.")
            return eng.STATE_ERROR, "Fichier introuvable."
        recs = None if tags.write_refusal(path) else self.read_records([path], numeric=True)
        rec = recs[0] if recs else None
        if tags.write_refusal(path) is None and not rec:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : lecture préalable impossible, aucune écriture.")
            return eng.STATE_ERROR, "Lecture impossible."
        entries = tags.parse_entries(rec) if rec else []
        try:
            plan = eng.build_plan(path, sc, entries, self._options)
            args = tags.change_args(tags.resolve_changes(path, plan.changes))
        except ValueError as exc:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : {exc}")
            return eng.STATE_ERROR, str(exc)
        if plan.is_empty:
            return eng.STATE_UNCHANGED, " ".join(plan.notes)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        before: dict = {}
        unrestorable: list[str] = []
        if args:
            resolved = tags.resolve_changes(path, plan.changes)
            before, unrestorable = tags.snapshot_before(entries, resolved)
            ok, msg = self.write_tags(path, args)
            if not ok:
                self.error_count += 1
                self.emit_log("ERR", f"{path.name} : {msg}")
                return eng.STATE_ERROR, msg
            if msg:
                self.emit_log("WARN", f"{path.name} : {msg}")
        if plan.mtime is not None:
            try:
                ts = plan.mtime.timestamp()
                os.utime(path, (ts, ts))
            except (OSError, OverflowError, ValueError) as exc:
                self.emit_log("WARN", f"{path.name} : date de modification non alignée ({exc}).")
        records.append(tags.snapshot_record(path, mtime, before, unrestorable))
        self.ok_count += 1
        note = " ".join(plan.notes)
        self.emit_log("OK", f"{path.name} : {len(args)} modification(s) écrite(s)" + (f" ({note})" if note else "."))
        return (eng.STATE_DONE if args else eng.STATE_MTIME_ONLY), note

    def _save_undo(self, records: list[dict]) -> str | None:
        if not records:
            return None
        key = tags.new_undo_key()
        try:
            storage = get_storage()
            storage.save(eng.UNDO_COLLECTION, key, tags.undo_record(records, []))
            for old in tags.keys_to_prune(storage.list_keys(eng.UNDO_COLLECTION)):
                storage.delete(eng.UNDO_COLLECTION, old)
        except Exception as exc:                                  # le stockage ne doit pas faire échouer le lot
            self.emit_log("WARN", f"Sauvegarde d'annulation impossible : {exc}")
            return None
        return key


class TakeoutUndoWorker(UndoWorker):
    """Rétablit l'état d'avant la dernière application de Takeout."""

    def __init__(self, exiftool: Path, key: str, parent: QObject | None = None) -> None:
        super().__init__(exiftool, key, parent, collection=eng.UNDO_COLLECTION)
