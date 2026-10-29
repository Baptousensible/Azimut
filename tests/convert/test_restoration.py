"""Fonctions restaurées de Convert : sonde, HDR, audio, sous-titres, Pillow, nommage, passthrough, Takeout."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from azimut.modules.convert import advanced, engine, imaging, probe, profiles
from azimut.modules.convert.engine import SourceItem

ALL_ENC = frozenset({"libsvtav1", "libaom-av1", "libx264", "mjpeg", "libwebp",
                     "libmp3lame", "aac", "libopus", "flac"})


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def build(pid, info, **kw):
    p = kw.pop("profile", None) or profiles.get_profile(pid)
    return engine.build_command(p, Path("/in/a.mkv"), Path("/out/.a.part" + p.out_ext), ffmpeg="ffmpeg",
                                encoders=ALL_ENC, probe=info, **kw)


def ffprobe_json(**over):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264", "color_transfer": "bt709"},
               {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "codec_tag_string": "mp4a"}]
    data = {"streams": over.get("streams", streams), "format": {"duration": "12.5"}}
    return json.dumps(data)


# ── Sonde ─────────────────────────────────────────────────────────────────────
def test_parse_probe_audio_subs_hdr():
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "color_transfer": "smpte2084"},
        {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "color_transfer": "bt709"},     # vignette
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 1, "codec_tag_string": "mp4a"},
        {"index": 3, "codec_type": "audio", "codec_name": "", "channels": 2, "codec_tag_string": "apac"},
        {"index": 4, "codec_type": "subtitle", "codec_name": "subrip"},
        {"index": 5, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
    ]
    info = probe.parse_media_probe(ffprobe_json(streams=streams))
    assert info.duration == 12.5 and info.hdr_trc == "smpte2084"
    assert [s["index"] for s in info.audio] == [2] and info.unusable_audio == 1 and info.max_channels == 1
    assert [s["index"] for s in info.subs] == [4]
    assert probe.parse_media_probe("nope") is None and probe.parse_media_probe("[]") is None


def test_hlg_detected_and_filters():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "color_transfer": "arib-std-b67"}]))
    assert info.hdr_trc == "arib-std-b67"
    text = " T.. = Timeline support\n ---\n .S. tonemap           V->V       Conversion\n .SC zscale            V->V       Apply\n"
    names = probe.parse_filters(text)
    assert {"tonemap", "zscale"} <= names
    assert "zscale=tin=arib-std-b67" in probe.hdr_to_sdr_filter("arib-std-b67", names)
    assert probe.hdr_to_sdr_filter("smpte2084", frozenset()).startswith("colorspace=")


def test_input_flags_strategies_and_avchd():
    assert probe.input_flags(0, "a.mp4") == []
    assert "+genpts+igndts" in probe.input_flags(1, "a.mp4")
    assert probe.input_flags(2, "a.mp4")[-2:] == ["-err_detect", "ignore_err"]
    assert probe.input_flags(0, "a.MTS")[:2] == ["-analyzeduration", "100M"]


# ── Commandes ─────────────────────────────────────────────────────────────────
def test_mono_opus_becomes_stereo_and_explicit_maps():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 3, "codec_type": "audio", "codec_name": "aac", "channels": 1, "codec_tag_string": "mp4a"}]))
    cmd = build("video_webm_av1", info)
    assert "0:a?" not in cmd and cmd.count("-map") == 2 and "0:3" in cmd
    assert val(cmd, "-af") == "pan=stereo|c0=c0|c1=c0" and val(cmd, "-c:a") == "libopus"


def test_multichannel_filter_and_opus_copy():
    info = probe.parse_media_probe(ffprobe_json())
    assert val(build("video_webm_av1", info), "-af").startswith("aformat=channel_layouts=7.1|5.1|quad|stereo")
    opus = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "vp9"},
        {"index": 1, "codec_type": "audio", "codec_name": "opus", "channels": 2}]))
    cmd = build("video_webm_av1", opus)
    assert val(cmd, "-c:a") == "copy" and "-af" not in cmd


def test_no_decodable_audio_means_no_audio_map():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "", "channels": 2, "codec_tag_string": "apac"}]))
    cmd = build("video_webm_av1", info)
    assert "-c:a" not in cmd and cmd.count("-map") == 1


def test_first_audio_only_fallback():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2}]))
    assert "0:2" in build("video_webm_av1", info) and "0:2" not in build("video_webm_av1", info, first_audio_only=True)


def test_text_subtitles_kept_per_container_bitmap_dropped():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 4, "codec_type": "subtitle", "codec_name": "subrip"},
        {"index": 5, "codec_type": "subtitle", "codec_name": "dvd_subtitle"}]))
    webm, mp4 = build("video_webm_av1", info), build("video_mp4_h264", info)
    assert val(webm, "-c:s") == "webvtt" and "0:4" in webm and "0:5" not in webm and "-sn" not in webm
    assert val(mp4, "-c:s") == "mov_text"
    assert "-sn" in build("video_webm_av1", probe.parse_media_probe(ffprobe_json()))


def test_hdr_tonemap_and_native():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "color_transfer": "smpte2084"}]))
    tm = build("video_webm_av1", info, hdr_filter="zscale=X,format=yuv420p")
    assert val(tm, "-vf").startswith("scale=trunc(iw/2)*2") and "zscale=X" in val(tm, "-vf")
    assert val(tm, "-color_trc") == "bt709" and "-pix_fmt" not in tm
    native = build("video_webm_av1", info, profile=__import__("dataclasses").replace(
        profiles.get_profile("video_webm_av1"), hdr_mode="H"))
    assert val(native, "-pix_fmt") == "yuv420p10le" and val(native, "-color_trc") == "smpte2084"
    h264 = build("video_mp4_h264", info, profile=__import__("dataclasses").replace(
        profiles.get_profile("video_mp4_h264"), hdr_mode="H"), hdr_filter="zscale=X,format=yuv420p")
    assert val(h264, "-color_trc") == "bt709"          # H.264 : toujours SDR


def test_creation_time_attempts_and_lp_fallback():
    info = probe.parse_media_probe(ffprobe_json())
    p = advanced.apply_overrides(profiles.get_profile("video_webm_av1"), None, threads=3)
    cmd = build("", info, profile=p, creation_time="2020-09-13T12:26:40Z", attempt=2)
    assert val(cmd, "-metadata") == "creation_time=2020-09-13T12:26:40Z" and "-err_detect" in cmd
    assert val(cmd, "-svtav1-params") == "lp=3"
    assert "-svtav1-params" not in build("", info, profile=p, lp_ok=False)


def test_fix_mono_profile_copies_video_and_fixes_only_mono():
    info = probe.parse_media_probe(ffprobe_json(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "av1"},
        {"index": 1, "codec_type": "audio", "codec_name": "opus", "channels": 1},
        {"index": 2, "codec_type": "audio", "codec_name": "opus", "channels": 2}]))
    cmd = build("video_fix_mono", info)
    assert val(cmd, "-c:v") == "copy" and "-vf" not in cmd and "-crf" not in cmd
    assert val(cmd, "-c:a:0") == "libopus" and val(cmd, "-filter:a:0") == "pan=stereo|c0=c0|c1=c0"
    assert val(cmd, "-c:a:1") == "copy"


# ── Noms et plan ──────────────────────────────────────────────────────────────
def item(name, size=10):
    return SourceItem(Path("/src/Album") / name, Path("/src"), "image" if name.endswith(
        (".jpg", ".png", ".avif", ".heic")) else "video", size)


def test_name_ext_avoids_collisions_and_keeps_extension():
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    jobs = engine.plan_jobs([item("a.jpg"), item("a.png")], by_kind, Path("/o"), ALL_ENC, check_disk=False,
                            name_ext=True)
    assert [j.dest.name for j in jobs] == ["a_jpg.avif", "a_png.avif"]
    plain = engine.plan_jobs([item("a.jpg"), item("a.png")], by_kind, Path("/o"), ALL_ENC, check_disk=False)
    assert sorted(j.dest.name for j in plain) == ["a.avif", "a__1.avif"]


def test_passthrough_for_already_final_format():
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    jobs = engine.plan_jobs([item("x.avif"), item("y.webm"), item("z.jpg")], by_kind, Path("/o"), ALL_ENC,
                            check_disk=False, name_ext=True)
    assert [(j.passthrough, j.dest.name) for j in jobs] == [(True, "x.avif"), (True, "y.webm"), (False, "z_jpg.avif")]
    jpeg = {**by_kind, "image": profiles.get_profile("image_jpeg")}
    assert not engine.plan_jobs([item("q.jpg")], jpeg, Path("/o"), ALL_ENC, check_disk=False)[0].passthrough


def test_raw_supported_only_with_rawpy(monkeypatch):
    raw = SourceItem(Path("/x/a.NEF"), None, "image", 1)
    monkeypatch.setattr(imaging, "raw_available", lambda: True)
    assert engine.initial_state(raw, profiles.default_profile("image"), ALL_ENC) == engine.STATE_PENDING
    monkeypatch.setattr(imaging, "raw_available", lambda: False)
    assert engine.initial_state(raw, profiles.default_profile("image"), ALL_ENC) == engine.STATE_UNSUPPORTED


# ── Pillow ────────────────────────────────────────────────────────────────────
PIL = pytest.importorskip("PIL")


def make_jpeg(path: Path, orientation: int, size=(64, 32)):
    from PIL import Image
    im = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = orientation
    im.save(path, exif=exif)


def test_orientation_detected_and_png_is_rotated(tmp_path):
    from PIL import Image
    plain, rotated = tmp_path / "p.jpg", tmp_path / "r.jpg"
    make_jpeg(plain, 1)
    make_jpeg(rotated, 6)
    assert not imaging.needs_pillow(plain) and imaging.needs_pillow(rotated)
    png = imaging.prepare_png(rotated, tmp_path)
    try:
        with Image.open(png) as im:
            assert im.size == (32, 64)                  # tournée de 90° : la largeur devient la hauteur
    finally:
        png.unlink()


def test_multiframe_gif_detected(tmp_path):
    from PIL import Image
    gif = tmp_path / "a.gif"
    frames = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=50)
    single = tmp_path / "b.gif"
    frames[0].save(single)
    assert imaging.is_multiframe(gif) and not imaging.is_multiframe(single)


def test_heic_through_pillow_heif(tmp_path):
    pytest.importorskip("pillow_heif")
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
    src = tmp_path / "a.heic"
    Image.new("RGB", (16, 16), (10, 200, 10)).save(src, format="HEIF")
    assert imaging.needs_pillow(src)
    png = imaging.prepare_png(src, tmp_path)
    assert png.stat().st_size > 0
    png.unlink()


def test_prepare_png_reports_missing_support(tmp_path, monkeypatch):
    monkeypatch.setattr(imaging, "raw_available", lambda: False)
    f = tmp_path / "a.nef"
    f.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="rawpy"):
        imaging.prepare_png(f)


# ── Conversion réelle (ffmpeg, ffprobe, ExifTool) ────────────────────────────
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg et ffprobe requis")


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


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def stream_info(path: Path, entries="stream=codec_type,codec_name,channels,color_transfer,width,height") -> list[dict]:
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
                         capture_output=True, text=True)
    return json.loads(res.stdout)["streams"]


def convert(tmp_path, files, profile_ids, qapp, **opts):
    from azimut.modules.convert.workers import ConvertOptions, ConvertWorker
    src = tmp_path / "in"
    out = tmp_path / "out"
    items = engine.scan_inputs([src]).items
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    for pid in profile_ids:
        p = profiles.get_profile(pid)
        by_kind[p.kind] = p
    worker = ConvertWorker(items, by_kind, out, Path(shutil.which("ffmpeg")), Path(shutil.which("ffprobe")),
                           None, options=ConvertOptions(**opts), exiftool=(
                               Path(shutil.which("exiftool")) if shutil.which("exiftool") else None))
    run_worker(worker)
    return out, worker


@needs_ffmpeg
def test_e2e_video_mono_audio_and_subtitles(tmp_path, qapp):
    src = tmp_path / "in"
    src.mkdir()
    (src / "s.srt").write_text("1\n00:00:00,000 --> 00:00:00,800\nBonjour\n", encoding="utf-8")
    ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3", "-f", "lavfi",
       "-i", "sine=frequency=440:duration=3", "-ac", "1", "-i", str(src / "s.srt"),
       "-c:v", "libx264", "-crf", "6", "-c:a", "aac", "-c:s", "srt", str(src / "clip.mkv"))
    (src / "s.srt").unlink()
    out, worker = convert(tmp_path, ["clip.mkv"], ["video_webm_av1"], qapp, name_ext=True, memory_guard=False)
    dest = out / "clip_mkv.webm"
    assert dest.exists(), worker.summary
    kinds = {s["codec_type"]: s for s in stream_info(dest)}
    assert kinds["audio"]["channels"] == 2 and kinds["audio"]["codec_name"] == "opus"
    assert "subtitle" in kinds                                   # sous-titres texte conservés (WebVTT)
    assert not list(out.glob("*problemes.txt"))


@needs_ffmpeg
def test_e2e_hdr_tonemapped_to_sdr(tmp_path, qapp):
    src = tmp_path / "in"
    src.mkdir()
    ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2", "-pix_fmt", "yuv420p10le", "-c:v", "libx265",
       "-x265-params", "crf=8:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:log-level=error",
       str(src / "hdr.mp4"))
    assert stream_info(src / "hdr.mp4")[0]["color_transfer"] == "smpte2084"
    out, worker = convert(tmp_path, [], ["video_mp4_h264"], qapp, memory_guard=False)
    dest = out / "hdr.mp4"
    assert dest.exists(), worker.summary
    assert stream_info(dest)[0]["color_transfer"] == "bt709"


@needs_ffmpeg
def test_e2e_orientation_gif_and_passthrough(tmp_path, qapp):
    from PIL import Image
    src = tmp_path / "in"
    src.mkdir()
    make_jpeg(src / "rot.jpg", 6, (160, 80))
    frames = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(src / "anim.gif", save_all=True, append_images=frames[1:], duration=50)
    ff("-f", "lavfi", "-i", "testsrc=size=160x96", "-frames:v", "1", "-c:v", "libsvtav1", str(src / "done.avif"))
    out, worker = convert(tmp_path, [], ["image_avif_small"], qapp, name_ext=True, memory_guard=False)
    rot = out / "rot_jpg.avif"
    assert rot.exists(), worker.summary
    info = stream_info(rot)[0]
    assert (info["width"], info["height"]) == (80, 160)           # orientation EXIF appliquée
    assert (out / "anim.gif").read_bytes() == (src / "anim.gif").read_bytes()      # GIF animé copié tel quel
    assert (out / "done.avif").read_bytes() == (src / "done.avif").read_bytes()    # AVIF déjà au bon format
    assert not list(out.glob("*problemes.txt"))


@needs_ffmpeg
def test_e2e_fix_mono_profile(tmp_path, qapp):
    src = tmp_path / "in"
    src.mkdir()
    ff("-f", "lavfi", "-i", "testsrc=size=160x96:rate=5:duration=1", "-f", "lavfi", "-i", "sine=duration=1",
       "-ac", "1", "-c:v", "libvpx", "-c:a", "libopus", str(src / "m.webm"))
    out, worker = convert(tmp_path, [], ["video_fix_mono"], qapp, memory_guard=False)
    dest = out / "m.webm"
    assert dest.exists(), worker.summary
    kinds = {s["codec_type"]: s for s in stream_info(dest)}
    assert kinds["audio"]["channels"] == 2 and kinds["video"]["codec_name"] == "vp8"     # vidéo non recompressée


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("exiftool")), reason="ffmpeg et ExifTool requis")
def test_e2e_takeout_injection_and_missing_json_report(tmp_path, qapp):
    src = tmp_path / "in"
    src.mkdir()
    make_jpeg(src / "a.jpg", 1, (64, 48))
    make_jpeg(src / "b.jpg", 1, (64, 48))
    (src / "a.jpg.supplemental-metadata.json").write_text(json.dumps({
        "title": "a.jpg", "photoTakenTime": {"timestamp": "1600000000"},
        "geoData": {"latitude": 48.8584, "longitude": 2.2945, "altitude": 35.0}}), encoding="utf-8")
    out, worker = convert(tmp_path, [], ["image_jpeg"], qapp, takeout=True, name_ext=True, memory_guard=False)
    res = subprocess.run(["exiftool", "-json", "-n", "-G1", str(out / "a_jpg.jpg")], capture_output=True, text=True)
    tags = json.loads(res.stdout)[0]
    assert tags["ExifIFD:DateTimeOriginal"] == "2020:09:13 12:26:40"
    assert round(tags["GPS:GPSLatitude"], 4) == 48.8584
    assert abs((out / "a_jpg.jpg").stat().st_mtime - 1_600_000_000) < 2
    problems = next(out.glob("*problemes.txt")).read_text(encoding="utf-8")
    assert "SANS_JSON\tb.jpg" in problems and "a.jpg" not in problems.split("\n", 1)[1].replace("SANS_JSON\tb.jpg", "")
