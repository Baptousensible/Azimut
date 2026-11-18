"""Dedup : Live Photos, fichiers « modifiés » (A/B), détection exacte facultative."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import subprocess
import sys
import textwrap
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from azimut.modules.dedup import engine as dedup_engine, livephoto
from azimut.modules.dedup.actions import MODE_DELETE, MODE_QUARANTINE, ApplyOptions, apply_groups, undo_journal
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
    resolve_group,
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


def script(path: Path, body: str) -> Path:
    """Faux binaire exécutable (ffprobe simulé)."""
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path


def test_video_duration_cancel_stops_the_probe_without_waiting_for_timeout(tmp_path):
    """Une annulation pendant la sonde rend la main tout de suite, pas après 30 s (DEDUP-003)."""
    slow = script(tmp_path / "ffprobe-lent", """
        import time
        time.sleep(60)
        """)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    assert livephoto.video_duration(slow, tmp_path / "a.mov", timeout=30, cancel=stop.is_set) is None
    assert time.monotonic() - t0 < 5


@pytest.mark.skipif(sys.platform == "win32", reason="groupes de processus POSIX")
def test_video_duration_timeout_kills_the_wrapper_and_its_child(tmp_path):
    """Un wrapper qui lance le vrai ffprobe ne laisse pas de petit-fils vivant (DEDUP-004)."""
    marker = tmp_path / "petit-fils.txt"
    wrapper = script(tmp_path / "ffprobe-wrapper", f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-c",
            "import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('vivant')"])
        time.sleep(120)
        """)
    assert livephoto.video_duration(wrapper, tmp_path / "a.mov", timeout=1) is None
    time.sleep(3.5)
    assert not marker.exists()


def test_kill_tree_uses_taskkill_tree_on_windows(monkeypatch):
    """Sous Windows, taskkill /T tue le sous-arbre (TerminateProcess ne voit que l'enfant direct)."""
    calls, kills = [], []

    class Proc:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            kills.append(True)

    monkeypatch.setattr(livephoto, "_IS_WINDOWS", True)
    monkeypatch.setattr(livephoto.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    livephoto._kill_tree(Proc())
    assert calls[0][0] == ["taskkill", "/T", "/F", "/PID", "4242"]
    assert kills == []


def test_kill_tree_falls_back_to_kill_when_taskkill_fails(monkeypatch):
    """taskkill indisponible ou bloqué : repli sur TerminateProcess, sans laisser filer l'erreur."""
    kills = []

    class Proc:
        pid = 7

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("taskkill", timeout)

        def kill(self):
            kills.append(True)

    def boom(*_args, **_kwargs):
        raise OSError("taskkill introuvable")

    monkeypatch.setattr(livephoto, "_IS_WINDOWS", True)
    monkeypatch.setattr(livephoto.subprocess, "run", boom)
    livephoto._kill_tree(Proc())
    assert kills == [True]


def test_video_duration_reads_a_normal_probe(tmp_path):
    """La sonde nominale rend la valeur de ffprobe (sortie binaire décodée)."""
    ok = script(tmp_path / "ffprobe-ok", """
        print("2.5")
        """)
    assert livephoto.video_duration(ok, tmp_path / "a.mov") == 2.5


def test_video_duration_bounds_a_talkative_probe_output(tmp_path):
    """Un ffprobe très bavard n'est pas chargé entier en mémoire (DEDUP-005)."""
    flood = script(tmp_path / "ffprobe-bavard", """
        import sys
        sys.stdout.write("x" * (50 * 1024 * 1024))
        """)
    tracemalloc.start()
    try:
        assert livephoto.video_duration(flood, tmp_path / "a.mov") is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f"pic mémoire {peak / 1024 / 1024:.1f} Mio"


def test_scan_cancel_during_duration_probe_is_immediate(tmp_path):
    """Annuler un scan n'attend pas le timeout de la sonde en cours (DEDUP-003)."""
    a = tmp_path / "A"
    write(a / "IMG_1.HEIC", b"photo")
    write(a / "IMG_1.MOV", b"video")
    slow = script(tmp_path / "ffprobe-lent", """
        import time
        time.sleep(60)
        """)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    with pytest.raises(dedup_engine.ScanCancelled):
        scan(ScanOptions(folder_a=a, live=True, exact=False, similar=False, find_modified=False,
                         ffprobe=slow), Hooks(cancel=stop.is_set, workers=1))
    assert time.monotonic() - t0 < 5


# ── Propositions ──────────────────────────────────────────────────────────────
def item(path: str, side="A", protected=False, size=10):
    return FileItem(Path(path), side, Path("/r"), size, 1.0, protected=protected)


def test_proposal_live_keeps_everything_by_default_or_drops_video_only():
    g = Group(1, KIND_LIVE, [item("/r/a.HEIC"), item("/r/a.MOV")])
    g.items[1].duration = 2.0
    assert set(proposal(g, "a_first").values()) == {ACTION_KEEP}
    res = proposal(g, "a_first", live_drop_video=True)
    assert [res[i] for i in g.items] == [ACTION_KEEP, ACTION_DELETE]
    prot = Group(2, KIND_LIVE, [item("/r/b.HEIC"), item("/r/b.MOV", protected=True)])
    assert set(proposal(prot, "a_first", live_drop_video=True).values()) == {ACTION_KEEP}
    conv = Group(3, KIND_LIVE, [item("/r/c_heic.avif"), item("/r/c_mov.webm")])
    conv.items[1].duration = 2.0
    assert [proposal(conv, "a_first", True)[i] for i in conv.items] == [ACTION_KEEP, ACTION_DELETE]


def test_proposal_modified_never_deletes_and_manual_choices_can_delete_all():
    g = Group(1, KIND_MODIFIED, [item("/r/x.jpg"), item("/r/x.jpg", side="B")])
    assert set(proposal(g, "oldest").values()) == {ACTION_KEEP}
    for selected in g.items:
        ok, message = set_actions(g, [selected], ACTION_DELETE)
        assert ok, message
    assert all(i.action == ACTION_DELETE for i in g.items)


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
    dropped = scan(ScanOptions(folder_a=a, live=True, live_drop_video=True))
    assert all(i.action == ACTION_KEEP for g in dropped.groups if g.kind == KIND_LIVE for i in g.items)
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
    # Sans ffprobe, la durée est inconnue : la vidéo n'est jamais proposée à la suppression.
    assert {i.name: i.action for i in g.items} == {"IMG_1.HEIC": ACTION_KEEP, "IMG_1.MOV": ACTION_KEEP}
    video = next(i for i in g.items if i.name.endswith(".MOV"))
    video.duration = 2.0
    resolve_group(g, "a_first", live_drop_video=True)
    assert video.action == ACTION_DELETE


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
    paired = next(i for g in res.groups for i in g.items if i.name.endswith(".MOV"))
    paired.duration = 2.0
    resolve_group(next(g for g in res.groups if paired in g.items), "a_first", live_drop_video=True)
    report = apply_groups(res.groups, ApplyOptions(MODE_QUARANTINE, quarantine_parent=tmp_path))
    assert report.moved == 1 and photo.exists() and not video.exists()
    undo = undo_journal(report.journal)
    assert undo.restored == 1 and video.read_bytes() == b"video-bytes"


def test_similar_groups_are_star_clusters_around_the_keeper(tmp_path, monkeypatch):
    """Une chaîne A≈B≈C ne doit pas faire supprimer C si C est loin du gardien A."""
    paths = []
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        p = write(tmp_path / name, name.encode() * 4)
        paths.append(p)
    base = 0xAAAAAAAAAAAAAAAA
    hashes = {"a.jpg": base, "b.jpg": base ^ 1, "c.jpg": base ^ ((1 << 64) - 1)}
    monkeypatch.setattr(dedup_engine, "dhash_file", lambda p: (hashes[p.name], 10, 10))
    items = [FileItem(p, "A", tmp_path, p.stat().st_size, p.stat().st_mtime) for p in paths]
    remaining, similar = dedup_engine.find_similar_groups(
        [], items, ScanOptions(folder_a=tmp_path, similar=True, tolerance=1),
        Hooks(), dedup_engine.ScanStats())
    assert len(similar) == 1 and {i.name for i in similar[0]} == {"a.jpg", "b.jpg"}
    assert not any(i.name == "c.jpg" for g in similar for i in g)


def test_live_unknown_duration_is_never_deleted_by_proposal():
    g = Group(1, KIND_LIVE, [item("/r/a.HEIC"), item("/r/a.MOV")])
    assert set(proposal(g, "a_first", live_drop_video=True).values()) == {ACTION_KEEP}
    g.items[1].duration = 0.0
    assert set(proposal(g, "a_first", live_drop_video=True).values()) == {ACTION_KEEP}
    g.items[1].duration = 2.0
    assert proposal(g, "a_first", live_drop_video=True)[g.items[1]] == ACTION_DELETE


# ── Fenêtre ───────────────────────────────────────────────────────────────────
def test_apply_warns_when_group_keeps_no_file(qapp, tmp_path, monkeypatch):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow

    a = write(tmp_path / "a.bin", b"same")
    b = write(tmp_path / "b.bin", b"same")
    group = Group(1, KIND_EXACT, [
        FileItem(a, "A", tmp_path, a.stat().st_size, a.stat().st_mtime),
        FileItem(b, "B", tmp_path, b.stat().st_size, b.stat().st_mtime),
    ])
    for it in group.items:
        it.action = ACTION_DELETE

    w = DedupWindow(MODULE)
    calls: list[tuple[str, str, bool]] = []

    def ask(title: str, text: str, danger: bool = False) -> bool:
        calls.append((title, text, danger))
        return False

    monkeypatch.setattr(w, "_ask", ask)
    w._groups = [group]
    w.cmb_mode.setCurrentIndex(w.cmb_mode.findData(MODE_DELETE))
    try:
        w._on_apply()
    finally:
        w.close()
    assert calls and calls[0][0] == "Aucun fichier conservé"
    assert "Aucun fichier ne sera conservé" in calls[0][1]
    assert calls[0][2] is True


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


def test_protect_orb_animates_the_padlock(qapp):
    from PySide6.QtCore import QAbstractAnimation
    from PySide6.QtTest import QTest

    from azimut.modules.dedup.window import _ProtectOrb
    orb = _ProtectOrb("A", "Protéger A", "Protéger A")
    orb.show()
    qapp.processEvents()
    try:
        # L'anse se lève à l'ouverture et revient à zéro quand le cadenas se ferme.
        assert _ProtectOrb._shackle_angle(0.0) > 0
        assert _ProtectOrb._shackle_angle(1.0) == 0.0
        orb.setChecked(True)
        assert orb._check_anim.state() == QAbstractAnimation.State.Running
        orb._set_check(0.5)                  # état intermédiaire : l'anse est peinte entrouverte
        assert not orb.grab().isNull()
        QTest.qWait(320)
        assert orb._check == 1.0
    finally:
        orb.close()


def test_run_button_waits_for_folder_and_detection(tmp_path, qapp):
    """« Démarrer l'analyse » exige un dossier A et au moins une détection."""
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow

    folder = tmp_path / "A"
    folder.mkdir()
    w = DedupWindow(MODULE)
    try:
        assert not w.run_button.isEnabled()
        w.field_a.set_path(str(folder))
        assert w.run_button.isEnabled()
        for check in (w.chk_exact, w.chk_similar, w.chk_live):
            check.setChecked(False)
        assert not w.run_button.isEnabled()
        w.chk_exact.setChecked(True)
        assert w.run_button.isEnabled()
    finally:
        w.close()


def test_window_folders_are_side_by_side_and_swappable(qapp, tmp_path):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    from azimut.ui.widgets import PathDropZone
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    w = DedupWindow(MODULE)
    assert isinstance(w.field_a, PathDropZone) and isinstance(w.field_b, PathDropZone)
    assert w.field_a.import_progress.parent() is w.field_a
    w.field_a.set_path(str(a))
    w.field_b.set_path(str(b))
    assert w.btn_swap.text() == "" and not w.btn_swap.icon().isNull() and w.btn_swap.isEnabled()
    assert w.btn_swap.accessibleName()
    w.btn_swap.click()
    assert w.field_a.path() == str(b) and w.field_b.path() == str(a)
    assert w.chk_protect_a.isChecked()          # la protection reste liée au rôle A
    w.close()


def test_window_animates_both_folder_zones(qapp, tmp_path, monkeypatch):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    w = DedupWindow(MODULE)
    w.field_a.set_path(str(a))
    w.field_b.set_path(str(b))
    seen: dict = {}
    monkeypatch.setattr(w, "run_worker", lambda worker, **kwargs: seen.update(kwargs))
    w.on_run()
    assert seen["import_progress"] == [w.field_a.import_progress, w.field_b.import_progress]
    w.close()


def test_window_options_are_grouped_by_category(qapp):
    from PySide6.QtWidgets import QLabel
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    w = DedupWindow(MODULE)
    groups = {label.text(): label.parentWidget() for label in w.findChildren(QLabel, "OptionGroupTitle")}
    assert set(groups) == {"Portée de l'analyse", "Détection", "Traitement"}
    for title, widgets in (
        ("Portée de l'analyse", (w.chk_recursive, w.chk_media, w.chk_hidden)),
        ("Détection", (w.chk_exact, w.chk_similar, w.chk_live, w.chk_modified)),
        ("Traitement", (w.cmb_strategy, w.cmb_mode, w.field_quarantine)),
    ):
        assert all(groups[title].isAncestorOf(widget) for widget in widgets)
    w.close()


def test_start_analysis_keeps_the_page_scroll_at_the_top(qapp, tmp_path, monkeypatch):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    w = DedupWindow(MODULE)
    try:
        w.field_a.set_path(str(tmp_path))
        w.chk_exact.setChecked(True)
        bar = w._page_scroll.verticalScrollBar()
        bar.setRange(0, 100)
        bar.setValue(75)
        monkeypatch.setattr(w, "run_worker", lambda *args, **kwargs: None)
        w.on_run()
        assert bar.value() == 0
    finally:
        w.close()


def test_options_and_results_tabs_open_results_when_analysis_starts(qapp, tmp_path, monkeypatch):
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow

    w = DedupWindow(MODULE)
    try:
        assert [w._subtab_strip.itemText(i) for i in range(2)] == ["Options", "Résultats"]
        assert w._subtab_strip.currentIndex() == 0
        assert w.subtabs.currentIndex() == 0
        assert w.subtabs.widget(0).isAncestorOf(w.chk_exact)
        assert w.subtabs.widget(1).isAncestorOf(w.tree)
        assert not w.subtabs.isAncestorOf(w.action_bar)   # accessible depuis les deux pages

        w.field_a.set_path(str(tmp_path))
        w.chk_exact.setChecked(True)
        monkeypatch.setattr(w, "run_worker", lambda *args, **kwargs: None)
        w.on_run()
        assert w.subtabs.currentIndex() == 1
        assert w._subtab_strip.currentIndex() == 1
    finally:
        w.close()


def test_window_progress_bar_is_below_the_run_actions(qapp):
    """Le retour de l'analyse apparaît sous le bouton, pas au-dessus de la barre d'actions."""
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    w = DedupWindow(MODULE)
    try:
        layout = w.action_bar.layout()
        assert layout.indexOf(w.progress_bar) > layout.indexOf(w.action_layout)
    finally:
        w.close()


def test_long_caption_keeps_the_side_panel_width_and_corrections_position(qapp):
    """Le chemin affiché ne doit ni élargir l'aperçu ni déplacer les corrections."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow

    w = DedupWindow(MODULE)
    try:
        w.resize(1400, 900)
        w.show()
        QTest.qWait(30)
        panel = w.side_dock.widget()
        title = next(label for label in panel.findChildren(QLabel)
                     if label.text() == "Corrections manuelles")
        dock_width = w.side_dock.width()
        title_y = title.mapTo(panel, title.rect().topLeft()).y()
        caption_height = w.caption_a.height()

        for text in ("court", "/un/chemin/très/long/sans/espace/" + "a" * 160 + ".jpg",
                     "ligne 1\nligne 2\nligne 3\nligne 4\nligne 5\nligne 6\nligne 7\nligne 8"):
            w.caption_a.setText(text)
            qapp.processEvents()
            QTest.qWait(10)
            assert w.caption_a.height() == caption_height
            assert w.side_dock.width() == dock_width
            assert title.mapTo(panel, title.rect().topLeft()).y() == title_y
    finally:
        w.close()


def test_window_labels_and_framed_previews(qapp):
    """Libellés demandés, page transparente et aperçus avec leur cadre/lecteur."""
    from azimut.modules.dedup import MODULE
    from azimut.modules.dedup.window import DedupWindow
    w = DedupWindow(MODULE)
    try:
        assert w.run_button.text() == "Démarrer l'analyse"
        assert w.btn_apply.text() == "Appliquer les modifications"
        assert w._content_widget.autoFillBackground() is False
        for preview in (w.preview_a, w.preview_b):
            style = preview.image.label.styleSheet()
            assert "background-color" in style
            assert "border" in style
            assert "transparent" not in style
    finally:
        w.close()


def test_pair_up_does_not_merge_folders_that_differ_only_by_case_on_posix():
    """LOG-012 : « Album » et « album » sont deux dossiers sur un système sensible à la casse (Linux)."""
    import sys
    pairs, _ = livephoto.pair_up([("Album", "IMG_1.HEIC"), ("album", "IMG_1.MOV")])
    assert len(pairs) == (1 if sys.platform == "win32" else 0)
    same, _ = livephoto.pair_up([("Album", "IMG_1.HEIC"), ("Album", "img_1.MOV")])
    assert len(same) == 1                                    # même dossier : la casse du nom reste sans effet
