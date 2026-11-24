"""Réglages de qualité d'Azimut Convert : CRF, preset, qualité, débit audio, threads, parallélisme."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from azimut.modules.convert import advanced, engine, profiles
from azimut.modules.convert.advanced import Overrides, apply_overrides

ALL_ENC = frozenset({"libsvtav1", "libaom-av1", "libx264", "mjpeg", "libwebp",
                     "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_vaapi", "hevc_vaapi",
                     "libmp3lame", "aac", "libopus", "flac", "pcm_s16be", "pcm_s24le"})


@pytest.fixture(autouse=True)
def _dispose_convert_windows():
    """Détruit les fenêtres Convert après chaque test.

    Les widgets se connectent au ThemeManager, un singleton : une fenêtre seulement
    fermée reste retenue et les tests de thème suivants restylent des fenêtres fantômes.
    """
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from azimut.modules.convert.window import ConvertWindow

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if isinstance(widget, ConvertWindow):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def probe_result(encoders, failed=frozenset()):
    """Résultat de détection simulé, sans lancer FFmpeg ni toucher au matériel."""
    from azimut.modules.convert.workers import EncoderProbe
    return EncoderProbe(frozenset(encoders), frozenset(failed))


def cmd_for(profile, enc=ALL_ENC):
    return engine.build_command(profile, Path("/in/a.src"), Path("/out/.a.part" + profile.out_ext),
                                ffmpeg="ffmpeg", encoders=enc)


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_defaults_unchanged_without_overrides():
    p = profiles.get_profile("video_webm_av1")
    assert apply_overrides(p) is p
    cmd = cmd_for(p)
    assert "-svtav1-params" not in cmd and "-threads" not in cmd


def test_custom_crf_and_preset_svtav1():
    p = apply_overrides(profiles.get_profile("video_webm_av1"), Overrides(crf=30, preset="6"))
    cmd = cmd_for(p)
    assert val(cmd, "-crf") == "30" and val(cmd, "-preset") == "6"
    assert advanced.describe(p) == "CRF 30 · preset 6 · 128 kbit/s"
    assert advanced.summarize(p, profiles.get_profile("video_webm_av1")) == "CRF 30, preset 6"


def test_threads_per_encoder():
    av1 = apply_overrides(profiles.get_profile("video_webm_av1"), None, threads=4)
    assert val(cmd_for(av1), "-svtav1-params") == "lp=4"
    assert val(cmd_for(av1, frozenset({"libaom-av1", "libopus"})), "-threads") == "4"
    h264 = apply_overrides(profiles.get_profile("video_mp4_h264"), None, threads=2)
    assert val(cmd_for(h264), "-threads") == "2"
    assert advanced.describe(av1) == advanced.describe(profiles.get_profile("video_webm_av1"))   # threads : pas de qualité


def test_threads_ignored_for_audio():
    p = apply_overrides(profiles.get_profile("audio_mp3"), None, threads=8)
    assert p.threads == 0


def test_values_are_clamped_and_invalid_preset_ignored():
    p = apply_overrides(profiles.get_profile("video_mp4_h264"), Overrides(crf=99, preset="nope"))
    assert p.crf == 51 and p.preset == "medium"
    p = apply_overrides(profiles.get_profile("image_avif"), Overrides(crf=-5))
    assert p.crf == 0
    p = apply_overrides(profiles.get_profile("image_jpeg"), Overrides(quality=1))
    assert p.quality == 2 and val(cmd_for(p), "-q:v") == "2"
    p = apply_overrides(profiles.get_profile("image_webp"), Overrides(quality=95))
    assert val(cmd_for(p), "-quality") == "95"


def test_presets_follow_available_encoder():
    p = profiles.get_profile("video_webm_av1")
    assert advanced.preset_choices(p, ALL_ENC)[-1] == "13"
    assert advanced.preset_choices(p, frozenset({"libaom-av1"}))[-1] == "8"
    assert "ultrafast" in advanced.preset_choices(profiles.get_profile("video_mp4_h264"))
    assert advanced.preset_choices(profiles.get_profile("audio_mp3")) == ()
    assert advanced.preset_choices(profiles.get_profile("video_mp4_h264_nvenc")) == advanced.NVENC_PRESETS
    assert advanced.preset_choices(profiles.get_profile("video_mp4_h265_qsv")) == advanced.QSV_PRESETS
    assert advanced.preset_choices(profiles.get_profile("video_mp4_h264_vaapi")) == ()


def test_overrides_roundtrip_and_tolerance():
    ov = Overrides(crf=33, preset="5")
    assert Overrides.from_dict(ov.to_dict()) == ov
    assert Overrides.from_dict("garbage") == Overrides()
    assert Overrides.from_dict({"crf": "abc", "quality": True}) == Overrides()
    assert Overrides().is_empty()


def test_clamps_and_auto_threads():
    assert advanced.clamp_threads("x") == 0 and advanced.clamp_threads(999) == advanced.MAX_THREADS
    assert advanced.clamp_parallel(0) == 1 and advanced.clamp_parallel(99) == advanced.MAX_PARALLEL
    assert advanced.auto_threads(4, cpu_count=16) == 4
    assert advanced.auto_threads(8, cpu_count=4) == 1


# ── Fenêtre ───────────────────────────────────────────────────────────────────
@pytest.fixture
def window(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    yield w
    w.close()


def test_window_applies_and_persists_overrides(window):
    w = window
    tree = w.codec_tree
    tree.set_selected("video", "video_webm_av1")
    tree.rows("video_webm_av1")["crf"].slider.setValue(                       # droite = meilleure qualité
        tree.rows("video_webm_av1")["crf"]._values.index(28))
    tree.rows("video_webm_av1")["preset"].set_value("8")
    tree.rows("video_webm_av1")["preset"].valueChanged.emit("8")
    tree.slider_threads.slider.setValue(3)
    eff = w._profile("video")
    assert (eff.crf, eff.preset, eff.threads) == (28, "8", 3)
    assert w.prefs.get("advanced")["video_webm_av1"] == {"crf": 28, "preset": "8"}
    # un autre codec n'est pas touché
    assert advanced.apply_overrides(profiles.get_profile("video_mp4_h264")).crf == 23
    # rétablir
    tree.reset_button("video_webm_av1").click()
    assert w._profile("video").crf == 40 and "video_webm_av1" not in w.prefs.get("advanced")
    assert tree.rows("video_webm_av1")["crf"].value() == 40
    assert w._profile("video").threads == 3     # les threads sont globaux


def test_sliders_go_right_for_better_quality(window):
    tree = window.codec_tree
    crf = tree.rows("image_avif")["crf"]
    assert crf.value() == 39 and crf._values[0] == 63 and crf._values[-1] == 0
    jpeg = tree.rows("image_jpeg")["quality"]
    assert jpeg._values[0] == 31 and jpeg._values[-1] == 2 and jpeg.value() == 3
    webp = tree.rows("image_webp")["quality"]
    assert webp._values[0] == 0 and webp._values[-1] == 100 and webp.value() == 80
    heic = tree.rows("image_heic")["quality"]
    assert heic._values[0] == 0 and heic._values[-1] == 100 and heic.value() == 80
    preset = tree.rows("image_avif")["preset"]
    assert preset._values[0] == "13" and preset._values[-1] == "0" and preset.value() == "4"   # droite = lent
    x264 = tree.rows("video_mp4_h264")["preset"]
    assert x264._values[0] == "ultrafast" and x264._values[-1] == "veryslow"


def test_every_codec_gets_the_right_sliders(window):
    tree = window.codec_tree
    names = {p.id: set(tree.rows(p.id)) for p in profiles.PROFILES}
    assert names["image_avif"] == {"crf", "preset"}
    assert names["image_heic"] == {"quality"}
    assert names["image_jpeg"] == names["image_webp"] == {"quality"}
    assert names["image_png"] == names["image_tiff"] == {"preset"}
    assert names["image_jxl"] == {"preset", "quality"}
    assert names["image_jp2"] == names["image_bmp"] == names["image_qoi"] == set()
    assert names["video_webm_av1"] == names["video_mp4_av1"] == names["video_mp4_h264"] == names["video_mp4_h265"] == {"crf", "preset"}
    assert names["video_mkv_h264"] == {"crf", "preset"}
    assert names["video_mp4_h264_nvenc"] == names["video_mp4_h265_nvenc"] == {"crf", "preset"}
    assert names["video_mp4_h264_qsv"] == names["video_mp4_h265_qsv"] == {"crf", "preset"}
    assert names["video_mp4_av1_nvenc"] == names["video_mp4_av1_qsv"] == {"crf", "preset"}
    assert names["video_mp4_h264_vaapi"] == names["video_mp4_h265_vaapi"] == {"crf"}
    assert names["video_mp4_av1_vaapi"] == {"crf"}
    assert names["video_webm_vp9"] == {"crf", "preset"}
    assert names["video_mp4_mpeg4"] == names["video_mpg_mpeg2"] == names["video_ogv_theora"] == {"quality"}
    assert names["video_mov_prores"] == {"preset"}
    assert names["audio_mp3"] == names["audio_aac"] == names["audio_opus"] == {"bitrate"}
    assert names["audio_vorbis"] == names["audio_ac3"] == names["audio_mp2"] == {"bitrate"}
    assert names["audio_flac"] == {"preset"}                      # niveau de compression
    assert names["audio_alac"] == names["audio_wav"] == names["audio_aiff"] == names["audio_pcm24"] == set()
    # sous la liste vidéo, seuls les réglages du codec audio choisi sont dupliqués
    assert set(tree.video_audio_rows("audio_opus")) == {"bitrate"}
    assert set(tree.video_audio_rows("audio_flac")) == {"preset"}


def test_audio_sliders_change_the_effective_profile(window):
    w, tree = window, window.codec_tree
    tree.set_selected("audio", "audio_mp3")
    row = tree.rows("audio_mp3")["bitrate"]
    row.slider.setValue(row._values.index(256))
    assert w._profile("audio").audio_bitrate == "256k"
    assert w.prefs.get("advanced")["audio_mp3"] == {"bitrate": 256}
    tree.set_selected("audio", "audio_flac")
    flac = tree.rows("audio_flac")["preset"]
    flac.slider.setValue(flac._values.index("8"))
    assert w._profile("audio").preset == "8"


def test_choosing_a_codec_persists_and_selects_exclusively(window):
    w, tree = window, window.codec_tree
    tree.choose("image", "image_webp")
    assert tree.selected("image") == "image_webp" and w.prefs.get("profile_image") == "image_webp"
    assert w._profile("image").out_ext == ".webp"
    assert tree.selected("video") == "video_webm_av1"            # les familles sont indépendantes


def test_video_and_audio_codecs_are_chosen_in_two_lists(window):
    w, tree = window, window.codec_tree
    assert tree.selected("video") == "video_webm_av1" and tree.selected("video_audio") == "audio_opus"
    tree.choose("video", "video_mp4_h264")                      # MP4 n'accepte pas Opus : AAC par défaut
    assert tree.selected("video_audio") == "audio_aac"
    assert w._profile("video").audio_encoders == ("aac",)
    tree._audio_combo.setCurrentIndex(tree._audio_combo.findData("audio_mp3"))
    assert w.prefs.get("profile_video_audio") == "audio_mp3"
    assert w._profile("video").audio_encoders == ("libmp3lame",)
    row = tree.video_audio_rows("audio_mp3")["bitrate"]
    row.slider.setValue(row._values.index(256))
    assert w._profile("video").audio_bitrate == "256k"
    assert tree.rows("audio_mp3")["bitrate"].value() == 192      # réglage vidéo indépendant de l'audio seul
    assert w._profile("audio").audio_bitrate == "192k"
    assert w.prefs.get("advanced")[advanced.video_audio_key("audio_mp3")] == {"bitrate": 256}
    tree.choose("video", "video_webm_vp9")                      # MP3 incompatible WebM → Opus
    assert tree.selected("video_audio") == "audio_opus"
    assert w.prefs.get("profile_video_audio") == "audio_opus"
    assert tree._audio_combo.isEnabled()
    assert w._profile("video").audio_encoders == ("libopus",)


def test_audio_choices_are_curated_per_video_codec(window):
    tree = window.codec_tree

    def audio_ids() -> list[str]:
        return [str(tree._audio_combo.itemData(i)) for i in range(tree._audio_combo.count())]

    tree.choose("video", "video_mp4_av1")
    assert audio_ids() == ["audio_aac", "audio_opus", "audio_mp3"]
    tree.choose("video", "video_mp4_mpeg4")
    assert audio_ids() == ["audio_aac", "audio_mp3"]
    tree.choose("video", "video_ogv_theora")                    # Theora : Vorbis, comme décrit dans le profil
    assert audio_ids() == ["audio_vorbis"] and tree.selected("video_audio") == "audio_vorbis"
    tree.choose("video", "video_mov_prores")                    # montage : PCM 24 bits par défaut
    assert audio_ids() == ["audio_pcm24", "audio_wav", "audio_aac", "audio_alac"]
    assert tree.selected("video_audio") == "audio_pcm24"


def test_video_audio_slider_does_not_change_standalone_audio(window):
    w, tree = window, window.codec_tree
    tree.set_selected("audio", "audio_mp3")
    row = tree.rows("audio_mp3")["bitrate"]
    row.slider.setValue(row._values.index(320))
    assert w._profile("audio").audio_bitrate == "320k"
    assert w.prefs.get("advanced")["audio_mp3"] == {"bitrate": 320}
    tree.choose("video", "video_mp4_h264")
    tree._audio_combo.setCurrentIndex(tree._audio_combo.findData("audio_mp3"))
    video_row = tree.video_audio_rows("audio_mp3")["bitrate"]
    video_row.slider.setValue(video_row._values.index(128))
    assert w._profile("video").audio_bitrate == "128k"
    assert tree.rows("audio_mp3")["bitrate"].value() == 320
    assert w._profile("audio").audio_bitrate == "320k"


def test_new_codecs_are_listed(window):
    tree = window.codec_tree

    def ids(kind: str) -> set[str]:
        combo = tree._combos[kind]
        return {str(combo.itemData(i)) for i in range(combo.count())}

    assert {"image_png", "image_jxl", "image_tiff", "image_jp2", "image_bmp", "image_qoi"} <= ids("image")
    assert {"video_mp4_h265", "video_webm_vp9", "video_mp4_mpeg4", "video_mpg_mpeg2",
            "video_ogv_theora", "video_mov_prores"} <= ids("video")
    assert {"audio_vorbis", "audio_alac", "audio_wav", "audio_ac3", "audio_mp2"} <= ids("audio")


def test_window_reload_uses_saved_values(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    crf = w.codec_tree.rows("image_avif")["crf"]
    crf.slider.setValue(crf._values.index(31))
    w.codec_tree.chk_parallel_auto.setChecked(False)              # réglage manuel par famille
    w.codec_tree.parallel_rows["image"].slider.setValue(2)        # 3 = index 2
    w.close()
    w2 = ConvertWindow()
    assert w2._profile("image").crf == 31
    assert w2.codec_tree.parallel_mode() == "manual"
    assert w2.codec_tree.parallel_values()["image"] == 3
    assert w2.codec_tree.rows("image_avif")["crf"].value() == 31
    w2.close()


def test_video_list_marks_gpu_profiles_and_names_detected_hardware(qapp, monkeypatch):
    from azimut.modules.convert import engine
    from azimut.modules.convert.window import ConvertWindow
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: None)
    w = ConvertWindow()
    combo = w.codec_tree._combos["video"]
    gpu_item = combo.itemText(combo.findData("video_mp4_h264_nvenc"))
    cpu_item = combo.itemText(combo.findData("video_mp4_h264"))
    assert "GPU" in gpu_item and "GPU" not in cpu_item
    w._on_encoders(probe_result({"h264_nvenc", "hevc_nvenc", "libx264", "aac", "libopus", "libsvtav1"}))
    text = w.codec_tree.hardware_label.text()
    assert "NVIDIA NVENC" in text and "H.264" in text and "H.265" in text
    w.close()


def test_no_gpu_is_reported_plainly(qapp, monkeypatch):
    from azimut.modules.convert import engine
    from azimut.modules.convert.window import ConvertWindow
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: None)
    w = ConvertWindow()
    w._on_encoders(probe_result({"libx264", "libx265", "aac", "libopus", "libsvtav1"}))
    assert "Aucune accélération" in w.codec_tree.hardware_label.text()
    w.close()


def test_av1_hardware_profiles_use_backend_presets_and_quality_flags(monkeypatch):
    nvenc = profiles.get_profile("video_mp4_av1_nvenc")
    qsv = profiles.get_profile("video_mp4_av1_qsv")
    vaapi = profiles.get_profile("video_mp4_av1_vaapi")
    assert advanced.family(nvenc) == advanced.family(qsv) == advanced.family(vaapi) == "av1"
    assert advanced.preset_choices(nvenc) == advanced.NVENC_PRESETS
    assert advanced.preset_choices(qsv) == advanced.QSV_PRESETS
    assert advanced.preset_choices(vaapi) == ()
    assert advanced.crf_range(nvenc) == (0, 63)
    enc = frozenset({"av1_nvenc", "av1_qsv", "av1_vaapi", "aac"})
    nv = cmd_for(nvenc, enc)
    assert val(nv, "-c:v") == "av1_nvenc" and val(nv, "-rc") == "vbr"
    assert val(nv, "-cq") == "32" and val(nv, "-preset") == "p4" and val(nv, "-tag:v") == "av01"
    qs = cmd_for(qsv, enc)
    assert val(qs, "-c:v") == "av1_qsv" and val(qs, "-global_quality") == "32"
    assert val(qs, "-preset") == "medium" and "qsv=hw" in qs
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: "/dev/dri/renderD128")
    va = cmd_for(vaapi, enc)
    assert val(va, "-c:v") == "av1_vaapi" and val(va, "-qp") == "32"
    assert val(va, "-vaapi_device") == "/dev/dri/renderD128"


def test_cpu_vs_gpu_quality_hint_appears_with_hardware(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    assert not w.codec_tree.quality_hint.isVisibleTo(w)
    w._on_encoders(probe_result({"h264_nvenc", "libx264", "aac"}))
    hint = w.codec_tree.quality_hint
    assert hint.isVisibleTo(w) and "CPU" in hint.text() and "plus gros" in hint.text()
    w._on_encoders(probe_result({"libx264", "aac"}))
    assert not hint.isVisibleTo(w)
    w.close()


def test_probe_failure_reports_unknown_hardware(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    w._on_probe_failed("échec simulé")
    assert "indéterminé" in w.codec_tree.hardware_label.text()
    assert not w.codec_tree.quality_hint.isVisibleTo(w)
    w.close()


def test_gpu_profiles_explain_the_quality_tradeoff(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    tree = w.codec_tree
    gpu = tree._description(profiles.get_profile("video_mp4_av1_nvenc"))
    cpu = tree._description(profiles.get_profile("video_mp4_av1"))
    assert "plus rapide" in gpu and "encodeur CPU" in gpu
    assert "Conversion GPU" not in cpu
    w.close()


def test_encoder_refresh_keeps_the_video_choice(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    tree = w.codec_tree
    encoders = frozenset({"av1_nvenc", "libx264", "aac"})
    w._on_encoders(probe_result(encoders))
    tree.choose("video", "video_mp4_av1_nvenc")
    w._on_encoders(probe_result(encoders))            # même liste : le choix ne bouge pas
    assert tree.selected("video") == "video_mp4_av1_nvenc"
    w._on_encoders(probe_result({"libx264", "aac"}))    # l'encodeur disparaît : repli sur un profil visible
    assert tree.selected("video") != "video_mp4_av1_nvenc"
    w.close()


def test_av1_hardware_profiles_are_listed_only_when_ffmpeg_exposes_them(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    combo = w.codec_tree._combos["video"]
    assert combo.findData("video_mp4_av1_nvenc") == -1
    w._on_encoders(probe_result({"av1_nvenc", "av1_qsv", "libx264", "aac", "libopus"}))
    assert combo.findData("video_mp4_av1_nvenc") >= 0 and combo.findData("video_mp4_av1_qsv") >= 0
    assert combo.findData("video_mp4_av1_vaapi") == -1
    label = combo.itemText(combo.findData("video_mp4_av1_nvenc"))
    assert "GPU" in label and "AV1" in label
    w._on_encoders(probe_result({"libx264", "aac", "libopus"}))
    assert combo.findData("video_mp4_av1_nvenc") == -1
    w.close()


def test_probe_hardware_only_tests_listed_hardware_encoders(qapp, monkeypatch):
    from azimut.core.workers import BaseWorker, ProcessResult
    from azimut.modules.convert import workers

    class FakeWorker(BaseWorker):
        pass

    w = FakeWorker()
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        encoder = cmd[cmd.index("-c:v") + 1]
        return ProcessResult(0 if encoder == "h264_nvenc" else -1, "", "no device")

    monkeypatch.setattr(w, "run_process", fake_run)
    monkeypatch.setattr(w, "emit_log", lambda *_args, **_kwargs: None)
    failed = workers.probe_hardware(w, Path("ffmpeg"), {"h264_nvenc", "hevc_nvenc", "h264_qsv", "libx264"})
    assert failed == frozenset({"hevc_nvenc", "h264_qsv"})
    assert len(calls) == 3 and all(cmd[0] == "ffmpeg" for cmd in calls)


def test_closing_window_cancels_the_pending_encoder_probe(qapp):
    """Une fenêtre fermée ne doit pas lancer sa sonde FFmpeg pendant un autre test."""
    from azimut.modules.convert.window import ConvertWindow

    w = ConvertWindow()
    assert w._probe_timer.isActive()
    w.close()
    assert not w._probe_timer.isActive()


def test_probe_worker_returns_encoders_and_driver_failures(monkeypatch):
    from azimut.modules.convert import workers
    monkeypatch.setattr(workers, "detect_encoders", lambda _w, _f: frozenset({"h264_nvenc"}))
    monkeypatch.setattr(workers, "probe_hardware", lambda _w, _f, _e: frozenset({"h264_nvenc"}))
    result = workers.ProbeWorker(Path("ffmpeg")).do_work()
    assert result == workers.EncoderProbe(frozenset({"h264_nvenc"}), frozenset({"h264_nvenc"}))


def test_driver_failure_marks_video_profiles_unavailable(qapp, monkeypatch):
    from azimut.modules.convert.window import ConvertWindow
    monkeypatch.setattr(engine, "find_vaapi_device", lambda: None)
    w = ConvertWindow()
    w._on_encoders(probe_result({"h264_nvenc", "hevc_nvenc", "libx264", "aac"}, {"hevc_nvenc"}))
    combo = w.codec_tree._combos["video"]
    working = combo.itemText(combo.findData("video_mp4_h264_nvenc"))
    broken = combo.itemText(combo.findData("video_mp4_h265_nvenc"))
    assert "pilote ou GPU ne répond pas" in broken
    assert "pilote ou GPU ne répond pas" not in working
    assert "ne répond pas" in w.codec_tree.hardware_label.text()
    tree = w.codec_tree
    tree.choose("video", "video_mp4_h264_nvenc")
    assert tree.parallel_values()["video"] == 2          # encodeur testé et fonctionnel
    tree.choose("video", "video_mp4_h265_nvenc")
    assert tree.parallel_values()["video"] == 1          # pilote muet : profil indisponible
    w.close()


def test_window_auto_parallel_tracks_hardware_video_profile(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    tree = w.codec_tree
    assert tree.parallel_mode() == "auto"
    tree.choose("video", "video_mp4_h264_nvenc")
    assert tree.parallel_values()["video"] == 2 and tree.parallel_rows["video"].value() == 2
    tree.choose("video", "video_mp4_h264")
    assert tree.parallel_values()["video"] == 1 and tree.parallel_rows["video"].value() == 1
    tree.chk_parallel_auto.setChecked(False)
    tree.choose("video", "video_mp4_h265_nvenc")
    assert tree.parallel_values()["video"] == 1          # en manuel, le choix reste respecté
    w.close()


def test_window_defaults_to_automatic_parallelism(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    assert w.codec_tree.parallel_mode() == "auto"
    assert w.codec_tree.parallel_values()["video"] == 1
    assert not w.codec_tree.parallel_rows["image"].slider.isEnabled()   # valeurs calculées
    w.close()


def test_window_migrates_the_old_single_parallel_setting(qapp):
    from azimut.core.prefs import Preferences
    from azimut.modules.convert.window import ConvertWindow
    Preferences.instance().module("convert").set("parallel", 4)
    w = ConvertWindow()
    assert w.codec_tree.parallel_mode() == "manual"
    assert w.codec_tree.parallel_values() == {"copy": 4, "image": 4, "audio": 4, "video": 4}
    w.close()


def test_window_migrates_legacy_profiles(qapp):
    from azimut.core.prefs import Preferences
    from azimut.modules.convert.window import ConvertWindow
    prefs = Preferences.instance().module("convert")
    prefs.set("profile_image", "image_avif_hq")
    prefs.set("profile_video", "video_webm_max")
    prefs.set("advanced", {"image_avif_hq": {"crf": 20}, "audio_mp3": {"bitrate": 256}})
    w = ConvertWindow()
    assert w.codec_tree.selected("image") == "image_avif" and w.codec_tree.selected("video") == "video_webm_av1"
    assert (w._profile("image").crf, w._profile("image").preset) == (20, "4")      # réglage perso conservé
    assert (w._profile("video").crf, w._profile("video").preset) == (40, "2")      # « max » = preset 2
    assert w._profile("audio").audio_bitrate == "256k"                              # réglage d'un profil inchangé
    assert not any(k in w.prefs.get("advanced") for k in ("image_avif_hq", "video_webm_max"))
    w.close()


def test_mono_repair_has_no_dedicated_option(window):
    tree = window.codec_tree
    assert not hasattr(tree, "chk_repair_mono")
    assert not hasattr(tree, "repair_mono")
    assert window._profile("video").id == "video_webm_av1"


def test_existing_fix_mono_selection_becomes_a_webm_profile(qapp):
    from azimut.core.prefs import Preferences
    from azimut.modules.convert.window import ConvertWindow
    prefs = Preferences.instance().module("convert")
    prefs.set("profile_video", "video_fix_mono")
    prefs.set("repair_mono", True)
    w = ConvertWindow()
    assert w.codec_tree.selected("video") == "video_webm_av1"
    assert w.prefs.get("profile_video") == "video_webm_av1"
    assert w.prefs.get("repair_mono") is False
    assert w._profile("video").id == "video_webm_av1"
    w.close()


def test_window_queue_filter_layout_and_labels(window):
    """Barre d'actions sous la liste, filtre à la Rename et carte « File d'attente » supprimée."""
    from pathlib import Path

    from PySide6.QtWidgets import QLabel

    from azimut.modules.convert.engine import SourceItem
    from azimut.modules.convert.window import FILTERS

    format_card = window.codec_tree.parentWidget()
    assert format_card.title_label is None
    assert format_card.body_layout.indexOf(window.codec_tree) == 0
    assert format_card.body_layout.count() == 1
    assert not any("compression plus poussée" in label.text() for label in format_card.findChildren(QLabel))
    assert window.run_button.text() == "Démarrer la conversion"
    assert [window.cmb_filter.itemText(i) for i in range(window.cmb_filter.count())] == list(FILTERS)
    assert "Autres" not in FILTERS
    layout = window._content_widget.layout()
    assert layout.indexOf(window.action_bar) > layout.indexOf(window.table)
    assert window.btn_out.text() == "Sortie…"
    assert window.action_layout.indexOf(window.btn_out) == 0
    assert window.action_layout.indexOf(window.btn_out) < window.action_layout.indexOf(window.run_button)
    assert window.btn_out.sizeHint().width() <= 140
    assert not window.btn_out.icon().isNull()
    assert "Laisser vide" in window.btn_out.toolTip()
    assert "Laisser vide" not in [label.text() for label in window.findChildren(QLabel)]
    assert window.btn_remove.text() == "Retirer la sélection"
    assert window.btn_clear.text() == "Vider la liste"
    assert not window.btn_remove.isEnabled() and not window.btn_clear.isEnabled()
    assert window.btn_remove.property("kind") == "ghost"
    assert "File d'attente" not in [label.text() for label in window.findChildren(QLabel)]

    window._items = [SourceItem(Path("/tmp/a.jpg"), None, "image", 1_000),
                     SourceItem(Path("/tmp/b.mp4"), None, "video", 2 * 1024 * 1024),
                     SourceItem(Path("/tmp/c.pdf"), None, "other", 3_000)]
    window._set_large_threshold(50)
    window.cmb_filter.setCurrentIndex(1)
    assert window._visible == [0]
    assert window.btn_clear.isEnabled() and window.btn_clear.property("kind") == "secondary"
    assert not window.btn_remove.isEnabled()
    window.table.selectRow(0)
    assert window.btn_remove.isEnabled() and window.btn_remove.property("kind") == "secondary"
    assert window.btn_large.isHidden()
    window.cmb_filter.setCurrentIndex(4)
    assert not window.btn_large.isHidden()
    assert window.btn_large.text() == "≥ 50 Mo"
    assert window._large_actions[2][1].isChecked()
    window._set_large_threshold(1)
    assert window.btn_large.text() == "≥ 1 Mo"
    assert window.prefs.get("large_mb") == 1
    assert window._visible == [1]
    window.cmb_filter.setCurrentIndex(0)
    assert window._visible == [0, 1, 2]
    assert window.btn_large.isHidden()


def test_clear_queue_asks_for_confirmation(window, monkeypatch):
    """« Vider la liste » attend la confirmation ; Annuler laisse la file intacte."""
    from azimut.modules.convert.engine import SourceItem, path_key

    window._items = [SourceItem(Path("/tmp/a.jpg"), None, "image", 1_000)]
    window._keys = {path_key(window._items[0].path)}
    window._render_queue()
    assert window.btn_clear.isEnabled()
    monkeypatch.setattr(window, "confirm_clear_list", lambda: False)
    window.btn_clear.click()
    assert window._items and window._keys
    monkeypatch.setattr(window, "confirm_clear_list", lambda: True)
    window.btn_clear.click()
    assert window._items == [] and window._keys == set() and window._visible == []


def test_run_button_waits_for_queue_and_output(window, tmp_path):
    """« Démarrer la conversion » reste inactif tant que la file ou la sortie manque."""
    from azimut.modules.convert.engine import SourceItem, path_key

    window._bins_ok = True
    window._refresh_run_state()
    assert not window.run_button.isEnabled()
    item = SourceItem(Path("/tmp/a.jpg"), None, "image", 1_000)
    window._items = [item]
    window._keys = {path_key(item.path)}
    window._render_queue()
    assert not window.run_button.isEnabled()          # fichier isolé : dossier de sortie requis
    window._set_output_dir(str(tmp_path))
    assert window.run_button.isEnabled()
    window._items, window._keys = [], set()
    window._render_queue()
    assert not window.run_button.isEnabled()


def test_output_dir_button_sets_and_resets_path(window, monkeypatch, tmp_path):
    out = tmp_path / "Dossier de Sortie"
    out.mkdir()
    monkeypatch.setattr("azimut.modules.convert.window.choose_directory", lambda *args, **kwargs: str(out))
    window._choose_output_dir()
    assert window._out_dir == str(out)
    assert window.prefs.get("output_dir") == str(out)
    assert str(out) in window.btn_out.toolTip()
    # Le libellé est volontairement élidé au milieu (180 px, _refresh_out_button) : la coupure
    # dépend des métriques de police de l'OS. On vérifie donc le préfixe et la fin du nom.
    assert window.btn_out.text().startswith("Sortie : ")
    assert window.btn_out.text().endswith("Sortie")
    window._set_output_dir("")
    assert window._out_dir == ""
    assert window.prefs.get("output_dir") == ""
    assert "dossier par défaut" in window.btn_out.toolTip()
    assert window.btn_out.text() == "Sortie…"


def test_large_threshold_menu_and_labels(window):
    from azimut.modules.convert.window import LARGE_MB_PRESETS, _threshold_label

    assert [mb for mb, _action in window._large_actions] == list(LARGE_MB_PRESETS)
    assert _threshold_label(50) == "50 Mo"
    assert _threshold_label(1024) == "1 Go"
    assert _threshold_label(1536) == "1536 Mo"
    window.cmb_filter.setCurrentIndex(4)
    window._large_actions[3][1].trigger()          # 100 Mo
    assert window._large_mb == 100 and window.btn_large.text() == "≥ 100 Mo"
    window._large_actions[6][1].trigger()          # 1 Go
    assert window.btn_large.text() == "≥ 1 Go"


def test_queue_column_shows_format_and_quality(window):
    from azimut.modules.convert.window import _format_text
    assert _format_text(window._profile("image")) == "AVIF — CRF 39 · preset 4"
    assert _format_text(window._profile("audio")) == "MP3 — 192 kbit/s"


# ── Débit audio, FLAC, résumé, migration ─────────────────────────────────────
def test_audio_bitrate_override_is_clamped():
    mp3 = profiles.get_profile("audio_mp3")
    assert apply_overrides(mp3, Overrides(bitrate=256)).audio_bitrate == "256k"
    assert apply_overrides(mp3, Overrides(bitrate=999)).audio_bitrate == "320k"
    assert apply_overrides(mp3, Overrides(bitrate=1)).audio_bitrate == "32k"
    assert apply_overrides(profiles.get_profile("audio_opus"), Overrides(bitrate=16)).audio_bitrate == "16k"
    # vidéo : le débit concerne la piste audio
    assert apply_overrides(profiles.get_profile("video_mp4_h264"), Overrides(bitrate=96)).audio_bitrate == "96k"
    assert advanced.bitrate_range(profiles.get_profile("audio_flac")) is None


def test_flac_preset_is_the_compression_level():
    flac = profiles.get_profile("audio_flac")
    assert advanced.preset_choices(flac) == advanced.FLAC_LEVELS
    assert apply_overrides(flac, Overrides(preset="12")).preset == "12"
    assert apply_overrides(flac, Overrides(preset="99")).preset == "5"        # invalide : ignoré
    assert advanced.crf_range(flac) is None and advanced.quality_range(flac) is None


def test_describe_and_overrides_diff():
    assert advanced.describe(profiles.get_profile("image_jpeg")) == "qualité 3"
    assert advanced.describe(profiles.get_profile("audio_flac")) == "niveau 5"
    base = profiles.get_profile("video_webm_av1")
    assert advanced.overrides_diff(base, Overrides(crf=40, preset="4", bitrate=128)) == {}
    assert advanced.overrides_diff(base, Overrides(crf=30, preset="4", bitrate=160)) == {"crf": 30, "bitrate": 160}


def test_overrides_bitrate_roundtrip():
    ov = Overrides(crf=33, bitrate=192)
    assert Overrides.from_dict(ov.to_dict()) == ov and not ov.is_empty()
    assert not Overrides(bitrate=96).is_empty()


def test_legacy_profiles_are_gone_and_mapped():
    ids = {p.id for p in profiles.PROFILES}
    assert len(ids) == len(profiles.PROFILES)
    for old, (new, implied) in profiles.LEGACY_PROFILES.items():
        assert old not in ids and new in ids
        assert set(implied) <= {"crf", "preset", "quality", "bitrate"}


def test_migrate_legacy_keeps_choice_and_custom_values():
    sel, store = advanced.migrate_legacy(
        {"image": "image_avif_hq", "video": "video_webm_max", "audio": "audio_mp3"},
        {"image_avif_hq": {"crf": 20}, "image_avif_small": {"crf": 10}, "audio_mp3": {"bitrate": 256}})
    assert sel == {"image": "image_avif", "video": "video_webm_av1", "audio": "audio_mp3"}
    assert store == {"image_avif": {"crf": 20}, "video_webm_av1": {"preset": "2"}, "audio_mp3": {"bitrate": 256}}
    sel, store = advanced.migrate_legacy({"image": "image_avif_hq"}, {})
    assert sel == {"image": "image_avif"} and store == {"image_avif": {"crf": 25}}      # valeur implicite du HQ
    sel, store = advanced.migrate_legacy({"image": "image_webp"}, {"image_webp": {"quality": 90}})
    assert sel == {"image": "image_webp"} and store == {"image_webp": {"quality": 90}}   # rien à migrer


def test_auto_parallel_follows_cores_and_threads():
    assert advanced.auto_parallel("video", 0, cpu_count=32) == 1        # l'encodeur vidéo utilise déjà tout
    assert advanced.auto_parallel("copy", 0, cpu_count=8) == 4
    assert advanced.auto_parallel("copy", 0, cpu_count=2) == 2
    assert advanced.auto_parallel("audio", 0, cpu_count=8) == 4
    assert advanced.auto_parallel("audio", 0, cpu_count=2) == 1
    assert advanced.auto_parallel("image", 0, cpu_count=8) == 4
    assert advanced.auto_parallel("image", 0, cpu_count=4) == 2
    assert advanced.auto_parallel("image", 4, cpu_count=8) == 2         # threads manuels : cœurs partagés
    assert advanced.auto_parallel("image", 8, cpu_count=8) == 1
    assert advanced.auto_parallel("image", 64, cpu_count=8) == 1
    assert advanced.auto_parallel("image", 0, cpu_count=1) == 1
    assert advanced.clamp_parallel(9, "video") == advanced.MAX_PARALLEL_VIDEO
    assert advanced.clamp_parallel(9) == 9                              # hors vidéo : plafond global


def test_auto_parallel_allows_two_videos_only_with_hardware_encoder():
    assert advanced.auto_parallel("video", 0, cpu_count=32) == 1
    assert advanced.auto_parallel("video", 0, cpu_count=32, hardware_video=True) == 2
    assert advanced.parallel_counts("auto", None, threads=0, cpu_count=8)["video"] == 1
    assert advanced.parallel_counts("auto", None, threads=0, cpu_count=8,
                                    hardware_video=True)["video"] == 2
    # Le mode manuel garde le choix de l'utilisateur, même avec un encodeur matériel.
    assert advanced.parallel_counts("manual", {"video": 1}, cpu_count=8,
                                    hardware_video=True)["video"] == 1


def test_parallel_counts_auto_and_manual():
    assert advanced.parallel_counts("auto", None, threads=2, cpu_count=8) == {
        "copy": 4, "image": 4, "audio": 4, "video": 1}
    manual = advanced.parallel_counts("manual", {"image": 3, "video": 99}, cpu_count=8)
    assert manual == {"copy": 1, "image": 3, "audio": 1, "video": advanced.MAX_PARALLEL_VIDEO}


def test_worker_shares_cores_per_family():
    from azimut.modules.convert.workers import ConvertWorker
    profs = {k: profiles.default_profile(k) for k in profiles.KINDS}
    w = ConvertWorker([], profs, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC,
                      parallel_by_kind={"image": 4, "video": 1})
    assert w._parallel_by_kind == {"copy": 1, "image": 4, "audio": 1, "video": 1}
    assert w._profiles["image"].threads == advanced.auto_threads(4)
    assert w._profiles["video"].threads == advanced.default_video_threads()
    assert w._profiles["audio"].threads == 0


def test_worker_shares_cores_when_parallel():
    from azimut.modules.convert.workers import ConvertWorker
    profs = {k: profiles.default_profile(k) for k in profiles.KINDS}
    w = ConvertWorker([], profs, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC, parallel=2)
    assert w._profiles["video"].threads >= 1 and w._profiles["audio"].threads == 0
    w1 = ConvertWorker([], profs, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC)
    # un seul fichier à la fois : « Auto » = threads vidéo bornés (la RAM d'un encodeur AV1 croît avec eux)
    assert w1._profiles["video"].threads == advanced.default_video_threads()
    assert w1._profiles["image"].threads == 0 and w1._profiles["audio"].threads == 0
    fixed = {k: advanced.apply_overrides(p, None, threads=5) for k, p in profs.items()}
    assert ConvertWorker([], fixed, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC)._profiles["video"].threads == 5


def test_default_video_threads_bounds():
    assert advanced.default_video_threads(1) == 2 and advanced.default_video_threads(8) == 4
    assert advanced.default_video_threads(64) == 6
