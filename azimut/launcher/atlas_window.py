"""Fenêtre principale d'Atlas : barre latérale, accueil (dépôt intelligent, récents, outils), activité."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from azimut import __version__
from azimut.core.history import HistoryStore
from azimut.core.logs import get_logger
from azimut.core.module_spec import PLANNED_MODULES, ModuleSpec, PlannedModule, load_window_class
from azimut.launcher import routing
from azimut.launcher.dashboard import Dashboard, safe_resolve
from azimut.launcher.palette import CommandPalette, PaletteEntry
from azimut.launcher.recents import RecentStore, ago
from azimut.launcher.registry import discover_with_errors
from azimut.ui.preferences_dialog import PreferencesDialog
from azimut.ui.theme import ThemeManager, token
from azimut.ui.widgets import Card, DropZone, StatusChip

log = get_logger(__name__)

MIN_SIZE = (860, 540)
DEFAULT_SIZE = (1180, 760)
DEFAULT_RECOMMENDED = ("convert", "dedup", "tags")


class ModuleCard(Card):
    """Carte d'un module : icône, nom, tagline, pastilles de binaires, bouton « Ouvrir »."""

    openRequested = Signal(str)        # id du module
    pathsDropped = Signal(str, list)   # id du module, chemins déposés

    def __init__(self, spec: ModuleSpec, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.spec = spec
        self._error = ""
        self.chips: dict[str, StatusChip] = {}
        self.setAcceptDrops(True)

        self.icon_label = QLabel(spec.icon)
        self.icon_label.setStyleSheet("font-size: 24px;")
        self.name_label = QLabel(spec.name)
        self.name_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        head = QHBoxLayout()
        head.addWidget(self.icon_label)
        head.addWidget(self.name_label, 1)
        self.body_layout.addLayout(head)

        self.tagline_label = QLabel(spec.tagline)
        self.tagline_label.setWordWrap(True)
        self.body_layout.addWidget(self.tagline_label)

        self.chips_layout = QHBoxLayout()
        for name in spec.requires:
            chip = StatusChip(name, "neutre")
            self.chips[name] = chip
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        self.body_layout.addLayout(self.chips_layout)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self.body_layout.addWidget(self.error_label)

        self.hint_label = QLabel("")                 # conservé pour compatibilité ; l'astuce est en infobulle
        self.hint_label.hide()
        self.setToolTip("Déposez des fichiers ou un dossier sur cette carte pour les ouvrir directement.")
        self.open_button = QPushButton("Ouvrir")
        self.open_button.setProperty("kind", "primary")
        self.open_button.clicked.connect(lambda: self.openRequested.emit(self.spec.id))
        self.recommended_chip = StatusChip("★ Recommandé", "info")
        self.recommended_chip.hide()
        foot = QHBoxLayout()
        foot.addWidget(self.recommended_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addStretch(1)
        foot.addWidget(self.open_button)
        self.body_layout.addLayout(foot)

        self.refresh_binaries()
        self.restyle()
        ThemeManager.instance().changed.connect(self.restyle)

    # ── état ──
    def is_failed(self) -> bool:
        return bool(self._error)

    def set_error(self, message: str) -> None:
        """Désactive la carte et affiche l'erreur."""
        self._error = message or "erreur inconnue"
        short = self._error if len(self._error) <= 240 else self._error[:237] + "…"
        self.error_label.setText(short)
        self.error_label.setToolTip(self._error)
        self.error_label.show()
        if "__error__" not in self.chips:
            chip = StatusChip("✖ indisponible", "err")
            chip.setToolTip(self._error)
            self.chips["__error__"] = chip
            self.chips_layout.insertWidget(0, chip)
        self.open_button.setEnabled(False)
        self.setAcceptDrops(False)
        self.restyle()

    def refresh_binaries(self) -> None:
        for name, chip in self.chips.items():
            if name == "__error__":
                continue
            info = safe_resolve(name)
            if info is not None and info.available:
                chip.set_state(f"✔ {name}", "ok")
                chip.setToolTip(f"{name} {info.version}".strip() + f"\n{info.path}")
            else:
                chip.set_state(f"✖ {name}", "err")
                chip.setToolTip("Introuvable : voir « Préférences… » ou le dossier bin/.")

    def restyle(self, _name: str = "") -> None:
        muted = f"color: {token('muted')};"
        self.tagline_label.setStyleSheet(muted)
        self.error_label.setStyleSheet(f"color: {token('err_txt')};")

    # ── glisser-déposer ──
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if not self.is_failed() and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(self.spec.id, paths)


def _placeholder_spec(module_id: str) -> ModuleSpec:
    """Spec factice pour afficher un module dont l'import a échoué."""
    return ModuleSpec(id=module_id, name=f"Azimut {module_id.capitalize()}",
                      tagline="Ce module n'a pas pu être chargé.", icon="⚠️", entry="", order=1000)


class PlannedCard(Card):
    """Carte d'un module à venir : atténuée, non ouvrable."""

    def __init__(self, planned: PlannedModule, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.planned = planned
        self.setProperty("planned", True)
        head = QHBoxLayout()
        icon = QLabel(planned.icon)
        icon.setStyleSheet("font-size: 24px;")
        name = QLabel(planned.name)
        name.setStyleSheet("font-size: 15px; font-weight: 700;")
        head.addWidget(icon)
        head.addWidget(name, 1)
        head.addWidget(StatusChip("Bientôt", "neutre"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.body_layout.addLayout(head)
        tagline = QLabel(planned.tagline)
        tagline.setWordWrap(True)
        tagline.setProperty("role", "muted")
        self.body_layout.addWidget(tagline)


class SuggestionDialog(QDialog):
    """« Que faire de ces fichiers ? » : modules proposés pour un dépôt, du plus au moins pertinent."""

    def __init__(self, summary_text: str, suggestions: list[routing.Suggestion],
                 names: dict[str, tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Que faire de ces fichiers ?")
        self.setMinimumWidth(480)
        self.chosen: str | None = None
        self.buttons: dict[str, QPushButton] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        title = QLabel("Que faire de ces fichiers ?")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        lay.addWidget(title)
        what = QLabel(summary_text)
        what.setProperty("role", "muted")
        what.setWordWrap(True)
        lay.addWidget(what)
        for index, sug in enumerate(suggestions):
            name, icon = names.get(sug.module_id, (sug.module_id, ""))
            btn = QPushButton()
            btn.setProperty("kind", "tile")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            inner = QVBoxLayout(btn)
            inner.setContentsMargins(4, 2, 4, 2)
            inner.setSpacing(2)
            head = QLabel(f"{icon}  <b>{name}</b>" + ("   ★ conseillé" if index == 0 else ""))
            head.setTextFormat(Qt.TextFormat.RichText)
            reason = QLabel(sug.reason)
            reason.setWordWrap(True)
            reason.setProperty("role", "muted")
            for lab in (head, reason):
                lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                inner.addWidget(lab)
            btn.setMinimumHeight(60)
            btn.clicked.connect(partial(self.choose, sug.module_id))
            lay.addWidget(btn)
            self.buttons[sug.module_id] = btn
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        lay.addLayout(row)

    def choose(self, module_id: str) -> None:
        self.chosen = module_id
        self.accept()


class AtlasWindow(QMainWindow):
    """Atlas — le recueil des utilitaires Azimut."""

    COLUMNS = 3
    NARROW_WIDTH = 1000          # en dessous, deux colonnes de cartes

    def __init__(self, specs: list[ModuleSpec] | None = None, errors: dict[str, str] | None = None,
                 *, history_store: HistoryStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if specs is None:
            specs, found = discover_with_errors()
            if errors is None:
                errors = found
        self._specs = sorted(specs, key=lambda s: (s.order, s.id))
        self._spec_by_id = {s.id: s for s in self._specs}
        self._errors = dict(errors or {})
        self._windows: dict[str, QMainWindow] = {}
        self.cards: dict[str, ModuleCard] = {}
        self._planned_cards: list[PlannedCard] = []
        self._history_store = history_store
        self._columns = self.COLUMNS
        try:
            self.recents = RecentStore()
        except Exception:                                   # préférences illisibles : pas de récents, pas d'erreur
            log.exception("Fichiers récents indisponibles")
            self.recents = None                             # type: ignore[assignment]

        self.setWindowTitle("Azimut — Atlas")
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*self._default_size())

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        body = QVBoxLayout()
        body.setContentsMargins(18, 14, 18, 10)
        body.setSpacing(10)
        self.warning_banner = QLabel("")
        self.warning_banner.setWordWrap(True)
        self.warning_banner.hide()
        body.addWidget(self.warning_banner)
        self.stack = QStackedWidget()
        self.dashboard = Dashboard(self._specs, history_store)
        self.dashboard.binariesRefreshed.connect(self._refresh_binary_state)
        self.home_page = self._build_home()
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.dashboard)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self.setCentralWidget(central)

        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_palette)
        self._restyle()
        ThemeManager.instance().changed.connect(self._restyle)
        self._update_banner()
        self._refresh_recents()
        self._refresh_recommended()
        self.show_page("home")

    # ── construction ──
    def _default_size(self) -> tuple[int, int]:
        from PySide6.QtWidgets import QApplication
        w, h = DEFAULT_SIZE
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            w, h = min(w, max(MIN_SIZE[0], area.width() - 40)), min(h, max(MIN_SIZE[1], area.height() - 60))
        return w, h

    def _nav_button(self, text: str, callback, *, checkable: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("kind", "nav")
        btn.setCheckable(checkable)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _c=False: callback())
        return btn

    def _build_sidebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(224)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(4)

        title = QLabel("🧭 Azimut")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.subtitle_label = QLabel("Atlas — le recueil des utilitaires")
        self.subtitle_label.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(self.subtitle_label)
        lay.addSpacing(8)

        self.search_button = self._nav_button("🔍  Rechercher…        Ctrl+K", self.open_palette)
        lay.addWidget(self.search_button)
        lay.addSpacing(6)

        self.nav_home = self._nav_button("🏠  Accueil", lambda: self.show_page("home"), checkable=True)
        self.nav_activity = self._nav_button("🕘  Activité", lambda: self.show_page("activity"), checkable=True)
        lay.addWidget(self.nav_home)
        lay.addWidget(self.nav_activity)
        lay.addSpacing(8)
        tools = QLabel("OUTILS")
        tools.setProperty("role", "muted")
        tools.setStyleSheet("font-size: 11px; font-weight: 700; padding-left: 12px;")
        lay.addWidget(tools)

        self.module_buttons: dict[str, QPushButton] = {}
        for spec in self._specs:
            btn = self._nav_button(f"{spec.icon}  {spec.name.replace('Azimut ', '')}",
                                   partial(self.open_module, spec.id))
            btn.setToolTip(spec.tagline)
            self.module_buttons[spec.id] = btn
            lay.addWidget(btn)
        for planned in PLANNED_MODULES:
            btn = self._nav_button(f"{planned.icon}  {planned.name.replace('Azimut ', '')} · bientôt", lambda: None)
            btn.setEnabled(False)
            btn.setToolTip(planned.tagline)
            lay.addWidget(btn)
        lay.addStretch(1)

        self.version_label = QLabel(f"Version {__version__}")
        lay.addWidget(self.version_label)
        self.theme_button = self._nav_button("Thème", lambda: ThemeManager.instance().toggle())
        self.theme_button.setShortcut("Ctrl+T")
        self.prefs_button = self._nav_button("⚙️  Préférences…", lambda: self._on_preferences())
        self.prefs_button.setShortcut("Ctrl+,")
        lay.addWidget(self.theme_button)
        lay.addWidget(self.prefs_button)
        self._on_theme_label()
        return bar

    def _build_home(self) -> QScrollArea:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)

        title = QLabel("Que voulez-vous faire ?")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.home_hint = QLabel("Déposez des fichiers ou des dossiers : Atlas vous propose les outils adaptés. "
                                "Ctrl+K pour tout rechercher.")
        self.home_hint.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(self.home_hint)

        self.drop = DropZone("Déposez ici des fichiers ou des dossiers\n(ou cliquez pour parcourir)")
        self.drop.setMinimumHeight(110)
        self.drop.pathsDropped.connect(self.handle_smart_drop)
        lay.addWidget(self.drop)

        self.recents_card = Card("Reprendre")
        self.recents_body = QVBoxLayout()
        self.recents_body.setSpacing(2)
        self.recents_card.body_layout.addLayout(self.recents_body)
        self.recents_clear = QPushButton("Effacer la liste")
        self.recents_clear.setProperty("kind", "ghost")
        self.recents_clear.clicked.connect(self._clear_recents)
        self.recents_card.body_layout.addWidget(self.recents_clear, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.recents_card)

        label = QLabel("Tous les outils")
        label.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(label)
        self.cards_grid = QGridLayout()
        self.cards_grid.setSpacing(12)
        entries: list[tuple[ModuleSpec, str]] = [(s, "") for s in self._specs]
        entries += [(_placeholder_spec(mid), msg) for mid, msg in self._errors.items()
                    if mid not in self._spec_by_id]
        if not entries:
            self.cards_grid.addWidget(QLabel("Aucun module disponible."), 0, 0)
        for spec, error in entries:
            card = ModuleCard(spec)
            if error:
                card.set_error(error)
            card.openRequested.connect(self.open_module)
            card.pathsDropped.connect(self.open_module)
            self.cards[spec.id] = card
        self._planned_cards = [PlannedCard(p) for p in PLANNED_MODULES]
        self._layout_cards()
        lay.addLayout(self.cards_grid)
        lay.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    def _layout_cards(self) -> None:
        """(Re)place les cartes : 3 colonnes si la fenêtre est large, 2 sinon."""
        widgets: list[QWidget] = [*self.cards.values(), *self._planned_cards]
        for w in widgets:
            self.cards_grid.removeWidget(w)
        for index, w in enumerate(widgets):
            self.cards_grid.addWidget(w, index // self._columns, index % self._columns)
        for column in range(3):
            self.cards_grid.setColumnStretch(column, 1 if column < self._columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        columns = self.COLUMNS if self.width() >= self.NARROW_WIDTH else 2
        if columns != self._columns and hasattr(self, "cards_grid"):
            self._columns = columns
            self._layout_cards()

    # ── navigation ──
    def show_page(self, name: str) -> None:
        activity = name == "activity"
        self.stack.setCurrentWidget(self.dashboard if activity else self.home_page)
        self.nav_home.setChecked(not activity)
        self.nav_activity.setChecked(activity)
        if activity:
            try:
                self.dashboard.refresh_history()
            except RuntimeError:
                pass

    def current_page(self) -> str:
        return "activity" if self.stack.currentWidget() is self.dashboard else "home"

    # ── style ──
    def _restyle(self, _name: str = "") -> None:
        muted = f"color: {token('muted')};"
        self.subtitle_label.setStyleSheet(muted + " font-size: 11px;")
        self.version_label.setStyleSheet(muted + " font-size: 11px; padding-left: 12px;")
        self.home_hint.setStyleSheet(muted)
        self.warning_banner.setStyleSheet(
            f"background: {token('warn_bg')}; color: {token('warn_txt')}; "
            "border-radius: 8px; padding: 8px 12px;")
        self._on_theme_label()

    def _on_theme_label(self) -> None:
        dark = ThemeManager.instance().theme() == "dark"
        self.theme_button.setText("☀️  Thème clair" if dark else "🌙  Thème sombre")

    # ── binaires ──
    def _update_banner(self) -> None:
        """Avertissement non bloquant si un binaire requis est absent."""
        missing: dict[str, list[str]] = {}
        for spec in self._specs:
            for name in spec.requires:
                info = safe_resolve(name)
                if info is None or not info.available:
                    missing.setdefault(name, []).append(spec.name)
        if not missing:
            self.warning_banner.hide()
            return
        parts = [f"{name} ({', '.join(users)})" for name, users in missing.items()]
        self.warning_banner.setText(
            "⚠ Binaire(s) requis introuvable(s) : " + " · ".join(parts)
            + ". Indiquez leur chemin dans « Préférences… » ou déposez-les dans le dossier bin/.")
        self.warning_banner.show()

    def _refresh_binary_state(self) -> None:
        for card in self.cards.values():
            card.refresh_binaries()
        self._update_banner()

    def _on_preferences(self) -> None:
        PreferencesDialog(self).exec()
        self.dashboard.binaries_tab.reload()
        self._refresh_binary_state()

    # ── recommandations et récents ──
    def recommended_ids(self) -> list[str]:
        """Les trois modules les plus utilisés (historique) ; complétés par les valeurs par défaut."""
        counts: dict[str, int] = {}
        try:
            store = self._history_store if self._history_store is not None else HistoryStore()
            for entry in store.recent(limit=200):
                counts[entry.module] = counts.get(entry.module, 0) + 1
        except Exception:
            log.exception("Historique illisible pour les recommandations")
        ranked = [m for m, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if m in self._spec_by_id]
        for module_id in DEFAULT_RECOMMENDED:
            if module_id in self._spec_by_id and module_id not in ranked:
                ranked.append(module_id)
        return ranked[:3]

    def _refresh_recommended(self) -> None:
        chosen = set(self.recommended_ids())
        for module_id, card in self.cards.items():
            card.recommended_chip.setVisible(module_id in chosen and not card.is_failed())

    def _refresh_recents(self) -> None:
        while self.recents_body.count():
            item = self.recents_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        items = [r for r in (self.recents.items() if self.recents else []) if r.module in self._spec_by_id][:6]
        self.recents_card.setVisible(bool(items))
        for recent in items:
            spec = self._spec_by_id[recent.module]
            btn = QPushButton(f"{spec.icon}  {recent.name}   —   {spec.name} · {ago(recent.timestamp)}")
            btn.setProperty("kind", "ghost")
            btn.setStyleSheet("QPushButton { text-align: left; }")
            btn.setToolTip(recent.path)
            btn.clicked.connect(partial(self.open_module, recent.module, [recent.path]))
            self.recents_body.addWidget(btn)

    def _clear_recents(self) -> None:
        if self.recents:
            self.recents.clear()
        self._refresh_recents()

    def _remember(self, module_id: str, paths: list[str]) -> None:
        if not paths or not self.recents:
            return
        try:
            self.recents.add([str(p) for p in paths[:3]], module_id)
            self._refresh_recents()
        except Exception:
            log.exception("Fichiers récents non enregistrés")

    # ── dépôt intelligent ──
    def usable_module_ids(self) -> list[str]:
        return [mid for mid in self._spec_by_id if mid in self.cards and not self.cards[mid].is_failed()]

    def handle_smart_drop(self, paths: list[str]) -> str | None:
        """Analyse un dépôt, propose les modules adaptés, ouvre celui qu'on choisit. Renvoie son identifiant."""
        paths = [str(p) for p in paths if p]
        if not paths:
            return None
        suggestions = routing.suggest(paths, available=self.usable_module_ids())
        if not suggestions:
            self.statusBar().showMessage("Aucun outil ne correspond à ce dépôt.", 6000)
            return None
        names = {s.id: (s.name, s.icon) for s in self._specs}
        dialog = SuggestionDialog(routing.describe(routing.summarize(paths)), suggestions, names, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.chosen:
            return None
        self.open_module(dialog.chosen, paths)
        return dialog.chosen

    # ── palette de commandes ──
    def palette_entries(self) -> list[PaletteEntry]:
        entries: list[PaletteEntry] = [
            PaletteEntry("page:home", "Accueil", "Déposer des fichiers, reprendre, tous les outils", "🏠",
                         ("home", "demarrer"), callback=lambda: self.show_page("home")),
            PaletteEntry("page:activity", "Activité", "Historique des traitements, journal, binaires", "🕘",
                         ("historique", "journal", "binaires", "ffmpeg", "exiftool"),
                         callback=lambda: self.show_page("activity")),
        ]
        for spec in self._specs:
            card = self.cards.get(spec.id)
            entries.append(PaletteEntry(
                f"module:{spec.id}", spec.name, spec.tagline, spec.icon, (spec.id,),
                enabled=card is not None and not card.is_failed(),
                callback=partial(self.open_module, spec.id)))
        for planned in PLANNED_MODULES:
            entries.append(PaletteEntry(f"planned:{planned.id}", f"{planned.name} (bientôt)", planned.tagline,
                                        planned.icon, (planned.id,), enabled=False))
        entries += [
            PaletteEntry("theme", "Basculer le thème clair / sombre", "", "🌓", ("sombre", "clair", "dark", "light"),
                         callback=lambda: ThemeManager.instance().toggle()),
            PaletteEntry("prefs", "Préférences…", "Thème, taille du texte, chemins des outils externes", "⚙️",
                         ("reglages", "settings", "zoom"), callback=self._on_preferences),
        ]
        return entries

    def open_palette(self) -> CommandPalette:
        palette = CommandPalette(self.palette_entries(), self)
        palette.exec()
        return palette

    # ── ouverture des modules ──
    @staticmethod
    def _is_alive(window: QMainWindow) -> bool:
        try:
            return window.isVisible() or window.isMinimized()
        except RuntimeError:  # objet C++ déjà détruit
            return False

    @staticmethod
    def _bring_to_front(window: QMainWindow) -> None:
        window.show()
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()

    def open_module(self, module_id: str, paths: list[str] | None = None) -> QMainWindow | None:
        """Ouvre (ou ramène au premier plan) la fenêtre d'un module ; transmet les chemins."""
        spec = self._spec_by_id.get(module_id)
        card = self.cards.get(module_id)
        if spec is None or card is None or card.is_failed():
            return None
        window = self._windows.get(module_id)
        if window is not None and not self._is_alive(window):
            self._windows.pop(module_id, None)
            window = None
        if window is None:
            try:
                window = load_window_class(spec)(spec)
            except Exception as exc:
                message = f"{type(exc).__name__} : {exc}"
                log.exception("Ouverture de %s impossible", spec.name)
                card.set_error(message)
                self._update_banner()
                QMessageBox.warning(self, spec.name, f"Impossible d'ouvrir {spec.name}.\n\n{message}")
                return None
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            window.destroyed.connect(partial(self._on_module_closed, module_id, id(window)))
            self._windows[module_id] = window
        self._bring_to_front(window)
        if paths:
            self._remember(module_id, list(paths))
            try:
                window.open_paths(list(paths))
            except Exception:
                log.exception("Transmission des chemins à %s impossible", spec.name)
                self.statusBar().showMessage("Les fichiers n'ont pas pu être transmis au module.", 5000)
        return window

    def _on_module_closed(self, module_id: str, window_token: int | None = None, *_: object) -> None:
        """Une fenêtre de module a été fermée : l'oublier et rafraîchir l'historique."""
        current = self._windows.get(module_id)
        if window_token is None or current is None or id(current) == window_token:
            self._windows.pop(module_id, None)
        try:
            self.dashboard.refresh_history()
            self._refresh_recommended()
        except RuntimeError:
            pass

    def closeEvent(self, event) -> None:  # noqa: N802
        for window in list(self._windows.values()):
            try:
                window.close()
            except RuntimeError:
                pass
        self._windows.clear()
        self.dashboard.shutdown()
        super().closeEvent(event)
