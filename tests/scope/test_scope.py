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
    assert eng.clean_path("/a/b%20c") == "/a/b%20c"          # chemin ordinaire : pas de décodage (AZ-013)
    assert eng.clean_path("file:///a/b%2520c") == "/a/b%20c"


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
    from PySide6.QtWidgets import QApplication
    from azimut.modules.scope import MODULE
    from azimut.modules.scope.window import ScopeWindow
    from azimut.modules.scope.workers import CompareWorker
    from azimut.ui.widgets import PathDropZone
    m = tree(tmp_path / "m", {"a": 2, "b": 1})
    f = tree(tmp_path / "f", {"a": 2, "b": 3})
    out = run_worker(CompareWorker(m, f))
    assert isinstance(out["r"], eng.Comparison) and len(out["r"].differences()) == 1
    assert "err" in run_worker(CompareWorker(tmp_path / "nope", f))
    w = ScopeWindow(MODULE)
    assert isinstance(w.field_a, PathDropZone) and isinstance(w.field_b, PathDropZone)
    assert w.field_a.import_progress.parent() is w.field_a
    assert not w.lbl_summary.text() and w.lbl_summary.isHidden()
    assert not w.search.isEnabled()
    w._cmp = out["r"]
    w._render()
    assert w.lbl_summary.text().startswith("MÈRE :")
    assert w.search.isEnabled() and w.search.placeholderText() == "Rechercher un dossier…"
    assert w.table.data_model.rowCount() == 3                         # ., a, b
    w.chk_diff.setChecked(True)
    assert w.table.data_model.rowCount() == 1 and w.prefs.get("only_differences") is True
    assert "+2 fichiers" in w.lbl_gap.text() and w.lbl_gap.property("level") == "warn"
    assert w.tree.folders() == [".", "b"]                  # filtre : le dossier différent et son ancêtre
    w.search.setText("b")
    assert w.tree.folders() == [".", "b"] and w.lbl_search.text() == "1 dossier"
    assert not w.lbl_search.isHidden()
    w.search.setText("a")
    assert w.tree.row_count() == 0 and w.tree.search_no_match()
    assert w.lbl_search.text() == "Aucun résultat"
    w.search.clear()
    assert w.lbl_search.isHidden() and w.tree.folders() == [".", "b"]
    w._toggle_view()
    assert w.search.isHidden()
    w._toggle_view()
    assert not w.search.isHidden() and w.btn_view.text() == "Vue liste"
    w._copy()
    assert "STRUCTURE MÈRE" in QApplication.clipboard().text()
    target = tmp_path / "rapport.txt"
    monkeypatch.setattr("azimut.modules.scope.window.choose_save_file", lambda *a, **k: str(target))
    w._export()
    assert "RÉSUMÉ GLOBAL" in target.read_text(encoding="utf-8")
    w.open_paths([f"file://{m}", str(f)])
    assert w.field_a.path() == str(m) and w.field_b.path() == str(f)
    assert w.btn_swap.text() == "" and not w.btn_swap.icon().isNull()
    assert w.btn_swap.accessibleName()
    w.btn_swap.click()
    assert w.field_a.path() == str(f)
    w.close()


def test_run_button_waits_for_two_distinct_folders(tmp_path, qapp):
    """« Démarrer l'analyse » reste inactif sans deux dossiers distincts."""
    from azimut.modules.scope import MODULE
    from azimut.modules.scope.window import ScopeWindow

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    w = ScopeWindow(MODULE)
    try:
        assert not w.run_button.isEnabled()
        w.field_a.set_path(str(a))
        assert not w.run_button.isEnabled()
        w.field_b.set_path(str(b))
        assert w.run_button.isEnabled()
        w.field_b.set_path(str(a))
        assert not w.run_button.isEnabled()
    finally:
        w.close()


def test_scope_tab_scrolls_and_reveals_the_filter_after_analysis(qapp):
    """L'onglet entier défile et, avec assez de résultats, le filtre passe en tête."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea
    from azimut.modules.scope import MODULE
    from azimut.modules.scope.window import ScopeWindow

    counts_a = {".": 1, **{f"d{i}": 1 for i in range(20)}}
    counts_b = {".": 1, **{f"d{i}": 2 for i in range(20)}}
    cmp = eng.compare(eng.Structure(Path("/mere"), counts=counts_a),
                      eng.Structure(Path("/fille"), counts=counts_b))
    w = ScopeWindow(MODULE)
    try:
        w.resize(1100, 760)
        w.show()
        qapp.processEvents()
        assert isinstance(w._content_widget, QScrollArea) and w._content_widget is w.scroll
        w.chk_diff.setChecked(False)
        w._on_done(cmp)
        QTest.qWait(50)
        assert w.views.minimumHeight() > 300       # la zone Dossier s'étire avec le contenu
        assert w.scroll.verticalScrollBar().maximum() > 0
        assert w.scroll.verticalScrollBar().value() > 0
        filter_y = w.chk_diff.mapTo(w.scroll.viewport(), QPoint(0, 0)).y()
        assert 0 <= filter_y <= 30                 # le filtre est calé en haut du viewport
    finally:
        w.close()


def test_window_animates_both_folder_zones(tmp_path, qapp, monkeypatch):
    from azimut.modules.scope import MODULE
    from azimut.modules.scope.window import ScopeWindow
    a = tree(tmp_path / "a", {".": 1})
    b = tree(tmp_path / "b", {".": 1})
    w = ScopeWindow(MODULE)
    w.field_a.set_path(str(a))
    w.field_b.set_path(str(b))
    seen: dict = {}
    monkeypatch.setattr(w, "run_worker", lambda worker, **kwargs: seen.update(kwargs))
    w.on_run()
    assert seen["import_progress"] == [w.field_a.import_progress, w.field_b.import_progress]
    w.close()


def test_module_registered():
    from azimut.launcher.registry import discover_with_errors
    specs, errors = discover_with_errors()
    assert "scope" in [s.id for s in specs] and not errors


def test_tree_rows_follow_terminal_layout():
    a = eng.Structure(Path("/a"), counts={".": 1, "x": 2, "x/y": 1, "z": 0})
    b = eng.Structure(Path("/b"), counts={".": 1, "x": 2, "x/y": 3, "w": 1})
    cmp = eng.compare(a, b)
    rows = eng.tree_rows(cmp)
    assert [r.folder for r in rows] == [".", "w", "x", "x/y", "z"]      # profondeur d'abord, trié
    by = {r.folder: r for r in rows}
    assert rows[0].prefix == "" and rows[0].depth == 0
    assert by["x/y"].depth == 2 and by["x/y"].name == "y"
    assert by["w"].prefix == "├─ " and by["z"].prefix == "└─ "           # dernier frère sans barre
    assert by["x/y"].prefix.startswith("│  ")                      # x n'est pas le dernier de la racine
    only = eng.tree_rows(cmp, only_differences=True)
    assert {r.folder for r in only} == {".", "w", "x", "z", "x/y"}      # x gardé comme ancêtre de x/y
    same = eng.compare(a, a)
    assert eng.tree_rows(same, only_differences=True) == []


def test_comparison_tree_states_colors_and_navigation(qapp):
    from azimut.modules.scope.treeview import ComparisonTree
    a = eng.Structure(Path("/mere"), counts={".": 1, "same": 4, "diff": 3, "only_a": 2})
    b = eng.Structure(Path("/fille"), counts={".": 1, "same": 4, "diff": 1, "only_b": 5})
    cmp = eng.compare(a, b)
    w = ComparisonTree()
    w.set_comparison(cmp)
    assert w.row_count() == 5
    assert w.folders() == [".", "diff", "only_a", "only_b", "same"]   # profondeur d'abord, trié
    assert w.counts() == {eng.STATE_EQUAL: 2, eng.STATE_DIFF: 1,
                          eng.STATE_ONLY_A: 1, eng.STATE_ONLY_B: 1}
    assert w.chip_equal.text() == "2 identiques"
    assert w.chip_diff.text() == "1 différent"
    assert w.chip_absent.text() == "2 absents"
    w.resize(900, 420)
    assert not w.grab().toImage().isNull()
    w.select_next_difference()
    assert w.view.currentIndex().data() == "diff"
    w.select_next_difference()
    assert w.view.currentIndex().data() == "only_a"
    w.select_previous_difference()
    assert w.view.currentIndex().data() == "diff"

    w.set_comparison(cmp, only_differences=True)
    assert w.folders() == [".", "diff", "only_a", "only_b"]            # ancêtre conservé pour le contexte
    w.set_comparison(eng.compare(a, a), only_differences=True)
    assert w.row_count() == 0 and w.stack.currentWidget() is w.empty
    w.set_comparison(None)
    assert w.row_count() == 0 and not w.stats.isVisible()

    accented = eng.Structure(Path("/a"), counts={".": 0, "Hiver": 1, "Été": 2,
                                                  "Jour 10": 1, "Jour 2": 1})
    ordered = ComparisonTree()
    ordered.set_comparison(eng.compare(accented, accented))
    assert ordered.folders() == [".", "Été", "Hiver", "Jour 2", "Jour 10"]   # tri naturel


def test_search_filters_by_name_and_keeps_context(qapp):
    from azimut.modules.scope.treeview import ComparisonTree
    a = eng.Structure(Path("/mere"), counts={
        ".": 0, "Divers": 1, "Voyage Japon": 0, "Voyage Japon/Jour 2": 1,
        "Voyage Japon/Jour 10": 1, "2023": 0, "2023/Été": 4, "2023/Hiver": 2})
    cmp = eng.compare(a, a)
    w = ComparisonTree()
    w.set_comparison(cmp)
    assert w.row_count() == 8

    w.set_search("ete")                                      # casse et accents ignorés
    assert w.folders() == [".", "2023", "2023/Été"]
    assert w.search_result_count() == 1

    w.set_search("JOUR")
    assert w.folders() == [".", "Voyage Japon", "Voyage Japon/Jour 2", "Voyage Japon/Jour 10"]
    assert w.search_result_count() == 2

    w.set_search("voya")                                     # le sous-arbre du dossier trouvé reste visible
    assert w.search_result_count() == 1
    assert w.folders() == [".", "Voyage Japon", "Voyage Japon/Jour 2", "Voyage Japon/Jour 10"]

    w.set_search("o")
    assert w.search_result_count() == 3                     # le dossier et ses deux enfants

    w.set_search("zzz")
    assert w.row_count() == 0 and w.search_no_match()
    assert w.stack.currentWidget() is w.empty
    assert "zzz" in w.empty.detail.text()

    w.set_search("")
    assert w.stack.currentWidget() is w.view and w.row_count() == 8

    a2 = eng.Structure(Path("/m"), counts={".": 1, "same": 4, "diff": 3})
    b2 = eng.Structure(Path("/f"), counts={".": 1, "same": 4, "diff": 1})
    w.set_comparison(eng.compare(a2, b2), only_differences=True)
    w.set_search("same")                                     # exclu par « seulement les différences »
    assert w.row_count() == 0 and w.search_no_match()
    assert "résultats affichés" in w.empty.detail.text()
