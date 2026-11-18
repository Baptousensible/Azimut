"""Note personnelle d'Azimut, affichée depuis le titre ou le logo.

Le texte vit dans ``resources/personal_note.txt`` et s'affiche dans une notification
permanente (voir ``azimut.ui.notification.notify_persistent``), fermée à la main.
"""
from __future__ import annotations

from azimut.core.paths import resource_path


NOTE_PATH = resource_path("personal_note.txt")


def personal_note_text() -> str:
    """Texte personnel embarqué ; vide si le fichier est absent ou illisible."""
    try:
        return NOTE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
