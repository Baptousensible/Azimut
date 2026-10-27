from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from azimut.modules.tags import engine as eng
from azimut.modules.tags.engine import TagChange

# Sortie réaliste d'ExifTool : -json -G1 -a -s
RECORD = {
    "SourceFile": "/photos/a.jpg",
    "System:FileName": "a.jpg",
    "File:FileType": "JPEG",
    "ExifTool:ExifToolVersion": 12.76,
    "IFD0:Make": "Canon",
    "IFD0:Model": "EOS 5D",
    "ExifIFD:DateTimeOriginal": "2019:04:12 15:13:42",
    "GPS:GPSLatitude": "48 deg 51' 0.00\" N",
    "XMP-dc:Subject": ["vacances", "été"],
    "XMP-dc:Title": "Tour Eiffel",
    "IPTC:Keywords": "paris",
    "IFD1:ThumbnailImage": "(Binary data 5120 bytes, use -b option to extract)",
    "MakerNotes:LensType": "EF 24-70mm",
    "ICC_Profile:ProfileDescription": "sRGB",
    "Composite:GPSPosition": "48.85 N, 2.35 E",
}


def test_parse_entries_groups_values_and_readonly():
    entries = {e.full_name: e for e in eng.parse_entries(RECORD)}
    assert "SourceFile" not in entries
    assert entries["IFD0:Make"].value == "Canon" and entries["IFD0:Make"].writable
    assert entries["ExifIFD:DateTimeOriginal"].writable
    assert entries["XMP-dc:Subject"].value == "vacances, été"
    assert entries["XMP-dc:Subject"].items == ("vacances", "été")
    assert entries["IPTC:Keywords"].writable
    for ro in ("System:FileName", "File:FileType", "ExifTool:ExifToolVersion", "Composite:GPSPosition",
               "MakerNotes:LensType", "ICC_Profile:ProfileDescription"):
        assert not entries[ro].writable, ro
    assert not entries["IFD1:ThumbnailImage"].writable          # binaire


def test_parse_entries_strips_duplicate_suffix():
    e = eng.parse_entries({"XMP-dc:Title (1)": "x"})[0]
    assert (e.group, e.name) == ("XMP-dc", "Title")


def test_read_command_options():
    cmd = eng.read_command("exiftool", "/tmp/a.args")
    assert cmd == ["exiftool", "-json", "-G1", "-a", "-s", "-charset", "exiftool=utf8",
                   "-charset", "filename=utf8", "-m", "-q", "-@", "/tmp/a.args"]
    assert "-n" not in cmd and "-n" in eng.read_command("exiftool", "x", numeric=True)
    assert eng.READ_CHUNK == 200
    assert [len(c) for c in eng.chunks(list(range(450)))] == [200, 200, 50]


def test_write_command_and_argfile(tmp_path):
    cmd = eng.write_command("exiftool", "/tmp/w.args")
    for opt in ("-m", "-overwrite_original", "-P"):
        assert opt in cmd
    assert cmd[-2:] == ["-@", "/tmp/w.args"]
    f = eng.write_argfile(["-XMP-dc:Title=Été à Paris", str(tmp_path / "é.jpg")])
    try:
        assert f.read_text(encoding="utf-8").splitlines() == ["-XMP-dc:Title=Été à Paris", str(tmp_path / "é.jpg")]
    finally:
        f.unlink()


def test_change_args_set_and_delete():
    args = eng.change_args([TagChange("set", "IFD0:Artist", "Léa"), TagChange("delete", "XMP-dc:Title")])
    assert args == ["-IFD0:Artist=Léa", "-XMP-dc:Title="]
    assert eng.change_args([TagChange("set", "IFD0:Artist", "a\nb")]) == ["-IFD0:Artist=a b"]
    with pytest.raises(ValueError):
        eng.change_args([TagChange("rename", "x")])


def test_preset_date_tags_and_validation():
    changes = eng.preset_set_date("2020-01-02 03:04:05")
    assert [c.tag for c in changes] == ["DateTimeOriginal", "CreateDate", "ModifyDate", "MediaCreateDate",
                                        "MediaModifyDate", "TrackCreateDate", "TrackModifyDate"]
    assert all(c.value == "2020:01:02 03:04:05" for c in changes)
    assert eng.change_args(changes)[0] == "-DateTimeOriginal=2020:01:02 03:04:05"
    for bad in ("", "2020-13-01 00:00:00", "2020-01-02", "1850-01-01 00:00:00", "2999-01-01 00:00:00"):
        with pytest.raises(ValueError):
            eng.preset_set_date(bad)
    assert eng.date_from_changes(changes) == datetime(2020, 1, 2, 3, 4, 5)


def test_gps_image_vs_quicktime_exact_tags():
    changes = eng.preset_set_gps(48.85, 2.35, 35)
    assert eng.build_tag_args("photo.jpg", changes) == [
        "-GPSLatitude=48.85", "-GPSLatitudeRef=N", "-GPSLongitude=2.35", "-GPSLongitudeRef=E",
        "-GPSAltitude=35", "-GPSAltitudeRef=0"]
    for ext in (".mp4", ".MOV", "clip.m4v", ".3gp"):
        assert eng.build_tag_args(ext, changes) == ["-GPSCoordinates=48.85, 2.35, 35"]
    sud_ouest = eng.preset_set_gps(-33.9, -70.6, -12)
    args = eng.build_tag_args(".jpg", sud_ouest)
    assert "-GPSLatitude=33.9" in args and "-GPSLatitudeRef=S" in args
    assert "-GPSLongitudeRef=W" in args and "-GPSAltitude=12" in args and "-GPSAltitudeRef=1" in args
    assert eng.build_tag_args(".mp4", sud_ouest) == ["-GPSCoordinates=-33.9, -70.6, -12"]


def test_delete_gps_image_vs_quicktime():
    changes = eng.preset_delete_gps()
    assert eng.build_tag_args(".jpg", changes) == ["-GPS:All="]
    assert eng.build_tag_args(".avif", changes) == ["-GPS:All="]
    assert eng.build_tag_args(".mp4", changes) == ["-GPSCoordinates="]
    assert eng.build_tag_args(".jpg", eng.preset_delete_all()) == ["-All="]


def test_readonly_group_refused():
    for tag in ("File:FileName", "System:FileSize", "Composite:GPSPosition", "ExifTool:Foo",
                "ICC_Profile:Bar", "MakerNotes:LensType"):
        assert eng.validate_changes([TagChange("set", tag, "x")]), tag
    assert eng.validate_changes([TagChange("set", "IFD0:Make", "x"), TagChange("delete", "XMP-dc:Title"),
                                 TagChange("delete", "IPTC:Keywords"), TagChange("delete", "GPS:All"),
                                 TagChange("delete", "All")]) == []
    ro = next(e for e in eng.parse_entries(RECORD) if e.group == "System")
    assert "lecture seule" in eng.entry_refusal(ro)
    binary = next(e for e in eng.parse_entries(RECORD) if e.name == "ThumbnailImage")
    assert eng.entry_refusal(binary)
    assert eng.validate_changes([TagChange("set", "IFD0:Make=x", "y")])           # nom de tag invalide


def test_matroska_write_refused():
    for name in ("film.mkv", "clip.WEBM", ".mkv"):
        assert "format non modifiable ici" in (eng.write_refusal(name) or "")
    for name in ("a.jpg", "a.mp4", "a.avif"):
        assert eng.write_refusal(name) is None


def test_gps_validation():
    for lat, lon in ((91, 0), ("91", "0"), (-90.5, 10), (10, 181), (0, 0), ("0", "0"), ("abc", 1), (float("nan"), 1)):
        with pytest.raises(ValueError):
            eng.validate_gps(lat, lon)
    assert eng.validate_gps("48,85", "2,35") == (48.85, 2.35, 0.0)
    assert eng.validate_gps(90, 180, "") == (90.0, 180.0, 0.0)
    assert eng.validate_gps(0, 5.5, "12") == (0.0, 5.5, 12.0)
    with pytest.raises(ValueError):
        eng.parse_coordinates_text("91,0")
    with pytest.raises(ValueError):
        eng.parse_coordinates_text("0,0")
    assert eng.parse_coordinates_text("48.85, 2.35, 35") == (48.85, 2.35, 35.0)


def test_snapshot_then_restoration_absent_tag_is_deleted():
    entries = eng.parse_entries(RECORD)
    changes = eng.resolve_changes(".jpg", eng.preset_set_date("2020-01-02 03:04:05"))
    before, unrest = eng.snapshot_before(entries, changes)
    assert unrest == []
    assert before["ExifIFD:DateTimeOriginal"] == "2019:04:12 15:13:42"
    assert before["CreateDate"] is None and before["TrackModifyDate"] is None       # absents à l'origine
    restore = eng.restoration_changes(before)
    args = eng.change_args(restore)
    assert "-ExifIFD:DateTimeOriginal=2019:04:12 15:13:42" in args
    assert "-CreateDate=" in args and "-MediaCreateDate=" in args


def test_snapshot_lists_gps_and_binary():
    entries = eng.parse_entries(RECORD)
    before, _ = eng.snapshot_before(entries, eng.resolve_changes(".jpg", eng.preset_delete_gps()))
    assert before == {"GPS:GPSLatitude": "48 deg 51' 0.00\" N"}
    before, _ = eng.snapshot_before(entries, [TagChange("delete", "XMP-dc:Subject")])
    assert before == {"XMP-dc:Subject": ["vacances", "été"]}
    assert eng.change_args(eng.restoration_changes(before)) == ["-XMP-dc:Subject=vacances", "-XMP-dc:Subject=été"]
    before, unrest = eng.snapshot_before(entries, eng.preset_delete_all())
    assert "IFD0:Make" in before and "System:FileName" not in before and "ICC_Profile:ProfileDescription" not in before
    assert unrest == ["IFD1:ThumbnailImage"]


def test_snapshot_set_on_missing_explicit_tag():
    entries = eng.parse_entries({"IFD0:Make": "Canon"})
    before, _ = eng.snapshot_before(entries, [TagChange("set", "XMP-dc:Title", "Nouveau")])
    assert before == {"XMP-dc:Title": None}
    assert eng.restoration_changes(before) == [TagChange("delete", "XMP-dc:Title")]


def test_parse_write_result():
    assert eng.parse_write_result(0, "    1 image files updated\n", "") == (True, "")
    assert eng.parse_write_result(0, "    1 image files unchanged\n", "")[0] is True
    ok, msg = eng.parse_write_result(0, "    1 image files updated\n", "Warning: Sorry, X is not writable\n")
    assert ok and "Sorry" in msg
    ok, msg = eng.parse_write_result(1, "", "Error: File format error - a.jpg\n")
    assert not ok and "File format error" in msg
    ok, _ = eng.parse_write_result(0, "    0 image files updated\n", "")
    assert not ok
    assert eng.parse_write_result(1, "    1 files weren't updated due to errors\n", "")[0] is False


def test_json_output_and_record_matching():
    assert eng.parse_json_output("pas du json") is None
    assert eng.parse_json_output('[{"SourceFile": "a.jpg"}]') == [{"SourceFile": "a.jpg"}]
    paths = [Path("/x/a.jpg"), Path("/x/b.jpg")]
    recs = [{"SourceFile": "/x/b.jpg", "k": 2}]
    out = eng.match_records(paths, recs)
    assert out[0] == {} and out[1]["k"] == 2


def test_merge_pending_replaces_same_tag_and_keeps_batches():
    p = [TagChange("set", "IFD0:Make", "A"), TagChange("delete", "GPS:All")]
    p = eng.merge_pending(p, [TagChange("set", "ifd0:make", "B")])
    assert p == [TagChange("delete", "GPS:All"), TagChange("set", "ifd0:make", "B")]
    p = eng.merge_pending(p, [TagChange("set", "XMP-dc:Subject", "a"), TagChange("set", "XMP-dc:Subject", "b")])
    assert sum(1 for c in p if c.tag == "XMP-dc:Subject") == 2


def test_undo_keys_and_record():
    keys = [eng.new_undo_key(datetime(2026, 1, i, 10, 0, 0)) for i in range(1, 6)]
    assert eng.latest_undo_key(keys) == keys[-1] and eng.latest_undo_key([]) is None
    assert eng.keys_to_prune(keys, 3) == keys[:2]
    rec = eng.undo_record([eng.snapshot_record(Path("/x/a.jpg"), 1.0, {"A:B": None}, [])],
                          [TagChange("delete", "All")], datetime(2026, 1, 1, 10, 0, 0))
    assert rec["timestamp"] == "2026-01-01T10:00:00" and rec["files"][0]["before"] == {"A:B": None}


def test_module_import_does_not_load_qtwidgets():
    root = Path(__file__).resolve().parents[2]
    code = ("import sys; from azimut.modules.tags import MODULE; "
            "assert MODULE.id == 'tags' and MODULE.requires == ('exiftool',); "
            "assert 'PySide6.QtWidgets' not in sys.modules")
    env = {**os.environ, "PYTHONPATH": str(root)}
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=root)
    assert res.returncode == 0, res.stderr
