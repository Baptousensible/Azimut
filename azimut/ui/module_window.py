"""Fenêtre de base commune à tous les modules : en-tête, barre d'outils, journal, historique, progression."""
from __future__ import annotations

import html
import os
import sys
import time
from typing import Callable, Iterable

from PySide6.QtCore import (
    QCoreApplication, QEasingCurve, QEvent, QMimeData, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QGuiApplication, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from azimut import APP_NAME, __version__
from azimut.core import binaries
from azimut.core.binaries import BinaryInfo
from azimut.core.history import HistoryEntry, HistoryStore
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import ModulePrefs, Preferences
from azimut.core.screenfit import fit_to_screen
from azimut.core.workers import BaseWorker
from azimut.ui import icons
from azimut.ui.flow_layout import FlowLayout
from azimut.ui.glass import AuroraWindowMixin, GlassDialog, GlassFrame, LiquidButton
from azimut.ui.notification import notify_window
from azimut.ui.preferences_dialog import PreferencesDialog, source_labels
from azimut.ui.theme import ThemeManager, repolish, scaled
from azimut.ui.widgets import Column, DataTable, ImportProgress, LiquidProgressBar, LogPanel, StatusChip, plain_tooltip

log = get_logger(__name__)

BUTTON_KINDS: tuple[str, ...] = ("primary", "success", "danger", "accent", "secondary", "ghost")
_CLOSE_WAIT_MS = 3000               # attente d'un worker avant de reporter la fermeture de la fenêtre


def shortcuts() -> tuple[tuple[str, str], ...]:
    """Raccourcis clavier de l'ossature et leur description, traduits."""
    return (
        ("Ctrl+Return", QCoreApplication.translate("ModuleWindow", "Démarrer le traitement")),
        ("Esc", QCoreApplication.translate("ModuleWindow", "Annuler le traitement en cours")),
        ("Ctrl+L", QCoreApplication.translate("ModuleWindow", "Afficher / masquer le journal")),
        ("Ctrl+T", QCoreApplication.translate("ModuleWindow", "Basculer entre thème sombre et clair")),
        ("Ctrl+,", QCoreApplication.translate("ModuleWindow", "Préférences…")),
        ("F1", QCoreApplication.translate("ModuleWindow", "Aide")),
    )


def history_columns() -> list[Column]:
    """Colonnes de l'historique d'un module, traduites."""
    return [
        Column("time", QCoreApplication.translate("ModuleWindow", "Date"), width=150),
        Column("action", QCoreApplication.translate("ModuleWindow", "Action"), width=130),
        Column("summary", QCoreApplication.translate("ModuleWindow", "Résumé"), stretch=True),
        Column("ok", QCoreApplication.translate("ModuleWindow", "Réussis"), width=70, align="right"),
        Column("errors", QCoreApplication.translate("ModuleWindow", "Erreurs"), width=70, align="right"),
    ]


def _import_progresses(target: ImportProgress | Iterable[ImportProgress] | None) -> tuple[ImportProgress, ...]:
    """Normalise une cible d'animation unique ou multiple (Dedup et Scope ont deux zones)."""
    if target is None:
        return ()
    if isinstance(target, ImportProgress):
        return (target,)
    return tuple(target)


def _mime_paths(mime: QMimeData | None) -> list[str]:
    """Chemins locaux absolus d'un QMimeData (sans doublon)."""
    if mime is None or not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if url.isLocalFile():
            p = os.path.abspath(url.toLocalFile())
            if p not in out:
                out.append(p)
    return out


class _FitWidthBody(QWidget):
    """Contenu de module qui accepte la largeur réellement disponible.

    Un tableau Qt annonce une largeur minimale fondée sur ses colonnes. Dans la zone
    défilante du module, cette largeur créait une barre horizontale et rognait la fin
    des colonnes à 125 % ou 150 %. Le tableau sait déjà resserrer ses colonnes quand
    il est plus étroit (voir ``DataTable._fit_columns``) : on ne transmet donc que la
    hauteur minimale, jamais une largeur qui dépasserait le panneau.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = super().sizeHint()
        return QSize(1, hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = super().minimumSizeHint()
        return QSize(1, hint.height())


class ModuleWindow(AuroraWindowMixin, QMainWindow):
    """Classe mère de la fenêtre de chaque module (mêmes barre d'outils, raccourcis et organisation)."""

    # Largeur commune des panneaux de réglages à droite (Dedup, Convert, Tags, Rename) :
    # sans elle, chaque panneau prenait la largeur de son contenu. Un contenu plus large
    # (les deux aperçus de comparaison de Dedup) peut encore étendre le panneau.
    SIDE_PANEL_WIDTH = 380

    spec: ModuleSpec
    prefs: ModulePrefs
    log_panel: LogPanel
    progress_bar: LiquidProgressBar
    busyChanged = Signal(bool)          # True pendant un traitement, False à sa fin

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.prefs = Preferences.instance().module(spec.id)

        self._worker: BaseWorker | None = None
        self._last_worker: BaseWorker | None = None      # garde la dernière référence (évite une destruction prématurée)
        self._connections: list[tuple[object, Callable]] = []
        self._callbacks: dict[str, Callable | None] = {}
        self._locked: list[tuple[QWidget, bool]] = []
        self._history_action = ""
        self._outcome: str | None = None
        self._result: object = None
        self._failure = ""
        self._cancel_requested = False
        self._close_pending = False
        self._min_progress_ms = 0
        self._determinate_start = False
        self._progress_started_at = 0.0
        self._progress_hold_pending = False
        self._progress_hold_timer = QTimer(self)
        self._progress_hold_timer.setSingleShot(True)
        self._progress_hold_timer.timeout.connect(self._on_worker_finished)
        self._progress_fill_anim = QVariantAnimation(self)
        self._progress_fill_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._progress_fill_anim.valueChanged.connect(self._on_progress_frame)
        self._run_enabled = True
        self._binary_names: tuple[str, ...] = ()
        self.binary_chips: list[StatusChip] = []
        self.side_dock: QDockWidget | None = None
        self._content_widget: QWidget | None = None
        self._embedded = False
        self._global_shortcuts: dict[QPushButton, QKeySequence] = {}

        self.setWindowTitle(spec.name)
        if icons.available(spec.icon):
            self.setWindowIcon(icons.tile_icon(spec.icon))
        screen = self.screen() or QGuiApplication.primaryScreen()       # AZ-C03 : jamais plus grande que l'écran
        area = screen.availableGeometry() if screen is not None else None
        available = (area.width(), area.height()) if area is not None else None
        self._min_base = (960, 640)
        self._resize_base = (1180, 780)
        self._apply_window_limits()
        self.resize(*fit_to_screen(*self._resize_base, available))
        self.setAcceptDrops(True)

        self._build_central()
        self._build_action_bar()
        self._build_toolbar()
        self._build_menu()
        self._build_bottom()

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        ThemeManager.instance().accessibility_changed.connect(self._on_accessibility_changed)
        self._on_theme_changed(ThemeManager.instance().theme())
        self.show_binary_status((*spec.requires, *spec.optional))
        self.refresh_history()
        self._update_buttons()

    # ═══════════════════════════════════════════════════════════════════════
    # Construction
    # ═══════════════════════════════════════════════════════════════════════
    def _build_central(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 12, 24, 12)
        outer.setSpacing(12)
        self._outer_layout = outer

        self.header = GlassFrame(radius=8)
        self.header.setObjectName("Card")
        hl = QVBoxLayout(self.header)
        hl.setContentsMargins(16, 12, 16, 12)
        hl.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.header_icon = QLabel()
        self.header_icon.setFixedSize(24, 24)
        self.header_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # L'icône de l'outil reste fixe dans son onglet ouvert : pas d'animation au survol.
        self._refresh_header_icon()
        top.addWidget(self.header_icon)
        # Taille par la feuille de style (QLabel#ModuleName), jamais « x-large » : une
        # taille CSS absolue ignore le zoom et la taille de police du système.
        self.title_label = QLabel(self.spec.name)
        self.title_label.setObjectName("ModuleName")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        top.addWidget(self.title_label)
        top.addStretch(1)
        self.chips_layout = QHBoxLayout()
        self.chips_layout.setSpacing(6)

        # Journal et Aide concernent l'outil : ils restent dans son en-tête, à droite.
        self.log_button = self._make_button(self.tr("Journal"), self._toggle_log, "ghost", "Ctrl+L",
                                            self.tr("Afficher ou masquer le journal"))
        # Le bouton reste enfoncé tant que le journal est affiché : l'état du panneau
        # se lit directement dans l'en-tête, sans avoir à regarder le bas de la fenêtre.
        self.log_button.setCheckable(True)
        self.help_button = self._make_button(self.tr("Aide"), self._show_help, "ghost", "F1",
                                             self.tr("Aide et raccourcis"))
        top.addWidget(self.log_button, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.help_button, 0, Qt.AlignmentFlag.AlignTop)
        hl.addLayout(top)
        # Les pastilles de binaires vivent sous le titre, avec la phrase d'accroche :
        # sur la ligne du titre, elles entraient en concurrence avec le nom et les actions
        # (et se faisaient rogner dès que la fenêtre était étroite ou le zoom élevé).
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self.tagline_label = QLabel(QCoreApplication.translate("Modules", self.spec.tagline))
        self.tagline_label.setProperty("role", "muted")
        self.tagline_label.setWordWrap(True)
        info_row.addWidget(self.tagline_label, 1)
        info_row.addLayout(self.chips_layout, 0)
        hl.addLayout(info_row)
        outer.addWidget(self.header)

        # L'en-tête (nom, Journal, Aide) reste fixe au-dessus : seule la zone qui le
        # suit défile. À 125 %, un module réclame près de 1 000 px de haut, plus que
        # les 793 px utiles d'un MacBook ; sans défilement, Qt agrandissait la fenêtre
        # (et l'onglet) au-delà du bas de l'écran en rognant la fin du contenu.
        self._scroll_body = _FitWidthBody()
        self._content_layout = QVBoxLayout(self._scroll_body)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self.central_scroll = QScrollArea()
        self.central_scroll.setWidgetResizable(True)
        self.central_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.central_scroll.setWidget(self._scroll_body)
        outer.addWidget(self.central_scroll, 1)
        self.setCentralWidget(central)

    def _refresh_header_icon(self) -> None:
        """Pictogramme Papicons à gauche du nom ; repli texte si l'icône est absente."""
        self.header_icon.setFixedSize(scaled(24), scaled(24))
        self.header_icon.clear()
        if icons.available(self.spec.icon):
            self.header_icon.setPixmap(icons.pixmap(self.spec.icon, scaled(20), "muted"))
            self.header_icon.setStyleSheet("background: transparent;")
        else:
            self.header_icon.setText(self.spec.icon)
            self.header_icon.setStyleSheet(
                f"font-size: {scaled(20)}px; background: transparent;")

    def _make_button(self, text: str, callback: Callable[[], None], kind: str = "secondary",
                     shortcut: str | None = None, tooltip: str = "",
                     button_class: type[QPushButton] = LiquidButton) -> QPushButton:
        btn = button_class(text)
        btn.setProperty("kind", kind if kind in BUTTON_KINDS else "secondary")
        tip = tooltip
        if shortcut:
            seq = QKeySequence(shortcut)
            btn.setShortcut(seq)
            shown = seq.toString(QKeySequence.SequenceFormat.NativeText)
            tip = f"{tooltip} ({shown})" if tooltip else f"({shown})"
        if tip:
            btn.setToolTip(tip)
        btn.clicked.connect(lambda _checked=False: callback())
        return btn

    @staticmethod
    def _set_text_keep_shortcut(btn: QPushButton, text: str) -> None:
        """setText() remplace le raccourci par le mnémonique du texte (vide ici) : on le conserve."""
        seq = btn.shortcut()
        btn.setText(text)
        btn.setShortcut(seq)

    def _build_action_bar(self) -> None:
        """Barre des actions de traitement, placée par chaque module juste avant ses résultats."""
        self.action_bar = GlassFrame(radius=8, parent=self)
        self.action_bar.setObjectName("ActionBar")
        self.action_bar.setProperty("flat", True)
        lay = QVBoxLayout(self.action_bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        # Une barre seule, en bas : ni compteur ni nom de fichier au-dessus de la zone d'import.
        self.progress_bar = LiquidProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)

        # En flux : dans une fenêtre étroite, les boutons passent à la ligne au lieu
        # d'être comprimés sous la largeur de leur libellé.
        self.action_layout = FlowLayout(h_spacing=8, v_spacing=6)
        lay.addLayout(self.action_layout)

        self.run_button = self._make_button(self.tr("Démarrer"), lambda: self.on_run(), "primary",
                                            "Ctrl+Return", self.tr("Démarrer le traitement"))
        # « Annuler » n'est pas destructeur : le rouge reste réservé aux actions irréversibles.
        self.cancel_button = self._make_button(self.tr("Annuler"), self.cancel_current, "secondary", "Esc",
                                               self.tr("Annuler le traitement en cours"))
        self.action_layout.addWidget(self.run_button)
        self.action_layout.addWidget(self.cancel_button)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=lambda: self.run_button.click())

    def _build_toolbar(self) -> None:
        tb = QToolBar(self.tr("Principale"), self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.toolbar = tb
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self.prefs_button = self._make_button(self.tr("Préférences…"), self._open_preferences, "secondary", "Ctrl+,",
                                              self.tr("Langue, thème, taille du texte, outils externes"))
        self._prefs_action = tb.addWidget(self.prefs_button)
        self._refresh_button_icons()

    def _build_menu(self) -> None:
        """Barre de menus desktop : toutes les commandes y sont aussi accessibles."""
        bar = self.menuBar()

        app_menu = bar.addMenu(self.tr("Azimut"))
        about = app_menu.addAction(self.tr("À propos d'Azimut"))
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self._show_about)
        prefs = app_menu.addAction(self.tr("Préférences…"))
        prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs.triggered.connect(self._open_preferences)
        app_menu.addSeparator()
        quit_action = app_menu.addAction(self.tr("Quitter"))
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(lambda: QApplication.quit())

        file_menu = bar.addMenu(self.tr("Fichier"))
        add_files = file_menu.addAction(self.tr("Ajouter des fichiers…"))
        add_files.triggered.connect(self.on_open)
        add_folder = file_menu.addAction(self.tr("Ajouter un dossier…"))
        add_folder.triggered.connect(self.on_open_folder)
        file_menu.addSeparator()
        close_action = file_menu.addAction(self.tr("Fermer"))
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)

        edit_menu = bar.addMenu(self.tr("Édition"))
        self._undo_action = edit_menu.addAction(self.tr("Annuler le dernier renommage"))
        self._undo_action.triggered.connect(self.undo_last)
        edit_menu.aboutToShow.connect(self._refresh_undo_action)
        self._refresh_undo_action()
        edit_menu.addSeparator()
        for text, standard, method in (
            (self.tr("Annuler"), QKeySequence.StandardKey.Undo, "undo"),
            (self.tr("Rétablir"), QKeySequence.StandardKey.Redo, "redo"),
            (self.tr("Couper"), QKeySequence.StandardKey.Cut, "cut"),
            (self.tr("Copier"), QKeySequence.StandardKey.Copy, "copy"),
            (self.tr("Coller"), QKeySequence.StandardKey.Paste, "paste"),
            (self.tr("Tout sélectionner"), QKeySequence.StandardKey.SelectAll, "selectAll"),
        ):
            action = edit_menu.addAction(text)
            action.setShortcut(standard)
            action.triggered.connect(lambda _checked=False, m=method: self._invoke_focus(m))

        view_menu = bar.addMenu(self.tr("Présentation"))
        log_action = view_menu.addAction(self.tr("Afficher / masquer le journal"))
        log_action.triggered.connect(self._toggle_log)
        theme_action = view_menu.addAction(self.tr("Basculer entre thème sombre et clair"))
        theme_action.setShortcut(QKeySequence("Ctrl+T"))
        theme_action.triggered.connect(lambda: ThemeManager.instance().toggle())
        view_menu.addSeparator()
        zoom_in = view_menu.addAction(self.tr("Agrandir le texte"))
        zoom_in.setShortcut(QKeySequence("Ctrl++"))
        zoom_in.triggered.connect(lambda: ThemeManager.instance().set_zoom(ThemeManager.instance().zoom() + 0.1))
        zoom_out = view_menu.addAction(self.tr("Réduire le texte"))
        zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out.triggered.connect(lambda: ThemeManager.instance().set_zoom(ThemeManager.instance().zoom() - 0.1))

        task_menu = bar.addMenu(self.tr("Traitement"))
        self._run_action = task_menu.addAction(self.tr("Démarrer"))
        self._run_action.triggered.connect(self.run_button.click)
        self._cancel_action = task_menu.addAction(self.tr("Annuler"))
        self._cancel_action.triggered.connect(self.cancel_current)

        window_menu = bar.addMenu(self.tr("Fenêtre"))
        minimize = window_menu.addAction(self.tr("Réduire"))
        minimize.setShortcut(QKeySequence("Ctrl+M"))
        minimize.triggered.connect(self.showMinimized)
        zoom_window = window_menu.addAction(self.tr("Zoom"))
        zoom_window.triggered.connect(
            lambda: self.showNormal() if self.isMaximized() else self.showMaximized())

        help_menu = bar.addMenu(self.tr("Aide"))
        help_action = help_menu.addAction(self.tr("Raccourcis clavier"))
        help_action.triggered.connect(self._show_help)

    def _invoke_focus(self, method: str) -> None:
        """Applique une commande d'édition au widget qui a le focus."""
        widget = QApplication.focusWidget()
        action = getattr(widget, method, None)
        if callable(action):
            action()

    def undo_label(self) -> str | None:
        """Libellé de l'annulation métier du module, ou None s'il n'y en a pas."""
        return None

    def undo_last(self) -> None:
        """Annule la dernière opération métier du module (à surcharger)."""

    def _refresh_undo_action(self) -> None:
        label = self.undo_label()
        self._undo_action.setText(label or self.tr("Annuler le dernier renommage"))
        self._undo_action.setEnabled(bool(label))

    def confirm_clear_list(self) -> bool:
        """Demande confirmation avant de vider une liste de travail.

        Aucun fichier n'est modifié : seule la liste affichée est oubliée.
        """
        return QMessageBox.question(
            self, self.tr("Vider la liste"),
            self.tr("Voulez-vous vraiment vider la liste ?\n\nLes fichiers ne seront pas modifiés."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _show_about(self) -> None:
        QMessageBox.about(
            self, self.tr("À propos d'Azimut"),
            f"<b>{APP_NAME}</b> {__version__}<br>"
            f"{html.escape(QCoreApplication.translate('Modules', self.spec.tagline))}")

    def set_embedded(self, embedded: bool) -> None:
        """Masque les commandes globales quand le module vit dans un onglet Azimut.

        Le journal et l'aide restent dans l'en-tête du module : ils concernent son contenu
        et son fonctionnement propre. En lancement autonome, le module conserve « Thème »
        et « Préférences… » dans la barre d'outils ; dans Azimut, cette barre est masquée.
        """
        self._embedded = bool(embedded)
        self.menuBar().setVisible(not self._embedded)
        self.toolbar.setVisible(not self._embedded)
        self.setProperty("embedded", self._embedded)
        self.header.setProperty("embedded", self._embedded)
        self._outer_layout.setContentsMargins(16 if self._embedded else 24, 0 if self._embedded else 12, 16 if self._embedded else 24, 12)
        repolish(self)
        repolish(self.header)
        for button, action in ((self.prefs_button, self._prefs_action),):
            action.setVisible(not self._embedded)
            button.setVisible(not self._embedded)
            if self._embedded:
                if button not in self._global_shortcuts:
                    self._global_shortcuts[button] = button.shortcut()
                button.setShortcut(QKeySequence())
            else:
                shortcut = self._global_shortcuts.pop(button, None)
                if shortcut is not None:
                    button.setShortcut(shortcut)

    # (attribut du bouton, icône, couleur) : blanc sur les boutons pleins, couleur du texte sinon
    _BUTTON_ICONS = (
        ("run_button", "play", "#10171a"),
        ("cancel_button", "cross", "text"), ("log_button", "list", "text"),
        ("prefs_button", "gears", "text"), ("help_button", "info", "text"),
    )

    def _refresh_button_icons(self) -> None:
        """(Re)pose les icônes SVG des boutons de la barre d'outils ; à rappeler quand le thème change."""
        for attr, name, color in self._BUTTON_ICONS:
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setIcon(icons.icon(name, color, scaled(18)))

    def _build_bottom(self) -> None:
        self.log_panel = LogPanel()
        self.history_table = DataTable(history_columns())
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.log_panel, self.tr("Journal"))
        self.bottom_tabs.addTab(self.history_table, self.tr("Historique"))
        self.bottom_tabs.setMinimumHeight(scaled(150))
        clear_btn = self._make_button(self.tr("Effacer le journal"), self.log_panel.clear_log, "ghost")
        self.bottom_tabs.setCornerWidget(clear_btn, Qt.Corner.TopRightCorner)
        self.bottom_tabs.currentChanged.connect(self._on_tab_changed)

        dock = QDockWidget(self.tr("Journal"), self)
        dock.setObjectName("LogDock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        dock.setTitleBarWidget(QWidget())                   # pas de barre de titre : l'onglet suffit
        dock.setWidget(self.bottom_tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.hide()                                          # replié à l'ouverture ; Ctrl+L / « Journal » l'affiche
        self.log_dock = dock
        # Tout chemin qui affiche ou replie le panneau (bouton, Ctrl+L, menu) recale le bouton.
        dock.visibilityChanged.connect(lambda _visible: self._sync_log_button())

    # ═══════════════════════════════════════════════════════════════════════
    # Mise en page
    # ═══════════════════════════════════════════════════════════════════════
    def set_content(self, widget: QWidget) -> None:
        old = self._content_widget
        if old is widget:
            return
        if old is not None:
            self._content_layout.removeWidget(old)
            old.hide()
            old.setParent(None)
        self._content_widget = widget
        self._content_layout.addWidget(widget, 1)
        widget.show()

    def set_side_panel(self, widget: QWidget | None, title: str = "") -> None:
        if widget is None:
            if self.side_dock is not None:
                self.side_dock.hide()
            return
        if self.side_dock is None:
            dock = QDockWidget(self)
            dock.setObjectName("SideDock")
            dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)   # non flottant, non fermable
            dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            self.side_dock = dock
        dock = self.side_dock
        dock.setWindowTitle(title)
        dock.setTitleBarWidget(self._side_title_bar(title) if title else QWidget())
        if isinstance(widget, QScrollArea):
            # QScrollArea active l'auto-remplissage de son contenu : sans cela, le panneau
            # peint un grand rectangle opaque au lieu de laisser voir le verre de la
            # fenêtre, alors qu'un panneau simple (Takeout) reste transparent.
            inner = widget.widget()
            if inner is not None:
                inner.setAutoFillBackground(False)
        dock.setWidget(widget)
        # La largeur type ne suit pas la largeur préférée du contenu : on réévalue donc
        # quand la mise en page du panneau change (options ajoutées, aperçus Dedup…).
        for watched in (widget, widget.widget() if isinstance(widget, QScrollArea) else None):
            if watched is not None:
                watched.removeEventFilter(self)
                watched.installEventFilter(self)
        dock.show()
        widget.show()
        self._sync_side_panel_width()

    def _sync_side_panel_width(self) -> None:
        """Le panneau suit sa largeur type, agrandie seulement si son contenu l'exige.

        Sans cela, il prenait la largeur *préférée* de son contenu (une phrase longue,
        un libellé de liste) : à 85 %, « Réglages » débordait de sa largeur type. Un
        contenu qui ne peut pas tenir (les deux aperçus de Dedup) l'étend encore, et
        la largeur type, elle, suit le zoom comme toutes les autres mesures.
        """
        dock = self.side_dock
        if dock is None:
            return
        widget = dock.widget()
        inner = widget.widget() if isinstance(widget, QScrollArea) else widget
        # Un panneau latéral ne doit pas manger le contenu central à fort zoom :
        # 40 % de la fenêtre au maximum, la largeur type restant la référence.
        available = self.width()
        width = scaled(self.SIDE_PANEL_WIDTH)
        if available >= 1000:
            width = min(width, max(scaled(260), int(available * 0.40)))
        chrome = 0
        if isinstance(widget, QScrollArea):
            chrome = widget.verticalScrollBar().sizeHint().width() + 2
        if inner is not None:
            # Mesure à la largeur type : une phrase à retour à la ligne annonce le
            # minimum de la largeur qu'elle *occupe*, donc mesurée à l'ancienne largeur
            # (après un changement de zoom) elle empêcherait le panneau de rétrécir.
            probe = width - chrome
            if inner.width() != probe:
                inner.resize(probe, max(inner.height(), 1))
            layout = inner.layout()
            if layout is not None:
                layout.activate()
            width = max(width, inner.minimumSizeHint().width() + chrome)
        else:
            width += chrome
        # Minimum et maximum : la largeur ne dépend pas du contenu *préféré*.
        if dock.minimumWidth() != width:
            dock.setMinimumWidth(width)
        if dock.maximumWidth() != width:
            dock.setMaximumWidth(width)

    def _queue_side_panel_sync(self) -> None:
        """Regroupe les recalculs : une seule passe de largeur par boucle d'événements."""
        if getattr(self, "_side_width_pending", False):
            return
        self._side_width_pending = True

        def run() -> None:
            self._side_width_pending = False
            self._sync_side_panel_width()

        QTimer.singleShot(0, run)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802 - API Qt
        """Un panneau dont le contenu change (aperçus Dedup, options) se recale en largeur."""
        if self.side_dock is not None and event.type() in (
                QEvent.Type.LayoutRequest, QEvent.Type.FontChange):
            widget = self.side_dock.widget()
            inner = widget.widget() if isinstance(widget, QScrollArea) else widget
            if watched is widget or watched is inner:
                self._queue_side_panel_sync()
        return super().eventFilter(watched, event)

    @staticmethod
    def _side_title_bar(title: str) -> QWidget:
        """En-tête arrondi du panneau latéral, avec un titre typographié comme un vrai titre."""
        bar = QWidget()
        bar.setObjectName("SideDockTitleBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 0)
        lay.setSpacing(0)
        label = QLabel(title)
        label.setObjectName("SideDockTitle")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setToolTip(title)
        lay.addWidget(label, 1)
        return bar

    def add_action_button(self, text: str, callback: Callable[[], None], *, kind: str = "secondary",
                          shortcut: str | None = None, tooltip: str = "",
                          button_class: type[QPushButton] = LiquidButton) -> QPushButton:
        """Ajoute une action métier à gauche, avant la zone extensible de la barre d'actions."""
        btn = self._make_button(text, callback, kind, shortcut, tooltip, button_class)
        self.action_layout.addWidget(btn)
        return btn

    def set_run_label(self, text: str) -> None:
        self._set_text_keep_shortcut(self.run_button, text)

    def set_run_enabled(self, enabled: bool) -> None:
        self._run_enabled = bool(enabled)
        self._update_buttons()

    def show_binary_status(self, names: Iterable[str]) -> None:
        """Pastilles ✔ / ✖ dans l'en-tête ; version et origine en infobulle."""
        self._binary_names = tuple(names)
        for chip in self.binary_chips:
            self.chips_layout.removeWidget(chip)
            # Détacher avant la suppression différée : sinon l'ancienne pastille reste
            # peinte à sa géométrie par défaut (bloc coloré sur tout l'en-tête).
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()
        self.binary_chips = []
        for name in self._binary_names:
            try:
                info = binaries.resolve(name)
            except Exception as exc:                       # détection défaillante : traité comme manquant
                log.warning("Détection de %s impossible : %s", name, exc)
                info = BinaryInfo(name, None, "missing", "")
            if info.available:
                chip = StatusChip(f"✔ {name}", "ok")
                origin = source_labels().get(info.source, info.source)
                chip.setToolTip(plain_tooltip(f"{name} {info.version}".strip() + f"\n{origin} : {info.path}"))
            else:
                level = "err" if name in self.spec.requires else "warn"
                chip = StatusChip(f"✖ {name}", level)
                chip.setToolTip(self.tr("{name} est introuvable.\nInstallez-le ou indiquez son chemin dans "
                                        "les Préférences.").format(name=name))
            self.chips_layout.addWidget(chip)
            self.binary_chips.append(chip)

    # ═══════════════════════════════════════════════════════════════════════
    # Crochets à surcharger
    # ═══════════════════════════════════════════════════════════════════════
    def open_paths(self, paths: list[str]) -> None:
        """Chemins reçus par glisser-déposer, ligne de commande ou Azimut (à surcharger)."""

    def on_open(self) -> None:
        """Import de fichiers (crochet conservé pour les modules, à surcharger)."""

    def on_open_folder(self) -> None:
        """Import d'un dossier ; par défaut, même action que l'import de fichiers."""
        self.on_open()

    def on_run(self) -> None:
        """Bouton « Démarrer » (à surcharger)."""

    # ═══════════════════════════════════════════════════════════════════════
    # Exécution d'un traitement
    # ═══════════════════════════════════════════════════════════════════════
    def is_busy(self) -> bool:
        return self._worker is not None

    def run_worker(self, worker: BaseWorker, *,
                   on_completed: Callable[[object], None] | None = None,
                   on_failed: Callable[[str], None] | None = None,
                   on_cancelled: Callable[[], None] | None = None,
                   lock_widgets: Iterable[QWidget] = (),
                   import_progress: ImportProgress | Iterable[ImportProgress] | None = None,
                   show_progress: bool = True,
                   min_progress_ms: int = 0,
                   determinate_start: bool = False,
                   history_action: str = "") -> None:
        """Lance un worker ; ``show_progress=False`` réserve sa progression à l'import.

        Certains modules affichent déjà l'avancement dans leur zone de dépôt. Dans ce cas,
        la barre de progression de la barre d'actions reste silencieuse pour ne pas doubler
        l'indicateur. ``min_progress_ms`` garde la barre visible au moins ce temps, même
        quand le traitement est instantané, pour laisser voir son animation.
        """
        if self._worker is not None:
            self.log_panel.append_log("WARN", self.tr(
                "Un traitement est déjà en cours : nouvelle demande ignorée."))
            return
        self._worker = worker
        self._callbacks = {"completed": on_completed, "failed": on_failed, "cancelled": on_cancelled}
        self._history_action = history_action
        self._outcome, self._result, self._failure = None, None, ""
        self._cancel_requested = False
        self._min_progress_ms = max(0, int(min_progress_ms)) if show_progress else 0
        self._determinate_start = bool(determinate_start) and show_progress
        self._progress_started_at = time.monotonic()
        self._progress_hold_pending = False
        self._progress_hold_timer.stop()
        self._progress_fill_anim.stop()

        for progress in _import_progresses(import_progress):
            progress.begin_import()
            worker.progress.connect(progress.set_import_progress)
            worker.finished.connect(progress.end_import)
            self._connections.append((worker.progress, progress.set_import_progress))
            self._connections.append((worker.finished, progress.end_import))

        self._locked = []
        for w in lock_widgets:
            self._locked.append((w, w.isEnabled()))
            w.setEnabled(False)

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        if self._min_progress_ms > 0:
            # Bande liquide dès le départ : le traitement peut être trop rapide pour
            # émettre sa propre progression avant d'être terminé.
            self.progress_bar.setRange(0, 0)
            self.progress_bar.show()
        signals = [
            (worker.log, self.log_panel.append_log),
            (worker.completed, self._on_worker_completed),
            (worker.cancelled, self._on_worker_cancelled),
            (worker.failed, self._on_worker_failed),
            (worker.finished, self._on_worker_finished),
        ]
        if show_progress:
            signals.insert(0, (worker.progress, self._on_worker_progress))
        for signal, slot in signals:
            signal.connect(slot)
            self._connections.append((signal, slot))
        self._update_buttons()
        self.busyChanged.emit(True)
        worker.start()

    def cancel_current(self) -> None:
        worker = self._worker
        if worker is None or self._cancel_requested:
            return
        self._cancel_requested = True
        worker.cancel()
        self.log_panel.append_log("WARN", self.tr("Annulation demandée…"))
        self._update_buttons()

    def _on_worker_progress(self, done: int, total: int, _label: str = "") -> None:
        if total <= 0:
            if self._min_progress_ms > 0:
                self.progress_bar.setRange(0, 0)
                self.progress_bar.show()
            else:
                self.progress_bar.hide()
            return
        if self._min_progress_ms > 0:
            if done <= 0:
                self.progress_bar.setRange(0, 0)         # bande liquide avant le premier point précis
            else:
                # On garde les 10 derniers pour cent à l'animation de fin : un fichier
                # unique atteindrait sinon 100 % avant même que la barre soit visible.
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(max(0, min(90, round(done * 100 / total))))
            self.progress_bar.show()
            return
        if done <= 0 and not self._determinate_start:
            # Départ historique des autres modules : une bande liquide en mouvement
            # avant le premier point de progression précis.
            self.progress_bar.setRange(0, 0)
        else:
            # Convert demande un total connu dès le départ (0 % fixe et segments visibles).
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(max(0, min(done, total)))
        self.progress_bar.show()

    def _on_worker_completed(self, result: object) -> None:
        self._outcome, self._result = "completed", result
        summary = getattr(self._worker, "summary", "") if self._worker is not None else ""
        self.log_panel.append_log("OK", summary or self.tr("Traitement terminé."))

    def _on_worker_cancelled(self) -> None:
        self._outcome = "cancelled"
        summary = getattr(self._worker, "summary", "") if self._worker is not None else ""
        self.log_panel.append_log("WARN", self.tr("Traitement annulé.")
                                  + (f" {summary}" if summary else ""))

    def _on_worker_failed(self, message: str) -> None:
        self._outcome, self._failure = "failed", message
        self.log_panel.append_log("ERR", self.tr("Échec du traitement : {message}").format(message=message))

    def _on_progress_frame(self, value: object) -> None:
        """Image de l'animation de fin de progression (0 à 100 %)."""
        try:
            self.progress_bar.setValue(int(float(value)))
        except (TypeError, ValueError):
            log.debug("Progression ignorée : %r", value)

    def _begin_progress_hold(self, remaining_ms: int) -> None:
        """Termine le remplissage en douceur, puis garde la barre le temps restant."""
        self.progress_bar.show()
        if self._outcome == "completed":
            if self.progress_bar.maximum() == self.progress_bar.minimum():
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
            self.progress_bar.setRange(0, 100)
            current = max(0, min(100, self.progress_bar.value()))
            if ThemeManager.instance().reduce_motion():
                self.progress_bar.setValue(100)
            elif current < 100:
                fill_ms = min(remaining_ms, min(900, max(120, remaining_ms * 3 // 5)))
                self._progress_fill_anim.stop()
                self._progress_fill_anim.setDuration(fill_ms)
                self._progress_fill_anim.setStartValue(current)
                self._progress_fill_anim.setEndValue(100)
                self._progress_fill_anim.start()
        self._progress_hold_timer.start(remaining_ms)

    def _on_worker_finished(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if not self._progress_hold_pending:
            elapsed_ms = (time.monotonic() - self._progress_started_at) * 1000.0
            remaining_ms = int(self._min_progress_ms - elapsed_ms + 0.999)
            if remaining_ms > 0:
                # Le worker a fini, mais la barre reste affichée le temps promis :
                # la suite (historique, notification, relecture) attend la fin du spectacle.
                self._progress_hold_pending = True
                self._begin_progress_hold(remaining_ms)
                return
        self._progress_hold_pending = False
        self._progress_hold_timer.stop()
        self._progress_fill_anim.stop()
        self._min_progress_ms = 0
        summary = str(getattr(worker, "summary", "") or "").strip()
        worker.wait(2000)                                   # le fil est vraiment terminé avant de lâcher la référence
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError) as exc:
                log.debug("Déconnexion de signal ignorée : %s", exc)
        self._connections = []

        outcome, result, failure = self._outcome, self._result, self._failure
        callbacks, locked, action = self._callbacks, self._locked, self._history_action
        closing = self._close_pending
        self._worker, self._last_worker = None, worker
        self._callbacks, self._locked, self._history_action = {}, [], ""
        self._outcome, self._result, self._failure, self._cancel_requested = None, None, "", False
        self.busyChanged.emit(False)

        for widget, was_enabled in locked:
            if was_enabled:
                try:
                    widget.setEnabled(True)
                except RuntimeError as exc:                  # widget détruit entre-temps
                    log.debug("Réactivation de widget ignorée : %s", exc)
        self.progress_bar.hide()                     # pas de « Terminé / n sur n » qui traîne en bas
        self._update_buttons()
        if action:
            self._record_history(worker, action, outcome, failure)
        self.refresh_history()
        if self._close_pending:
            self._close_pending = False
            try:
                self.close()                                 # fermeture demandée pendant le traitement
            except RuntimeError as exc:                      # fenêtre déjà détruite (arrêt de l'application)
                log.debug("Fermeture différée ignorée : %s", exc)

        cb = callbacks.get(outcome or "")
        if cb is not None:
            try:
                if outcome == "completed":
                    cb(result)
                elif outcome == "failed":
                    cb(failure)
                else:
                    cb()
            except Exception as exc:                         # un rappel défaillant ne doit pas casser la fenêtre
                log.exception("Erreur dans le rappel de fin de traitement")
                self.log_panel.append_log("ERR", self.tr("Erreur interne : {error}").format(error=exc))

        # Une notification seulement pour un vrai travail (pas un simple import, qui n'a
        # pas d'action d'historique) et jamais pour une fenêtre qui se referme.
        if action and not closing and outcome in ("completed", "failed"):
            if outcome == "completed":
                notice = str(getattr(worker, "notification_summary", "") or summary).strip()
                notify_window(self, self.spec.name, notice or self.tr("Traitement terminé."),
                              kind="success")
            else:
                notify_window(self, self.spec.name,
                              self.tr("Échec : {reason}").format(reason=failure), kind="error")

    def _record_history(self, worker: BaseWorker, action: str, outcome: str | None, failure: str) -> None:
        summary = getattr(worker, "summary", "") or ""
        if not summary:
            summary = {"cancelled": self.tr("Traitement annulé."),
                       "failed": self.tr("Échec : {reason}").format(reason=failure)}.get(outcome or "", "")
        entry = HistoryEntry(module=self.spec.id, action=action, summary=summary,
                             ok=int(getattr(worker, "ok_count", 0) or 0),
                             errors=int(getattr(worker, "error_count", 0) or 0))
        try:
            HistoryStore().add(entry)
        except Exception as exc:
            log.warning("Historique non enregistré : %s", exc)

    # ═══════════════════════════════════════════════════════════════════════
    # Historique
    # ═══════════════════════════════════════════════════════════════════════
    def refresh_history(self) -> None:
        try:
            entries = HistoryStore().recent(limit=200, module=self.spec.id)
        except Exception as exc:
            log.warning("Historique illisible : %s", exc)
            entries = []
        rows = []
        for e in entries:
            tag = "err" if e.errors else ("ok" if e.ok else "neutre")
            rows.append({"time": e.timestamp.replace("T", " ")[:19], "action": e.action, "summary": e.summary,
                         "ok": e.ok, "errors": e.errors, "_tag": tag})
        self.history_table.data_model.set_rows(rows)

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self.refresh_history()

    # ═══════════════════════════════════════════════════════════════════════
    # Actions de la barre d'outils
    # ═══════════════════════════════════════════════════════════════════════
    def _update_buttons(self) -> None:
        busy = self.is_busy()
        can_run, can_cancel = self._run_enabled and not busy, busy and not self._cancel_requested
        self.run_button.setEnabled(can_run)
        self.cancel_button.setEnabled(can_cancel)
        if hasattr(self, "_run_action"):
            self._run_action.setEnabled(can_run)
        if hasattr(self, "_cancel_action"):
            self._cancel_action.setEnabled(can_cancel)

    def _toggle_log(self) -> None:
        self.log_dock.setVisible(self.log_dock.isHidden())
        self._sync_log_button()

    def _sync_log_button(self) -> None:
        """Le bouton « Journal » est coché exactement quand le panneau est visible."""
        self.log_button.setChecked(not self.log_dock.isHidden())

    def _on_theme_changed(self, _theme: str) -> None:
        if hasattr(self, "run_button"):                    # barre d'outils déjà construite
            self._refresh_button_icons()
        if hasattr(self, "header_icon"):
            self._refresh_header_icon()
        # La nouvelle police n'est pas encore propagée aux enfants : on mesure
        # le panneau après la passe de style, sinon sa largeur minimale reste celle
        # de l'ancien zoom et ses libellés sont coupés.
        QTimer.singleShot(0, self._sync_side_panel_width)
        self._apply_window_limits()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - API Qt
        """Recalcule la largeur du panneau après un changement de police ou de style."""
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            QTimer.singleShot(0, self._sync_side_panel_width)

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Recale le panneau quand le module devient visible dans son onglet.

        Un module caché au moment du changement de zoom garde sinon la largeur
        calculée pour l'ancienne police.
        """
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_side_panel_width)

    def event(self, event: QEvent) -> bool:  # noqa: N802 - API Qt
        """Le zoom suit l'écran : Qt prévient à l'affichage et à chaque changement d'écran."""
        if event.type() in (QEvent.Type.Show, QEvent.Type.ScreenChangeInternal):
            self._follow_screen(self.screen())
        return super().event(event)

    def _follow_screen(self, screen) -> None:
        """Reprend le zoom mémorisé par l'écran où la fenêtre se trouve."""
        if screen is not None:
            ThemeManager.instance().activate_screen(screen)

    def _on_accessibility_changed(self) -> None:
        """Réapplique les couleurs peintes à la main (icônes, focus, progression)."""
        self.update()
        self._refresh_button_icons()

    def _available_screen(self) -> tuple[int, int] | None:
        """Zone utile de l'écran courant, ou None quand elle est inconnue."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        return (area.width(), area.height()) if area is not None else None

    def _apply_window_limits(self) -> None:
        """La taille minimale suit le zoom : à 160 %, la fenêtre ne peut plus être écrasée."""
        zoom = ThemeManager.instance().zoom()
        self.setMinimumSize(*fit_to_screen(
            round(self._min_base[0] * zoom), round(self._min_base[1] * zoom), self._available_screen()))

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        """Taille souhaitée : celle d'ouverture, plafonnée à l'écran.

        Ouvert dans un onglet, un module imposait sinon la hauteur réclamée par son
        contenu (près de 1 000 px pour Dedup à 125 %) : la fenêtre grandissait au-delà
        du bas de l'écran. Elle garde ici la taille demandée à l'ouverture, quitte à
        faire défiler son contenu interne.
        """
        zoom = ThemeManager.instance().zoom()
        return QSize(*fit_to_screen(round(self._resize_base[0] * zoom),
                                    round(self._resize_base[1] * zoom),
                                    self._available_screen()))

    def _open_preferences(self) -> None:
        PreferencesDialog(self).exec()
        self.show_binary_status(self._binary_names)

    @staticmethod
    def _shortcut_text(sequence: str) -> str:
        """Raccourci lisible : ⌘ sur macOS, Ctrl+Entrée en français ailleurs."""
        text = QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)
        if sys.platform != "darwin":
            text = text.replace("Return", "Entrée").replace("Enter", "Entrée").replace("Esc", "Échap")
        return text

    def _help_shortcuts(self) -> list[tuple[str, str]]:
        return [(self._shortcut_text(sequence), description) for sequence, description in shortcuts()]

    def _build_help_dialog(self) -> GlassDialog:
        dlg = GlassDialog(self)
        dlg.setWindowTitle(self.tr("Aide — {name}").format(name=self.spec.name))
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        title = QWidget()
        title_row = QHBoxLayout(title)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        if icons.available(self.spec.icon):
            logo = QLabel()
            logo.setPixmap(icons.pixmap(self.spec.icon, 24, "muted"))
            title_row.addWidget(logo)
            heading = QLabel(self.spec.name)
        else:                                              # texte ou emoji de repli
            heading = QLabel(f"{self.spec.icon}\u00a0\u00a0{self.spec.name}")
        heading.setObjectName("ModuleName")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        title_row.addWidget(heading)
        title_row.addStretch(1)
        tagline = QLabel(QCoreApplication.translate("Modules", self.spec.tagline))
        tagline.setWordWrap(True)
        version = QLabel(self.tr("{app} · version {version}").format(app=APP_NAME, version=__version__))
        version.setProperty("role", "muted")
        rows = "".join(f"<tr><td><b>{html.escape(k)}</b>&nbsp;&nbsp;&nbsp;</td><td>{html.escape(v)}</td></tr>"
                       for k, v in self._help_shortcuts())
        shortcut_label = QLabel(f"<p><b>{html.escape(self.tr('Raccourcis clavier'))}</b></p>"
                                f"<table cellspacing='4'>{rows}</table>")
        shortcut_label.setTextFormat(Qt.TextFormat.RichText)
        for w in (title, tagline, version, shortcut_label):
            lay.addWidget(w)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(self.tr("Fermer"))
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        return dlg

    def _show_help(self) -> None:
        self._build_help_dialog().exec()

    # ═══════════════════════════════════════════════════════════════════════
    # Glisser-déposer et fermeture
    # ═══════════════════════════════════════════════════════════════════════
    def handle_drop(self, mime: QMimeData | None) -> list[str]:
        """Transmet les chemins déposés à `open_paths` (refusé pendant un traitement)."""
        paths = _mime_paths(mime)
        if not paths:
            return []
        if self.is_busy():
            self.log_panel.append_log("WARN", self.tr("Un traitement est en cours : dépôt ignoré."))
            return []
        self.open_paths(paths)
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _mime_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self.handle_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def closeEvent(self, event: QCloseEvent) -> None:
        worker = self._worker
        if worker is not None:
            self.cancel_current()
            if not worker.wait(_CLOSE_WAIT_MS):
                # Ne jamais détruire un QThread en cours : la fermeture est reportée à sa fin réelle.
                log.warning("Le traitement ne s'est pas arrêté dans %d ms : fermeture reportée.", _CLOSE_WAIT_MS)
                self._close_pending = True
                self.log_panel.append_log("WARN", self.tr("Fermeture reportée : le traitement doit s'arrêter."))
                event.ignore()
                return
            QCoreApplication.processEvents()                 # traite les signaux en attente (historique, rappels)
        event.accept()
