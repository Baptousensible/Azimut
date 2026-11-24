"""Workers d'Azimut Takeout : analyse des dossiers, application des dates et positions."""
from __future__ import annotations

import os
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from PySide6.QtCore import QObject

from azimut.core.fsutil import folder_lock
from azimut.core.storage import get_storage
from azimut.core.workers import BaseWorker
from azimut.modules.tags import engine as tags
from azimut.modules.tags.workers import UndoWorker, _ExifToolWorker
from azimut.modules.takeout import engine as eng

READ_BATCH = 50                       # fichiers lus en un seul appel ExifTool
READ_BATCH_BYTES = 256 * 1024 * 1024  # plafond par lecture groupée (grosses vidéos)
UNDO_CHECKPOINT_FILES = 25            # instantané d'annulation réécrit tous les N fichiers…
UNDO_CHECKPOINT_SECONDS = 2.0         # …ou toutes les N secondes (fichiers lents)


@dataclass
class _ReadState:
    """Tags relus une fois, avec l'état du fichier au moment de cette lecture."""
    entries: list[tags.TagEntry]
    size: int
    mtime_ns: int
    mtime: float
    atime: float


class AnalyzeWorker(BaseWorker):                          # pas besoin d'ExifTool pour analyser
    """Apparie médias et JSON. Résultat : `engine.Analysis`."""

    def __init__(self, folders: Sequence[Path | str], recursive: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folders = [Path(f) for f in folders]
        self._recursive = recursive

    def do_work(self) -> eng.Analysis:
        self.emit_status(self.tr("Recherche des médias et des JSON…"))
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
        self.summary = self.tr(
            "{media} média(s) : {ready} prêt(s), {no_json} sans JSON, {no_data} sans données ; "
            "{orphans} JSON sans média"
        ).format(media=len(total.items), ready=ready, no_json=total.count(eng.STATE_NO_JSON),
                 no_data=total.count(eng.STATE_NO_DATA), orphans=total.orphan_json)
        self.emit_log("INFO", self.summary)
        return total


class ApplyWorker(_ExifToolWorker):
    """Écrit dates et GPS fichier par fichier, avec instantané d'annulation.

    Les tags sont lus par lots (un appel ExifTool pour plusieurs fichiers) ; l'écriture reste
    fichier par fichier, dans l'ordre de la liste. L'instantané d'annulation est réécrit pendant le
    lot : un arrêt brutal ne peut pas laisser des fichiers modifiés sans retour possible.

    Résultat : {"states": {indice: (état, message)}, "key": clé d'annulation | None}.
    """

    def __init__(self, exiftool: Path, items: Sequence[eng.TakeoutItem], options: eng.Options,
                 parent: QObject | None = None, roots: Sequence[Path | str] | None = None) -> None:
        super().__init__(exiftool, parent)
        self._items = list(items)
        self._options = options
        self._roots = [Path(r) for r in roots] if roots else None
        self.result: dict | None = None        # renseigné même si le lot est annulé
        self._undo_saved = False

    def do_work(self) -> dict:
        todo = [(i, it) for i, it in enumerate(self._items) if it.state == eng.STATE_READY and it.sidecar]
        total = len(todo)
        states: dict[int, tuple[str, str]] = {}
        records: list[dict] = []
        key: str | None = None
        saved_len, last_save, done = 0, time.monotonic(), 0
        aborted = False
        try:
            with ExitStack() as locks:
                for root in self._lock_roots(todo):
                    locks.enter_context(folder_lock(root))
                for chunk in self._chunks(todo):
                    self.check_cancelled()
                    prepared = self._read_chunk(chunk)
                    for idx, item in chunk:
                        self.check_cancelled()
                        states[idx] = self._one(item, records, prepared.get(idx))
                        done += 1
                        self.emit_progress(done, total, item.path.name)
                        fresh = len(records) - saved_len
                        if fresh and (fresh >= UNDO_CHECKPOINT_FILES
                                      or time.monotonic() - last_save >= UNDO_CHECKPOINT_SECONDS):
                            key, ok = self._save_undo_step(records, key)
                            if not ok:
                                # Plus d'écriture sans retour arrière possible.
                                self.emit_log("ERR", "Instantané d'annulation non enregistrable : "
                                                     "arrêt du lot avant d'autres écritures.")
                                aborted = True
                                break
                            saved_len, last_save = len(records), time.monotonic()
                    if aborted:
                        break
        finally:
            key = self._save_undo(records, key)
            self.summary = self.tr("{ok} fichier(s) mis à jour, {errors} erreur(s)").format(
                ok=self.ok_count, errors=self.error_count)
            self.result = {"states": dict(states), "key": key}
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        self.check_cancelled()
        return self.result

    def _lock_roots(self, todo: Sequence[tuple[int, eng.TakeoutItem]]) -> list[Path]:
        """Dossiers verrouillés : les racines analysées, sinon un ancêtre commun par volume."""
        if self._roots:
            return sorted(set(self._roots), key=lambda p: str(p).lower())
        by_anchor: dict[str, list[str]] = {}
        for _i, item in todo:
            by_anchor.setdefault(Path(item.path).anchor, []).append(str(item.path.parent))
        roots: list[Path] = []
        for parts in by_anchor.values():
            try:
                roots.append(Path(os.path.commonpath(parts)))
            except ValueError:                      # volumes différents : verrou par dossier
                roots.extend(Path(p) for p in parts)
        return sorted(set(roots), key=lambda p: str(p).lower())

    @staticmethod
    def _chunks(todo: Sequence[tuple[int, eng.TakeoutItem]]) -> Iterator[list[tuple[int, eng.TakeoutItem]]]:
        """Lots de `READ_BATCH` fichiers, plafonnés en octets pour les grosses vidéos."""
        chunk: list[tuple[int, eng.TakeoutItem]] = []
        size = 0
        for pair in todo:
            try:
                length = pair[1].path.stat().st_size
            except OSError:
                length = 0
            if chunk and (len(chunk) >= READ_BATCH or size + length > READ_BATCH_BYTES):
                yield chunk
                chunk, size = [], 0
            chunk.append(pair)
            size += length
        if chunk:
            yield chunk

    def _read_chunk(self, chunk: Sequence[tuple[int, eng.TakeoutItem]]) -> dict[int, _ReadState | None]:
        """Lit les tags d'un lot en un seul appel ; l'état de chaque fichier est relevé AVANT la lecture."""
        prepared: dict[int, _ReadState | None] = {}
        to_read: list[tuple[int, Path, _ReadState]] = []
        for idx, item in chunk:
            path = item.path
            try:
                st = path.stat()
            except OSError:
                prepared[idx] = None                 # `_one` produira le message exact
                continue
            state = _ReadState([], st.st_size, st.st_mtime_ns, st.st_mtime, st.st_atime)
            if tags.write_refusal(path) is not None or not (st.st_mode & 0o222):
                prepared[idx] = state                # ni tags à lire, ni écriture possible
            else:
                to_read.append((idx, path, state))
        if not to_read:
            return prepared
        records = self.read_records([path for _i, path, _s in to_read], numeric=True)
        if records is None:
            if len(to_read) == 1:
                return prepared
            self.emit_log("WARN", "Lecture groupée impossible : lecture fichier par fichier.")
            records = [self._read_one_record(path) for _i, path, _s in to_read]
        for (idx, _path, state), rec in zip(to_read, records, strict=False):
            if not rec:
                prepared[idx] = None
            else:
                state.entries = tags.parse_entries(rec)
                prepared[idx] = state
        return prepared

    def _read_one_record(self, path: Path) -> dict | None:
        records = self.read_records([path], numeric=True)
        return records[0] if records else None

    def _one(self, item: eng.TakeoutItem, records: list[dict],
             state: _ReadState | None) -> tuple[str, str]:
        path, sc = item.path, item.sidecar
        if sc is None:                              # invariant rompu : erreur explicite, jamais d'AttributeError
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : données Takeout introuvables.")
            return eng.STATE_ERROR, "Données Takeout introuvables."
        try:
            st = path.stat()
        except OSError:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : fichier introuvable.")
            return eng.STATE_ERROR, "Fichier introuvable."
        # Aucun bit d'écriture : le fichier est protégé, mais ExifTool le réécrirait (mode conservé).
        if not (st.st_mode & 0o222):
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : fichier en lecture seule, aucune écriture.")
            return eng.STATE_ERROR, "Fichier en lecture seule : aucune écriture."
        if state is None:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : lecture préalable impossible, aucune écriture.")
            return eng.STATE_ERROR, "Lecture impossible."
        if (st.st_size, st.st_mtime_ns) != (state.size, state.mtime_ns):
            # Le fichier a changé depuis la lecture groupée : relire avant de décider quoi écrire.
            rec = self._read_one_record(path)
            if not rec:
                self.error_count += 1
                self.emit_log("ERR", f"{path.name} : modifié pendant le traitement, relecture impossible.")
                return eng.STATE_ERROR, "Fichier modifié pendant le traitement : relecture impossible."
            self.emit_log("WARN", f"{path.name} : modifié entre la lecture et l'écriture, relu.")
            state = _ReadState(tags.parse_entries(rec), st.st_size, st.st_mtime_ns, st.st_mtime, st.st_atime)
        mtime, atime, entries = state.mtime, state.atime, state.entries
        try:
            plan = eng.build_plan(path, sc, entries, self._options)
            args = tags.change_args(tags.resolve_changes(path, plan.changes))
        except ValueError as exc:
            self.error_count += 1
            self.emit_log("ERR", f"{path.name} : {exc}")
            return eng.STATE_ERROR, str(exc)
        if plan.is_empty:
            return eng.STATE_UNCHANGED, " ".join(plan.notes)
        before: dict = {}
        unrestorable: list[str] = []
        if args:
            problem = eng.write_space_problem(path)
            if problem:
                self.error_count += 1
                self.emit_log("ERR", f"{path.name} : {problem}")
                return eng.STATE_ERROR, problem
            resolved = tags.resolve_changes(path, plan.changes)
            before, unrestorable = tags.snapshot_before(entries, resolved)
            ok, msg = self.write_tags(path, args, backup=self._options.backup)
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
        records.append(tags.snapshot_record(path, mtime, before, unrestorable, atime))
        self.ok_count += 1
        note = " ".join(plan.notes)
        self.emit_log("OK", f"{path.name} : {len(args)} modification(s) écrite(s)" + (f" ({note})" if note else "."))
        return (eng.STATE_DONE if args else eng.STATE_MTIME_ONLY), note

    def _save_undo_step(self, records: list[dict], key: str | None) -> tuple[str | None, bool]:
        """Écrit l'instantané courant ; renvoie (clé, succès)."""
        if not records:
            return key, True
        key = key or tags.new_undo_key()
        try:
            get_storage().save(eng.UNDO_COLLECTION, key, tags.undo_record(records, []))
        except Exception as exc:                                  # le stockage ne doit pas faire échouer le lot
            self.emit_log("WARN", f"Sauvegarde d'annulation impossible : {exc}")
            return key, False
        self._undo_saved = True
        return key, True

    def _save_undo(self, records: list[dict], key: str | None = None) -> str | None:
        key, ok = self._save_undo_step(records, key)
        if ok and records:
            try:
                storage = get_storage()
                for old in tags.keys_to_prune(storage.list_keys(eng.UNDO_COLLECTION)):
                    if old != key:
                        storage.delete(eng.UNDO_COLLECTION, old)
            except Exception as exc:
                self.emit_log("WARN", f"Nettoyage des annulations impossible : {exc}")
        return key if ok or self._undo_saved else None


class TakeoutUndoWorker(UndoWorker):
    """Rétablit l'état d'avant la dernière application de Takeout."""

    def __init__(self, exiftool: Path, key: str, parent: QObject | None = None) -> None:
        super().__init__(exiftool, key, parent, collection=eng.UNDO_COLLECTION)
