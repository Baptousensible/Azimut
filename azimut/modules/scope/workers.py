"""Worker d'Azimut Scope : parcours des deux dossiers (lecture seule)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from azimut.core.workers import BaseWorker
from azimut.modules.scope import engine as eng


class CompareWorker(BaseWorker):
    """Compte les fichiers de chaque dossier des deux arborescences. Résultat : `engine.Comparison`."""

    def __init__(self, folder_a: Path | str, folder_b: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._a, self._b = folder_a, folder_b
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def do_work(self) -> eng.Comparison:
        self.emit_status("Analyse de la mère…")
        a = eng.scan_structure(self._a, cancel=self.is_cancelled)
        self.check_cancelled()
        self.emit_status("Analyse de la fille…")
        b = eng.scan_structure(self._b, cancel=self.is_cancelled)
        self.check_cancelled()
        cmp = eng.compare(a, b)
        diffs = len(cmp.differences())
        self.ok_count, self.error_count = len(cmp.rows) - diffs, diffs
        self.summary = (f"mère {a.files} fichiers / {a.folders} dossiers · fille {b.files} fichiers / "
                        f"{b.folders} dossiers · {diffs} dossier(s) différent(s)")
        self.emit_log("OK" if cmp.identical else "WARN", self.summary)
        return cmp
