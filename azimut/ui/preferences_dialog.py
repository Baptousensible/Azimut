"""Boîte de préférences : thème, taille du texte, chemins personnalisés des binaires externes."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from azimut.core import binaries
from azimut.core.binaries import BinaryInfo
from azimut.core.logs import get_logger
from azimut.core.prefs import Preferences
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import Card, PathField, StatusChip

log = get_logger(__name__)

ZOOM_CHOICES: tuple[float, ...] = (0.9, 1.0, 1.1, 1.25, 1.5)
SOURCE_LABELS: dict[str, str] = {
    "bundled": "embarqué",
    "custom": "personnalisé",
    "system": "système",
    "missing": "introuvable",
}


def describe_binary(info: BinaryInfo) -> tuple[str, str, str]:
    """(texte de pastille, niveau, infobulle) pour un binaire détecté ou manquant."""
    if not info.available:
        return ("✖ introuvable", "err",
                f"{info.name} est introuvable : indiquez son chemin ou installez-le.")
    label = SOURCE_LABELS.get(info.source, info.source)
    text = f"✔ {label}" + (f" · {info.version}" if info.version else "")
    return text, "ok", str(info.path)


class PreferencesDialog(QDialog):
    """Thème, taille du texte et chemins de ffmpeg / ffprobe / exiftool (écrits à la validation)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Préférences")
        self.setModal(True)
        self.setMinimumWidth(760)
        self._prefs = Preferences.instance()
        self._theme = ThemeManager.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        # ── Apparence ────────────────────────────────────────────────────────
        card = Card("Apparence")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Sombre", "dark")
        self.theme_combo.addItem("Clair", "light")
        self.theme_combo.setCurrentIndex(0 if self._theme.theme() == "dark" else 1)
        self.zoom_combo = QComboBox()
        for z in ZOOM_CHOICES:
            self.zoom_combo.addItem(f"{round(z * 100)} %", z)
        current = self._theme.zoom()
        self.zoom_combo.setCurrentIndex(
            min(range(len(ZOOM_CHOICES)), key=lambda i: abs(ZOOM_CHOICES[i] - current)))
        row.addWidget(QLabel("Thème"))
        row.addWidget(self.theme_combo)
        row.addSpacing(18)
        row.addWidget(QLabel("Taille du texte"))
        row.addWidget(self.zoom_combo)
        row.addStretch(1)
        card.body_layout.addLayout(row)
        root.addWidget(card)

        # ── Outils externes ──────────────────────────────────────────────────
        tools = Card("Outils externes")
        hint = QLabel("Un exemplaire embarqué est utilisé en priorité ; sinon le chemin saisi, puis le PATH "
                      "du système. Les changements sont enregistrés avec « Enregistrer ».")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        tools.body_layout.addWidget(hint)
        self.fields: dict[str, PathField] = {}
        self.chips: dict[str, StatusChip] = {}
        self.detect_buttons: dict[str, QPushButton] = {}
        for name in binaries.KNOWN_BINARIES:
            line = QHBoxLayout()
            line.setSpacing(8)
            field = PathField(name, mode="file", filter="Tous les fichiers (*)")
            field.label_widget.setMinimumWidth(70)
            field.set_path(str(self._prefs.get(f"binaries.{name}", "") or ""))
            chip = StatusChip("…", "neutre")
            chip.setMinimumWidth(190)
            btn = QPushButton("Détecter")
            btn.setProperty("kind", "secondary")
            btn.clicked.connect(lambda _checked=False, n=name: self._detect(n))
            line.addWidget(field, 1)
            line.addWidget(chip)
            line.addWidget(btn)
            tools.body_layout.addLayout(line)
            self.fields[name], self.chips[name], self.detect_buttons[name] = field, chip, btn
        root.addWidget(tools)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = box.button(QDialogButtonBox.StandardButton.Save)
        save.setText("Enregistrer")
        save.setProperty("kind", "primary")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._refresh_chips(None)

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
            text, level, tip = describe_binary(self._info_for(name, infos))
            chip.set_state(text, level)
            chip.setToolTip(tip)
            repolish(chip)

    def _detect(self, name: str) -> None:
        """Relance la détection des trois binaires ; signale aussi le chemin saisi s'il diffère de l'enregistré."""
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            infos = binaries.refresh()
            self._refresh_chips(infos)
            typed = self.fields[name].path()
            saved = str(self._prefs.get(f"binaries.{name}", "") or "")
            if typed and typed != saved:
                p = Path(typed)
                version = binaries.probe_version(name, p) if p.is_file() else ""
                chip = self.chips[name]
                if version:
                    chip.set_state(f"✔ saisi · {version}", "info")
                    chip.setToolTip("Chemin saisi, pas encore enregistré.")
                else:
                    chip.set_state("✖ chemin saisi inutilisable", "warn")
                    chip.setToolTip("Le fichier saisi n'a pas pu être exécuté.")
        finally:
            QApplication.restoreOverrideCursor()

    # ── Validation ───────────────────────────────────────────────────────────
    def accept(self) -> None:
        self._save()
        super().accept()

    def _save(self) -> None:
        self._theme.set_theme(str(self.theme_combo.currentData()))
        self._theme.set_zoom(float(self.zoom_combo.currentData()))
        changed = False
        for name, field in self.fields.items():
            key = f"binaries.{name}"
            new = field.path()
            if new != str(self._prefs.get(key, "") or ""):
                self._prefs.set(key, new)
                changed = True
        if changed:
            binaries.refresh()
