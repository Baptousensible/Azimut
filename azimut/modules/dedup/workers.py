"""Threads d'Azimut Dedup : analyse, application, annulation d'une application."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject

from azimut.core.workers import BaseWorker, CancelledError
from azimut.modules.dedup import actions
from azimut.modules.dedup.actions import ApplyOptions, ApplyReport, UndoReport
from azimut.modules.dedup.engine import Hooks, ScanCancelled, ScanOptions, ScanResult, scan
from azimut.modules.dedup.model import Group


class ScanWorker(BaseWorker):
    """Inventaire, empreintes, dHash et regroupement. `completed` transmet un ScanResult."""

    def __init__(self, options: ScanOptions, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.options = options
        self.result: ScanResult | None = None
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def _pmap(self, fn: Callable[[Any], Any], items: Sequence[Any], label: str) -> list[Any]:
        return self.map_parallel(fn, items, workers=4, label=label)

    def do_work(self) -> ScanResult:
        hooks = Hooks(cancel=self.is_cancelled, log=self.emit_log, progress=self.emit_progress,
                      status=self.emit_status, map_parallel=self._pmap)
        try:
            result = scan(self.options, hooks)
        except ScanCancelled:
            raise CancelledError() from None
        self.result = result
        self.ok_count = len(result.groups)
        self.error_count = result.stats.unreadable
        self.summary = result.stats.summary()
        return result


class ApplyWorker(BaseWorker):
    """Applique les suppressions. `completed` transmet un ApplyReport ; en cas d'annulation,
    le rapport partiel reste disponible dans `self.report`."""

    def __init__(self, groups: list[Group], options: ApplyOptions, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.groups = groups
        self.options = options
        self.report: ApplyReport | None = None
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def do_work(self) -> ApplyReport:
        hooks = Hooks(cancel=self.is_cancelled, log=self.emit_log,
                      progress=self.emit_progress, status=self.emit_status)
        self.emit_status("Application des suppressions…")
        report = actions.apply_groups(self.groups, self.options, hooks)
        self.report = report
        self.ok_count = report.moved
        self.error_count = report.errors + len(report.skipped)
        self.summary = (f"{report.moved} fichier(s) traité(s), {len(report.skipped)} ignoré(s), "
                        f"{report.errors} erreur(s)")
        if report.cancelled:
            raise CancelledError()
        return report


class UndoWorker(BaseWorker):
    """Relit un journal.csv et remet les fichiers à leur place. `completed` transmet un UndoReport."""

    def __init__(self, journal: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.journal = Path(journal)
        self.report: UndoReport | None = None
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    def do_work(self) -> UndoReport:
        hooks = Hooks(cancel=self.is_cancelled, log=self.emit_log,
                      progress=self.emit_progress, status=self.emit_status)
        self.emit_status("Restauration depuis le journal…")
        report = actions.undo_journal(self.journal, hooks)
        self.report = report
        self.ok_count = report.restored
        self.error_count = report.errors
        self.summary = (f"{report.restored} fichier(s) restauré(s), {report.skipped} ignoré(s), "
                        f"{report.errors} erreur(s)")
        if report.cancelled:
            raise CancelledError()
        return report
