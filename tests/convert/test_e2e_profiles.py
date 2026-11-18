"""Bout en bout réel : chaque profil de Convert produit un fichier lisible avec le vrai FFmpeg.

Ces tests lancent ffmpeg / ffprobe pour de bon sur de petits échantillons générés à la volée (aucun binaire
commité). Ils sont ignorés quand ffmpeg ou ffprobe manquent, comme les autres tests de bout en bout.
Les profils matériels (NVENC, QSV, VA-API…) et HEIC (Pillow) ne sont pas couverts ici.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from azimut.modules.convert import engine, imaging, profiles
from azimut.modules.convert import probe as probing

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NEEDS_FFMPEG = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg et ffprobe requis")
_HARDWARE = ("nvenc", "vaapi", "qsv", "amf", "videotoolbox")
_EXCLUDED = {"image_heic"}
PROFILE_IDS = [p.id for p in profiles.PROFILES
               if p.id not in _EXCLUDED and not any(h in e for e in p.video_encoders for h in _HARDWARE)]


def _run(cmd: list[str], timeout: float = 240) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _encoders_here() -> frozenset[str]:
    """Encodeurs du ffmpeg local (ensemble vide si ffmpeg manque ou échoue)."""
    if not (FFMPEG and FFPROBE):
        return frozenset()
    try:
        return engine.parse_encoders(_run([FFMPEG, "-hide_banner", "-encoders"]).stdout)
    except (OSError, subprocess.SubprocessError):
        return frozenset()


# LOG-002 : sans libaom-av1, aucun repli n'est possible pour les médias AV1 sous 64 px.
NEEDS_LIBAOM = pytest.mark.skipif("libaom-av1" not in _encoders_here(), reason="libaom-av1 requis")


def _streams(path: Path) -> list[dict]:
    res = _run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,channels",
                "-of", "json", str(path)])
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)["streams"]


@pytest.fixture(scope="module")
def encoders():
    return engine.parse_encoders(_run([FFMPEG, "-hide_banner", "-encoders"]).stdout)


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    """Un PNG, une vidéo H.264 stéréo de dimensions impaires, un WAV et un WebM VP9 à audio mono."""
    from PIL import Image
    base = tmp_path_factory.mktemp("e2e_samples")
    image = base / "src.png"
    Image.effect_noise((320, 240), 40).convert("RGB").save(image)
    video = base / "src.mkv"
    audio = base / "src.wav"
    mono = base / "mono.webm"
    sine = ["-f", "lavfi", "-i", "sine=f=440:d=1"]
    for cmd in (
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=321x241:d=1:r=10", *sine,
         "-c:v", "libx264", "-pix_fmt", "yuv444p", "-c:a", "aac", str(video)],
        [FFMPEG, "-y", "-v", "error", *sine, str(audio)],
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1:r=10", *sine, "-ac", "1",
         "-c:v", "libvpx-vp9", "-c:a", "libopus", str(mono)],
    ):
        res = _run(cmd)
        assert res.returncode == 0, res.stderr
    return {"image": image, "video": video, "audio": audio, "mono": mono}


def _convert(profile, source: Path, tmp: Path, encoders, with_probe: bool) -> Path:
    found = None
    if with_probe:
        found = probing.parse_media_probe(_run([FFPROBE, *engine.build_media_probe_command(FFPROBE, source)[1:]]).stdout)
        assert found is not None
    dest = tmp / f"{profile.id}{profile.out_ext}"
    out = engine.part_path(dest)
    res = _run(engine.build_command(profile, source, out, ffmpeg=FFMPEG, encoders=encoders, probe=found))
    assert res.returncode == 0, res.stderr
    assert out.is_file() and out.stat().st_size > 0
    return out


@NEEDS_FFMPEG
@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_every_profile_produces_a_readable_output(profile_id, samples, encoders, tmp_path):
    profile = profiles.get_profile(profile_id)
    reason = engine.profile_unavailable_reason(profile, encoders)
    if reason:
        pytest.skip(f"{profile_id} : {reason}")
    out = _convert(profile, samples[profile.kind], tmp_path, encoders, with_probe=profile.kind == "video")
    streams = _streams(out)
    video = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    if profile.kind == "image":
        assert len(video) == 1 and not audio
    elif profile.kind == "audio":
        assert audio and not video
    else:
        assert video and audio, "la vidéo et son audio doivent survivre à la conversion"
    if profile.even_dims:
        assert all(s["width"] % 2 == 0 and s["height"] % 2 == 0 for s in video)


def _small_sample(kind: str, tmp: Path) -> Path:
    """Échantillon de 48×48 px : PNG pour l'image, vidéo H.264 stéréo pour la vidéo."""
    if kind == "image":
        from PIL import Image
        image = tmp / "small.png"
        Image.effect_noise((48, 48), 40).convert("RGB").save(image)
        return image
    video = tmp / "small.mkv"
    res = _run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=48x48:d=1:r=10",
                "-f", "lavfi", "-i", "sine=f=440:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(video)])
    assert res.returncode == 0, res.stderr
    return video


@NEEDS_FFMPEG
@NEEDS_LIBAOM
@pytest.mark.parametrize("profile_id", ["image_avif", "video_webm_av1"])
def test_small_av1_media_is_sent_to_libaom(profile_id, encoders, tmp_path):
    """LOG-002 : un média AV1 de 48×48 px est confié à libaom-av1 (SVT-AV1 refuse < 64 px sur FFmpeg 6.x)."""
    profile = profiles.get_profile(profile_id)
    source = _small_sample(profile.kind, tmp_path)
    found = None
    if profile.kind == "video":
        found = probing.parse_media_probe(
            _run([FFPROBE, *engine.build_media_probe_command(FFPROBE, source)[1:]]).stdout)
        assert found is not None and (found.width, found.height) == (48, 48)
        width, height = found.width, found.height
    else:
        size = imaging.image_size(source)
        assert size == (48, 48)
        width, height = size

    chosen = engine.av1_small_media_encoder(profile, encoders, width, height)    # choix fait par les workers
    assert chosen == "libaom-av1", "un média AV1 de 48 px doit être confié à libaom-av1"
    out = engine.part_path(tmp_path / f"small{profile.out_ext}")
    cmd = engine.build_command(replace(profile, video_encoders=(chosen,)), source, out,
                               ffmpeg=FFMPEG, encoders=encoders, probe=found)
    assert cmd[cmd.index("-c:v") + 1] == "libaom-av1"
    res = _run(cmd)
    assert res.returncode == 0, res.stderr
    assert out.is_file() and out.stat().st_size > 0

    streams = _streams(out)
    video = [s for s in streams if s["codec_type"] == "video"]
    assert [s["codec_name"] for s in video] == ["av1"]
    assert (video[0]["width"], video[0]["height"]) == (48, 48)
    if profile.kind == "video":
        assert any(s["codec_type"] == "audio" for s in streams), "l'audio doit survivre à la conversion"


@NEEDS_FFMPEG
def test_webm_mono_audio_is_automatically_stereo(samples, encoders, tmp_path):
    profile = profiles.get_profile("video_webm_av1")
    if engine.profile_unavailable_reason(profile, encoders):
        pytest.skip("libsvtav1 ou libopus absent")
    out = _convert(profile, samples["mono"], tmp_path, encoders, with_probe=True)
    streams = _streams(out)
    assert [s["codec_name"] for s in streams if s["codec_type"] == "video"] == ["av1"]
    assert [s["channels"] for s in streams if s["codec_type"] == "audio"] == [2]            # mono → stéréo
