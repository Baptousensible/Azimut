"""Fenêtre d'Azimut Rename : propose, vérifie puis applique des noms contenant la date de prise de vue."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFrame,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import get_storage
from azimut.modules.rename import MODULE, engine as eng
from azimut.modules.rename.workers import ApplyWorker, ScanResult, ScanWorker, UndoWorker
from azimut.modules.tags import engine as tags
from azimut.ui.controls import CheckBox, fit_combo_to_panel, set_action_available
from azimut.ui.file_dialog import choose_directory, choose_files, choose_save_file, media_filter
from azimut.ui.flow_layout import FlowLayout
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, DropZone, MediaPreview, plain_message

_log = get_logger("rename.window")

COLUMNS = [
    Column("check", QCoreApplication.translate("RenameWindow", "Sél."), width=56, align="center"),
    Column("before_dir", QCoreApplication.translate("RenameWindow", "Dossier avant"), width=114),
    Column("before_name", QCoreApplication.translate("RenameWindow", "Nom avant"), width=250, stretch=True),
    Column("after_dir", QCoreApplication.translate("RenameWindow", "Dossier après"), width=114),
    Column("after_name", QCoreApplication.translate("RenameWindow", "Nom après"), width=250, stretch=True),
    Column("date", QCoreApplication.translate("RenameWindow", "Date retenue"), width=150),
    Column("status", QCoreApplication.translate("RenameWindow", "Statut"), width=150),
]
FILTERS = (QCoreApplication.translate("RenameWindow", "À renommer"), QCoreApplication.translate("RenameWindow", "Tout"), QCoreApplication.translate("RenameWindow", "Déjà conformes"), QCoreApplication.translate("RenameWindow", "Ignorés (décochés)"), QCoreApplication.translate("RenameWindow", "Sans date"), QCoreApplication.translate("RenameWindow", "Date de modification (à vérifier)"))
_Yes, _No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
CONFIRM_LIMIT = 3000
STATUS_TEXT = {
    eng.STATUS_RENAME: QCoreApplication.translate("RenameWindow", "À renommer"),
    eng.STATUS_OK: QCoreApplication.translate("RenameWindow", "Déjà conforme"),
    eng.STATUS_SKIPPED: QCoreApplication.translate("RenameWindow", "Ignoré (décoché)"),
    eng.STATUS_NO_DATE: QCoreApplication.translate("RenameWindow", "Sans date — ignoré"),
}
SOURCE_TEXT = {
    eng.SOURCE_META: QCoreApplication.translate("RenameWindow", "métadonnées"),
    eng.SOURCE_MTIME: QCoreApplication.translate("RenameWindow", "date de modification (à vérifier)"),
}


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
        self.set_run_label(self.tr("Démarrer le renommage"))
        self._build_ui()
        self._undo_btn = self.add_action_button(
            self.tr("Annuler le dernier renommage"), self._undo_last, kind="secondary",
            tooltip=self.tr("Rend leur ancien nom aux fichiers du dernier renommage"))
        self.add_action_button(self.tr("Exporter le plan (CSV)…"), self._export_plan, kind="secondary")
        self.show_binary_status(("exiftool", "ffmpeg"))
        self._ensure_exiftool(quiet=False)
        self._refresh_undo_button()
        self._refresh_run_state()
        self.resize(1250, 800)

    # ── Interface ───────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        self.drop = DropZone(self.tr("Glissez ici le dossier de photos, vidéos ou fichiers audio à renommer"),
                             accept_files=False)
        self.drop.pathsDropped.connect(self.open_paths)
        lay.addWidget(self.drop)

        # En flux : les trois boutons passent à la ligne plutôt que d'être rognés
        # quand le panneau latéral et le zoom réduisent la place disponible.
        bar = FlowLayout(h_spacing=8, v_spacing=6)
        bar.addWidget(QLabel(self.tr("Afficher :")))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(FILTERS)
        self.cmb_filter.currentIndexChanged.connect(lambda _i: self._render())
        bar.addWidget(self.cmb_filter)
        self.btn_all = QPushButton(self.tr("Cocher la sélection"))
        self.btn_none = QPushButton(self.tr("Décocher la sélection"))
        # Uniquement les lignes surlignées dans le tableau, jamais toutes les lignes visibles.
        self.btn_all.clicked.connect(lambda: self._set_checked(self.table.selected_rows(), True))
        self.btn_none.clicked.connect(lambda: self._set_checked(self.table.selected_rows(), False))
        self.btn_clear = QPushButton(self.tr("Vider la liste"))
        self.btn_clear.clicked.connect(self._clear_list)
        bar.addWidget(self.btn_all)
        bar.addWidget(self.btn_none)
        bar.addWidget(self.btn_clear)
        lay.addLayout(bar)

        self.table = DataTable(COLUMNS)
        self.table.set_empty_text(self.tr("Aucun fichier : ouvrez des photos, vidéos ou fichiers audio."))
        self.table.rowActivated.connect(lambda r: self._set_checked([r], None))
        self.table.selectionRowsChanged.connect(lambda _rows: self._on_selection())
        lay.addWidget(self.table, 1)
        self._refresh_check_buttons()
        self.lbl_summary = QLabel(self.tr("Aucun dossier analysé."))
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)
        lay.addWidget(self.action_bar)
        self.set_content(central)

        side = QWidget()
        sl = QVBoxLayout(side)
        p = self.prefs

        card = Card(self.tr("Nom proposé"))
        card.body_layout.addWidget(QLabel(self.tr("Format de la date")))
        self.cmb_format = QComboBox()
        date_labels = {
            "full": self.tr("AAAA-MM-JJ_HH-MM-SS   (2008-06-15_14-30-00)"),
            "compact": self.tr("AAAAMMJJ_HHMMSS   (20080615_143000)"),
            "day": self.tr("AAAA-MM-JJ   (jour seul, 2008-06-15)"),
        }
        self._date_examples: dict[str, str] = {}
        for fid in eng.DATE_FORMATS:
            full = date_labels.get(fid, fid)
            pattern, separator, example = full.partition("   (")
            if separator:
                example = example[:-1] if example.endswith(")") else example
            else:
                pattern, example = full, ""
            index = self.cmb_format.count()
            self.cmb_format.addItem(pattern, fid)
            self.cmb_format.setItemData(index, full, Qt.ItemDataRole.ToolTipRole)
            self._date_examples[fid] = example
        self.cmb_format.setCurrentIndex(max(0, self.cmb_format.findData(p.get("format", eng.DEFAULT_FORMAT))))
        # Les libellés de format sont longs : la liste ne doit pas élargir le panneau.
        fit_combo_to_panel(self.cmb_format)
        self.cmb_format.setToolTip(self.cmb_format.currentText())
        self.cmb_format.setAccessibleDescription(self.cmb_format.currentText())
        self.cmb_format.currentIndexChanged.connect(lambda _i: self._on_option("format", self.cmb_format.currentData()))
        self.cmb_format.currentIndexChanged.connect(self._sync_format_tooltip)
        self.date_example = QLabel()
        self.date_example.setProperty("role", "muted")
        self.date_example.setWordWrap(True)
        card.body_layout.addWidget(self.date_example)
        self._sync_format_tooltip()
        card.body_layout.addWidget(self.cmb_format)
        self.chk_prefix = CheckBox(self.tr("Conserver le préfixe personnalisé"))
        self.chk_type = CheckBox(self.tr("Ajouter IMG / VID / AUD au début"))
        self.chk_mtime = CheckBox(self.tr("Sans métadonnées : utiliser la date de modification"))
        self.chk_recursive = CheckBox(self.tr("Inclure les sous-dossiers"))
        for chk, key, default in ((self.chk_prefix, "keep_prefix", True), (self.chk_type, "add_type", True),
                                  (self.chk_mtime, "use_mtime", False), (self.chk_recursive, "recursive", True)):
            chk.setChecked(bool(p.get(key, default)))
            chk.toggled.connect(lambda v, k=key: self._on_option(k, bool(v)))
            card.body_layout.addWidget(chk)
        sl.addWidget(card)

        card_p = Card(self.tr("Préfixe personnalisé"))
        self.edit_prefix = QLineEdit()
        self.edit_prefix.setPlaceholderText(self.tr("(aucun) — ex. Vacances Corse"))
        self.edit_prefix.returnPressed.connect(self._apply_prefix)
        self.btn_set_prefix = QPushButton(self.tr("Appliquer à la sélection"))
        self.btn_set_prefix.clicked.connect(self._apply_prefix)
        self.btn_auto_prefix = QPushButton(self.tr("Rétablir le préfixe automatique"))
        self.btn_auto_prefix.clicked.connect(self._reset_prefix)
        for w in (self.edit_prefix, self.btn_set_prefix, self.btn_auto_prefix):
            card_p.body_layout.addWidget(w)
        sl.addWidget(card_p)

        card_s = Card(self.tr("Sélection"))
        # Plus de boutons « Cocher / Décocher la sélection » ici : la barre au-dessus du tableau
        # porte déjà ces actions. La zone ne garde que l'aperçu (lecteur pour l'audio et la vidéo)
        # et les informations du fichier sélectionné.
        self.preview = MediaPreview(size=190, volume_controls=True)
        self.preview.open_btn.failed.connect(lambda msg: self.log_panel.append_log("WARN", msg))
        card_s.body_layout.addWidget(self.preview)
        self.lbl_info = QLabel()
        self.lbl_info.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_info.setWordWrap(True)
        card_s.body_layout.addWidget(self.lbl_info)
        hint = QLabel(self.tr("Entrée ou double-clic : cocher / décocher la ligne. Le contenu, les métadonnées et la date "
                      "de modification ne sont jamais touchés."))
        hint.setWordWrap(True)
        card_s.body_layout.addWidget(hint)
        sl.addWidget(card_s)
        sl.addStretch(1)
        scroll = QScrollArea()                                  # le panneau défile si la fenêtre est basse
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(side)
        self.set_side_panel(scroll, self.tr("Réglages"))
        self._lockables = [self.drop, self.cmb_format, self.chk_prefix, self.chk_type, self.chk_mtime,
                           self.chk_recursive, self.cmb_filter, self.edit_prefix,
                           self.btn_set_prefix, self.btn_auto_prefix, self.btn_clear]
        self.busyChanged.connect(lambda _busy: self._refresh_check_buttons())
        self._refresh_check_buttons()

    def _options(self) -> eng.Options:
        return eng.Options(fmt=str(self.cmb_format.currentData()), keep_prefix=self.chk_prefix.isChecked(),
                           add_type=self.chk_type.isChecked(), use_mtime=self.chk_mtime.isChecked())

    def _sync_format_tooltip(self, _index: int = 0) -> None:
        """Sépare le format de son exemple : la liste fermée reste lisible à 80 %."""
        fid = self.cmb_format.currentData()
        example = self._date_examples.get(fid, "")
        full = (self.cmb_format.itemData(self.cmb_format.currentIndex(), Qt.ItemDataRole.ToolTipRole)
                or self.cmb_format.currentText())
        self.cmb_format.setToolTip(str(full))
        self.cmb_format.setAccessibleDescription(str(full))
        self.date_example.setText(str(example))
        self.date_example.setVisible(bool(example))

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
        prefs = Preferences.instance()
        start = str(prefs.get("last_dir", "") or "")
        patterns = " ".join(f"*{ext}" for ext in sorted(eng.SUPPORTED_EXTS))
        files = choose_files(self, self.tr("Importer des fichiers"), start,
                             media_filter(patterns))
        if files:
            prefs.set("last_dir", str(Path(files[0]).parent))
            self.open_paths(files)

    def on_open_folder(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = choose_directory(self, self.tr("Importer un dossier"), start)
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
        self._ensure_exiftool(quiet=False)
        self._rows, self._entries, self._visible = [], {}, []
        self._render()
        self.log_panel.append_log("INFO", f"Analyse — {folder}")
        self.run_worker(ScanWorker(folder, self.chk_recursive.isChecked(), self._exiftool),
                        on_completed=self._on_scanned, lock_widgets=self._lockables,
                        import_progress=self.drop.import_progress)

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
            except ValueError as exc:
                _log.debug("Chemin hors du dossier de base (%s) : %s", path, exc)
        return str(path)

    def _location_name(self, path: Path) -> tuple[str, str]:
        """Dossier et nom affichés (chemins relatifs à la racine analysée quand c'est possible)."""
        shown = Path(self._display(path))
        location = shown.parent.as_posix()
        return ("." if location in ("", ".") else location), shown.name

    def _render(self) -> None:
        mode = self.cmb_filter.currentIndex()
        self._visible = [i for i, r in enumerate(self._rows) if _passes(r, mode)]
        rows = []
        for i in self._visible:
            r = self._rows[i]
            selectable = r.status not in (eng.STATUS_NO_DATE, eng.STATUS_OK)
            box = "" if selectable else "—"                 # case cochée = icône « check » (voir « _icon_check »)
            before_dir, before_name = self._location_name(r.path)
            after_dir, after_name = self._location_name(r.new_path if r.status == eng.STATUS_RENAME else r.path)
            tag = "warn" if r.status == eng.STATUS_RENAME and r.source == eng.SOURCE_MTIME \
                else eng.TAG_BY_STATUS.get(r.status, "")
            rows.append({"check": box, "_icon_check": "check" if selectable and r.checked else None,
                         "before_dir": before_dir, "before_name": before_name,
                         "after_dir": after_dir, "after_name": after_name,
                         "date": r.date.strftime("%Y-%m-%d %H:%M:%S") if r.date else "—",
                         "status": STATUS_TEXT.get(r.status, r.status),
                         "_tag": tag})
        self.table.data_model.set_rows(rows)
        self._update_summary()
        self._on_selection()
        self._refresh_run_state()

    def _update_summary(self) -> None:
        if not self._rows:
            self.lbl_summary.setText(self.tr("Aucun dossier analysé."))
            return
        count = lambda s: sum(1 for r in self._rows if r.status == s)       # noqa: E731
        self.lbl_summary.setText(self.tr(
            "{shown} affiché(s) / {total} fichiers · à renommer {rename} · conformes {ok} · "
            "décochés {skipped} · sans date {nodate}").format(
                shown=len(self._visible), total=len(self._rows), rename=count(eng.STATUS_RENAME),
                ok=count(eng.STATUS_OK), skipped=count(eng.STATUS_SKIPPED), nodate=count(eng.STATUS_NO_DATE)))

    def _selected(self) -> list[eng.RenameRow]:
        return [self._rows[self._visible[i]] for i in self.table.selected_rows() if 0 <= i < len(self._visible)]

    def _on_selection(self) -> None:
        sel = self._selected()
        self._refresh_check_buttons()
        if len(sel) != 1:
            self.preview.clear()
            self.lbl_info.setText(self.tr("{count} fichiers sélectionnés").format(count=len(sel)) if sel else "")
            return
        r = sel[0]
        self.preview.set_path(r.path)
        after = r.new_name if r.status == eng.STATUS_RENAME else self.tr("(inchangé)")
        self.lbl_info.setText(self.tr("Avant : {before}\nAprès : {after}\nDate : {date}\n"
                                      "Source : {source}\nStatut : {status}").format(
            before=r.old_name, after=after,
            date=r.date.strftime("%Y-%m-%d %H:%M:%S") if r.date else "—",
            source=SOURCE_TEXT.get(r.source, r.source or "—"),
            status=STATUS_TEXT.get(r.status, r.status)))
        if not self.edit_prefix.hasFocus():
            self.edit_prefix.setText(r.prefix)

    def _clear_list(self) -> None:
        """Vide le tableau et l'analyse en cours ; aucun fichier n'est touché sur le disque."""
        if self.is_busy() or not self._rows:
            return
        if not self.confirm_clear_list():
            return
        self._rows, self._entries, self._visible = [], {}, []
        self._base_dir = self._folder = None
        self._applied = {}
        self._render()

    # ── Cases à cocher et préfixe ───────────────────────────────────────────
    def _refresh_check_buttons(self) -> None:
        """Les actions de coche suivent la sélection ; « Vider » suit le contenu du tableau."""
        busy = self.is_busy()
        enabled = bool(self.table.selected_rows()) and not busy
        set_action_available(self.btn_all, enabled)
        set_action_available(self.btn_none, enabled)
        set_action_available(self.btn_clear, bool(self._rows) and not busy)

    def _refresh_run_state(self) -> None:
        """« Démarrer le renommage » n'est actif que s'il reste des fichiers à renommer."""
        self.set_run_enabled(any(row.status == eng.STATUS_RENAME for row in self._rows))

    def _set_checked(self, view_rows: list[int], value: bool | None) -> None:
        """value=None : inverse chaque ligne. Seules les lignes datées et non conformes sont concernées.

        view_rows : indices du modèle source, c'est-à-dire les lignes surlignées dans le tableau.
        """
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
        if not sel:
            return
        path = sel[0].path
        # Seuls les médias sont ouverts ; tout le reste est simplement montré dans son dossier (AZ-006).
        target = path if path.suffix.lower() in eng.SUPPORTED_EXTS else path.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.log_panel.append_log("WARN", f"Impossible d'ouvrir : {target}")

    # ── Application ─────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        todo = [r for r in self._rows if r.status == eng.STATUS_RENAME]
        if not todo:
            QMessageBox.information(self, self.tr("Rien à renommer"), self.tr("Aucun fichier coché à renommer."))
            return
        if not self._confirm(todo):
            return
        pairs = [(i, r.path, r.new_path) for i, r in enumerate(todo)]
        self._applied = dict(enumerate(todo))
        worker = ApplyWorker(pairs)
        self.run_worker(worker, on_completed=self._on_applied,
                        on_cancelled=lambda: self._on_applied(worker.result),
                        lock_widgets=self._lockables, history_action="Renommage par date")

    def _confirm(self, todo: list[eng.RenameRow]) -> bool:
        """Liste complète avant → après, avant toute action."""
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Confirmer le renommage"))
        dlg.resize(1000, 640)
        lay = QVBoxLayout(dlg)
        n_mtime = sum(1 for r in todo if r.source == eng.SOURCE_MTIME)
        n_pref = sum(1 for r in todo if r.prefix and self.chk_prefix.isChecked())
        text = (f"{len(todo)} fichier(s) vont être renommés ({n_pref} avec préfixe conservé).\n"
                "Aucun contenu ni métadonnée n'est modifié ; l'annulation du dernier renommage reste possible.")
        if n_mtime:
            text += f"\nAttention : {n_mtime} nom(s) basé(s) sur la date de modification (pas sur les métadonnées)."
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
    def undo_label(self) -> str | None:
        """Le menu Édition suit l'état réel du bouton d'annulation."""
        button = getattr(self, "_undo_btn", None)
        return self.tr("Annuler le dernier renommage") if button is not None and button.isEnabled() else None

    def undo_last(self) -> None:
        self._undo_last()

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
            QMessageBox.information(self, self.tr("Rien à annuler"), self.tr("Aucun renommage à annuler."))
            self._refresh_undo_button()
            return
        when = str(rec.get("timestamp", "")).replace("T", " ")
        if QMessageBox.question(
                self, self.tr("Annuler le dernier renommage"),
                self.tr("Rendre leur ancien nom aux {count} fichier(s) renommés le {date} ?")
                .format(count=len(rec.get('pairs', [])), date=when), _Yes | _No, _No) != _Yes:
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
            QMessageBox.information(self, self.tr("Rien à exporter"), self.tr("Aucun résultat d'analyse."))
            return
        default = f"plan_renommage_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
        path = choose_save_file(self, self.tr("Exporter le plan"), default, self.tr("CSV (*.csv)"))
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(eng.plan_csv_lines(self._rows))
        except OSError as exc:
            plain_message(self, QMessageBox.Icon.Critical, self.tr("Erreur"), str(exc))
            return
        self.log_panel.append_log("OK", f"Plan exporté : {path}")
