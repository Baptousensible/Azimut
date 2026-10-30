"""Fenêtre d'Azimut Rename : propose, vérifie puis applique des noms contenant la date de prise de vue."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import get_storage
from azimut.modules.rename import MODULE, engine as eng
from azimut.modules.rename.workers import ApplyWorker, ScanResult, ScanWorker, UndoWorker
from azimut.modules.tags import engine as tags
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, DropZone, ImagePreview

COLUMNS = [
    Column("check", "☑", width=40, align="center"),
    Column("before", "Avant", width=340, stretch=True),
    Column("after", "Après", width=340, stretch=True),
    Column("date", "Date retenue", width=150),
    Column("status", "Statut", width=150),
]
FILTERS = ("À renommer", "Tout", "Déjà conformes", "Ignorés (décochés)", "Sans date", "Date de modification (à vérifier)")
_Yes, _No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
CONFIRM_LIMIT = 3000


def _passes(row: eng.RenameRow, index: int) -> bool:
    return [row.status == eng.STATUS_RENAME, True, row.status == eng.STATUS_OK,
            row.status == eng.STATUS_SKIPPED, row.status == eng.STATUS_NO_DATE,
            row.source == eng.SOURCE_MTIME][index]


class RenameWindow(ModuleWindow):
    """Renommage par lots selon la date de prise de vue, avec confirmation détaillée et annulation."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._rows: list[eng.RenameRow] = []
        self._entries: dict[str, set[str]] = {}
        self._visible: list[int] = []
        self._base_dir: Path | None = None
        self._folder: Path | None = None
        self._exiftool: Path | None = None
        self._applied: dict[int, eng.RenameRow] = {}
        self._build_ui()
        self.set_run_label("Renommer…")
        self._undo_btn = self.add_toolbar_button(
            "Annuler le dernier renommage", self._undo_last, kind="secondary",
            tooltip="Rend leur ancien nom aux fichiers du dernier renommage")
        self.add_toolbar_button("Exporter le plan (CSV)…", self._export_plan, kind="secondary")
        self.add_toolbar_button("Ouvrir un dossier…", self.on_open, kind="secondary")
        self.show_binary_status(("exiftool", "ffmpeg"))
        self._ensure_exiftool(quiet=False)
        self._refresh_undo_button()
        self.resize(1250, 800)

    # ── Interface ───────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        self.drop = DropZone("Glissez ici le dossier de photos, vidéos ou fichiers audio à renommer",
                             accept_files=False)
        self.drop.pathsDropped.connect(self.open_paths)
        lay.addWidget(self.drop)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Afficher :"))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(FILTERS)
        self.cmb_filter.currentIndexChanged.connect(lambda _i: self._render())
        bar.addWidget(self.cmb_filter)
        self.btn_all = QPushButton("Tout cocher (affichés)")
        self.btn_none = QPushButton("Tout décocher (affichés)")
        self.btn_all.clicked.connect(lambda: self._set_checked(list(range(len(self._visible))), True))
        self.btn_none.clicked.connect(lambda: self._set_checked(list(range(len(self._visible))), False))
        bar.addWidget(self.btn_all)
        bar.addWidget(self.btn_none)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.table = DataTable(COLUMNS)
        self.table.rowActivated.connect(lambda r: self._set_checked([r], None))
        self.table.selectionRowsChanged.connect(lambda _rows: self._on_selection())
        lay.addWidget(self.table, 1)
        self.lbl_summary = QLabel("Aucun dossier analysé.")
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)
        self.set_content(central)

        side = QWidget()
        sl = QVBoxLayout(side)
        p = self.prefs

        card = Card("Nom proposé")
        card.body_layout.addWidget(QLabel("Format de la date"))
        self.cmb_format = QComboBox()
        for fid, (label, _fmt) in eng.DATE_FORMATS.items():
            self.cmb_format.addItem(label, fid)
        self.cmb_format.setCurrentIndex(max(0, self.cmb_format.findData(p.get("format", eng.DEFAULT_FORMAT))))
        self.cmb_format.currentIndexChanged.connect(lambda _i: self._on_option("format", self.cmb_format.currentData()))
        card.body_layout.addWidget(self.cmb_format)
        self.chk_prefix = QCheckBox("Conserver le préfixe personnalisé")
        self.chk_type = QCheckBox("Ajouter IMG / VID / AUD au début")
        self.chk_mtime = QCheckBox("Sans métadonnées : utiliser la date de modification")
        self.chk_recursive = QCheckBox("Inclure les sous-dossiers")
        for chk, key, default in ((self.chk_prefix, "keep_prefix", True), (self.chk_type, "add_type", True),
                                  (self.chk_mtime, "use_mtime", False), (self.chk_recursive, "recursive", True)):
            chk.setChecked(bool(p.get(key, default)))
            chk.toggled.connect(lambda v, k=key: self._on_option(k, bool(v)))
            card.body_layout.addWidget(chk)
        sl.addWidget(card)

        card_p = Card("Préfixe personnalisé")
        self.edit_prefix = QLineEdit()
        self.edit_prefix.setPlaceholderText("(aucun) — ex. Vacances Corse")
        self.edit_prefix.returnPressed.connect(self._apply_prefix)
        self.btn_set_prefix = QPushButton("Appliquer à la sélection")
        self.btn_set_prefix.clicked.connect(self._apply_prefix)
        self.btn_auto_prefix = QPushButton("Rétablir le préfixe automatique")
        self.btn_auto_prefix.clicked.connect(self._reset_prefix)
        for w in (self.edit_prefix, self.btn_set_prefix, self.btn_auto_prefix):
            card_p.body_layout.addWidget(w)
        sl.addWidget(card_p)

        card_s = Card("Sélection")
        self.btn_check = QPushButton("Cocher la sélection")
        self.btn_uncheck = QPushButton("Décocher la sélection")
        self.btn_open_file = QPushButton("Ouvrir le fichier")
        self.btn_check.clicked.connect(lambda: self._set_checked(self.table.selected_rows(), True))
        self.btn_uncheck.clicked.connect(lambda: self._set_checked(self.table.selected_rows(), False))
        self.btn_open_file.clicked.connect(self._open_file)
        for w in (self.btn_check, self.btn_uncheck, self.btn_open_file):
            card_s.body_layout.addWidget(w)
        self.preview = ImagePreview(size=230)
        card_s.body_layout.addWidget(self.preview)
        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        card_s.body_layout.addWidget(self.lbl_info)
        hint = QLabel("Entrée ou double-clic : cocher / décocher la ligne. Le contenu, les métadonnées et la date "
                      "de modification ne sont jamais touchés.")
        hint.setWordWrap(True)
        card_s.body_layout.addWidget(hint)
        sl.addWidget(card_s)
        sl.addStretch(1)
        self.set_side_panel(side, "Réglages")
        self._lockables = [self.drop, self.cmb_format, self.chk_prefix, self.chk_type, self.chk_mtime,
                           self.chk_recursive, self.cmb_filter, self.btn_all, self.btn_none, self.edit_prefix,
                           self.btn_set_prefix, self.btn_auto_prefix, self.btn_check, self.btn_uncheck]

    def _options(self) -> eng.Options:
        return eng.Options(fmt=str(self.cmb_format.currentData()), keep_prefix=self.chk_prefix.isChecked(),
                           add_type=self.chk_type.isChecked(), use_mtime=self.chk_mtime.isChecked())

    def _on_option(self, key: str, value: object) -> None:
        self.prefs.set(key, value)
        if key == "use_mtime":
            for r in self._rows:
                if r.date_meta is None and r.date_mtime is not None:
                    r.checked = bool(value)
        if key != "recursive":
            self._recompute()

    # ── Binaire ─────────────────────────────────────────────────────────────
    def _ensure_exiftool(self, quiet: bool = True) -> Path | None:
        try:
            self._exiftool = binaries.require("exiftool")
        except BinaryNotFoundError as exc:
            self._exiftool = None
            if not quiet:
                self.log_panel.append_log("ERR", str(exc))
                self.log_panel.append_log("WARN", "Sans ExifTool, seule la date de modification des fichiers peut "
                                                  "servir (case « Sans métadonnées »). Indiquez son chemin dans "
                                                  "les Préférences.")
        return self._exiftool

    # ── Analyse ─────────────────────────────────────────────────────────────
    def on_open(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Dossier à analyser", start)
        if folder:
            Preferences.instance().set("last_dir", folder)
            self.open_paths([folder])

    def open_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        first = Path(paths[0])
        folder = first if first.is_dir() else first.parent
        if self.is_busy():
            self.log_panel.append_log("WARN", "Un traitement est en cours : réessayez dans un instant.")
            return
        self._folder = folder
        self.drop.set_text(f"✅  {folder.name or folder}")
        self._ensure_exiftool(quiet=False)
        self._rows, self._entries, self._visible = [], {}, []
        self._render()
        self.log_panel.append_log("INFO", f"Analyse — {folder}")
        self.run_worker(ScanWorker(folder, self.chk_recursive.isChecked(), self._exiftool),
                        on_completed=self._on_scanned, lock_widgets=self._lockables)

    def _on_scanned(self, result: object) -> None:
        if not isinstance(result, ScanResult):
            return
        self._rows, self._entries, self._base_dir = result.rows, result.entries, result.base_dir
        use = self.chk_mtime.isChecked()
        for r in self._rows:
            if r.date_meta is None and r.date_mtime is not None:
                r.checked = use
        self._recompute()
        if any(r.status == eng.STATUS_RENAME for r in self._rows):
            self.cmb_filter.setCurrentIndex(0)
            self.log_panel.append_log("INFO", "Vérifiez la colonne « Après », décochez ce que vous ne voulez pas "
                                              "modifier, puis « Renommer… » (confirmation détaillée demandée).")
        elif self._rows:
            self.cmb_filter.setCurrentIndex(1)

    # ── Calcul et affichage ─────────────────────────────────────────────────
    def _recompute(self) -> None:
        eng.compute_names(self._rows, self._entries, self._options())
        self._render()

    def _display(self, path: Path) -> str:
        if self._base_dir:
            try:
                return str(path.relative_to(self._base_dir))
            except ValueError:
                pass
        return str(path)

    def _render(self) -> None:
        mode = self.cmb_filter.currentIndex()
        self._visible = [i for i, r in enumerate(self._rows) if _passes(r, mode)]
        rows = []
        for i in self._visible:
            r = self._rows[i]
            box = "—" if r.status in (eng.STATUS_NO_DATE, eng.STATUS_OK) else ("☑" if r.checked else "☐")
            after = self._display(r.new_path) if r.status == eng.STATUS_RENAME else "(inchangé)"
            tag = "warn" if r.status == eng.STATUS_RENAME and r.source == eng.SOURCE_MTIME \
                else eng.TAG_BY_STATUS.get(r.status, "")
            rows.append({"check": box, "before": self._display(r.path), "after": after,
                         "date": r.date.strftime("%Y-%m-%d %H:%M:%S") if r.date else "—", "status": r.status,
                         "_tag": tag})
        self.table.data_model.set_rows(rows)
        self._update_summary()
        self._on_selection()

    def _update_summary(self) -> None:
        if not self._rows:
            self.lbl_summary.setText("Aucun dossier analysé.")
            return
        count = lambda s: sum(1 for r in self._rows if r.status == s)       # noqa: E731
        self.lbl_summary.setText(
            f"{len(self._visible)} affiché(s) / {len(self._rows)} fichiers · à renommer {count(eng.STATUS_RENAME)} · "
            f"conformes {count(eng.STATUS_OK)} · décochés {count(eng.STATUS_SKIPPED)} · "
            f"sans date {count(eng.STATUS_NO_DATE)}")

    def _selected(self) -> list[eng.RenameRow]:
        return [self._rows[self._visible[i]] for i in self.table.selected_rows() if 0 <= i < len(self._visible)]

    def _on_selection(self) -> None:
        sel = self._selected()
        if len(sel) != 1:
            self.preview.clear()
            self.lbl_info.setText(f"{len(sel)} fichiers sélectionnés" if sel else "")
            return
        r = sel[0]
        self.preview.show_image(r.path)
        after = r.new_name if r.status == eng.STATUS_RENAME else "(inchangé)"
        self.lbl_info.setText(f"Avant : {r.old_name}\nAprès : {after}\nDate : "
                              f"{r.date.strftime('%Y-%m-%d %H:%M:%S') if r.date else '—'}\n"
                              f"Source : {r.source or '—'}\nStatut : {r.status}")
        if not self.edit_prefix.hasFocus():
            self.edit_prefix.setText(r.prefix)

    # ── Cases à cocher et préfixe ───────────────────────────────────────────
    def _set_checked(self, view_rows: list[int], value: bool | None) -> None:
        """value=None : inverse chaque ligne. Seules les lignes datées et non conformes sont concernées."""
        if self.is_busy():
            return
        for i in view_rows:
            if not 0 <= i < len(self._visible):
                continue
            r = self._rows[self._visible[i]]
            if r.status in (eng.STATUS_NO_DATE, eng.STATUS_OK):
                continue
            r.checked = (not r.checked) if value is None else value
        self._recompute()

    def _apply_prefix(self) -> None:
        sel = self._selected()
        if self.is_busy() or not sel:
            return
        prefix = eng.sanitize_prefix(self.edit_prefix.text())
        for r in sel:
            r.prefix = prefix
        if not self.chk_prefix.isChecked():
            self.chk_prefix.setChecked(True)                 # déclenche le recalcul
        else:
            self._recompute()
        self.log_panel.append_log("INFO", f"Préfixe « {prefix or '(aucun)'} » défini pour {len(sel)} fichier(s).")

    def _reset_prefix(self) -> None:
        if self.is_busy():
            return
        for r in self._selected():
            r.prefix = r.auto_prefix
        self._recompute()

    def _open_file(self) -> None:
        sel = self._selected()
        if sel and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(sel[0].path))):
            self.log_panel.append_log("WARN", f"Impossible d'ouvrir : {sel[0].path}")

    # ── Application ─────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        todo = [r for r in self._rows if r.status == eng.STATUS_RENAME]
        if not todo:
            QMessageBox.information(self, "Rien à renommer", "Aucun fichier coché à renommer.")
            return
        if not self._confirm(todo):
            return
        pairs = [(i, r.path, r.new_path) for i, r in enumerate(todo)]
        self._applied = dict(enumerate(todo))
        self.run_worker(ApplyWorker(pairs), on_completed=self._on_applied, lock_widgets=self._lockables,
                        history_action="Renommage par date")

    def _confirm(self, todo: list[eng.RenameRow]) -> bool:
        """Liste complète avant → après, avant toute action."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Confirmer le renommage")
        dlg.resize(1000, 640)
        lay = QVBoxLayout(dlg)
        n_mtime = sum(1 for r in todo if r.source == eng.SOURCE_MTIME)
        n_pref = sum(1 for r in todo if r.prefix and self.chk_prefix.isChecked())
        text = (f"{len(todo)} fichier(s) vont être renommés ({n_pref} avec préfixe conservé).\n"
                "Aucun contenu ni métadonnée n'est modifié ; l'annulation du dernier renommage reste possible.")
        if n_mtime:
            text += f"\n⚠️ {n_mtime} nom(s) basé(s) sur la date de modification (pas sur les métadonnées)."
        label = QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        lines = []
        for r in todo[:CONFIRM_LIMIT]:
            lines.append(f"  {self._display(r.path)}\n→ {self._display(r.new_path)}\n")
        if len(todo) > CONFIRM_LIMIT:
            lines.append(f"… et {len(todo) - CONFIRM_LIMIT} autre(s) (non affichés ici).")
        box.setPlainText("\n".join(lines))
        lay.addWidget(box, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton(f"Renommer {len(todo)} fichier(s)", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setDefault(True)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def _on_applied(self, result: object) -> None:
        res = result if isinstance(result, dict) else {}
        for idx, (ok, _err) in (res.get("results") or {}).items():
            row = self._applied.get(idx)
            if not ok or row is None:
                continue
            entries = self._entries.setdefault(str(row.folder), set())
            entries.discard(row.old_name.lower())
            entries.add(row.new_name.lower())
            row.path, row.old_name, row.checked = row.new_path, row.new_name, False
        self._applied = {}
        self._recompute()
        QTimer.singleShot(0, self._refresh_undo_button)

    # ── Annulation ──────────────────────────────────────────────────────────
    def _refresh_undo_button(self) -> None:
        try:
            keys = get_storage().list_keys(eng.UNDO_COLLECTION)
        except Exception:                                     # noqa: BLE001
            keys = []
        self._undo_btn.setEnabled(bool(keys))

    def _undo_last(self) -> None:
        if self.is_busy():
            return
        storage = get_storage()
        key = tags.latest_undo_key(storage.list_keys(eng.UNDO_COLLECTION))
        rec = storage.load(eng.UNDO_COLLECTION, key) if key else None
        if not key or not rec:
            QMessageBox.information(self, "Rien à annuler", "Aucun renommage à annuler.")
            self._refresh_undo_button()
            return
        when = str(rec.get("timestamp", "")).replace("T", " ")
        if QMessageBox.question(self, "Annuler le dernier renommage",
                                f"Rendre leur ancien nom aux {len(rec.get('pairs', []))} fichier(s) renommés le "
                                f"{when} ?", _Yes | _No, _No) != _Yes:
            return
        self.run_worker(UndoWorker(key), on_completed=self._on_undone, lock_widgets=self._lockables,
                        history_action="Annulation du renommage")

    def _on_undone(self, _result: object) -> None:
        QTimer.singleShot(0, self._refresh_undo_button)
        if self._folder is not None:
            QTimer.singleShot(0, lambda: self.open_paths([str(self._folder)]))

    # ── Export ──────────────────────────────────────────────────────────────
    def _export_plan(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Rien à exporter", "Aucun résultat d'analyse.")
            return
        default = f"plan_renommage_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Exporter le plan", default, "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(eng.plan_csv_lines(self._rows))
        except OSError as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self.log_panel.append_log("OK", f"Plan exporté : {path}")
