from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
from pathlib import Path

import pytest

from azimut.core.fsutil import unique_path
from azimut.modules.convert import advanced, engine, profiles
from azimut.modules.convert.engine import SourceItem
from azimut.modules.convert.workers import conversion_notification

ALL_ENC = frozenset({"libsvtav1", "libaom-av1", "libx264", "libx265", "libvpx-vp9", "mpeg4",
                     "mpeg2video", "libtheora", "prores_ks", "mjpeg", "libwebp", "png", "libjxl",
                     "libopenjpeg", "tiff", "bmp", "qoi", "h264_nvenc", "hevc_nvenc", "h264_qsv",
                     "hevc_qsv", "h264_vaapi", "hevc_vaapi", "av1_nvenc", "av1_qsv", "av1_vaapi",
                     "libmp3lame", "aac", "libopus", "flac",
                     "libvorbis", "alac", "pcm_s16le", "pcm_s16be", "pcm_s24le", "ac3", "mp2"})

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


def build(pid: str, enc=ALL_ENC, overrides=None):
    p = profiles.get_profile(pid)
    if overrides is not None:
        p = advanced.apply_overrides(p, overrides, 0, enc)
    tmp = engine.part_path(Path("/out") / ("photo" + p.out_ext))
    return p, tmp, engine.build_command(p, Path("/in/photo.src"), tmp, ffmpeg="ffmpeg", encoders=enc)


def test_conversion_notification_is_short_and_informative():
    assert conversion_notification(ok=3, errors=0) == "3 fichier(s) traité(s) · 0 erreur(s)"
    text = conversion_notification(ok=2, errors=1, skipped=2, unsupported=1, missing=1, gain=4096)
    assert text == ("2 fichier(s) traité(s) · 1 erreur(s) · 3 ignoré(s) · "
                    "1 sortie(s) manquante(s)")
    assert "gain" in conversion_notification(ok=3, errors=0, gain=4096)


def test_convert_worker_fills_its_short_notification(qapp):
    from azimut.modules.convert.workers import ConvertWorker

    worker = ConvertWorker([], BY_KIND, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC)
    worker.do_work()
    assert worker.notification_summary == "0 fichier(s) traité(s) · 0 erreur(s)"


# ── build_command par profil ────────────────────────────────────────────────
@pytest.mark.parametrize("overrides,crf,preset", [(None, "39", "4"), (advanced.Overrides(crf=25), "25", "4"),
                                                  (advanced.Overrides(crf=30, preset="2"), "30", "2")])
def test_image_avif(overrides, crf, preset):
    ext = ".avif"
    p, tmp, cmd = build("image_avif", overrides=overrides)
    assert value(cmd, "-c:v") == "libsvtav1" and value(cmd, "-crf") == crf and value(cmd, "-preset") == preset
    assert value(cmd, "-frames:v") == "1" and "trunc(iw/2)*2" in value(cmd, "-vf")
    assert value(cmd, "-map_metadata") == "0" and "-vn" not in cmd
    assert cmd[-1] == str(tmp) and tmp.name.startswith(".photo.") and tmp.name.endswith(".part" + ext)
    assert tmp.suffix == ext
    assert cmd[0] == "ffmpeg" and value(cmd, "-progress") == "pipe:1"


def test_image_avif_fallback_libaom():
    enc = ALL_ENC - {"libsvtav1"}
    _, _, cmd = build("image_avif", enc)
    assert value(cmd, "-c:v") == "libaom-av1" and value(cmd, "-cpu-used") == "4" and value(cmd, "-crf") == "39"


def test_av1_small_media_encoder_prefers_libaom_under_64():
    p = profiles.get_profile("image_avif")
    assert engine.av1_small_media_encoder(p, ALL_ENC, 48, 48) == "libaom-av1"
    assert engine.av1_small_media_encoder(p, ALL_ENC, 63, 128) == "libaom-av1"
    assert engine.av1_small_media_encoder(p, ALL_ENC, 64, 64) is None
    assert engine.av1_small_media_encoder(p, ALL_ENC, 128, 64) is None
    assert engine.av1_small_media_encoder(p, ALL_ENC - {"libaom-av1"}, 48, 48) is None
    assert engine.av1_small_media_encoder(p, None, 48, 48) == "libaom-av1"
    assert engine.av1_small_media_encoder(p, frozenset(), 48, 48) is None
    assert engine.av1_small_media_encoder(profiles.get_profile("image_jpeg"), ALL_ENC, 48, 48) is None
    assert engine.av1_small_media_encoder(p, ALL_ENC, 0, 0) is None
    assert engine.av1_small_media_encoder(profiles.get_profile("video_webm_av1"), ALL_ENC, 32, 32) == "libaom-av1"


def test_image_jpeg_and_webp():
    p, tmp, cmd = build("image_jpeg")
    assert value(cmd, "-c:v") == "mjpeg" and value(cmd, "-q:v") == "3" and "-vf" not in cmd
    assert tmp.name.startswith(".photo.") and tmp.name.endswith(".part.jpg")
    assert value(cmd, "-map_metadata") == "0"
    p, tmp, cmd = build("image_webp")
    assert value(cmd, "-c:v") == "libwebp" and value(cmd, "-quality") == "80"
    assert tmp.name.startswith(".photo.") and tmp.name.endswith(".part.webp")


def test_video_webm():
    p, tmp, cmd = build("video_webm_av1")
    assert value(cmd, "-c:v") == "libsvtav1" and value(cmd, "-crf") == "40" and value(cmd, "-preset") == "4"
    assert value(cmd, "-c:a") == "libopus" and value(cmd, "-b:a") == "128k"
    assert value(cmd, "-map") == "0:V:0" and "0:a?" in cmd and value(cmd, "-map_metadata") == "0"
    assert tmp.name.startswith(".photo.") and tmp.name.endswith(".part.webm")
    assert cmd[-1] == str(tmp)


def test_video_mp4():
    p, tmp, cmd = build("video_mp4_h264")
    assert value(cmd, "-c:v") == "libx264" and value(cmd, "-crf") == "23" and value(cmd, "-preset") == "medium"
    assert value(cmd, "-c:a") == "aac" and value(cmd, "-b:a") == "160k"
    assert value(cmd, "-movflags") == "+faststart"
    assert tmp.name.startswith(".photo.") and tmp.name.endswith(".part.mp4")


def test_new_image_codec_flags():
    _, tmp, cmd = build("image_png")
    assert value(cmd, "-c:v") == "png" and value(cmd, "-compression_level") == "6" and tmp.suffix == ".png"
    _, _, cmd = build("image_jxl")
    assert value(cmd, "-c:v") == "libjxl" and value(cmd, "-distance") == "1" and value(cmd, "-effort") == "7"
    _, _, cmd = build("image_tiff")
    assert value(cmd, "-compression_algo") == "deflate"
    _, _, cmd = build("image_jp2")
    assert value(cmd, "-c:v") == "libopenjpeg" and "-q:v" not in cmd
    _, _, cmd = build("image_bmp")
    assert value(cmd, "-c:v") == "bmp" and "-vf" not in cmd


def test_new_video_codecs_and_their_audio():
    _, tmp, cmd = build("video_mp4_h265")
    assert value(cmd, "-c:v") == "libx265" and value(cmd, "-tag:v") == "hvc1" and tmp.suffix == ".mp4"
    _, _, cmd = build("video_webm_vp9")
    assert value(cmd, "-c:v") == "libvpx-vp9" and value(cmd, "-b:v") == "0"
    assert value(cmd, "-cpu-used") == "2" and "-row-mt" in cmd and value(cmd, "-c:a") == "libopus"
    _, tmp, cmd = build("video_mpg_mpeg2")
    assert value(cmd, "-c:v") == "mpeg2video" and value(cmd, "-q:v") == "4"
    assert value(cmd, "-c:a") == "mp2" and tmp.suffix == ".mpg"
    _, _, cmd = build("video_ogv_theora")
    assert value(cmd, "-c:v") == "libtheora" and value(cmd, "-c:a") == "libvorbis"
    _, tmp, cmd = build("video_mov_prores")
    assert value(cmd, "-c:v") == "prores_ks" and value(cmd, "-profile:v") == "hq" and tmp.suffix == ".mov"


def test_video_mp4_av1_and_mkv():
    _, tmp, cmd = build("video_mp4_av1")
    assert value(cmd, "-c:v") == "libsvtav1" and value(cmd, "-crf") == "40"
    assert value(cmd, "-c:a") == "aac" and value(cmd, "-movflags") == "+faststart"
    assert tmp.suffix == ".mp4"
    _, tmp, cmd = build("video_mkv_h264")
    assert value(cmd, "-c:v") == "libx264" and value(cmd, "-crf") == "23"
    assert value(cmd, "-c:a") == "aac" and tmp.suffix == ".mkv" and "-sn" in cmd


def test_hardware_video_encoders(monkeypatch):
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: "/dev/dri/renderD128")
    _, _, cmd = build("video_mp4_h264_nvenc")
    assert value(cmd, "-c:v") == "h264_nvenc" and value(cmd, "-rc") == "vbr"
    assert value(cmd, "-cq") == "23" and value(cmd, "-preset") == "p4"
    _, _, cmd = build("video_mp4_h265_qsv")
    assert value(cmd, "-c:v") == "hevc_qsv" and value(cmd, "-init_hw_device") == "qsv=hw"
    assert value(cmd, "-global_quality") == "26" and "hwupload" in value(cmd, "-vf")
    _, _, cmd = build("video_mp4_h264_vaapi")
    assert value(cmd, "-c:v") == "h264_vaapi" and value(cmd, "-vaapi_device") == "/dev/dri/renderD128"
    assert value(cmd, "-qp") == "23" and value(cmd, "-vf").endswith(",format=nv12,hwupload")


def test_heic_profile_uses_pillow():
    p = profiles.get_profile("image_heic")
    assert p.out_ext == ".heic" and advanced.quality_range(p) == (0, 100)
    with pytest.raises(ValueError, match="Pillow"):
        engine.build_command(p, Path("a.jpg"), Path("o.heic"), encoders=ALL_ENC)


def test_passthrough_does_not_require_an_encoder():
    heic = SourceItem(Path("/x/a.heic"), None, "image", 10)
    assert engine.initial_state(heic, profiles.get_profile("image_heic"), frozenset()) == engine.STATE_PENDING
    avif = SourceItem(Path("/x/a.avif"), None, "image", 10)
    assert engine.initial_state(avif, profiles.get_profile("image_avif"), frozenset()) == engine.STATE_PENDING


def test_heic_and_vaapi_availability(monkeypatch):
    heic = profiles.get_profile("image_heic")
    monkeypatch.setattr(engine.imaging, "heif_available", lambda: False)
    assert "pillow-heif" in (engine.missing_encoder_message(heic, ALL_ENC) or "")
    item = SourceItem(Path("/x/a.jpg"), None, "image", 10)
    assert engine.initial_state(item, heic, ALL_ENC) == engine.STATE_NO_ENCODER
    vaapi = profiles.get_profile("video_mp4_h264_vaapi")
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: None)
    assert "VA-API" in (engine.missing_encoder_message(vaapi, ALL_ENC) or "")


def test_video_audio_codec_composition():
    video = profiles.get_profile("video_mp4_h264")
    audio = profiles.get_profile("audio_mp3")
    eff = profiles.compose_video(video, audio)
    assert eff.label == "H.264 (MP4) + MP3" and eff.audio_encoders == ("libmp3lame",)
    cmd = engine.build_command(eff, Path("/in/a.mkv"), Path("/out/.a.part.mp4"), encoders=ALL_ENC)
    assert value(cmd, "-c:v") == "libx264" and value(cmd, "-c:a") == "libmp3lame"
    assert value(cmd, "-b:a") == "192k"


def test_video_passthrough_only_with_opus_audio():
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    it = SourceItem(Path("/src/y.webm"), Path("/src"), "video", 10)
    assert engine.plan_jobs([it], by_kind, Path("/out"), ALL_ENC, check_disk=False)[0].passthrough
    vorbis = {**by_kind, "video": profiles.compose_video(profiles.get_profile("video_webm_av1"),
                                                         profiles.get_profile("audio_vorbis"))}
    assert not engine.plan_jobs([it], vorbis, Path("/out"), ALL_ENC, check_disk=False)[0].passthrough


@pytest.mark.parametrize("pid,codec,rate,ext", [
    ("audio_mp3", "libmp3lame", "192k", ".mp3"), ("audio_aac", "aac", "160k", ".m4a"),
    ("audio_opus", "libopus", "128k", ".opus"), ("audio_flac", "flac", None, ".flac"),
    ("audio_vorbis", "libvorbis", "160k", ".ogg"), ("audio_alac", "alac", None, ".m4a"),
    ("audio_wav", "pcm_s16le", None, ".wav"), ("audio_ac3", "ac3", "192k", ".ac3"),
    ("audio_mp2", "mp2", "192k", ".mp2"), ("audio_aiff", "pcm_s16be", None, ".aiff"),
    ("audio_pcm24", "pcm_s24le", None, ".wav")])
def test_audio(pid, codec, rate, ext):
    p, tmp, cmd = build(pid)
    assert "-vn" in cmd and value(cmd, "-c:a") == codec and value(cmd, "-map_metadata") == "0"
    assert (value(cmd, "-b:a") == rate) if rate else ("-b:a" not in cmd)
    assert tmp.name.startswith(".photo.") and tmp.name.endswith(".part" + ext)


def test_audio_bitrate_and_flac_level_follow_the_sliders():
    _, _, cmd = build("audio_mp3", overrides=advanced.Overrides(bitrate=256))
    assert value(cmd, "-b:a") == "256k"
    _, _, cmd = build("audio_flac")
    assert value(cmd, "-compression_level") == "5" and "-b:a" not in cmd
    _, _, cmd = build("audio_flac", overrides=advanced.Overrides(preset="12"))
    assert value(cmd, "-compression_level") == "12"
    _, _, cmd = build("audio_mp3")
    assert "-compression_level" not in cmd                       # réservé à FLAC


def test_every_profile_has_a_build():
    for p in profiles.PROFILES:
        if p.id == "image_heic":
            continue
        cmd = engine.build_command(p, Path("a.src"), engine.part_path(Path("o") / ("a" + p.out_ext)), encoders=ALL_ENC)
        assert cmd[-1].endswith(p.out_ext) and "-map_metadata" in cmd


def test_metadata_copy_args_excludes_geometry_and_orientation():
    source, target = Path("/in/photo.heic"), Path("/out/photo.avif")
    args = engine.metadata_copy_args(source, target)
    # Les séparateurs suivent la plateforme : on compare au chemin rendu par pathlib.
    assert "-TagsFromFile" in args and args[args.index("-TagsFromFile") + 1] == str(source)
    assert "--Orientation" in args and "--ImageWidth" in args and "--ThumbnailImage" in args
    assert args[-1] == str(target)


def test_manifest_detects_changed_profile_and_allows_safe_replacement(tmp_path):
    src = tmp_path / "S"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"source")
    item = engine.scan_inputs([str(src)]).items[0]
    job = engine.plan_jobs([item], BY_KIND, None, ALL_ENC)[0]
    assert job.dest is not None
    job.dest.parent.mkdir(parents=True)
    job.dest.write_bytes(b"old")
    base = job.dest.parent
    rec = {"profile": engine.profile_fingerprint(profiles.get_profile("image_jpeg")),
           "source_size": item.size, "source_mtime": item.mtime, "dest_size": 3}
    changed = engine.plan_jobs([item], BY_KIND, None, ALL_ENC,
                               manifests={base: {job.dest.name: rec}})[0]
    assert changed.state == engine.STATE_PENDING and changed.replace_existing
    assert changed.replace_size == 3
    rec["profile"] = engine.profile_fingerprint(job.profile)
    same = engine.plan_jobs([item], BY_KIND, None, ALL_ENC,
                            manifests={base: {job.dest.name: rec}})[0]
    assert same.state == engine.STATE_SKIPPED


def test_part_path():
    part = engine.part_path(Path("/o/x.avif"))
    assert part.parent == Path("/o") and part.suffix == ".avif"
    assert part.name.startswith(".x.") and part.name.endswith(".part.avif")
    assert engine.part_path(Path("/o/x.avif")) != part          # nom imprévisible (AZ-010)
    assert engine.part_path(Path("/o/a.b.webm")).name.endswith(".part.webm")


def test_every_ffmpeg_command_has_protocol_whitelist():
    """Aucune commande ne doit ouvrir un média sans liste blanche de protocoles (AZ-004)."""
    for p in profiles.PROFILES:
        if p.id == "image_heic":
            continue
        cmd = engine.build_command(p, Path("/in/a.mp4"), Path("/out/a" + p.out_ext), encoders=ALL_ENC)
        assert "-protocol_whitelist" in cmd
        assert cmd.index("-protocol_whitelist") < cmd.index("-i")
        assert cmd[cmd.index("-i") + 1].startswith("file:")
    probe = engine.build_probe_command("ffprobe", Path("/in/a.mp4"))
    assert "-protocol_whitelist" in probe and probe[-1].startswith("file:")


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


# ── Encodeurs matériels ─────────────────────────────────────────────────────
def test_hardware_backends_lists_only_available_encoders(monkeypatch):
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: "/dev/dri/renderD128")
    enc = {"h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_vaapi", "libx264"}
    assert engine.hardware_backends(enc) == [
        ("nvenc", ["H.264", "H.265"]), ("qsv", ["H.264", "H.265"]), ("vaapi", ["H.264"])]
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: None)
    assert [backend for backend, _codecs in engine.hardware_backends(enc)] == ["nvenc", "qsv"]
    assert engine.hardware_backends(frozenset()) == [] and engine.hardware_backends(None) == []


def test_hardware_backends_reports_av1_when_exposed(monkeypatch):
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: None)
    assert engine.hardware_backends({"av1_nvenc"}) == [("nvenc", ["AV1"])]
    assert engine.hardware_backends({"av1_qsv", "av1_vaapi"}) == [("qsv", ["AV1"])]


def test_fingerprint_ignores_display_only_flags():
    from dataclasses import replace
    profile = profiles.get_profile("video_mp4_av1")
    tagged = replace(profile, only_if_encoder=True)
    assert engine.profile_fingerprint(tagged) == engine.profile_fingerprint(profile)
    assert engine.profile_fingerprint(profile) != engine.profile_fingerprint(
        replace(profile, crf=(profile.crf or 0) + 1))


def test_av1_hardware_profiles_only_when_exposed():
    nvenc = profiles.get_profile("video_mp4_av1_nvenc")
    assert nvenc.only_if_encoder and advanced.family(nvenc) == "av1"
    assert "av1_nvenc" in nvenc.video_encoders
    assert not profiles.get_profile("video_mp4_av1").only_if_encoder


def test_hardware_probe_command_really_encodes_a_frame():
    cmd = engine.build_hardware_probe_command("ffmpeg", "h264_nvenc")
    assert cmd[0] == "ffmpeg" and "lavfi" in cmd and "h264_nvenc" in cmd
    assert cmd[-2:] == ["null", "-"] and "hwupload" not in cmd      # aucun fichier, aucune conversion GPU requise
    qsv = engine.build_hardware_probe_command("ffmpeg", "hevc_qsv")
    assert "qsv=hw" in qsv and "format=nv12,hwupload=extra_hw_frames=64" in qsv


def test_driver_failure_marks_profile_unavailable_before_start():
    profile = profiles.get_profile("video_mp4_h265_nvenc")
    encoders = {"h264_nvenc", "hevc_nvenc", "libx264", "aac"}
    assert engine.profile_unavailable_reason(profile, encoders) is None
    assert engine.profile_unavailable_reason(profile, encoders, {"hevc_nvenc"}) == \
        "pilote ou GPU ne répond pas"
    message = engine.missing_encoder_message(profile, encoders, {"hevc_nvenc"}) or ""
    assert "pilote graphique ou le GPU ne répond pas" in message and "hevc_nvenc" in message
    item = SourceItem(Path("film.mkv"), None, "video", 10)
    assert engine.initial_state(item, profile, encoders, {"hevc_nvenc"}) == engine.STATE_NO_ENCODER
    assert engine.initial_state(item, profile, encoders, {"h264_nvenc"}) == engine.STATE_PENDING


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
    assert cmd[0] == "ffprobe" and "format=duration" in cmd
    assert cmd[cmd.index("-i") if "-i" in cmd else -1] in (cmd[-1],), cmd
    assert cmd[-1].startswith("file:") and cmd[-1].endswith("v.mp4")
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"


def test_growth_guard():
    assert engine.grew(100, 101) and not engine.grew(100, 100) and not engine.grew(100, 40)
    assert not engine.grew(0, 10)
    assert all(p.guard_growth for p in profiles.PROFILES if p.kind in ("image", "video"))
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
