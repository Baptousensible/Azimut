"""Commande de relance d'Azimut (sans lancer de processus)."""
from __future__ import annotations

import os
import sys

import pytest

from azimut.ui import app as app_module


@pytest.fixture(autouse=True)
def _not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_restart_command_reuses_the_script(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py"])
    program, args = app_module.restart_command()
    assert program == sys.executable
    assert args == [os.path.abspath("main.py")]


def test_restart_command_uses_the_frozen_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["/opt/azimut", "/tmp/photo.jpg"])
    assert app_module.restart_command() == (sys.executable, ["/tmp/photo.jpg"])


def test_restart_command_for_package_entry(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/projet/azimut/__main__.py", "--list"])
    program, args = app_module.restart_command()
    assert program == sys.executable
    assert args == ["-m", "azimut", "--list"]


def test_restart_application_reports_a_failed_spawn(monkeypatch):
    monkeypatch.setattr(app_module, "restart_command", lambda: ("/introuvable", []))
    monkeypatch.setattr(app_module.QProcess, "startDetached", lambda *args: False)
    assert app_module.restart_application() is False


def test_restart_application_reads_the_pyside_success_tuple(monkeypatch):
    monkeypatch.setattr(app_module, "restart_command", lambda: ("/bin/true", []))
    monkeypatch.setattr(app_module.QProcess, "startDetached", lambda *args: (True, 4321))
    assert app_module.restart_application() is True


def test_restart_application_rejects_the_pyside_failure_tuple(monkeypatch):
    monkeypatch.setattr(app_module, "restart_command", lambda: ("/introuvable", []))
    monkeypatch.setattr(app_module.QProcess, "startDetached", lambda *args: (False, 0))
    assert app_module.restart_application() is False
