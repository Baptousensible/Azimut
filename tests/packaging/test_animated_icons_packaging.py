"""Livraison des icônes animées et de leur licence dans les paquets.

Les chemins sont vérifiés dans la configuration setuptools et dans le script PyInstaller :
un SVG ajouté au thème doit partir dans le wheel, la sdist et les distributions de bureau.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANIMATED = ROOT / "azimut" / "resources" / "icons_animated"


def load_build_module():
    """Charge packaging/build.py sans dépendre du paquet PyPI « packaging »."""
    name = "azimut_packaging_build"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_animated_assets_are_declared_in_package_data():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = config["tool"]["setuptools"]
    patterns = setuptools["package-data"]["azimut"]

    assert setuptools["include-package-data"] is True
    assert "resources/icons_animated/*.svg" in patterns
    assert "resources/icons_animated/*.txt" in patterns
    assert "resources/icons_animated/*.md" in patterns
    assert setuptools["data-files"]["share/doc/azimut"] == [
        "README.md",
        "THIRD_PARTY_NOTICES.md",
    ]


def test_animated_sources_and_mit_license_are_present():
    svgs = sorted(ANIMATED.glob("*.svg"))
    assert len(svgs) == 33
    readme = (ANIMATED / "README.md").read_text(encoding="utf-8")
    assert "lucide-animated" in readme and "MIT" in readme
    license_text = (ANIMATED / "LICENSE-lucide-animated.txt").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License")
    assert "pqoqubbw" in license_text
    assert "Permission is hereby granted" in license_text


def test_pyinstaller_spec_declares_animated_guard_and_notice():
    spec = (ROOT / "packaging" / "azimut.spec").read_text(encoding="utf-8")
    assert "ANIMATED_SVGS" in spec
    assert "LICENSE-lucide-animated.txt" in spec
    assert "THIRD_PARTY_NOTICES.md" in spec


def test_build_publishes_notice_at_distribution_root(tmp_path):
    module = load_build_module()
    output = tmp_path / "dist"
    output.mkdir()

    assert module.publish_notice(output)
    assert (output / module.THIRD_PARTY_NOTICES).read_text(encoding="utf-8") == (
        (ROOT / module.THIRD_PARTY_NOTICES).read_text(encoding="utf-8"))


def test_build_checker_accepts_a_complete_distribution(tmp_path):
    module = load_build_module()
    output = tmp_path / "dist"
    target = output / "azimut" / "resources" / "icons_animated"
    shutil.copytree(module.ANIMATED_SOURCE, target)
    (output / module.THIRD_PARTY_NOTICES).write_text("notices", encoding="utf-8")

    assert module.check_packaged_assets(output)


def test_build_checker_rejects_a_distribution_without_animated_icons(tmp_path):
    module = load_build_module()
    output = tmp_path / "dist"
    output.mkdir()
    (output / module.THIRD_PARTY_NOTICES).write_text("notices", encoding="utf-8")

    assert not module.check_packaged_assets(output)
