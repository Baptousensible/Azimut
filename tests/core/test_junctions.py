"""Une jonction NTFS ne doit pas être suivie par le parcours récursif (boucle possible)."""
from __future__ import annotations

import sys

from azimut.core import fsutil


class FakeEntry:
    def __init__(self, junction: bool | Exception | None) -> None:
        if junction is not None:
            self._j = junction
            self.is_junction = self._is_junction

    def _is_junction(self):
        if isinstance(self._j, Exception):
            raise self._j
        return self._j


def test_junction_is_detected_only_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert fsutil._is_junction(FakeEntry(True)) is True
    assert fsutil._is_junction(FakeEntry(False)) is False
    monkeypatch.setattr(sys, "platform", "linux")
    assert fsutil._is_junction(FakeEntry(True)) is False


def test_old_python_without_is_junction_and_errors_are_harmless(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert fsutil._is_junction(FakeEntry(None)) is False
    assert fsutil._is_junction(FakeEntry(OSError("accès refusé"))) is False


def test_iter_files_still_finds_nested_files(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.txt").write_text("x", encoding="utf-8")
    (tmp_path / "y.txt").write_text("y", encoding="utf-8")
    assert sorted(p.name for p in fsutil.iter_files(tmp_path)) == ["x.txt", "y.txt"]
