"""Vérification de la sortie après un arrêt forcé : temporaires, tailles, médias illisibles."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
from pathlib import Path

import pytest

from azimut.modules.convert import engine


def test_partial_file_detection_follows_part_path(tmp_path):
    dest = tmp_path / "out" / "photo.avif"
    part = engine.part_path(dest)
    assert engine.is_partial_file(part)
    assert not engine.is_partial_file(dest)
    assert not engine.is_partial_file(dest.with_name(".photo.avif"))


def test_find_incomplete_files_walks_subfolders_and_ignores_finished_outputs(tmp_path):
    base = tmp_path / "S_Converted"
    (base / "a" / "b").mkdir(parents=True)
    part = engine.part_path(base / "a" / "b" / "video.webm")
    part.write_bytes(b"coupe")
    manifest_tmp = base / f"{engine.MANIFEST_NAME}.abcd1234.tmp"
    manifest_tmp.write_text("{}")
    (base / "a" / "b" / "video.webm").write_bytes(b"complet")
    found = {p.name for p in engine.find_incomplete_files(base)}
    assert found == {part.name, manifest_tmp.name}
    assert engine.find_incomplete_files(base, cancel=lambda: True) == []


def test_manifest_size_problems_flags_truncated_outputs_only(tmp_path):
    base = tmp_path / "S_Converted"
    base.mkdir()
    dest = base / "photo.avif"
    dest.write_bytes(b"abc")
    engine.save_manifest(base, {"photo.avif": {"dest_size": 10}, "absente.avif": {"dest_size": 4}})
    problems = engine.manifest_size_problems(base)
    assert [(p.path, p.reason) for p in problems] == [(dest, "taille")]
    assert "3" in problems[0].detail and "10" in problems[0].detail
    engine.save_manifest(base, {"photo.avif": {"dest_size": 3}})
    assert engine.manifest_size_problems(base) == []


def test_verify_worker_reports_interrupted_temporary_and_counts_suspects(qapp, tmp_path):
    from azimut.modules.convert.workers import VerifyWorker

    base = tmp_path / "S_Converted"
    base.mkdir()
    (base / "ok.jpg").write_bytes(b"fini")
    part = engine.part_path(base / "ok.avif")
    part.write_bytes(b"coupe")
    worker = VerifyWorker([base], None)
    result = worker.do_work()
    assert [d.path for d in result.damaged] == [part]
    assert result.damaged[0].reason == "partiel"
    assert result.checked == 0 and worker.error_count == 1 and worker.ok_count == 0
    assert "1" in worker.summary


def test_verify_worker_reports_missing_output_folder(qapp, tmp_path):
    from azimut.modules.convert.workers import VerifyWorker

    worker = VerifyWorker([tmp_path / "absente"], None)
    result = worker.do_work()
    assert result.damaged == [] and result.checked == 0
    assert worker.summary


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe absent")
def test_verify_worker_flags_unreadable_media_and_ignores_images(qapp, tmp_path):
    from azimut.modules.convert.workers import VerifyWorker

    base = tmp_path / "S_Converted"
    base.mkdir()
    broken = base / "casse.mp4"
    broken.write_bytes(b"pas un media")
    (base / "image.jpg").write_bytes(b"pas un media")
    worker = VerifyWorker([base], Path(shutil.which("ffprobe")))
    result = worker.do_work()
    assert result.checked == 1
    assert [(d.path, d.reason) for d in result.damaged] == [(broken, "illisible")]
