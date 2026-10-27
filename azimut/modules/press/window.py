"""Azimut Press — fenêtre du module : file d'attente, réglages, lancement du traitement."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from azimut.core.fsutil import format_size
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.modules.press import imagepress, jsonpress
from azimut.modules.press.imagepress import ImageSettings
from azimut.modules.press.jsonpress import JsonSettings
from azimut.modules.press.workers import (KIND_JSON, STATE_LABELS, STATE_PENDING, STATE_TAGS, ItemResult,
                                          PressItem, PressOptions, PressSummary, PressWorker, ScanWorker,
                                          all_extensions, format_gain)
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, DropZone, PathField

COLUMNS = [
    Column("file", "Fichier", 320, stretch=True),
    Column("type", "Type", 70, align="center"),
    Column("before", "Avant", 95, align="right"),
    Column("after", "Après", 95, align="right"),
    Column("gain", "Gain", 80, align="right"),
    Column("state", "État", 140),
]

JSON_CHOICES = (
    ("minify", "Minifier"),
    ("indent2", "Indenter (2 espaces)"),
    ("indent4", "Indenter (4 espaces)"),
)


def _key(path: Path) -> Path:
    """Clé d'unicité d'un fichier de la file (chemin résolu quand c'est possible)."""
    try:
        return path.resolve()
    except OSError:
        return path


class PressWindow(ModuleWindow):
    """Compresseur d'images et de fichiers JSON."""

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._items: list[PressItem] = []
        self._build_ui()
        self._load_prefs()
        self.set_run_label("Compresser")
        self._update_totals()

    # --- Construction de l'interface -----------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        self.drop = DropZone(
            "Déposez ici des images, des fichiers JSON ou des dossiers\n(ou cliquez pour choisir des fichiers)",
            extensions=all_extensions(),
        )
        self.drop.pathsDropped.connect(self.open_paths)
        self.table = DataTable(COLUMNS)
        self.total_label = QLabel()
        layout.addWidget(self.drop)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.total_label)
        self.set_content(central)

        self._settings_widget = self._build_settings()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self._settings_widget)
        scroll.setMinimumWidth(340)
        self.set_side_panel(scroll, "Réglages")

        self.add_toolbar_button("Ajouter un dossier…", self._add_folder,
                                tooltip="Ajouter tous les fichiers pris en charge d'un dossier")
        self.add_toolbar_button("Retirer la sélection", self._remove_selected, kind="ghost")
        self.add_toolbar_button("Vider la liste", self._clear_all, kind="ghost")

    def _build_settings(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self._build_image_card())
        layout.addWidget(self._build_json_card())
        layout.addWidget(self._build_output_card())
        layout.addStretch(1)
        return panel

    def _build_image_card(self) -> Card:
        card = Card("Images")
        body = card.body_layout

        body.addWidget(QLabel("Format de sortie"))
        self.cmb_format = QComboBox()
        availability = imagepress.format_availability()
        unavailable: list[str] = []
        for key in imagepress.OUTPUT_FORMATS:
            ok, reason = availability[key]
            label = imagepress.OUTPUT_LABELS[key] + ("" if ok else " (indisponible)")
            self.cmb_format.addItem(label, key)
            if not ok:   # option grisée, avec l'explication en infobulle et en note
                item = self.cmb_format.model().item(self.cmb_format.count() - 1)
                item.setEnabled(False)
                item.setToolTip(reason)
                unavailable.append(f"{imagepress.OUTPUT_LABELS[key]} : {reason}")
        self.cmb_format.currentIndexChanged.connect(self._on_format_changed)
        body.addWidget(self.cmb_format)
        if unavailable:
            note = QLabel("\n".join(unavailable))
            note.setWordWrap(True)
            body.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(QLabel("Qualité"))
        self.slider_quality = QSlider(Qt.Horizontal)
        self.slider_quality.setRange(1, 100)
        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(1, 100)
        self.slider_quality.valueChanged.connect(self.spin_quality.setValue)
        self.spin_quality.valueChanged.connect(self.slider_quality.setValue)
        row.addWidget(self.slider_quality, 1)
        row.addWidget(self.spin_quality)
        body.addLayout(row)

        row = QHBoxLayout()
        self.chk_dim = QCheckBox("Limiter la plus grande dimension à")
        self.spin_dim = QSpinBox()
        self.spin_dim.setRange(16, 30000)
        self.spin_dim.setValue(2048)
        self.spin_dim.setSuffix(" px")
        self.spin_dim.setEnabled(False)
        self.chk_dim.toggled.connect(self.spin_dim.setEnabled)
        row.addWidget(self.chk_dim, 1)
        row.addWidget(self.spin_dim)
        body.addLayout(row)

        self.chk_strip = QCheckBox("Supprimer les métadonnées")
        self.chk_strip.setToolTip("Retire EXIF et profil ICC ; l'orientation est d'abord appliquée aux pixels.")
        self.chk_colors = QCheckBox("Réduire les couleurs (avec perte, 256 couleurs)")
        self.chk_colors.setToolTip("PNG uniquement.")
        self.chk_lossless = QCheckBox("WebP sans perte")
        self.chk_lossless.setToolTip("WebP uniquement : la qualité règle alors l'effort de compression.")
        self.chk_smallest = QCheckBox("Conserver le plus petit")
        self.chk_smallest.setToolTip("Si le résultat n'est pas plus petit, l'original est copié (« Déjà optimisé »).")
        for box in (self.chk_strip, self.chk_colors, self.chk_lossless, self.chk_smallest):
            body.addWidget(box)
        return card

    def _build_json_card(self) -> Card:
        card = Card("JSON")
        self.cmb_json = QComboBox()
        for key, label in JSON_CHOICES:
            self.cmb_json.addItem(label, key)
        self.cmb_json.currentIndexChanged.connect(self._on_json_changed)
        self.chk_sort = QCheckBox("Trier les clés")
        self.lbl_json_warning = QLabel(jsonpress.INDENT_WARNING)
        self.lbl_json_warning.setWordWrap(True)
        card.body_layout.addWidget(QLabel("Mode"))
        card.body_layout.addWidget(self.cmb_json)
        card.body_layout.addWidget(self.chk_sort)
        card.body_layout.addWidget(self.lbl_json_warning)
        return card

    def _build_output_card(self) -> Card:
        card = Card("Sortie")
        self.out_field = PathField("Dossier de sortie (vide : « <source>_Pressed » voisin de la source)")
        self.chk_recursive = QCheckBox("Inclure les sous-dossiers à l'ajout")
        self.chk_inplace = QCheckBox("Remplacer sur place (confirmation requise)")
        self.chk_inplace.setToolTip("Les originaux sont déplacés dans le dossier de sauvegarde avant d'être remplacés.")
        self.backup_field = PathField("Dossier de sauvegarde des originaux")
        self.chk_inplace.toggled.connect(self._on_inplace_toggled)
        for widget in (self.out_field, self.chk_recursive, self.chk_inplace, self.backup_field):
            card.body_layout.addWidget(widget)
        return card

    # --- Préférences ---------------------------------------------------------------------------

    def _load_prefs(self) -> None:
        self._apply_image_settings(ImageSettings.from_dict(self.prefs.get("image", {})))
        self._apply_json_settings(JsonSettings.from_dict(self.prefs.get("json", {})))
        self.out_field.set_path(str(self.prefs.get("output_dir", "") or ""))
        self.backup_field.set_path(str(self.prefs.get("backup_dir", "") or ""))
        self.chk_recursive.setChecked(bool(self.prefs.get("recursive", True)))
        self.chk_inplace.setChecked(False)   # le remplacement sur place n'est jamais mémorisé
        self._on_inplace_toggled(False)

    def _save_prefs(self) -> None:
        self.prefs.set("image", self._read_image_settings().to_dict())
        self.prefs.set("json", self._read_json_settings().to_dict())
        self.prefs.set("output_dir", self.out_field.path().strip())
        self.prefs.set("backup_dir", self.backup_field.path().strip())
        self.prefs.set("recursive", self.chk_recursive.isChecked())

    def closeEvent(self, event) -> None:  # noqa: N802 — signature Qt
        self._save_prefs()
        super().closeEvent(event)

    def _apply_image_settings(self, s: ImageSettings) -> None:
        availability = imagepress.format_availability()
        fmt = s.output_format if availability.get(s.output_format, (False, ""))[0] else imagepress.FORMAT_KEEP
        self.cmb_format.setCurrentIndex(max(0, self.cmb_format.findData(fmt)))
        self.spin_quality.setValue(s.quality)
        self.chk_dim.setChecked(s.max_dimension is not None)
        if s.max_dimension:
            self.spin_dim.setValue(s.max_dimension)
        self.chk_strip.setChecked(s.strip_metadata)
        self.chk_colors.setChecked(s.reduce_colors)
        self.chk_lossless.setChecked(s.webp_lossless)
        self.chk_smallest.setChecked(s.keep_smallest)
        self._on_format_changed()

    def _read_image_settings(self) -> ImageSettings:
        return ImageSettings(
            output_format=str(self.cmb_format.currentData()),
            quality=self.spin_quality.value(),
            max_dimension=self.spin_dim.value() if self.chk_dim.isChecked() else None,
            strip_metadata=self.chk_strip.isChecked(),
            reduce_colors=self.chk_colors.isChecked(),
            webp_lossless=self.chk_lossless.isChecked(),
            keep_smallest=self.chk_smallest.isChecked(),
        )

    def _apply_json_settings(self, s: JsonSettings) -> None:
        key = "minify" if s.mode == jsonpress.MODE_MINIFY else f"indent{s.indent}"
        self.cmb_json.setCurrentIndex(max(0, self.cmb_json.findData(key)))
        self.chk_sort.setChecked(s.sort_keys)
        self._on_json_changed()

    def _read_json_settings(self) -> JsonSettings:
        key = str(self.cmb_json.currentData())
        return JsonSettings(
            mode=jsonpress.MODE_MINIFY if key == "minify" else jsonpress.MODE_INDENT,
            indent=4 if key == "indent4" else 2,
            sort_keys=self.chk_sort.isChecked(),
        )

    # --- Réactions de l'interface --------------------------------------------------------------

    def _on_format_changed(self, *_: object) -> None:
        fmt = str(self.cmb_format.currentData())
        self.chk_colors.setEnabled(fmt in ("keep", "png"))
        self.chk_lossless.setEnabled(fmt in ("keep", "webp"))

    def _on_json_changed(self, *_: object) -> None:
        indent = str(self.cmb_json.currentData()) != "minify"
        self.chk_sort.setEnabled(indent)
        self.lbl_json_warning.setVisible(indent)

    def _on_inplace_toggled(self, checked: bool) -> None:
        self.backup_field.setEnabled(checked)
        self.out_field.setEnabled(not checked)

    # --- Ajout de fichiers ---------------------------------------------------------------------

    def open_paths(self, paths: list[str]) -> None:
        """Ajoute fichiers et dossiers (glisser-déposer, ligne de commande, Atlas)."""
        if not paths:
            return
        if self.is_busy():
            self.log_panel.append_log("WARN", "Un traitement est en cours : ajoutez les fichiers une fois terminé.")
            return
        worker = ScanWorker([Path(p) for p in paths], self.chk_recursive.isChecked(), self)
        self.run_worker(worker, on_completed=self._on_scan_completed, lock_widgets=[self.drop])

    def on_open(self) -> None:
        if self.is_busy():
            return
        patterns = " ".join(f"*{ext}" for ext in all_extensions())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter des fichiers", self._last_dir(),
            f"Images et JSON ({patterns});;Tous les fichiers (*)",
        )
        if paths:
            self._remember_dir(Path(paths[0]).parent)
            self.open_paths(paths)

    def _add_folder(self) -> None:
        if self.is_busy():
            return
        folder = QFileDialog.getExistingDirectory(self, "Ajouter un dossier", self._last_dir())
        if folder:
            self._remember_dir(Path(folder).parent)
            self.open_paths([folder])

    @staticmethod
    def _last_dir() -> str:
        return str(Preferences.instance().get("last_dir", "") or "")

    @staticmethod
    def _remember_dir(folder: Path) -> None:
        Preferences.instance().set("last_dir", str(folder))

    def _on_scan_completed(self, result: object) -> None:
        found = result if isinstance(result, list) else []
        known = {_key(item.path) for item in self._items}
        added = 0
        for item in found:
            key = _key(item.path)
            if key in known:
                continue
            known.add(key)
            self._items.append(item)
            self.table.data_model.append_row(self._row_for(item))
            added += 1
        if len(found) - added:
            self.log_panel.append_log("INFO", f"{len(found) - added} fichier(s) déjà présent(s) dans la liste.")
        self._update_totals()

    def _remove_selected(self) -> None:
        if self.is_busy():
            return
        selected = set(self.table.selected_rows())
        if selected:
            self._set_items([item for row, item in enumerate(self._items) if row not in selected])

    def _clear_all(self) -> None:
        if not self.is_busy():
            self._set_items([])

    def _set_items(self, items: list[PressItem]) -> None:
        self._items = items
        self.table.data_model.set_rows([self._row_for(item) for item in items])
        self._update_totals()

    # --- Tableau et totaux ---------------------------------------------------------------------

    @staticmethod
    def _row_for(item: PressItem) -> dict:
        row = {
            "file": item.display or item.path.name,
            "type": "JSON" if item.kind == KIND_JSON else "Image",
            "before": format_size(item.size),
            "after": "—",
            "gain": "—",
            "state": STATE_PENDING,
            "_tag": "neutre",
        }
        if item.result is not None:
            row.update(PressWindow._result_values(item.result))
        return row

    @staticmethod
    def _result_values(result: ItemResult) -> dict:
        values = {"state": STATE_LABELS[result.status], "_tag": STATE_TAGS[result.status],
                  "after": "—", "gain": "—"}
        if result.ok and result.size_after is not None:
            values["after"] = format_size(result.size_after)
            values["gain"] = format_gain(result.size_before, result.size_after)
        return values

    def _update_totals(self) -> None:
        results = [item.result for item in self._items if item.result is not None and item.result.ok]
        if not results:
            self.total_label.setText(f"{len(self._items)} fichier(s) dans la liste.")
            return
        before = sum(r.size_before for r in results)
        after = sum(r.size_after or 0 for r in results)
        self.total_label.setText(
            f"{len(self._items)} fichier(s) · {len(results)} traité(s) · Avant : {format_size(before)} · "
            f"Après : {format_size(after)} · Gain total : {format_size(before - after)} ({format_gain(before, after)})"
        )

    # --- Traitement ----------------------------------------------------------------------------

    def on_run(self) -> None:
        if self.is_busy():
            return
        jobs = [(row, item) for row, item in enumerate(self._items) if not item.done]
        if not jobs:
            self.log_panel.append_log("INFO", "Rien à traiter : ajoutez des images, des fichiers JSON ou des dossiers.")
            return
        options = self._build_options()
        if options is None or (options.in_place and not self._confirm_in_place(options, len(jobs))):
            return
        self._save_prefs()
        for row, item in jobs:
            item.result = None
            self.table.data_model.update_row(row, after="—", gain="—", state=STATE_PENDING, _tag="neutre")
        worker = PressWorker(jobs, options, self)
        worker.item_done.connect(self._on_item_done)
        self.run_worker(
            worker,
            on_completed=self._on_run_completed,
            on_failed=self._on_run_failed,
            on_cancelled=self._on_run_cancelled,
            lock_widgets=[self._settings_widget, self.drop],
            history_action="Compression",
        )

    def _build_options(self) -> PressOptions | None:
        in_place = self.chk_inplace.isChecked()
        out_text = self.out_field.path().strip()
        backup_text = self.backup_field.path().strip()
        if in_place and not backup_text:
            QMessageBox.warning(self, "Sauvegarde requise",
                                "Choisissez un dossier de sauvegarde pour les originaux avant de remplacer sur place.")
            return None
        return PressOptions(
            image_settings=self._read_image_settings(),
            json_settings=self._read_json_settings(),
            out_dir=Path(out_text) if out_text and not in_place else None,
            in_place=in_place,
            backup_dir=Path(backup_text) if in_place else None,
        )

    def _confirm_in_place(self, options: PressOptions, count: int) -> bool:
        box = QMessageBox(
            QMessageBox.Warning, "Remplacer sur place",
            f"{count} fichier(s) vont être remplacés sur place.\n\n"
            f"Chaque original est d'abord déplacé dans :\n{options.backup_dir}\n\nContinuer ?",
            parent=self,
        )
        confirm = box.addButton("Remplacer", QMessageBox.AcceptRole)
        cancel = box.addButton("Annuler", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def _on_item_done(self, row: int, result: object) -> None:
        if not isinstance(result, ItemResult) or not 0 <= row < len(self._items):
            return
        self._items[row].result = result
        self.table.data_model.update_row(row, **self._result_values(result))
        self._update_totals()

    def _on_run_completed(self, summary: object) -> None:
        self._update_totals()
        if isinstance(summary, PressSummary):
            self.log_panel.append_log(
                "OK" if not summary.errors else "WARN",
                f"Gain total : {format_size(summary.saved)} ({format_gain(summary.total_before, summary.total_after)}).",
            )

    def _on_run_failed(self, message: str) -> None:
        self.log_panel.append_log("ERR", f"Traitement interrompu : {message}")
        self._update_totals()

    def _on_run_cancelled(self) -> None:
        self.log_panel.append_log("WARN", "Traitement annulé : les fichiers non terminés restent en attente.")
        self._update_totals()
