"""Configuration pytest commune : Qt hors écran, isolation des dossiers utilisateur, fixtures."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_dirs(tmp_path_factory, monkeypatch):
    """Aucun test n'écrit dans les vrais dossiers utilisateur ; le singleton Preferences est remis à zéro."""
    home = tmp_path_factory.mktemp("home")
    for var, sub in (("HOME", ""), ("USERPROFILE", ""), ("APPDATA", "AppData/Roaming"),
                     ("XDG_DATA_HOME", ".local/share"), ("XDG_CONFIG_HOME", ".config")):
        monkeypatch.setenv(var, str(home / sub))
    from azimut.core.prefs import Preferences
    monkeypatch.setattr(Preferences, "_instance", None)


@pytest.fixture(scope="session")
def qapp():
    """Application Qt unique (QApplication si QtWidgets est disponible, sinon QCoreApplication)."""
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    if app is None:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
        except ImportError:
            app = QCoreApplication([])
    yield app


@pytest.fixture
def tmp_prefs(qapp, tmp_path_factory):
    """Preferences sur un fichier temporaire."""
    from azimut.core.prefs import Preferences
    return Preferences(tmp_path_factory.mktemp("prefs") / "preferences.json")
