"""Dépôt intelligent, récents et palette de commandes d'Atlas (fonctions pures, sans écran)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

from azimut.launcher import routing
from azimut.launcher.palette import PaletteEntry, filter_entries, normalize, score
from azimut.launcher.recents import MAX_RECENTS, RecentStore, ago

ALL = ["convert", "tags", "takeout", "dedup", "press", "rename", "scope"]


def _touch(path, name):
    f = path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return str(f)


def test_summarize_counts_families(tmp_path):
    paths = [_touch(tmp_path, n) for n in ("a.jpg", "b.HEIC", "c.mp4", "d.mp3", "e.json", "f.txt", "g.NEF")]
    s = routing.summarize(paths)
    assert (s.images, s.raws, s.videos, s.audios, s.jsons, s.others) == (2, 1, 1, 1, 1, 1)
    assert s.media == 5 and not s.takeout and s.folders == 0


def test_summarize_folder_scan_is_bounded_and_detects_takeout(tmp_path):
    root = tmp_path / "Takeout" / "Google Photos" / "Album"
    _touch(root, "IMG_1.jpg")
    _touch(root, "IMG_1.jpg.supplemental-metadata.json")
    s = routing.summarize([str(tmp_path / "Takeout")])
    assert s.folders == 1 and s.takeout and s.images == 1 and s.jsons == 1
    deep = tmp_path / "deep" / "a" / "b" / "c"
    _touch(deep, "hidden.jpg")
    assert routing.summarize([str(tmp_path / "deep")]).images == 0          # au-delà de trois niveaux


def test_scan_limit_marks_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(routing, "SCAN_LIMIT", 5)
    for i in range(20):
        _touch(tmp_path / "many", f"{i}.jpg")
    s = routing.summarize([str(tmp_path / "many")])
    assert s.truncated and "aperçu limité" in routing.describe(s)


def test_suggest_images_and_video_prefers_convert(tmp_path):
    out = routing.suggest([_touch(tmp_path, "a.jpg"), _touch(tmp_path, "b.mov")], ALL)
    ids = [x.module_id for x in out]
    assert ids[0] == "convert" and {"tags", "rename", "press"} <= set(ids)
    assert "dedup" not in ids and "scope" not in ids                       # pas de dossier déposé


def test_suggest_two_folders_scope_first(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    _touch(a, "x.jpg")
    _touch(b, "x.jpg")
    out = routing.suggest([str(a), str(b)], ALL)
    assert out[0].module_id == "scope" and "dedup" in [x.module_id for x in out]


def test_suggest_takeout_and_json_only(tmp_path):
    root = tmp_path / "Takeout"
    _touch(root, "a.jpg")
    assert routing.suggest([str(root)], ALL)[0].module_id == "takeout"
    j = routing.suggest([_touch(tmp_path, "data.json")], ALL)
    assert [x.module_id for x in j] == ["press"]


def test_suggest_respects_available(tmp_path):
    out = routing.suggest([_touch(tmp_path, "a.jpg")], ["tags"])
    assert [x.module_id for x in out] == ["tags"]
    assert routing.suggest([_touch(tmp_path, "z.xyz")], ALL) == []


def test_recent_store_dedup_order_and_limit(tmp_path, monkeypatch):
    from azimut.core.prefs import Preferences
    store = RecentStore(Preferences(tmp_path / "prefs.json").module("atlas"))
    store.add(["/a", "/b"], "convert")
    store.add(["/a"], "tags")
    items = store.items()
    assert [(r.path, r.module) for r in items] == [("/a", "tags"), ("/b", "convert")]
    store.add([f"/p{i}" for i in range(30)], "dedup")
    assert len(store.items()) == MAX_RECENTS
    store.clear()
    assert store.items() == []


def test_ago_labels():
    now = time.time()
    assert ago(now, now) == "à l'instant"
    assert ago(now - 300, now) == "il y a 5 min"
    assert ago(now - 7200, now) == "il y a 2 h"
    assert ago(now - 3 * 86400, now) == "il y a 3 j"


def test_palette_filter_accents_and_order():
    entries = [PaletteEntry("a", "Azimut Convert", "Convertir audio, vidéo", keywords=("ffmpeg",)),
               PaletteEntry("b", "Azimut Dedup", "Trouver les doublons"),
               PaletteEntry("c", "Préférences…", "Thème, réglages")]
    assert normalize("Réglages") == "reglages"
    assert [e.id for e in filter_entries(entries, "")] == ["a", "b", "c"]
    assert [e.id for e in filter_entries(entries, "doublon")] == ["b"]
    assert [e.id for e in filter_entries(entries, "reglages")] == ["c"]
    assert [e.id for e in filter_entries(entries, "ffmpeg")] == ["a"]
    assert [e.id for e in filter_entries(entries, "conv aud")] == ["a"]       # tous les mots doivent correspondre
    assert filter_entries(entries, "zzz") == []
    assert score(entries[0], "convert") > score(entries[0], "audio")           # titre > description
