"""Sélecteurs de fichiers et de dossiers alignés sur le thème Azimut."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QDialog, QFileDialog, QToolButton, QWidget

from azimut.ui import icons

# Les icônes standard de QFileDialog ne suivent pas la palette claire/sombre :
# le retour et l'avance désactivés, notamment, deviennent presque invisibles en
# thème clair. On remplace les pictogrammes de la barre d'outils par ceux de la
# bibliothèque Papicons, qui reçoivent une couleur lisible dans chaque thème.
_BUTTON_ICONS: dict[str, str] = {
    "backButton": "arrow-left",
    "forwardButton": "arrow-right",
    "toParentButton": "arrow-up",
    "newFolderButton": "folder",
    "listModeButton": "list",
    "detailModeButton": "grid",
}

_ICON_SIZE = 16


def _themed_icon(name: str):
    return icons.icon(name, "text", _ICON_SIZE, disabled_color="muted")


class FileDialog(QFileDialog):
    """Sélecteur Qt non natif dont les libellés suivent les catalogues Azimut.

    Les méthodes statiques de ``QFileDialog`` utilisent un dialogue natif dont les
    libellés ne peuvent pas être traduits par l'application. Ce dialogue est donc
    non natif et reçoit explicitement tous ses libellés, y compris ceux que Qt
    affiche en anglais par défaut.
    """

    def __init__(self, parent: QWidget | None = None, caption: str = "", directory: str = "",
                 *, file_mode: QFileDialog.FileMode = QFileDialog.FileMode.ExistingFile,
                 accept_mode: QFileDialog.AcceptMode = QFileDialog.AcceptMode.AcceptOpen,
                 file_filter: str = "") -> None:
        super().__init__(parent, caption, directory)
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.setOption(QFileDialog.Option.ShowDirsOnly,
                       file_mode == QFileDialog.FileMode.Directory)
        self.setFileMode(file_mode)
        self.setAcceptMode(accept_mode)
        self._apply_labels()
        if file_mode != QFileDialog.FileMode.Directory:
            self.setNameFilter(file_filter or _default_filter())

    def _apply_labels(self) -> None:
        """Traduit les libellés internes et le filtre du mode dossier."""
        self.setLabelText(QFileDialog.DialogLabel.LookIn, self.tr("Emplacement :"))
        self.setLabelText(QFileDialog.DialogLabel.FileType, self.tr("Types de fichiers :"))
        if self.fileMode() == QFileDialog.FileMode.Directory:
            self.setLabelText(QFileDialog.DialogLabel.FileName, self.tr("Dossier :"))
            self.setLabelText(QFileDialog.DialogLabel.Accept, self.tr("Choisir"))
            self.setNameFilter(self.tr("Dossiers"))
        else:
            self.setLabelText(QFileDialog.DialogLabel.FileName, self.tr("Nom du fichier :"))
            if self.acceptMode() == QFileDialog.AcceptMode.AcceptSave:
                self.setLabelText(QFileDialog.DialogLabel.Accept, self.tr("Enregistrer"))
            else:
                self.setLabelText(QFileDialog.DialogLabel.Accept, self.tr("Ouvrir"))
        self.setLabelText(QFileDialog.DialogLabel.Reject, self.tr("Annuler"))

    def showEvent(self, event) -> None:  # noqa: N802 - nom hérité de Qt
        super().showEvent(event)
        self._refresh_icons()

    def _button_text(self, object_name: str) -> str:
        """Infobulle traduite d'un bouton de navigation, ou chaîne vide."""
        if object_name == "backButton":
            return self.tr("Retour")
        if object_name == "forwardButton":
            return self.tr("Avancer")
        if object_name == "toParentButton":
            return self.tr("Dossier parent")
        if object_name == "newFolderButton":
            return self.tr("Nouveau dossier")
        if object_name == "listModeButton":
            return self.tr("Vue en liste")
        if object_name == "detailModeButton":
            return self.tr("Vue détaillée")
        return ""

    def _refresh_icons(self) -> None:
        """Applique les icônes Papicons et les infobulles traduites aux boutons de Qt."""
        for button in self.findChildren(QToolButton):
            object_name = button.objectName()
            name = _BUTTON_ICONS.get(object_name)
            if not name:
                continue
            button.setIcon(_themed_icon(name))
            text = self._button_text(object_name)
            if text:
                button.setToolTip(text)
                button.setAccessibleName(text)


class FolderDialog(FileDialog):
    """Sélecteur de dossier Qt, rendu cohérent avec les deux thèmes d'Azimut."""

    def __init__(self, parent: QWidget | None = None, caption: str = "", directory: str = "") -> None:
        super().__init__(parent, caption, directory,
                         file_mode=QFileDialog.FileMode.Directory,
                         accept_mode=QFileDialog.AcceptMode.AcceptOpen)
        self.setOption(QFileDialog.Option.ShowDirsOnly, True)


def _default_filter() -> str:
    return QCoreApplication.translate("FileDialog", "Tous les fichiers (*)")


def media_filter(patterns: str) -> str:
    """Filtre des médias, avec le libellé « Tous les fichiers » traduit."""
    return QCoreApplication.translate(
        "FileDialog", "Médias ({patterns});;Tous les fichiers (*)").format(patterns=patterns)


def _run(dialog: QFileDialog) -> list[str]:
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    return dialog.selectedFiles()


def choose_directory(parent: QWidget | None, caption: str, directory: str = "") -> str:
    """Ouvre le sélecteur de dossier et renvoie le chemin choisi, ou une chaîne vide."""
    selected = _run(FolderDialog(parent, caption, directory))
    return selected[0] if selected else ""


def choose_file(parent: QWidget | None, caption: str, directory: str = "",
                file_filter: str = "") -> str:
    """Ouvre le sélecteur d'un fichier existant et renvoie son chemin, ou une chaîne vide."""
    selected = _run(FileDialog(parent, caption, directory,
                               file_mode=QFileDialog.FileMode.ExistingFile,
                               accept_mode=QFileDialog.AcceptMode.AcceptOpen,
                               file_filter=file_filter))
    return selected[0] if selected else ""


def choose_files(parent: QWidget | None, caption: str, directory: str = "",
                 file_filter: str = "") -> list[str]:
    """Ouvre le sélecteur de plusieurs fichiers existants et renvoie les chemins choisis."""
    return _run(FileDialog(parent, caption, directory,
                           file_mode=QFileDialog.FileMode.ExistingFiles,
                           accept_mode=QFileDialog.AcceptMode.AcceptOpen,
                           file_filter=file_filter))


def choose_save_file(parent: QWidget | None, caption: str, path: str = "",
                     file_filter: str = "") -> str:
    """Ouvre le sélecteur d'enregistrement et renvoie le chemin choisi, ou une chaîne vide."""
    selected = _run(FileDialog(parent, caption, path,
                               file_mode=QFileDialog.FileMode.AnyFile,
                               accept_mode=QFileDialog.AcceptMode.AcceptSave,
                               file_filter=file_filter))
    return selected[0] if selected else ""
