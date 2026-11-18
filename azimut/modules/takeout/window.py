"""Fenêtre d'Azimut Takeout : analyse d'un export Google Photos, puis écriture des dates et positions."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import get_storage
from azimut.modules.tags import engine as tags
from azimut.modules.takeout import MODULE, engine as eng
from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker, TakeoutUndoWorker
from azimut.ui.controls import CheckBox, set_action_available
from azimut.ui.file_dialog import choose_directory, choose_files, media_filter
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, DropZone

COLUMNS = [
    Column("file", QCoreApplication.translate("TakeoutWindow", "Fichier"), width=320, stretch=True),
    Column("json", QCoreApplication.translate("TakeoutWindow", "JSON"), width=190),
    Column("date", QCoreApplication.translate("TakeoutWindow", "Date de prise de vue"), width=150),
    Column("gps", QCoreApplication.translate("TakeoutWindow", "Position"), width=150),
    Column("state", QCoreApplication.translate("TakeoutWindow", "État"), width=160),
]

STATE_TEXT = {
    eng.STATE_READY: QCoreApplication.translate("TakeoutWindow", "Prêt"),
    eng.STATE_NO_JSON: QCoreApplication.translate("TakeoutWindow", "Sans JSON"),
    eng.STATE_NO_DATA: QCoreApplication.translate("TakeoutWindow", "JSON sans date ni GPS"),
    eng.STATE_DONE: QCoreApplication.translate("TakeoutWindow", "Mis à jour"),
    eng.STATE_UNCHANGED: QCoreApplication.translate("TakeoutWindow", "Déjà renseigné"),
    eng.STATE_MTIME_ONLY: QCoreApplication.translate("TakeoutWindow", "Date du fichier seule"),
    eng.STATE_ERROR: QCoreApplication.translate("TakeoutWindow", "Erreur"),
}
_Yes, _No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
FILTER_REMOVED = 2          # « Tout » = 0, « Fichiers sans JSON » = 1


class TakeoutWindow(ModuleWindow):
    """Réinjecte les dates et positions des JSON Google Photos dans les médias."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._roots: list[Path] = []
        self._items: list[eng.TakeoutItem] = []
        self._removed: list[eng.TakeoutItem] = []
        self._visible: list[int] = []
        self._orphans = 0
        self._exiftool: Path | None = None
        self._build_ui()
        self._undo_btn = self.add_action_button(
            self.tr("Annuler la dernière application"), self._undo_last, kind="secondary",
            tooltip=self.tr("Rétablit les valeurs d'avant la dernière application de Takeout"))
        self.show_binary_status(("exiftool",))
        self._ensure_exiftool(quiet=False)
        self._refresh_undo_button()

    # ── Interface ───────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        grid = QGridLayout(central)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.drop = DropZone(self.tr("Glissez ici le dossier de l'export Google Photos\n(les médias et leurs fichiers .json)"),
                             accept_files=False)
        self.drop.pathsDropped.connect(self.open_paths)
        grid.addWidget(self.drop, 0, 0, 1, 2)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(self.tr("Afficher :")))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItem(self.tr("Tout"))
        self.cmb_filter.addItem(self.tr("Fichiers sans JSON"))
        self.cmb_filter.addItem(self.tr("Fichiers retirés"))
        self.cmb_filter.currentIndexChanged.connect(lambda _i: self._render())
        bar.addWidget(self.cmb_filter)
        self.btn_remove = QPushButton(self.tr("Retirer la sélection"))
        self.btn_remove.setToolTip(self.tr("Retire les lignes sélectionnées de la liste, sans modifier les fichiers."))
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self._remove_selected)
        bar.addWidget(self.btn_remove)
        self.btn_clear = QPushButton(self.tr("Vider la liste"))
        self.btn_clear.setToolTip(self.tr("Vide la liste et les dossiers analysés, sans modifier les fichiers."))
        self.btn_clear.clicked.connect(self._on_clear)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        grid.addLayout(bar, 1, 0)

        self.table = DataTable(COLUMNS)
        self.table.set_empty_text(self.tr("Aucun fichier : déposez un export Google Photos."))
        self.table.selectionRowsChanged.connect(lambda _rows: self._refresh_remove_button())
        grid.addWidget(self.table, 2, 0)
        grid.addWidget(self.action_bar, 3, 0)
        self.lbl_summary = QLabel(self.tr("Aucun dossier analysé."))
        self.lbl_summary.setWordWrap(True)
        grid.addWidget(self.lbl_summary, 4, 0)
        self._refresh_remove_button()

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        sl.addWidget(self._side_title_bar(self.tr("Réglages")))
        card = Card(self.tr("Écrire dans les fichiers"))
        p = self.prefs
        self.chk_dates = CheckBox(self.tr("Date de prise de vue"))
        self.chk_gps = CheckBox(self.tr("Position GPS"))
        self.chk_overwrite = CheckBox(self.tr("Écraser les valeurs déjà présentes"))
        self.chk_overwrite.setToolTip(self.tr("Décoché : un fichier qui a déjà une date (ou une position) la garde."))
        self.chk_mtime = CheckBox(self.tr("Aligner la date de modification du fichier"))
        self.chk_backup = CheckBox(self.tr("Conserver une copie binaire de l'original"))
        self.chk_backup.setToolTip(self.tr("ExifTool conserve le fichier tel quel sous « nom.ext_original » avant "
                                   "modification. Cette copie n'est pas supprimée par l'annulation."))
        self.chk_recursive = CheckBox(self.tr("Inclure les sous-dossiers"))
        for chk, key, default in ((self.chk_dates, "write_dates", True), (self.chk_gps, "write_gps", True),
                                  (self.chk_overwrite, "overwrite", False), (self.chk_mtime, "align_mtime", True),
                                  (self.chk_backup, "backup", False), (self.chk_recursive, "recursive", True)):
            chk.setChecked(bool(p.get(key, default)))
            chk.toggled.connect(lambda v, k=key: self._on_option_changed(k, bool(v)))
            card.body_layout.addWidget(chk)
        card.body_layout.addWidget(QLabel(self.tr("Fuseau des dates des images")))
        self.cmb_tz = QComboBox()
        self.cmb_tz.addItem(self.tr("UTC (recommandé, identique à Google)"), eng.TZ_UTC)
        self.cmb_tz.addItem(self.tr("Heure locale de cet ordinateur"), eng.TZ_LOCAL)
        self.cmb_tz.setToolTip(self.tr("Google enregistre l'heure en UTC. Le fuseau est aussi écrit dans le fichier "
                               "(OffsetTime), donc la date reste exacte pour les logiciels qui le lisent. "
                               "Les vidéos QuickTime sont toujours écrites en UTC."))
        idx = self.cmb_tz.findData(p.get("tz", eng.TZ_UTC))
        self.cmb_tz.setCurrentIndex(max(0, idx))
        self.cmb_tz.currentIndexChanged.connect(lambda _i: self._on_option_changed("tz", self.cmb_tz.currentData()))
        card.body_layout.addWidget(self.cmb_tz)
        sl.addWidget(card)
        sl.addStretch(1)
        grid.addWidget(side, 1, 1, 4, 1)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(2, 1)
        self.set_content(central)
        self._lockables = [self.drop, self.chk_dates, self.chk_gps, self.chk_overwrite, self.chk_mtime,
                           self.chk_backup, self.chk_recursive, self.cmb_tz, self.cmb_filter, self.btn_clear]
        self.busyChanged.connect(lambda _busy: self._refresh_remove_button())

    def _options(self) -> eng.Options:
        return eng.Options(write_dates=self.chk_dates.isChecked(), write_gps=self.chk_gps.isChecked(),
                           overwrite=self.chk_overwrite.isChecked(), align_mtime=self.chk_mtime.isChecked(),
                           backup=self.chk_backup.isChecked(), tz=str(self.cmb_tz.currentData()))

    def _on_option_changed(self, key: str, value: object) -> None:
        self.prefs.set(key, value)
        if key != "recursive":
            self._render()

    # ── Binaire ─────────────────────────────────────────────────────────────
    def _ensure_exiftool(self, quiet: bool = True) -> Path | None:
        try:
            self._exiftool = binaries.require("exiftool")
        except BinaryNotFoundError as exc:
            self._exiftool = None
            if not quiet:
                self.log_panel.append_log("ERR", str(exc))
                self.log_panel.append_log("ERR", "ExifTool est introuvable : « Démarrer » est désactivé. "
                                                 "Indiquez son chemin dans les Préférences.")
        self._refresh_run_state()
        return self._exiftool

    # ── Ajout de dossiers et analyse ────────────────────────────────────────
    def on_open(self) -> None:
        prefs = Preferences.instance()
        start = str(prefs.get("last_dir", "") or "")
        patterns = " ".join(f"*{ext}" for ext in sorted(eng.MEDIA_EXTS))
        files = choose_files(self, self.tr("Importer des fichiers"), start,
                             media_filter(patterns))
        if files:
            prefs.set("last_dir", str(Path(files[0]).parent))
            self.open_paths(files)

    def on_open_folder(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = choose_directory(self, self.tr("Importer le dossier de l'export Google Photos"), start)
        if folder:
            Preferences.instance().set("last_dir", folder)
            self.open_paths([folder])

    def open_paths(self, paths: list[str]) -> None:
        dirs = [Path(p) if Path(p).is_dir() else Path(p).parent for p in paths if p]
        if not dirs:
            return
        if self.is_busy():
            self.log_panel.append_log("WARN", "Un traitement est en cours : réessayez dans un instant.")
            return
        for d in dirs:
            if d not in self._roots:
                self._roots.append(d)
        worker = AnalyzeWorker(self._roots, self.chk_recursive.isChecked())
        self.run_worker(worker, on_completed=self._on_analyzed, lock_widgets=self._lockables,
                        import_progress=self.drop.import_progress)

    def _on_analyzed(self, result: object) -> None:
        if isinstance(result, eng.Analysis):
            self._items, self._orphans = result.items, result.orphan_json
            self._removed = []
        self._render()

    def _on_clear(self) -> None:
        if self.is_busy() or not (self._items or self._removed):
            return
        if not self.confirm_clear_list():
            return
        self._roots, self._items, self._removed, self._orphans = [], [], [], 0
        self._render()

    def _render(self) -> None:
        self._refresh_run_state()
        opts = self._options()
        mode = self.cmb_filter.currentIndex()
        source = self._removed if mode == FILTER_REMOVED else self._items
        self._visible = [i for i, it in enumerate(source)
                         if mode in (0, FILTER_REMOVED) or it.state == eng.STATE_NO_JSON]
        rows = []
        for i in self._visible:
            it = source[i]
            date, gps = eng.describe(it.sidecar, opts)
            rows.append({"file": it.path.name, "json": it.sidecar.path.name if it.sidecar else "",
                         "date": date, "gps": gps, "state": STATE_TEXT.get(it.state, it.state),
                         "_tag": eng.state_tag(it.state)})
        self.table.data_model.set_rows(rows)
        if mode == FILTER_REMOVED:
            self.table.set_empty_text(self.tr("Aucun fichier retiré."))
        elif mode:
            self.table.set_empty_text(self.tr("Aucun fichier sans JSON."))
        else:
            self.table.set_empty_text(self.tr("Aucun fichier : déposez un export Google Photos."))
        self._refresh_remove_button()
        if not self._items:
            if mode == FILTER_REMOVED and self._removed:
                self.lbl_summary.setText(self.tr("{n} fichier(s) retiré(s) de la liste.").format(n=len(self._removed)))
            else:
                self.lbl_summary.setText(self.tr("Aucun dossier analysé."))
            return
        count = lambda s: sum(1 for i in self._items if i.state == s)       # noqa: E731
        self.lbl_summary.setText(self.tr(
            "{total} média(s) · {ready} prêt(s) · {nojson} sans JSON · "
            "{nodata} sans date ni position · {orphans} JSON sans média.").format(
                total=len(self._items), ready=count(eng.STATE_READY), nojson=count(eng.STATE_NO_JSON),
                nodata=count(eng.STATE_NO_DATA), orphans=self._orphans))

    def _remove_selected(self) -> None:
        """Retire de la liste les lignes sélectionnées ; les fichiers ne sont jamais touchés."""
        if self.is_busy() or self.cmb_filter.currentIndex() == FILTER_REMOVED:
            return
        selected = self.table.selected_rows()
        if not selected:
            return
        indices = {self._visible[i] for i in selected if 0 <= i < len(self._visible)}
        if not indices:
            return
        kept: list[eng.TakeoutItem] = []
        dropped: list[eng.TakeoutItem] = []
        for i, it in enumerate(self._items):
            (dropped if i in indices else kept).append(it)
        self._items = kept
        self._removed.extend(dropped)
        self._render()

    def _refresh_remove_button(self) -> None:
        """Le retrait suit la sélection ; « Vider » n'est actif que si la liste contient des lignes."""
        if getattr(self, "btn_remove", None) is None:
            return
        busy = self.is_busy()
        set_action_available(self.btn_remove,
                             self.cmb_filter.currentIndex() != FILTER_REMOVED
                             and bool(self.table.selected_rows()) and not busy)
        set_action_available(self.btn_clear, bool(self._items or self._removed) and not busy)

    def _refresh_run_state(self) -> None:
        """« Démarrer » exige ExifTool, une option d'écriture et au moins un média prêt."""
        if not hasattr(self, "chk_dates"):
            return
        opts = self._options()
        writes = opts.write_dates or opts.write_gps or opts.align_mtime
        ready = any(item.state == eng.STATE_READY for item in self._items)
        self.set_run_enabled(self._exiftool is not None and writes and ready)

    # ── Application ─────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        exiftool = self._ensure_exiftool(quiet=False)
        if exiftool is None:
            QMessageBox.warning(self, self.tr("ExifTool introuvable"), self.tr("Impossible d'appliquer sans ExifTool."))
            return
        opts = self._options()
        if not (opts.write_dates or opts.write_gps or opts.align_mtime):
            QMessageBox.information(self, self.tr("Rien à écrire"), self.tr("Cochez au moins une option d'écriture."))
            return
        ready = [i for i in self._items if i.state == eng.STATE_READY]
        if not ready:
            QMessageBox.information(self, self.tr("Rien à appliquer"), self.tr("Aucun média prêt : ajoutez un dossier d'export."))
            return
        copie = ("\nUne copie binaire de chaque original sera conservée à côté du fichier."
                 if opts.backup else "")
        msg = (f"Les dates et positions de {len(ready)} fichier(s) vont être écrites dans les fichiers eux-mêmes.\n\n"
               f"L'état précédent est enregistré pour permettre l'annulation.{copie}\n\nContinuer ?")
        if QMessageBox.question(self, self.tr("Confirmer l'application"), msg, _Yes | _No, _No) != _Yes:
            return
        worker = ApplyWorker(exiftool, self._items, opts)
        self.run_worker(worker, on_completed=self._on_applied,
                        on_cancelled=lambda: self._on_applied(worker.result),
                        lock_widgets=self._lockables, history_action="Réinjection Takeout")

    def _on_applied(self, result: object) -> None:
        res = result if isinstance(result, dict) else {}
        for idx, (state, message) in (res.get("states") or {}).items():
            if 0 <= idx < len(self._items):
                self._items[idx].state, self._items[idx].message = state, message
        self._render()
        QTimer.singleShot(0, self._refresh_undo_button)

    # ── Annulation ──────────────────────────────────────────────────────────
    def _refresh_undo_button(self) -> None:
        try:
            keys = get_storage().list_keys(eng.UNDO_COLLECTION)
        except Exception:
            keys = []
        self._undo_btn.setEnabled(bool(keys))

    def _undo_last(self) -> None:
        if self.is_busy():
            return
        exiftool = self._ensure_exiftool(quiet=False)
        if exiftool is None:
            QMessageBox.warning(self, self.tr("ExifTool introuvable"), self.tr("Impossible d'annuler sans ExifTool."))
            return
        storage = get_storage()
        key = tags.latest_undo_key(storage.list_keys(eng.UNDO_COLLECTION))
        rec = storage.load(eng.UNDO_COLLECTION, key) if key else None
        if not key or not rec:
            QMessageBox.information(self, self.tr("Rien à annuler"), self.tr("Aucune application à annuler."))
            self._refresh_undo_button()
            return
        quand = str(rec.get("timestamp", "")).replace("T", " ")
        if QMessageBox.question(
                self, self.tr("Annuler la dernière application"),
                self.tr("Rétablir l'état d'avant l'application du {date} sur {count} fichier(s) ?")
                .format(date=quand, count=len(rec.get('files', []))), _Yes | _No, _No) != _Yes:
            return
        self.run_worker(TakeoutUndoWorker(exiftool, key), on_completed=self._on_undone,
                        lock_widgets=self._lockables, history_action="Annulation Takeout")

    def _on_undone(self, _result: object) -> None:
        QTimer.singleShot(0, self._refresh_undo_button)
        if self._roots:
            QTimer.singleShot(0, lambda: self.open_paths([str(r) for r in self._roots]))
