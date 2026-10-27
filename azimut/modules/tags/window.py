"""Fenêtre d'Azimut Tags : liste de fichiers, tableau des métadonnées, modifications en attente."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.fsutil import format_size
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import get_storage
from azimut.modules.tags import engine as eng
from azimut.modules.tags.workers import ReadWorker, UndoWorker, WriteWorker
from azimut.ui.module_window import ModuleWindow
from azimut.ui.theme import token
from azimut.ui.widgets import Card, Column, DataTable, DropZone, ImagePreview

log = get_logger(__name__)

OP_LABELS = {"set": "Définir", "delete": "Supprimer"}
_Yes, _No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


class _GpsDialog(QDialog):
    """Saisie et validation d'une position GPS."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Définir la position GPS")
        self.values: tuple[float, float, float] | None = None
        form = QFormLayout(self)
        self._lat, self._lon, self._alt = QLineEdit(), QLineEdit(), QLineEdit()
        self._lat.setPlaceholderText("ex. 48.8584 (entre -90 et 90)")
        self._lon.setPlaceholderText("ex. 2.2945 (entre -180 et 180)")
        self._alt.setPlaceholderText("facultative, en mètres")
        form.addRow("Latitude :", self._lat)
        form.addRow("Longitude :", self._lon)
        form.addRow("Altitude :", self._alt)
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {token('err_txt')};")
        form.addRow(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ajouter")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate(self) -> None:
        try:
            self.values = eng.validate_gps(self._lat.text(), self._lon.text(), self._alt.text())
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        self.accept()


class TagsWindow(ModuleWindow):
    """Éditeur de métadonnées : rien n'est écrit avant « Appliquer »."""

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._files: list[eng.FileTags] = []
        self._index: dict[str, int] = {}
        self._current_index: int | None = None
        self._current_entries: list[eng.TagEntry] = []
        self._pending: list[eng.TagChange] = []
        self._exiftool: Path | None = None
        self._lockables: list[QWidget] = []
        self._build_ui()
        self.set_run_label("Appliquer")
        self._undo_btn = self.add_toolbar_button(
            "Annuler la dernière application", self._undo_last, kind="secondary",
            tooltip="Rétablit les valeurs d'avant la dernière application")
        self.add_toolbar_button("Ouvrir un dossier…", self._open_folder, kind="secondary")
        self._init_binary()
        self._refresh_undo_button()

    # ── Construction ─────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        self._drop = DropZone("📂  Glissez des fichiers ou des dossiers ici (ou cliquez pour parcourir)")
        self._drop.setMinimumHeight(64)
        self._drop.setMaximumHeight(90)
        self._drop.pathsDropped.connect(self.open_paths)
        top.addWidget(self._drop, 1)
        opts = QVBoxLayout()
        self._recursive = QCheckBox("Inclure les sous-dossiers")
        self._recursive.setChecked(bool(self.prefs.get("recursive", True)))
        self._recursive.toggled.connect(lambda v: self.prefs.set("recursive", bool(v)))
        clear_btn = QPushButton("Vider la liste")
        clear_btn.clicked.connect(self._clear_files)
        opts.addWidget(self._recursive)
        opts.addWidget(clear_btn)
        top.addLayout(opts)
        outer.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        files_card = Card("Fichiers")
        self._files_table = DataTable([
            Column("name", "Nom", 220, stretch=True), Column("type", "Type", 60),
            Column("size", "Taille", 80, align="right"), Column("tags", "Tags", 55, align="right"),
            Column("state", "État", 130)])
        self._files_table.selectionRowsChanged.connect(self._on_files_selected)
        self._sel_label = QLabel("Aucun fichier.")
        files_card.body_layout.addWidget(self._files_table, 1)
        files_card.body_layout.addWidget(self._sel_label)
        split.addWidget(files_card)

        meta_card = Card("Métadonnées")
        self._meta_title = QLabel("Sélectionnez un fichier.")
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filtrer les tags (groupe, nom ou valeur)…")
        self._filter.setClearButtonEnabled(True)
        self._meta = DataTable([
            Column("group", "Groupe", 110), Column("name", "Tag", 190),
            Column("value", "Valeur", 260, stretch=True), Column("state", "État", 100)])
        self._filter.textChanged.connect(self._meta.set_filter_text)
        self._meta.rowActivated.connect(self._edit_tag)
        meta_btns = QHBoxLayout()
        self._edit_btn = QPushButton("Modifier la valeur…")
        self._del_btn = QPushButton("Supprimer le tag")
        self._edit_btn.clicked.connect(self._edit_selected)
        self._del_btn.clicked.connect(self._delete_selected_tags)
        meta_btns.addWidget(self._edit_btn)
        meta_btns.addWidget(self._del_btn)
        meta_btns.addStretch(1)
        for w in (self._meta_title, self._filter, self._meta):
            meta_card.body_layout.addWidget(w, 1 if w is self._meta else 0)
        meta_card.body_layout.addLayout(meta_btns)
        split.addWidget(meta_card)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)

        pend_card = Card("Modifications en attente (rien n'est écrit avant « Appliquer »)")
        self._pending_table = DataTable([
            Column("op", "Opération", 100), Column("tag", "Tag", 260),
            Column("value", "Valeur", 300, stretch=True)])
        self._pending_table.setMaximumHeight(150)
        pend_btns = QHBoxLayout()
        self._pend_remove = QPushButton("Retirer la sélection")
        self._pend_clear = QPushButton("Tout retirer")
        self._pend_remove.clicked.connect(self._remove_pending)
        self._pend_clear.clicked.connect(self._clear_pending)
        pend_btns.addWidget(self._pend_remove)
        pend_btns.addWidget(self._pend_clear)
        pend_btns.addStretch(1)
        pend_card.body_layout.addWidget(self._pending_table)
        pend_card.body_layout.addLayout(pend_btns)
        outer.addWidget(pend_card)
        self.set_content(root)

        side = QWidget()
        sl = QVBoxLayout(side)
        self._preview = ImagePreview(260)
        sl.addWidget(self._preview)
        presets = Card("Préréglages")
        self._preset_btns = []
        for text, cb in (("Supprimer le GPS", self._preset_delete_gps),
                         ("Supprimer toutes les métadonnées…", self._preset_delete_all),
                         ("Définir la date de prise de vue…", self._preset_set_date),
                         ("Définir la position GPS…", self._preset_set_gps)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            presets.body_layout.addWidget(b)
            self._preset_btns.append(b)
        sl.addWidget(presets)
        options = Card("Options")
        self._align = QCheckBox("Aligner la date de modification du fichier")
        self._align.setToolTip("Après écriture de la date de prise de vue, la date de modification du fichier est alignée dessus.")
        self._align.setChecked(False)
        options.body_layout.addWidget(self._align)
        sl.addWidget(options)
        sl.addStretch(1)
        self.set_side_panel(side, "Aperçu et préréglages")

        self._lockables = [self._drop, self._edit_btn, self._del_btn, self._pend_remove, self._pend_clear,
                           *self._preset_btns]

    def _init_binary(self) -> None:
        self.show_binary_status(("exiftool",))
        self._ensure_exiftool(quiet=False)

    def _ensure_exiftool(self, quiet: bool = True) -> Path | None:
        try:
            self._exiftool = binaries.require("exiftool")
            self.set_run_enabled(True)
        except BinaryNotFoundError as exc:
            self._exiftool = None
            self.set_run_enabled(False)
            if not quiet:
                self.log_panel.append_log("ERR", str(exc))
                self.log_panel.append_log(
                    "ERR", "ExifTool est introuvable : la lecture et « Appliquer » sont désactivés. "
                           "Indiquez son chemin dans les Préférences.")
        return self._exiftool

    # ── Ouverture des fichiers ───────────────────────────────────────────
    def open_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        if self.is_busy():
            self.log_panel.append_log("WARN", "Un traitement est en cours : réessayez dans un instant.")
            return
        exiftool = self._ensure_exiftool()
        if exiftool is None:
            QMessageBox.warning(self, "ExifTool introuvable",
                                "ExifTool est nécessaire pour lire les métadonnées. "
                                "Installez-le ou indiquez son chemin dans les Préférences.")
            return
        worker = ReadWorker(exiftool, [Path(p) for p in paths], self._recursive.isChecked())
        self.run_worker(worker, on_completed=self._on_files_read, lock_widgets=self._lockables)

    def on_open(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        files, _ = QFileDialog.getOpenFileNames(self, "Ajouter des fichiers", start)
        if files:
            Preferences.instance().set("last_dir", str(Path(files[0]).parent))
            self.open_paths(files)

    def _open_folder(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Ajouter un dossier", start)
        if folder:
            Preferences.instance().set("last_dir", folder)
            self.open_paths([folder])

    def _clear_files(self) -> None:
        if self.is_busy():
            return
        self._files, self._index, self._current_index = [], {}, None
        self._files_table.data_model.clear()
        self._render_meta()
        self._preview.clear()
        self._sel_label.setText("Aucun fichier.")

    @staticmethod
    def _file_row(ft: eng.FileTags) -> dict:
        refus = eng.write_refusal(ft.path)
        row = {"name": ft.path.name, "type": ft.path.suffix.upper().lstrip(".") or "—",
               "size": format_size(ft.size), "tags": str(len(ft.entries)),
               "state": ft.error or ("Non modifiable ici" if refus else "")}
        if ft.error:
            row["_tag"] = "err"
        elif refus:
            row["_tag"] = "neutre"
        return row

    def _on_files_read(self, results: object) -> None:
        added = 0
        for ft in results or []:
            k = eng.path_key(ft.path)
            if k in self._index:
                i = self._index[k]
                self._files[i] = ft
                self._files_table.data_model.update_row(i, **self._file_row(ft))
            else:
                self._index[k] = len(self._files)
                self._files.append(ft)
                self._files_table.data_model.append_row(self._file_row(ft))
                added += 1
        if added:
            self.log_panel.append_log("INFO", f"{added} fichier(s) ajouté(s) à la liste.")
        if self._current_index is None and self._files:
            self._files_table.selectRow(0)
        elif self._current_index is not None:
            self._render_meta()

    # ── Sélection et métadonnées ─────────────────────────────────────────
    def _on_files_selected(self, rows: list) -> None:
        rows = [r for r in rows if 0 <= r < len(self._files)]
        if not rows:
            self._sel_label.setText(f"{len(self._files)} fichier(s) dans la liste, aucun sélectionné.")
            return
        n = len(rows)
        self._sel_label.setText(f"{n} fichier(s) sélectionné(s)" + (" : édition par lot." if n > 1 else "."))
        if rows[0] != self._current_index:
            self._current_index = rows[0]
            self._preview.show_image(self._files[rows[0]].path)
            self._render_meta()

    def _render_meta(self) -> None:
        if self._current_index is None or self._current_index >= len(self._files):
            self._current_entries = []
            self._meta.data_model.set_rows([])
            self._meta_title.setText("Sélectionnez un fichier.")
            return
        ft = self._files[self._current_index]
        entries = sorted(ft.entries, key=lambda e: (e.group.lower(), e.name.lower()))
        self._current_entries = entries
        rows = []
        for e in entries:
            pending = any(eng.entry_matches(e, ch.tag) for ch in self._pending)
            row = {"group": e.group, "name": e.name, "value": e.value,
                   "state": "En attente" if pending else ("" if e.writable else "Lecture seule")}
            if pending:
                row["_tag"] = "warn"
            elif not e.writable:
                row["_tag"] = "neutre"
            rows.append(row)
        self._meta.data_model.set_rows(rows)
        self._meta_title.setText(f"{ft.path.name} — {len(entries)} tag(s)")

    def _current_refusal(self) -> str | None:
        if self._current_index is None:
            return "Aucun fichier sélectionné."
        return eng.write_refusal(self._files[self._current_index].path)

    # ── Édition d'un tag ─────────────────────────────────────────────────
    def _edit_selected(self) -> None:
        rows = self._meta.selected_rows()
        if rows:
            self._edit_tag(rows[0])

    def _edit_tag(self, index: int) -> None:
        if self.is_busy() or not (0 <= index < len(self._current_entries)):
            return
        entry = self._current_entries[index]
        reason = eng.entry_refusal(entry) or self._current_refusal()
        if reason:
            QMessageBox.warning(self, "Modification refusée", reason)
            self.log_panel.append_log("WARN", f"{entry.full_name} : {reason}")
            return
        text, ok = QInputDialog.getText(self, "Modifier la valeur", f"{entry.full_name} :",
                                        QLineEdit.EchoMode.Normal, entry.value)
        if not ok or text == entry.value:
            return
        if text.strip() == "":
            self._add_pending([eng.TagChange("delete", entry.full_name)])
        elif entry.items:                                # tag « liste » : un élément par valeur séparée par une virgule
            parts = [p.strip() for p in text.split(",") if p.strip()]
            self._add_pending([eng.TagChange("set", entry.full_name, p) for p in parts])
        else:
            self._add_pending([eng.TagChange("set", entry.full_name, text)])

    def _delete_selected_tags(self) -> None:
        if self.is_busy():
            return
        reason = self._current_refusal()
        if reason:
            QMessageBox.warning(self, "Suppression refusée", reason)
            return
        changes, refused = [], 0
        for i in self._meta.selected_rows():
            if not (0 <= i < len(self._current_entries)):
                continue
            e = self._current_entries[i]
            if eng.entry_refusal(e):
                refused += 1
            else:
                changes.append(eng.TagChange("delete", e.full_name))
        if refused:
            QMessageBox.warning(self, "Suppression refusée",
                                f"{refused} tag(s) en lecture seule ou binaires n'ont pas été retenus : "
                                "seuls les tags EXIF, IPTC et XMP sont modifiables.")
        if changes:
            self._add_pending(changes)

    # ── Modifications en attente ─────────────────────────────────────────
    def _add_pending(self, changes: list[eng.TagChange]) -> None:
        self._pending = eng.merge_pending(self._pending, changes)
        self._refresh_pending()

    def _refresh_pending(self) -> None:
        rows = [{"op": OP_LABELS.get(c.op, c.op), "tag": c.tag,
                 "value": (c.value[:200] if c.op == "set" else "—")} for c in self._pending]
        self._pending_table.data_model.set_rows(rows)
        self._render_meta()

    def _remove_pending(self) -> None:
        drop = set(self._pending_table.selected_rows())
        if drop:
            self._pending = [c for i, c in enumerate(self._pending) if i not in drop]
            self._refresh_pending()

    def _clear_pending(self) -> None:
        self._pending = []
        self._refresh_pending()

    # ── Préréglages ──────────────────────────────────────────────────────
    def _preset_delete_gps(self) -> None:
        self._add_pending(eng.preset_delete_gps())

    def _preset_delete_all(self) -> None:
        rep = QMessageBox.question(
            self, "Supprimer toutes les métadonnées",
            "Cette modification supprimera TOUTES les métadonnées modifiables (EXIF, IPTC, XMP) des fichiers "
            "sélectionnés au moment de « Appliquer ».\n\nElle reste annulable avec « Annuler la dernière "
            "application » (hors valeurs binaires).\n\nAjouter cette modification en attente ?",
            _Yes | _No, _No)
        if rep == _Yes:
            self._add_pending(eng.preset_delete_all())

    def _preset_set_date(self) -> None:
        text, ok = QInputDialog.getText(self, "Définir la date de prise de vue", "Date (AAAA-MM-JJ HH:MM:SS) :",
                                        QLineEdit.EchoMode.Normal, datetime.now().strftime(eng.INPUT_DATE_FORMAT))
        if not ok:
            return
        try:
            self._add_pending(eng.preset_set_date(text))
        except ValueError as exc:
            QMessageBox.warning(self, "Date invalide", str(exc))

    def _preset_set_gps(self) -> None:
        dlg = _GpsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.values:
            self._add_pending(eng.preset_set_gps(*dlg.values))

    # ── Application ──────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        exiftool = self._ensure_exiftool()
        if exiftool is None:
            QMessageBox.warning(self, "ExifTool introuvable", "Impossible d'appliquer sans ExifTool.")
            return
        if not self._pending:
            QMessageBox.information(self, "Rien à appliquer", "Aucune modification en attente.")
            return
        targets = [self._files[i].path for i in self._files_table.selected_rows() if 0 <= i < len(self._files)]
        if not targets:
            QMessageBox.information(self, "Aucun fichier", "Sélectionnez au moins un fichier dans la liste.")
            return
        problems = eng.validate_changes(self._pending)
        if problems:
            QMessageBox.warning(self, "Modifications refusées", "\n".join(problems))
            return
        refused = [p for p in targets if eng.write_refusal(p)]
        if len(refused) == len(targets):
            QMessageBox.warning(self, "Format non modifiable", eng.write_refusal(refused[0]) or "")
            return
        msg = f"{len(self._pending)} modification(s) vont être écrites dans {len(targets)} fichier(s)."
        if any(c.op == "delete" and c.tag.lower() == "all" for c in self._pending):
            msg += "\n\n⚠ TOUTES les métadonnées modifiables seront supprimées."
        if refused:
            msg += f"\n\n{len(refused)} fichier(s) de format non modifiable ici seront ignorés."
        msg += "\n\nL'état précédent est enregistré pour permettre l'annulation. Continuer ?"
        if QMessageBox.question(self, "Confirmer l'application", msg, _Yes | _No, _No) != _Yes:
            return
        worker = WriteWorker(exiftool, targets, list(self._pending), self._align.isChecked())
        self.run_worker(worker, on_completed=self._on_write_done, lock_widgets=self._lockables,
                        history_action="Modification des métadonnées")

    def _on_write_done(self, result: object) -> None:
        res = result if isinstance(result, dict) else {}
        written = res.get("written") or []
        if written:
            self._pending = []
            self._refresh_pending()
            self._reload(written)
        QTimer.singleShot(0, self._refresh_undo_button)

    def _reload(self, paths: list[Path]) -> None:
        exiftool = self._ensure_exiftool()
        if exiftool is None or not paths:
            return
        # Le worker précédent est terminé : on relit les fichiers touchés.
        QTimer.singleShot(0, lambda: self.run_worker(
            ReadWorker(exiftool, paths, False), on_completed=self._on_files_read, lock_widgets=self._lockables))

    # ── Annulation ───────────────────────────────────────────────────────
    def _refresh_undo_button(self) -> None:
        try:
            keys = get_storage().list_keys(eng.UNDO_COLLECTION)
        except Exception:
            keys = []
        self._undo_btn.setEnabled(bool(keys))

    def _undo_last(self) -> None:
        if self.is_busy():
            return
        exiftool = self._ensure_exiftool()
        if exiftool is None:
            QMessageBox.warning(self, "ExifTool introuvable", "Impossible d'annuler sans ExifTool.")
            return
        storage = get_storage()
        key = eng.latest_undo_key(storage.list_keys(eng.UNDO_COLLECTION))
        rec = storage.load(eng.UNDO_COLLECTION, key) if key else None
        if not key or not rec:
            QMessageBox.information(self, "Rien à annuler", "Aucune application à annuler.")
            self._refresh_undo_button()
            return
        n = len(rec.get("files", []))
        quand = str(rec.get("timestamp", "")).replace("T", " ")
        if QMessageBox.question(self, "Annuler la dernière application",
                                f"Rétablir l'état d'avant l'application du {quand} sur {n} fichier(s) ?",
                                _Yes | _No, _No) != _Yes:
            return
        self.run_worker(UndoWorker(exiftool, key), on_completed=self._on_undo_done,
                        lock_widgets=self._lockables, history_action="Annulation des métadonnées")

    def _on_undo_done(self, result: object) -> None:
        res = result if isinstance(result, dict) else {}
        restored = res.get("restored") or []
        if restored:
            self._reload(restored)
        QTimer.singleShot(0, self._refresh_undo_button)
