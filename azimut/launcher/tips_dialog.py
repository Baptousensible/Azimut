"""Page « Conseils » : parcours Google Photos et réflexes de prévention."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from azimut.core.module_spec import ModuleSpec
from azimut.ui import icons
from azimut.ui.glass import GlassDialog, GlassTabBar
from azimut.ui.theme import ThemeManager, scaled
from azimut.ui.widgets import Card, DialogHeader, IconLabel


class _TipsTabBar(GlassTabBar):
    """Barre de sections de Conseils, avec la capsule glissante des autres onglets."""

    def current_index(self) -> int:
        """Compatibilité avec l'ancienne barre segmentée."""
        return self.currentIndex()

    def set_current(self, index: int, *, animate: bool = True) -> None:
        """Compatibilité : la capsule suit automatiquement le changement d'onglet."""
        del animate
        if 0 <= index < self.count() and self.currentIndex() != index:
            self.setCurrentIndex(index)


class TipsPage(QWidget):
    """Conseils d'export Google Photos et précautions pour les traitements en lot."""

    TAKEOUT = 0
    PRECAUTIONS = 1
    # Largeur de lecture : la carte ne s'étire plus sur toute la page quand les lignes
    # de texte restent courtes, sans pour autant rétrécir dans une fenêtre étroite.
    CONTENT_MAX_WIDTH = 760

    def __init__(self, specs: Iterable[ModuleSpec] | None = None,
                 parent: QWidget | None = None, *, dialog_mode: bool = False) -> None:
        super().__init__(parent)
        self._dialog_mode = bool(dialog_mode)
        by_id = {spec.id: spec for spec in specs or ()}

        self.steps: list[tuple[str, str, str]] = []
        self.precautions: list[tuple[str, str]] = []
        self._tab_icon_names: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 0)
        root.setSpacing(14)
        self.header = self._build_header()
        root.addWidget(self.header)
        root.addWidget(self._build_tabs(), 0, Qt.AlignmentFlag.AlignLeft)

        self.content_stack = QStackedWidget()
        self.content_stack.setFrameShape(QFrame.Shape.NoFrame)
        self.content_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.content_stack.addWidget(self._build_takeout_page(by_id))
        self.content_stack.addWidget(self._build_precautions_page())
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TipsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.content_stack)
        # Comme « Journaux et historique » : la page laisse voir le fond de la fenêtre ;
        # seules les cartes portent la couleur de zone (surface).
        for target in (self, self.scroll, self.scroll.viewport(), self.content_stack):
            target.setAutoFillBackground(False)
        root.addWidget(self.scroll, 1)

        self._show_tab(self.TAKEOUT, animate=False)
        # Les pictogrammes sont des images teintées à la génération : une bascule de thème
        # (ou de zoom) doit les redessiner, comme le fait la barre des onglets d'outils.
        theme = ThemeManager.instance()
        theme.changed.connect(self._refresh_tab_icons)
        theme.accessibility_changed.connect(self._refresh_tab_icons)

    # ── En-tête et navigation ─────────────────────────────────────────────
    def _build_header(self) -> DialogHeader:
        """Titre de la page, avec une icône discrète dans le langage d'Azimut."""
        return DialogHeader(
            self.tr("Conseils"),
            self.tr("Préparez, traitez et vérifiez vos photos sans perdre l'original."),
            "info")

    def _build_tabs(self) -> _TipsTabBar:
        """Deux sections avec les mêmes onglets à capsule que les autres pages."""
        self.tab_switch = _TipsTabBar(animate_icons=False)
        self.tab_switch.setObjectName("TipsTabs")
        self.tab_switch.setAccessibleName(self.tr("Sections des conseils"))
        self.tab_switch.setDrawBase(False)
        self.tab_switch.setExpanding(False)
        self.tab_switch.setUsesScrollButtons(False)
        for label, icon_name in (
            (self.tr("Google Photos Takeout"), "gallery"),
            (self.tr("Précautions"), "lock"),
        ):
            self.tab_switch.addTab(
                icons.icon(icon_name, "muted", scaled(16)), label)
            self._tab_icon_names.append(icon_name)
        self.tab_switch.currentChanged.connect(self._show_tab)
        return self.tab_switch

    def _show_tab(self, index: int, *, animate: bool = True) -> None:
        """Affiche une section et synchronise la capsule et les icônes."""
        if self.tab_switch.count() == 0:
            return
        index = max(0, min(index, self.tab_switch.count() - 1))
        if self.tab_switch.currentIndex() != index:
            self.tab_switch.setCurrentIndex(index)
        self.content_stack.setCurrentIndex(index)
        del animate
        self._refresh_tab_icons()

    def _refresh_tab_icons(self, _name: str = "") -> None:
        """Retinte les pictogrammes des sections selon le thème courant et l'onglet actif."""
        if self.tab_switch.count() == 0:
            return
        index = self.tab_switch.currentIndex()
        for tab_index, icon_name in enumerate(self._tab_icon_names):
            active = tab_index == index
            self.tab_switch.setTabIcon(
                tab_index, icons.icon(icon_name, "text" if active else "muted", scaled(16)))

    # ── Pages ─────────────────────────────────────────────────────────────
    def _build_takeout_page(self, by_id: dict[str, ModuleSpec]) -> QWidget:
        """Le parcours historique, conservé dans son ordre."""
        page, layout = self._page()
        card = self._card(self.tr("De Google Photos à vos dossiers"))
        intro = QLabel(self.tr("L'ordre recommandé pour traiter un export Google Photos :"))
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        card.body_layout.addWidget(intro)

        steps: tuple[tuple[str, str], ...] = (
            ("takeout", self.tr("Demandez l'export à Google (Takeout), décompressez-le, puis réinjectez les dates et les positions GPS dans vos fichiers.")),
            ("dedup", self.tr("Écartez d'abord les doublons : les étapes suivantes seront plus rapides et plus sûres.")),
            ("convert", self.tr("Convertissez ensuite au format et à la qualité souhaités, par lots.")),
            ("rename", self.tr("Inscrivez la date de prise de vue dans les noms pour classer les fichiers chronologiquement.")),
            ("scope", self.tr("Comparez les dossiers avant et après pour vérifier que rien ne manque.")),
            ("tags", self.tr("Terminez par les métadonnées : lecture, complément ou suppression des EXIF, IPTC et XMP.")),
        )
        for number, (module_id, description) in enumerate(steps, start=1):
            spec = by_id.get(module_id)
            name = spec.name if spec is not None else module_id.capitalize()
            icon = spec.icon if spec is not None else ""
            self.steps.append((module_id, name, description))
            if number > 1:
                card.body_layout.addWidget(self._divider())
            card.body_layout.addLayout(self._step_row(number, name, description, icon))

        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_precautions_page(self) -> QWidget:
        """Les gestes simples qui protègent les fichiers pendant un traitement."""
        page, layout = self._page()
        card = self._card(self.tr("Précautions"))
        intro = QLabel(self.tr(
            "Quelques réflexes suffisent pour éviter la plupart des mauvaises surprises "
            "lors d'un traitement en lot."))
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        card.body_layout.addWidget(intro)

        precautions: tuple[tuple[str, str, str], ...] = (
            ("Toujours garder une copie",
             self.tr("Travaillez sur une copie et conservez l'original intact jusqu'à la "
                     "vérification finale. En cas d'erreur, il reste toujours récupérable."),
             "archive"),
            ("Vérifier l'espace disque",
             self.tr("Un export, ses conversions et ses sauvegardes peuvent occuper beaucoup "
                     "de place. Libérez de l'espace avant de lancer un gros lot."),
             "pie"),
            ("Tester sur un petit lot",
             self.tr("Commencez par quelques fichiers représentatifs, vérifiez le résultat, "
                     "puis traitez le reste de la bibliothèque."),
             "tasks"),
            ("Laisser la synchronisation tranquille",
             self.tr("Évitez OneDrive, iCloud ou Dropbox pendant le traitement : une "
                     "synchronisation en cours peut verrouiller ou modifier des fichiers."),
             "globe"),
            ("Ne pas interrompre brutalement",
             self.tr("Utilisez « Annuler » plutôt que de fermer l'application ou de "
                     "débrancher un disque pendant une écriture."),
             "pause"),
            ("Comparer source et destination",
             self.tr("Après une copie ou une conversion, passez Scope sur les deux dossiers "
                     "et gardez le rapport jusqu'à validation."),
             "search"),
        )
        for index, (title, description, icon_name) in enumerate(precautions):
            self.precautions.append((title, description))
            if index:
                card.body_layout.addWidget(self._divider())
            card.body_layout.addLayout(
                self._precaution_row(title, description, icon_name))

        layout.addWidget(card)
        layout.addStretch(1)
        return page

    @staticmethod
    def _card(title: str) -> Card:
        """Carte de conseils : son titre garde sa hauteur naturelle."""
        card = Card(title)
        card.setMaximumWidth(TipsPage.CONTENT_MAX_WIDTH)
        if card.title_label is not None:
            card.title_label.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return card

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("TipsPage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        return page, layout

    # ── Lignes de contenu ─────────────────────────────────────────────────
    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("TipsDivider")
        line.setFrameShape(QFrame.Shape.NoFrame)
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _badge(number: int) -> QLabel:
        """Pastille numérotée du parcours."""
        badge = QLabel(str(number))
        badge.setObjectName("TipsBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(scaled(24), scaled(24))
        return badge

    def _step_row(self, number: int, name: str, description: str, icon: str) -> QHBoxLayout:
        """Une étape : numéro, pictogramme du module, nom et explication courte."""
        row = QHBoxLayout()
        row.setSpacing(scaled(10))
        row.addWidget(self._badge(number), 0, Qt.AlignmentFlag.AlignTop)
        # Pas d'animation du pictogramme de l'outil dans Conseils.
        title = (IconLabel(name, icon, "text", animate=False)
                 if icon and icons.available(icon) else QLabel(name))
        title.setObjectName("TipsStepTitle")
        title.setWordWrap(True)
        text = QVBoxLayout()
        text.setSpacing(2)
        label = QLabel(description)
        label.setWordWrap(True)
        label.setProperty("role", "muted")
        text.addWidget(title)
        text.addWidget(label)
        row.addLayout(text, 1)
        return row

    def _precaution_row(self, title: str, description: str, icon_name: str) -> QHBoxLayout:
        """Une précaution : pictogramme dans une pastille, titre et explication."""
        row = QHBoxLayout()
        row.setSpacing(scaled(10))
        marker = QLabel()
        marker.setObjectName("TipsMarker")
        marker.setFixedSize(scaled(26), scaled(26))
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = icon_name if icons.available(icon_name) else "info"
        marker.setPixmap(icons.pixmap(icon, scaled(15), "accent"))
        row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("TipsPrecautionTitle")
        title_label.setWordWrap(True)
        label = QLabel(description)
        label.setWordWrap(True)
        label.setProperty("role", "muted")
        text.addWidget(title_label)
        text.addWidget(label)
        row.addLayout(text, 1)
        return row


class TipsDialog(GlassDialog):
    """Enveloppe de dialogue historique autour de :class:`TipsPage`.

    Azimut n'ouvre plus de fenêtre : la page est intégrée à la navigation principale.
    Cette enveloppe reste disponible pour les usages autonomes et les tests.
    """

    TAKEOUT = TipsPage.TAKEOUT
    PRECAUTIONS = TipsPage.PRECAUTIONS

    def __init__(self, specs: Iterable[ModuleSpec] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Conseils"))
        self.setModal(True)
        self.page = TipsPage(specs, self, dialog_mode=True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(self.page, 1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = box.button(QDialogButtonBox.StandardButton.Close)
        close.setText(self.tr("Fermer"))
        close.setProperty("kind", "secondary")
        box.rejected.connect(self.reject)
        root.addWidget(box)

        # Attributs pratiques, utilisés par les tests et les appelants existants.
        self.steps = self.page.steps
        self.precautions = self.page.precautions
        self.content_stack = self.page.content_stack
        self.tab_switch = self.page.tab_switch
        self.fit_to_content(self.page.scroll, min_width=680)
