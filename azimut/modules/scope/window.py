"""Fenêtre d'Azimut Scope : compare deux dossiers (arborescence et nombre de fichiers), en lecture seule."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from azimut.core.module_spec import ModuleSpec
from azimut.modules.scope import MODULE, engine as eng
from azimut.modules.scope.workers import CompareWorker
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, PathField

COLUMNS = [
    Column("folder", "Dossier", width=380, stretch=True),
    Column("a", "Mère", width=90, align="right"),
    Column("b", "Fille", width=90, align="right"),
    Column("delta", "Écart", width=80, align="right"),
    Column("state", "État", width=160),
]


class ScopeWindow(ModuleWindow):
    """Compare le nombre de fichiers de deux arborescences, dossier par dossier."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._cmp: eng.Comparison | None = None
        self._build_ui()
        self.set_run_label("Lancer l'analyse")
        self.add_toolbar_button("Copier", self._copy, kind="secondary", tooltip="Copie le rapport texte")
        self.add_toolbar_button("Exporter…", self._export, kind="secondary", tooltip="Enregistre le rapport texte")
        self.add_toolbar_button("Inverser", self._swap, kind="secondary", tooltip="Échange la mère et la fille")
        self.resize(1100, 760)

    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        card = Card("Dossiers à comparer")
        self.field_a = PathField("Dossier mère (référence) :", mode="dir")
        self.field_b = PathField("Dossier fille (copie) :", mode="dir")
        card.body_layout.addWidget(self.field_a)
        card.body_layout.addWidget(self.field_b)
        note = QLabel("Lecture seule : rien n'est modifié, déplacé ni supprimé. Les fichiers texte, JSON, scripts, "
                      "sous-titres, fichiers système et cachés ne sont pas comptés.")
        note.setWordWrap(True)
        card.body_layout.addWidget(note)
        lay.addWidget(card)

        bar = QHBoxLayout()
        self.chk_diff = QCheckBox("Afficher seulement les différences")
        self.chk_diff.setChecked(bool(self.prefs.get("only_differences", False)))
        self.chk_diff.toggled.connect(self._on_diff_toggled)
        bar.addWidget(self.chk_diff)
        bar.addStretch(1)
        self.lbl_gap = QLabel()
        bar.addWidget(self.lbl_gap)
        lay.addLayout(bar)

        self.lbl_summary = QLabel("Choisissez deux dossiers puis lancez l'analyse.")
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)
        self.table = DataTable(COLUMNS)
        lay.addWidget(self.table, 1)
        self.set_content(central)
        self._lockables = [self.field_a, self.field_b, self.chk_diff]

    # ── Dossiers ────────────────────────────────────────────────────────────
    def open_paths(self, paths: list[str]) -> None:
        cleaned = [eng.clean_path(p) for p in paths if p]
        dirs = [str(Path(p) if Path(p).is_dir() else Path(p).parent) for p in cleaned]
        if dirs:
            self.field_a.set_path(dirs[0])
        if len(dirs) > 1:
            self.field_b.set_path(dirs[1])

    def _swap(self) -> None:
        if self.is_busy():
            return
        a, b = self.field_a.path(), self.field_b.path()
        self.field_a.set_path(b)
        self.field_b.set_path(a)

    # ── Analyse ─────────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        a, b = eng.clean_path(self.field_a.path()), eng.clean_path(self.field_b.path())
        if not a or not b:
            QMessageBox.warning(self, "Champs vides", "Renseignez les deux dossiers.")
            return
        try:
            if Path(a).resolve() == Path(b).resolve():
                QMessageBox.warning(self, "Même dossier", "Les deux chemins sont identiques.")
                return
        except (ValueError, OSError):
            pass
        self.run_worker(CompareWorker(a, b), on_completed=self._on_done, lock_widgets=self._lockables,
                        on_failed=lambda msg: self.log_panel.append_log("ERR", f"Échec de l'analyse : {msg}"),
                        history_action="Comparaison de dossiers")

    def _on_done(self, result: object) -> None:
        if isinstance(result, eng.Comparison):
            self._cmp = result
            self._render()

    def _on_diff_toggled(self, value: bool) -> None:
        self.prefs.set("only_differences", bool(value))
        self._render()

    def _render(self) -> None:
        cmp = self._cmp
        if cmp is None:
            return
        rows = []
        for r in cmp.rows:
            if self.chk_diff.isChecked() and r.state == eng.STATE_EQUAL:
                continue
            rows.append({"folder": eng.tree_label(r.folder), "a": "—" if r.count_a is None else r.count_a,
                         "b": "—" if r.count_b is None else r.count_b,
                         "delta": f"{r.delta:+d}" if r.state != eng.STATE_EQUAL else "0",
                         "state": r.state, "_tag": eng.STATE_TAGS[r.state]})
        self.table.data_model.set_rows(rows)
        self.lbl_summary.setText(f"MÈRE : {cmp.a.folders} sous-dossiers · {cmp.a.files} fichiers     "
                                 f"FILLE : {cmp.b.folders} sous-dossiers · {cmp.b.files} fichiers")
        self.lbl_gap.setText("✔ Écart : 0 — identiques" if cmp.identical else
                             f"Écart : {cmp.folder_delta:+d} dossiers · {cmp.file_delta:+d} fichiers")

    # ── Rapport ─────────────────────────────────────────────────────────────
    def _report(self) -> str | None:
        return eng.report_text(self._cmp, self.chk_diff.isChecked()) if self._cmp else None

    def _copy(self) -> None:
        text = self._report()
        if text:
            QApplication.clipboard().setText(text)
            self.log_panel.append_log("OK", "Rapport copié dans le presse-papiers.")

    def _export(self) -> None:
        text = self._report()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter les résultats", "comparaison_dossiers.txt",
                                              "Texte (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self.log_panel.append_log("OK", f"Rapport exporté : {path}")
