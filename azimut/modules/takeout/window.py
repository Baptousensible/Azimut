"""Fenêtre d'Azimut Takeout : analyse d'un export Google Photos, puis écriture des dates et positions."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import get_storage
from azimut.modules.tags import engine as tags
from azimut.modules.takeout import MODULE, engine as eng
from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker, TakeoutUndoWorker
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, DropZone

COLUMNS = [
    Column("file", "Fichier", width=320, stretch=True),
    Column("json", "JSON", width=190),
    Column("date", "Date de prise de vue", width=150),
    Column("gps", "Position", width=150),
    Column("state", "État", width=160),
]
_Yes, _No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


class TakeoutWindow(ModuleWindow):
    """Réinjecte les dates et positions des JSON Google Photos dans les médias."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._roots: list[Path] = []
        self._items: list[eng.TakeoutItem] = []
        self._orphans = 0
        self._exiftool: Path | None = None
        self._build_ui()
        self.set_run_label("Appliquer")
        self._undo_btn = self.add_toolbar_button(
            "Annuler la dernière application", self._undo_last, kind="secondary",
            tooltip="Rétablit les valeurs d'avant la dernière application de Takeout")
        self.add_toolbar_button("Ouvrir un dossier…", self.on_open, kind="secondary")
        self.show_binary_status(("exiftool",))
        self._ensure_exiftool(quiet=False)
        self._refresh_undo_button()

    # ── Interface ───────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        self.drop = DropZone("Glissez ici le dossier de l'export Google Photos\n(les médias et leurs fichiers .json)",
                             accept_files=False)
        self.drop.pathsDropped.connect(self.open_paths)
        lay.addWidget(self.drop)
        self.table = DataTable(COLUMNS)
        lay.addWidget(self.table, 1)
        self.lbl_summary = QLabel("Aucun dossier analysé.")
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)
        self.set_content(central)

        side = QWidget()
        sl = QVBoxLayout(side)
        card = Card("Écrire dans les fichiers")
        p = self.prefs
        self.chk_dates = QCheckBox("Date de prise de vue")
        self.chk_gps = QCheckBox("Position GPS")
        self.chk_overwrite = QCheckBox("Écraser les valeurs déjà présentes")
        self.chk_overwrite.setToolTip("Décoché : un fichier qui a déjà une date (ou une position) la garde.")
        self.chk_mtime = QCheckBox("Aligner la date de modification du fichier")
        self.chk_recursive = QCheckBox("Inclure les sous-dossiers")
        for chk, key, default in ((self.chk_dates, "write_dates", True), (self.chk_gps, "write_gps", True),
                                  (self.chk_overwrite, "overwrite", False), (self.chk_mtime, "align_mtime", True),
                                  (self.chk_recursive, "recursive", True)):
            chk.setChecked(bool(p.get(key, default)))
            chk.toggled.connect(lambda v, k=key: self._on_option_changed(k, bool(v)))
            card.body_layout.addWidget(chk)
        card.body_layout.addWidget(QLabel("Fuseau des dates des images"))
        self.cmb_tz = QComboBox()
        self.cmb_tz.addItem("Heure locale de cet ordinateur", eng.TZ_LOCAL)
        self.cmb_tz.addItem("UTC", eng.TZ_UTC)
        self.cmb_tz.setToolTip("Google enregistre l'heure en UTC. Les vidéos QuickTime sont toujours écrites en UTC.")
        idx = self.cmb_tz.findData(p.get("tz", eng.TZ_LOCAL))
        self.cmb_tz.setCurrentIndex(max(0, idx))
        self.cmb_tz.currentIndexChanged.connect(lambda _i: self._on_option_changed("tz", self.cmb_tz.currentData()))
        card.body_layout.addWidget(self.cmb_tz)
        sl.addWidget(card)

        card_q = Card("Liste")
        self.btn_clear = QPushButton("Vider la liste")
        self.btn_clear.clicked.connect(self._on_clear)
        card_q.body_layout.addWidget(self.btn_clear)
        sl.addWidget(card_q)
        sl.addStretch(1)
        self.set_side_panel(side, "Réglages")
        self._lockables = [self.drop, self.chk_dates, self.chk_gps, self.chk_overwrite, self.chk_mtime,
                           self.chk_recursive, self.cmb_tz, self.btn_clear]

    def _options(self) -> eng.Options:
        return eng.Options(write_dates=self.chk_dates.isChecked(), write_gps=self.chk_gps.isChecked(),
                           overwrite=self.chk_overwrite.isChecked(), align_mtime=self.chk_mtime.isChecked(),
                           tz=str(self.cmb_tz.currentData()))

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
                self.log_panel.append_log("ERR", "ExifTool est introuvable : « Appliquer » est désactivé. "
                                                 "Indiquez son chemin dans les Préférences.")
        self.set_run_enabled(self._exiftool is not None and not self.is_busy())
        return self._exiftool

    # ── Ajout de dossiers et analyse ────────────────────────────────────────
    def on_open(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Choisir le dossier de l'export Google Photos", start)
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
        self.run_worker(worker, on_completed=self._on_analyzed, lock_widgets=self._lockables)

    def _on_analyzed(self, result: object) -> None:
        if isinstance(result, eng.Analysis):
            self._items, self._orphans = result.items, result.orphan_json
        self._render()

    def _on_clear(self) -> None:
        if self.is_busy():
            return
        self._roots, self._items, self._orphans = [], [], 0
        self._render()

    def _render(self) -> None:
        opts = self._options()
        rows = []
        for it in self._items:
            date, gps = eng.describe(it.sidecar, opts)
            rows.append({"file": it.path.name, "json": it.sidecar.path.name if it.sidecar else "",
                         "date": date, "gps": gps, "state": it.state, "_tag": eng.state_tag(it.state)})
        self.table.data_model.set_rows(rows)
        if not self._items:
            self.lbl_summary.setText("Aucun dossier analysé.")
            return
        count = lambda s: sum(1 for i in self._items if i.state == s)       # noqa: E731
        self.lbl_summary.setText(
            f"{len(self._items)} média(s) · {count(eng.STATE_READY)} prêt(s) · {count(eng.STATE_NO_JSON)} sans JSON · "
            f"{count(eng.STATE_NO_DATA)} sans date ni position · {self._orphans} JSON sans média.")

    # ── Application ─────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        exiftool = self._ensure_exiftool(quiet=False)
        if exiftool is None:
            QMessageBox.warning(self, "ExifTool introuvable", "Impossible d'appliquer sans ExifTool.")
            return
        opts = self._options()
        if not (opts.write_dates or opts.write_gps or opts.align_mtime):
            QMessageBox.information(self, "Rien à écrire", "Cochez au moins une option d'écriture.")
            return
        ready = [i for i in self._items if i.state == eng.STATE_READY]
        if not ready:
            QMessageBox.information(self, "Rien à appliquer", "Aucun média prêt : ajoutez un dossier d'export.")
            return
        msg = (f"Les dates et positions de {len(ready)} fichier(s) vont être écrites dans les fichiers eux-mêmes.\n\n"
               "L'état précédent est enregistré pour permettre l'annulation. Continuer ?")
        if QMessageBox.question(self, "Confirmer l'application", msg, _Yes | _No, _No) != _Yes:
            return
        worker = ApplyWorker(exiftool, self._items, opts)
        self.run_worker(worker, on_completed=self._on_applied, lock_widgets=self._lockables,
                        history_action="Réinjection Takeout")

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
            QMessageBox.warning(self, "ExifTool introuvable", "Impossible d'annuler sans ExifTool.")
            return
        storage = get_storage()
        key = tags.latest_undo_key(storage.list_keys(eng.UNDO_COLLECTION))
        rec = storage.load(eng.UNDO_COLLECTION, key) if key else None
        if not key or not rec:
            QMessageBox.information(self, "Rien à annuler", "Aucune application à annuler.")
            self._refresh_undo_button()
            return
        quand = str(rec.get("timestamp", "")).replace("T", " ")
        if QMessageBox.question(self, "Annuler la dernière application",
                                f"Rétablir l'état d'avant l'application du {quand} sur {len(rec.get('files', []))} "
                                "fichier(s) ?", _Yes | _No, _No) != _Yes:
            return
        self.run_worker(TakeoutUndoWorker(exiftool, key), on_completed=self._on_undone,
                        lock_widgets=self._lockables, history_action="Annulation Takeout")

    def _on_undone(self, _result: object) -> None:
        QTimer.singleShot(0, self._refresh_undo_button)
        if self._roots:
            QTimer.singleShot(0, lambda: self.open_paths([str(r) for r in self._roots]))
