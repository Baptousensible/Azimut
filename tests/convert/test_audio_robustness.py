"""Bout en bout réel : un audio dont le contenu est masqué ne doit jamais ressortir en « succès » vide.

Cas rapporté : « Sonnerie - Nokia.m4a » → Opus échouait avec « Could not open encoder before EOF » (FFmpeg 7).
Reconstitué ici : un M4A dont la liste d'édition (`elst`) saute tous les échantillons. FFmpeg 6 produit alors un
fichier Opus de 283 octets avec le code 0 ; FFmpeg 7 échoue. Dans les deux cas, Azimut doit reprendre la lecture
en ignorant la liste d'édition, ou signaler une erreur — jamais compter un fichier vide comme converti.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from azimut.modules.convert import engine, profiles

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg et ffprobe requis")


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _duration(path: Path) -> float:
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        return 0.0
    try:
        return float(json.loads(res.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _run_worker(worker) -> None:
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    out: dict = {}
    worker.completed.connect(lambda r: (out.setdefault("r", r), app.quit()))
    worker.failed.connect(lambda m: (out.setdefault("err", m), app.quit()))
    worker.start()
    app.exec()
    worker.wait()
    assert "err" not in out, out.get("err")


def _convert_audio(tmp_path: Path, logs: list[tuple[str, str]]):
    from azimut.modules.convert.workers import ConvertOptions, ConvertWorker
    out = tmp_path / "out"
    items = engine.scan_inputs([tmp_path / "in"]).items
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind["audio"] = profiles.get_profile("audio_opus")
    worker = ConvertWorker(items, by_kind, out, Path(shutil.which("ffmpeg")), Path(shutil.which("ffprobe")),
                           None, options=ConvertOptions(takeout=False, memory_guard=False))
    worker.log.connect(lambda level, text: logs.append((level, text)))
    _run_worker(worker)
    return out, worker


def _make_editlist_hiding_everything(dest: Path) -> None:
    """M4A valide dont l'unique entrée `elst` débute après le dernier échantillon."""
    tmp = dest.with_name("plain.m4a")
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "2", "-c:a", "aac", str(tmp))
    data = bytearray(tmp.read_bytes())
    tmp.unlink()
    i = data.find(b"elst")
    assert i > 0, "ce ffmpeg n'écrit pas de liste d'édition : cas non reproductible"
    assert data[i + 4] == 0, "elst version 0 attendue"
    assert struct.unpack(">I", data[i + 8:i + 12])[0] >= 1
    data[i + 16:i + 20] = struct.pack(">i", 0x7FFFFFF0)       # media_time de la première entrée
    dest.write_bytes(bytes(data))


@needs_ffmpeg
def test_hidden_content_is_recovered_not_reported_as_empty_success(tmp_path, qapp):
    src = tmp_path / "in"
    src.mkdir()
    _make_editlist_hiding_everything(src / "Sonnerie - Nokia.m4a")
    logs: list[tuple[str, str]] = []
    out, worker = _convert_audio(tmp_path, logs)
    produced = sorted(out.glob("*.opus"))
    assert len(produced) == 1, [p.name for p in out.iterdir()]
    assert _duration(produced[0]) > 2.0                       # un vrai contenu, pas 283 octets sans paquet
    assert produced[0].stat().st_size > 5000
    assert worker.ok_count == 1 and worker.error_count == 0


@needs_ffmpeg
def test_normal_audio_converts_without_any_retry(tmp_path, qapp):
    src = tmp_path / "in"
    src.mkdir()
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame", str(src / "a.mp3"))
    _ff("-f", "lavfi", "-i", "sine=frequency=330:duration=2", "-ac", "1", "-c:a", "aac", str(src / "b.m4a"))
    logs: list[tuple[str, str]] = []
    out, worker = _convert_audio(tmp_path, logs)
    assert sorted(p.name for p in out.glob("*.opus")) == ["a.opus", "b.opus"]
    assert all(_duration(p) > 1.5 for p in out.glob("*.opus"))
    assert worker.error_count == 0
    assert not [t for lvl, t in logs if "tentative" in t]       # aucune reprise sur des fichiers sains


@needs_ffmpeg
def test_broken_non_mp4_audio_gets_a_truthful_error_not_an_option_error(tmp_path, qapp):
    """Un MP3 illisible ne doit pas échouer sur « Option ignore_editlist not found » (faux diagnostic)."""
    src = tmp_path / "in"
    src.mkdir()
    (src / "broken.mp3").write_bytes(b"ID3" + os.urandom(4000))
    logs: list[tuple[str, str]] = []
    out, worker = _convert_audio(tmp_path, logs)
    assert not any(_duration(p) > 0 for p in out.glob("*.opus"))
    assert not any("ignore_editlist" in t for _lvl, t in logs)


@needs_ffmpeg
def test_unknown_channel_layout_is_recovered_by_the_filterless_retry(tmp_path, qapp):
    """M4A multicanal dont le conteneur n'indique pas la disposition : `aformat` échoue, la reprise sans filtre aboutit."""
    src = tmp_path / "in"
    src.mkdir()
    _ff("-f", "lavfi", "-i", "anoisesrc=d=2:r=44100,aformat=channel_layouts=5.1(side)", "-c:a", "aac",
        str(src / "surround.m4a"))
    logs: list[tuple[str, str]] = []
    out, worker = _convert_audio(tmp_path, logs)
    produced = sorted(out.glob("*.opus"))
    assert len(produced) == 1 and _duration(produced[0]) > 1.5
    assert worker.ok_count == 1 and worker.error_count == 0
