from __future__ import annotations

import os
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSize
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QDialog, QFileDialog, QToolButton

from azimut.core import i18n
from azimut.ui.file_dialog import (
    _BUTTON_ICONS,
    FileDialog,
    FolderDialog,
    _themed_icon,
    choose_directory,
    choose_file,
    choose_files,
    choose_save_file,
    media_filter,
)


FILE_DIALOG_SOURCES = {
    "Emplacement :",
    "Nom du fichier :",
    "Types de fichiers :",
    "Dossier :",
    "Ouvrir",
    "Enregistrer",
    "Choisir",
    "Annuler",
    "Dossiers",
    "Tous les fichiers (*)",
    "Médias ({patterns});;Tous les fichiers (*)",
    "Retour",
    "Avancer",
    "Dossier parent",
    "Nouveau dossier",
    "Vue en liste",
    "Vue détaillée",
}

CALLER_SOURCES = {
    "TakeoutWindow": ("Importer des fichiers", "Importer le dossier de l'export Google Photos"),
    "ConvertWindow": ("Importer des fichiers", "Importer un dossier"),
    "RenameWindow": ("Importer des fichiers", "Importer un dossier", "Exporter le plan", "CSV (*.csv)"),
    "TagsWindow": ("Importer des fichiers", "Importer un dossier"),
    "ScopeWindow": ("Importer des fichiers", "Importer un dossier", "Exporter les résultats", "Texte (*.txt)"),
    "DedupWindow": ("Importer un dossier", "Exporter le rapport", "Fichiers CSV (*.csv)"),
}


def _has_visible_pixels(image: QImage) -> bool:
    return any(image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width()))


def test_folder_dialog_uses_themed_toolbar_icons(qapp, tmp_path):
    dialog = FolderDialog(None, "Choisir un dossier", str(tmp_path))
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert dialog.fileMode() == QFileDialog.FileMode.Directory
    assert dialog.testOption(QFileDialog.Option.ShowDirsOnly)
    dialog.show()
    qapp.processEvents()
    try:
        buttons = {button.objectName(): button for button in dialog.findChildren(QToolButton)}
        for object_name, icon_name in _BUTTON_ICONS.items():
            button = buttons[object_name]
            normal = button.icon().pixmap(QSize(16, 16))
            disabled = button.icon().pixmap(QSize(16, 16), QIcon.Mode.Disabled)
            assert _has_visible_pixels(normal.toImage())
            assert _has_visible_pixels(disabled.toImage())
            assert normal.toImage() == _themed_icon(icon_name).pixmap(QSize(16, 16)).toImage()
        assert buttons["backButton"].toolTip() == "Retour"
        assert buttons["newFolderButton"].toolTip() == "Nouveau dossier"
    finally:
        dialog.close()


def test_file_dialog_translates_its_internal_labels(qapp):
    dialog = FileDialog(None, "Choisir un fichier")
    try:
        assert dialog.labelText(QFileDialog.DialogLabel.LookIn) == "Emplacement :"
        assert dialog.labelText(QFileDialog.DialogLabel.FileName) == "Nom du fichier :"
        assert dialog.labelText(QFileDialog.DialogLabel.FileType) == "Types de fichiers :"
        assert dialog.labelText(QFileDialog.DialogLabel.Accept) == "Ouvrir"
        assert dialog.labelText(QFileDialog.DialogLabel.Reject) == "Annuler"
        assert dialog.nameFilters() == ["Tous les fichiers (*)"]
    finally:
        dialog.close()

    folder = FolderDialog(None, "Choisir un dossier")
    try:
        assert folder.labelText(QFileDialog.DialogLabel.FileName) == "Dossier :"
        assert folder.labelText(QFileDialog.DialogLabel.Accept) == "Choisir"
        assert folder.nameFilters() == ["Dossiers"]
    finally:
        folder.close()

    save = FileDialog(None, "Enregistrer un fichier",
                      file_mode=QFileDialog.FileMode.AnyFile,
                      accept_mode=QFileDialog.AcceptMode.AcceptSave)
    try:
        assert save.labelText(QFileDialog.DialogLabel.Accept) == "Enregistrer"
    finally:
        save.close()


def test_file_dialog_labels_follow_the_selected_language(qapp):
    i18n.install(qapp, "en")
    try:
        dialog = FileDialog(None, "Choose a file")
        try:
            assert dialog.labelText(QFileDialog.DialogLabel.LookIn) == "Location:"
            assert dialog.labelText(QFileDialog.DialogLabel.FileName) == "File name:"
            assert dialog.labelText(QFileDialog.DialogLabel.FileType) == "Files of type:"
            assert dialog.labelText(QFileDialog.DialogLabel.Accept) == "Open"
            assert dialog.labelText(QFileDialog.DialogLabel.Reject) == "Cancel"
            assert dialog.nameFilters() == ["All files (*)"]
            dialog.show()
            qapp.processEvents()
            buttons = {button.objectName(): button for button in dialog.findChildren(QToolButton)}
            assert buttons["backButton"].toolTip() == "Back"
            assert buttons["detailModeButton"].toolTip() == "Details view"
        finally:
            dialog.close()

        folder = FolderDialog(None, "Choose a folder")
        try:
            assert folder.labelText(QFileDialog.DialogLabel.FileName) == "Folder:"
            assert folder.labelText(QFileDialog.DialogLabel.Accept) == "Choose"
            assert folder.nameFilters() == ["Folders"]
        finally:
            folder.close()

        save = FileDialog(None, "Save a file",
                          file_mode=QFileDialog.FileMode.AnyFile,
                          accept_mode=QFileDialog.AcceptMode.AcceptSave)
        try:
            assert save.labelText(QFileDialog.DialogLabel.Accept) == "Save"
        finally:
            save.close()
        assert media_filter("*.jpg") == "Media (*.jpg);;All files (*)"
        assert QCoreApplication.translate("TakeoutWindow", "Importer des fichiers") == "Import files"
        assert QCoreApplication.translate("ScopeWindow", "Exporter les résultats") == "Export results"
        assert QCoreApplication.translate("RenameWindow", "CSV (*.csv)") == "CSV (*.csv)"
    finally:
        i18n.uninstall(qapp)


def test_file_dialog_sources_are_translated_in_every_catalog():
    """Chaque libellé interne existe et est traduit dans les 27 catalogues."""
    for path in sorted(i18n.translations_dir().glob("azimut_*.ts")):
        root = ET.parse(path).getroot()
        context = next((ctx for ctx in root.findall("context")
                        if ctx.findtext("name") == "FileDialog"), None)
        assert context is not None, path.name
        messages = {msg.findtext("source"): msg.find("translation")
                    for msg in context.findall("message")}
        for source in FILE_DIALOG_SOURCES:
            translation = messages.get(source)
            assert translation is not None, f"{path.name}: {source!r} manque"
            assert translation.text and translation.text.strip(), f"{path.name}: {source!r} vide"
            assert translation.get("type") != "unfinished", f"{path.name}: {source!r} inachevé"
        assert "{patterns}" in messages["Médias ({patterns});;Tous les fichiers (*)"].text

        for context_name, sources in CALLER_SOURCES.items():
            context = next((ctx for ctx in root.findall("context")
                            if ctx.findtext("name") == context_name), None)
            assert context is not None, f"{path.name}: contexte {context_name} absent"
            messages = {msg.findtext("source"): msg.find("translation")
                        for msg in context.findall("message")}
            for source in sources:
                translation = messages.get(source)
                assert translation is not None, f"{path.name}: {context_name}/{source!r} manque"
                assert translation.text and translation.text.strip(), f"{path.name}: {context_name}/{source!r} vide"
                assert translation.get("type") != "unfinished", f"{path.name}: {context_name}/{source!r} inachevé"


def test_choose_directory_returns_the_selected_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(FolderDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(FolderDialog, "selectedFiles", lambda self: [str(tmp_path)])
    assert choose_directory(None, "Choisir un dossier", str(tmp_path)) == str(tmp_path)


def test_choose_directory_returns_empty_on_cancel(monkeypatch):
    monkeypatch.setattr(FolderDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert choose_directory(None, "Choisir un dossier") == ""


def test_choose_files_returns_all_selected_paths(monkeypatch, tmp_path):
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    monkeypatch.setattr(FileDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(FileDialog, "selectedFiles", lambda self: [str(first), str(second)])
    assert choose_files(None, "Choisir des fichiers", str(tmp_path)) == [str(first), str(second)]


def test_choose_file_and_save_file_handle_cancel(monkeypatch):
    monkeypatch.setattr(FileDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert choose_file(None, "Choisir un fichier") == ""
    assert choose_save_file(None, "Enregistrer") == ""


def test_choose_file_and_save_file_return_first_selection(monkeypatch, tmp_path):
    target = tmp_path / "rapport.csv"
    monkeypatch.setattr(FileDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(FileDialog, "selectedFiles", lambda self: [str(target)])
    assert choose_file(None, "Choisir un fichier") == str(target)
    assert choose_save_file(None, "Enregistrer", str(target)) == str(target)
