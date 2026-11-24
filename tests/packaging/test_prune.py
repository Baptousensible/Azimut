"""Règles d'élagage des distributions PyInstaller (packaging/prune.py).

Ces vérifications remplacent la campagne manuelle qui avait validé le premier
élagage : elles fixent ce qui doit disparaître du produit (QML, métatypes,
outils de développement, greffons inutiles) et ce qui doit y rester (greffons
d'exécution, bibliothèques libav, catalogues qtbase de chaque langue, sources
.ts hors de package-data).
"""
from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRANSLATIONS = ROOT / "azimut" / "resources" / "translations"


def load_prune():
    """Charge packaging/prune.py sans dépendre du paquet PyPI « packaging »."""
    name = "azimut_packaging_prune"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "prune.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _make_dist(tmp_path: Path) -> tuple[Path, Path]:
    """Produit factice : dist/Azimut/_internal/PySide6/Qt, comme PyInstaller ≥ 6."""
    dist = tmp_path / "dist" / "Azimut"
    pyside = dist / "_internal" / "PySide6"
    (pyside / "Qt").mkdir(parents=True)
    return dist, pyside


def test_plugin_rules_cover_the_runtime_plugins_and_stay_disjoint():
    module = load_prune()

    assert {"platforms", "imageformats", "iconengines", "multimedia"} <= module.KEPT_PLUGIN_DIRS
    assert module.KEPT_PLUGIN_DIRS.isdisjoint(module.DROPPED_PLUGIN_DIRS)
    for useless in ("sqldrivers", "qmltooling", "geoservices", "position", "sensors",
                    "texttospeech", "webview", "designer"):
        assert useless in module.DROPPED_PLUGIN_DIRS


def test_prune_removes_useless_plugins_and_keeps_runtime_plugins(tmp_path):
    module = load_prune()
    dist, pyside = _make_dist(tmp_path)
    plugins = pyside / "Qt" / "plugins"
    for name in module.KEPT_PLUGIN_DIRS:
        _touch(plugins / name / f"libq{name}.dylib")
    for name in module.DROPPED_PLUGIN_DIRS:
        _touch(plugins / name / f"libq{name}.dylib")
    # Un greffon inconnu n'est pas supprimé : seule la liste noire est appliquée.
    _touch(plugins / "wayland" / "libqwayland.dylib")

    module.prune(dist)

    assert {path.name for path in plugins.iterdir()} == set(module.KEPT_PLUGIN_DIRS) | {"wayland"}
    for name in module.KEPT_PLUGIN_DIRS:
        assert any((plugins / name).iterdir())


def test_prune_removes_qml_and_metatypes(tmp_path):
    module = load_prune()
    dist, pyside = _make_dist(tmp_path)
    qt = pyside / "Qt"
    _touch(qt / "qml" / "QtQuick" / "Controls" / "Button.qml")
    _touch(qt / "metatypes" / "qt6core_metatypes.json")
    _touch(qt / "plugins" / "imageformats" / "libqjpeg.metatypes.json")
    kept_plugin = _touch(qt / "plugins" / "imageformats" / "libqjpeg.dylib")

    module.prune(dist)

    assert not (qt / "qml").exists()
    assert not (qt / "metatypes").exists()
    assert not list(qt.rglob("*.metatypes.json"))
    assert kept_plugin.is_file()


def test_prune_removes_development_tools(tmp_path):
    module = load_prune()
    dist, pyside = _make_dist(tmp_path)
    qt = pyside / "Qt"
    tools = [
        pyside / "Assistant.app" / "Contents" / "MacOS" / "Assistant",
        pyside / "Designer.app" / "Contents" / "MacOS" / "Designer",
        pyside / "Linguist.app",
        pyside / "lrelease",
        pyside / "lupdate",
        pyside / "qmlformat",
        pyside / "qmllint",
        pyside / "balsam",
        qt / "libexec" / "uic",
        qt / "libexec" / "qmlcachegen",
        qt / "libexec" / "rcc",
    ]
    for path in tools:
        _touch(path)
    runtime = _touch(pyside / "QtCore.abi3.so")
    runtime_plugin = _touch(qt / "plugins" / "platforms" / "libqcocoa.dylib")

    module.prune(dist)

    for path in tools:
        assert not path.exists()
    assert runtime.is_file()
    assert runtime_plugin.is_file()


def test_prune_keeps_multimedia_plugin_and_libav_libraries(tmp_path):
    module = load_prune()
    dist, pyside = _make_dist(tmp_path)
    qt = pyside / "Qt"
    libraries = [
        _touch(qt / "lib" / "libavcodec.63.dylib"),
        _touch(qt / "lib" / "libavformat.63.dylib"),
        _touch(qt / "lib" / "libavutil.61.dylib"),
        _touch(qt / "lib" / "libswresample.7.dylib"),
        _touch(qt / "lib" / "libswscale.10.dylib"),
    ]
    plugin = _touch(qt / "plugins" / "multimedia" / "libffmpegmediaplugin.dylib")

    module.prune(dist)

    assert all(path.is_file() for path in libraries)
    assert plugin.is_file()


def test_prune_keeps_the_qtbase_catalog_of_each_of_the_28_languages(tmp_path):
    module = load_prune()
    dist, pyside = _make_dist(tmp_path)
    from azimut.core.i18n import LANGUAGES, qt_catalog_code

    registry = {f"qtbase_{qt_catalog_code(language.code)}.qm" for language in LANGUAGES}
    assert len(LANGUAGES) == 28
    assert len(registry) == 28
    extras = {"qtbase_bg.qm", "qtbase_hr.qm", "qtbase_sk.qm", "qtbase_nn.qm",
              "assistant_fr.qm", "designer_de.qm", "qt_fr.qm", "qtmultimedia_fr.qm"}
    translations = pyside / "Qt" / "translations"
    for name in registry | extras:
        _touch(translations / name)

    module.prune(dist)

    remaining = {path.name for path in translations.iterdir()}
    assert registry <= remaining
    assert remaining == registry | {"qtbase_nn.qm"}   # alias conservé pour « nb »
    for dropped in extras - {"qtbase_nn.qm"}:
        assert not (translations / dropped).exists()


def test_dry_run_reports_without_deleting(tmp_path):
    module = load_prune()
    dist, pyside = _make_dist(tmp_path)
    qml = _touch(pyside / "Qt" / "qml" / "QtQuick" / "qmltypes.qmltypes")

    removed = module.prune(dist, dry_run=True)

    assert qml.is_file()
    assert [path.name for path in removed] == ["qml"]


def test_package_data_no_longer_ships_ts_sources():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["azimut"]
    shipped_translations = [pattern for pattern in patterns
                            if pattern.startswith("resources/translations/")]

    assert shipped_translations == ["resources/translations/*.qm"]
    assert not any(pattern.endswith(".ts") for pattern in patterns)
    # Les sources .ts restent au dépôt : elles servent à regénérer les .qm.
    assert sorted(TRANSLATIONS.glob("azimut_*.ts"))
