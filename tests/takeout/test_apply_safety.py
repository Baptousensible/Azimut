"""Sûreté de l'application Takeout : lecture groupée, dérive, garde-fous, annulation incrémentale."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from azimut.modules.takeout import engine as eng
from azimut.modules.takeout import workers as tk

TAKEN = 1_600_000_000
ROOT = Path(__file__).resolve().parents[2]


def make_items(folder: Path, count: int) -> list[eng.TakeoutItem]:
    payload = {"title": "x.jpg", "photoTakenTime": {"timestamp": str(TAKEN)},
               "geoData": {"latitude": 48.8584, "longitude": 2.2945}}
    for i in range(count):
        name = f"f{i:03d}.jpg"
        (folder / name).write_bytes(b"x")
        (folder / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return eng.analyze(folder).items


def fake_read(calls: list[list[Path]]):
    def read(self, paths, *, numeric: bool = False):
        calls.append([Path(p) for p in paths])
        return [{"SourceFile": str(p), "ExifIFD:DateTimeOriginal": "2019:01:01 00:00:00"} for p in paths]

    return read


def fake_write(writes: list[Path], on_write=None):
    def write(self, path, tag_args, *, numeric: bool = False, backup: bool = False):
        writes.append(Path(path))
        if on_write is not None:
            on_write(Path(path))
        return True, ""

    return write


def run_worker(worker) -> tuple[dict, list[tuple[str, str]]]:
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    out: dict = {}
    logs: list[tuple[str, str]] = []
    worker.log.connect(lambda level, text: logs.append((level, text)))
    worker.completed.connect(lambda result: (out.setdefault("result", result), app.quit()))
    worker.failed.connect(lambda msg: (out.setdefault("error", msg), app.quit()))
    worker.start()
    app.exec()
    worker.wait()
    return out, logs


class _RecordingStorage:
    """Stockage en mémoire : compte les réécritures de l'instantané d'annulation."""

    def __init__(self, fail: bool = False) -> None:
        self.saves: list[tuple[str, int]] = []
        self.fail = fail
        self.keys: set[str] = set()

    def save(self, collection: str, key: str, data: dict) -> None:
        if self.fail:
            raise OSError("disque plein")
        self.keys.add(key)
        self.saves.append((key, len(data.get("files", []))))

    def list_keys(self, collection: str) -> list[str]:
        return sorted(self.keys)

    def delete(self, collection: str, key: str) -> None:
        self.keys.discard(key)

    def load(self, collection: str, key: str) -> dict | None:
        return None


def test_metadata_is_read_by_batches_but_written_one_by_one(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 60)
    calls, writes = [], []
    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read(calls))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write(writes))

    out, _logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))

    assert "error" not in out
    assert [len(call) for call in calls] == [tk.READ_BATCH, 10]
    assert len(writes) == 60
    assert {state for state, _msg in out["result"]["states"].values()} == {eng.STATE_DONE}
    assert out["result"]["key"]


def test_file_modified_between_read_and_write_is_reread(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 3)
    calls, writes = [], []

    def mutate(path: Path) -> None:
        if path.name == "f000.jpg":
            (tmp_path / "f002.jpg").write_bytes(b"changed between read and write")

    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read(calls))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write(writes, mutate))

    out, logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))

    assert [len(call) for call in calls] == [3, 1]
    assert calls[1][0].name == "f002.jpg"
    assert any("relu" in text for _level, text in logs)
    assert {state for state, _msg in out["result"]["states"].values()} == {eng.STATE_DONE}


def test_missing_free_space_refuses_the_write(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 1)
    writes = []
    mtime = (tmp_path / "f000.jpg").stat().st_mtime
    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read([]))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write(writes))
    monkeypatch.setattr(eng.shutil, "disk_usage",
                        lambda _path: types.SimpleNamespace(total=1, used=1, free=0))

    out, _logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))

    state, message = out["result"]["states"][0]
    assert state == eng.STATE_ERROR and "Espace disque" in message
    assert writes == []
    assert (tmp_path / "f000.jpg").stat().st_mtime == mtime


@pytest.mark.skipif(sys.platform == "win32", reason="chmod de dossier non pertinent sous Windows")
def test_read_only_folder_is_refused(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 1)
    writes = []
    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read([]))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write(writes))
    os.chmod(tmp_path, 0o555)
    try:
        if os.access(tmp_path, os.W_OK):
            pytest.skip("droits non applicables sur cette machine")
        out, _logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))
    finally:
        os.chmod(tmp_path, 0o755)

    state, message = out["result"]["states"][0]
    assert state == eng.STATE_ERROR and "lecture seule" in message.lower()
    assert writes == []


def test_folder_already_locked_by_another_instance_is_refused(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 1)
    calls, writes = [], []
    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read(calls))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write(writes))
    marker = tmp_path / ".lock-ready"
    child = (
        "import time\n"
        "from pathlib import Path\n"
        "from azimut.core.fsutil import folder_lock\n"
        f"root = Path({str(tmp_path)!r})\n"
        "with folder_lock(root):\n"
        f"    Path({str(marker)!r}).write_text('1')\n"
        "    time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", child], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.05)
        assert marker.exists(), (proc.poll() is not None and proc.stderr.read()) or "verrou non pris"

        out, _logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))

        assert "error" in out and "déjà en cours" in out["error"]
        assert writes == [] and calls == []
    finally:
        proc.kill()
        proc.wait(timeout=10)
        marker.unlink(missing_ok=True)


def test_undo_snapshot_is_written_during_the_batch(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 30)
    store = _RecordingStorage()
    monkeypatch.setattr(tk, "get_storage", lambda: store)
    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read([]))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write([]))

    out, _logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))

    sizes = [count for _key, count in store.saves]
    assert sizes[0] == tk.UNDO_CHECKPOINT_FILES
    assert sizes[-1] == 30
    assert len({key for key, _count in store.saves}) == 1          # une seule annulation, mise à jour
    assert out["result"]["key"] in store.keys


def test_failed_undo_snapshot_stops_before_more_writes(tmp_path, qapp, monkeypatch):
    items = make_items(tmp_path, 40)
    writes = []
    monkeypatch.setattr(tk, "get_storage", lambda: _RecordingStorage(fail=True))
    monkeypatch.setattr(tk.ApplyWorker, "read_records", fake_read([]))
    monkeypatch.setattr(tk.ApplyWorker, "write_tags", fake_write(writes))

    out, logs = run_worker(tk.ApplyWorker(Path("exiftool"), items, eng.Options(), roots=[tmp_path]))

    assert len(writes) == tk.UNDO_CHECKPOINT_FILES
    assert len(out["result"]["states"]) == tk.UNDO_CHECKPOINT_FILES
    assert out["result"]["key"] is None                            # aucune annulation utilisable
    assert any(level == "ERR" for level, _text in logs)
