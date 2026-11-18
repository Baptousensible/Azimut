"""Tests : note personnelle d'Azimut (texte embarqué, affiché en notification permanente)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from azimut.launcher import personal_note


def test_personal_note_reads_the_file(tmp_path, monkeypatch):
    note = tmp_path / "note.txt"
    note.write_text("Ma note\n\nDeuxième paragraphe.", encoding="utf-8")
    monkeypatch.setattr(personal_note, "NOTE_PATH", note)

    assert personal_note.personal_note_text() == "Ma note\n\nDeuxième paragraphe."


def test_personal_note_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(personal_note, "NOTE_PATH", tmp_path / "absente.txt")

    assert personal_note.personal_note_text() == ""


def test_shipped_personal_note_is_packaged():
    assert personal_note.NOTE_PATH.is_file()
    text = personal_note.personal_note_text()
    assert text.startswith("Merci d'avoir téléchargé cette application")
    assert text.endswith("BC")
