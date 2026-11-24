"""Thème Azimut : palette claire/sombre, feuille de style QSS maison et gestionnaire de thème."""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from string import Template
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QFont, QGuiApplication, QPalette, QScreen
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget

from azimut.core import system_accessibility
from azimut.core.logs import get_logger
from azimut.core.prefs import Preferences
from azimut.ui.indicators import build_images

log = get_logger(__name__)

THEMES: tuple[str, ...] = ("dark", "light")
APPEARANCES: tuple[str, ...] = ("system", "dark", "light")
# Thèmes d'icônes : Papicons (historique) et Lucide Animées (lucide-animated, animé au survol).
ICON_THEMES: tuple[str, ...] = ("papicons", "animated")
ZOOM_MIN, ZOOM_MAX = 0.8, 1.6
# Crans de zoom proposés dans les préférences ; le calibrage du premier lancement y retombe.
ZOOM_STEPS: tuple[float, ...] = (0.8, 0.85, 0.9, 1.0, 1.1, 1.25, 1.5)
TARGET_BODY_MM = 2.3            # corps de texte visé au premier lancement, sur l'écran
CALIBRATION_MIN, CALIBRATION_MAX = 0.85, 1.25
BASE_POINT_SIZE = 13.0          # repli de build_qss ; apply() reprend la taille du système
MIN_POINT_SIZE = 8.0            # plancher absolu : en dessous, le texte n'est plus lisible
# Échelle typographique unique, exprimée en fraction du corps (base × zoom). Des tailles
# absolues (10 pt de libellé, plancher de 13 pt pour un titre) inversaient la hiérarchie
# selon la police du système : sous Windows (9 pt), les libellés passaient au-dessus du
# corps ; sous macOS (13 pt), le titre du panneau latéral dépassait le nom du module.
CAPTION_SIZE_RATIO = 0.8        # libellés secondaires (pastilles, titres de source…)
TITLE_SIZE_RATIO = 1.2          # nom du module et titre d'une boîte de dialogue

# Accent Azimut : orange profond de l'aiguille, décliné pour chaque fond.
# nom : (clair, sombre)
# Palette « Azimut nuit » : fonds navy, surfaces ardoise, accent orange.
_PALETTE: dict[str, tuple[str, str]] = {
    "bg": ("#cbd5df", "#0b131e"),
    "surface": ("#dde3e9", "#132338"),
    "surface_alt": ("#ced8e2", "#132338"),
    "border": ("#aebdcd", "#1e3552"),
    "text": ("#032b4c", "#f1f5f9"),
    "muted": ("#475569", "#94a3b8"),
    "primary": ("#e34b00", "#e8590c"),
    "primary_hover": ("#c2410c", "#f97316"),
    "success": ("#2f7554", "#327a55"),
    "success_hover": ("#276346", "#286b4a"),
    "danger": ("#c93b3b", "#c24141"),
    "danger_hover": ("#b13434", "#a03333"),
    "accent": ("#e34b00", "#e8590c"),
    "focus": ("#b8400a", "#e8590c"),
    "accent_hover": ("#c2410c", "#f97316"),
    "secondary": ("#ced8e2", "#1b2c45"),
    "secondary_hover": ("#b8c6d6", "#24395a"),
    # Textes des pastilles : sur leur fond clair, chaque teinte atteint 4,5:1.
    "ok_txt": ("#1f5138", "#7cc79a"),
    "warn_txt": ("#7a3a05", "#ffb27a"),
    "err_txt": ("#7d1d1d", "#ff7b7b"),
    "info_txt": ("#33484f", "#a9bdd4"),
    "ok_bg": ("#aacdba", "#17382a"),
    "warn_bg": ("#dfc5af", "#3a2618"),
    "err_bg": ("#cfaeaa", "#362222"),
    "info_bg": ("#b4c6d6", "#16283d"),
    "accent_bg": ("#dfb79c", "#3a2313"),
    # Segments de la barre de progression de Convert : une teinte par famille de fichiers.
    "seg_image": ("#2f6f9f", "#4fa3e0"),
    "seg_audio": ("#6b4c9a", "#a98bf0"),
    "seg_video": ("#c2410c", "#e8590c"),
    "seg_copy": ("#64748b", "#8ea3b8"),
}
# Variantes à contraste élevé : liserés et anneau de focus plus francs.
_HIGH_CONTRAST: dict[str, tuple[str, str]] = {
    "border": ("#94a3b8", "#54708f"),
    "focus": ("#9a3412", "#ffb067"),
}
TOKEN_NAMES: tuple[str, ...] = tuple(_PALETTE)

# ─────────────────────────────────────────────────────────────────────────────
# Liquid Glass : couches translucides, liserés et halos d'arrière-plan
# ─────────────────────────────────────────────────────────────────────────────
# Les couleurs à transparence utilisent #AARRGGBB : QColor les lit comme les feuilles
# de style Qt, ce qui permet de peindre et de styler avec les mêmes jetons.
_GLASS: dict[str, dict[str, str]] = {
    "dark": {
        "bg_top": "#0b131e",
        "bg_bottom": "#0b131e",
        "glass_panel": "#f2132338",
        "glass_panel_alt": "#ff132338",
        "glass_rail": "#fa0b131e",
        "glass_stroke": "#661e3552",
        "glass_stroke_soft": "#331e3552",
        "glass_shine": "#66ffffff",
        "glass_field": "#ff132338",
        "glass_field_top": "#132338",
        "glass_field_bottom": "#0f1c2e",
        "glass_table": "#ff132338",
        "glass_table_alt": "#ff132338",
        "glass_menu": "#fc132338",
        "glass_button_top": "#1b2c45",
        "glass_button_bottom": "#132338",
        "glass_button_hover_top": "#24395a",
        "glass_button_hover_bottom": "#1b2c45",
        "glass_button_pressed_top": "#0f1c2e",
        "glass_button_pressed_bottom": "#0b131e",
        "page_hover": "#00000000",
        "action": "#e8590c",
        "action_hover": "#f97316",
    },
    "light": {
        "bg_top": "#cbd5df",
        "bg_bottom": "#cbd5df",
        "glass_panel": "#fadde3e9",
        "glass_panel_alt": "#dde3e9",
        "glass_rail": "#f2ced8e2",
        "glass_stroke": "#66aebdcd",
        "glass_stroke_soft": "#33aebdcd",
        "glass_shine": "#ccdde3e9",
        "glass_field": "#dde3e9",
        "glass_field_top": "#dde3e9",
        "glass_field_bottom": "#cbd5df",
        "glass_table": "#dde3e9",
        "glass_table_alt": "#ced8e2",
        "glass_menu": "#fcdde3e9",
        "glass_button_top": "#dde3e9",
        "glass_button_bottom": "#cbd5df",
        "glass_button_hover_top": "#dde3e9",
        "glass_button_hover_bottom": "#c3cedb",
        "glass_button_pressed_top": "#c3cedb",
        "glass_button_pressed_bottom": "#aebdcd",
        "page_hover": "#14032b4c",
        # Orange d'action assombri : sur le fond clair, #e34b00 ne tenait que 2,7:1
        # (cases, curseurs, sélection) alors que le repère non textuel en demande 3.
        "action": "#b8400a",
        "action_hover": "#9a3000",
    },
}

# (x, y, rayon, couleur, alpha) : halos peints dans le repère de la fenêtre.
# La palette est volontairement plate : aucun halo ne vient décaler les fonds exacts.
_AURORA: dict[str, tuple[tuple[float, float, float, str, int], ...]] = {
    "dark": (),
    "light": (),
}
# (x, y, rayon, couleur, alpha) : halos peints dans le repère de la fenêtre.
_AURORA: dict[str, tuple[tuple[float, float, float, str, int], ...]] = {
    # Une seule nappe, très basse : elle donne de la profondeur au verre sans
    # remettre la couleur au premier plan. Le reste du fond est un dégradé neutre.
    "dark": ((0.28, -0.18, 0.95, "#2d8cc0", 20),),
    "light": ((0.28, -0.18, 0.95, "#2d8cc0", 18),),
}


def palette(theme: str, high_contrast: bool = False) -> dict[str, str]:
    """Couleurs (hexadécimal) de tous les tokens pour le thème donné."""
    if theme not in THEMES:
        raise ValueError(f"Thème inconnu : {theme!r}")
    idx = 1 if theme == "dark" else 0
    out = {name: pair[idx] for name, pair in _PALETTE.items()}
    if high_contrast:
        out.update({name: pair[idx] for name, pair in _HIGH_CONTRAST.items()})
    return out


def glass_style(theme: str, reduce_transparency: bool = False) -> dict[str, str]:
    """Jetons du verre (translucides, dégradés de boutons, fond) pour le thème donné.

    ``reduce_transparency`` rend opaques les surfaces en verre et coupe le reflet :
    le réglage système « Réduire la transparence » retrouve alors des plaques nettes.
    """
    if theme not in THEMES:
        raise ValueError(f"Thème inconnu : {theme!r}")
    style = dict(_GLASS[theme])
    style["bg"] = palette(theme)["bg"]         # le fond de page, pour peindre l'environnement
    if reduce_transparency:
        for key, value in style.items():
            if key.startswith("glass_") and key not in ("glass_stroke", "glass_stroke_soft") and len(value) == 9:
                style[key] = "#ff" + value[3:]
        style["glass_shine"] = "#00FFFFFF"
    return style


def aurora_layers(theme: str) -> tuple[tuple[float, float, float, str, int], ...]:
    """Halos colorés de l'arrière-plan : position et rayon relatifs, couleur, opacité."""
    if theme not in THEMES:
        raise ValueError(f"Thème inconnu : {theme!r}")
    return _AURORA[theme]


def normalize_icon_theme(value: Any) -> str:
    """Thème d'icônes valide ; toute valeur inconnue retombe sur Papicons.

    « lucide » était la clé de l'ancien thème statique : une préférence restée sur le
    disque bascule sur le thème animé qui le remplace.
    """
    name = str(value or "").strip().lower()
    if name == "lucide":
        return "animated"
    return name if name in ICON_THEMES else "papicons"


def point_size_floor(base_point_size: float) -> float:
    """Plancher du corps de texte pour une taille de système donnée.

    Le plancher fixe de 10 pt d'avant ne laissait aucune marge sous 100 % aux systèmes dont
    la police fait moins : sur Windows (9 pt), 100 %, 90 % et 80 % donnaient tous 10 pt et
    seules les marges rétrécissaient. Le plancher suit donc la taille du système sans jamais
    la dépasser, et garde un minimum absolu pour la lisibilité.
    """
    return min(MIN_POINT_SIZE, float(base_point_size))


def _screen_density(screen: QScreen | None = None) -> float | None:
    """Pixels logiques par pouce d'un écran (le principal par défaut), None si inconnue.

    Hors écran (tests, CI), la taille physique rapportée est une valeur de synthèse
    (800 × 800 px pour 203,2 mm) : calibrer dessus figerait un zoom arbitraire.
    """
    if QGuiApplication.platformName() == "offscreen":
        return None
    screen = screen if screen is not None else QGuiApplication.primaryScreen()
    if screen is None:
        return None
    width = screen.geometry().width()
    millimetres = screen.physicalSize().width()
    if width <= 0 or millimetres <= 0:
        return None
    return width / (millimetres / 25.4)


def screen_key(screen: QScreen) -> str:
    """Clé d'un écran : son nom et son facteur d'échelle.

    Le facteur fait partie de la clé : le même moniteur passé de 150 % à 200 % (ou deux
    écrans portant le même nom de modèle) doit garder deux réglages distincts.
    """
    return f"{screen.name() or 'écran'}|{float(screen.devicePixelRatio()):.2f}"


def calibrated_zoom(base_point_size: float, logical_dpi: float, density: float) -> float:
    """Zoom amenant le corps de texte à ~2,3 mm sur un écran de densité donnée.

    Le corps vaut ``base × logical_dpi / 72`` pixels logiques (conversion point → pixel de
    Qt), donc ``corps / densité × 25,4`` millimètres. La densité seule relie un pixel à une
    taille physique : la résolution en pixels, elle, ne dit rien (un 27" 4K à 200 % est
    moins dense qu'un 13" en 1440 × 900). Le résultat est borné à 0,85–1,25, puis ramené
    au cran proposé le plus proche pour que les préférences affichent la valeur réelle.
    """
    if base_point_size <= 0 or logical_dpi <= 0 or density <= 0:
        return 1.0
    body_mm = (base_point_size * logical_dpi / 72.0) / density * 25.4
    if body_mm <= 0:
        return 1.0
    wanted = max(CALIBRATION_MIN, min(CALIBRATION_MAX, TARGET_BODY_MM / body_mm))
    steps = [step for step in ZOOM_STEPS if CALIBRATION_MIN <= step <= CALIBRATION_MAX]
    return min(steps, key=lambda step: abs(step - wanted))


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
# Anneau de focus
# ─────────────────────────────────────────────────────────────────────────────
_FOCUS_RING_OBJECTS = frozenset({"Card", "ContentCard", "DropZone"})
_KEYBOARD_FOCUS_REASONS = frozenset({
    Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason,
})

# Le filtre voit *tous* les événements de l'application : on écarte les autres types
# par une seule comparaison ensembliste avant tout travail (des milliers par seconde).
_FOCUS_EVENTS = frozenset({
    QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn, QEvent.Type.FocusOut,
})


def _has_focus_ring(widget: QWidget) -> bool:
    """Widgets dont le contour réagit au focus (boutons, cartes, zones de dépôt)."""
    return (isinstance(widget, QAbstractButton)
            or widget.property("focusRing") is True
            or widget.objectName() in _FOCUS_RING_OBJECTS)


class _FocusRingFilter(QObject):
    """N'allume le contour de focus qu'après une navigation au clavier.

    Qt conserve le focus après un clic souris : sans ce filtre, la carte Azimut cliquée (ou le
    bouton qui a ouvert l'onglet) restait cerclé d'orange, comme s'il était encore la cible
    active. La raison du focus est donc consignée dans la propriété dynamique « keyboardFocus »,
    que la feuille de style consulte ; un focus repris à la souris l'efface.
    """

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind not in _FOCUS_EVENTS:
            return False
        if not isinstance(watched, QWidget):
            return False
        if kind == QEvent.Type.MouseButtonPress:
            # Widget déjà focalisé au clavier : Qt n'émet pas de nouveau FocusIn, il faut
            # donc éteindre l'anneau ici, y compris quand le clic tombe sur un enfant.
            widget: QWidget | None = watched
            while widget is not None:
                if widget.property("keyboardFocus"):
                    widget.setProperty("keyboardFocus", False)
                    repolish(widget)
                    break
                widget = widget.parentWidget()
        elif not isinstance(event, QFocusEvent):
            return False
        elif kind == QEvent.Type.FocusIn and _has_focus_ring(watched):
            keyboard = event.reason() in _KEYBOARD_FOCUS_REASONS
            if watched.property("keyboardFocus") != keyboard:
                watched.setProperty("keyboardFocus", keyboard)
                repolish(watched)
        elif kind == QEvent.Type.FocusOut and watched.property("keyboardFocus"):
            watched.setProperty("keyboardFocus", False)
            repolish(watched)
        return False


def install_focus_ring_filter(app: QApplication) -> None:
    """Installe le filtre d'anneau de focus une seule fois par application."""
    if getattr(app, "_azimut_focus_filter", None) is not None:
        return
    flt = _FocusRingFilter(app)
    app.installEventFilter(flt)
    app._azimut_focus_filter = flt


# ─────────────────────────────────────────────────────────────────────────────
# Feuille de style
# ─────────────────────────────────────────────────────────────────────────────
_QSS = Template(r"""
QWidget { color: $text; selection-background-color: $action; selection-color: #ffffff; }
QMainWindow { background: $bg_top; }
QDialog { background: $bg_top; }
QMainWindow[embedded="true"] { background: transparent; }
QLabel { background: transparent; }
QLabel#AtlasTitle { color: $text; background: transparent; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="title"] { font-weight: 600; }
QLabel#EmptyState { color: $muted; background: transparent; }

QToolTip { background-color: $bg; color: $text; border: 1px solid $glass_stroke; padding: $p4; border-radius: 8px; }

/* ── Composants Azimut ── */
QFrame#Card { background-color: transparent; border: none; border-radius: 8px; }
/* L'anneau est réservé au clavier : un clic souris ne doit pas laisser de contour qui persiste. */
QFrame#Card:focus[keyboardFocus="true"] { border-color: $focus; }
/* En Azimut, l'en-tête se fond dans la surface du panneau et rejoint la barre d'onglets. */
QFrame#Card[embedded="true"] { background: transparent; border: none; }
QFrame#Card QLabel#CardTitle { font-weight: 500; }
/* En-têtes de boîte (Conseils, Préférences) : une hiérarchie légère, icône neutre. */
QLabel#DialogTitle { font-size: $dialog_title_size; font-weight: 600; }
QLabel#DialogSubtitle { color: $muted; }
QLabel#DialogIcon { background-color: $surface; border: 1px solid $glass_stroke_soft; border-radius: 12px; }
QLabel#TipsBadge { background-color: $accent_bg; color: $text; border: 1px solid $border;
                   border-radius: 12px; font-weight: 600; }
QLabel#TipsMarker { background-color: $accent_bg; border: 1px solid $border; border-radius: 13px; }
QLabel#TipsStepTitle, QLabel#TipsPrecautionTitle { font-weight: 600; }
QFrame#TipsDivider { background-color: $border; border: none; }
QFrame#ActionBar { background-color: $surface; border: 1px solid $border; border-radius: 8px; }
QGroupBox { background-color: $surface; border: 1px solid $border; border-radius: 8px;
            margin-top: $p12; padding: $p10; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: $p10; padding: 0 $p4; color: $text; }
QGroupBox:disabled { color: $muted; border-color: $border; }
QFrame#OptionGroup { background-color: $surface; border: 1px solid $border; border-radius: 8px; }
QLabel#OptionGroupTitle { color: $muted; font-weight: 600; font-size: $caption_size; }
/* Cartes de contenu : surface opaque ; le verre reste au chrome (en-tête, onglets, menus). */
QFrame#ContentCard { background-color: $surface; border: 1px solid $border; border-radius: 8px; }
QFrame#ContentCard QLabel#CardTitle { font-weight: 500; }
QLabel#ModuleName { font-size: $module_title_size; font-weight: 500; }
QFrame#ContentCard[interactive="true"]:hover { background-color: $surface_alt; border-color: $focus; }
QFrame#ContentCard:focus[keyboardFocus="true"] { border-color: $focus; }
QFrame#DropZone { background-color: transparent; border: 1px dashed $border; border-radius: 8px; }
QFrame#DropZone[dragOver="true"] { border-color: $focus; background-color: $surface; }
QFrame#DropZone:focus[keyboardFocus="true"] { border-color: $focus; }
QFrame#DropZone QLabel { color: $muted; }
/* Les pastilles sont des étiquettes discrètes : la couleur porte le sens, pas un fond plein. */
QLabel#StatusChip { background-color: transparent; color: $muted; border: 1px solid $border;
                    border-radius: 6px; padding: $p2 $p6; font-weight: 500; font-size: $caption_size; }
QLabel#StatusChip[level="ok"] { color: $ok_txt; }
QLabel#StatusChip[level="warn"] { color: $warn_txt; }
QLabel#StatusChip[level="err"] { color: $err_txt; }
QLabel#StatusChip[level="info"] { color: $info_txt; }
QLabel#StatusChip[level="neutre"] { color: $muted; }
QLabel#StatusChip[quiet="true"] { background: transparent; border-color: transparent; padding: 0; color: $muted; }
QLabel#StatusChip[quiet="true"][level="ok"] { color: $ok_txt; }
QLabel#StatusChip[quiet="true"][level="warn"] { color: $warn_txt; }
QLabel#StatusChip[quiet="true"][level="err"] { color: $err_txt; }
QLabel#StatusChip[quiet="true"][level="info"] { color: $info_txt; }

/* ── Boutons : surfaces opaques, liseré fin ; le verre reste aux grandes surfaces ── */
/* Graisse régulière : les libellés de boutons restent légers, le fond et le liseré suffisent. */
QPushButton { background-color: $surface; color: $text; border: 1px solid $border;
              border-radius: 6px; padding: $p6 $p12; }
QPushButton:hover { background-color: $secondary; border-color: $muted; }
QPushButton:pressed { background-color: $secondary_hover; }
QPushButton:focus[keyboardFocus="true"] { border-color: $focus; }
__KINDS__
/* Barre d'outils et en-tête d'Azimut : même gabarit, sans liseré ni fond de focus. */
QToolBar QPushButton:focus { border-color: transparent; }
QPushButton#AtlasHeaderButton:focus { border-color: transparent; }
/* Navigation Azimut : le fond de sélection est la capsule peinte qui glisse (glass.py). */
QPushButton#AtlasPageButton { background: transparent; border: 1px solid transparent; color: $muted; }
QPushButton#AtlasPageButton:hover { background-color: $page_hover; border-color: transparent; color: $text; }
QPushButton#AtlasPageButton:checked { background: transparent; border-color: transparent; color: $text;
                                     font-weight: 500; }
/* Pas de fond de survol sur l'onglet actif : il resterait figé à destination pendant
   que la capsule glisse, et dépasserait d'elle jusqu'à la fin de l'animation. */
QPushButton#AtlasPageButton:checked:hover { background: transparent; color: $text; }
QPushButton#AtlasPageButton:focus[keyboardFocus="true"] { border-color: $focus; }
QPushButton#AtlasPageButton:disabled { background: transparent; border-color: transparent; color: $muted; }
QPushButton:disabled, QPushButton[kind]:disabled { background: $surface_alt; color: $muted;
                                                   border-color: $border; }
QPushButton[kind="ghost"]:disabled { background: transparent; color: $muted; }
/* Bouton d'inversion : aucune marge interne, sinon l'icône est rognée dans sa case carrée. */
QPushButton#SwapButton { padding: 0; }
/* Bouton à menu (seuil des fichiers volumineux) : chevron du thème, place réservée au texte. */
QPushButton[hasMenu="true"] { padding-right: $p32; }
QPushButton[hasMenu="true"]::menu-indicator { image: $img_chevron_down; width: 10px; height: 10px;
                                               subcontrol-origin: padding; subcontrol-position: center right;
                                               margin-right: $p6; }

/* ── Champs : surface pleine, liseré fin, focus orange ── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: $surface;
                                          color: $text; border: 1px solid $border;
                                          border-radius: 6px; padding: $p6 $p10; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: $muted; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: $focus; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: $muted; background-color: $surface_alt; border-color: $border; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: $img_chevron_down; width: 12px; height: 12px; }
/* Sélecteur numérique : boutons haut/bas explicites, sans l'artefact du style natif sous QSS. */
QSpinBox, QDoubleSpinBox { padding-right: $p32; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right; width: $p24;
    border: none; border-left: 1px solid $glass_stroke_soft;
    border-top-right-radius: 6px; background: $surface_alt; }
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right; width: $p24;
    border: none; border-left: 1px solid $glass_stroke_soft;
    border-bottom-right-radius: 6px; background: $surface_alt; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: $secondary; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: $img_chevron_up; width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: $img_chevron_down; width: 12px; height: 12px; }
QComboBox QAbstractItemView { background-color: $glass_menu; border: 1px solid $border; selection-background-color: $action; selection-color: #ffffff; outline: 0; }

QCheckBox, QRadioButton { spacing: $p8; background: transparent; }
QCheckBox:disabled, QRadioButton:disabled { color: $muted; }
/* Case décochée : cadre net sur fond sombre/clair. Cochée : aplat bleu + coche blanche. */
QCheckBox::indicator { width: $ind; height: $ind; border: 2px solid $muted; border-radius: $ind_r; background-color: $surface; }
QCheckBox::indicator:hover { border-color: $action; }
QCheckBox::indicator:checked { background-color: $action; border-color: $action; image: $img_check; }
QCheckBox::indicator:checked:hover { background-color: $action_hover; border-color: $action_hover; }
QCheckBox::indicator:disabled { border-color: $border; background-color: $surface; }
QCheckBox::indicator:checked:disabled { background-color: $border; border-color: $border; image: $img_check; }
QRadioButton::indicator { width: $ind; height: $ind; border: 2px solid $muted; border-radius: $ind_radio; background-color: $surface_alt; }
QRadioButton::indicator:hover { border-color: $action; }
QRadioButton::indicator:checked { border-color: $action; image: $img_radio; }
QRadioButton::indicator:disabled { border-color: $border; background-color: $surface; }

/* Curseur : piste fine et poignée ronde plus petite, entièrement contenue dans la hauteur du widget. */
QSlider { min-height: 20px; }
QSlider::groove:horizontal { height: 4px; background-color: $border; border-radius: 2px; }
QSlider::sub-page:horizontal { background-color: $action; border-radius: 2px; }
QSlider::handle:horizontal { background-color: $surface; border: 2px solid $action; width: 10px; height: 10px; margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { background-color: $action; }
QSlider::groove:horizontal:disabled { background-color: $surface_alt; }
QSlider::sub-page:horizontal:disabled { background-color: $muted; }
QSlider::handle:horizontal:disabled { border-color: $muted; background-color: $surface_alt; }
QSlider::groove:vertical { width: 4px; background-color: $border; border-radius: 2px; }
/* En vertical, « add-page » est la portion sous la poignée : c'est elle qui doit se remplir. */
QSlider::add-page:vertical { background-color: $action; border-radius: 2px; }
QSlider::sub-page:vertical { background-color: transparent; }
QSlider::handle:vertical { background-color: $surface; border: 2px solid $action; width: 10px; height: 10px; margin: 0 -5px; border-radius: 7px; }
QSlider::handle:vertical:hover { background-color: $action; }
QSlider::groove:vertical:disabled { background-color: $surface_alt; }
QSlider::add-page:vertical:disabled { background-color: $muted; }
QSlider::handle:vertical:disabled { border-color: $muted; background-color: $surface_alt; }

QProgressBar { background-color: $surface_alt; border: 1px solid $border; border-radius: 6px;
               min-height: 12px; max-height: 12px; text-align: center; color: $text; }
QProgressBar::chunk { background-color: $action; border-radius: 6px; }
QWidget#ProgressStrip { background-color: $surface; border: 1px solid $border; border-radius: 8px; }

/* ── Tableaux et arbres : surfaces opaques, lisibles au premier plan ── */
QTableView, QTreeView, QListView { background-color: $surface; alternate-background-color: $surface_alt;
                                   border: 1px solid $border; border-radius: 8px;
                                   gridline-color: $border; outline: 0; }
QTableView::item, QTreeView::item { padding: $p2 $p4; }
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover { background-color: $secondary; }
QTableView:disabled, QTreeView:disabled, QListView:disabled { color: $muted; }
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected { background-color: $action; color: #ffffff; }
/* Arborescence « Format et qualité » (Azimut Convert) : fond transparent dans la carte, chevrons dessinés */
QTreeView#CodecTree { background-color: transparent; border: none; }
QTreeView#CodecTree::item { padding: 0; }
QTreeView#CodecTree::item:hover, QTreeView#CodecTree::item:selected { background-color: transparent; }
QTreeView#CodecTree::branch { background-color: transparent; }
QTreeView#CodecTree::branch:has-children:closed { image: $img_chevron_right; }
QTreeView#CodecTree::branch:has-children:open { image: $img_chevron_down; }
/* Comparaison de Scope : un seul arbre, fonds de ligne peints par la vue selon l'état du dossier. */
QTreeView#ScopeTree { background-color: $surface; border: 1px solid $border; border-radius: 8px;
                       selection-background-color: transparent; selection-color: $text; }
QTreeView#ScopeTree::item { padding: 0; background: transparent; }
QTreeView#ScopeTree::item:selected, QTreeView#ScopeTree::item:hover { background: transparent; }
QTreeView#ScopeTree::branch, QTreeView#ScopeTree::branch:selected { background: transparent; }
QTreeView#ScopeTree::branch:has-children:closed { image: $img_chevron_right; }
QTreeView#ScopeTree::branch:has-children:open { image: $img_chevron_down; }
QTreeView#ScopeTree QHeaderView::section { background-color: $secondary; color: $text; padding: $p6 $p8;
                                           border: none; border-bottom: 1px solid $border; font-weight: 500; }
QFrame#ScopeSources { background-color: $surface; border: 1px solid $border; border-radius: 8px; }
QFrame#ScopeSources QLabel { background: transparent; }
QLabel#SourceTitle { color: $muted; font-size: $caption_size; font-weight: 500; }
QLabel#SourceName { font-weight: 500; }
QFrame#ScopeEmpty { background-color: $surface; border: 1px solid $border; border-radius: 8px; }
QFrame#ScopeEmpty QLabel { background: transparent; }
QHeaderView::section { background-color: $secondary; color: $text; padding: $p6 $p8; border: none;
                       border-right: 1px solid $border; border-bottom: 1px solid $border;
                       font-weight: 500; }
QTableCornerButton::section { background-color: $secondary; border: none; }

QPlainTextEdit, QTextEdit { background-color: $surface; color: $text; border: 1px solid $border;
                            border-radius: 8px; padding: $p4; }

QScrollArea { background: transparent; border: none; }
/* Barres de défilement : rail et poignée en capsules, bords arrondis comme les cartes Azimut. */
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background-color: $border; border-radius: 4px; min-height: 32px; margin: 2px; }
QScrollBar::handle:vertical:hover { background-color: $muted; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background-color: $border; border-radius: 4px; min-width: 32px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background-color: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ── Barres, docks, onglets, menus ── */
QToolBar { background-color: $surface_alt; border: none; border-bottom: 1px solid $border;
           spacing: $p6; padding: $p4; }
QMenuBar { background-color: $surface_alt; color: $text; border-bottom: 1px solid $border; }
QMenuBar::item { background: transparent; padding: $p4 $p8; }
QMenuBar::item:selected { background-color: $secondary; }
QToolBar::separator { background-color: $glass_stroke; width: 1px; margin: $p4 $p6; }
QToolButton { background: transparent; color: $text; border: none; border-radius: 6px; padding: $p6; }
QToolButton:hover { background-color: $secondary; }
QToolButton#TabCloseButton { background: transparent; border: none; border-radius: 8px; padding: 0; }
QToolButton#TabCloseButton:hover { background-color: $surface_alt; }
QStatusBar { background-color: $surface_alt; border-top: 1px solid $border; }
QSplitter::handle { background-color: transparent; }

QDockWidget { color: $text; }
QDockWidget::title { background-color: $glass_rail; padding: $p6 $p10; border-bottom: 1px solid $border; }
QDockWidget#SideDock { background: transparent; border: none; }
QDockWidget#SideDock::title { background: transparent; border: none; padding: 0; margin: 0; }
QWidget#SideDockTitleBar { background: transparent; }
QLabel#SideDockTitle { background-color: $surface_alt; border: 1px solid $border; border-radius: 8px;
                       padding: $p6 $p10; font-size: $side_title_size; font-weight: 600; }

QTabWidget::pane { background: transparent; border: none; }
/* Azimut : la zone des outils est volontairement sans cadre (les cartes gardent leur contour). */
QTabWidget#ToolsTabs::pane { border: none; }
QTabWidget#ToolsTabs::tab-bar { left: 8px; }
QTabWidget#ToolsTabs QTabBar { background: transparent; }
QTabWidget#ToolsTabs QTabBar::tab {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    margin-top: $p8;
    padding: $p6 $p10;                 /* onglet de module : un cran sous la navigation de page */
}
/* La capsule de sélection est peinte par GlassTabBar, sous le texte de l'onglet. */
QTabWidget#ToolsTabs QTabBar::tab:selected {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}
/* Pas de fond au survol dans la marge haute : l'espace avec la barre de pages reste net. */
QTabWidget#ToolsTabs QTabBar::tab:hover:!selected {
    background: transparent;
    border-color: transparent;
}
/* Journaux et historique : mêmes onglets à capsule glissante que les modules. */
QTabWidget#ActivityTabs::pane { border: none; }
QTabWidget#ActivityTabs::tab-bar { left: 8px; }        /* léger retrait sous l'onglet mère, comme les modules */
QTabWidget#ActivityTabs QTabBar { background: transparent; }
QTabWidget#ActivityTabs QTabBar::tab {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    margin-top: $p8;                   /* place de la capsule, comme les onglets de modules */
    padding: $p6 $p10;
}
/* La capsule de sélection est peinte par GlassTabBar, sous le texte de l'onglet. */
QTabWidget#ActivityTabs QTabBar::tab:selected {
    background: transparent;
    border-color: transparent;
}
/* Conseils : mêmes onglets à capsule que les modules et l'historique. */
QTabBar#TipsTabs { background: transparent; }
QTabBar#TipsTabs::tab {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    margin-top: $p8;
    padding: $p6 $p10;
    color: $muted;
}
QTabBar#TipsTabs::tab:selected {
    background: transparent;
    border-color: transparent;
    color: $text;
}
QTabBar#TipsTabs::tab:hover:!selected {
    background: transparent;
    border-color: transparent;
    color: $text;
}
/* $p10 horizontal : la largeur des onglets est calculée avec la même valeur (voir _ToolsTabBar). */
QTabBar::tab { background: transparent; color: $muted; padding: $p6 $p10; border: 1px solid transparent;
               border-radius: 6px; margin-right: 3px; }
QTabBar::tab:selected { color: $text; background-color: $surface; border-color: $border; }
QTabBar::tab:hover:!selected { color: $text; background-color: $secondary; border-color: $border; }

QMenu { background-color: $glass_menu; border: 1px solid $glass_stroke_soft; border-radius: 8px; padding: $p4; }
QMenu::item { padding: $p6 $p16 $p6 $p10; border-radius: 7px; }
QMenu::item:selected { background-color: $action; color: #ffffff; }
QMenu::item:disabled { color: $muted; }
QMenu::separator { height: 1px; background-color: $glass_stroke_soft; margin: $p4 $p6; }
""")

_FILLED_KINDS = (
    ("primary", "action", "action_hover"),
    ("success", "success", "success_hover"),
    ("danger", "danger", "danger_hover"),
    ("accent", "accent", "accent_hover"),
)


def _kind_rules(p: dict[str, str], g: dict[str, str]) -> str:
    """Variantes de boutons (propriété dynamique « kind »)."""
    out: list[str] = []
    colors = {**p, **g}
    for kind, base, hover in _FILLED_KINDS:
        # Orange : encre foncée plutôt que blanc (le blanc manque de contraste sur l'accent).
        ink = "#10171a" if kind in ("primary", "accent") else "#ffffff"
        base_color, hover_color = colors[base], colors[hover]
        pressed = QColor(base_color).darker(112).name()
        out.append(f'QPushButton[kind="{kind}"] {{ background-color: {base_color}; color: {ink}; '
                   f'border-color: {base_color}; }}')
        out.append(f'QPushButton[kind="{kind}"]:hover {{ background-color: {hover_color}; '
                   f'border-color: {hover_color}; }}')
        out.append(f'QPushButton[kind="{kind}"]:pressed {{ background-color: {pressed}; }}')
    out.append(f'QPushButton[kind="secondary"] {{ background-color: {p["surface"]}; '
               f'color: {p["text"]}; border-color: {p["border"]}; }}')
    out.append(f'QPushButton[kind="secondary"]:hover {{ background-color: {p["secondary"]}; '
               f'border-color: {p["muted"]}; }}')
    out.append(f'QPushButton[kind="ghost"] {{ background: transparent; color: {p["muted"]}; '
               'border-color: transparent; }')
    out.append(f'QPushButton[kind="ghost"]:hover {{ background-color: {p["secondary"]}; color: {p["text"]}; '
               f'border-color: {p["border"]}; }}')
    # Bouton d'état (journal affiché) : même surface qu'au survol, maintenue enfoncée.
    out.append(f'QPushButton[kind="ghost"]:checked {{ background-color: {p["secondary"]}; '
               f'color: {p["text"]}; border-color: {p["border"]}; }}')
    out.append(f'QPushButton[kind="ghost"]:checked:hover {{ background-color: {p["secondary_hover"]}; '
               f'color: {p["text"]}; border-color: {p["muted"]}; }}')
    return "\n".join(out)


def build_qss(theme: str, zoom: float = 1.0, *,
              high_contrast: bool = False, reduce_transparency: bool = False,
              base_point_size: float = BASE_POINT_SIZE) -> str:
    """Feuille de style complète pour un thème, un zoom et les réglages d'accessibilité.

    ``base_point_size`` est la taille de corps du système : 100 % la restitue telle
    quelle, sans l'agrandir artificiellement.
    """
    p = palette(theme, high_contrast)
    g = glass_style(theme, reduce_transparency)
    z = clamp_zoom(zoom)
    base = max(MIN_POINT_SIZE, float(base_point_size))
    floor = point_size_floor(base)
    mapping: dict[str, str] = dict(p)
    mapping.update(g)
    for n in (2, 4, 6, 8, 10, 12, 14, 16, 24, 32):
        mapping[f"p{n}"] = f"{max(1, round(n * z))}px"
    indicator = max(12, round(16 * z))
    mapping["ind"] = f"{indicator}px"                     # cases et radios suivent le zoom
    mapping["ind_r"] = f"{max(3, round(indicator * 0.31))}px"
    mapping["ind_radio"] = f"{indicator // 2}px"
    # Tout dérive du corps réel (base × zoom) : les rapports restent identiques d'une
    # plateforme à l'autre, seule l'échelle change.
    body = max(floor, base * z)
    title = max(floor, round(body * TITLE_SIZE_RATIO, 1))
    mapping["module_title_size"] = f"{title:g}pt"       # nom du module : rang de titre
    mapping["dialog_title_size"] = f"{title:g}pt"
    mapping["side_title_size"] = f"{body:g}pt"          # « Réglages » : rang du corps
    mapping["caption_size"] = f"{max(floor, round(body * CAPTION_SIZE_RATIO, 1)):g}pt"
    images = build_images(theme, p)
    for name in ("check", "radio", "chevron_right", "chevron_down", "chevron_up"):
        mapping[f"img_{name}"] = f'url("{images[name]}")' if name in images else "none"
    qss = _QSS.substitute(mapping)
    return qss.replace("__KINDS__", _kind_rules(p, g))


def _build_palette(theme: str, high_contrast: bool = False,
                   reduce_transparency: bool = False) -> QPalette:
    """Palette Qt cohérente (pour les widgets non couverts par le QSS)."""
    p = palette(theme, high_contrast)
    pal = QPalette()
    roles = (
        (QPalette.ColorRole.Window, "bg_top"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "surface_alt"),
        (QPalette.ColorRole.AlternateBase, "surface"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "secondary"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.ToolTipBase, "glass_menu"),
        (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.PlaceholderText, "muted"),
        (QPalette.ColorRole.Highlight, "action"),
        (QPalette.ColorRole.Link, "action"),
    )
    merged = {**p, **glass_style(theme, reduce_transparency)}
    for role, name in roles:
        pal.setColor(role, QColor(merged[name]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p["muted"]))
    return pal


# ─────────────────────────────────────────────────────────────────────────────
# Gestionnaire de thème
# ─────────────────────────────────────────────────────────────────────────────
class ThemeManager(QObject):
    """Thème et zoom courants ; réapplique la feuille de style à chaud.

    Le zoom (avec les réglages d'accessibilité) et l'apparence choisie sont mémorisés :
    « system » suit le bureau, « Sombre » et « Clair » sont repris au lancement suivant.
    """

    changed = Signal(str)                     # "dark" | "light" (émis aussi quand le zoom change)
    zoomChanging = Signal(float)              # avant l'application d'un nouveau zoom
    accessibility_changed = Signal()          # animation, transparence ou contraste modifiés

    _instance: "ThemeManager | None" = None

    def __init__(self, prefs: Preferences | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs if prefs is not None else Preferences.instance()
        self._appearance: str = self._stored_appearance()
        self._theme: str = self._resolve(self._appearance)
        self._icon_theme: str = normalize_icon_theme(self._prefs.get("icon_theme", "papicons"))
        # Une préférence explicite gagne ; sinon, on reprend le réglage du système.
        self._explicit: dict[str, bool] = {}
        self._flags: dict[str, bool] = {
            "reduce_motion": self._initial_flag("reduce_motion", system_accessibility.reduce_motion),
            "reduce_transparency": self._initial_flag(
                "reduce_transparency", system_accessibility.reduce_transparency),
            "high_contrast": self._initial_flag("high_contrast", system_accessibility.high_contrast),
        }
        self._last_accessibility_check = 0.0
        self._state_app: QApplication | None = None
        self._app: QApplication | None = None
        self._base_font: QFont | None = None
        # Zoom solidaire de l'écran : chaque écran garde le sien (voir `zoom_for`).
        self._screen_key: str | None = None
        # Après `_base_font` : le calibrage du premier lancement lit la taille du système.
        self._zoom: float = self._initial_zoom()
        self._scheme_hints: object | None = None
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

    def _stored_appearance(self) -> str:
        """Apparence enregistrée ; absente ou inconnue, elle revient à « system ».

        Une préférence « theme » écrite par une version antérieure est relue telle quelle :
        ses valeurs « dark » et « light » sont exactement celles d'aujourd'hui.
        """
        value = str(self._prefs.get("theme", "system") or "system").strip().lower()
        return value if value in APPEARANCES else "system"

    def _initial_zoom(self) -> float:
        """Zoom de départ : celui mémorisé par l'écran principal, sinon son calibrage.

        Le calibrage n'a lieu qu'une fois par écran (aucun réglage enregistré pour lui) et
        son résultat est mémorisé : il n'est plus recalculé ensuite. Sans écran mesurable,
        rien n'est figé et le repli est 100 %.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 1.0
        self._screen_key = screen_key(screen)
        return self.zoom_for(screen)

    def zoom_for(self, screen: QScreen) -> float:
        """Zoom mémorisé pour cet écran ; à défaut, son calibrage (une seule fois)."""
        key = screen_key(screen)
        saved = self._saved_zooms()
        if key in saved:
            return saved[key]
        legacy = self._legacy_zoom()
        if legacy is not None:
            # Migration : l'ancienne valeur globale devient celle de l'écran courant.
            self._remember_zoom(key, legacy)
            return legacy
        density = _screen_density(screen)
        if density is None:
            return 1.0                        # écran non mesurable : ne rien figer
        zoom = calibrated_zoom(self.base_point_size(), screen.logicalDotsPerInch(), density)
        self._remember_zoom(key, zoom)
        self._prefs.set("zoom", zoom)         # miroir : dernière valeur appliquée
        return zoom

    def activate_screen(self, screen: QScreen | None) -> None:
        """Adopte le zoom de l'écran où se trouve la fenêtre (à chaque changement d'écran).

        Chaque écran a son réglage : passer la fenêtre sur un second moniteur reprend le
        zoom mémorisé pour lui, au lieu de garder une valeur globale unique.
        """
        if screen is None:
            return
        key = screen_key(screen)
        if key == self._screen_key:
            return
        self._screen_key = key
        self._set_zoom_value(self.zoom_for(screen))

    def _saved_zooms(self) -> dict[str, float]:
        """Zooms mémorisés par écran (lecture tolérante : une entrée illisible est ignorée)."""
        raw = self._prefs.get("zoom_by_screen", {})
        if not isinstance(raw, dict):
            return {}
        saved: dict[str, float] = {}
        for key, value in raw.items():
            try:
                saved[str(key)] = clamp_zoom(value)
            except (TypeError, ValueError):
                continue
        return saved

    def _remember_zoom(self, key: str | None, zoom: float) -> None:
        if key is None:
            return
        saved = self._saved_zooms()
        zoom = clamp_zoom(zoom)
        if key in saved and abs(saved[key] - zoom) < 1e-9:
            return                            # déjà enregistré : ne pas réécrire le disque
        saved[key] = zoom
        self._prefs.set("zoom_by_screen", saved)

    def _legacy_zoom(self) -> float | None:
        """Ancienne préférence globale, lue une seule fois pour migrer vers le par écran."""
        if self._prefs.contains("zoom_by_screen") or not self._prefs.contains("zoom"):
            return None
        return clamp_zoom(self._prefs.get("zoom", 1.0))

    def _set_zoom_value(self, z: float) -> None:
        z = clamp_zoom(z)
        if abs(z - self._zoom) < 1e-9:
            return
        # Certaines fenêtres doivent se replier avant que la nouvelle police ne
        # impose une largeur minimale plus grande (voir AtlasWindow).
        self.zoomChanging.emit(z)
        self._zoom = z
        self._reapply()
        self.changed.emit(self._theme)

    def base_point_size(self) -> float:
        """Taille de corps du système (avant application du zoom), plancher absolu seul.

        La taille du système est reprise telle quelle : la relever à 10 pt retirait toute
        marge de zoom aux systèmes à 9 pt (voir ``point_size_floor``).
        """
        if self._base_font is not None:
            return max(MIN_POINT_SIZE, self._base_font.pointSizeF())
        app = QGuiApplication.instance()
        if app is not None:
            return max(MIN_POINT_SIZE, app.font().pointSizeF())
        return max(MIN_POINT_SIZE, BASE_POINT_SIZE)

    def appearance(self) -> str:
        """Choix persisté : « system », « dark » ou « light »."""
        return self._appearance

    def icon_theme(self) -> str:
        """Thème d'icônes persisté : « papicons » ou « animated »."""
        return self._icon_theme

    def reduce_motion(self) -> bool:
        return self._flags["reduce_motion"]

    def reduce_transparency(self) -> bool:
        return self._flags["reduce_transparency"]

    def high_contrast(self) -> bool:
        return self._flags["high_contrast"]

    # ── Modifications ───────────────────────────────────────────────────────
    def set_theme(self, name: str) -> None:
        """Choisit l'apparence ; « system » suit le réglage du système, et le reste est retenu.

        Le choix est enregistré : sombre ou clair est repris au lancement suivant.
        """
        if name not in APPEARANCES:
            raise ValueError(f"Apparence inconnue : {name!r}")
        resolved = self._resolve(name)
        changed = name != self._appearance or resolved != self._theme
        self._appearance = name
        self._theme = resolved
        # Enregistré avant la réapplication : la préférence reste la source de vérité, et
        # le signal qu'elle émet ne reboucle pas (l'apparence est déjà à jour).
        if str(self._prefs.get("theme", "system") or "system") != name:
            self._prefs.set("theme", name)
        if not changed:
            return
        self._reapply()
        self.changed.emit(self._theme)

    def set_reduce_motion(self, enabled: bool) -> None:
        self._explicit["reduce_motion"] = True
        self._flags["reduce_motion"] = bool(enabled)
        self._prefs.set("reduce_motion", bool(enabled))

    def set_reduce_transparency(self, enabled: bool) -> None:
        self._explicit["reduce_transparency"] = True
        self._flags["reduce_transparency"] = bool(enabled)
        self._prefs.set("reduce_transparency", bool(enabled))

    def set_high_contrast(self, enabled: bool) -> None:
        self._explicit["high_contrast"] = True
        self._flags["high_contrast"] = bool(enabled)
        self._prefs.set("high_contrast", bool(enabled))

    def set_icon_theme(self, name: str) -> None:
        """Change le thème d'icônes à chaud et le mémorise pour les prochains lancements."""
        theme = normalize_icon_theme(name)
        if theme == self._icon_theme:
            return
        self._icon_theme = theme
        self._prefs.set("icon_theme", theme)
        from azimut.ui import icons               # import tardif : icons lit ThemeManager
        icons.clear_cache()
        self.changed.emit(self._theme)

    def set_zoom(self, z: float) -> None:
        """Choisit le zoom, mémorisé pour l'écran d'où vient le choix."""
        z = clamp_zoom(z)
        self._remember_zoom(self._screen_key, z)
        if abs(z - self._zoom) < 1e-9:
            return
        self._prefs.set("zoom", z)            # miroir : dernière valeur appliquée
        self._set_zoom_value(z)

    def toggle(self) -> None:
        self.set_theme("light" if self._theme == "dark" else "dark")

    def apply(self, app: QApplication) -> None:
        """Installe police, palette et feuille de style sur l'application."""
        self._app = app
        self._theme = self._resolve(self._appearance)
        if self._base_font is None:
            self._base_font = QFont(app.font())      # police du système, mémorisée avant toute modification
        base = self.base_point_size()
        font = QFont(self._base_font)
        # 100 % = taille du système ; le plancher suit cette taille (voir point_size_floor).
        font.setPointSizeF(max(point_size_floor(base), base * self._zoom))
        high_contrast = self.high_contrast()
        reduce_transparency = self.reduce_transparency()
        app.setPalette(_build_palette(self._theme, high_contrast, reduce_transparency))
        install_focus_ring_filter(app)
        # La police vient *avant* la feuille de style : le moteur de style de Qt fige la
        # police des widgets qu'il re-polit. Appliquée après, la nouvelle taille n'était
        # reprise que par une partie des textes ; les autres gardaient le zoom précédent
        # jusqu'au changement suivant (infobulle, boutons des cartes, onglets).
        app.setFont(font)
        app.setStyleSheet(build_qss(self._theme, self._zoom, high_contrast=high_contrast,
                                    reduce_transparency=reduce_transparency,
                                    base_point_size=base))
        # La feuille de style fait repartir de la police *par défaut* les widgets créés
        # après elle (13 pt sous macOS, quel que soit le zoom). La police doit donc être
        # la dernière chose posée : sans ce rappel, une fenêtre construite au lancement —
        # presque tout le texte de l'interface — apparaissait à 100 % et ne prenait le
        # zoom qu'au changement suivant (AZ-C12).
        app.setFont(font)
        self._watch_system_appearance(app)
        if self._state_app is not app:
            app.applicationStateChanged.connect(self._on_app_state)
            self._state_app = app
        log.debug("Thème appliqué : %s (choix : %s), zoom %.2f", self._theme, self._appearance, self._zoom)

    # ── Interne ─────────────────────────────────────────────────────────────
    def _initial_flag(self, key: str, detector: Callable[[], bool | None]) -> bool:
        """Valeur initiale : préférence explicite, sinon réglage du système."""
        self._explicit[key] = self._prefs.contains(key)
        if self._explicit[key]:
            return bool(self._prefs.get(key, False))
        system_value = detector()
        return bool(system_value) if system_value is not None else False

    def refresh_accessibility(self, *, force: bool = False) -> None:
        """Relit les réglages système non figés par une préférence explicite."""
        now = time.monotonic()
        if not force and now - self._last_accessibility_check < 2.0:
            return
        self._last_accessibility_check = now
        detectors = {
            "reduce_motion": system_accessibility.reduce_motion,
            "reduce_transparency": system_accessibility.reduce_transparency,
            "high_contrast": system_accessibility.high_contrast,
        }
        changed = False
        for key, detector in detectors.items():
            if self._explicit.get(key, False):
                continue
            value = detector()
            enabled = bool(value) if value is not None else False
            if enabled != self._flags[key]:
                self._flags[key] = enabled
                changed = True
        if changed:
            self._reapply()
            self.accessibility_changed.emit()

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        """Au retour dans l'app, reprendre les réglages changés dans le système."""
        if state == Qt.ApplicationState.ApplicationActive:
            self.refresh_accessibility()
            self._refresh_system_scheme()

    def _refresh_system_scheme(self) -> None:
        """Suit le thème du bureau même quand Qt ne signale pas le changement (Linux)."""
        if self._appearance != "system":
            return
        resolved = self._resolve("system")
        if resolved != self._theme:
            self._theme = resolved
            self._reapply()
            self.changed.emit(self._theme)

    def _reapply(self) -> None:
        if self._app is not None:
            self.apply(self._app)

    def _resolve(self, appearance: str) -> str:
        """Thème effectif : le choix explicite, sinon la préférence système."""
        if appearance in THEMES:
            return appearance
        app = QGuiApplication.instance()
        if app is None:
            return "dark"                       # avant QApplication : repli neutre
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        detected = system_accessibility.color_scheme()
        if detected in THEMES:
            return detected
        return "dark"                          # inconnu (offscreen, certains Linux) : repli historique

    def _watch_system_appearance(self, app: QApplication) -> None:
        """Réagit au basculement clair/sombre du système quand l'apparence est « system »."""
        hints = app.styleHints()
        if getattr(self, "_scheme_hints", None) is hints:
            return
        try:
            hints.colorSchemeChanged.connect(self._on_system_scheme)
        except (AttributeError, RuntimeError) as exc:      # Qt < 6.5
            log.debug("Signal colorSchemeChanged indisponible : %s", exc)
        self._scheme_hints = hints

    def _on_system_scheme(self, _scheme: object) -> None:
        self._refresh_system_scheme()

    def _on_pref_changed(self, key: str, value: object) -> None:
        """Suit l'apparence, le zoom et l'accessibilité modifiés ailleurs dans les préférences."""
        if key == "theme":
            self.set_theme(str(value or "system"))
        elif key == "zoom":
            z = clamp_zoom(value)
            if abs(z - self._zoom) >= 1e-9:
                self._zoom = z
                self._reapply()
                self.changed.emit(self._theme)
        elif key == "icon_theme":
            theme = normalize_icon_theme(value)
            if theme != self._icon_theme:
                self._icon_theme = theme
                from azimut.ui import icons
                icons.clear_cache()
                self.changed.emit(self._theme)
        elif key in self._flags:
            self._explicit[key] = True
            self._flags[key] = bool(value)
            self._reapply()
            self.accessibility_changed.emit()


def token(name: str) -> str:
    """Couleur hexadécimale du token pour le thème courant (KeyError si le nom est inconnu)."""
    manager = ThemeManager.instance()
    return palette(manager.theme(), manager.high_contrast())[name]


def caption_point_size() -> float:
    """Taille des libellés secondaires : une fraction du corps, jamais sous le plancher."""
    manager = ThemeManager.instance()
    floor = point_size_floor(manager.base_point_size())
    body = max(floor, manager.base_point_size() * manager.zoom())
    return max(floor, round(body * CAPTION_SIZE_RATIO, 1))


def action_color() -> str:
    """Encre des actions et sélections : jeton de verre, hors palette historique testée."""
    return glass_style(ThemeManager.instance().theme())["action"]


def scaled(px: float) -> int:
    """Taille logique mise à l'échelle du zoom courant (icônes, indicateurs, marges d'image)."""
    return max(1, round(px * ThemeManager.instance().zoom()))


def system_text_scale() -> float:
    """Facteur de la taille de texte du système par rapport au corps de référence.

    Les tailles exprimées en pixels (lettrage du titre) ne suivent pas la taille de
    corps choisie dans le système : ce facteur les y raccorde, sans réappliquer le
    zoom que `scaled` prend déjà en charge. Il ne descend jamais sous 1 : un système
    à 9 pt n'a pas à rapetisser le logotype sous sa taille de dessin.
    """
    return max(1.0, ThemeManager.instance().base_point_size() / BASE_POINT_SIZE)
