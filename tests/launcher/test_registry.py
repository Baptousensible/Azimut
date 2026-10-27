"""Tests de la découverte des modules et de la ligne de commande (sans Qt visible)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

import pytest

from azimut import __version__
from azimut.core.module_spec import ModuleSpec
from azimut.launcher import cli, registry


def _spec(module_id: str, order: int = 100, requires: tuple[str, ...] = ()) -> ModuleSpec:
    return ModuleSpec(id=module_id, name=f"Azimut {module_id.title()}", tagline="Module factice.",
                      icon="🧪", entry=f"fake.{module_id}:Window", requires=requires, order=order)


@pytest.fixture
def fake_modules(monkeypatch):
    modules = {
        "azimut.modules.zeta": types.SimpleNamespace(MODULE=_spec("zeta", 30)),
        "azimut.modules.alpha": types.SimpleNamespace(MODULE=_spec("alpha", 10)),
        "azimut.modules.mid": types.SimpleNamespace(MODULE=_spec("mid", 20)),
        "azimut.modules.liar": types.SimpleNamespace(MODULE=_spec("autre", 5)),
    }

    def fake_import(name: str, package=None):
        if name == "azimut.modules.broken":
            raise ImportError("module cassé")
        if name == "azimut.modules.nomodule":
            return types.SimpleNamespace()
        try:
            return modules[name]
        except KeyError:
            raise ModuleNotFoundError(name) from None

    monkeypatch.setattr(registry, "importlib", types.SimpleNamespace(import_module=fake_import))
    monkeypatch.setattr(registry, "MODULE_IDS", ("zeta", "broken", "alpha", "mid", "nomodule", "liar"))


def test_discover_sorted_by_order(fake_modules):
    assert [s.id for s in registry.discover()] == ["alpha", "mid", "zeta"]


def test_discover_tolerates_failures(fake_modules):
    specs, errors = registry.discover_with_errors()
    assert [s.id for s in specs] == ["alpha", "mid", "zeta"]
    assert "module cassé" in errors["broken"]
    assert "nomodule" in errors            # pas de MODULE
    assert "liar" in errors                # identifiant incohérent
    assert set(errors) == {"broken", "nomodule", "liar"}


# ── ligne de commande ──
@pytest.fixture
def fake_discovery(monkeypatch):
    specs = [_spec("convert", 10, ("ffmpeg", "ffprobe")), _spec("tags", 20, ("exiftool",))]
    monkeypatch.setattr(registry, "discover_with_errors", lambda: (specs, {"broken": "boom"}))
    return specs


def test_cli_list(fake_discovery, capsys):
    assert cli.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "convert" in out and "tags" in out
    assert "ffmpeg" in out and "exiftool" in out
    assert "broken" in out and "indisponible" in out


def test_cli_version(capsys):
    assert cli.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_cli_unknown_module(fake_discovery, capsys):
    assert cli.main(["inconnu"]) != 0
    err = capsys.readouterr().err
    assert "inconnu" in err and "module inconnu" in err.lower()


def test_cli_broken_module(fake_discovery, capsys):
    assert cli.main(["broken"]) == 1
    assert "indisponible" in capsys.readouterr().err


def test_cli_unknown_option(fake_discovery, capsys):
    assert cli.main(["--zzz"]) == 2
    assert "option inconnue" in capsys.readouterr().err


def test_cli_runs_module(fake_discovery, monkeypatch):
    calls: list[tuple] = []

    def fake_run(spec, argv=None):
        calls.append((spec, argv))
        return 7

    monkeypatch.setattr("azimut.ui.app.run_module", fake_run)
    assert cli.main(["convert", "a.jpg", "dossier"]) == 7
    spec, argv = calls[0]
    assert spec.id == "convert" and argv[-2:] == ["a.jpg", "dossier"]
