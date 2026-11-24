"""Boîte de préférences : langue, thème, taille du texte, chemins personnalisés des binaires."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from azimut.core import binaries, i18n
from azimut.core.binaries import BinaryInfo
from azimut.core.logs import get_logger
from azimut.core.prefs import Preferences
from azimut.ui.controls import SegmentedControl, SwitchRow
from azimut.ui.glass import GlassDialog
from azimut.ui.theme import ZOOM_STEPS, ThemeManager, scaled
from azimut.ui.widgets import Card, DialogHeader, PathField, StatusChip, plain_tooltip

log = get_logger(__name__)

# Crans partagés avec le calibrage du premier lancement (theme.ZOOM_STEPS).
ZOOM_CHOICES: tuple[float, ...] = ZOOM_STEPS
_DETECT_TIMEOUT_MS = 35_000        # 3 sondes de 10 s au pire, plus la vérification du chemin saisi

# Clés stables des origines de binaires ; les libellés sont traduits à l'affichage.
SOURCE_KEYS: tuple[str, ...] = ("bundled", "custom", "system", "missing")


def pending_suffix() -> str:
    """Mention ajoutée aux langues dont le catalogue n'existe pas encore."""
    return QCoreApplication.translate("PreferencesDialog", " — traduction à venir")


def source_labels() -> dict[str, str]:
    """Libellés traduits des origines de binaires (embarqué, personnalisé, système, introuvable)."""
    return {
        "bundled": QCoreApplication.translate("PreferencesDialog", "embarqué"),
        "custom": QCoreApplication.translate("PreferencesDialog", "personnalisé"),
        "system": QCoreApplication.translate("PreferencesDialog", "système"),
        "missing": QCoreApplication.translate("PreferencesDialog", "introuvable"),
    }


def describe_binary(info: BinaryInfo) -> tuple[str, str, str]:
    """(texte de pastille, niveau, infobulle) pour un binaire détecté, obsolète ou manquant."""
    if not info.available:
        return (QCoreApplication.translate("PreferencesDialog", "✖ introuvable"), "err",
                QCoreApplication.translate("PreferencesDialog", "{name} est introuvable : indiquez son chemin ou installez-le.").format(name=info.name))
    label = source_labels().get(info.source, info.source)
    if info.outdated:
        minimum = ".".join(str(x) for x in binaries.MIN_VERSIONS.get(info.name, ()))
        text = QCoreApplication.translate("PreferencesDialog", "⚠ {source} · {version}").format(source=label, version=info.version or "?")
        tip = QCoreApplication.translate(
            "PreferencesDialog",
            "Version {version} antérieure au minimum recommandé ({minimum}) ; mettez-le à jour. "
            "Chemin : {path}").format(version=info.version or "?", minimum=minimum, path=info.path)
        return text, "warn", tip
    text = QCoreApplication.translate("PreferencesDialog", "✔ {source}").format(source=label) + (f" · {info.version}" if info.version else "")
    return text, "ok", str(info.path)


class PreferencesPage(QWidget):
    """Langue, thème, taille du texte et chemins de ffmpeg / ffprobe / exiftool."""

    # Même largeur de lecture que la page Conseils : les cartes ne s'étirent pas sur
    # toute la largeur d'un grand écran quand leur contenu reste compact.
    CONTENT_MAX_WIDTH = 760

    saved = Signal()
    restartRequested = Signal()
    accepted = Signal()
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None, *, allow_restart: bool = False,
                 dialog_mode: bool = False) -> None:
        super().__init__(parent)
        self._dialog_mode = bool(dialog_mode)
        self._prefs = Preferences.instance()
        self._theme = ThemeManager.instance()
        self.restart_requested = False
        self._restart_allowed = bool(allow_restart)
        self._initial_language = (i18n.normalize_language(
            self._prefs.get("language", i18n.AUTO)) or i18n.AUTO)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 12)
        root.setSpacing(12)
        self.header = DialogHeader(
            self.tr("Préférences"),
            self.tr("Apparence, langue et outils externes."),
            "gears")
        root.addWidget(self.header)
        content = QWidget()
        content.setMaximumWidth(self.CONTENT_MAX_WIDTH)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # ── Apparence ────────────────────────────────────────────────────────
        card = Card(self.tr("Apparence"))
        appearance = QGridLayout()
        appearance.setHorizontalSpacing(14)
        appearance.setVerticalSpacing(8)
        self.theme_combo = SegmentedControl()
        self.theme_combo.addItem(self.tr("Système"), "system")
        self.theme_combo.addItem(self.tr("Sombre"), "dark")
        self.theme_combo.addItem(self.tr("Clair"), "light")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self._theme.appearance())))
        self.zoom_combo = QComboBox()
        for z in ZOOM_CHOICES:
            self.zoom_combo.addItem(f"{round(z * 100)} %", z)
        current = self._theme.zoom()
        self.zoom_combo.setCurrentIndex(
            min(range(len(ZOOM_CHOICES)), key=lambda i: abs(ZOOM_CHOICES[i] - current)))
        appearance.addWidget(QLabel(self.tr("Thème")), 0, 0)
        appearance.addWidget(self.theme_combo, 0, 1)
        appearance.addWidget(QLabel(self.tr("Taille du texte")), 1, 0)
        appearance.addWidget(self.zoom_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self.icon_combo = SegmentedControl()
        self.icon_combo.addItem(self.tr("Papicons"), "papicons")
        self.icon_combo.addItem(self.tr("Lucide Animées"), "animated")
        self.icon_combo.setCurrentIndex(max(0, self.icon_combo.findData(self._theme.icon_theme())))
        self.icon_combo.setToolTip(self.tr(
            "Papicons : icônes pleines historiques. Lucide Animées : icônes au trait, animées au "
            "survol (sans animation si « Réduire les animations » est actif)."))
        appearance.addWidget(QLabel(self.tr("Icônes")), 2, 0)
        appearance.addWidget(self.icon_combo, 2, 1)
        # La troisième colonne absorbe la largeur restante : les contrôles restent
        # collés à leur libellé au lieu de flotter au milieu de la carte.
        appearance.setColumnStretch(2, 1)
        card.body_layout.addLayout(appearance)
        content_layout.addWidget(card)

        # Réglages d'accessibilité : ils pilotent le verre et les animations de toute l'app.
        accessibility_card = Card(self.tr("Accessibilité"))
        accessibility = QVBoxLayout()
        accessibility.setSpacing(4)
        self.reduce_motion = SwitchRow(self.tr("Réduire les animations"), align_right=True)
        self.reduce_motion.setToolTip(self.tr(
            "Coupe les animations de capsule, d'en-tête et les icônes d'onglet animées."))
        self.reduce_transparency = SwitchRow(self.tr("Réduire la transparence"), align_right=True)
        self.reduce_transparency.setToolTip(self.tr(
            "Rend opaques les surfaces en verre : barres, cartes et dialogues restent nets."))
        self.high_contrast = SwitchRow(self.tr("Contraste élevé"), align_right=True)
        self.high_contrast.setToolTip(self.tr(
            "Renforce les liserés et l'anneau de focus pour les personnes à basse vision."))
        self._initial_flags: dict[str, bool] = {}
        for check, name in ((self.reduce_motion, "reduce_motion"),
                            (self.reduce_transparency, "reduce_transparency"),
                            (self.high_contrast, "high_contrast")):
            self._initial_flags[name] = bool(getattr(self._theme, name)())
            check.setChecked(self._initial_flags[name])
            accessibility.addWidget(check)
        accessibility_hint = QLabel(self.tr(
            "Tant qu'une option n'est pas modifiée ici, elle suit le réglage d'accessibilité "
            "du système lorsqu'il est disponible."))
        accessibility_hint.setWordWrap(True)
        accessibility_hint.setProperty("role", "muted")
        accessibility.addWidget(accessibility_hint)
        accessibility_card.body_layout.addLayout(accessibility)
        content_layout.addWidget(accessibility_card)

        # ── Langue ───────────────────────────────────────────────────────────
        language_card = Card(self.tr("Langue"))
        language_row = QHBoxLayout()
        language_row.setSpacing(10)
        self.language_combo = QComboBox()
        self._fill_languages()
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_row.addWidget(QLabel(self.tr("Langue de l'interface")))
        self.language_combo.setMaximumWidth(320)
        language_row.addWidget(self.language_combo, 0)
        language_row.addStretch(1)
        language_card.body_layout.addLayout(language_row)
        language_hint = QLabel(self.tr(
            "« Automatique » suit la langue du système. Les langues marquées « traduction à "
            "venir » n'ont pas encore de catalogue : l'anglais, puis le français, sont utilisés. "
            "Le changement prend effet au prochain démarrage d'Azimut."))
        language_hint.setWordWrap(True)
        language_hint.setProperty("role", "muted")
        language_card.body_layout.addWidget(language_hint)
        content_layout.addWidget(language_card)

        # ── Outils externes ──────────────────────────────────────────────────
        tools = Card(self.tr("Outils externes"))
        hint = QLabel(self.tr(
            "Un exemplaire embarqué est utilisé en priorité ; sinon le chemin saisi, puis le PATH "
            "du système. Les changements sont enregistrés avec « Enregistrer »."))
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        tools.body_layout.addWidget(hint)
        self.fields: dict[str, PathField] = {}
        self.chips: dict[str, StatusChip] = {}
        self.detect_buttons: dict[str, QPushButton] = {}
        for name in binaries.KNOWN_BINARIES:
            field = PathField(name, mode="file", filter=self.tr("Tous les fichiers (*)"))
            field.label_widget.setMinimumWidth(scaled(70))
            field.set_path(str(self._prefs.get(f"binaries.{name}", "") or ""))
            chip = StatusChip("…", "neutre")
            chip.setMinimumWidth(scaled(190))
            btn = QPushButton(self.tr("Détecter"))
            btn.setProperty("kind", "secondary")
            btn.clicked.connect(lambda _checked=False, n=name: self._detect(n))
            # Le chemin occupe sa propre ligne : à 150 %, état et bouton restent lisibles
            # au lieu de comprimer le champ jusqu'à un fragment de texte.
            row = QVBoxLayout()
            row.setSpacing(4)
            row.addWidget(field)
            actions = QHBoxLayout()
            actions.setSpacing(8)
            actions.addWidget(chip)
            actions.addStretch(1)
            actions.addWidget(btn)
            row.addLayout(actions)
            tools.body_layout.addLayout(row)
            self.fields[name], self.chips[name], self.detect_buttons[name] = field, chip, btn
        # Détection asynchrone : la localisation est publiée tout de suite, les versions arrivent
        # par le fil de ``binaries.refresh(background=True)``. Un chemin saisi est vérifié dans
        # son propre fil, jamais sur le fil de l'interface.
        self._detect_timer = QTimer(self)
        self._detect_timer.setInterval(160)
        self._detect_timer.timeout.connect(self._poll_detection)
        self._detect_started = QElapsedTimer()
        self._detecting = False
        self._typed_names: set[str] = set()             # pastilles pilotées par le champ, pas par le cache
        self._typed_name: str | None = None             # ligne en cours de vérification
        self._typed_target: Path | None = None
        self._typed_result: dict | None = None
        for name, field in self.fields.items():
            field.pathChanged.connect(lambda text, n=name: self._on_path_changed(n, text))
            # Le texte tapé compte avant la validation du champ (Entrée ou perte de focus) :
            # sinon « Enregistrer » resterait inactif alors qu'un chemin vient d'être saisi.
            field.edit.textChanged.connect(lambda _text="": self._update_save_button())
        content_layout.addWidget(tools)
        content_layout.addStretch(1)               # les cartes gardent leur hauteur naturelle

        # Contenu défilant : à 150 % la page dépasse la hauteur d'un écran de portable.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setWidget(content)
        # Comme « Journaux et historique » : le fond de la fenêtre reste visible ;
        # seules les cartes portent la couleur de zone (surface).
        for target in (self, self.scroll, self.scroll.viewport(), content):
            target.setAutoFillBackground(False)
        root.addWidget(self.scroll, 1)

        # Actions alignées à droite, validation en dernier (convention des boîtes de dialogue).
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.saved_note = QLabel(self.tr("Préférences enregistrées."))
        self.saved_note.setProperty("role", "muted")
        self.saved_note.hide()
        buttons.addWidget(self.saved_note)
        buttons.addStretch(1)
        cancel = QPushButton(self.tr("Annuler"))
        cancel.setProperty("kind", "secondary")
        cancel.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(cancel)
        self.save_button = QPushButton(self.tr("Enregistrer"))
        self.save_button.setProperty("kind", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save_clicked)
        buttons.addWidget(self.save_button)
        root.addLayout(buttons)

        self._initial_theme = str(self.theme_combo.currentData())
        self._initial_zoom = float(self.zoom_combo.currentData())
        self._initial_icon_theme = str(self.icon_combo.currentData())
        self._initial_paths = {name: field.path() for name, field in self.fields.items()}
        # Tout réglage modifié active « Enregistrer » ; revenir à l'état enregistré le désactive.
        self.theme_combo.currentIndexChanged.connect(lambda _index=0: self._update_save_button())
        self.zoom_combo.currentIndexChanged.connect(lambda _index=0: self._update_save_button())
        self.icon_combo.currentIndexChanged.connect(lambda _index=0: self._update_save_button())
        for check in (self.reduce_motion, self.reduce_transparency, self.high_contrast):
            check.toggled.connect(lambda _checked=False: self._update_save_button())
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(self.saved_note.hide)

        self._refresh_chips(None)
        self._update_save_button()
        if binaries.refresh_in_progress():          # sonde lancée au démarrage : suivre ses versions
            self._detect_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Garde une colonne de lecture centrée, même dans une page très large."""
        super().resizeEvent(event)
        layout = self.layout()
        if layout is None:
            return
        side = max(16, (self.width() - self.CONTENT_MAX_WIDTH) // 2)
        margins = layout.contentsMargins()
        if margins.left() != side or margins.right() != side:
            layout.setContentsMargins(side, margins.top(), side, margins.bottom())

    # ── Langue ───────────────────────────────────────────────────────────────
    def _fill_languages(self) -> None:
        """« Automatique » puis les langues du registre ; les catalogues absents sont signalés."""
        self.language_combo.addItem(self.tr("Automatique (système)"), i18n.AUTO)
        self.language_combo.setItemData(0, self.tr("Suit la langue de l'interface du système."),
                                        Qt.ItemDataRole.ToolTipRole)
        for entry in i18n.LANGUAGES:
            ready = i18n.is_available(entry.code)
            label = entry.native_name if ready else entry.native_name + pending_suffix()
            self.language_combo.addItem(label, entry.code)
            index = self.language_combo.count() - 1
            tip = entry.english_name if ready else (
                self.tr("{language} : catalogue à venir, repli sur l'anglais puis le français.")
                .format(language=entry.english_name))
            self.language_combo.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        position = self.language_combo.findData(self._initial_language)
        self.language_combo.setCurrentIndex(max(0, position))

    def _selected_language(self) -> str:
        return str(self.language_combo.currentData() or i18n.AUTO)

    def _language_changed(self) -> bool:
        """Vrai si la langue choisie diffère de celle enregistrée à l'ouverture."""
        return self._selected_language() != self._initial_language

    def _on_language_changed(self, _index: int = 0) -> None:
        self._update_save_button()

    def _has_changes(self) -> bool:
        """Vrai si au moins un réglage diffère du dernier état enregistré."""
        if str(self.theme_combo.currentData()) != self._initial_theme:
            return True
        if abs(float(self.zoom_combo.currentData()) - self._initial_zoom) > 1e-9:
            return True
        if str(self.icon_combo.currentData()) != self._initial_icon_theme:
            return True
        if self._language_changed():
            return True
        for check, name in ((self.reduce_motion, "reduce_motion"),
                            (self.reduce_transparency, "reduce_transparency"),
                            (self.high_contrast, "high_contrast")):
            if check.isChecked() != self._initial_flags[name]:
                return True
        return any(field.path() != self._initial_paths.get(name, "")
                   for name, field in self.fields.items())

    def _update_save_button(self) -> None:
        """« Enregistrer et relancer » seulement quand la langue change."""
        restart = self._restart_allowed and self._language_changed()
        self.save_button.setText(
            self.tr("Enregistrer et relancer") if restart else self.tr("Enregistrer"))
        self.save_button.setEnabled(self._has_changes())
        self.save_button.setToolTip(
            self.tr("Enregistre les préférences et redémarre Azimut pour les appliquer.")
            if restart else "")

    # ── Détection ────────────────────────────────────────────────────────────
    def _info_for(self, name: str, infos: dict[str, BinaryInfo] | None) -> BinaryInfo:
        if infos and name in infos:
            return infos[name]
        try:
            return binaries.resolve(name)
        except Exception as exc:                       # détection défaillante : on l'affiche comme manquant
            log.warning("Détection de %s impossible : %s", name, exc)
            return BinaryInfo(name, None, "missing", "")

    def _refresh_chips(self, infos: dict[str, BinaryInfo] | None) -> None:
        from azimut.ui.theme import repolish  # import local : évite de charger le QSS plus tôt que nécessaire
        for name, chip in self.chips.items():
            if name in self._typed_names:
                continue                            # pastille du chemin saisi : gérée par sa vérification
            text, level, tip = describe_binary(self._info_for(name, infos))
            chip.set_state(text, level)
            chip.setToolTip(plain_tooltip(tip))
            repolish(chip)

    def _on_path_changed(self, name: str, text: str) -> None:
        """Un champ modifié n'affiche plus l'état de l'ancien chemin (ni un résultat périmé)."""
        saved = str(self._prefs.get(f"binaries.{name}", "") or "")
        if self._typed_name == name:
            self._typed_name = None
            self._typed_target = None
            self._typed_result = None              # une sonde en cours ne pilote plus cette pastille
        value = text.strip()
        if not value or value == saved:
            self._typed_names.discard(name)
            self._refresh_chips(None)
            self._update_save_button()
            return
        self._typed_names.add(name)
        chip = self.chips.get(name)
        if chip is not None:
            chip.set_state(self.tr("… non vérifié"), "neutre")
            chip.setToolTip(plain_tooltip(self.tr("Cliquez sur « Détecter » pour vérifier le chemin saisi.")))
        self._update_save_button()

    def _detect(self, name: str) -> None:
        """Relance la détection des trois binaires sans bloquer l'interface.

        La localisation (rapide) est publiée immédiatement ; les versions sont sondées dans le
        fil d'arrière-plan de ``binaries.refresh(background=True)`` et les pastilles se mettent
        à jour au fil de l'eau. Le chemin saisi, s'il diffère de l'enregistré, est vérifié dans
        son propre fil avec les mêmes règles que le résolveur.
        """
        if self._detecting:
            return
        self._detecting = True
        self._detect_started.restart()
        self._typed_name = None
        self._typed_target = None
        self._typed_result = None
        for button in self.detect_buttons.values():
            button.setEnabled(False)

        # Chaque champ modifié garde son propre état : les autres lignes ne doivent pas
        # afficher la détection du chemin enregistré à la place du chemin saisi.
        self._typed_names = {n for n, field in self.fields.items()
                             if field.path().strip()
                             and field.path().strip() != str(self._prefs.get(f"binaries.{n}", "") or "")}
        for typed_name in self._typed_names:
            if typed_name != name:
                self._set_typed_chip(typed_name, unverified=True)

        typed = self.fields[name].path()
        saved = str(self._prefs.get(f"binaries.{name}", "") or "")
        if typed and typed != saved:
            self._typed_name = name
            self._typed_target = binaries.custom_path(typed)
            if self._typed_target is None:
                self._set_typed_chip(name, "", usable=False)
            else:
                chip = self.chips[name]
                chip.set_state(self.tr("… vérification"), "neutre")
                chip.setToolTip(plain_tooltip(self.tr("Exécution du binaire saisi pour lire sa version.")))
                result: dict = {}
                self._typed_result = result
                threading.Thread(target=self._probe_typed, args=(name, self._typed_target, result),
                                 name="azimut-typed-probe", daemon=True).start()

        try:
            infos = binaries.refresh(background=True)
        except Exception:
            log.exception("Détection des outils externes impossible")
            self._typed_result = None
            self._typed_name = None
            self._show_detection_failure()
            self._finish_detection()
            return
        self._refresh_chips(infos)
        if binaries.refresh_in_progress() or self._typed_result is not None:
            self._detect_timer.start()
        else:
            self._finish_detection()

    def _set_typed_chip(self, name: str, version: str = "", *, usable: bool = False,
                        unverified: bool = False) -> None:
        """Pastille du chemin saisi : vérifié (version lue), inutilisable ou pas encore vérifié."""
        chip = self.chips.get(name)
        if chip is None:
            return
        if unverified:
            chip.set_state(self.tr("… non vérifié"), "neutre")
            chip.setToolTip(plain_tooltip(self.tr("Cliquez sur « Détecter » pour vérifier le chemin saisi.")))
        elif usable:
            chip.set_state(self.tr("✔ saisi") + f" · {version}", "info")
            chip.setToolTip(plain_tooltip(self.tr("Chemin saisi, pas encore enregistré.")))
        else:
            chip.set_state(self.tr("✖ chemin saisi inutilisable"), "warn")
            chip.setToolTip(plain_tooltip(self.tr("Le fichier saisi n'a pas pu être exécuté.")))

    @staticmethod
    def _probe_typed(name: str, path: Path, result: dict) -> None:
        """Sonde de version du chemin saisi, hors du fil de l'interface."""
        try:
            result["version"] = binaries.probe_version(name, path)
        except Exception:                          # noqa: BLE001 - sonde best-effort
            log.exception("Sonde de version impossible pour %s", path)
            result["version"] = ""

    def _poll_detection(self) -> None:
        """Met les pastilles à jour tant que les sondes tournent, puis rend les boutons."""
        if not self._detecting:                    # sonde du démarrage : simple suivi des versions
            self._refresh_chips(None)
            if not binaries.refresh_in_progress():
                self._detect_timer.stop()
            return
        self._refresh_chips(None)
        if self._typed_name is not None and self._typed_result is not None \
                and "version" in self._typed_result:
            version = str(self._typed_result.get("version") or "")
            self._set_typed_chip(self._typed_name, version, usable=bool(version))
            self._typed_result = None
            self._typed_target = None
            self._typed_name = None
        timed_out = self._detect_started.elapsed() > _DETECT_TIMEOUT_MS
        if timed_out and self._typed_name is not None and self._typed_result is not None:
            self._set_typed_chip(self._typed_name, "", usable=False)
            self._typed_result = None
            self._typed_target = None
            self._typed_name = None
        if (not binaries.refresh_in_progress() and self._typed_result is None) or timed_out:
            self._refresh_chips(None)
            self._finish_detection()

    def _finish_detection(self) -> None:
        self._detecting = False
        self._detect_timer.stop()
        for button in self.detect_buttons.values():
            button.setEnabled(True)

    def _show_detection_failure(self) -> None:
        """Échec de la détection : un état explicite plutôt que des pastilles figées sur « … »."""
        for chip in self.chips.values():
            chip.set_state(self.tr("✖ détection impossible"), "err")
            chip.setToolTip(plain_tooltip(self.tr("La détection a échoué (voir le journal).")))

    # ── Validation ───────────────────────────────────────────────────────────
    def _on_cancel_clicked(self) -> None:
        """Annule les modifications en cours (ou ferme l'enveloppe de dialogue)."""
        if self._dialog_mode:
            self.cancelled.emit()
            return
        self.reset()

    def reset(self) -> None:
        """Rétablit les valeurs présentes à l'ouverture (ou au dernier enregistrement)."""
        theme = self.theme_combo.findData(self._initial_theme)
        self.theme_combo.setCurrentIndex(max(0, theme))
        self.zoom_combo.setCurrentIndex(
            min(range(self.zoom_combo.count()),
                key=lambda i: abs(float(self.zoom_combo.itemData(i)) - self._initial_zoom)))
        self.icon_combo.setCurrentIndex(max(0, self.icon_combo.findData(self._initial_icon_theme)))
        language = self.language_combo.findData(self._initial_language)
        self.language_combo.setCurrentIndex(max(0, language))
        for check, name in ((self.reduce_motion, "reduce_motion"),
                            (self.reduce_transparency, "reduce_transparency"),
                            (self.high_contrast, "high_contrast")):
            check.setChecked(self._initial_flags[name])
        for name, field in self.fields.items():
            field.set_path(self._initial_paths.get(name, ""))
        self.saved_note.hide()
        self._saved_timer.stop()
        self._update_save_button()

    def refresh(self) -> None:
        """Relit l'état appliqué et les outils externes quand la page redevient visible."""
        self._sync_controls()
        self._refresh_chips(None)
        self._update_save_button()
        if binaries.refresh_in_progress() and not self._detect_timer.isActive():
            self._detect_timer.start()

    def _sync_controls(self) -> None:
        """Recale les réglages sur l'état appliqué, sauf modification en cours (AZ-C11).

        La taille du texte et l'apparence changent aussi hors de cette page : menu
        Présentation, Ctrl+T, zoom propre à chaque écran. Sans ce recalage, la page
        affichait — puis réenregistrait — la valeur figée à sa construction : ouvrir les
        Préférences après un changement de taille ramenait l'application à l'ancienne
        valeur dès « Enregistrer », et le bouton restait actif sans rien à changer.
        """
        if self._has_changes():
            return                       # modifications non enregistrées : on les conserve
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(self._theme.appearance())))
        zoom = float(self._theme.zoom())
        self.zoom_combo.setCurrentIndex(
            min(range(self.zoom_combo.count()),
                key=lambda i: abs(float(self.zoom_combo.itemData(i)) - zoom)))
        self.icon_combo.setCurrentIndex(max(0, self.icon_combo.findData(self._theme.icon_theme())))
        for check, current in ((self.reduce_motion, self._theme.reduce_motion),
                               (self.reduce_transparency, self._theme.reduce_transparency),
                               (self.high_contrast, self._theme.high_contrast)):
            check.setChecked(bool(current()))
        self._remember_current()

    def _remember_current(self) -> None:
        """Fige les valeurs enregistrées : une annulation ultérieure revient à cet état."""
        self._initial_theme = str(self.theme_combo.currentData())
        self._initial_zoom = float(self.zoom_combo.currentData())
        self._initial_icon_theme = str(self.icon_combo.currentData())
        self._initial_language = self._selected_language()
        for check, name in ((self.reduce_motion, "reduce_motion"),
                            (self.reduce_transparency, "reduce_transparency"),
                            (self.high_contrast, "high_contrast")):
            self._initial_flags[name] = check.isChecked()
        self._initial_paths = {name: field.path() for name, field in self.fields.items()}

    def _save_clicked(self) -> None:
        """Enregistre les préférences ; relance seulement si la langue a changé."""
        restart = self._restart_allowed and self._language_changed()
        self._save()
        self._remember_current()
        self.restart_requested = restart
        self._update_save_button()
        if self._dialog_mode:
            self.accepted.emit()
            return
        if restart:
            self.restartRequested.emit()
        else:
            self.saved_note.show()
            self._saved_timer.start(3500)
        self.saved.emit()

    def _save(self) -> None:
        self._theme.set_theme(str(self.theme_combo.currentData()))
        self._theme.set_zoom(float(self.zoom_combo.currentData()))
        self._theme.set_icon_theme(str(self.icon_combo.currentData()))
        # On ne fige que ce que l'utilisateur a réellement changé : laisser une option
        # intacte continue de suivre le réglage du système au prochain démarrage.
        for check, name in ((self.reduce_motion, "reduce_motion"),
                            (self.reduce_transparency, "reduce_transparency"),
                            (self.high_contrast, "high_contrast")):
            if check.isChecked() != self._initial_flags[name]:
                getattr(self._theme, f"set_{name}")(check.isChecked())
        language = str(self.language_combo.currentData() or i18n.AUTO)
        if language != self._prefs.get("language", i18n.AUTO):
            self._prefs.set("language", language)
        changed = False
        for name, field in self.fields.items():
            key = f"binaries.{name}"
            new = field.path()
            if new != str(self._prefs.get(key, "") or ""):
                self._prefs.set(key, new)
                changed = True
        if changed:
            binaries.refresh(background=True)     # ne pas bloquer la fermeture sur les sondes de version


class PreferencesDialog(GlassDialog):
    """Enveloppe de dialogue historique autour de :class:`PreferencesPage`.

    Azimut affiche désormais les préférences dans un onglet ; cette enveloppe reste
    disponible pour les usages autonomes et les tests existants.
    """

    def __init__(self, parent: QWidget | None = None, *, allow_restart: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Préférences"))
        self.setModal(True)
        self.restart_requested = False
        self.page = PreferencesPage(self, allow_restart=allow_restart, dialog_mode=True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.page, 1)

        self.page.accepted.connect(self._on_page_accepted)
        self.page.cancelled.connect(self.reject)
        # Attributs historiques utilisés par les tests et les appelants existants.
        for name in ("theme_combo", "zoom_combo", "icon_combo", "language_combo", "save_button",
                     "fields", "chips", "detect_buttons", "reduce_motion",
                     "reduce_transparency", "high_contrast"):
            setattr(self, name, getattr(self.page, name))
        self.fit_to_content(self.page.scroll, min_width=680)

    def _on_page_accepted(self) -> None:
        """La page a déjà enregistré : fermer sans relancer une seconde sauvegarde."""
        self.restart_requested = self.page.restart_requested
        QDialog.accept(self)

    def accept(self) -> None:
        """Validation directe (raccourci Entrée, appelant autonome)."""
        self.page._save()
        self.page._remember_current()
        QDialog.accept(self)

    def reject(self) -> None:
        QDialog.reject(self)
