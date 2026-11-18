"""Tests : localisation des binaires (système de fichiers simulé, aucun vrai binaire requis)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
import io
import logging
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from azimut.core import binaries
from azimut.core.binaries import (KNOWN_BINARIES, BinaryInfo, BinaryNotFoundError, bundled_dir, custom_path,
                                  is_outdated, platform_tag, probe_version, refresh, refresh_in_progress,
                                  require, resolve)

EXE = ".exe" if sys.platform == "win32" else ""


@pytest.fixture(autouse=True)
def _clean_cache():
    binaries._cache.clear()
    yield
    binaries._cache.clear()
    thread = getattr(binaries, "_refresh_thread", None)
    if thread is not None:
        thread.join(10)                                  # aucune sonde d'arrière-plan ne fuit d'un test à l'autre


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
    assert platform_tag() in ("windows-x64", "linux-x64", "macos-arm64", "macos-x64",
                              "windows-arm64", "linux-arm64")
    cases = [("win32", "AMD64", "windows-x64"), ("linux", "x86_64", "linux-x64"),
             ("darwin", "arm64", "macos-arm64"), ("darwin", "x86_64", "macos-x64"),
             ("win32", "ARM64", "windows-arm64"), ("linux", "aarch64", "linux-arm64")]   # AZ-C04
    for plat, machine, tag in cases:
        with monkeypatch.context() as m:
            m.setattr(sys, "platform", plat)
            m.setattr(binaries.platform, "machine", lambda mach=machine: mach)
            assert platform_tag() == tag
    monkeypatch.setattr(binaries, "app_root", lambda: tmp_path)
    assert bundled_dir() == tmp_path / "bin" / platform_tag()


def test_binary_versions_outdated():
    """ExifTool < 12.24 (CVE-2021-22204) et FFmpeg < 6 sont signalés (AZ-009)."""
    assert is_outdated("exiftool", "12.20") is True
    assert is_outdated("exiftool", "12.76") is False
    assert is_outdated("ffmpeg", "5.1.2") is True
    assert is_outdated("ffmpeg", "n6.0-12-gabc") is False
    assert is_outdated("ffmpeg", "") is False and is_outdated("inconnu", "1.0") is False
    assert BinaryInfo("x", Path("/a"), "system", "1", outdated=True).outdated


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


def test_custom_path_rules(tmp_path, monkeypatch):
    """Chemin saisi utilisable : ~ développé, absolu et exécutable ; relatif ou absent refusé."""
    exe = make_exe(tmp_path / "mon-ffmpeg")
    assert custom_path(str(exe)) == exe
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert custom_path(f"~{os.sep}{exe.name}") == exe
    assert custom_path(f"~/{exe.name}") == exe
    assert custom_path(exe.name) is None
    assert custom_path(str(tmp_path / "n-existe-pas")) is None
    assert custom_path("") is None and custom_path("   ") is None


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


def test_relative_custom_path_is_not_resolved_against_cwd(env, tmp_prefs, tmp_path, monkeypatch, caplog):
    """Un chemin personnalisé relatif dépend du dossier courant : il ne doit pas être retenu (CORE-2),
    comme ``_system_path()`` écarte déjà les entrées relatives du PATH."""
    folder = tmp_path / "dossier-courant"
    make_exe(folder / f"ffmpeg{EXE}")
    monkeypatch.chdir(folder)
    tmp_prefs.set("binaries.ffmpeg", f"ffmpeg{EXE}")
    env.system["ffmpeg"] = "/usr/bin/ffmpeg"
    with caplog.at_level(logging.WARNING, logger="azimut"):
        info = resolve("ffmpeg", tmp_prefs)
    assert (info.source, info.path) == ("system", Path("/usr/bin/ffmpeg"))
    assert any(r.levelno == logging.WARNING and "relatif" in r.getMessage() for r in caplog.records)


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


def test_bundled_binary_without_readable_version_falls_back(env, tmp_prefs, monkeypatch, caplog):
    """Un binaire embarqué qui ne répond pas à sa sonde est invalide : le candidat suivant
    prend sa place (CORE-3)."""
    broken = make_exe(env.bundled / f"ffmpeg{EXE}")
    env.system["ffmpeg"] = "/usr/bin/ffmpeg"
    monkeypatch.setattr(binaries, "probe_version", lambda name, path: "" if Path(path) == broken else "9.9")
    with caplog.at_level(logging.WARNING, logger="azimut"):
        info = resolve("ffmpeg", tmp_prefs)
    assert (info.source, info.path, info.version) == ("system", Path("/usr/bin/ffmpeg"), "9.9")
    assert any(r.levelno == logging.WARNING and "embarqué" in r.getMessage() for r in caplog.records)


def test_custom_binary_without_readable_version_is_kept(env, tmp_prefs, tmp_path, monkeypatch):
    """Un chemin fourni par l'utilisateur peut être un script sans version lisible : il reste
    retenu (CORE-3, contrairement à l'embarqué)."""
    custom = make_exe(tmp_path / f"mon-ffmpeg{EXE}")
    monkeypatch.setattr(binaries, "probe_version", lambda name, path: "")
    tmp_prefs.set("binaries.ffmpeg", str(custom))
    info = resolve("ffmpeg", tmp_prefs)
    assert (info.source, info.path, info.version) == ("custom", custom, "")


def test_background_refresh_publishes_locations_then_versions(env, tmp_prefs, monkeypatch):
    """``refresh(background=True)`` rend la main sans sonde ; les versions arrivent ensuite (CORE-6)."""
    caller = threading.get_ident()
    calls: list[tuple[str, int]] = []

    def slow_probe(name, path):
        calls.append((name, threading.get_ident()))
        time.sleep(0.2)
        return "9.9"

    monkeypatch.setattr(binaries, "probe_version", slow_probe)
    for name in KNOWN_BINARIES:
        env.system[name] = f"/usr/bin/{name}"
    t0 = time.monotonic()
    located = refresh(tmp_prefs, background=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"refresh(background=True) a duré {elapsed:.2f} s"
    assert refresh_in_progress(), "la sonde d'arrière-plan n'est pas signalée comme active"
    assert located["ffmpeg"].available and located["ffmpeg"].version == ""
    assert all(ident != caller for _name, ident in calls), "une sonde a tourné sur le fil appelant"
    thread = binaries._refresh_thread
    assert thread is not None
    thread.join(10)
    assert not thread.is_alive()
    assert not refresh_in_progress()
    assert sorted(name for name, _ident in calls) == sorted(KNOWN_BINARIES)
    assert resolve("ffmpeg", tmp_prefs).version == "9.9"


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
class _FakeProc:
    """Processus simulé : stdout lisible, attente programmée."""

    pid = 12345

    def __init__(self, owner: "FakePopen") -> None:
        self.owner = owner
        self.returncode = owner.returncode
        self.stdout = io.BytesIO(owner.stdout.encode("utf-8"))

    def wait(self, timeout=None):
        self.owner.wait_timeout = timeout
        if self.owner.timeout and not self.owner.killed:
            raise subprocess.TimeoutExpired(self.owner.calls[-1][0], timeout)
        return self.returncode

    def poll(self):
        return None if self.owner.timeout and not self.owner.killed else self.returncode

    def kill(self):
        self.owner.killed = True
        self.returncode = -9


class FakePopen:
    """Double de Popen : enregistre la commande et le délai, sert une sortie programmée."""

    def __init__(self, stdout: str = "", returncode: int = 0, exc: Exception | None = None,
                 timeout: bool = False) -> None:
        self.stdout, self.returncode, self.exc, self.timeout = stdout, returncode, exc, timeout
        self.calls: list[tuple[list[str], dict]] = []
        self.wait_timeout: float | None = None
        self.killed = False

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc is not None:
            raise self.exc
        return _FakeProc(self)


def test_probe_version_parsing(monkeypatch):
    fake = FakePopen("ffmpeg version 7.1 Copyright (c) 2000-2024\nbuilt with gcc\n")
    monkeypatch.setattr(binaries.subprocess, "Popen", fake)
    assert probe_version("ffmpeg", Path("/x/ffmpeg")) == "7.1"
    args, kwargs = fake.calls[0]
    assert args == [str(Path("/x/ffmpeg")), "-version"]
    assert fake.wait_timeout == 10
    assert kwargs["start_new_session"] == (sys.platform != "win32")

    monkeypatch.setattr(binaries.subprocess, "Popen", FakePopen("ffprobe version n6.0-12-gabc Copyright\n"))
    assert probe_version("ffprobe", Path("/x/ffprobe")) == "n6.0-12-gabc"

    fake = FakePopen("12.76\n")
    monkeypatch.setattr(binaries.subprocess, "Popen", fake)
    assert probe_version("exiftool", Path("/x/exiftool")) == "12.76"
    assert fake.calls[0][0][-1] == "-ver"


def _no_group(pid: int) -> int:
    """Aucun vrai groupe : un test de timeout ne doit jamais envoyer de signal système."""
    raise OSError(f"PID factice {pid} : aucun groupe")


def test_probe_version_failures(monkeypatch):
    monkeypatch.setattr(binaries.os, "getpgid", _no_group, raising=False)
    # Windows : _kill_probe lance taskkill par subprocess.run. Sans ce double, l'appel
    # atteindrait le faux Popen (sans communicate) et le repli kill() ne serait pas vérifié.
    monkeypatch.setattr(binaries.subprocess, "run",
                        lambda args, **kwargs: subprocess.CompletedProcess(args, 1), raising=False)
    fake = FakePopen(timeout=True)
    monkeypatch.setattr(binaries.subprocess, "Popen", fake)
    assert probe_version("ffmpeg", Path("/x")) == ""
    assert fake.killed

    monkeypatch.setattr(binaries.subprocess, "Popen", FakePopen(exc=OSError("illisible")))
    assert probe_version("exiftool", Path("/x")) == ""
    monkeypatch.setattr(binaries.subprocess, "Popen", FakePopen("ffmpeg version 7.1\n", returncode=1))
    assert probe_version("ffmpeg", Path("/x")) == ""
    monkeypatch.setattr(binaries.subprocess, "Popen", FakePopen("n'importe quoi\n"))
    assert probe_version("ffmpeg", Path("/x")) == ""
    assert probe_version("inconnu", Path("/x")) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="groupes de processus POSIX")
def test_probe_version_timeout_kills_the_grandchild(tmp_path, monkeypatch):
    """Le timeout de la sonde tue le sous-arbre, pas seulement le script wrapper (SUBPROC-001)."""
    marker = tmp_path / "petit-fils.txt"
    child_code = f"import time, pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).write_text('vivant')"
    wrapper = tmp_path / "ffmpeg-lent"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(binaries, "_VERSION_TIMEOUT", 1.0)
    start = time.monotonic()
    assert probe_version("ffmpeg", wrapper) == ""
    assert time.monotonic() - start < 5
    time.sleep(3)
    assert not marker.exists(), "le petit-fils a survécu au timeout de la sonde"


@pytest.mark.skipif(sys.platform == "win32", reason="script à shebang")
def test_probe_version_bounds_a_talkative_output(tmp_path):
    """50 Mo écrits par un faux binaire ne sont pas chargés en mémoire (SUBPROC-002)."""
    flood = tmp_path / "ffmpeg-bavard"
    flood.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write('ffmpeg version 7.1\\n')\n"
        "sys.stdout.write('x' * (50 * 1024 * 1024))\n",
        encoding="utf-8")
    flood.chmod(0o755)
    tracemalloc.start()
    try:
        assert probe_version("ffmpeg", flood) == "7.1"
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f"pic mémoire {peak / 1024 / 1024:.1f} Mio"
