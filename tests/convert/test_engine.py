from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
from pathlib import Path

import pytest

from azimut.core.fsutil import unique_path
from azimut.modules.convert import engine, profiles
from azimut.modules.convert.engine import SourceItem

ALL_ENC = frozenset({"libsvtav1", "libaom-av1", "libx264", "mjpeg", "libwebp",
                     "libmp3lame", "aac", "libopus", "flac"})

ENCODERS_TEXT = """Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D libsvtav1            SVT-AV1(Scalable Video Technology for AV1) encoder (codec av1)
 V....D libaom-av1           libaom AV1 (codec av1)
 VF...D mjpeg                MJPEG (Motion JPEG)
 V.S... libwebp              libwebp WebP image (codec webp)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)
 A....D libopus              libopus Opus (codec opus)
 A....D flac                 FLAC (Free Lossless Audio Codec)
"""

BY_KIND = {k: profiles.default_profile(k) for k in profiles.KINDS}


def value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def build(pid: str, enc=ALL_ENC):
    p = profiles.get_profile(pid)
    tmp = engine.part_path(Path("/out") / ("photo" + p.out_ext))
    return p, tmp, engine.build_command(p, Path("/in/photo.src"), tmp, ffmpeg="ffmpeg", encoders=enc)


# ── build_command par profil ────────────────────────────────────────────────
@pytest.mark.parametrize("pid,crf,ext", [("image_avif_small", "39", ".avif"), ("image_avif_hq", "25", ".avif")])
def test_image_avif(pid, crf, ext):
    p, tmp, cmd = build(pid)
    assert value(cmd, "-c:v") == "libsvtav1" and value(cmd, "-crf") == crf and value(cmd, "-preset") == "4"
    assert value(cmd, "-frames:v") == "1" and "trunc(iw/2)*2" in value(cmd, "-vf")
    assert value(cmd, "-map_metadata") == "0" and "-vn" not in cmd
    assert cmd[-1] == str(tmp) and tmp.name == ".photo.part" + ext and tmp.suffix == ext
    assert cmd[0] == "ffmpeg" and value(cmd, "-progress") == "pipe:1"


def test_image_avif_fallback_libaom():
    enc = ALL_ENC - {"libsvtav1"}
    _, _, cmd = build("image_avif_small", enc)
    assert value(cmd, "-c:v") == "libaom-av1" and value(cmd, "-cpu-used") == "4" and value(cmd, "-crf") == "39"


def test_image_jpeg_and_webp():
    p, tmp, cmd = build("image_jpeg")
    assert value(cmd, "-c:v") == "mjpeg" and value(cmd, "-q:v") == "3" and "-vf" not in cmd
    assert tmp.name == ".photo.part.jpg" and value(cmd, "-map_metadata") == "0"
    p, tmp, cmd = build("image_webp")
    assert value(cmd, "-c:v") == "libwebp" and value(cmd, "-quality") == "80" and tmp.name == ".photo.part.webp"


def test_video_webm():
    p, tmp, cmd = build("video_webm_av1")
    assert value(cmd, "-c:v") == "libsvtav1" and value(cmd, "-crf") == "40" and value(cmd, "-preset") == "4"
    assert value(cmd, "-c:a") == "libopus" and value(cmd, "-b:a") == "128k"
    assert value(cmd, "-map") == "0:V:0" and "0:a?" in cmd and value(cmd, "-map_metadata") == "0"
    assert tmp.name == ".photo.part.webm" and cmd[-1] == str(tmp)


def test_video_mp4():
    p, tmp, cmd = build("video_mp4_h264")
    assert value(cmd, "-c:v") == "libx264" and value(cmd, "-crf") == "23" and value(cmd, "-preset") == "medium"
    assert value(cmd, "-c:a") == "aac" and value(cmd, "-b:a") == "160k"
    assert value(cmd, "-movflags") == "+faststart" and tmp.name == ".photo.part.mp4"


@pytest.mark.parametrize("pid,codec,rate,ext", [
    ("audio_mp3", "libmp3lame", "192k", ".mp3"), ("audio_aac", "aac", "160k", ".m4a"),
    ("audio_opus", "libopus", "128k", ".opus"), ("audio_flac", "flac", None, ".flac")])
def test_audio(pid, codec, rate, ext):
    p, tmp, cmd = build(pid)
    assert "-vn" in cmd and value(cmd, "-c:a") == codec and value(cmd, "-map_metadata") == "0"
    assert (value(cmd, "-b:a") == rate) if rate else ("-b:a" not in cmd)
    assert tmp.name == ".photo.part" + ext


def test_every_profile_has_a_build():
    for p in profiles.PROFILES:
        cmd = engine.build_command(p, Path("a.src"), engine.part_path(Path("o") / ("a" + p.out_ext)), encoders=ALL_ENC)
        assert cmd[-1].endswith(p.out_ext) and "-map_metadata" in cmd


def test_part_path():
    assert engine.part_path(Path("/o/x.avif")) == Path("/o/.x.part.avif")
    assert engine.part_path(Path("/o/a.b.webm")) == Path("/o/.a.b.part.webm")


# ── Chemins de sortie ───────────────────────────────────────────────────────
def test_output_mapping_keeps_tree(tmp_path):
    src = tmp_path / "Photos"
    (src / "a" / "b").mkdir(parents=True)
    (src / "top.png").write_bytes(b"1")
    (src / "a" / "b" / "x.jpg").write_bytes(b"22")
    res = engine.scan_inputs([str(src)], recursive=True)
    jobs = {j.item.path.name: j for j in engine.plan_jobs(res.items, BY_KIND, None, ALL_ENC)}
    out = tmp_path / "Photos_Converted"
    assert jobs["x.jpg"].dest == out / "a" / "b" / "x.avif"
    assert jobs["top.png"].dest == out / "top.avif"
    assert engine.default_output_dir(src) == out


def test_explicit_output_and_isolated_file(tmp_path):
    (tmp_path / "A").mkdir(); (tmp_path / "B").mkdir()
    (tmp_path / "A" / "f.jpg").write_bytes(b"1"); (tmp_path / "B" / "g.jpg").write_bytes(b"1")
    solo = tmp_path / "solo.png"; solo.write_bytes(b"1")
    out = tmp_path / "out"
    res = engine.scan_inputs([str(tmp_path / "A"), str(tmp_path / "B"), str(solo)])
    d = {j.item.path.name: j.dest for j in engine.plan_jobs(res.items, BY_KIND, out, ALL_ENC)}
    assert d == {"f.jpg": out / "A" / "f.avif", "g.jpg": out / "B" / "g.avif", "solo.png": out / "solo.avif"}
    only = engine.scan_inputs([str(solo)]).items
    job = engine.plan_jobs(only, BY_KIND, None, ALL_ENC)[0]
    assert job.dest is None and job.state == engine.STATE_PENDING


def test_collision_unique_naming_and_rerun(tmp_path):
    src = tmp_path / "S"; src.mkdir()
    (src / "a.jpg").write_bytes(b"1"); (src / "a.png").write_bytes(b"2")
    items = engine.scan_inputs([str(src)]).items
    out = tmp_path / "S_Converted"
    jobs = {j.item.path.name: j for j in engine.plan_jobs(items, BY_KIND, None, ALL_ENC)}
    assert jobs["a.jpg"].dest == out / "a.avif" and jobs["a.png"].dest == out / "a__1.avif"
    out.mkdir()
    (out / "a.avif").write_bytes(b"x")
    assert unique_path(out / "a.avif").name == "a__1.avif"          # même schéma que unique_path
    (out / "a__1.avif").write_bytes(b"x")
    again = engine.plan_jobs(items, BY_KIND, None, ALL_ENC)
    assert [j.state for j in again] == [engine.STATE_SKIPPED, engine.STATE_SKIPPED]


def test_source_copy_counts_as_done(tmp_path):
    src = tmp_path / "S"; src.mkdir()
    (src / "top.png").write_bytes(b"12345")
    out = tmp_path / "S_Converted"; out.mkdir()
    (out / "top.png").write_bytes(b"abcde")                          # copie conservée au passage précédent
    items = engine.scan_inputs([str(src)]).items
    assert engine.plan_jobs(items, BY_KIND, None, ALL_ENC)[0].state == engine.STATE_SKIPPED


def test_scan_inputs_options(tmp_path):
    d = tmp_path / "D"; (d / "sub").mkdir(parents=True)
    (d / "a.jpg").write_bytes(b"1"); (d / "sub" / "b.mp3").write_bytes(b"1")
    (d / ".hidden.jpg").write_bytes(b"1"); (d / "notes.txt").write_text("x")
    rec = engine.scan_inputs([str(d)], recursive=True)
    assert sorted(i.path.name for i in rec.items) == ["a.jpg", "b.mp3"]
    assert {i.kind for i in rec.items} == {"image", "audio"}
    assert [i.path.name for i in engine.scan_inputs([str(d)], recursive=False).items] == ["a.jpg"]
    res = engine.scan_inputs([str(d / "notes.txt"), str(d / "a.jpg"), str(d)])
    assert len(res.ignored) == 1 and "notes.txt" in res.ignored[0] and len(res.items) == 2   # dédoublonné


# ── États ───────────────────────────────────────────────────────────────────
def test_missing_encoder_state():
    enc = ALL_ENC - {"libsvtav1", "libaom-av1"}
    it = SourceItem(Path("/x/a.jpg"), None, "image", 10)
    job = engine.plan_jobs([it], BY_KIND, Path("/o"), enc)[0]
    assert job.state == engine.STATE_NO_ENCODER and "libsvtav1 ou libaom-av1" in job.message
    webp = engine.plan_jobs([it], {**BY_KIND, "image": profiles.get_profile("image_webp")}, Path("/o"), enc)[0]
    assert webp.state == engine.STATE_PENDING
    with pytest.raises(engine.EncoderMissingError):
        engine.build_command(BY_KIND["image"], Path("a.jpg"), Path("o.avif"), encoders=enc)


def test_unsupported_raw_and_failure_classification(monkeypatch):
    from azimut.modules.convert import imaging
    monkeypatch.setattr(imaging, "raw_available", lambda: False)
    it = SourceItem(Path("/x/DSC.NEF"), None, "image", 10)
    assert engine.plan_jobs([it], BY_KIND, Path("/o"), ALL_ENC)[0].state == engine.STATE_UNSUPPORTED
    assert engine.classify_failure("Invalid data found when processing input", ".jpg") == engine.STATE_UNSUPPORTED
    assert engine.classify_failure("anything", ".HEIC") == engine.STATE_UNSUPPORTED
    assert engine.classify_failure("Unknown encoder 'libsvtav1'", ".jpg") == engine.STATE_NO_ENCODER
    assert engine.classify_failure("Conversion failed!", ".mp4") == engine.STATE_ERROR


# ── Encodeurs ───────────────────────────────────────────────────────────────
def test_parse_encoders():
    enc = engine.parse_encoders(ENCODERS_TEXT)
    assert {"libsvtav1", "libaom-av1", "libx264", "mjpeg", "libwebp", "libopus", "flac", "aac"} <= enc
    assert "=" not in enc and "Encoders:" not in enc
    assert engine.pick_encoder(profiles.AV1_ENCODERS, enc) == "libsvtav1"
    sans = engine.parse_encoders("\n".join(l for l in ENCODERS_TEXT.splitlines() if "libsvtav1" not in l))
    assert engine.pick_encoder(profiles.AV1_ENCODERS, sans) == "libaom-av1"
    none = engine.parse_encoders("\n".join(l for l in ENCODERS_TEXT.splitlines() if "av1" not in l))
    with pytest.raises(engine.EncoderMissingError):
        engine.pick_encoder(profiles.AV1_ENCODERS, none)
    assert engine.pick_encoder(profiles.AV1_ENCODERS, None) == "libsvtav1"


# ── Progression, durée, anti-grossissement ──────────────────────────────────
def test_progress_parsing():
    assert engine.parse_progress_line("out_time_ms=5000000", 10.0) == 50
    assert engine.parse_progress_line("out_time_us=2500000\n", 10.0) == 25
    assert engine.parse_progress_line("out_time_ms=99000000", 10.0) == 99
    assert engine.parse_progress_line("out_time_ms=N/A", 10.0) is None
    assert engine.parse_progress_line("out_time_ms=-9223372036854775807", 10.0) is None
    assert engine.parse_progress_line("out_time_ms=5000000", None) is None
    assert engine.parse_progress_line("frame=12", 10.0) is None
    assert engine.parse_progress_line("progress=end", None) == 100


def test_duration_and_probe_command():
    assert engine.parse_duration("12.480000\n") == 12.48
    assert engine.parse_duration("N/A\n") is None and engine.parse_duration("") is None
    cmd = engine.build_probe_command("ffprobe", Path("v.mp4"))
    assert cmd[0] == "ffprobe" and "format=duration" in cmd and cmd[-1] == "v.mp4"


def test_growth_guard():
    assert engine.grew(100, 101) and not engine.grew(100, 100) and not engine.grew(100, 40)
    assert not engine.grew(0, 10)
    assert all(p.guard_growth for p in profiles.PROFILES if p.kind in ("image", "video") and not p.video_copy)
    assert not any(p.guard_growth for p in profiles.PROFILES if p.kind == "audio")


# ── Import léger ────────────────────────────────────────────────────────────
def test_module_import_is_light():
    root = Path(__file__).resolve().parents[2]
    code = ("import sys; from azimut.modules.convert import MODULE; "
            "assert MODULE.id == 'convert' and MODULE.requires == ('ffmpeg', 'ffprobe'); "
            "assert 'PySide6.QtWidgets' not in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(root)})
    assert r.returncode == 0, r.stderr
