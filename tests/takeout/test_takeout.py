"""Azimut Takeout : appariement média/JSON, plans d'écriture, application réelle avec ExifTool."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from azimut.modules.tags import engine as tags
from azimut.modules.takeout import engine as eng

TAKEN = 1_600_000_000                      # 2020-09-13 12:26:40 UTC


def sidecar_json(taken=TAKEN, lat=48.8584, lon=2.2945, alt=35.0, title="x.jpg") -> dict:
    return {"title": title, "photoTakenTime": {"timestamp": str(taken)},
            "geoData": {"latitude": lat, "longitude": lon, "altitude": alt}}


def touch(folder: Path, *names: str) -> None:
    for n in names:
        (folder / n).write_bytes(b"x")


def write_json(folder: Path, name: str, **kw) -> Path:
    p = folder / name
    p.write_text(json.dumps(sidecar_json(**kw)), encoding="utf-8")
    return p


# ── JSON ──────────────────────────────────────────────────────────────────────
def test_parse_sidecar_values():
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    assert sc.taken == datetime.fromtimestamp(TAKEN, tz=timezone.utc)
    assert (sc.lat, sc.lon, sc.alt) == (48.8584, 2.2945, 35.0) and sc.has_gps


def test_parse_sidecar_ignores_placeholder_gps_and_bad_dates():
    sc = eng.parse_sidecar({"photoTakenTime": {"timestamp": "0"},
                            "geoData": {"latitude": 0.0, "longitude": 0.0}}, Path("a.json"))
    assert sc.taken is None and not sc.has_gps
    sc = eng.parse_sidecar({"creationTime": {"timestamp": str(TAKEN)},
                            "geoData": {"latitude": 0, "longitude": 0},
                            "geoDataExif": {"latitude": 10.5, "longitude": -3.2, "altitude": "x"}}, Path("a.json"))
    assert sc.taken is not None and (sc.lat, sc.lon, sc.alt) == (10.5, -3.2, None)
    assert eng.parse_sidecar([], Path("a.json")) is None
    sc = eng.parse_sidecar({"geoData": {"latitude": 123, "longitude": 5}}, Path("a.json"))
    assert not sc.has_gps


@pytest.mark.parametrize("taken,text", [(-157_766_400, "1965:01:01 00:00:00"),
                                        (13_046_400, "1970:06:01 00:00:00")])
def test_pre_1971_dates_are_accepted(taken, text):
    """Les photos scannées avant 1971 doivent être datées ; 0 reste le marqueur « inconnu » (LOG-007)."""
    try:
        expected = datetime.fromtimestamp(taken, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        pytest.skip("cette plateforme ne représente pas cette date")
    sc = eng.parse_sidecar(sidecar_json(taken=taken), Path("a.json"))
    assert sc is not None and sc.taken == expected
    plan = eng.build_plan(Path("a.jpg"), sc, [], eng.Options())
    assert {c.tag: c.value for c in plan.changes}["DateTimeOriginal"] == text
    assert eng.parse_sidecar(sidecar_json(taken=0), Path("a.json")).taken is None


# ── Appariement ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("media,json_name", [
    ("IMG_1.jpg", "IMG_1.jpg.json"),
    ("IMG_1.jpg", "IMG_1.jpg.supplemental-metadata.json"),
    ("IMG_1.jpg", "IMG_1.jpg.supplemental-metad.json"),
    ("IMG_1-edited.jpg", "IMG_1.jpg.json"),
    ("IMG_1-modifié.jpg", "IMG_1.jpg.supplemental-metadata.json"),
    ("IMG_1(1).jpg", "IMG_1.jpg(1).json"),
    ("IMG_1(1).jpg", "IMG_1.jpg.supplemental-metadata(1).json"),
    ("IMG_1_jpg.avif", "IMG_1.jpg.json"),
    ("IMG_1.avif", "IMG_1.jpg.json"),
    ("clip.webm", "clip.mp4.supplemental-metadata.json"),
    ("IMG_1.JPG", "img_1.jpg.json"),
])
def test_matching(tmp_path, media, json_name):
    touch(tmp_path, media)
    js = write_json(tmp_path, json_name)
    assert eng.DirIndex(tmp_path).find(tmp_path / media) == js


def test_duplicates_do_not_cross(tmp_path):
    touch(tmp_path, "a.jpg", "a(1).jpg")
    j0, j1 = write_json(tmp_path, "a.jpg.json"), write_json(tmp_path, "a.jpg(1).json")
    idx = eng.DirIndex(tmp_path)
    assert idx.find(tmp_path / "a.jpg") == j0 and idx.find(tmp_path / "a(1).jpg") == j1


def test_truncated_long_title(tmp_path):
    name = "PXL_20200913_122640123.PORTRAIT-ADDITIONAL-LONG.jpg"
    touch(tmp_path, name)
    js = write_json(tmp_path, "PXL_20200913_122640123.PORTRAIT-ADDITIONAL-LONG.j.json")
    assert eng.DirIndex(tmp_path).find(tmp_path / name) == js


def test_no_match(tmp_path):
    touch(tmp_path, "b.jpg")
    write_json(tmp_path, "other.jpg.json")
    assert eng.DirIndex(tmp_path).find(tmp_path / "b.jpg") is None


def test_analyze_states_and_orphans(tmp_path):
    sub = tmp_path / "Album"
    sub.mkdir()
    touch(sub, "a.jpg", "b.jpg", "c.mp4", "notes.txt")
    write_json(sub, "a.jpg.json")
    (sub / "c.mp4.json").write_text(json.dumps({"title": "c.mp4"}), encoding="utf-8")
    write_json(sub, "ghost.jpg.json")
    res = eng.analyze(tmp_path)
    states = {i.path.name: i.state for i in res.items}
    assert states == {"a.jpg": eng.STATE_READY, "b.jpg": eng.STATE_NO_JSON, "c.mp4": eng.STATE_NO_DATA}
    assert res.orphan_json == 1
    assert eng.analyze(tmp_path, recursive=False).items == []


# ── Plans ─────────────────────────────────────────────────────────────────────
def test_plan_image_writes_dates_and_gps():
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    plan = eng.build_plan(Path("a.jpg"), sc, [], eng.Options(tz=eng.TZ_UTC))
    by_tag = {c.tag: c.value for c in plan.changes}
    assert by_tag["DateTimeOriginal"] == "2020:09:13 12:26:40" and by_tag["CreateDate"] == by_tag["ModifyDate"]
    assert by_tag["GPSCoordinates"].startswith("48.8584, 2.2945")
    assert plan.mtime == sc.taken
    assert {by_tag[t] for t in ("OffsetTime", "OffsetTimeOriginal", "OffsetTimeDigitized")} == {"+00:00"}


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="le fuseau ne se change pas par TZ sous Windows")
def test_default_is_utc_and_local_offset_is_explicit(monkeypatch):
    assert eng.Options().tz == eng.TZ_UTC
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        sc = eng.parse_sidecar(sidecar_json(taken=1_600_000_000), Path("a.json"))        # 13 sept. : CEST
        plan = eng.build_plan(Path("a.jpg"), sc, [], eng.Options(tz=eng.TZ_LOCAL))
        by_tag = {c.tag: c.value for c in plan.changes}
        assert by_tag["DateTimeOriginal"] == "2020:09:13 14:26:40" and by_tag["OffsetTimeOriginal"] == "+02:00"
        winter = eng.parse_sidecar(sidecar_json(taken=1_610_000_000), Path("a.json"))    # 7 janv. : CET
        assert eng.utc_offset_text(winter.taken, eng.TZ_LOCAL) == "+01:00"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_plan_video_uses_utc_quicktime_tags():
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    plan = eng.build_plan(Path("a.mp4"), sc, [], eng.Options(tz=eng.TZ_LOCAL))
    assert not any(c.tag.startswith("OffsetTime") for c in plan.changes)
    tagset = {c.tag for c in plan.changes}
    assert {"CreateDate", "TrackCreateDate", "MediaModifyDate", "GPSCoordinates"} <= tagset
    assert all(c.value == "2020:09:13 12:26:40" for c in plan.changes if "Date" in c.tag)


def test_plan_keeps_existing_unless_overwrite():
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    entries = [tags.TagEntry("ExifIFD", "DateTimeOriginal", "2019:01:01 10:00:00"),
               tags.TagEntry("GPS", "GPSLatitude", "10")]
    keep = eng.build_plan(Path("a.jpg"), sc, entries, eng.Options())
    assert keep.changes == [] and len(keep.notes) == 2
    over = eng.build_plan(Path("a.jpg"), sc, entries, eng.Options(overwrite=True))
    assert over.changes
    zero = [tags.TagEntry("QuickTime", "CreateDate", "0000:00:00 00:00:00")]
    assert eng.build_plan(Path("a.mp4"), sc, zero, eng.Options()).changes


def test_plan_matroska_only_mtime():
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    plan = eng.build_plan(Path("a.webm"), sc, [], eng.Options())
    assert plan.changes == [] and plan.mtime == sc.taken and plan.notes
    assert eng.build_plan(Path("a.webm"), sc, [], eng.Options(align_mtime=False)).is_empty


def test_options_switch_off():
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    plan = eng.build_plan(Path("a.jpg"), sc, [], eng.Options(write_dates=False, write_gps=False, align_mtime=False))
    assert plan.is_empty


# ── Application réelle (ExifTool + ffmpeg) ───────────────────────────────────
needs_tools = pytest.mark.skipif(not (shutil.which("exiftool") and shutil.which("ffmpeg")),
                                 reason="ExifTool et ffmpeg requis")


def run_worker(worker):
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    out = {}
    worker.completed.connect(lambda r: (out.setdefault("r", r), app.quit()))
    worker.failed.connect(lambda m: (out.setdefault("err", m), app.quit()))
    worker.start()
    app.exec()
    worker.wait()
    assert "err" not in out, out.get("err")
    return out["r"]


def exif(path: Path, *args: str) -> dict:
    res = subprocess.run(["exiftool", "-json", "-n", "-G1", *args, str(path)], capture_output=True, text=True)
    return json.loads(res.stdout)[0]


@needs_tools
def test_apply_and_undo_end_to_end(tmp_path, qapp):
    from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker, TakeoutUndoWorker

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:duration=1",
                    "-frames:v", "1", str(tmp_path / "p.jpg")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1",
                    "-c:v", "libx264", str(tmp_path / "v.mp4")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1",
                    "-c:v", "libvpx", str(tmp_path / "w.webm")], check=True)
    for n in ("p.jpg", "v.mp4", "w.webm"):
        write_json(tmp_path, f"{n}.supplemental-metadata.json", title=n)

    analysis = run_worker(AnalyzeWorker([tmp_path], True))
    assert analysis.count(eng.STATE_READY) == 3
    exiftool = Path(shutil.which("exiftool"))
    opts = eng.Options(tz=eng.TZ_UTC)
    res = run_worker(ApplyWorker(exiftool, analysis.items, opts))
    assert res["key"] and all(s[0] != eng.STATE_ERROR for s in res["states"].values()), res

    jpg = exif(tmp_path / "p.jpg")
    assert jpg["ExifIFD:DateTimeOriginal"] == "2020:09:13 12:26:40"
    assert jpg["ExifIFD:OffsetTimeOriginal"] == "+00:00"
    assert round(jpg["GPS:GPSLatitude"], 4) == 48.8584 and round(jpg["GPS:GPSLongitude"], 4) == 2.2945
    mp4 = exif(tmp_path / "v.mp4")
    assert mp4["QuickTime:CreateDate"] == "2020:09:13 12:26:40"
    assert any(k.endswith(":GPSCoordinates") and "48.8584" in str(v) for k, v in mp4.items())
    assert abs((tmp_path / "w.webm").stat().st_mtime - TAKEN) < 2          # WebM : date du fichier seulement
    assert abs((tmp_path / "p.jpg").stat().st_mtime - TAKEN) < 2

    # second passage : les valeurs présentes sont conservées (seule la date du fichier est réalignée)
    again = run_worker(ApplyWorker(exiftool, analysis.items, opts))
    assert {st for st, _ in again["states"].values()} == {eng.STATE_MTIME_ONLY}
    assert exif(tmp_path / "p.jpg")["ExifIFD:DateTimeOriginal"] == "2020:09:13 12:26:40"
    # annulation de la première application
    run_worker(TakeoutUndoWorker(exiftool, res["key"]))
    assert "GPS:GPSLatitude" not in exif(tmp_path / "p.jpg")
    assert "ExifIFD:DateTimeOriginal" not in exif(tmp_path / "p.jpg")


def test_window_smoke(qapp):
    from azimut.modules.takeout.window import TakeoutWindow
    w = TakeoutWindow()
    assert w._options().write_dates and w._options().tz == eng.TZ_UTC
    w.close()


def test_window_backup_option_default_off(qapp):
    from azimut.modules.takeout.window import TakeoutWindow
    w = TakeoutWindow()
    try:
        assert w._options().backup is False
        assert not w.chk_backup.isChecked()
        w.chk_backup.setChecked(True)
        assert w._options().backup is True
    finally:
        w.close()


def test_run_button_waits_for_ready_media_and_write_option(tmp_path, qapp):
    """« Démarrer » exige un média prêt et au moins une option d'écriture."""
    from azimut.modules.takeout.window import TakeoutWindow

    touch(tmp_path, "ready.jpg")
    write_json(tmp_path, "ready.jpg.json")
    w = TakeoutWindow()
    try:
        assert not w.run_button.isEnabled()
        w._exiftool = Path("/usr/bin/exiftool")
        w._items = eng.analyze(tmp_path).items
        w._render()
        assert w.run_button.isEnabled()
        w.chk_dates.setChecked(False)
        w.chk_gps.setChecked(False)
        w.chk_mtime.setChecked(False)
        assert not w.run_button.isEnabled()
    finally:
        w.close()


def test_window_filter_remove_and_action_bar_position(tmp_path, qapp, monkeypatch):
    """Le filtre « sans JSON » et le retrait portent sur la liste, jamais sur les fichiers."""
    from azimut.modules.takeout.window import TakeoutWindow

    from PySide6.QtWidgets import QLabel

    w = TakeoutWindow()
    try:
        layout = w._content_widget.layout()
        assert layout.indexOf(w.action_bar) > layout.indexOf(w.table)
        assert w.cmb_filter.count() == 3
        assert [w.cmb_filter.itemText(i) for i in range(3)] == ["Tout", "Fichiers sans JSON", "Fichiers retirés"]
        assert w.btn_clear.text() == "Vider la liste"
        assert not w.btn_clear.isEnabled() and w.btn_clear.property("kind") == "ghost"
        assert "Liste" not in [label.text() for label in w.findChildren(QLabel)]

        touch(tmp_path, "ready.jpg", "orphan.jpg")
        write_json(tmp_path, "ready.jpg.json")
        w._items = eng.analyze(tmp_path).items
        w._render()
        assert w.table.data_model.rowCount() == 2
        assert w.btn_clear.isEnabled() and w.btn_clear.property("kind") == "secondary"

        w.cmb_filter.setCurrentIndex(1)          # « Fichiers sans JSON »
        assert w.table.data_model.rowCount() == 1
        assert not w.btn_remove.isEnabled()

        w.table.selectRow(0)
        assert w.btn_remove.isEnabled()
        w.btn_remove.click()
        assert len(w._items) == 1 and w._items[0].state == eng.STATE_READY
        assert len(w._removed) == 1
        w.cmb_filter.setCurrentIndex(2)          # « Fichiers retirés »
        assert w.table.data_model.rowCount() == 1
        assert not w.btn_remove.isEnabled()
        monkeypatch.setattr(w, "confirm_clear_list", lambda: False)
        w.btn_clear.click()
        assert w._items and w._removed
        monkeypatch.setattr(w, "confirm_clear_list", lambda: True)
        w.btn_clear.click()
        assert w._items == [] and w._removed == [] and w._roots == [] and w.table.data_model.rowCount() == 0
        assert not w.btn_clear.isEnabled() and w.btn_clear.property("kind") == "ghost"
    finally:
        w.close()


def test_infinite_timestamp_is_ignored_not_fatal(tmp_path):
    """Un JSON externe avec un horodatage infini (1e400, Infinity) ne doit pas faire échouer l'analyse."""
    touch(tmp_path, "a.jpg")
    (tmp_path / "a.jpg.json").write_text(
        '{"photoTakenTime": {"timestamp": 1e400},'
        ' "geoData": {"latitude": 48.8584, "longitude": 2.2945}}', encoding="utf-8")
    res = eng.analyze(tmp_path)
    item = res.items[0]
    assert item.state == eng.STATE_READY
    assert item.sidecar is not None and item.sidecar.taken is None
    assert item.sidecar.has_gps


def test_converted_media_with_two_sidecars_is_not_guessed(tmp_path):
    """Deux JSON de même radical (photo.jpg / photo.png) : ne pas choisir au hasard pour photo.avif."""
    touch(tmp_path, "photo.avif")
    write_json(tmp_path, "photo.jpg.json", taken=TAKEN)
    write_json(tmp_path, "photo.png.json", taken=TAKEN + 3600)
    assert eng.DirIndex(tmp_path).find(tmp_path / "photo.avif") is None


def test_plan_keeps_partial_existing_dates_without_overwrite():
    """Sans overwrite, CreateDate/ModifyDate déjà présentes ne doivent pas être écrasées."""
    sc = eng.parse_sidecar(sidecar_json(), Path("a.json"))
    entries = [tags.TagEntry("ExifIFD", "CreateDate", "2019:01:01 10:00:00"),
               tags.TagEntry("ExifIFD", "ModifyDate", "2018:02:02 09:00:00")]
    plan = eng.build_plan(Path("a.jpg"), sc, entries, eng.Options())
    by_tag = {c.tag: c.value for c in plan.changes}
    assert by_tag["DateTimeOriginal"] == "2020:09:13 12:26:40"
    assert "CreateDate" not in by_tag and "ModifyDate" not in by_tag
    over = eng.build_plan(Path("a.jpg"), sc, entries, eng.Options(overwrite=True))
    assert {"DateTimeOriginal", "CreateDate", "ModifyDate"} <= {c.tag for c in over.changes}


@needs_tools
def test_apply_keeps_existing_create_date_without_overwrite(tmp_path):
    """Preuve réelle : CreateDate 2010 conservée, DateTimeOriginal absente ajoutée."""
    from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker

    jpg = tmp_path / "p.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:duration=1",
                    "-frames:v", "1", str(jpg)], check=True)
    subprocess.run(["exiftool", "-overwrite_original", "-DateTimeOriginal=",
                    "-CreateDate=2010:05:05 05:05:05", str(jpg)], check=True, capture_output=True)
    write_json(tmp_path, "p.jpg.json", title="p.jpg")
    analysis = run_worker(AnalyzeWorker([tmp_path], True))
    res = run_worker(ApplyWorker(Path(shutil.which("exiftool")), analysis.items, eng.Options()))
    assert res["states"][0][0] == eng.STATE_DONE
    after = exif(jpg)
    assert after.get("ExifIFD:CreateDate") == "2010:05:05 05:05:05"
    assert after.get("ExifIFD:DateTimeOriginal") == "2020:09:13 12:26:40"


@needs_tools
def test_apply_refuses_read_only_file(tmp_path):
    """Un fichier 0444 ne doit être ni réécrit ni réaligné."""
    from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker

    jpg = tmp_path / "p.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:duration=1",
                    "-frames:v", "1", str(jpg)], check=True)
    write_json(tmp_path, "p.jpg.json", title="p.jpg")
    os.chmod(jpg, 0o444)
    before = jpg.stat()
    try:
        analysis = run_worker(AnalyzeWorker([tmp_path], True))
        res = run_worker(ApplyWorker(Path(shutil.which("exiftool")), analysis.items, eng.Options()))
        state, message = res["states"][0]
        assert state == eng.STATE_ERROR and "lecture seule" in message
        after = jpg.stat()
        assert (after.st_mtime, after.st_mode) == (before.st_mtime, before.st_mode)
        assert "ExifIFD:DateTimeOriginal" not in exif(jpg)
    finally:
        os.chmod(jpg, 0o644)


def test_sidecar_json_recursion_error_is_not_fatal(tmp_path, monkeypatch):
    """Python 3.11/3.12 lève RecursionError sur un JSON très imbriqué : il doit être « illisible », pas fatal."""
    touch(tmp_path, "a.jpg")
    (tmp_path / "a.jpg.json").write_text("{}", encoding="utf-8")

    def too_deep(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(eng.json, "load", too_deep)
    item = eng.analyze(tmp_path).items[0]
    assert item.state == eng.STATE_NO_JSON and "illisible" in item.message


@needs_tools
def test_undo_restores_atime(tmp_path):
    """L'annulation restaure l'atime d'origine, pas seulement la mtime (TAKEOUT-004)."""
    from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker, TakeoutUndoWorker

    jpg = tmp_path / "p.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:duration=1",
                    "-frames:v", "1", str(jpg)], check=True)
    write_json(tmp_path, "p.jpg.json", title="p.jpg")
    os.utime(jpg, (1_400_000_000, 1_500_000_000))
    exiftool = Path(shutil.which("exiftool"))
    analysis = run_worker(AnalyzeWorker([tmp_path], True))
    res = run_worker(ApplyWorker(exiftool, analysis.items, eng.Options()))
    assert res["key"]
    run_worker(TakeoutUndoWorker(exiftool, res["key"]))
    st = jpg.stat()
    assert abs(st.st_atime - 1_400_000_000) < 2
    assert abs(st.st_mtime - 1_500_000_000) < 2
    assert not (tmp_path / "p.jpg_original").exists()


@needs_tools
def test_apply_with_optional_binary_backup(tmp_path):
    """Option backup : ExifTool conserve l'original octet pour octet sous « …_original »."""
    from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker, TakeoutUndoWorker

    jpg = tmp_path / "p.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:duration=1",
                    "-frames:v", "1", str(jpg)], check=True)
    write_json(tmp_path, "p.jpg.json", title="p.jpg")
    original = jpg.read_bytes()
    exiftool = Path(shutil.which("exiftool"))
    analysis = run_worker(AnalyzeWorker([tmp_path], True))
    res = run_worker(ApplyWorker(exiftool, analysis.items, eng.Options(backup=True)))
    assert res["key"] and res["states"][0][0] == eng.STATE_DONE
    backup = tmp_path / "p.jpg_original"
    assert backup.is_file() and backup.read_bytes() == original
    assert exif(jpg).get("ExifIFD:DateTimeOriginal") == "2020:09:13 12:26:40"
    run_worker(TakeoutUndoWorker(exiftool, res["key"]))
    assert backup.read_bytes() == original          # l'annulation ne supprime pas la copie


def test_backup_write_command_omits_overwrite_original():
    """Sans « -overwrite_original », ExifTool crée la copie « …_original »."""
    cmd = tags.write_command("exiftool", "/tmp/w.args", backup=True)
    assert "-overwrite_original" not in cmd and "-m" in cmd and "-P" in cmd
    assert "-overwrite_original" in tags.write_command("exiftool", "/tmp/w.args")



@needs_tools
def test_apply_handles_jpeg_content_named_heic(tmp_path, qapp):
    """Un export mal étiqueté (contenu JPEG, extension .HEIC) doit tout de même être écrit."""
    from azimut.modules.takeout.workers import AnalyzeWorker, ApplyWorker, TakeoutUndoWorker

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:duration=1",
                    "-frames:v", "1", str(tmp_path / "p.jpg")], check=True)
    (tmp_path / "p.jpg").rename(tmp_path / "p.HEIC")
    write_json(tmp_path, "p.HEIC.supplemental-metadata.json", title="p.HEIC")
    exiftool = Path(shutil.which("exiftool"))

    analysis = run_worker(AnalyzeWorker([tmp_path], True))
    assert analysis.count(eng.STATE_READY) == 1
    res = run_worker(ApplyWorker(exiftool, analysis.items, eng.Options()))
    assert res["states"][0][0] == eng.STATE_DONE, res["states"]
    rec = exif(tmp_path / "p.HEIC")
    assert rec["ExifIFD:DateTimeOriginal"] == "2020:09:13 12:26:40"
    assert round(rec["GPS:GPSLatitude"], 4) == 48.8584
    assert (tmp_path / "p.HEIC").is_file()                  # nom d'origine conservé
    assert not list(tmp_path.glob(".azimut-tmp-*"))         # aucune copie temporaire résiduelle

    run_worker(TakeoutUndoWorker(exiftool, res["key"]))
    rec = exif(tmp_path / "p.HEIC")
    assert "ExifIFD:DateTimeOriginal" not in rec and "GPS:GPSLatitude" not in rec
