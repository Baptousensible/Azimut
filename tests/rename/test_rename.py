"""Azimut Rename : préfixe, noms, collisions, renommages sûrs, annulation, lecture des dates, fenêtre."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from azimut.modules.rename import engine as eng
from azimut.modules.rename.engine import Options, RenameRow

D = datetime(2008, 6, 15, 14, 30, 0)


def row(path: Path, date: datetime | None = D, prefix: str | None = None, checked: bool = True, mtime=None):
    r = eng.make_row(path, date)
    if prefix is not None:
        r.prefix = prefix
    r.checked = checked and date is not None
    if mtime is not None:
        r.date_mtime = mtime
    return r


def names_of(rows):
    return [r.new_name for r in rows]


# ── Dates ─────────────────────────────────────────────────────────────────────
def test_date_from_record_priority_and_fake_dates():
    assert eng.date_from_record({"DateTimeOriginal": "2008:06:15 14:30:00"}) == D
    assert eng.date_from_record({"CreateDate": "2008:06:15 14:30:00+02:00"}) == D
    assert eng.date_from_record({"DateTimeOriginal": "0000:00:00 00:00:00", "CreateDate": "2010:01:02 03:04:05"}) \
        == datetime(2010, 1, 2, 3, 4, 5)
    assert eng.date_from_record({"CreateDate": "1970:01:01 00:00:00"}) is None
    assert eng.date_from_record({"MediaCreateDate": "1904:01:01 00:00:00"}) is None
    assert eng.date_from_record({"CreateDate": "2008:13:45 14:30:00"}) is None
    assert eng.date_from_record({}) is None
    assert eng.parse_json_records("pas du json") is None and eng.parse_json_records("{}") is None


# ── Préfixe ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stem,expected", [
    ("IMG_1234", ""), ("DSC0001", ""), ("PXL_20230101_123456789", ""), ("IMG-20230101-WA0012", ""),
    ("Vacances Corse 2008", "Vacances Corse"),
    ("IMG_Vacances Corse_2008-06-15_14-30-00", "Vacances Corse"),
    ("IMG_Vacances Corse_2008-06-15_14-30-00_2", "Vacances Corse"),
    ("photo_jpg", "photo"), ("jpg", ""), ("Mariage Léa_mp4", "Mariage Léa"),
    ("Screenshot 2023-01-01 at 10.00.00", ""),
    ("2008-06-15_14-30-00", ""), ("Anniversaire (1)", "Anniversaire"),
    ("20080615_143000", ""), ("12345", ""),
])
def test_extract_prefix(stem, expected):
    assert eng.extract_prefix(stem) == expected


def test_sanitize_prefix():
    assert eng.sanitize_prefix('  a<b>:c/d  ') == "a_b__c_d"
    assert eng.sanitize_prefix("***") == "___"[:0] or eng.sanitize_prefix("***") == ""
    assert eng.sanitize_prefix("   ") == "" and eng.sanitize_prefix("---") == ""
    assert len(eng.sanitize_prefix("x" * 300)) == 100


def test_type_acronym_and_base():
    assert [eng.type_acronym(e) for e in (".JPG", ".mov", ".flac", ".xyz")] == ["IMG", "VID", "AUD", "IMG"]
    assert eng.build_base("Corse", D, "%Y-%m-%d_%H-%M-%S", "IMG") == "IMG_Corse_2008-06-15_14-30-00"
    assert eng.build_base("", D, "%Y%m%d_%H%M%S") == "20080615_143000"


# ── Calcul des noms ───────────────────────────────────────────────────────────
def test_compute_names_basic_prefix_and_types(tmp_path):
    a = row(tmp_path / "Vacances Corse 12345.jpg", prefix=None)
    b = row(tmp_path / "IMG_0001.MOV", date=datetime(2009, 1, 2, 3, 4, 5))
    c = row(tmp_path / "song.mp3", date=datetime(2010, 1, 1))
    rows = [a, b, c]
    entries = eng.folder_entries(rows)
    eng.compute_names(rows, entries, Options())
    assert names_of(rows) == ["IMG_Vacances Corse_2008-06-15_14-30-00.jpg", "VID_2009-01-02_03-04-05.MOV",
                              "AUD_song_2010-01-01_00-00-00.mp3"]
    assert all(r.status == eng.STATUS_RENAME and r.source == eng.SOURCE_META for r in rows)
    eng.compute_names(rows, entries, Options(add_type=False, keep_prefix=False, fmt="day"))
    assert names_of(rows) == ["2008-06-15.jpg", "2009-01-02.MOV", "2010-01-01.mp3"]
    eng.compute_names(rows, entries, Options(fmt="compact"))
    assert rows[1].new_name == "VID_20090102_030405.MOV"


def test_compute_names_status_rules(tmp_path):
    for n in ("done.jpg", "skipped.jpg", "nodate.jpg"):
        (tmp_path / n).write_bytes(b"x")
    ok = row(tmp_path / "IMG_2008-06-15_14-30-00.jpg")                        # déjà conforme
    counter = row(tmp_path / "IMG_2008-06-15_14-30-00_2.jpg")                 # conforme avec compteur
    skipped = row(tmp_path / "skipped.jpg", checked=False)
    nodate = row(tmp_path / "nodate.jpg", date=None)
    rows = [ok, counter, skipped, nodate]
    eng.compute_names(rows, eng.folder_entries(rows), Options())
    assert [r.status for r in rows] == [eng.STATUS_OK, eng.STATUS_OK, eng.STATUS_SKIPPED, eng.STATUS_NO_DATE]
    assert skipped.new_name == "skipped.jpg" and nodate.new_name == "nodate.jpg"


def test_mtime_fallback_is_flagged(tmp_path):
    mt = datetime(2015, 5, 5, 5, 5, 5)
    r = row(tmp_path / "x.jpg", date=None, mtime=mt)
    r.checked = True
    eng.compute_names([r], {}, Options(use_mtime=False))
    assert r.status == eng.STATUS_NO_DATE
    eng.compute_names([r], {}, Options(use_mtime=True))
    assert r.status == eng.STATUS_RENAME and r.source == eng.SOURCE_MTIME and r.new_name.endswith("2015-05-05_05-05-05.jpg")


def test_collisions_get_counters_and_never_overwrite_unmoved_files(tmp_path):
    # un fichier étranger occupe déjà le nom visé ; deux fichiers ont la même date
    (tmp_path / "IMG_2008-06-15_14-30-00.jpg").write_bytes(b"other")      # non listé dans les lignes
    r1, r2 = row(tmp_path / "a1.jpg", prefix=""), row(tmp_path / "a2.jpg", prefix="")
    rows = [r1, r2]
    eng.compute_names(rows, eng.folder_entries(rows), Options())
    assert sorted(names_of(rows)) == ["IMG_2008-06-15_14-30-00_2.jpg", "IMG_2008-06-15_14-30-00_3.jpg"]


def test_name_freed_by_a_renamed_file_can_be_reused(tmp_path):
    # b.jpg doit devenir le nom actuel de a.jpg, qui lui-même se renomme : pas de compteur inutile
    (tmp_path / "IMG_2008-06-15_14-30-00.jpg").write_bytes(b"1")
    a = row(tmp_path / "IMG_2008-06-15_14-30-00.jpg", date=datetime(2020, 1, 1))     # change de nom
    b = row(tmp_path / "b.jpg", prefix="")
    a.prefix = ""
    rows = [a, b]
    eng.compute_names(rows, eng.folder_entries(rows), Options())
    assert b.new_name == "IMG_2008-06-15_14-30-00.jpg" and a.new_name == "IMG_2020-01-01_00-00-00.jpg"


def test_compute_is_idempotent_after_rename(tmp_path):
    r = row(tmp_path / "Corse 9999.jpg")
    eng.compute_names([r], {}, Options())
    new = r.new_name
    r2 = row(tmp_path / new)
    assert r2.auto_prefix == "Corse"
    eng.compute_names([r2], {}, Options())
    assert r2.status == eng.STATUS_OK


# ── Inventaire ────────────────────────────────────────────────────────────────
def test_list_files_filters_types_and_parasites(tmp_path):
    for n in ("a.jpg", "b.MOV", "c.mp3", "d.txt", "._x.jpg", f"{eng.TEMP_PREFIX}abc.jpg"):
        (tmp_path / n).write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "e.png").write_bytes(b"x")
    inv = eng.list_files(tmp_path, recursive=True)
    assert [p.name for p in inv.files] == ["a.jpg", "b.MOV", "c.mp3", "e.png"]
    assert inv.ignored == {".txt": 1} and inv.parasites == 2
    assert [p.name for p in eng.list_files(tmp_path, recursive=False).files] == ["a.jpg", "b.MOV", "c.mp3"]


# ── Exécution sûre ────────────────────────────────────────────────────────────
def make(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_execute_simple_and_no_overwrite(tmp_path):
    a, b = make(tmp_path / "a.txt", "A"), make(tmp_path / "b.txt", "B")
    make(tmp_path / "taken.txt", "T")
    res = eng.execute_renames([(0, a, tmp_path / "x.txt"), (1, b, tmp_path / "taken.txt")])
    assert res[0] == (True, "") and res[1][0] is False
    assert (tmp_path / "x.txt").read_text() == "A"
    assert b.read_text() == "B" and (tmp_path / "taken.txt").read_text() == "T"         # rien d'écrasé
    missing = eng.execute_renames([(0, tmp_path / "nope.txt", tmp_path / "y.txt")])
    assert missing[0] == (False, "fichier introuvable")


def test_execute_chain_and_swap(tmp_path):
    a, b, c = (make(tmp_path / f"{n}.txt", n) for n in "abc")
    # chaîne : a→b, b→c, c libre ; et permutation : x↔y
    x, y = make(tmp_path / "x.txt", "X"), make(tmp_path / "y.txt", "Y")
    res = eng.execute_renames([(0, a, tmp_path / "b.txt"), (1, b, tmp_path / "c.txt"), (2, c, tmp_path / "d.txt"),
                               (3, x, tmp_path / "y.txt"), (4, y, tmp_path / "x.txt")])
    assert all(ok for ok, _ in res.values()), res
    assert {n: (tmp_path / n).read_text() for n in ("b.txt", "c.txt", "d.txt", "x.txt", "y.txt")} == \
           {"b.txt": "a", "c.txt": "b", "d.txt": "c", "x.txt": "Y", "y.txt": "X"}
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(eng.TEMP_PREFIX)]


def test_execute_deadlock_restores_original_names(tmp_path):
    a = make(tmp_path / "a.txt", "A")
    make(tmp_path / "blocker.txt", "Z")                    # destination occupée par un fichier qui ne bouge pas
    res = eng.execute_renames([(0, a, tmp_path / "blocker.txt")])
    assert res[0][0] is False and a.read_text() == "A"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(eng.TEMP_PREFIX)]


def test_execute_case_only_change(tmp_path):
    a = make(tmp_path / "photo.JPG", "A")
    res = eng.execute_renames([(0, a, tmp_path / "photo.jpg")])
    assert res[0][0] and "photo.jpg" in os.listdir(tmp_path)


def test_execute_cancel_stops_without_losing_files(tmp_path):
    files = [make(tmp_path / f"f{i}.txt", str(i)) for i in range(5)]
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    eng.execute_renames([(i, f, tmp_path / f"g{i}.txt") for i, f in enumerate(files)], cancel=cancel)
    names = sorted(os.listdir(tmp_path))
    assert len(names) == 5 and all(n.startswith(("f", "g")) for n in names)


# ── Annulation ────────────────────────────────────────────────────────────────
def test_undo_plan_and_roundtrip(tmp_path):
    a, b = make(tmp_path / "a.txt", "A"), make(tmp_path / "b.txt", "B")
    pairs = [(a, tmp_path / "x.txt"), (b, tmp_path / "y.txt"), (tmp_path / "ghost.txt", tmp_path / "z.txt")]
    record = eng.undo_record(pairs, "2026-01-01T00:00:00")
    eng.execute_renames([(0, a, tmp_path / "x.txt"), (1, b, tmp_path / "y.txt")])
    plan, skipped = eng.plan_undo(record)
    assert skipped == ["z.txt"]                                   # jamais renommé : ignoré
    res = eng.execute_renames(plan)
    assert all(ok for ok, _ in res.values())
    assert sorted(os.listdir(tmp_path)) == ["a.txt", "b.txt"]
    assert eng.plan_undo({"pairs": [{"old": "", "new": "x"}, "bad", {"old": "o"}]}) == ([], [])


# ── Workers (ExifTool réel) ───────────────────────────────────────────────────
def run_worker(worker):
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    out = {}
    worker.completed.connect(lambda r: (out.setdefault("r", r), app.quit()))
    worker.failed.connect(lambda m: (out.setdefault("err", m), app.quit()))
    worker.cancelled.connect(lambda: (out.setdefault("cancelled", True), app.quit()))
    worker.start()
    app.exec()
    worker.wait()
    assert "err" not in out, out.get("err")
    return out.get("r")


def make_jpeg(path: Path, when: str | None):
    from PIL import Image
    im = Image.new("RGB", (16, 16), (10, 120, 200))
    if when:
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9003] = when                      # DateTimeOriginal
        im.save(path, exif=exif)
    else:
        im.save(path)


EXIFTOOL = shutil.which("exiftool")


@pytest.mark.skipif(not EXIFTOOL, reason="ExifTool requis")
def test_scan_apply_undo_end_to_end(tmp_path, qapp):
    from azimut.core.storage import get_storage
    from azimut.modules.rename.workers import ApplyWorker, ScanResult, ScanWorker, UndoWorker
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    make_jpeg(src / "Corse 1234.jpg", "2008:06:15 14:30:00")
    make_jpeg(src / "sub" / "é accentué.jpg", "2009:07:16 08:00:01")
    make_jpeg(src / "nodate.jpg", None)
    res = run_worker(ScanWorker(src, True, Path(EXIFTOOL)))
    assert isinstance(res, ScanResult) and len(res.rows) == 3 and res.without_meta == 1
    rows = res.rows
    eng.compute_names(rows, res.entries, Options())
    by_old = {r.old_name: r for r in rows}
    assert by_old["Corse 1234.jpg"].new_name == "IMG_Corse_2008-06-15_14-30-00.jpg"
    assert by_old["é accentué.jpg"].new_name == "IMG_é accentué_2009-07-16_08-00-01.jpg"
    assert by_old["nodate.jpg"].status == eng.STATUS_NO_DATE
    todo = [r for r in rows if r.status == eng.STATUS_RENAME]
    pairs = [(i, r.path, r.new_path) for i, r in enumerate(todo)]
    mtimes = {r.old_name: r.path.stat().st_mtime for r in todo}
    out = run_worker(ApplyWorker(pairs))
    assert all(ok for ok, _ in out["results"].values()) and out["key"]
    assert sorted(p.name for p in src.rglob("*.jpg")) == sorted(
        ["IMG_Corse_2008-06-15_14-30-00.jpg", "IMG_é accentué_2009-07-16_08-00-01.jpg", "nodate.jpg"])
    for r in todo:                                           # la date de modification n'a pas bougé
        assert r.new_path.stat().st_mtime == mtimes[r.old_name]
    record = get_storage().load(eng.UNDO_COLLECTION, out["key"])
    assert len(record["pairs"]) == 2
    done, errors = run_worker(UndoWorker(out["key"]))
    assert (done, errors) == (2, 0)
    assert sorted(p.name for p in src.rglob("*.jpg")) == sorted(["Corse 1234.jpg", "é accentué.jpg", "nodate.jpg"])
    assert get_storage().load(eng.UNDO_COLLECTION, out["key"]) is None       # annulé : n'est plus proposé


@pytest.mark.skipif(not EXIFTOOL, reason="ExifTool requis")
def test_scan_reads_video_dates_and_batches(tmp_path, qapp):
    from azimut.modules.rename.workers import ScanWorker
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg requis")
    src = tmp_path / "in"
    src.mkdir()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=5",
                    "-metadata", "creation_time=2012-03-04T05:06:07Z", "-c:v", "libx264", str(src / "v.mp4")],
                   check=True)
    for i in range(eng.READ_CHUNK + 5):                      # plus d'un lot
        make_jpeg(src / f"p{i:03d}.jpg", "2001:02:03 04:05:06")
    res = run_worker(ScanWorker(src, False, Path(EXIFTOOL)))
    assert len(res.rows) == eng.READ_CHUNK + 6
    video = next(r for r in res.rows if r.old_name == "v.mp4")
    assert video.date_meta == datetime(2012, 3, 4, 5, 6, 7)
    assert all(r.date_meta == datetime(2001, 2, 3, 4, 5, 6) for r in res.rows if r.old_name.endswith(".jpg"))


def test_scan_without_exiftool_uses_no_metadata(tmp_path, qapp):
    from azimut.modules.rename.workers import ScanWorker
    (tmp_path / "a.jpg").write_bytes(b"not really a jpeg")
    res = run_worker(ScanWorker(tmp_path, True, None))
    assert len(res.rows) == 1 and res.rows[0].date_meta is None and res.rows[0].date_mtime is not None


# ── Fenêtre ───────────────────────────────────────────────────────────────────
def test_window_filters_checkboxes_prefix_and_export(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from azimut.modules.rename import MODULE
    from azimut.modules.rename.window import RenameWindow
    w = RenameWindow(MODULE)
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"x")
    (tmp_path / "c.jpg").write_bytes(b"x")
    rows = [row(tmp_path / "a.jpg", prefix="Trip"), row(tmp_path / "b.jpg", date=None),
            row(tmp_path / "c.jpg", date=datetime(2011, 1, 1))]
    w._rows, w._entries, w._base_dir = rows, eng.folder_entries(rows), tmp_path
    w._recompute()
    assert w.table.data_model.rowCount() == 2                        # filtre « À renommer »
    w.cmb_filter.setCurrentIndex(1)
    assert w.table.data_model.rowCount() == 3
    w.cmb_filter.setCurrentIndex(0)
    w._set_checked([0], False)                                       # décoche a.jpg
    assert rows[0].status == eng.STATUS_SKIPPED and w.table.data_model.rowCount() == 1
    w.cmb_filter.setCurrentIndex(3)
    w._set_checked([0], None)                                        # le recoche (bascule)
    assert rows[0].status == eng.STATUS_RENAME
    w.cmb_filter.setCurrentIndex(1)
    w.table.selectRow(0)
    w.edit_prefix.setText("Autre")
    w._apply_prefix()
    assert rows[0].new_name == "IMG_Autre_2008-06-15_14-30-00.jpg"
    w.chk_type.setChecked(False)
    assert rows[0].new_name == "Autre_2008-06-15_14-30-00.jpg" and w.prefs.get("add_type") is False
    target = tmp_path / "plan.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    w._export_plan()
    text = target.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("chemin,ancien_nom") and "Autre_2008-06-15_14-30-00.jpg" in text
    w.close()


def test_module_is_registered_and_runnable():
    from azimut.launcher.registry import discover_with_errors
    specs, errors = discover_with_errors()
    assert "rename" in [s.id for s in specs] and not errors
    spec = next(s for s in specs if s.id == "rename")
    assert spec.name == "Azimut Rename" and spec.optional == ("exiftool", "ffmpeg") and spec.requires == ()
