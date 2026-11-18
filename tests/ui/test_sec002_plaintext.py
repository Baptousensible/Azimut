"""SEC-002 : les données externes restent du texte littéral dans l'interface.

Un nom de fichier, un chemin ou une valeur de tag peut contenir ``<b>`` : les widgets qui
interprètent le HTML en mode automatique (QLabel, QMessageBox, infobulle) doivent forcer
``Qt.TextFormat.PlainText`` ou échapper le texte.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from azimut.core.binaries import BinaryInfo, BinaryNotFoundError
from azimut.core.prefs import Preferences
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import DropZone, ImagePreview, ProgressPanel, StatusChip, VideoPreview


@pytest.fixture(autouse=True)
def manager(qapp, tmp_path, monkeypatch):
    """ThemeManager isolé, comme dans test_widgets.py."""
    tm = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", tm)
    return tm


@pytest.fixture
def no_binaries(monkeypatch):
    """Évite de sonder les vrais binaires pendant la construction des fenêtres."""
    from azimut.core import binaries

    monkeypatch.setattr(binaries, "resolve",
                        lambda name, prefs=None: BinaryInfo(name, None, "missing", ""))

    def missing(name, prefs=None):
        raise BinaryNotFoundError(f"{name} introuvable")

    monkeypatch.setattr(binaries, "require", missing)


# ── Widgets génériques ───────────────────────────────────────────────────────
def test_progress_file_label_is_plain(qapp):
    panel = ProgressPanel()
    panel.set_progress(1, 2, "<b>x</b>.jpg")
    assert panel.file_label.textFormat() == Qt.TextFormat.PlainText
    assert panel.file_label.text() == "<b>x</b>.jpg"


def test_status_chip_is_plain(qapp):
    chip = StatusChip("✔ ffmpeg 8.1.3", "ok")
    assert chip.textFormat() == Qt.TextFormat.PlainText


def test_drop_zone_label_is_plain(qapp):
    zone = DropZone("<b>Album</b>")
    assert zone._label.textFormat() == Qt.TextFormat.PlainText


def test_image_preview_label_is_plain(qapp):
    preview = ImagePreview(size=80)
    preview.show_text("<b>x</b>")
    assert preview.label.textFormat() == Qt.TextFormat.PlainText


def test_video_preview_label_is_plain(qapp):
    preview = VideoPreview(size=80)
    assert preview.view.textFormat() == Qt.TextFormat.PlainText


# ── Fenêtres des modules ─────────────────────────────────────────────────────
def test_scope_source_name_is_plain(qapp, no_binaries):
    from azimut.modules.scope import MODULE
    from azimut.modules.scope.window import ScopeWindow

    window = ScopeWindow(MODULE)
    try:
        window.tree.mother.name.set_full_text("<b>Album</b>")
        assert window.tree.mother.name.textFormat() == Qt.TextFormat.PlainText
    finally:
        window.close()


def test_rename_info_label_is_plain(qapp, no_binaries):
    from azimut.modules.rename import MODULE
    from azimut.modules.rename.window import RenameWindow

    window = RenameWindow(MODULE)
    try:
        assert window.lbl_info.textFormat() == Qt.TextFormat.PlainText
    finally:
        window.close()


def test_tags_meta_title_is_plain(qapp, no_binaries):
    from azimut.modules.tags import MODULE
    from azimut.modules.tags.window import TagsWindow

    window = TagsWindow(MODULE)
    try:
        assert window._meta_title.textFormat() == Qt.TextFormat.PlainText
    finally:
        window.close()


def test_dedup_captions_are_plain(qapp, no_binaries):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow

    window = DedupWindow(MODULE)
    try:
        assert window.caption_a.textFormat() == Qt.TextFormat.PlainText
        assert window.caption_b.textFormat() == Qt.TextFormat.PlainText
    finally:
        window.close()


def test_dedup_ask_forces_plain_text(qapp, no_binaries, monkeypatch):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow

    window = DedupWindow(MODULE)
    seen: list[Qt.TextFormat] = []

    def fake_exec(box):
        seen.append(box.textFormat())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    try:
        assert window._ask("Confirmer", "<b>x</b>.jpg") is False
        assert seen == [Qt.TextFormat.PlainText]
    finally:
        window.close()


def test_atlas_error_label_is_plain(qapp, no_binaries):
    from azimut.launcher.atlas_window import ModuleCard
    from azimut.modules.dedup import MODULE

    card = ModuleCard(MODULE)
    card.set_error("<b>boom</b>")
    assert card.error_label.textFormat() == Qt.TextFormat.PlainText


# ── Dialogues et infobulles ──────────────────────────────────────────────────
def test_plain_message_forces_plain_text(qapp, monkeypatch):
    from azimut.ui.widgets import plain_message

    seen: list[Qt.TextFormat] = []

    def fake_exec(box):
        seen.append(box.textFormat())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    plain_message(None, QMessageBox.Icon.Warning, "Titre", "<b>x</b>.jpg")
    assert seen == [Qt.TextFormat.PlainText]


def test_plain_tooltip_escapes_html(qapp):
    from azimut.ui.widgets import plain_tooltip

    assert plain_tooltip("<b>x</b>.jpg") == "<qt>&lt;b&gt;x&lt;/b&gt;.jpg</qt>"
