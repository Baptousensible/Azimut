"""Fenêtre d'Azimut Dedup : dossiers A/B, options, arbre de groupes, comparaison, application."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from azimut.core.fsutil import format_size
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.modules.dedup.actions import (
    MODE_DELETE,
    MODE_QUARANTINE,
    MODE_TRASH,
    ApplyOptions,
    ApplyReport,
    default_quarantine_parent,
    export_report,
    plan_deletions,
    trash_available,
)
from azimut.modules.dedup.engine import ScanOptions, ScanResult
from azimut.modules.dedup.model import (
    ACTION_DELETE,
    ACTION_KEEP,
    KIND_EXACT,
    KIND_SIMILAR,
    STRATEGY_LABELS,
    FileItem,
    Group,
    Strategy,
    keep_only,
    resolve_selection,
    set_actions,
    strategy_from_key,
    totals,
)
from azimut.modules.dedup.workers import ApplyWorker, ScanWorker, UndoWorker
from azimut.ui.module_window import ModuleWindow
from azimut.ui.theme import ThemeManager, token
from azimut.ui.widgets import Card, ImagePreview, PathField

ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ("Chemin", "Côté", "Taille", "Date", "Dimensions", "Action")
TOLERANCES = (("Strict (1)", 1), ("Équilibré (3)", 3), ("Large (5)", 5), ("Très large (7)", 7))
FILTERS = ("Tous", "Doublons exacts", "Images similaires", "Proposés à la suppression")
MODE_LABELS = {
    MODE_QUARANTINE: "Quarantaine (réversible, par défaut)",
    MODE_TRASH: "Corbeille système",
    MODE_DELETE: "Suppression définitive",
}
HINT_SINGLE = ("Un seul dossier : la protection de A est décochée par défaut ; "
               "les doublons de A pourront être proposés à la suppression.")
HINT_DOUBLE = ("A est la référence et reste protégé par défaut : seuls les fichiers de B "
               "sont proposés à la suppression. Décochez « Protégé » pour changer cela.")


class _ResultsTree(QTreeView):
    """Arbre des groupes ; Espace et Suppr sont relayés par des signaux."""

    toggleRequested = Signal()
    deleteRequested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.toggleRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _cell(text: str = "") -> QStandardItem:
    item = QStandardItem(text)
    item.setEditable(False)
    return item


def _same_dir(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class DedupWindow(ModuleWindow):
    """Fenêtre du module Dedup."""

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._groups: list[Group] = []
        self._result: ScanResult | None = None
        self._group_rows: dict[Group, QStandardItem] = {}
        self._item_rows: dict[FileItem, list[QStandardItem]] = {}
        self._item_group: dict[FileItem, Group] = {}
        self._dd_worker = None
        self._last_journal: Path | None = None
        self._protect_a_touched = False
        self._quarantine_touched = False
        self._programmatic = False

        self._build_ui()
        self._load_prefs()
        self._update_hint()
        self._update_stats()

        self.set_run_label("Analyser")
        self.btn_apply = self.add_toolbar_button(
            "Valider et appliquer…", self._on_apply, kind="success",
            tooltip="Retire les fichiers marqués « Supprimer » selon le mode choisi")
        self.btn_undo = self.add_toolbar_button(
            "Annuler une application…", self._on_undo, kind="secondary",
            tooltip="Remet en place les fichiers d'une quarantaine à partir de son journal.csv")
        self.btn_export = self.add_toolbar_button(
            "Exporter le rapport…", self._on_export, kind="secondary",
            tooltip="Enregistre le rapport complet des groupes en CSV")
        self.show_binary_status(("ffmpeg",))
        ThemeManager.instance().changed.connect(lambda _name: self._refresh_colors())
        self.resize(1250, 800)

    # ── Construction de l'interface ───────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Dossiers
        folders = Card("Dossiers")
        self.field_a = PathField("Dossier A (référence) :", mode="dir")
        self.field_b = PathField("Dossier B (à comparer, facultatif) :", mode="dir")
        self.chk_protect_a = QCheckBox("Protégé")
        self.chk_protect_b = QCheckBox("Protégé")
        for field, chk in ((self.field_a, self.chk_protect_a), (self.field_b, self.chk_protect_b)):
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(chk)
            folders.body_layout.addLayout(row)
        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        folders.body_layout.addWidget(self.lbl_hint)
        self.field_a.pathChanged.connect(self._on_a_changed)
        self.field_b.pathChanged.connect(lambda _p: self._update_protection())
        self.chk_protect_a.clicked.connect(self._on_protect_a_clicked)
        self.chk_protect_a.toggled.connect(lambda _c: self._update_hint())
        self.chk_protect_b.toggled.connect(lambda _c: self._update_hint())
        layout.addWidget(folders)

        # Options
        options = Card("Options")
        row1 = QHBoxLayout()
        self.chk_recursive = QCheckBox("Inclure les sous-dossiers")
        self.chk_recursive.setChecked(True)
        self.chk_media = QCheckBox("Photos et vidéos uniquement")
        self.chk_hidden = QCheckBox("Ignorer les fichiers cachés")
        self.chk_hidden.setChecked(True)
        for w in (self.chk_recursive, self.chk_media, self.chk_hidden):
            row1.addWidget(w)
        row1.addStretch(1)
        options.body_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.chk_similar = QCheckBox("Détecter les images similaires")
        self.cmb_tolerance = QComboBox()
        for label, value in TOLERANCES:
            self.cmb_tolerance.addItem(label, value)
        self.cmb_tolerance.setCurrentIndex(1)
        self.cmb_tolerance.setEnabled(False)
        self.chk_similar.toggled.connect(self.cmb_tolerance.setEnabled)
        row2.addWidget(self.chk_similar)
        row2.addWidget(QLabel("Tolérance :"))
        row2.addWidget(self.cmb_tolerance)
        row2.addSpacing(16)
        row2.addWidget(QLabel("Proposition automatique :"))
        self.cmb_strategy = QComboBox()
        for strat, label in STRATEGY_LABELS.items():
            self.cmb_strategy.addItem(label, strat.value)
        row2.addWidget(self.cmb_strategy)
        row2.addStretch(1)
        options.body_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Mode de suppression :"))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem(MODE_LABELS[MODE_QUARANTINE], MODE_QUARANTINE)
        if trash_available():
            self.cmb_mode.addItem(MODE_LABELS[MODE_TRASH], MODE_TRASH)
        self.cmb_mode.addItem(MODE_LABELS[MODE_DELETE], MODE_DELETE)
        self.cmb_mode.currentIndexChanged.connect(lambda _i: self._update_mode_widgets())
        row3.addWidget(self.cmb_mode)
        self.chk_empty_dirs = QCheckBox("Supprimer les dossiers vides")
        row3.addWidget(self.chk_empty_dirs)
        row3.addStretch(1)
        options.body_layout.addLayout(row3)

        self.field_quarantine = PathField("Dossier de quarantaine :", mode="dir")
        self.field_quarantine.pathChanged.connect(self._on_quarantine_changed)
        options.body_layout.addWidget(self.field_quarantine)
        layout.addWidget(options)

        # Résultats
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Afficher :"))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(FILTERS)
        self.cmb_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        bar.addWidget(self.cmb_filter)
        bar.addStretch(1)
        self.lbl_stats = QLabel()
        bar.addWidget(self.lbl_stats)
        layout.addLayout(bar)

        self._tree_model = QStandardItemModel(self)
        self._tree_model.setHorizontalHeaderLabels(list(COLUMNS))
        self.tree = _ResultsTree()
        self.tree.setModel(self._tree_model)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        for col, width in enumerate((520, 70, 90, 130, 110, 90)):
            self.tree.setColumnWidth(col, width)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._on_selection_changed())
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.toggleRequested.connect(self._toggle_selection)
        self.tree.deleteRequested.connect(lambda: self._set_action(ACTION_DELETE))
        layout.addWidget(self.tree, 1)
        self.set_content(root)

        self._build_side_panel()
        self._update_mode_widgets()

    def _build_side_panel(self) -> None:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(8, 8, 8, 8)

        previews = QHBoxLayout()
        self.preview_a = ImagePreview(size=230)
        self.preview_b = ImagePreview(size=230)
        self.caption_a = QLabel()
        self.caption_b = QLabel()
        self.box_a, self.box_b = QWidget(), QWidget()
        for box, preview, caption in ((self.box_a, self.preview_a, self.caption_a),
                                      (self.box_b, self.preview_b, self.caption_b)):
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            caption.setWordWrap(True)
            caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(preview)
            lay.addWidget(caption)
            lay.addStretch(1)
            previews.addWidget(box)
        self.box_b.setVisible(False)
        col.addLayout(previews)

        title = QLabel("Corrections manuelles")
        title.setStyleSheet("font-weight: bold;")
        col.addWidget(title)
        for text, callback, tip in (
            ("Garder", lambda: self._set_action(ACTION_KEEP), "Garder la sélection"),
            ("Supprimer", lambda: self._set_action(ACTION_DELETE), "Proposer la sélection à la suppression (Suppr)"),
            ("Garder uniquement celui-ci", self._keep_only_selected, "Garder ce fichier, proposer les autres du groupe"),
            ("Proposition auto pour la sélection", self._auto_selection, "Appliquer la stratégie choisie à la sélection"),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(callback)
            col.addWidget(btn)
        hint = QLabel("Espace : bascule Garder / Supprimer · Suppr : Supprimer · Double-clic : ouvrir le fichier")
        hint.setWordWrap(True)
        col.addWidget(hint)
        col.addStretch(1)
        self.set_side_panel(panel, "Aperçu et corrections")

    # ── Préférences ───────────────────────────────────────────────────

    def _load_prefs(self) -> None:
        p = self.prefs
        self.chk_recursive.setChecked(bool(p.get("recursive", True)))
        self.chk_media.setChecked(bool(p.get("media_only", False)))
        self.chk_hidden.setChecked(bool(p.get("skip_hidden", True)))
        self.chk_similar.setChecked(bool(p.get("similar", False)))
        self.chk_empty_dirs.setChecked(bool(p.get("remove_empty", False)))
        idx = self.cmb_tolerance.findData(p.get("tolerance", 3))
        if idx >= 0:
            self.cmb_tolerance.setCurrentIndex(idx)
        idx = self.cmb_strategy.findData(p.get("strategy", Strategy.A_FIRST.value))
        if idx >= 0:
            self.cmb_strategy.setCurrentIndex(idx)
        idx = self.cmb_mode.findData(p.get("mode", MODE_QUARANTINE))
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)

    def _save_prefs(self) -> None:
        p = self.prefs
        p.set("recursive", self.chk_recursive.isChecked())
        p.set("media_only", self.chk_media.isChecked())
        p.set("skip_hidden", self.chk_hidden.isChecked())
        p.set("similar", self.chk_similar.isChecked())
        p.set("tolerance", self.cmb_tolerance.currentData())
        p.set("strategy", self.cmb_strategy.currentData())
        p.set("mode", self.cmb_mode.currentData())
        p.set("remove_empty", self.chk_empty_dirs.isChecked())

    # ── Dossiers et protection ────────────────────────────────────────

    def open_paths(self, paths: list[str]) -> None:
        """Le premier chemin devient A, le second B (un fichier est remplacé par son dossier)."""
        dirs = []
        for raw in paths[:2]:
            p = Path(raw)
            dirs.append(str(p if p.is_dir() else p.parent))
        if dirs:
            self.field_a.set_path(dirs[0])
        if len(dirs) > 1:
            self.field_b.set_path(dirs[1])

    def on_open(self) -> None:
        start = str(Preferences.instance().get("last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Choisir un dossier", start)
        if not folder:
            return
        Preferences.instance().set("last_dir", folder)
        (self.field_b if self.field_a.path().strip() else self.field_a).set_path(folder)

    def _on_a_changed(self, path: str) -> None:
        if not self._quarantine_touched and path.strip():
            self._programmatic = True
            self.field_quarantine.set_path(str(default_quarantine_parent(Path(path.strip()))))
            self._programmatic = False

    def _on_quarantine_changed(self, _path: str) -> None:
        if not self._programmatic:
            self._quarantine_touched = True

    def _on_protect_a_clicked(self) -> None:
        self._protect_a_touched = True

    def _update_protection(self) -> None:
        """Avec deux dossiers, A (référence) est protégé par défaut ; avec un seul, il ne l'est pas."""
        if not self._protect_a_touched:
            self.chk_protect_a.setChecked(bool(self.field_b.path().strip()))
        self._update_hint()

    def _update_hint(self) -> None:
        two = bool(self.field_b.path().strip())
        self.lbl_hint.setText(HINT_DOUBLE if two else HINT_SINGLE)

    def _update_mode_widgets(self) -> None:
        self.field_quarantine.setVisible(self.cmb_mode.currentData() == MODE_QUARANTINE)

    def _lockable(self) -> list[QWidget]:
        return [self.field_a, self.field_b, self.chk_protect_a, self.chk_protect_b, self.chk_recursive,
                self.chk_media, self.chk_hidden, self.chk_similar, self.cmb_tolerance, self.cmb_strategy,
                self.cmb_mode, self.chk_empty_dirs, self.field_quarantine]

    def _say(self, level: str, text: str) -> None:
        self.log_panel.append_log(level, text)

    # ── Analyse ───────────────────────────────────────────────────────

    def _collect_options(self) -> ScanOptions | None:
        a_txt, b_txt = self.field_a.path().strip(), self.field_b.path().strip()
        if not a_txt:
            self._say("ERR", "Choisissez le dossier A.")
            return None
        folder_a = Path(a_txt)
        if not folder_a.is_dir():
            self._say("ERR", f"Dossier A introuvable : {folder_a}")
            return None
        folder_b: Path | None = None
        if b_txt:
            folder_b = Path(b_txt)
            if not folder_b.is_dir():
                self._say("ERR", f"Dossier B introuvable : {folder_b}")
                return None
            if _same_dir(folder_a, folder_b):
                self._say("WARN", "A et B sont le même dossier : recherche dans A seulement.")
                folder_b = None
        protect_a = self.chk_protect_a.isChecked()
        protect_b = self.chk_protect_b.isChecked() and folder_b is not None
        if protect_a and folder_b is None:
            self._say("WARN", "A est protégé et B est vide : aucun fichier ne sera proposé à la suppression.")
        elif protect_a and protect_b:
            self._say("WARN", "A et B sont protégés : aucun fichier ne sera proposé à la suppression.")
        return ScanOptions(
            folder_a=folder_a, folder_b=folder_b, protect_a=protect_a, protect_b=protect_b,
            recursive=self.chk_recursive.isChecked(), media_only=self.chk_media.isChecked(),
            skip_hidden=self.chk_hidden.isChecked(), similar=self.chk_similar.isChecked(),
            tolerance=int(self.cmb_tolerance.currentData() or 3),
            strategy=strategy_from_key(self.cmb_strategy.currentData()),
        )

    def on_run(self) -> None:
        """Bouton « Analyser »."""
        if self.is_busy():
            return
        options = self._collect_options()
        if options is None:
            return
        self._save_prefs()
        self._say("INFO", "Analyse en cours…")
        worker = ScanWorker(options, self)
        self._dd_worker = worker
        self.run_worker(worker, on_completed=self._on_scan_done,
                        on_failed=lambda msg: self._say("ERR", f"Échec de l'analyse : {msg}"),
                        on_cancelled=lambda: self._say("WARN", "Analyse annulée."),
                        lock_widgets=self._lockable(), history_action="Analyse")

    def _on_scan_done(self, result: object) -> None:
        if not isinstance(result, ScanResult):
            return
        self._result = result
        self._groups = result.groups
        self._populate()
        if not self._groups:
            self._say("OK", "Aucun doublon trouvé.")

    # ── Arbre des résultats ───────────────────────────────────────────

    def _populate(self) -> None:
        self.tree.setUpdatesEnabled(False)
        self._tree_model.clear()
        self._tree_model.setHorizontalHeaderLabels(list(COLUMNS))
        self._group_rows.clear()
        self._item_rows.clear()
        self._item_group.clear()
        for g in self._groups:
            head = _cell()
            head.setData(g, ROLE)
            self._tree_model.appendRow([head] + [_cell() for _ in range(len(COLUMNS) - 1)])
            self._group_rows[g] = head
            for it in g.items:
                cells = [_cell(str(it.path)), _cell(), _cell(format_size(it.size)),
                         _cell(datetime.fromtimestamp(it.mtime).strftime("%Y-%m-%d %H:%M")),
                         _cell(it.dimensions_text), _cell()]
                cells[0].setData(it, ROLE)
                head.appendRow(cells)
                self._item_rows[it] = cells
                self._item_group[it] = g
            self._refresh_group(g)
        if len(self._groups) <= 500:
            self.tree.expandAll()
        self.tree.setUpdatesEnabled(True)
        self._apply_filter()
        self._update_stats()
        self._on_selection_changed()

    def _refresh_group(self, g: Group) -> None:
        head = self._group_rows.get(g)
        if head is None:
            return
        kind = "doublons exacts" if g.kind == KIND_EXACT else "images similaires"
        head.setText(f"Groupe {g.id} · {kind} · {len(g.items)} fichiers · gain : {format_size(g.reclaimable)}")
        ok_brush = QBrush(QColor(token("ok_bg")))
        err_brush = QBrush(QColor(token("err_bg")))
        for it in g.items:
            cells = self._item_rows.get(it)
            if not cells:
                continue
            cells[1].setText(it.side + (" · protégé" if it.protected else ""))
            cells[5].setText(it.action)
            brush = ok_brush if it.action == ACTION_KEEP else err_brush
            for cell in cells:
                cell.setBackground(brush)

    def _refresh_colors(self) -> None:
        for g in self._groups:
            self._refresh_group(g)

    def _update_stats(self) -> None:
        files, count, gain = totals(self._groups)
        if self._result is None:
            self.lbl_stats.setText("Aucune analyse effectuée.")
            return
        self.lbl_stats.setText(f"{self._result.stats.scanned} fichier(s) analysé(s) · {count} groupe(s) · "
                               f"{files} fichier(s) concerné(s) · gain libérable : {format_size(gain)}")

    def _apply_filter(self) -> None:
        mode = self.cmb_filter.currentIndex()
        for row, g in enumerate(self._groups):
            if mode == 1:
                hidden = g.kind != KIND_EXACT
            elif mode == 2:
                hidden = g.kind != KIND_SIMILAR
            elif mode == 3:
                hidden = not g.to_delete()
            else:
                hidden = False
            self.tree.setRowHidden(row, QModelIndex(), hidden)

    # ── Sélection, aperçu, comparaison ────────────────────────────────

    def _selected_objects(self) -> list[object]:
        return [idx.data(ROLE) for idx in self.tree.selectionModel().selectedRows(0)]

    def _selected_items(self) -> list[FileItem]:
        """Fichiers sélectionnés ; une ligne de groupe compte pour tous ses fichiers."""
        out: list[FileItem] = []
        for obj in self._selected_objects():
            members = obj.items if isinstance(obj, Group) else [obj] if isinstance(obj, FileItem) else []
            for it in members:
                if it not in out:
                    out.append(it)
        return out

    def _by_group(self, items: list[FileItem]) -> dict[Group, list[FileItem]]:
        grouped: dict[Group, list[FileItem]] = {}
        for it in items:
            g = self._item_group.get(it)
            if g is not None:
                grouped.setdefault(g, []).append(it)
        return grouped

    def _describe(self, it: FileItem) -> str:
        g = self._item_group.get(it)
        lines = [it.name, str(it.path), f"Côté {it.side}" + (" (protégé)" if it.protected else ""),
                 f"Taille : {format_size(it.size)}",
                 f"Modifié : {datetime.fromtimestamp(it.mtime).strftime('%Y-%m-%d %H:%M:%S')}"]
        if it.dimensions_text:
            lines.append(f"Dimensions : {it.dimensions_text}")
        if it.digest:
            lines.append(f"Empreinte : {it.digest[:16]}…")
        lines.append(f"Action : {it.action}")
        if g is not None:
            lines.append(f"Groupe {g.id} ({g.kind})")
        return "\n".join(lines)

    def _on_selection_changed(self) -> None:
        objs = self._selected_objects()
        files = [o for o in objs if isinstance(o, FileItem)]
        groups = [o for o in objs if isinstance(o, Group)]
        self.box_b.setVisible(False)
        if len(files) == 1 and not groups:
            self.preview_a.show_image(files[0].path)
            self.caption_a.setText(self._describe(files[0]))
        elif len(files) == 2 and not groups and self._item_group.get(files[0]) is self._item_group.get(files[1]):
            self.box_b.setVisible(True)
            self.preview_a.show_image(files[0].path)
            self.preview_b.show_image(files[1].path)
            self.caption_a.setText(self._describe(files[0]))
            self.caption_b.setText(self._describe(files[1]))
        elif len(groups) == 1 and not files:
            g = groups[0]
            self.preview_a.show_text(f"Groupe {g.id}")
            self.caption_a.setText(f"{len(g.items)} fichiers · {g.kind}\n"
                                   f"Taille totale : {format_size(g.total_size)}\n"
                                   f"Gain libérable : {format_size(g.reclaimable)}")
        elif objs:
            self.preview_a.show_text("Sélectionnez un fichier, ou deux fichiers du même groupe pour les comparer.")
            self.caption_a.setText(f"{len(self._selected_items())} fichier(s) sélectionné(s)")
        else:
            self.preview_a.clear()
            self.caption_a.setText("")

    def _on_double_click(self, index: QModelIndex) -> None:
        obj = index.siblingAtColumn(0).data(ROLE)
        if isinstance(obj, FileItem):
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(obj.path))):
                self._say("WARN", f"Impossible d'ouvrir : {obj.path}")
        elif isinstance(obj, Group):
            self.tree.setExpanded(index.siblingAtColumn(0), not self.tree.isExpanded(index.siblingAtColumn(0)))

    # ── Corrections manuelles ─────────────────────────────────────────

    def _after_change(self, groups: set[Group] | list[Group]) -> None:
        for g in groups:
            self._refresh_group(g)
        self._apply_filter()
        self._update_stats()
        self._on_selection_changed()

    def _set_action(self, action: str) -> None:
        if self.is_busy() or not self._groups:
            return
        changed: list[Group] = []
        for g, items in self._by_group(self._selected_items()).items():
            ok, message = set_actions(g, items, action)
            if ok:
                changed.append(g)
            else:
                self._say("WARN", message)
        self._after_change(changed)

    def _toggle_selection(self) -> None:
        if self.is_busy() or not self._groups:
            return
        changed: list[Group] = []
        for g, items in self._by_group(self._selected_items()).items():
            target = ACTION_DELETE if items[0].action == ACTION_KEEP else ACTION_KEEP
            ok, message = set_actions(g, items, target)
            if ok:
                changed.append(g)
            else:
                self._say("WARN", message)
        self._after_change(changed)

    def _keep_only_selected(self) -> None:
        if self.is_busy():
            return
        items = [o for o in self._selected_objects() if isinstance(o, FileItem)]
        if len(items) != 1:
            self._say("WARN", "Sélectionnez un seul fichier pour « Garder uniquement celui-ci ».")
            return
        g = self._item_group[items[0]]
        ok, message = keep_only(g, items[0])
        self._say("INFO" if ok else "WARN", message)
        self._after_change([g] if ok else [])

    def _auto_selection(self) -> None:
        if self.is_busy() or not self._groups:
            return
        strategy = strategy_from_key(self.cmb_strategy.currentData())
        changed = []
        for g, items in self._by_group(self._selected_items()).items():
            resolve_selection(g, items, strategy)
            changed.append(g)
        self._after_change(changed)

    # ── Application ───────────────────────────────────────────────────

    def _ask(self, title: str, text: str, danger: bool = False) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_apply(self) -> None:
        """Bouton « Valider et appliquer… »."""
        if self.is_busy():
            return
        plan = plan_deletions(self._groups)
        if not plan:
            self._say("INFO", "Aucun fichier proposé à la suppression.")
            return
        count, size = len(plan), sum(it.size for _, it in plan)
        mode = self.cmb_mode.currentData()
        quarantine_parent: Path | None = None
        if mode == MODE_QUARANTINE:
            text = self.field_quarantine.path().strip()
            if not text and self._result is not None and self._result.options is not None:
                text = str(default_quarantine_parent(self._result.options.folder_a))
            if not text:
                self._say("ERR", "Choisissez le dossier de quarantaine.")
                return
            quarantine_parent = Path(text)
            question = (f"{count} fichier(s) ({format_size(size)}) vont être déplacés dans un dossier "
                        f"« Quarantaine_… » créé dans :\n{quarantine_parent}\n\n"
                        "L'opération est réversible grâce au journal.csv.")
        elif mode == MODE_TRASH:
            question = f"{count} fichier(s) ({format_size(size)}) vont être envoyés à la corbeille du système."
        else:
            question = (f"{count} fichier(s) ({format_size(size)}) vont être supprimés DÉFINITIVEMENT.\n"
                        "Cette opération est irréversible.")
        if not self._ask("Valider et appliquer", question + "\n\nContinuer ?", danger=mode == MODE_DELETE):
            return
        if mode == MODE_DELETE and not self._ask(
                "Dernière confirmation",
                f"Supprimer définitivement {count} fichier(s) sans passer par la corbeille ?", danger=True):
            return
        self._save_prefs()
        options = ApplyOptions(mode=mode, quarantine_parent=quarantine_parent,
                               remove_empty_dirs=self.chk_empty_dirs.isChecked())
        worker = ApplyWorker(self._groups, options, self)
        self._dd_worker = worker
        self.run_worker(worker, on_completed=lambda r: self._after_apply(r if isinstance(r, ApplyReport) else None),
                        on_failed=lambda msg: self._say("ERR", f"Échec de l'application : {msg}"),
                        on_cancelled=lambda: self._after_apply(worker.report),
                        lock_widgets=self._lockable(), history_action="Application")

    def _after_apply(self, report: ApplyReport | None) -> None:
        if report is None:
            return
        done = set(report.done)
        if done:
            for g in self._groups:
                g.items = [i for i in g.items if i not in done]
            self._groups = [g for g in self._groups if len(g.items) >= 2]
            self._populate()
        if report.journal is not None:
            self._last_journal = report.journal
            self._say("OK", f"Journal de quarantaine : {report.journal}")
        if report.removed_dirs:
            self._say("INFO", f"{report.removed_dirs} dossier(s) vide(s) supprimé(s).")
        self._say("INFO", f"Libéré : {format_size(report.freed)}. Relancez une analyse pour actualiser les résultats.")

    # ── Annulation d'une application ──────────────────────────────────

    def _on_undo(self) -> None:
        """Bouton « Annuler une application… »."""
        if self.is_busy():
            return
        start = str(self._last_journal.parent if self._last_journal else
                    Preferences.instance().get("last_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(self, "Choisir le journal d'une quarantaine", start,
                                              "Journal (journal.csv);;Fichiers CSV (*.csv)")
        if not path:
            return
        worker = UndoWorker(Path(path), self)
        self._dd_worker = worker
        self.run_worker(worker, on_completed=lambda _r: self._say(
                            "INFO", "Restauration terminée. Relancez une analyse pour actualiser les résultats."),
                        on_failed=lambda msg: self._say("ERR", f"Échec de la restauration : {msg}"),
                        on_cancelled=lambda: self._say("WARN", "Restauration interrompue."),
                        lock_widgets=self._lockable(), history_action="Annulation")

    # ── Export ────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        """Bouton « Exporter le rapport… »."""
        if self.is_busy():
            return
        if not self._groups:
            self._say("INFO", "Aucun résultat à exporter : lancez d'abord une analyse.")
            return
        start = str(Path(str(Preferences.instance().get("last_dir", "") or "")) / "rapport_doublons.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Exporter le rapport", start, "Fichiers CSV (*.csv)")
        if not path:
            return
        try:
            rows = export_report(self._groups, Path(path))
        except OSError as exc:
            self._say("ERR", f"Export impossible : {exc}")
            return
        self._say("OK", f"Rapport exporté ({rows} ligne(s)) : {path}")
