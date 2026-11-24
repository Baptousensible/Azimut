"""Fenêtre d'Azimut Scope : compare deux dossiers (arborescence et nombre de fichiers), en lecture seule."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget, QWidgetAction)

from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.modules.scope import MODULE, engine as eng
from azimut.modules.scope.treeview import ComparisonTree
from azimut.modules.scope.workers import CompareWorker
from azimut.ui import icons
from azimut.ui.file_dialog import choose_directory, choose_files, choose_save_file
from azimut.ui.controls import CheckBox, CopyFeedbackButton
from azimut.ui.module_window import ModuleWindow
from azimut.ui.theme import scaled
from azimut.ui.widgets import Card, Column, DataTable, PathDropZone, StatusChip, plain_message

_log = get_logger("scope.window")

_CTX = "ScopeWindow"

COLUMNS = [
    Column("folder", QCoreApplication.translate(_CTX, "Dossier"), width=380, stretch=True),
    Column("a", QCoreApplication.translate(_CTX, "Mère"), width=90, align="right"),
    Column("b", QCoreApplication.translate(_CTX, "Fille"), width=90, align="right"),
    Column("delta", QCoreApplication.translate(_CTX, "Écart"), width=80, align="right"),
    Column("state", QCoreApplication.translate(_CTX, "État"), width=160),
]

TYPE_CHOICES: tuple[tuple[str, str], ...] = (
    (eng.FILE_KIND_IMAGES, QCoreApplication.translate(_CTX, "Images")),
    (eng.FILE_KIND_VIDEOS, QCoreApplication.translate(_CTX, "Vidéos")),
    (eng.FILE_KIND_AUDIO, QCoreApplication.translate(_CTX, "Audio")),
    (eng.FILE_KIND_DOCUMENTS, QCoreApplication.translate(_CTX, "Documents et code")),
    (eng.FILE_KIND_OTHER, QCoreApplication.translate(_CTX, "Autres fichiers")),
)
TYPE_LABELS = dict(TYPE_CHOICES)
STATE_TEXT = {
    eng.STATE_EQUAL: QCoreApplication.translate("ScopeWindow", "Identique"),
    eng.STATE_DIFF: QCoreApplication.translate("ScopeWindow", "Différent"),
    eng.STATE_ONLY_A: QCoreApplication.translate("ScopeWindow", "Absent de la fille"),
    eng.STATE_ONLY_B: QCoreApplication.translate("ScopeWindow", "Absent de la mère"),
}


def _signed(value: int) -> str:
    """Écart signé avec le vrai signe moins typographique."""
    return ("+" if value > 0 else "−") + str(abs(value)) if value else "0"


class ScopeWindow(ModuleWindow):
    """Compare le nombre de fichiers de deux arborescences, dossier par dossier."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._cmp: eng.Comparison | None = None
        self._updating_types = False
        self.set_run_label(self.tr("Démarrer l'analyse"))
        self._build_ui()
        self.copy_button = self.add_action_button(
            self.tr("Copier"), self._copy, kind="secondary",
            tooltip=self.tr("Copie le rapport texte"), button_class=CopyFeedbackButton)
        self.export_button = self.add_action_button(self.tr("Exporter…"), self._export, kind="secondary",
                                                    tooltip=self.tr("Enregistre le rapport texte"))
        self.copy_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self._focus_search)
        self._clear_search_shortcut = QShortcut(QKeySequence("Esc"), self.search)
        self._clear_search_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._clear_search_shortcut.activated.connect(self._clear_search)
        self.resize(1100, 760)

    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        card = Card(self.tr("Dossiers à comparer"))
        self.field_a = PathDropZone(self.tr("Dossier mère (référence) :"))
        self.field_b = PathDropZone(self.tr("Dossier fille (copie) :"))
        fields = QHBoxLayout()
        fields.setSpacing(10)
        fields.addWidget(self.field_a, 1)
        self.btn_swap = QPushButton()
        self.btn_swap.setObjectName("SwapButton")
        self.btn_swap.setProperty("kind", "secondary")
        self.btn_swap.setIcon(icons.icon("swap-horizontal", "text", scaled(18)))
        self.btn_swap.setIconSize(QSize(scaled(18), scaled(18)))
        self.btn_swap.setFixedSize(scaled(38), scaled(38))
        self.btn_swap.setToolTip(self.tr("Échange la mère et la fille"))
        self.btn_swap.setAccessibleName(self.tr("Inverser la mère et la fille"))
        self.btn_swap.clicked.connect(self._swap)
        fields.addWidget(self.btn_swap, 0, Qt.AlignmentFlag.AlignVCenter)
        fields.addWidget(self.field_b, 1)
        card.body_layout.addLayout(fields)
        lay.addWidget(card)
        lay.addWidget(self.action_bar)

        bar = QHBoxLayout()
        self.chk_diff = CheckBox(self.tr("Afficher seulement les différences"))
        self.chk_diff.setChecked(bool(self.prefs.get("only_differences", False)))
        self.chk_diff.toggled.connect(self._on_diff_toggled)
        bar.addWidget(self.chk_diff)
        bar.addSpacing(6)
        self._build_type_menu()
        bar.addWidget(self.type_button)
        bar.addSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Rechercher un dossier…"))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(self.tr("Rechercher un dossier"))
        self.search.setToolTip(self.tr("Filtre les dossiers par nom, sans tenir compte des accents ni de la casse.\n"
                                       "Ctrl+F pour y accéder, Échap pour effacer."))
        self.search.setMinimumWidth(scaled(200))
        self.search.setMaximumWidth(320)
        self.search.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.search.setEnabled(False)
        bar.addWidget(self.search)
        self.lbl_search = QLabel()
        self.lbl_search.setProperty("role", "muted")
        self.lbl_search.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.lbl_search.setToolTip(self.tr("Dossiers dont le nom correspond à la recherche."))
        self.lbl_search.hide()
        bar.addWidget(self.lbl_search)
        bar.addStretch(1)
        self.lbl_gap = StatusChip("", "neutre")
        self.lbl_gap.setToolTip(self.tr("Écart global calculé comme fille − mère.\nUn nombre négatif signale des "
                                        "éléments manquants dans la fille."))
        self.lbl_gap.hide()
        bar.addWidget(self.lbl_gap)
        bar.addStretch(1)
        self.btn_view = QPushButton(self.tr("Vue liste"))
        self.btn_view.setToolTip(self.tr("Bascule entre l'arborescence et la liste détaillée des dossiers"))
        self.btn_view.setProperty("kind", "secondary")
        bar.addWidget(self.btn_view)
        self.btn_view.clicked.connect(self._toggle_view)
        lay.addLayout(bar)

        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.hide()
        lay.addWidget(self.lbl_summary)
        self.tree = ComparisonTree()
        self.search.textChanged.connect(self._on_search_changed)
        self.table = DataTable(COLUMNS)
        self.table.set_empty_text(self.tr("Aucun écart affiché : lancez la comparaison ou ajustez les filtres."))
        self.views = QStackedWidget()
        self.views.addWidget(self.tree)               # 0 : mère à gauche, fille à droite, lignes parallèles
        self.views.addWidget(self.table)              # 1 : liste dossier par dossier
        # La zone des résultats garde une hauteur exploitable même dans une fenêtre
        # basse : le défilement se fait alors sur l'onglet entier, pas en écrasant l'arbre.
        self.views.setMinimumHeight(scaled(300))
        lay.addWidget(self.views, 1)

        # L'onglet entier défile : les dossiers, les actions, les filtres et l'arbre
        # restent accessibles sans réduire la zone « Dossier » à quelques pixels.
        self.scroll = QScrollArea()
        self.scroll.setObjectName("ScopeScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(central)
        self.scroll.viewport().setAutoFillBackground(False)
        self.set_content(self.scroll)
        self.field_a.pathChanged.connect(lambda _path: self._refresh_run_state())
        self.field_b.pathChanged.connect(lambda _path: self._refresh_run_state())
        self._refresh_run_state()
        self._lockables = [self.field_a, self.field_b, self.chk_diff, self.type_button, self.type_menu,
                          self.btn_swap, self.search]

    # ── Types de fichiers ────────────────────────────────────────────────────
    def _build_type_menu(self) -> None:
        """Menu de cases à cocher : le filtre peut combiner plusieurs familles."""
        self.type_menu = QMenu(self)
        self.type_checks: dict[str, CheckBox] = {}
        selected = set(self._initial_type_kinds())
        for kind, label in TYPE_CHOICES:
            check = CheckBox(label)
            check.setChecked(kind in selected)
            check.toggled.connect(partial(self._on_type_toggled, kind))
            holder = QWidget(self.type_menu)
            row = QHBoxLayout(holder)
            row.setContentsMargins(12, 4, 12, 4)
            row.addWidget(check)
            action = QWidgetAction(self.type_menu)
            action.setDefaultWidget(holder)
            self.type_menu.addAction(action)
            self.type_checks[kind] = check

        self.type_button = QPushButton()
        self.type_button.setProperty("kind", "secondary")
        self.type_button.setMenu(self.type_menu)
        self.type_button.setMinimumWidth(scaled(170))
        self.type_button.setAccessibleName(self.tr("Types de fichiers comparés"))
        self.type_button.setToolTip(self.tr("Choisit les familles de fichiers comptées dans chaque dossier.\n"
                                            "Toutes les cases sont cochées par défaut ; les fichiers cachés et "
                                            "système sont toujours ignorés."))
        self._refresh_type_button()

    def _all_type_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind, _label in TYPE_CHOICES)

    def _initial_type_kinds(self) -> tuple[str, ...]:
        """Reprend le réglage multiple ; migre l'ancien choix unique au premier passage."""
        saved = self.prefs.get("file_kinds", None)
        if isinstance(saved, (list, tuple)):
            chosen = {str(kind) for kind in saved}
            selected = tuple(kind for kind, _label in TYPE_CHOICES if kind in chosen)
            return selected or self._all_type_kinds()        # tout est coché par défaut

        legacy = str(self.prefs.get("file_kind", eng.FILE_KIND_ALL))
        if legacy == eng.FILE_KIND_MEDIA:
            return (eng.FILE_KIND_IMAGES, eng.FILE_KIND_VIDEOS, eng.FILE_KIND_AUDIO)
        if legacy == eng.FILE_KIND_DEFAULT:
            return (eng.FILE_KIND_IMAGES, eng.FILE_KIND_VIDEOS, eng.FILE_KIND_AUDIO,
                    eng.FILE_KIND_OTHER)
        if legacy in TYPE_LABELS:
            return (legacy,)
        return self._all_type_kinds()

    def _selected_type_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind, _label in TYPE_CHOICES if self.type_checks[kind].isChecked())

    def _type_summary(self) -> str:
        selected = self._selected_type_kinds()
        if len(selected) == len(TYPE_CHOICES):
            return self.tr("Tous les types")
        if not selected:
            return self.tr("Aucun type")
        if len(selected) == 1:
            return TYPE_LABELS[selected[0]]
        return self.tr("{label} + {count}").format(label=TYPE_LABELS[selected[0]], count=len(selected) - 1)

    def _refresh_type_button(self) -> None:
        self.type_button.setText(f"{self._type_summary()}  ▾")

    def _on_type_toggled(self, kind: str, checked: bool) -> None:
        if self._updating_types:
            return
        # Une comparaison sans aucune famille ne dit rien : on conserve la dernière case.
        if not checked and not self._selected_type_kinds():
            self._updating_types = True
            self.type_checks[kind].setChecked(True)
            self._updating_types = False
            return
        self.prefs.set("file_kinds", list(self._selected_type_kinds()))
        self._refresh_type_button()
        self._refresh_run_state()
        if (self._cmp is not None and not self.is_busy()
                and self.field_a.path().strip() and self.field_b.path().strip()):
            self.on_run()

    # ── Dossiers ────────────────────────────────────────────────────────────
    def open_paths(self, paths: list[str]) -> None:
        cleaned = [eng.clean_path(p) for p in paths if p]
        dirs = list(dict.fromkeys(
            str(Path(p) if Path(p).is_dir() else Path(p).parent) for p in cleaned))
        if dirs:
            self.field_a.set_path(dirs[0])
        if len(dirs) > 1:
            self.field_b.set_path(dirs[1])

    def on_open(self) -> None:
        start = str(self.prefs.get("last_dir", "") or "")
        files = choose_files(self, self.tr("Importer des fichiers"), start)
        if files:
            self.prefs.set("last_dir", str(Path(files[0]).parent))
            self.open_paths(files)

    def on_open_folder(self) -> None:
        start = str(self.prefs.get("last_dir", "") or "")
        folder = choose_directory(self, self.tr("Importer un dossier"), start)
        if not folder:
            return
        self.prefs.set("last_dir", folder)
        (self.field_a if not self.field_a.path().strip() else self.field_b).set_path(folder)

    def _swap(self) -> None:
        if self.is_busy():
            return
        self.field_a.animate_swap(self.field_b)

    def _refresh_run_state(self) -> None:
        """« Démarrer l'analyse » exige deux dossiers distincts et au moins un type."""
        a = eng.clean_path(self.field_a.path())
        b = eng.clean_path(self.field_b.path())
        if not a or not b:
            self.set_run_enabled(False)
            return
        try:
            same = Path(a).resolve() == Path(b).resolve()
        except (ValueError, OSError):
            same = False
        self.set_run_enabled(not same and Path(a).is_dir() and Path(b).is_dir()
                             and bool(self._selected_type_kinds()))

    # ── Analyse ─────────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        a, b = eng.clean_path(self.field_a.path()), eng.clean_path(self.field_b.path())
        if not a or not b:
            QMessageBox.warning(self, self.tr("Champs vides"), self.tr("Renseignez les deux dossiers."))
            return
        try:
            if Path(a).resolve() == Path(b).resolve():
                QMessageBox.warning(self, self.tr("Même dossier"), self.tr("Les deux chemins sont identiques."))
                return
        except (ValueError, OSError) as exc:
            _log.debug("Comparaison de chemins impossible : %s", exc)
        file_kinds = self._selected_type_kinds()
        if not file_kinds:
            return
        self.copy_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.run_worker(CompareWorker(a, b, file_kinds=file_kinds), on_completed=self._on_done,
                        lock_widgets=self._lockables,
                        on_failed=self._on_failed,
                        import_progress=[self.field_a.import_progress, self.field_b.import_progress],
                        history_action="Comparaison de dossiers")

    def _on_failed(self, message: str) -> None:
        self.log_panel.append_log("ERR", f"Échec de l'analyse : {message}")
        has_report = self._cmp is not None
        self.copy_button.setEnabled(has_report)
        self.export_button.setEnabled(has_report)

    def _on_done(self, result: object) -> None:
        if isinstance(result, eng.Comparison):
            if self.search.text():
                self.search.clear()               # nouvelle comparaison : repartir d'une recherche vierge
            self._cmp = result
            self._render()
            self._fit_results_height()
            QTimer.singleShot(0, self._reveal_results)

    def _fit_results_height(self) -> None:
        """Donne aux résultats la hauteur de leur contenu, sans dépasser la fenêtre.

        Peu de dossiers : la zone garde sa taille naturelle et aucun vide n'est créé.
        Beaucoup de dossiers : elle s'étire jusqu'à remplir le viewport, ce qui permet
        au filtre de venir en tête au moment du recalage.
        """
        if self._cmp is None:
            return
        if self.views.currentIndex() == 0:
            rows = self.tree.row_count()
            row_h = self.tree.view.sizeHintForRow(0) or scaled(30)
            chrome = (self.tree.sources.sizeHint().height()
                      + self.tree.stats.sizeHint().height()
                      + self.tree.view.header().sizeHint().height() + scaled(32))
            desired = chrome + rows * row_h
        else:
            rows = self.table.data_model.rowCount()
            desired = scaled(80) + rows * scaled(30)
        if rows <= 0:
            self.views.setMinimumHeight(scaled(300))
            return
        content = self.scroll.widget()
        viewport = self.scroll.viewport().height()
        if content is not None and viewport > 0:
            filter_y = self.chk_diff.mapTo(content, QPoint(0, 0)).y()
            views_y = self.views.mapTo(content, QPoint(0, 0)).y()
            cap = max(scaled(300), viewport - (views_y - filter_y) + scaled(8))
        else:
            cap = scaled(520)
        self.views.setMinimumHeight(max(scaled(300), min(cap, desired)))

    def _reveal_results(self) -> None:
        """Après l'analyse, place la barre de filtres en haut du défilement.

        Le déplacement s'arrête de lui-même si le contenu est plus court que la fenêtre :
        le filtre reste alors aussi haut que possible, sans créer de vide artificiel.
        """
        if self._cmp is None or self.scroll is None:
            return
        rows = (self.tree.row_count() if self.views.currentIndex() == 0
                else self.table.data_model.rowCount())
        if rows <= 0:
            return
        content = self.scroll.widget()
        if content is None:
            return
        target = self.chk_diff.mapTo(content, QPoint(0, 0)).y() - scaled(8)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target, bar.maximum())))

    def _toggle_view(self) -> None:
        showing_tree = self.views.currentIndex() == 0
        self.views.setCurrentIndex(1 if showing_tree else 0)
        self.btn_view.setText(self.tr("Vue arbre") if showing_tree else self.tr("Vue liste"))
        self.lbl_summary.setVisible(self._cmp is not None and not showing_tree)
        self.search.setVisible(not showing_tree)
        self._update_search_label()

    def _on_diff_toggled(self, value: bool) -> None:
        self.prefs.set("only_differences", bool(value))
        self._render()

    def _on_search_changed(self, text: str) -> None:
        self.tree.set_search(text)
        self._update_search_label()

    def _update_search_label(self) -> None:
        query = self.search.text().strip()
        if not query or self._cmp is None or self.views.currentIndex() != 0:
            self.lbl_search.hide()
            return
        count = self.tree.search_result_count()
        if count:
            template = self.tr("{count} dossier") if count <= 1 else self.tr("{count} dossiers")
            self.lbl_search.setText(template.format(count=count))
            self.lbl_search.setToolTip(self.tr("Dossiers dont le nom correspond à la recherche."))
            self.lbl_search.show()
        elif self.tree.search_no_match():
            self.lbl_search.setText(self.tr("Aucun résultat"))
            self.lbl_search.setToolTip(self.tr("Aucun dossier ne correspond à la recherche dans les résultats affichés."))
            self.lbl_search.show()
        else:
            self.lbl_search.hide()

    def _focus_search(self) -> None:
        """Ctrl+F : affiche la vue arbre si besoin, puis sélectionne la recherche."""
        if not self.search.isEnabled():
            return
        if self.views.currentIndex() != 0:
            self._toggle_view()
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def _clear_search(self) -> None:
        if self.search.text():
            self.search.clear()
        if self.tree.stack.currentWidget() is self.tree.view:
            self.tree.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _render(self) -> None:
        cmp = self._cmp
        if cmp is None:
            return
        self.search.setEnabled(True)
        rows = []
        for r in cmp.rows:
            if self.chk_diff.isChecked() and r.state == eng.STATE_EQUAL:
                continue
            rows.append({"folder": eng.tree_label(r.folder), "a": "—" if r.count_a is None else r.count_a,
                         "b": "—" if r.count_b is None else r.count_b,
                         "delta": f"{r.delta:+d}" if r.state != eng.STATE_EQUAL else "0",
                         "_sort_delta": r.delta if r.state != eng.STATE_EQUAL else 0,
                         "state": STATE_TEXT.get(r.state, r.state), "_tag": eng.STATE_TAGS[r.state]})
        self.table.data_model.set_rows(rows)
        self.tree.set_comparison(cmp, self.chk_diff.isChecked())
        self.lbl_summary.setText(self.tr("MÈRE : {a_folders} sous-dossiers · {a_files} fichiers     "
                                         "FILLE : {b_folders} sous-dossiers · {b_files} fichiers").format(
            a_folders=cmp.a.folders, a_files=cmp.a.files, b_folders=cmp.b.folders, b_files=cmp.b.files))
        self.lbl_summary.setVisible(self.views.currentIndex() == 1)
        has_missing = any(r.state in (eng.STATE_ONLY_A, eng.STATE_ONLY_B) for r in cmp.rows)
        level = "ok" if cmp.identical else ("err" if has_missing else "warn")
        dossiers = self.tr("dossier") if abs(cmp.folder_delta) <= 1 else self.tr("dossiers")
        fichiers = self.tr("fichier") if abs(cmp.file_delta) <= 1 else self.tr("fichiers")
        self.lbl_gap.set_state(self.tr("Écart : 0 — identiques") if cmp.identical else
                               self.tr("Écart : {folders} {dossiers} · {files} {fichiers}").format(
                                   folders=_signed(cmp.folder_delta), dossiers=dossiers,
                                   files=_signed(cmp.file_delta), fichiers=fichiers), level)
        self.lbl_gap.show()
        self.copy_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self._update_search_label()

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
        path = choose_save_file(self, self.tr("Exporter les résultats"), "comparaison_dossiers.txt",
                                self.tr("Texte (*.txt)"))
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            plain_message(self, QMessageBox.Icon.Critical, self.tr("Erreur"), str(exc))
            return
        self.log_panel.append_log("OK", f"Rapport exporté : {path}")
