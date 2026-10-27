"""Tests : localisation des binaires (système de fichiers simulé, aucun vrai binaire requis)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from azimut.core import binaries
from azimut.core.binaries import (KNOWN_BINARIES, BinaryInfo, BinaryNotFoundError, bundled_dir, platform_tag,
                                  probe_version, refresh, require, resolve)

EXE = ".exe" if sys.platform == "win32" else ""


@pytest.fixture(autouse=True)
def _clean_cache():
    binaries._cache.clear()
    yield
    binaries._cache.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Racine d'application factice, version simulée, PATH système vide (modifiable via ``env.system``)."""
    root = tmp_path / "app"
    monkeypatch.setattr(binaries, "app_root", lambda: root)
    monkeypatch.setattr(binaries, "probe_version", lambda name, path: "9.9")

    class Env:
        system: dict[str, str] = {}
        bundled = bundled_dir()

    state = Env()
    state.system = {}
    monkeypatch.setattr(binaries.shutil, "which", lambda name, *a, **k: state.system.get(name))
    return state


def make_exe(path: Path, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_platform_tag_and_bundled_dir(monkeypatch, tmp_path):
    assert platform_tag() in ("windows-x64", "linux-x64", "macos-arm64", "macos-x64")
    cases = [("win32", "AMD64", "windows-x64"), ("linux", "x86_64", "linux-x64"),
             ("darwin", "arm64", "macos-arm64"), ("darwin", "x86_64", "macos-x64")]
    for plat, machine, tag in cases:
        with monkeypatch.context() as m:
            m.setattr(sys, "platform", plat)
            m.setattr(binaries.platform, "machine", lambda mach=machine: mach)
            assert platform_tag() == tag
    monkeypatch.setattr(binaries, "app_root", lambda: tmp_path)
    assert bundled_dir() == tmp_path / "bin" / platform_tag()


def test_binary_info_available_and_frozen():
    assert BinaryInfo("x", Path("/a"), "system", "1").available
    missing = BinaryInfo("x", None, "missing", "")
    assert not missing.available
    with pytest.raises(dataclasses.FrozenInstanceError):
        missing.name = "y"                                            # type: ignore[misc]
    assert KNOWN_BINARIES == ("ffmpeg", "ffprobe", "exiftool")


def test_resolution_order(env, tmp_prefs, tmp_path):
    # ffmpeg : embarqué + personnalisé + système → embarqué
    bundled = make_exe(env.bundled / f"ffmpeg{EXE}")
    custom_ffmpeg = make_exe(tmp_path / "perso" / "ffmpeg-perso")
    tmp_prefs.set("binaries.ffmpeg", str(custom_ffmpeg))
    env.system["ffmpeg"] = "/usr/bin/ffmpeg"
    # ffprobe : personnalisé + système → personnalisé
    custom_probe = make_exe(tmp_path / "perso" / "ffprobe-perso")
    tmp_prefs.set("binaries.ffprobe", str(custom_probe))
    env.system["ffprobe"] = "/usr/bin/ffprobe"
    # exiftool : système seul
    env.system["exiftool"] = "/usr/bin/exiftool"

    a = resolve("ffmpeg", tmp_prefs)
    assert (a.source, a.path, a.version, a.available) == ("bundled", bundled, "9.9", True)
    b = resolve("ffprobe", tmp_prefs)
    assert (b.source, b.path) == ("custom", custom_probe)
    c = resolve("exiftool", tmp_prefs)
    assert (c.source, c.path) == ("system", Path("/usr/bin/exiftool"))


def test_missing(env, tmp_prefs):
    info = resolve("ffmpeg", tmp_prefs)
    assert info.source == "missing" and info.path is None and info.version == "" and not info.available


def test_invalid_custom_path_ignored(env, tmp_prefs, tmp_path, caplog):
    tmp_prefs.set("binaries.ffmpeg", str(tmp_path / "n-existe-pas"))
    env.system["ffmpeg"] = "/usr/bin/ffmpeg"
    with caplog.at_level(logging.WARNING, logger="azimut"):
        info = resolve("ffmpeg", tmp_prefs)
    assert info.source == "system"
    assert any(r.levelno == logging.WARNING and "ffmpeg" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(sys.platform == "win32", reason="Bit d'exécution inexistant sous Windows")
def test_bundled_not_executable_is_fixed_or_skipped(env, tmp_prefs, monkeypatch):
    path = make_exe(env.bundled / "ffmpeg", mode=0o644)
    assert resolve("ffmpeg", tmp_prefs).source == "bundled"           # chmod u+x réussi
    assert os.access(path, os.X_OK)

    make_exe(env.bundled / "ffprobe", mode=0o644)
    env.system["ffprobe"] = "/usr/bin/ffprobe"

    def boom(*args, **kwargs):
        raise OSError("lecture seule")

    monkeypatch.setattr(binaries.os, "chmod", boom)
    info = resolve("ffprobe", tmp_prefs)
    assert info.source == "system"                                    # embarqué ignoré, candidat suivant


def test_require(env, tmp_prefs):
    env.system["ffmpeg"] = "/usr/bin/ffmpeg"
    assert require("ffmpeg", tmp_prefs) == Path("/usr/bin/ffmpeg")
    with pytest.raises(BinaryNotFoundError) as exc:
        require("exiftool", tmp_prefs)
    assert "exiftool" in str(exc.value) and "introuvable" in str(exc.value)
    assert isinstance(exc.value, RuntimeError)


def test_cache_and_refresh(env, tmp_prefs, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(binaries, "probe_version", lambda name, path: calls.append(name) or "1.0")
    env.system["ffmpeg"] = "/usr/bin/ffmpeg"
    first = resolve("ffmpeg", tmp_prefs)
    assert resolve("ffmpeg", tmp_prefs) is first and calls == ["ffmpeg"]   # un seul sondage
    env.system.pop("ffmpeg")
    assert resolve("ffmpeg", tmp_prefs).source == "system"                 # toujours en cache
    result = refresh(tmp_prefs)
    assert set(result) == set(KNOWN_BINARIES)
    assert result["ffmpeg"].source == "missing"                            # cache vidé puis re-détecté
    assert resolve("ffmpeg", tmp_prefs) is result["ffmpeg"]


def test_refresh_logs(env, tmp_prefs, caplog):
    make_exe(env.bundled / f"ffmpeg{EXE}")
    env.system["exiftool"] = "/usr/bin/exiftool"
    with caplog.at_level(logging.INFO, logger="azimut"):
        refresh(tmp_prefs)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ffmpeg 9.9" in m and "embarqué" in m for m in infos)
    assert any("exiftool 9.9" in m and "système" in m for m in infos)
    assert any("ffprobe introuvable" in m for m in warns)


# ── probe_version ────────────────────────────────────────────────────────
class FakeRun:
    def __init__(self, stdout: str = "", returncode: int = 0, exc: Exception | None = None) -> None:
        self.stdout, self.returncode, self.exc, self.calls = stdout, returncode, exc, []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def test_probe_version_parsing(monkeypatch):
    fake = FakeRun("ffmpeg version 7.1 Copyright (c) 2000-2024\nbuilt with gcc\n")
    monkeypatch.setattr(binaries.subprocess, "run", fake)
    assert probe_version("ffmpeg", Path("/x/ffmpeg")) == "7.1"
    args, kwargs = fake.calls[0]
    assert args == [str(Path("/x/ffmpeg")), "-version"] and kwargs["timeout"] == 10

    monkeypatch.setattr(binaries.subprocess, "run", FakeRun("ffprobe version n6.0-12-gabc Copyright\n"))
    assert probe_version("ffprobe", Path("/x/ffprobe")) == "n6.0-12-gabc"

    fake = FakeRun("12.76\n")
    monkeypatch.setattr(binaries.subprocess, "run", fake)
    assert probe_version("exiftool", Path("/x/exiftool")) == "12.76"
    assert fake.calls[0][0][-1] == "-ver"


def test_probe_version_failures(monkeypatch):
    monkeypatch.setattr(binaries.subprocess, "run", FakeRun(exc=subprocess.TimeoutExpired("x", 10)))
    assert probe_version("ffmpeg", Path("/x")) == ""
    monkeypatch.setattr(binaries.subprocess, "run", FakeRun(exc=OSError("illisible")))
    assert probe_version("exiftool", Path("/x")) == ""
    monkeypatch.setattr(binaries.subprocess, "run", FakeRun("ffmpeg version 7.1\n", returncode=1))
    assert probe_version("ffmpeg", Path("/x")) == ""
    monkeypatch.setattr(binaries.subprocess, "run", FakeRun("n'importe quoi\n"))
    assert probe_version("ffmpeg", Path("/x")) == ""
    assert probe_version("inconnu", Path("/x")) == ""
