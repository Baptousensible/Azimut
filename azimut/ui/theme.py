"""Thème Azimut : palette claire/sombre, feuille de style QSS maison et gestionnaire de thème."""
from __future__ import annotations

import math
from string import Template
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from azimut.core.logs import get_logger
from azimut.core.paths import resource_path
from azimut.core.prefs import Preferences

log = get_logger(__name__)

THEMES: tuple[str, ...] = ("dark", "light")
ZOOM_MIN, ZOOM_MAX = 0.8, 1.6
BASE_POINT_SIZE = 10.0

# Police d'interface de PDFcraft (Inter, licence SIL OFL — voir resources/fonts/OFL-Inter.txt).
UI_FONT_FAMILY = "Inter"
_UI_FONT_FILES: tuple[str, ...] = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")
_loaded_ui_font: str | None = None      # None : pas encore tenté ; "" : indisponible


def load_ui_font() -> str:
    """Enregistre Inter auprès de Qt (une seule fois) ; renvoie la famille, ou "" si les fichiers manquent."""
    global _loaded_ui_font
    if _loaded_ui_font is not None:
        return _loaded_ui_font
    family = ""
    for name in _UI_FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(resource_path("fonts", name)))
        if font_id < 0:
            log.warning("Police d'interface introuvable ou illisible : %s", name)
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families and not family:
            family = families[0]
    _loaded_ui_font = family
    return family

# nom : (clair, sombre)
# Style « PDFcraft » : chrome neutre (gris à peine teinté), panneaux blancs en clair, un seul accent bleu.
# Les neutres et le bleu reprennent les jetons de crates/ui-egui/src/theme.rs de PDFcraft.
_PALETTE: dict[str, tuple[str, str]] = {
    "bg": ("#f1f1f3", "#19191c"),                 # PDFcraft : pasteboard
    "surface": ("#ffffff", "#26262a"),            # panel / chrome
    "surface_alt": ("#f6f6f8", "#1e1e22"),        # champs, tableaux, pistes (PDFcraft : field)
    "border": ("#dadade", "#3c3c43"),
    "text": ("#222226", "#ececef"),
    "muted": ("#5e5e66", "#aeaeb6"),
    "primary": ("#1b63e0", "#4b8bf5"),            # PDFcraft : accent
    "primary_hover": ("#1755c4", "#3d7dee"),
    "success": ("#1f9d63", "#22a06b"),
    "success_hover": ("#187a4d", "#1a8057"),
    "danger": ("#d64545", "#c24141"),
    "danger_hover": ("#b13434", "#a03333"),
    "accent": ("#7a5cc7", "#6f5bb5"),
    "accent_hover": ("#6248a8", "#5a4896"),
    "secondary": ("#f0f0f3", "#34343a"),          # PDFcraft : hover
    "secondary_hover": ("#e4e4e9", "#3e3e45"),    # PDFcraft : pressed
    "ok_txt": ("#13804a", "#4cc46a"),
    "warn_txt": ("#a85f00", "#f0a030"),
    "err_txt": ("#c93434", "#ff6b6b"),
    "info_txt": ("#55555d", "#c4c4cb"),
    "ok_bg": ("#dff5e8", "#17382a"),
    "warn_bg": ("#fdf0d8", "#3d2f12"),
    "err_bg": ("#fde3e3", "#402020"),
    "info_bg": ("#e3ecfd", "#243657"),            # PDFcraft : accent_soft
}
TOKEN_NAMES: tuple[str, ...] = tuple(_PALETTE)


def palette(theme: str) -> dict[str, str]:
    """Couleurs (hexadécimal) de tous les tokens pour le thème donné."""
    if theme not in THEMES:
        raise ValueError(f"Thème inconnu : {theme!r}")
    idx = 1 if theme == "dark" else 0
    return {name: pair[idx] for name, pair in _PALETTE.items()}


def clamp_zoom(value: Any) -> float:
    """Ramène une valeur quelconque dans 0.8–1.6 (1.0 si elle est inexploitable)."""
    try:
        z = float(value)
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(z):
        return 1.0
    return max(ZOOM_MIN, min(ZOOM_MAX, z))


def repolish(widget: QWidget) -> None:
    """À appeler après un changement de propriété dynamique (kind, level, dragOver…)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


# ─────────────────────────────────────────────────────────────────────────────
# Feuille de style
# ─────────────────────────────────────────────────────────────────────────────
_QSS = Template(r"""
QWidget { color: $text; selection-background-color: $info_bg; selection-color: $text; }
QMainWindow, QDialog { background-color: $bg; }
QLabel { background: transparent; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="title"] { font-weight: 600; }

QToolTip { background-color: $surface; color: $text; border: 1px solid $border; padding: $p4; }

/* ── Composants Azimut ── */
QFrame#Card { background-color: $surface; border: 1px solid $border; border-radius: 12px; }
QFrame#Card QLabel#CardTitle { font-weight: 600; }
QFrame#DropZone { background-color: $surface_alt; border: 2px dashed $border; border-radius: 12px; }
QFrame#DropZone[dragOver="true"] { border-color: $primary; background-color: $info_bg; }
QFrame#DropZone QLabel { color: $muted; }
QLabel#StatusChip { background-color: $secondary; color: $muted; border-radius: 11px; padding: $p2 $p10; font-weight: 600; }
QLabel#StatusChip[level="ok"] { background-color: $ok_bg; color: $ok_txt; }
QLabel#StatusChip[level="warn"] { background-color: $warn_bg; color: $warn_txt; }
QLabel#StatusChip[level="err"] { background-color: $err_bg; color: $err_txt; }
QLabel#StatusChip[level="info"] { background-color: $info_bg; color: $primary; }
QLabel#StatusChip[level="neutre"] { background-color: $surface_alt; color: $muted; }

/* ── Boutons ── */
QPushButton { background-color: $secondary; color: $text; border: 1px solid transparent; border-radius: 6px; padding: $p6 $p14; }
QPushButton:hover { background-color: $secondary_hover; }
QPushButton:focus { border-color: $primary; }
__KINDS__
QPushButton:disabled, QPushButton[kind]:disabled { background-color: $surface_alt; color: $muted; }
QPushButton[kind="ghost"]:disabled { background-color: transparent; }

/* ── Champs ── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: $surface_alt; color: $text; border: 1px solid $border; border-radius: 6px; padding: $p6 $p10; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: $primary; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: $muted; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background-color: $surface; border: 1px solid $border; border-radius: 6px; selection-background-color: $info_bg; selection-color: $text; outline: 0; }

QCheckBox { spacing: $p8; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid $muted; border-radius: 4px; background: transparent; }
QCheckBox::indicator:hover { border-color: $primary; }
QCheckBox::indicator:checked { background-color: $primary; border-color: $primary; }
QCheckBox::indicator:disabled { border-color: $border; }

QProgressBar { background-color: $surface_alt; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; text-align: center; color: $text; }
QProgressBar::chunk { background-color: $primary; border-radius: 4px; }

/* ── Tableaux et arbres ── */
QTableView, QTreeView, QListView { background-color: $surface_alt; alternate-background-color: $surface; border: 1px solid $border; border-radius: 8px; gridline-color: $border; outline: 0; }
QTableView::item, QTreeView::item { padding: $p2 $p4; }
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected { background-color: $info_bg; color: $text; }
QHeaderView::section { background-color: $secondary; color: $text; padding: $p6 $p8; border: none; border-right: 1px solid $border; border-bottom: 1px solid $border; font-weight: 600; }
QTableCornerButton::section { background-color: $secondary; border: none; }

QPlainTextEdit, QTextEdit { background-color: $surface_alt; color: $text; border: 1px solid $border; border-radius: 8px; padding: $p4; }

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background-color: $border; border-radius: 3px; min-height: 28px; margin: 2px; }
QScrollBar::handle:vertical:hover { background-color: $muted; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background-color: $border; border-radius: 3px; min-width: 28px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background-color: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ── Barres, docks, onglets, menus ── */
QToolBar { background-color: $surface; border: none; border-bottom: 1px solid $border; spacing: $p6; padding: $p4; }
QToolBar::separator { background-color: $border; width: 1px; margin: $p4 $p6; }
QToolButton { background: transparent; color: $text; border: none; border-radius: 6px; padding: $p6; }
QToolButton:hover { background-color: $secondary_hover; }
QStatusBar { background-color: $surface; border-top: 1px solid $border; }
QSplitter::handle { background-color: transparent; }

QDockWidget { color: $text; }
QDockWidget::title { background-color: $surface; padding: $p6 $p10; border-bottom: 1px solid $border; }

QTabWidget::pane { background-color: $surface; border: 1px solid $border; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; color: $muted; padding: $p6 $p14; border: none; border-bottom: 2px solid transparent; margin-right: 2px; }
QTabBar::tab:selected { color: $text; font-weight: 600; border-bottom: 2px solid $primary; }
QTabBar::tab:hover:!selected { color: $text; }

QMenu { background-color: $surface; border: 1px solid $border; border-radius: 8px; padding: $p4; }
QMenu::item { padding: $p6 $p16 $p6 $p10; border-radius: 6px; }
QMenu::item:selected { background-color: $secondary; color: $text; }
QMenu::item:disabled { color: $muted; }
QMenu::separator { height: 1px; background-color: $border; margin: $p4 $p6; }
""")

_FILLED_KINDS = (
    ("primary", "primary", "primary_hover"),
    ("success", "success", "success_hover"),
    ("danger", "danger", "danger_hover"),
    ("accent", "accent", "accent_hover"),
)


def _kind_rules(p: dict[str, str]) -> str:
    """Variantes de boutons (propriété dynamique « kind »)."""
    out: list[str] = []
    for kind, base, hover in _FILLED_KINDS:
        out.append(f'QPushButton[kind="{kind}"] {{ background-color: {p[base]}; color: #ffffff; font-weight: 600; }}')
        out.append(f'QPushButton[kind="{kind}"]:hover {{ background-color: {p[hover]}; }}')
    out.append(f'QPushButton[kind="secondary"] {{ background-color: {p["secondary"]}; color: {p["text"]}; }}')
    out.append(f'QPushButton[kind="secondary"]:hover {{ background-color: {p["secondary_hover"]}; }}')
    out.append(f'QPushButton[kind="ghost"] {{ background-color: transparent; color: {p["muted"]}; }}')
    out.append(f'QPushButton[kind="ghost"]:hover {{ background-color: {p["secondary"]}; color: {p["text"]}; }}')
    return "\n".join(out)


def build_qss(theme: str, zoom: float = 1.0) -> str:
    """Feuille de style complète pour un thème ("dark" | "light") et un zoom donnés."""
    p = palette(theme)
    z = clamp_zoom(zoom)
    mapping: dict[str, str] = dict(p)
    for n in (2, 4, 6, 8, 10, 14, 16):
        mapping[f"p{n}"] = f"{max(1, round(n * z))}px"
    qss = _QSS.substitute(mapping)
    return qss.replace("__KINDS__", _kind_rules(p))


def _build_palette(theme: str) -> QPalette:
    """Palette Qt cohérente (pour les widgets non couverts par le QSS)."""
    p = palette(theme)
    pal = QPalette()
    roles = (
        (QPalette.ColorRole.Window, "bg"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "surface_alt"),
        (QPalette.ColorRole.AlternateBase, "surface"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "secondary"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.PlaceholderText, "muted"),
        (QPalette.ColorRole.Highlight, "info_bg"),            # sélection « douce », comme PDFcraft
        (QPalette.ColorRole.HighlightedText, "text"),
        (QPalette.ColorRole.Link, "primary"),
    )
    for role, name in roles:
        pal.setColor(role, QColor(p[name]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p["muted"]))
    return pal


# ─────────────────────────────────────────────────────────────────────────────
# Gestionnaire de thème
# ─────────────────────────────────────────────────────────────────────────────
class ThemeManager(QObject):
    """Thème et zoom courants ; persiste dans Preferences et réapplique la feuille de style à chaud."""

    changed = Signal(str)                     # "dark" | "light" (émis aussi quand le zoom change)

    _instance: "ThemeManager | None" = None

    def __init__(self, prefs: Preferences | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs if prefs is not None else Preferences.instance()
        theme = self._prefs.get("theme", "dark")
        self._theme: str = theme if theme in THEMES else "dark"
        self._zoom: float = clamp_zoom(self._prefs.get("zoom", 1.0))
        self._app: QApplication | None = None
        self._base_font: QFont | None = None
        self._prefs.changed.connect(self._on_pref_changed)

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Accesseurs ──────────────────────────────────────────────────────────
    def theme(self) -> str:
        return self._theme

    def zoom(self) -> float:
        return self._zoom

    # ── Modifications ───────────────────────────────────────────────────────
    def set_theme(self, name: str) -> None:
        if name not in THEMES:
            raise ValueError(f"Thème inconnu : {name!r}")
        if name == self._theme:
            return
        self._theme = name
        self._prefs.set("theme", name)
        self._reapply()
        self.changed.emit(self._theme)

    def set_zoom(self, z: float) -> None:
        z = clamp_zoom(z)
        if abs(z - self._zoom) < 1e-9:
            return
        self._zoom = z
        self._prefs.set("zoom", z)
        self._reapply()
        self.changed.emit(self._theme)

    def toggle(self) -> None:
        self.set_theme("light" if self._theme == "dark" else "dark")

    def apply(self, app: QApplication) -> None:
        """Installe police, palette et feuille de style sur l'application."""
        self._app = app
        if self._base_font is None:
            self._base_font = QFont(app.font())      # police du système, mémorisée avant toute modification
        font = QFont(self._base_font)
        ui_family = load_ui_font()
        if ui_family:
            font.setFamily(ui_family)                # sinon : police du système, comme avant
        font.setPointSizeF(BASE_POINT_SIZE * self._zoom)
        app.setFont(font)
        app.setPalette(_build_palette(self._theme))
        app.setStyleSheet(build_qss(self._theme, self._zoom))
        log.debug("Thème appliqué : %s, zoom %.2f", self._theme, self._zoom)

    # ── Interne ─────────────────────────────────────────────────────────────
    def _reapply(self) -> None:
        if self._app is not None:
            self.apply(self._app)

    def _on_pref_changed(self, key: str, value: object) -> None:
        """Suit une modification faite ailleurs dans les préférences."""
        if key == "theme" and value in THEMES and value != self._theme:
            self._theme = str(value)
            self._reapply()
            self.changed.emit(self._theme)
        elif key == "zoom":
            z = clamp_zoom(value)
            if abs(z - self._zoom) >= 1e-9:
                self._zoom = z
                self._reapply()
                self.changed.emit(self._theme)


def token(name: str) -> str:
    """Couleur hexadécimale du token pour le thème courant (KeyError si le nom est inconnu)."""
    return palette(ThemeManager.instance().theme())[name]
