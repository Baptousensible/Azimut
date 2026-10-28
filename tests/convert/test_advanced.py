"""Réglages avancés d'Azimut Convert : CRF, preset, qualité, threads, parallélisme."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from azimut.modules.convert import advanced, engine, profiles
from azimut.modules.convert.advanced import Overrides, apply_overrides

ALL_ENC = frozenset({"libsvtav1", "libaom-av1", "libx264", "mjpeg", "libwebp",
                     "libmp3lame", "aac", "libopus", "flac"})


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
    assert p.label.endswith("(personnalisé)")


def test_threads_per_encoder():
    av1 = apply_overrides(profiles.get_profile("video_webm_av1"), None, threads=4)
    assert val(cmd_for(av1), "-svtav1-params") == "lp=4"
    assert val(cmd_for(av1, frozenset({"libaom-av1", "libopus"})), "-threads") == "4"
    h264 = apply_overrides(profiles.get_profile("video_mp4_h264"), None, threads=2)
    assert val(cmd_for(h264), "-threads") == "2"
    assert "(personnalisé)" not in av1.label          # les threads seuls ne changent pas la qualité


def test_threads_ignored_for_audio():
    p = apply_overrides(profiles.get_profile("audio_mp3"), None, threads=8)
    assert p.threads == 0


def test_values_are_clamped_and_invalid_preset_ignored():
    p = apply_overrides(profiles.get_profile("video_mp4_h264"), Overrides(crf=99, preset="nope"))
    assert p.crf == 51 and p.preset == "medium"
    p = apply_overrides(profiles.get_profile("image_avif_small"), Overrides(crf=-5))
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
    w._combos["video"].setCurrentIndex(w._combos["video"].findData("video_webm_av1"))
    row = w._adv_rows["video"]
    row["crf"].setValue(28)
    row["preset"].setCurrentIndex(row["preset"].findData("8"))
    w.spin_threads.setValue(3)
    eff = w._profile("video")
    assert (eff.crf, eff.preset, eff.threads) == (28, "8", 3)
    assert w.prefs.get("advanced")["video_webm_av1"] == {"crf": 28, "preset": "8"}
    # un autre profil n'est pas touché
    assert advanced.apply_overrides(profiles.get_profile("video_mp4_h264")).crf == 23
    # rétablir
    row["reset"].click()
    assert w._profile("video").crf == 40 and "video_webm_av1" not in w.prefs.get("advanced")
    assert w._profile("video").threads == 3     # les threads sont globaux


def test_window_reload_uses_saved_values(qapp):
    from azimut.modules.convert.window import ConvertWindow
    w = ConvertWindow()
    w._combos["image"].setCurrentIndex(w._combos["image"].findData("image_avif_hq"))
    w._adv_rows["image"]["crf"].setValue(31)
    w.spin_parallel.setValue(3)
    w.close()
    w2 = ConvertWindow()
    assert w2._profile("image").crf == 31 and w2.spin_parallel.value() == 3
    assert w2._adv_rows["image"]["crf"].value() == 31
    w2.close()


def test_worker_shares_cores_when_parallel():
    from azimut.modules.convert.workers import ConvertWorker
    profs = {k: profiles.default_profile(k) for k in profiles.KINDS}
    w = ConvertWorker([], profs, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC, parallel=2)
    assert w._profiles["video"].threads >= 1 and w._profiles["audio"].threads == 0
    w1 = ConvertWorker([], profs, None, Path("ffmpeg"), Path("ffprobe"), ALL_ENC)
    assert w1._profiles["video"].threads == 0
