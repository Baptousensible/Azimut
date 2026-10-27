"""Fenêtre principale d'Atlas : une carte par module + tableau de bord."""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from azimut import __version__
from azimut.core.history import HistoryStore
from azimut.core.logs import get_logger
from azimut.core.module_spec import ModuleSpec, load_window_class
from azimut.launcher.dashboard import Dashboard, safe_resolve
from azimut.launcher.registry import discover_with_errors
from azimut.ui.preferences_dialog import PreferencesDialog
from azimut.ui.theme import ThemeManager, token
from azimut.ui.widgets import Card, StatusChip

log = get_logger(__name__)


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
        self.icon_label.setStyleSheet("font-size: 30px;")
        self.name_label = QLabel(spec.name)
        self.name_label.setStyleSheet("font-size: 17px; font-weight: 700;")
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

        self.hint_label = QLabel("Déposez des fichiers ou un dossier ici pour les ouvrir directement.")
        self.hint_label.setWordWrap(True)
        self.open_button = QPushButton("Ouvrir")
        self.open_button.setProperty("kind", "primary")
        self.open_button.clicked.connect(lambda: self.openRequested.emit(self.spec.id))
        foot = QHBoxLayout()
        foot.addWidget(self.hint_label, 1)
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
        self.hint_label.hide()
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
        self.hint_label.setStyleSheet(muted + " font-size: 11px;")
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


class AtlasWindow(QMainWindow):
    """Atlas — le recueil des utilitaires Azimut."""

    COLUMNS = 2

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

        self.setWindowTitle("Azimut — Atlas")
        self.resize(1040, 820)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        self.warning_banner = QLabel("")
        self.warning_banner.setWordWrap(True)
        self.warning_banner.hide()
        root.addWidget(self.warning_banner)

        root.addLayout(self._build_cards())
        self.dashboard = Dashboard(self._specs, history_store)
        self.dashboard.binariesRefreshed.connect(self._refresh_binary_state)
        root.addWidget(self.dashboard, 1)
        self.setCentralWidget(central)

        self._restyle()
        ThemeManager.instance().changed.connect(self._restyle)
        self._update_banner()

    # ── construction ──
    def _build_header(self) -> QHBoxLayout:
        title = QLabel("🧭 Azimut")
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        self.subtitle_label = QLabel("Atlas — le recueil des utilitaires Azimut")
        self.version_label = QLabel(f"Version {__version__}")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(title)
        titles.addWidget(self.subtitle_label)
        titles.addWidget(self.version_label)

        self.theme_button = QPushButton("Thème")
        self.theme_button.setShortcut("Ctrl+T")
        self.theme_button.clicked.connect(lambda: ThemeManager.instance().toggle())
        self.prefs_button = QPushButton("Préférences…")
        self.prefs_button.setShortcut("Ctrl+,")
        self.prefs_button.clicked.connect(lambda: self._on_preferences())

        header = QHBoxLayout()
        header.addLayout(titles, 1)
        header.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.prefs_button, 0, Qt.AlignmentFlag.AlignTop)
        return header

    def _build_cards(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(12)
        entries: list[tuple[ModuleSpec, str]] = [(s, "") for s in self._specs]
        entries += [(_placeholder_spec(mid), msg) for mid, msg in self._errors.items()
                    if mid not in self._spec_by_id]
        if not entries:
            grid.addWidget(QLabel("Aucun module disponible."), 0, 0)
        for index, (spec, error) in enumerate(entries):
            card = ModuleCard(spec)
            if error:
                card.set_error(error)
            card.openRequested.connect(self.open_module)
            card.pathsDropped.connect(self.open_module)
            self.cards[spec.id] = card
            grid.addWidget(card, index // self.COLUMNS, index % self.COLUMNS)
        for column in range(self.COLUMNS):
            grid.setColumnStretch(column, 1)
        return grid

    # ── style ──
    def _restyle(self, _name: str = "") -> None:
        muted = f"color: {token('muted')};"
        self.subtitle_label.setStyleSheet(muted)
        self.version_label.setStyleSheet(muted + " font-size: 11px;")
        self.warning_banner.setStyleSheet(
            f"background: {token('warn_bg')}; color: {token('warn_txt')}; "
            "border-radius: 8px; padding: 8px 12px;")

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
