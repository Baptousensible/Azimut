from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import csv
import shutil
import sys
import types
from pathlib import Path

import pytest

from azimut.modules.dedup import actions
from azimut.modules.dedup.actions import (
    MODE_DELETE,
    MODE_QUARANTINE,
    MODE_TRASH,
    ApplyOptions,
    apply_groups,
    export_report,
    plan_deletions,
    undo_journal,
)
from azimut.modules.dedup.engine import Hooks, ScanOptions, scan
from azimut.modules.dedup.model import ACTION_DELETE, ACTION_KEEP, Group


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_scene(tmp_path: Path, protect_a: bool = False):
    """A contient 2 fichiers, B leurs copies (+ un fichier unique) ; B est proposé à la suppression."""
    a, b = tmp_path / "a", tmp_path / "b"
    write(a / "one.bin", b"1" * 500)
    write(a / "sub" / "two.bin", b"2" * 800)
    write(b / "copie_one.bin", b"1" * 500)
    write(b / "deep" / "dir" / "copie_two.bin", b"2" * 800)
    write(b / "unique.bin", b"U" * 300)
    res = scan(ScanOptions(folder_a=a, folder_b=b, protect_a=protect_a))
    assert len(res.groups) == 2
    return a, b, res.groups


def journal_of(tmp_path: Path) -> Path:
    found = list(tmp_path.glob("Quarantaine_*/journal.csv"))
    assert len(found) == 1
    return found[0]


def read_journal(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ── Quarantaine ───────────────────────────────────────────────────────

def test_quarantine_moves_right_files_and_writes_journal_before_move(tmp_path, monkeypatch):
    a, b, groups = make_scene(tmp_path)
    real_move = shutil.move
    checks: list[bool] = []

    def spy(src, dst, *args, **kwargs):
        rows = read_journal(journal_of(tmp_path))
        checks.append(str(src) in {r["chemin_original"] for r in rows})   # déjà journalisé ?
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(actions.shutil, "move", spy)
    report = apply_groups(groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    assert report.moved == 2 and report.errors == 0 and not report.skipped
    assert checks == [True, True]
    assert (a / "one.bin").exists() and (a / "sub" / "two.bin").exists()
    assert (b / "unique.bin").exists()
    assert not (b / "copie_one.bin").exists()
    qdir = report.quarantine_dir
    assert qdir is not None and qdir.name.startswith("Quarantaine_")
    assert (qdir / "B" / "copie_one.bin").read_bytes() == b"1" * 500
    assert (qdir / "B" / "deep" / "dir" / "copie_two.bin").exists()
    rows = read_journal(report.journal)
    assert list(rows[0].keys()) == ["chemin_original", "chemin_quarantaine", "cote", "taille"]
    assert {(r["cote"], r["taille"]) for r in rows} == {("B", "500"), ("B", "800")}
    assert report.freed == 1300


def test_undo_restores_and_never_overwrites(tmp_path):
    a, b, groups = make_scene(tmp_path)
    report = apply_groups(groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    # un fichier a été recréé entre-temps à l'emplacement d'origine
    write(b / "copie_one.bin", b"NOUVEAU")
    logs: list[tuple[str, str]] = []
    undo = undo_journal(report.journal, Hooks(log=lambda lv, tx: logs.append((lv, tx))))
    assert undo.restored == 1 and undo.errors == 1
    assert (b / "copie_one.bin").read_bytes() == b"NOUVEAU"           # jamais écrasé
    assert (report.quarantine_dir / "B" / "copie_one.bin").exists()    # reste en quarantaine
    assert (b / "deep" / "dir" / "copie_two.bin").read_bytes() == b"2" * 800
    assert any(lv == "ERR" for lv, _ in logs)
    # second passage : tout ce qui était restauré est « déjà en place »
    again = undo_journal(report.journal)
    assert again.restored == 0


def test_undo_rejects_invalid_journal(tmp_path):
    bad = tmp_path / "journal.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        undo_journal(bad)


def test_undo_finds_moved_quarantine_folder(tmp_path):
    a, b, groups = make_scene(tmp_path)
    report = apply_groups(groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    moved = tmp_path / "ailleurs" / report.quarantine_dir.name
    moved.parent.mkdir()
    shutil.move(str(report.quarantine_dir), str(moved))
    undo = undo_journal(moved / "journal.csv")
    assert undo.restored == 2 and undo.errors == 0
    assert (b / "copie_one.bin").exists()


# ── Sûreté ────────────────────────────────────────────────────────────

def test_file_modified_since_scan_is_ignored(tmp_path):
    a, b, groups = make_scene(tmp_path)
    (b / "copie_one.bin").write_bytes(b"1" * 501)                           # taille changée
    two = b / "deep" / "dir" / "copie_two.bin"
    st = two.stat()
    os.utime(two, (st.st_atime, st.st_mtime + 30))                          # date changée de 30 s
    report = apply_groups(groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    assert report.moved == 0 and len(report.skipped) == 2
    assert (b / "copie_one.bin").exists() and two.exists()


def test_small_mtime_drift_is_tolerated(tmp_path):
    a, b, groups = make_scene(tmp_path)
    f = b / "copie_one.bin"
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 1))
    report = apply_groups(groups, ApplyOptions(MODE_DELETE))
    assert report.moved == 2 and not f.exists()


def test_missing_or_changed_keeper_blocks_deletion(tmp_path):
    a, b, groups = make_scene(tmp_path)
    (a / "one.bin").unlink()                                                # exemplaire gardé disparu
    (a / "sub" / "two.bin").write_bytes(b"2" * 10)                          # exemplaire gardé modifié
    report = apply_groups(groups, ApplyOptions(MODE_DELETE))
    assert report.moved == 0 and len(report.skipped) == 2
    assert (b / "copie_one.bin").exists() and (b / "deep" / "dir" / "copie_two.bin").exists()


def test_protected_and_keeperless_groups_never_touched(tmp_path):
    a, b, groups = make_scene(tmp_path, protect_a=True)
    protected = [i for g in groups for i in g.items if i.protected]
    for it in protected:
        it.action = ACTION_DELETE                                           # état incohérent forcé
    report = apply_groups(groups, ApplyOptions(MODE_DELETE))
    assert (a / "one.bin").exists() and (a / "sub" / "two.bin").exists()
    assert all(i not in report.done for i in protected)
    # groupe sans aucun exemplaire gardé : rien n'est supprimé
    for g in groups:
        for it in g.items:
            it.action = ACTION_DELETE
            it.protected = False
    report = apply_groups(groups, ApplyOptions(MODE_DELETE))
    assert report.moved == 0
    assert all(i.path.exists() for g in groups for i in g.items)


def test_plan_excludes_protected_and_keeperless(tmp_path):
    _, _, groups = make_scene(tmp_path, protect_a=True)
    plan = plan_deletions(groups)
    assert len(plan) == 2 and all(not it.protected for _, it in plan)
    for it in groups[0].items:
        it.action = ACTION_DELETE
    assert all(g is not groups[0] for g, _ in plan_deletions(groups))


def test_cancel_stops_without_touching_remaining_files(tmp_path):
    a = tmp_path / "a"
    for i in range(5):
        write(a / f"k{i}.bin", bytes([i]) * 100)
        write(a / "dup" / f"c{i}.bin", bytes([i]) * 100)
    groups = scan(ScanOptions(folder_a=a)).groups
    assert len(groups) == 5
    stop = {"v": False}

    def progress(done, total, label):
        stop["v"] = True

    report = apply_groups(groups, ApplyOptions(MODE_DELETE), Hooks(cancel=lambda: stop["v"], progress=progress))
    assert report.cancelled and report.moved == 1
    assert sum(1 for g in groups for i in g.items if i.path.exists()) == 9


def test_remove_empty_dirs_option(tmp_path):
    a, b, groups = make_scene(tmp_path)
    apply_groups(groups, ApplyOptions(MODE_DELETE, remove_empty_dirs=True))
    assert not (b / "deep").exists() and b.exists()
    a, b, groups = make_scene(tmp_path / "x")
    apply_groups(groups, ApplyOptions(MODE_DELETE, remove_empty_dirs=False))
    assert (b / "deep" / "dir").exists()


# ── Corbeille et suppression définitive ───────────────────────────────

def test_trash_mode_uses_send2trash_and_is_hidden_when_absent(tmp_path, monkeypatch):
    a, b, groups = make_scene(tmp_path)
    sent: list[str] = []
    fake = types.ModuleType("send2trash")
    fake.send2trash = lambda p: (sent.append(p), os.remove(p))
    monkeypatch.setitem(sys.modules, "send2trash", fake)
    assert actions.trash_available()
    report = apply_groups(groups, ApplyOptions(MODE_TRASH))
    assert report.moved == 2 and len(sent) == 2
    monkeypatch.setitem(sys.modules, "send2trash", None)
    assert not actions.trash_available()
    with pytest.raises(RuntimeError):
        apply_groups(groups, ApplyOptions(MODE_TRASH))


def test_quarantine_requires_parent(tmp_path):
    _, _, groups = make_scene(tmp_path)
    with pytest.raises(ValueError):
        apply_groups(groups, ApplyOptions(MODE_QUARANTINE))


# ── Rapport ───────────────────────────────────────────────────────────

def test_export_report(tmp_path):
    a, b, groups = make_scene(tmp_path)
    out = tmp_path / "rapport.csv"
    assert export_report(groups, out) == 4
    with open(out, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == ["groupe", "type", "cote", "chemin", "taille", "date",
                                    "dimensions", "empreinte", "action"]
    assert {r["action"] for r in rows} == {ACTION_KEEP, ACTION_DELETE}
    assert all(r["type"] == "identique" and r["empreinte"] for r in rows)


# ── Workers ───────────────────────────────────────────────────────────

def test_workers_run_logic(tmp_path):
    pytest.importorskip("PySide6")
    from azimut.core.workers import CancelledError
    from azimut.modules.dedup.workers import ApplyWorker, ScanWorker, UndoWorker

    a, b = tmp_path / "a", tmp_path / "b"
    write(a / "x.bin", b"X" * 400)
    write(b / "y.bin", b"X" * 400)
    sw = ScanWorker(ScanOptions(folder_a=a, folder_b=b))
    result = sw.do_work()
    assert len(result.groups) == 1 and sw.ok_count == 1 and "groupe" in sw.summary

    aw = ApplyWorker(result.groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    report = aw.do_work()
    assert report.moved == 1 and aw.ok_count == 1 and not (b / "y.bin").exists()

    uw = UndoWorker(report.journal)
    undo = uw.do_work()
    assert undo.restored == 1 and (b / "y.bin").exists() and uw.ok_count == 1

    cancelled = ScanWorker(ScanOptions(folder_a=a, folder_b=b))
    cancelled.cancel()
    with pytest.raises(CancelledError):
        cancelled.do_work()
    assert (a / "x.bin").exists() and (b / "y.bin").exists()
