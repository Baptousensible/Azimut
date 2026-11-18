"""Option « Ne pas recopier les JSON Takeout déjà réinjectés » : jamais de suppression, jamais de perte."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from azimut.modules.convert import engine, profiles
from azimut.modules.convert.workers import ConvertOptions, ConvertWorker

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg et ffprobe requis")

TAKEN = {"title": "clip.mp3", "photoTakenTime": {"timestamp": "1600000000"},
         "geoData": {"latitude": 48.58, "longitude": 7.75, "altitude": 140.0}}


def _make_tree(root: Path) -> None:
    import subprocess
    src = root / "in"
    src.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(src / "clip.mp3")],
                   check=True)
    (src / "clip.mp3.json").write_text(json.dumps(TAKEN), encoding="utf-8")          # compagnon exploitable
    (src / "notes.json").write_text('{"a": 1}', encoding="utf-8")                      # document sans rapport
    (src / "orphan.jpg.json").write_text(json.dumps(TAKEN), encoding="utf-8")        # JSON sans média
    (src / "empty.mp3").write_bytes(b"")                                              # média inutilisable
    (src / "empty.mp3.json").write_text(json.dumps({"title": "empty.mp3"}), encoding="utf-8")   # sans date ni GPS


def _run(tmp_path: Path, *, skip: bool, exiftool: Path | None):
    out = tmp_path / "out"
    items = engine.scan_inputs([tmp_path / "in"], True, include_others=True).items
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind["audio"] = profiles.get_profile("audio_opus")
    worker = ConvertWorker(items, by_kind, out, Path(shutil.which("ffmpeg")), Path(shutil.which("ffprobe")), None,
                           options=ConvertOptions(takeout=True, skip_takeout_json=skip, memory_guard=False),
                           exiftool=exiftool)
    logs: list[tuple[str, str]] = []
    worker.log.connect(lambda level, text: logs.append((level, text)))
    worker.do_work() if os.environ.get("AZIMUT_SYNC_WORKER") else _run_qt(worker)
    return out, logs, tmp_path / "in"


def _run_qt(worker) -> None:
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    worker.finished.connect(app.quit)
    worker.start()
    app.exec()
    worker.wait()


@needs_ffmpeg
def test_option_off_copies_every_json(tmp_path, qapp):
    _make_tree(tmp_path)
    out, _logs, _src = _run(tmp_path, skip=False, exiftool=Path(sys.executable))
    assert sorted(p.name for p in out.glob("*.json") if not p.name.startswith(".")) == [
        "clip.mp3.json", "empty.mp3.json", "notes.json", "orphan.jpg.json"]


@needs_ffmpeg
def test_option_on_skips_only_the_consumed_companion(tmp_path, qapp):
    _make_tree(tmp_path)
    out, logs, src = _run(tmp_path, skip=True, exiftool=Path(sys.executable))
    copied = sorted(p.name for p in out.glob("*.json") if not p.name.startswith("."))
    assert "clip.mp3.json" not in copied                       # réinjecté : inutile dans la sortie
    assert "notes.json" in copied                              # document sans rapport : conservé
    assert "orphan.jpg.json" in copied                         # sans média dans la file : conservé
    assert "empty.mp3.json" in copied                          # sans date ni position : rien n'a été réinjecté
    assert any("JSON Takeout non recopié" in t for _l, t in logs)
    # jamais de suppression dans la source
    assert sorted(p.name for p in src.glob("*.json")) == ["clip.mp3.json", "empty.mp3.json", "notes.json", "orphan.jpg.json"]
    assert not any("absent" in t for lvl, t in logs if lvl == "ERR")   # le contrôle final ne crie pas à l'absence


@needs_ffmpeg
def test_without_exiftool_json_are_kept(tmp_path, qapp):
    """Sans ExifTool, rien n'est écrit dans les fichiers : le JSON est la seule trace des données."""
    _make_tree(tmp_path)
    out, logs, _src = _run(tmp_path, skip=True, exiftool=None)
    assert (out / "clip.mp3.json").exists()
    assert any("ExifTool introuvable" in t and "recopiés" in t for _l, t in logs)
