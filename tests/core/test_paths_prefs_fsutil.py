"""Tests : paths, prefs, logs, fsutil, module_spec."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import collections
import dataclasses
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from azimut.core import paths
from azimut.core.fsutil import (atomic_write_text, format_duration, format_size, iter_files,
                                safe_relative, unique_path)
from azimut.core.logs import get_logger, setup_logging
from azimut.core.module_spec import MODULE_IDS, ModuleSpec, load_window_class
from azimut.core.prefs import Preferences


# ── paths ────────────────────────────────────────────────────────────────
def test_user_dirs_created_and_nested():
    data = paths.user_data_dir()
    assert data.is_dir() and paths.user_config_dir().is_dir()
    assert paths.user_log_dir() == data / "logs" and paths.user_log_dir().is_dir()
    assert paths.user_cache_dir() == data / "cache" and paths.user_cache_dir().is_dir()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Spécifique à Linux (XDG)")
def test_linux_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    assert paths.user_data_dir() == tmp_path / "d" / "azimut"
    assert paths.user_config_dir() == tmp_path / "c" / "azimut"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert paths.user_data_dir() == Path.home() / ".local" / "share" / "azimut"


def test_app_root_dev_and_resources():
    assert not paths.is_frozen()
    assert (paths.app_root() / "azimut" / "core" / "paths.py").is_file()
    assert paths.resource_path("x.png") == paths.app_root() / "azimut" / "resources" / "x.png"


def test_frozen_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.is_frozen()
    assert paths.app_root() == tmp_path
    assert paths.resource_path("a") == tmp_path / "azimut" / "resources" / "a"
    monkeypatch.delattr(sys, "_MEIPASS")
    assert paths.app_root() == Path(sys.executable).resolve().parent


# ── prefs ────────────────────────────────────────────────────────────────
def test_prefs_defaults(tmp_prefs):
    assert tmp_prefs.get("theme") == "dark"
    assert tmp_prefs.get("zoom") == 1.0
    assert tmp_prefs.get("binaries.ffmpeg") == ""
    assert tmp_prefs.get("inconnue") is None
    assert tmp_prefs.get("inconnue", 42) == 42


def test_prefs_set_get_and_signal(tmp_prefs):
    got: list = []
    tmp_prefs.changed.connect(lambda k, v: got.append((k, v)))
    tmp_prefs.set("theme", "light")
    tmp_prefs.set("last_dir", "/tmp/x")
    assert tmp_prefs.get("theme") == "light"
    assert got == [("theme", "light"), ("last_dir", "/tmp/x")]
    assert Preferences(tmp_prefs.path).get("theme") == "light"      # relecture depuis le disque


def test_prefs_validation(tmp_prefs):
    tmp_prefs.set("theme", "bleu")
    assert tmp_prefs.get("theme") == "dark"
    tmp_prefs.set("zoom", 5)
    assert tmp_prefs.get("zoom") == 1.6
    tmp_prefs.set("zoom", 0.1)
    assert tmp_prefs.get("zoom") == 0.8
    tmp_prefs.set("zoom", "abc")
    assert tmp_prefs.get("zoom") == 1.0
    tmp_prefs.set("binaries.ffmpeg", 12)
    assert tmp_prefs.get("binaries.ffmpeg") == ""


def test_prefs_corrupt_file_tolerated(qapp, tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{pas du json", encoding="utf-8")
    p = Preferences(f)
    assert p.get("theme") == "dark" and p.get("zoom") == 1.0
    p.set("theme", "light")                                          # réécrit un fichier valide
    assert json.loads(f.read_text(encoding="utf-8"))["theme"] == "light"
    f.write_text("[1, 2]", encoding="utf-8")                         # JSON valide mais pas un objet
    assert Preferences(f).get("theme") == "dark"


def test_module_prefs_prefix(tmp_prefs):
    mp = tmp_prefs.module("convert")
    assert mp.get("quality", 7) == 7
    mp.set("quality", 5)
    assert mp.get("quality") == 5
    assert tmp_prefs.get("modules.convert.quality") == 5
    assert "modules.convert.quality" in json.loads(tmp_prefs.path.read_text(encoding="utf-8"))
    assert tmp_prefs.module("tags").get("quality") is None


def test_prefs_singleton(qapp):
    assert Preferences.instance() is Preferences.instance()


# ── logs ─────────────────────────────────────────────────────────────────
def test_get_logger_names():
    assert get_logger("binaries").name == "azimut.binaries"
    assert get_logger("azimut.core.x").name == "azimut.core.x"


def test_setup_logging_idempotent():
    logger = logging.getLogger("azimut")
    try:
        p1, p2 = setup_logging(), setup_logging()
        assert p1 == p2 == paths.user_log_dir() / "azimut.log"
        handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(handlers) == 1
        get_logger("test").info("bonjour le journal")
        for h in handlers:
            h.flush()
        assert "bonjour le journal" in p1.read_text(encoding="utf-8")
    finally:
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()


# ── fsutil ───────────────────────────────────────────────────────────────
def test_format_size():
    assert format_size(0) == "0 o"
    assert format_size(512) == "512 o"
    assert format_size(1536) == "1,5 Ko"
    assert format_size(1024 * 1024) == "1,0 Mo"
    assert format_size(1024 ** 3 * 2.5) == "2,5 Go"
    assert format_size(1024 ** 2 - 1) == "1,0 Mo"          # pas de « 1024,0 Ko »


def test_format_duration():
    assert format_duration(12) == "12 s"
    assert format_duration(60) == "1 min 00 s"
    assert format_duration(185) == "3 min 05 s"
    assert format_duration(3723) == "1 h 02 min 03 s"
    assert format_duration(-5) == "0 s"


def test_atomic_write_text(tmp_path):
    f = tmp_path / "sous" / "a.txt"
    atomic_write_text(f, "é\nligne")
    assert f.read_text(encoding="utf-8") == "é\nligne"
    atomic_write_text(f, "nouveau")
    assert f.read_text(encoding="utf-8") == "nouveau"
    assert [p.name for p in f.parent.iterdir()] == ["a.txt"]         # aucun temporaire résiduel


def test_unique_path(tmp_path):
    dest = tmp_path / "nom.ext"
    assert unique_path(dest) == dest
    dest.write_text("x")
    assert unique_path(dest) == tmp_path / "nom__1.ext"
    (tmp_path / "nom__1.ext").write_text("x")
    assert unique_path(dest) == tmp_path / "nom__2.ext"


def test_safe_relative(tmp_path):
    assert safe_relative(tmp_path / "a" / "b.txt", tmp_path) == Path("a") / "b.txt"
    with pytest.raises(ValueError):
        safe_relative(tmp_path.parent / "ailleurs.txt", tmp_path)
    with pytest.raises(ValueError):
        safe_relative(tmp_path / ".." / "x", tmp_path)


def _tree(root: Path) -> None:
    for rel in ("a.jpg", "B.PNG", ".hidden.jpg", "sub/c.jpg", "sub/e.txt", "sub/.dot/d.jpg"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def test_iter_files_options(tmp_path):
    _tree(tmp_path)
    names = lambda it: sorted(p.name for p in it)
    assert names(iter_files(tmp_path)) == ["B.PNG", "a.jpg", "c.jpg", "e.txt"]
    assert names(iter_files(tmp_path, extensions=[".jpg"])) == ["a.jpg", "c.jpg"]
    assert names(iter_files(tmp_path, extensions={".png", ".txt"})) == ["B.PNG", "e.txt"]
    assert names(iter_files(tmp_path, extensions=[".jpg"], skip_hidden=False)) == [
        ".hidden.jpg", "a.jpg", "c.jpg", "d.jpg"]
    assert names(iter_files(tmp_path, recursive=False, extensions=[".jpg"])) == ["a.jpg"]


def test_iter_files_cancel(tmp_path):
    _tree(tmp_path)
    flag = {"stop": False}
    out = []
    for p in iter_files(tmp_path, cancel=lambda: flag["stop"]):
        out.append(p)
        flag["stop"] = True
    assert len(out) == 1
    assert list(iter_files(tmp_path, cancel=lambda: True)) == []


def test_iter_files_missing_root(tmp_path):
    assert list(iter_files(tmp_path / "absent")) == []


# ── module_spec ──────────────────────────────────────────────────────────
def test_module_spec_and_loader():
    assert MODULE_IDS == ("convert", "tags", "takeout", "dedup", "press")
    spec = ModuleSpec(id="x", name="Azimut X", tagline="Test.", icon="🧪", entry="collections:OrderedDict")
    assert spec.requires == () and spec.optional == () and spec.order == 100
    assert load_window_class(spec) is collections.OrderedDict
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.id = "y"                                                 # type: ignore[misc]
    for bad in ("azimut.inexistant:Foo", "collections:Nope", "sansdeuxpoints", ":Classe"):
        with pytest.raises(ImportError):
            load_window_class(dataclasses.replace(spec, entry=bad))
