"""Fenêtre d'Azimut Tags : liste de fichiers, tableau des métadonnées, modifications en attente."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.fsutil import format_size
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.core.storage import get_storage
from azimut.modules.tags import engine as eng
from azimut.modules.tags.workers import ReadWorker, UndoWorker, WriteWorker
from azimut.ui.controls import CheckBox, SegmentedControl, set_action_available
from azimut.ui.file_dialog import choose_directory, choose_files
from azimut.ui.flow_layout import FlowLayout
from azimut.ui.module_window import ModuleWindow
from azimut.ui.theme import scaled, token
from azimut.ui.widgets import Card, Column, DataTable, DropZone, MediaPreview, plain_message

log = get_logger(__name__)

OP_LABELS = {"set": QCoreApplication.translate("TagsWindow", "Définir"), "delete": QCoreApplication.translate("TagsWindow", "Supprimer")}
_Yes, _No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


class _GpsDialog(QDialog):
    """Saisie et validation d'une position GPS."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Définir la position GPS"))
        self.values: tuple[float, float, float] | None = None
        form = QFormLayout(self)
        self._lat, self._lon, self._alt = QLineEdit(), QLineEdit(), QLineEdit()
        self._lat.setPlaceholderText(self.tr("ex. 48.8584 (entre -90 et 90)"))
        self._lon.setPlaceholderText(self.tr("ex. 2.2945 (entre -180 et 180)"))
        self._alt.setPlaceholderText(self.tr("facultative, en mètres"))
        form.addRow(self.tr("Latitude :"), self._lat)
        form.addRow(self.tr("Longitude :"), self._lon)
        form.addRow(self.tr("Altitude :"), self._alt)
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {token('err_txt')};")
        form.addRow(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Ajouter"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Annuler"))
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
    """Éditeur de métadonnées : rien n'est écrit avant « Appliquer les modifications »."""

    # Même sur un seul fichier, l'écriture est trop rapide pour que la barre liquide
    # ait le temps de se voir : on lui réserve une durée minimale d'affichage.
    APPLY_MIN_PROGRESS_MS = 2400

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self.set_run_label(self.tr("Appliquer les modifications"))
        self._files: list[eng.FileTags] = []
        self._index: dict[str, int] = {}
        self._current_index: int | None = None
        self._current_entries: list[eng.TagEntry] = []
        self._pending: list[eng.TagChange] = []
        self._exiftool: Path | None = None
        self._lockables: list[QWidget] = []
        self._build_ui()
        self.busyChanged.connect(self._on_busy_changed)
        self._refresh_files_actions()
        self._refresh_meta_actions()
        self._refresh_pending_actions()
        self._undo_btn = self.add_action_button(
            self.tr("Annuler la dernière application"), self._undo_last, kind="secondary",
            tooltip=self.tr("Rétablit les valeurs d'avant la dernière application"))
        self._init_binary()
        self._refresh_undo_button()

    # ── Construction ─────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self._drop = DropZone(self.tr("Glissez des fichiers ou des dossiers ici (ou cliquez pour parcourir)"))
        self._drop.setMinimumHeight(scaled(64))
        self._drop.setMaximumHeight(90)
        self._drop.pathsDropped.connect(self.open_paths)
        outer.addWidget(self._drop)

        split = QSplitter(Qt.Orientation.Horizontal)
        files_card = Card(self.tr("Fichiers"))
        self._files_table = DataTable([
            Column("name", self.tr("Nom"), 220, stretch=True), Column("type", self.tr("Type"), 60),
            Column("size", self.tr("Taille"), 80, align="right"), Column("tags", self.tr("Tags"), 55, align="right"),
            Column("state", self.tr("État"), 130)])
        self._files_table.set_empty_text(self.tr("Aucun fichier : ouvrez des fichiers pour lire leurs métadonnées."))
        self._files_table.selectionRowsChanged.connect(self._on_files_selected)
        self._sel_label = QLabel(self.tr("Aucun fichier."))
        files_card.body_layout.addWidget(self._files_table, 1)
        files_card.body_layout.addWidget(self._sel_label)
        split.addWidget(files_card)

        meta_card = Card(self.tr("Métadonnées"))
        self._meta_title = QLabel("")
        self._meta_title.setTextFormat(Qt.TextFormat.PlainText)
        self._meta_title.setVisible(False)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(self.tr("Filtrer les tags (groupe, nom ou valeur)…"))
        self._filter.setClearButtonEnabled(True)
        # Le texte d'invite ne doit pas imposer sa largeur au panneau : à 150 %, il
        # poussait le tableau des métadonnées à 396 px et écrasait la liste des fichiers.
        self._filter.setMinimumWidth(0)
        self._filter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._meta = DataTable([
            Column("group", self.tr("Groupe"), 110), Column("name", self.tr("Tag"), 190),
            Column("value", self.tr("Valeur"), 260, stretch=True), Column("state", self.tr("État"), 100)])
        self._filter.textChanged.connect(self._meta.set_filter_text)
        self._meta.rowActivated.connect(self._edit_tag)
        self._meta.selectionRowsChanged.connect(lambda _rows: self._refresh_meta_actions())
        meta_btns = FlowLayout(h_spacing=8, v_spacing=6)
        self._edit_btn = QPushButton(self.tr("Modifier la valeur…"))
        self._del_btn = QPushButton(self.tr("Supprimer le tag"))
        self._del_btn.setProperty("kind", "danger")      # action destructive
        self._edit_btn.clicked.connect(self._edit_selected)
        self._del_btn.clicked.connect(self._delete_selected_tags)
        meta_btns.addWidget(self._edit_btn)
        meta_btns.addWidget(self._del_btn)
        for w in (self._meta_title, self._filter, self._meta):
            meta_card.body_layout.addWidget(w, 1 if w is self._meta else 0)
        meta_card.body_layout.addLayout(meta_btns)
        split.addWidget(meta_card)
        # Les deux tableaux ont besoin de la même largeur : donner plus à Fichiers
        # réduisait Métadonnées à une bande de 200 px à 150 %.
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 1000])
        split.setChildrenCollapsible(False)
        self._split = split

        pend_card = Card(self.tr("Modifications en attente (rien n'est écrit avant « Appliquer les modifications »)"))
        self._pending_table = DataTable([
            Column("op", self.tr("Opération"), 100), Column("tag", self.tr("Tag"), 260),
            Column("value", self.tr("Valeur"), 300, stretch=True)])
        self._pending_table.selectionRowsChanged.connect(lambda _rows: self._refresh_pending_actions())
        pend_btns = FlowLayout(h_spacing=8, v_spacing=6)
        self._pend_remove = QPushButton(self.tr("Retirer la sélection"))
        self._pend_clear = QPushButton(self.tr("Tout retirer"))
        self._pend_remove.clicked.connect(self._remove_pending)
        self._pend_clear.clicked.connect(self._clear_pending)
        pend_btns.addWidget(self._pend_remove)
        pend_btns.addWidget(self._pend_clear)
        pend_card.body_layout.addWidget(self._pending_table)
        pend_card.body_layout.addLayout(pend_btns)

        # Deux pages avec le même sélecteur à capsule glissante que les thèmes. Le
        # sélecteur garde sa largeur naturelle : quand la fenêtre est étroite, les
        # deux actions passent sur une seconde ligne au lieu de le chevaucher.
        self._nav = QWidget()
        self._nav.installEventFilter(self)
        nav_root = QVBoxLayout(self._nav)
        nav_root.setContentsMargins(0, 0, 0, 0)
        nav_root.setSpacing(4)
        self._nav_top = QHBoxLayout()
        self._nav_top.setContentsMargins(0, 0, 0, 0)
        self._nav_top.setSpacing(4)
        nav_root.addLayout(self._nav_top)
        self._nav_bottom = QWidget()
        self._nav_bottom_layout = QHBoxLayout(self._nav_bottom)
        self._nav_bottom_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_bottom_layout.setSpacing(4)
        nav_root.addWidget(self._nav_bottom)
        self._nav_bottom.setVisible(False)
        self._nav_wide: bool | None = None
        self._subtab_strip = SegmentedControl()
        for label in (self.tr("Fichiers et métadonnées"), self.tr("Modifications en attente")):
            self._subtab_strip.addItem(label)
        self._subtab_strip.currentIndexChanged.connect(self._show_subtab)
        self._recursive = CheckBox(self.tr("Inclure les sous-dossiers"))
        self._recursive.setChecked(bool(self.prefs.get("recursive", True)))
        self._recursive.toggled.connect(lambda v: self.prefs.set("recursive", bool(v)))
        self._clear_btn = QPushButton(self.tr("Vider la liste"))
        self._clear_btn.clicked.connect(self._on_clear_files)
        self._arrange_nav(force=True)
        outer.addWidget(self._nav)

        self.subtabs = QStackedWidget()
        self.subtabs.addWidget(split)
        self.subtabs.addWidget(pend_card)
        outer.addWidget(self.subtabs, 1)
        outer.addWidget(self.action_bar)
        self._show_subtab(0)
        self.set_content(root)

        side = QWidget()
        sl = QVBoxLayout(side)
        # Un seul aperçu média : vignette pour les images, lecteur pour l'audio et la vidéo.
        # Son bouton « Ouvrir dans une application » est petit, juste sous la timeline.
        self._media = MediaPreview(260, volume_controls=True)
        self._media.open_btn.failed.connect(lambda msg: self.log_panel.append_log("WARN", msg))
        sl.addWidget(self._media)
        presets = Card(self.tr("Préréglages"))
        self._preset_btns = []
        for text, cb in ((self.tr("Supprimer le GPS"), self._preset_delete_gps),
                         (self.tr("Supprimer toutes les métadonnées…"), self._preset_delete_all),
                         (self.tr("Définir la date de prise de vue…"), self._preset_set_date),
                         (self.tr("Définir la position GPS…"), self._preset_set_gps)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            presets.body_layout.addWidget(b)
            self._preset_btns.append(b)
        sl.addWidget(presets)
        options = Card(self.tr("Options"))
        self._align = CheckBox(self.tr("Aligner la date de modification du fichier"))
        self._align.setToolTip(self.tr("Après écriture de la date de prise de vue, la date de modification du fichier est alignée dessus."))
        self._align.setChecked(False)
        options.body_layout.addWidget(self._align)
        sl.addWidget(options)
        sl.addStretch(1)
        self.set_side_panel(side, self.tr("Aperçu et préréglages"))

        self._lockables = [self._drop, self._edit_btn, self._del_btn, self._pend_remove, self._pend_clear,
                           self._clear_btn, *self._preset_btns]
        self._arrange_split()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if hasattr(self, "_nav"):
            self._arrange_nav()
        self._arrange_split()

    def _arrange_split(self) -> None:
        """Empile Fichiers et Métadonnées quand côte à côte ils n'ont plus de place.

        À 150 %, le panneau d'aperçu et le zoom laissent ~700 px au contenu : deux
        tableaux de 5 et 4 colonnes y perdaient tous leurs en-têtes. En vertical, chaque
        tableau reprend toute la largeur et les cases restent lisibles.
        """
        if not hasattr(self, "_split") or not hasattr(self, "_content_widget"):
            return
        stacked = self._split.orientation() == Qt.Orientation.Vertical
        # Le seuil ne se déclenche que si les deux tableaux ne peuvent vraiment plus
        # tenir côte à côte ; à 150 %, la barre horizontale de chaque tableau est
        # préférable à un empilement qui masquerait le second tableau.
        wanted = self._content_widget.width() < scaled(300)
        if wanted == stacked:
            return
        self._split.setOrientation(Qt.Orientation.Vertical if wanted else Qt.Orientation.Horizontal)
        self._split.setSizes([1000, 1000])

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self._nav and event.type() == QEvent.Type.Resize:
            self._arrange_nav()
        return super().eventFilter(watched, event)

    def _arrange_nav(self, *, force: bool = False) -> None:
        """Garde le sélecteur et ses actions sur une ou deux lignes selon la place."""
        if not hasattr(self, "_subtab_strip"):
            return
        spacing = 4
        required = (self._subtab_strip.width()
                    + self._recursive.sizeHint().width()
                    + self._clear_btn.sizeHint().width()
                    + 12 + spacing * 4 + 8)
        wide = self._nav.width() >= required
        if not force and wide == self._nav_wide:
            return
        self._nav_wide = wide
        for layout in (self._nav_top, self._nav_bottom_layout):
            while layout.count():
                layout.takeAt(0)
        self._nav_top.addWidget(self._subtab_strip)
        self._nav_top.addStretch(1)
        if wide:
            self._nav_top.addWidget(self._recursive)
            self._nav_top.addSpacing(12)
            self._nav_top.addWidget(self._clear_btn)
        else:
            self._nav_bottom_layout.addWidget(self._recursive)
            self._nav_bottom_layout.addSpacing(12)
            self._nav_bottom_layout.addWidget(self._clear_btn)
            self._nav_bottom_layout.addStretch(1)
        self._nav_bottom.setVisible(not wide)
        self._nav_top.invalidate()
        self._nav_bottom_layout.invalidate()

    def _show_subtab(self, index: int) -> None:
        """Affiche une des deux pages et synchronise le sélecteur."""
        if not 0 <= index < self.subtabs.count():
            return
        self.subtabs.setCurrentIndex(index)
        self._subtab_strip.setCurrentIndex(index)

    def _init_binary(self) -> None:
        self.show_binary_status(("exiftool",))
        self._ensure_exiftool(quiet=False)

    def _ensure_exiftool(self, quiet: bool = True) -> Path | None:
        try:
            self._exiftool = binaries.require("exiftool")
        except BinaryNotFoundError as exc:
            self._exiftool = None
            if not quiet:
                self.log_panel.append_log("ERR", str(exc))
                self.log_panel.append_log(
                    "ERR", "ExifTool est introuvable : la lecture et « Appliquer les modifications » "
                           "sont désactivés. Indiquez son chemin dans les Préférences.")
        self._refresh_run_state()
        self._refresh_preset_actions()
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
            QMessageBox.warning(self, self.tr("ExifTool introuvable"),
                                self.tr("ExifTool est nécessaire pour lire les métadonnées. "
                                "Installez-le ou indiquez son chemin dans les Préférences."))
            return
        worker = ReadWorker(exiftool, [Path(p) for p in paths], self._recursive.isChecked())
        # L'import a déjà sa barre dans la zone de dépôt : pas de second indicateur
        # dans la barre d'actions pendant la lecture des métadonnées.
        self.run_worker(worker, on_completed=self._on_files_read, lock_widgets=self._lockables,
                        import_progress=self._drop.import_progress, show_progress=False)

    def on_open(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        files = choose_files(self, self.tr("Importer des fichiers"), start)
        if files:
            Preferences.instance().set("last_dir", str(Path(files[0]).parent))
            self.open_paths(files)

    def on_open_folder(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = choose_directory(self, self.tr("Importer un dossier"), start)
        if folder:
            Preferences.instance().set("last_dir", folder)
            self.open_paths([folder])

    def _on_clear_files(self) -> None:
        """« Vider la liste » : demande confirmation avant d'oublier les fichiers chargés."""
        if self.is_busy() or not self._files:
            return
        if self.confirm_clear_list():
            self._clear_files()

    def _clear_files(self) -> None:
        if self.is_busy():
            return
        self._files, self._index, self._current_index = [], {}, None
        self._files_table.data_model.clear()
        self._render_meta()
        self._show_preview(None)
        self._sel_label.setText(self.tr("Aucun fichier."))
        self._refresh_files_actions()
        self._refresh_meta_actions()
        self._refresh_pending_actions()

    def _file_row(self, ft: eng.FileTags) -> dict:
        refus = eng.write_refusal(ft.path)
        row = {"name": ft.path.name, "type": ft.path.suffix.upper().lstrip(".") or "—",
               "size": format_size(ft.size), "_sort_size": ft.size,
               "tags": str(len(ft.entries)), "_sort_tags": len(ft.entries),
               "state": ft.error or (self.tr("Non modifiable ici") if refus else "")}
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
        self._refresh_files_actions()
        if self._current_index is None and self._files:
            self._files_table.selectRow(0)
        elif self._current_index is not None:
            self._render_meta()

    # ── Sélection et métadonnées ─────────────────────────────────────────
    def _on_files_selected(self, rows: list) -> None:
        rows = [r for r in rows if 0 <= r < len(self._files)]
        if not rows:
            self._sel_label.setText(self.tr("{count} fichier(s) dans la liste, aucun sélectionné.").format(
                count=len(self._files)))
            self._current_index = None
            self._show_preview(None)
            self._render_meta()
            self._refresh_files_actions()
            self._refresh_meta_actions()
            return
        n = len(rows)
        self._sel_label.setText(self.tr("{count} fichier(s) sélectionné(s)").format(count=n)
                                + (self.tr(" : édition par lot.") if n > 1 else "."))
        if rows[0] != self._current_index:
            self._current_index = rows[0]
            path = self._files[rows[0]].path
            self._show_preview(path)
            self._render_meta()
        self._refresh_files_actions()
        self._refresh_meta_actions()

    def _show_preview(self, path: Path | None) -> None:
        """Audio et vidéo → lecteur intégré ; image ou autre → vignette ; rien → case vide."""
        if not hasattr(self, "_media"):                # appelé avant la construction du panneau
            return
        self._media.set_path(path)

    def _render_meta(self) -> None:
        if self._current_index is None or self._current_index >= len(self._files):
            self._current_entries = []
            self._meta.data_model.set_rows([])
            self._meta_title.clear()
            self._meta_title.hide()
            self._refresh_meta_actions()
            return
        ft = self._files[self._current_index]
        entries = sorted(ft.entries, key=lambda e: (e.group.lower(), e.name.lower()))
        self._current_entries = entries
        rows = []
        for e in entries:
            pending = any(eng.entry_matches(e, ch.tag) for ch in self._pending)
            row = {"group": e.group, "name": e.name, "value": e.value,
                   "state": self.tr("En attente") if pending else ("" if e.writable else self.tr("Lecture seule"))}
            if pending:
                row["_tag"] = "warn"
            elif not e.writable:
                row["_tag"] = "neutre"
                row["_fg"] = "muted"              # métadonnée non modifiable : moins importante à l'œil
            rows.append(row)
        self._meta.data_model.set_rows(rows)
        self._meta_title.setText(self.tr("{name} — {count} tag(s)").format(name=ft.path.name, count=len(entries)))
        self._meta_title.show()
        self._refresh_meta_actions()

    def _refresh_files_actions(self) -> None:
        set_action_available(self._clear_btn, bool(self._files) and not self.is_busy())
        self._refresh_preset_actions()
        self._refresh_run_state()

    def _selected_files(self) -> list[eng.FileTags]:
        """Fichiers sélectionnés dans la liste, dans l'ordre du tableau."""
        return [self._files[row] for row in self._files_table.selected_rows()
                if 0 <= row < len(self._files)]

    def _preset_targets(self) -> list[eng.FileTags]:
        """Fichiers sélectionnés que l'on peut réellement modifier."""
        return [ft for ft in self._selected_files()
                if not ft.error and eng.write_refusal(ft.path) is None]

    def _refresh_preset_actions(self) -> None:
        """Active chaque préréglage seulement s'il a une cible et un effet possible."""
        if not self._preset_btns:
            return
        usable = self._exiftool is not None and not self.is_busy()
        targets = self._preset_targets() if usable else []
        can_edit = bool(targets)
        can_delete_gps = any(eng.has_gps_data(ft.entries) for ft in targets)
        can_delete_metadata = any(eng.has_editable_metadata(ft.entries) for ft in targets)
        for button, available in zip(
                self._preset_btns,
                (can_delete_gps, can_delete_metadata, can_edit, can_edit), strict=False):
            set_action_available(button, available)

    def _on_busy_changed(self, busy: bool) -> None:
        """Réapplique les conditions après le verrouillage/déverrouillage d'un traitement."""
        if busy:
            self._refresh_preset_actions()
        else:
            QTimer.singleShot(0, self._refresh_preset_actions)
        self._refresh_run_state()

    def _refresh_meta_actions(self) -> None:
        """« Modifier » et « Supprimer le tag » exigent une métadonnée sélectionnée."""
        enabled = bool(self._meta.selected_rows()) and not self.is_busy()
        set_action_available(self._edit_btn, enabled)
        set_action_available(self._del_btn, enabled, active="danger")

    def _refresh_pending_actions(self) -> None:
        busy = self.is_busy()
        set_action_available(self._pend_remove,
                             bool(self._pending_table.selected_rows()) and not busy)
        set_action_available(self._pend_clear, bool(self._pending) and not busy)

    def _refresh_run_state(self) -> None:
        """« Appliquer les modifications » exige ExifTool, une cible et des changements valides."""
        if not hasattr(self, "_files_table"):
            return
        targets = [self._files[row].path for row in self._files_table.selected_rows()
                   if 0 <= row < len(self._files)]
        writable = any(eng.write_refusal(path) is None for path in targets)
        valid = bool(self._pending) and not eng.validate_changes(self._pending)
        self.set_run_enabled(getattr(self, "_exiftool", None) is not None and writable and valid)

    def _current_refusal(self) -> str | None:
        if self._current_index is None:
            return self.tr("Aucun fichier sélectionné.")
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
            plain_message(self, QMessageBox.Icon.Warning, self.tr("Modification refusée"), reason)
            self.log_panel.append_log("WARN", f"{entry.full_name} : {reason}")
            return
        text, ok = QInputDialog.getText(self, self.tr("Modifier la valeur"), f"{entry.full_name} :",
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
            plain_message(self, QMessageBox.Icon.Warning, self.tr("Suppression refusée"), reason)
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
            QMessageBox.warning(
                self, self.tr("Suppression refusée"),
                self.tr("{count} tag(s) en lecture seule ou binaires n'ont pas été retenus : "
                        "seuls les tags EXIF, IPTC et XMP sont modifiables.").format(count=refused))
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
        self._subtab_strip.setItemText(
            1, self.tr("Modifications en attente ({count})").format(count=len(rows))
            if rows else self.tr("Modifications en attente"))
        self._arrange_nav()
        self._refresh_pending_actions()
        self._refresh_run_state()
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
            self, self.tr("Supprimer toutes les métadonnées"),
            self.tr("Cette modification supprimera TOUTES les métadonnées modifiables (EXIF, IPTC, XMP) des fichiers "
            "sélectionnés au moment de « Appliquer les modifications ».\n\nElle reste annulable avec "
            "« Annuler la dernière application » (hors valeurs binaires).\n\n"
            "Ajouter cette modification en attente ?"),
            _Yes | _No, _No)
        if rep == _Yes:
            self._add_pending(eng.preset_delete_all())

    def _preset_set_date(self) -> None:
        text, ok = QInputDialog.getText(self, self.tr("Définir la date de prise de vue"), self.tr("Date (AAAA-MM-JJ HH:MM:SS) :"),
                                        QLineEdit.EchoMode.Normal, datetime.now().strftime(eng.INPUT_DATE_FORMAT))
        if not ok:
            return
        try:
            self._add_pending(eng.preset_set_date(text))
        except ValueError as exc:
            QMessageBox.warning(self, self.tr("Date invalide"), str(exc))

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
            QMessageBox.warning(self, self.tr("ExifTool introuvable"), self.tr("Impossible d'appliquer sans ExifTool."))
            return
        if not self._pending:
            QMessageBox.information(self, self.tr("Rien à appliquer"), self.tr("Aucune modification en attente."))
            return
        targets = [self._files[i].path for i in self._files_table.selected_rows() if 0 <= i < len(self._files)]
        if not targets:
            QMessageBox.information(self, self.tr("Aucun fichier"), self.tr("Sélectionnez au moins un fichier dans la liste."))
            return
        problems = eng.validate_changes(self._pending)
        if problems:
            plain_message(self, QMessageBox.Icon.Warning, self.tr("Modifications refusées"),
                          "\n".join(problems))
            return
        refused = [p for p in targets if eng.write_refusal(p)]
        if len(refused) == len(targets):
            plain_message(self, QMessageBox.Icon.Warning, self.tr("Format non modifiable"),
                          eng.write_refusal(refused[0]) or "")
            return
        msg = f"{len(self._pending)} modification(s) vont être écrites dans {len(targets)} fichier(s)."
        if any(c.op == "delete" and c.tag.lower() == "all" for c in self._pending):
            msg += "\n\nAttention : TOUTES les métadonnées modifiables seront supprimées."
        if refused:
            msg += f"\n\n{len(refused)} fichier(s) de format non modifiable ici seront ignorés."
        msg += "\n\nL'état précédent est enregistré pour permettre l'annulation. Continuer ?"
        if QMessageBox.question(self, self.tr("Confirmer l'application"), msg, _Yes | _No, _No) != _Yes:
            return
        worker = WriteWorker(exiftool, targets, list(self._pending), self._align.isChecked())
        self.run_worker(worker, on_completed=self._on_write_done,
                        on_cancelled=lambda: self._on_write_done(worker.result),
                        lock_widgets=self._lockables, history_action="Modification des métadonnées",
                        min_progress_ms=self.APPLY_MIN_PROGRESS_MS)

    def _on_write_done(self, result: object) -> None:
        res = result if isinstance(result, dict) else {}
        written = res.get("written") or []
        total = int(res.get("total") or 0)
        partial = bool(res.get("cancelled")) and 0 < len(written) < total
        if written and not partial:
            self._pending = []
            self._refresh_pending()
        elif written:
            self.log_panel.append_log(
                "WARN", f"Lot annulé : {len(written)} / {total} fichier(s) modifié(s). "
                        "Les modifications en attente sont conservées pour les fichiers restants.")
        if written:
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
            QMessageBox.warning(self, self.tr("ExifTool introuvable"), self.tr("Impossible d'annuler sans ExifTool."))
            return
        storage = get_storage()
        key = eng.latest_undo_key(storage.list_keys(eng.UNDO_COLLECTION))
        rec = storage.load(eng.UNDO_COLLECTION, key) if key else None
        if not key or not rec:
            QMessageBox.information(self, self.tr("Rien à annuler"), self.tr("Aucune application à annuler."))
            self._refresh_undo_button()
            return
        n = len(rec.get("files", []))
        quand = str(rec.get("timestamp", "")).replace("T", " ")
        if QMessageBox.question(
                self, self.tr("Annuler la dernière application"),
                self.tr("Rétablir l'état d'avant l'application du {date} sur {count} fichier(s) ?")
                .format(date=quand, count=n), _Yes | _No, _No) != _Yes:
            return
        self.run_worker(UndoWorker(exiftool, key), on_completed=self._on_undo_done,
                        lock_widgets=self._lockables, history_action="Annulation des métadonnées")

    def _on_undo_done(self, result: object) -> None:
        res = result if isinstance(result, dict) else {}
        restored = res.get("restored") or []
        if restored:
            self._reload(restored)
        QTimer.singleShot(0, self._refresh_undo_button)
