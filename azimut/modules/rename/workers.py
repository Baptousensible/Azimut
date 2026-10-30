"""Workers d'Azimut Rename : analyse (dates), application des renommages, annulation."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject

from azimut.core.storage import get_storage
from azimut.core.workers import BaseWorker
from azimut.modules.rename import engine as eng
from azimut.modules.tags import engine as tags


@dataclass
class ScanResult:
    rows: list[eng.RenameRow] = field(default_factory=list)
    entries: dict[str, set[str]] = field(default_factory=dict)
    base_dir: Path | None = None
    without_meta: int = 0


class ScanWorker(BaseWorker):
    """Liste les médias d'un dossier et lit leur date de prise de vue (ExifTool, par lots). Résultat : ScanResult."""

    def __init__(self, folder: Path | str, recursive: bool, exiftool: Path | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folder = Path(folder)
        self._recursive = recursive
        self._exiftool = Path(exiftool) if exiftool else None

    # ── Lecture d'un lot ──
    def _read_batch(self, batch: Sequence[Path]) -> list[datetime | None]:
        assert self._exiftool is not None
        records = self._run([str(p) for p in batch], len(batch))
        if records is None or len(records) != len(batch):
            if len(batch) > 1:                                  # sortie incohérente : fichier par fichier
                return [d for p in batch for d in self._read_batch([p])]
            return [None]
        return [eng.date_from_record(r) for r in records]

    def _run(self, lines: list[str], count: int) -> list[dict] | None:
        argfile = tags.write_argfile(lines)
        try:
            res = self.run_process(eng.read_command(self._exiftool, argfile), timeout=max(120.0, 5.0 * count))
        finally:
            try:
                argfile.unlink()
            except OSError:
                pass
        if res.timed_out or res.cancelled:
            return None
        return eng.parse_json_records(res.stdout)

    def do_work(self) -> ScanResult:
        self.emit_status("Recherche des fichiers…")
        inv = eng.list_files(self._folder, self._recursive, cancel=self.is_cancelled)
        self.check_cancelled()
        total = len(inv.files)
        if inv.ignored:
            top = sorted(inv.ignored.items(), key=lambda kv: -kv[1])[:12]
            self.emit_log("INFO", f"{sum(inv.ignored.values())} fichier(s) d'un autre type ignorés : "
                                  + ", ".join(f"{e} ×{n}" for e, n in top))
        if inv.parasites:
            self.emit_log("INFO", f"{inv.parasites} fichier(s) parasites (« ._* », temporaires) ignorés.")
        if total == 0:
            self.emit_log("WARN", "Aucun fichier image, vidéo ou audio trouvé.")
            return ScanResult(base_dir=self._folder)
        self.emit_log("INFO", f"{total} fichier(s) à analyser.")

        if self._exiftool is None:
            self.emit_log("ERR", "ExifTool introuvable : aucune date de métadonnées n'a pu être lue.")
            dates: list[datetime | None] = [None] * total
        else:
            batches = [inv.files[i:i + eng.READ_CHUNK] for i in range(0, total, eng.READ_CHUNK)]
            self.emit_status("Lecture des dates de prise de vue…")
            out = self.map_parallel(self._read_batch, batches, workers=max(2, min(4, os.cpu_count() or 2)),
                                    label="Lecture des dates")
            dates = []
            for batch, result in zip(batches, out):
                dates.extend(result if result is not None and len(result) == len(batch) else [None] * len(batch))
        rows = [eng.make_row(p, d) for p, d in zip(inv.files, dates)]
        without = sum(1 for r in rows if r.date_meta is None)
        self.ok_count, self.error_count = total - without, without
        self.summary = f"{total - without} avec date de métadonnées, {without} sans"
        self.emit_log("OK", f"Analyse terminée : {self.summary}.")
        return ScanResult(rows=rows, entries=eng.folder_entries(rows), base_dir=self._folder, without_meta=without)


class ApplyWorker(BaseWorker):
    """Applique les renommages cochés. L'enregistrement d'annulation est écrit AVANT le premier renommage.

    Résultat : {"results": {id: (ok, erreur)}, "key": clé d'annulation | None}.
    """

    def __init__(self, pairs: Sequence[tuple[int, Path, Path]], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pairs = list(pairs)
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def do_work(self) -> dict:
        key = self._save_undo([(s, d) for _i, s, d in self._pairs])
        results = eng.execute_renames(self._pairs, cancel=self.is_cancelled, log=self.emit_log,
                                      progress=self.emit_progress)
        self.ok_count = sum(1 for ok, _ in results.values() if ok)
        self.error_count = sum(1 for ok, _ in results.values() if not ok)
        if key:
            done = [(s, d) for i, s, d in self._pairs if results.get(i, (False, ""))[0]]
            if done:
                self._save_undo(done, key)                    # ne garde que ce qui a réellement été renommé
            else:
                try:
                    get_storage().delete(eng.UNDO_COLLECTION, key)
                except Exception:                             # noqa: BLE001
                    pass
                key = None
        self.summary = f"{self.ok_count} fichier(s) renommé(s), {self.error_count} erreur(s)"
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        self.check_cancelled()
        return {"results": results, "key": key}

    def _save_undo(self, pairs: Sequence[tuple[Path, Path]], key: str | None = None) -> str | None:
        if not pairs:
            return key
        key = key or tags.new_undo_key()
        try:
            storage = get_storage()
            storage.save(eng.UNDO_COLLECTION, key, eng.undo_record(pairs, datetime.now().isoformat(timespec="seconds")))
            for old in tags.keys_to_prune(storage.list_keys(eng.UNDO_COLLECTION)):
                storage.delete(eng.UNDO_COLLECTION, old)
        except Exception as exc:                              # le stockage ne doit pas faire échouer le lot
            self.emit_log("WARN", f"Sauvegarde d'annulation impossible : {exc}")
            return None
        return key


class UndoWorker(BaseWorker):
    """Rend aux fichiers leur ancien nom d'après un enregistrement d'annulation. Résultat : (restaurés, erreurs)."""

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def do_work(self) -> tuple[int, int]:
        record = get_storage().load(eng.UNDO_COLLECTION, self._key)
        if not record:
            self.emit_log("ERR", "Enregistrement d'annulation introuvable.")
            return 0, 0
        plan, skipped = eng.plan_undo(record)
        if skipped:
            self.emit_log("WARN", f"{len(skipped)} fichier(s) absents sous leur nouveau nom (non renommés, ou "
                                  f"déplacés depuis) : ignorés. Ex. : {', '.join(skipped[:3])}")
        results = eng.execute_renames(plan, cancel=self.is_cancelled, log=self.emit_log, progress=self.emit_progress)
        self.ok_count = sum(1 for ok, _ in results.values() if ok)
        self.error_count = sum(1 for ok, _ in results.values() if not ok)
        self.summary = f"{self.ok_count} fichier(s) restauré(s), {self.error_count} erreur(s)"
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        self.check_cancelled()
        if not self.error_count:
            try:
                get_storage().delete(eng.UNDO_COLLECTION, self._key)     # annulé : n'est plus proposé
            except Exception:                                            # noqa: BLE001
                pass
        return self.ok_count, self.error_count
