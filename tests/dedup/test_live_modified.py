"""Dedup : Live Photos, fichiers « modifiés » (A/B), détection exacte facultative."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import subprocess
from pathlib import Path

import pytest

from azimut.modules.dedup import livephoto
from azimut.modules.dedup.actions import MODE_QUARANTINE, ApplyOptions, apply_groups, undo_journal
from azimut.modules.dedup.engine import Hooks, ScanOptions, scan
from azimut.modules.dedup.model import (
    ACTION_DELETE,
    ACTION_KEEP,
    KIND_EXACT,
    KIND_LIVE,
    KIND_MODIFIED,
    FileItem,
    Group,
    proposal,
    set_actions,
)

FFPROBE = shutil.which("ffprobe")
FFMPEG = shutil.which("ffmpeg")


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def kinds(result) -> dict[str, list[Group]]:
    out: dict[str, list[Group]] = {}
    for g in result.groups:
        out.setdefault(g.kind, []).append(g)
    return out


def names(group: Group) -> set[str]:
    return {i.name for i in group.items}


# ── Noms et jumelage ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("filename,expected", [
    ("IMG_1234.HEIC", ("IMG_1234", "heic")),
    ("IMG_1234_heic.avif", ("IMG_1234", "heic")),
    ("IMG_1234_mov.webm", ("IMG_1234", "mov")),
    ("holiday_trip.avif", ("holiday_trip", "avif")),        # suffixe qui n'est pas une extension connue
    ("noext", ("noext", "")),
    ("a.b.JPG", ("a.b", "jpg")),
])
def test_original_name(filename, expected):
    assert livephoto.original_name(filename) == expected


def test_pair_up_matches_raw_and_converted_names_per_folder():
    entries = [("d1", "IMG_1.HEIC"), ("d1", "IMG_1.MOV"),                 # paire
               ("d1", "IMG_2_heic.avif"), ("d1", "IMG_2_mov.webm"),        # paire convertie
               ("d2", "IMG_1.HEIC"),                                       # autre dossier : pas de vidéo
               ("d3", "IMG_3.JPG"), ("d3", "IMG_3.MOV"), ("d3", "IMG_3.MP4"),   # ambigu
               ("d4", "IMG_4.JPG"), ("d4", "IMG_4.HEIC"), ("d4", "IMG_4.MP4"),  # deux photos, une vidéo
               ("d5", "é.HEIC"), ("d5", "é.MOV")]                          # accents (NFC/NFD)
    pairs, ambiguous = livephoto.pair_up(entries)
    assert ambiguous == 1
    assert sorted((sorted(p), v) for p, v in pairs) == [([0], 1), ([2], 3), ([8, 9], 10), ([11], 12)]


def test_pair_up_is_unicode_normalization_insensitive():
    nfd = "é.HEIC"
    pairs, _ = livephoto.pair_up([("d", nfd), ("d", "é.MOV")])
    assert len(pairs) == 1


def test_parse_duration():
    assert livephoto.parse_duration("2.533000\n") == 2.533
    assert livephoto.parse_duration("N/A") is None and livephoto.parse_duration("") is None
    assert livephoto.parse_duration("-1") is None


def test_video_duration_with_missing_ffprobe(tmp_path):
    assert livephoto.video_duration(tmp_path / "nope-ffprobe", tmp_path / "a.mov") is None


# ── Propositions ──────────────────────────────────────────────────────────────
def item(path: str, side="A", protected=False, size=10):
    return FileItem(Path(path), side, Path("/r"), size, 1.0, protected=protected)


def test_proposal_live_keeps_everything_by_default_or_drops_video_only():
    g = Group(1, KIND_LIVE, [item("/r/a.HEIC"), item("/r/a.MOV")])
    assert set(proposal(g, "a_first").values()) == {ACTION_KEEP}
    res = proposal(g, "a_first", live_drop_video=True)
    assert [res[i] for i in g.items] == [ACTION_KEEP, ACTION_DELETE]
    prot = Group(2, KIND_LIVE, [item("/r/b.HEIC"), item("/r/b.MOV", protected=True)])
    assert set(proposal(prot, "a_first", live_drop_video=True).values()) == {ACTION_KEEP}
    conv = Group(3, KIND_LIVE, [item("/r/c_heic.avif"), item("/r/c_mov.webm")])
    assert [proposal(conv, "a_first", True)[i] for i in conv.items] == [ACTION_KEEP, ACTION_DELETE]


def test_proposal_modified_never_deletes_and_manual_choices_keep_one():
    g = Group(1, KIND_MODIFIED, [item("/r/x.jpg"), item("/r/x.jpg", side="B")])
    assert set(proposal(g, "oldest").values()) == {ACTION_KEEP}
    ok, _ = set_actions(g, [g.items[0]], ACTION_DELETE)
    assert ok
    ok, msg = set_actions(g, [g.items[1]], ACTION_DELETE)
    assert not ok and "au moins un" in msg


# ── Analyse ───────────────────────────────────────────────────────────────────
def test_scan_exact_can_be_disabled(tmp_path):
    a = tmp_path / "A"
    write(a / "x.bin", b"same-content-12345")
    write(a / "y.bin", b"same-content-12345")
    assert kinds(scan(ScanOptions(folder_a=a)))[KIND_EXACT]
    assert scan(ScanOptions(folder_a=a, exact=False)).groups == []


def test_scan_modified_groups_between_a_and_b(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    write(a / "sub/p.jpg", b"version-A-data")
    write(b / "sub/p.jpg", b"version-B-different")
    write(a / "same.jpg", b"identical-bytes-1")
    write(b / "same.jpg", b"identical-bytes-1")
    write(a / "only-a.jpg", b"unique-a-bytes")
    res = scan(ScanOptions(folder_a=a, folder_b=b))
    k = kinds(res)
    assert len(k[KIND_MODIFIED]) == 1 and names(k[KIND_MODIFIED][0]) == {"p.jpg"}
    assert {i.side for i in k[KIND_MODIFIED][0].items} == {"A", "B"}
    assert all(i.action == ACTION_KEEP for i in k[KIND_MODIFIED][0].items)
    assert len(k[KIND_EXACT]) == 1                       # same.jpg n'est PAS « modifié »
    assert res.stats.modified_groups == 1 and "1 modifié(s)" in res.stats.summary()
    assert not kinds(scan(ScanOptions(folder_a=a, folder_b=b, find_modified=False))).get(KIND_MODIFIED)
    assert not kinds(scan(ScanOptions(folder_a=a))).get(KIND_MODIFIED)          # un seul dossier


def test_scan_live_by_name_only_without_ffprobe(tmp_path):
    a = tmp_path / "A"
    for n in ("IMG_1.HEIC", "IMG_1.MOV", "IMG_2_heic.avif", "IMG_2_mov.webm", "IMG_3.JPG"):
        write(a / n, n.encode() * 3)
    write(a / "dup1.bin", b"duplicated-content")
    write(a / "dup2.bin", b"duplicated-content")
    res = scan(ScanOptions(folder_a=a, live=True))
    k = kinds(res)
    assert sorted(sorted(names(g)) for g in k[KIND_LIVE]) == [["IMG_1.HEIC", "IMG_1.MOV"],
                                                              ["IMG_2_heic.avif", "IMG_2_mov.webm"]]
    assert res.stats.live_groups == 2 and res.stats.live_unknown_duration == 2
    assert len(k[KIND_EXACT]) == 1
    assert all(i.action == ACTION_KEEP for g in k[KIND_LIVE] for i in g.items)
    assert not kinds(scan(ScanOptions(folder_a=a))).get(KIND_LIVE)             # option désactivée


def test_live_files_are_not_swallowed_by_exact_groups(tmp_path):
    a = tmp_path / "A"
    write(a / "IMG_1.HEIC", b"same-bytes-photo")
    write(a / "IMG_1.MOV", b"video-bytes")
    write(a / "copy.HEIC", b"same-bytes-photo")                  # doublon exact de la photo de la Live Photo
    k = kinds(scan(ScanOptions(folder_a=a, live=True)))
    assert names(k[KIND_LIVE][0]) == {"IMG_1.HEIC", "IMG_1.MOV"}
    assert KIND_EXACT not in k                                   # la photo jumelée sort de la recherche de doublons


def test_live_drop_video_option_sets_proposal(tmp_path):
    a = tmp_path / "A"
    write(a / "IMG_1.HEIC", b"photo")
    write(a / "IMG_1.MOV", b"video")
    g = scan(ScanOptions(folder_a=a, live=True, live_drop_video=True)).groups[0]
    assert {i.name: i.action for i in g.items} == {"IMG_1.HEIC": ACTION_KEEP, "IMG_1.MOV": ACTION_DELETE}


@pytest.mark.skipif(not (FFPROBE and FFMPEG), reason="ffmpeg et ffprobe requis")
def test_live_duration_threshold_with_real_ffprobe(tmp_path):
    a = tmp_path / "A"
    a.mkdir()

    def clip(name: str, seconds: float) -> None:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={seconds}:r=5",
                        "-c:v", "libx264", str(a / name)], check=True)

    clip("SHORT.MOV", 2)
    clip("LONG.MOV", 8)
    for n in ("SHORT.HEIC", "LONG.HEIC"):
        write(a / n, n.encode())
    res = scan(ScanOptions(folder_a=a, live=True, ffprobe=Path(FFPROBE), live_max_seconds=5))
    live = kinds(res)[KIND_LIVE]
    assert len(live) == 1 and names(live[0]) == {"SHORT.HEIC", "SHORT.MOV"}
    video = next(i for i in live[0].items if i.name.endswith(".MOV"))
    assert 1.5 < video.duration < 2.6
    assert res.stats.live_too_long == 1
    wide = scan(ScanOptions(folder_a=a, live=True, ffprobe=Path(FFPROBE), live_max_seconds=10))
    assert len(kinds(wide)[KIND_LIVE]) == 2


def test_apply_live_video_goes_to_quarantine_and_is_restorable(tmp_path):
    a = tmp_path / "A"
    photo = write(a / "IMG_9.HEIC", b"photo-bytes")
    video = write(a / "IMG_9.MOV", b"video-bytes")
    res = scan(ScanOptions(folder_a=a, live=True, live_drop_video=True))
    report = apply_groups(res.groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    assert report.moved == 1 and photo.exists() and not video.exists()
    undo = undo_journal(report.journal)
    assert undo.restored == 1 and video.read_bytes() == b"video-bytes"


# ── Fenêtre ───────────────────────────────────────────────────────────────────
def test_window_collects_live_options_and_persists_them(qapp, tmp_path):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    a = tmp_path / "A"
    a.mkdir()
    w = DedupWindow(MODULE)
    w.field_a.set_path(str(a))
    w.chk_live.setChecked(True)
    w.cmb_live_max.setCurrentIndex(w.cmb_live_max.findData(10.0))
    w.chk_live_drop.setChecked(True)
    w.chk_exact.setChecked(False)
    w.chk_modified.setChecked(False)
    opts = w._collect_options()
    assert opts is not None
    assert (opts.live, opts.live_max_seconds, opts.live_drop_video, opts.exact, opts.find_modified) == \
           (True, 10.0, True, False, False)
    w._save_prefs()
    w.close()
    w2 = DedupWindow(MODULE)
    assert w2.chk_live.isChecked() and w2.chk_live_drop.isChecked() and not w2.chk_exact.isChecked()
    assert w2.cmb_live_max.currentData() == 10.0
    # aucune détection cochée : refus explicite
    w2.field_a.set_path(str(a))
    w2.chk_live.setChecked(False)
    w2.chk_similar.setChecked(False)
    assert w2._collect_options() is None
    w2.close()
