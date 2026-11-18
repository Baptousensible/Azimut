"""Worker d'Azimut Scope : parcours des deux dossiers (lecture seule)."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject

from azimut.core.workers import BaseWorker
from azimut.modules.scope import engine as eng


class CompareWorker(BaseWorker):
    """Compte les fichiers de chaque dossier des deux arborescences. Résultat : `engine.Comparison`."""

    def __init__(self, folder_a: Path | str, folder_b: Path | str, parent: QObject | None = None,
                 *, file_kind: str = eng.FILE_KIND_DEFAULT,
                 file_kinds: Iterable[str] | None = None) -> None:
        super().__init__(parent)
        self._a, self._b = folder_a, folder_b
        self._file_kind = file_kind
        self._file_kinds = tuple(file_kinds) if file_kinds is not None else None
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def do_work(self) -> eng.Comparison:
        self.emit_status(self.tr("Analyse de la mère…"))
        a = eng.scan_structure(self._a, cancel=self.is_cancelled, file_kind=self._file_kind,
                               file_kinds=self._file_kinds)
        self.check_cancelled()
        self.emit_status(self.tr("Analyse de la fille…"))
        b = eng.scan_structure(self._b, cancel=self.is_cancelled, file_kind=self._file_kind,
                               file_kinds=self._file_kinds)
        self.check_cancelled()
        cmp = eng.compare(a, b)
        diffs = len(cmp.differences())
        self.ok_count, self.error_count = len(cmp.rows) - diffs, diffs
        self.summary = self.tr(
            "mère {a_files} fichiers / {a_folders} dossiers · fille {b_files} fichiers / "
            "{b_folders} dossiers · {diffs} dossier(s) différent(s)"
        ).format(a_files=a.files, a_folders=a.folders, b_files=b.files,
                 b_folders=b.folders, diffs=diffs)
        self.emit_log("OK" if cmp.identical else "WARN", self.summary)
        return cmp
