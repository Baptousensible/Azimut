"""Noms de fichiers et de dossiers piégeux pour FFmpeg : « % » (motif de séquence), tiret initial, « : », accents…

FFmpeg lit « photo%03d.jpg » comme une suite d'images numérotées, et fait de même avec un dossier « 100%d » dans le
chemin de sortie : ces fichiers échouaient (« Could find no file with path… », « Could not open file… »).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from azimut.modules.convert import engine, profiles

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg et ffprobe requis")


# ── Logique pure ─────────────────────────────────────────────────────────────
def test_part_path_never_carries_a_sequence_pattern():
    part = engine.part_path(Path("/out/photo%03d.webp"))
    assert "%" not in part.name and part.suffix == ".webp" and part.name.startswith(".photo_03d.")
    assert engine.part_path(Path("/out/normal.jpg")).name.startswith(".normal.")


def test_part_path_stays_within_the_255_byte_filename_limit():
    for stem in ("a" * 250, "é" * 120, "日本語" * 40, "x" * 235):
        for ext in (".webp", ".opus", ".mkv"):
            name = engine.part_path(Path("/out") / f"{stem}{ext}").name
            assert len(name.encode("utf-8")) <= 255, (stem[:5], ext, len(name.encode("utf-8")))
            assert name.endswith(f".part{ext}") and name.startswith(".")


def test_part_path_short_names_are_unchanged_apart_from_the_marker():
    name = engine.part_path(Path("/out/photo.jpg")).name
    assert name.startswith(".photo.") and name.endswith(".part.jpg")


def test_neutral_input_leaves_normal_paths_alone(tmp_path):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"x")
    assert engine.neutral_input(src) == (src, None)


def test_neutral_input_aliases_percent_paths_then_cleans_up(tmp_path):
    folder = tmp_path / "100%d"
    folder.mkdir()
    src = folder / "photo%03d.JPG"
    src.write_bytes(b"data")
    alias, scratch = engine.neutral_input(src)
    try:
        assert "%" not in str(alias) and alias.suffix == ".jpg" and alias.read_bytes() == b"data"
        assert scratch is not None and scratch.is_dir()
    finally:
        engine.drop_neutral_input(scratch)
    assert not scratch.exists() and src.read_bytes() == b"data"          # la source n'est jamais touchée
    engine.drop_neutral_input(None)                                       # sans effet


# ── Bout en bout (vrai FFmpeg, vrai worker) ──────────────────────────────────
def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _run_worker(worker) -> None:
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    worker.finished.connect(app.quit)
    worker.start()
    app.exec()
    worker.wait()


def _convert_images(tmp_path: Path, profile_id: str):
    from azimut.modules.convert.workers import ConvertOptions, ConvertWorker
    out = tmp_path / "out"
    items = engine.scan_inputs([tmp_path / "in"]).items
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind["image"] = profiles.get_profile(profile_id)
    worker = ConvertWorker(items, by_kind, out, Path(shutil.which("ffmpeg")), Path(shutil.which("ffprobe")), None,
                           options=ConvertOptions(takeout=False, memory_guard=False))
    logs: list[tuple[str, str]] = []
    worker.log.connect(lambda level, text: logs.append((level, text)))
    _run_worker(worker)
    return out, worker, logs


@needs_ffmpeg
@pytest.mark.parametrize("profile_id", ["image_webp", "image_png", "image_jpeg"])
def test_images_with_sequence_patterns_in_file_and_folder_names(tmp_path, qapp, profile_id):
    folder = tmp_path / "in" / "dir 100%d"
    folder.mkdir(parents=True)
    _ff("-f", "lavfi", "-i", "color=c=red:s=64x48:d=1", "-frames:v", "1", str(tmp_path / "base.png"))
    for name in ("photo%03d.png", "%d.png", "100%.png", "-tiret.png", "été (1).png"):
        shutil.copy(tmp_path / "base.png", folder / name)
    out, worker, logs = _convert_images(tmp_path, profile_id)
    assert worker.error_count == 0, [t for lvl, t in logs if lvl == "ERR"]
    produced = sorted(p.stem for p in (out / "dir 100%d").iterdir() if not p.name.startswith("."))
    # Chaque image doit ressortir sous son nom d'origine (convertie, ou source conservée si le résultat grossit).
    assert produced == sorted(["photo%03d", "%d", "100%", "-tiret", "été (1)"])
    assert not [p for p in out.rglob("*.part*")]                 # aucun temporaire oublié


def test_neutral_input_falls_back_to_a_symlink_before_copying(tmp_path, monkeypatch):
    """Hors lien physique (autre volume), un lien symbolique évite de recopier un gros fichier."""
    import os
    import sys
    if sys.platform == "win32":
        pytest.skip("liens symboliques : privilège requis sous Windows")
    src = tmp_path / "100%d.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("autre volume")))
    alias, scratch = engine.neutral_input(src)
    try:
        assert alias.is_symlink() and alias.read_bytes() == b"video"
    finally:
        engine.drop_neutral_input(scratch)
    assert src.read_bytes() == b"video"
