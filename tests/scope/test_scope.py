"""Azimut Scope : comparaison d'arborescences en lecture seule."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from azimut.modules.scope import engine as eng


def tree(root: Path, spec: dict[str, int]) -> Path:
    """spec : dossier relatif → nombre de fichiers .jpg à créer."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, n in spec.items():
        d = root / rel if rel != "." else root
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (d / f"f{i}.jpg").write_bytes(b"x")
    return root


def test_valid_files_rules():
    assert eng.is_valid_file("a.jpg") and eng.is_valid_file("movie.MP4") and eng.is_valid_file("noext")
    for bad in (".hidden.jpg", "notes.txt", "data.JSON", "Thumbs.db", "desktop.ini", ".DS_Store", "sub.srt", "x.py"):
        assert not eng.is_valid_file(bad)


def test_clean_path_variants():
    assert eng.clean_path("  {/a/b c}  ") == "/a/b c"
    assert eng.clean_path('"/a/b"') == "/a/b"
    assert eng.clean_path("file:///home/u/Mes%20Photos") == "/home/u/Mes Photos"
    assert eng.clean_path("/a/b%20c") == "/a/b c"


def test_scan_counts_and_ignores(tmp_path):
    root = tree(tmp_path / "m", {".": 1, "a": 2, "a/deep": 3})
    (root / "a" / "notes.txt").write_text("x")
    (root / ".cache").mkdir()
    (root / ".cache" / "z.jpg").write_bytes(b"x")                   # dossier caché : ignoré
    s = eng.scan_structure(root)
    assert s.counts == {".": 1, "a": 2, "a/deep": 3} and s.folders == 2 and s.files == 6
    with pytest.raises(ValueError, match="introuvable"):
        eng.scan_structure(tmp_path / "nope")
    f = tmp_path / "file.jpg"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="pas un dossier"):
        eng.scan_structure(f)


def test_compare_states_and_deltas(tmp_path):
    a = eng.scan_structure(tree(tmp_path / "m", {".": 1, "same": 2, "diff": 3, "only_a": 1}))
    b = eng.scan_structure(tree(tmp_path / "f", {".": 1, "same": 2, "diff": 1, "only_b": 4}))
    cmp = eng.compare(a, b)
    states = {r.folder: r.state for r in cmp.rows}
    assert states == {".": eng.STATE_EQUAL, "same": eng.STATE_EQUAL, "diff": eng.STATE_DIFF,
                      "only_a": eng.STATE_ONLY_A, "only_b": eng.STATE_ONLY_B}
    rows = {r.folder: r for r in cmp.rows}
    assert rows["diff"].delta == -2 and rows["only_b"].delta == 4 and rows["only_a"].delta == -1
    assert not cmp.identical and len(cmp.differences()) == 3


def test_identical_trees_and_report(tmp_path):
    spec = {".": 1, "x": 2, "x/y": 1}
    cmp = eng.compare(eng.scan_structure(tree(tmp_path / "m", spec)), eng.scan_structure(tree(tmp_path / "f", spec)))
    assert cmp.identical and cmp.differences() == []
    assert "ÉCART  →  +0 sous-dossiers  |  +0 fichiers" in eng.report_text(cmp)
    assert "Aucune différence" in eng.report_text(cmp, only_differences=True)
    text = eng.report_text(cmp)
    assert "└── x (2 fich.)" in text and "  └── y (1 fich.)" in text


def test_report_flags_absent_folders(tmp_path):
    a = eng.scan_structure(tree(tmp_path / "m", {"only_a": 1}))
    b = eng.scan_structure(tree(tmp_path / "f", {"only_b": 1}))
    text = eng.report_text(eng.compare(a, b), only_differences=True)
    assert "⚠  ABSENT  └── only_a" in text and "⚠  ABSENT  └── only_b" in text


def test_scan_never_modifies_anything(tmp_path):
    root = tree(tmp_path / "m", {"a": 2})
    before = sorted((p.name, p.stat().st_mtime_ns) for p in root.rglob("*"))
    eng.scan_structure(root)
    assert before == sorted((p.name, p.stat().st_mtime_ns) for p in root.rglob("*"))


def run_worker(worker):
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    out = {}
    worker.completed.connect(lambda r: (out.setdefault("r", r), app.quit()))
    worker.failed.connect(lambda m: (out.setdefault("err", m), app.quit()))
    worker.start()
    app.exec()
    worker.wait()
    return out


def test_worker_and_window(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    from azimut.modules.scope import MODULE
    from azimut.modules.scope.window import ScopeWindow
    from azimut.modules.scope.workers import CompareWorker
    m = tree(tmp_path / "m", {"a": 2, "b": 1})
    f = tree(tmp_path / "f", {"a": 2, "b": 3})
    out = run_worker(CompareWorker(m, f))
    assert isinstance(out["r"], eng.Comparison) and len(out["r"].differences()) == 1
    assert "err" in run_worker(CompareWorker(tmp_path / "nope", f))
    w = ScopeWindow(MODULE)
    w._cmp = out["r"]
    w._render()
    assert w.table.data_model.rowCount() == 3                         # ., a, b
    w.chk_diff.setChecked(True)
    assert w.table.data_model.rowCount() == 1 and w.prefs.get("only_differences") is True
    assert "+2 fichiers" in w.lbl_gap.text()
    w._copy()
    assert "STRUCTURE MÈRE" in QApplication.clipboard().text()
    target = tmp_path / "rapport.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    w._export()
    assert "RÉSUMÉ GLOBAL" in target.read_text(encoding="utf-8")
    w.open_paths([f"file://{m}", str(f)])
    assert w.field_a.path() == str(m) and w.field_b.path() == str(f)
    w._swap()
    assert w.field_a.path() == str(f)
    w.close()


def test_module_registered():
    from azimut.launcher.registry import discover_with_errors
    specs, errors = discover_with_errors()
    assert "scope" in [s.id for s in specs] and not errors
