"""LOG-011 : le parcours signale les liens symboliques qu'il écarte (le journal annonçait toujours « 0 lien »)."""
from __future__ import annotations

import os
import sys

import pytest

from azimut.core.fsutil import iter_files

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "symlink"),
                                reason="liens symboliques POSIX")


def _tree(tmp_path):
    (tmp_path / "real.jpg").write_bytes(b"x")
    os.symlink(tmp_path / "real.jpg", tmp_path / "link.jpg")
    os.symlink(tmp_path / "real.jpg", tmp_path / "link.txt")
    (tmp_path / "sub").mkdir()
    os.symlink(tmp_path / "sub", tmp_path / "dirlink")
    return tmp_path


def test_links_are_reported_and_never_yielded(tmp_path):
    root = _tree(tmp_path)
    seen: list[str] = []
    files = [p.name for p in iter_files(root, on_link=lambda p: seen.append(p.name))]
    assert files == ["real.jpg"]
    assert sorted(seen) == ["dirlink", "link.jpg", "link.txt"]


def test_links_with_unwanted_extension_are_not_reported(tmp_path):
    root = _tree(tmp_path)
    seen: list[str] = []
    list(iter_files(root, extensions={".jpg"}, on_link=lambda p: seen.append(p.name)))
    assert seen == ["link.jpg"]


def test_without_callback_behaviour_is_unchanged(tmp_path):
    root = _tree(tmp_path)
    assert [p.name for p in iter_files(root)] == ["real.jpg"]
