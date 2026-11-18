"""Pistes AAC/M4A dont le conteneur ne donne que le nombre de canaux (disposition « unknown »).

Constat (FFmpeg 6.1) : `-af aformat=channel_layouts=…` échoue sur ces fichiers (« Channel layout change is not
supported ») alors que la même source passe sans ce filtre. Azimut ne tentait qu'une fois l'audio avec le filtre :
le fichier restait en erreur. La commande de reprise (`plain_audio=True`) retire le filtre ; le worker la lance après
un échec qui désigne les canaux. Logique de commande uniquement : aucun Qt requis.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from azimut.modules.convert import engine, profiles

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg et ffprobe requis")

LAYOUT_ERROR = (
    "[aist#0:0/aac @ 0x1] Guessed Channel Layout: 5.1\n"
    "[Parsed_aformat_0 @ 0x2] Channel layout change is not supported\n"
    "[af#0:0 @ 0x3] Error while filtering: Not yet implemented in FFmpeg, patches welcome\n"
)
FFMPEG7_AUDIO_ENCODER = ("[aost#0:0/libopus @ 0x1] [enc:libopus @ 0x2] Could not open encoder before EOF\n"
                         "[aost#0:0/libopus @ 0x1] Task finished with error code: -22 (Invalid argument)\n")
VIDEO_ENCODER = "[vost#0:0/libsvtav1 @ 0x1] [enc:libsvtav1 @ 0x2] Could not open encoder before EOF\n"


# ── Logique pure ─────────────────────────────────────────────────────────────
def test_layout_errors_trigger_the_filterless_retry():
    assert engine.needs_plain_audio(LAYOUT_ERROR)
    assert engine.needs_plain_audio(LAYOUT_ERROR, audio_only=True)


def test_audio_encoder_opening_errors_count_for_audio_only():
    assert engine.needs_plain_audio(FFMPEG7_AUDIO_ENCODER, audio_only=True)
    assert engine.needs_plain_audio(FFMPEG7_AUDIO_ENCODER)              # ligne « aost » : flux audio, même en vidéo


def test_a_video_encoder_failure_does_not_drop_the_audio_filter():
    assert not engine.needs_plain_audio(VIDEO_ENCODER)
    assert not engine.needs_plain_audio("")
    assert not engine.needs_plain_audio("Conversion failed!")
    assert not engine.needs_plain_audio(None)                           # type: ignore[arg-type]


def test_plain_audio_removes_the_channel_filter_only():
    profile = profiles.get_profile("audio_opus")
    normal = engine.build_command(profile, Path("/in/a.m4a"), Path("/out/.a.part.opus"))
    plain = engine.build_command(profile, Path("/in/a.m4a"), Path("/out/.a.part.opus"), plain_audio=True)
    assert "-af" in normal and "aformat=channel_layouts" in normal[normal.index("-af") + 1]
    assert "-af" not in plain
    # tout le reste est identique : débit, encodeur, liste blanche de protocoles, métadonnées
    assert [a for a in normal if a not in ("-af", normal[normal.index("-af") + 1])] == plain


def test_default_command_is_unchanged():
    profile = profiles.get_profile("audio_flac")
    a = engine.build_command(profile, Path("/in/a.wav"), Path("/out/.a.part.flac"))
    b = engine.build_command(profile, Path("/in/a.wav"), Path("/out/.a.part.flac"), plain_audio=True)
    assert a == b                                                       # FLAC n'a pas de filtre : rien à retirer


# ── Bout en bout avec le vrai FFmpeg ─────────────────────────────────────────
def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channels,codec_name:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _unknown_layout_m4a(dest: Path, layout: str) -> None:
    _ff("-f", "lavfi", "-i", f"anoisesrc=d=1:r=44100,aformat=channel_layouts={layout}", "-c:a", "aac", str(dest))
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channel_layout", "-of", "csv=p=0",
                           str(dest)], capture_output=True, text=True).stdout.strip()
    if info != "unknown":
        pytest.skip(f"ce ffmpeg écrit une disposition connue pour {layout} : cas non reproductible")


@needs_ffmpeg
@pytest.mark.parametrize("layout,channels", [("5.1(side)", 6), ("quad(side)", 4), ("hexagonal", 6)])
def test_unknown_layout_fails_with_the_filter_and_converts_without_it(tmp_path, layout, channels):
    src = tmp_path / "in.m4a"
    _unknown_layout_m4a(src, layout)
    profile = profiles.get_profile("audio_opus")
    # ffmpeg 6.1 : le filtre échoue ; un ffmpeg qui l'accepte rend le premier essai inutile mais sans danger
    first = subprocess.run(engine.build_command(profile, src, tmp_path / "first.opus"), capture_output=True, text=True)
    if first.returncode != 0:
        assert engine.needs_plain_audio(first.stderr, audio_only=True), first.stderr
    out = tmp_path / "plain.opus"
    res = subprocess.run(engine.build_command(profile, src, out, plain_audio=True), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    info = _probe(out)
    assert info["streams"][0]["channels"] == channels                   # le surround est conservé, pas écrasé en stéréo
    assert float(info["format"]["duration"]) > 0.8


@needs_ffmpeg
def test_known_layout_still_uses_the_filter_and_converts(tmp_path):
    """Régression : une source normale reste convertie du premier coup, avec le filtre."""
    src = tmp_path / "in.m4a"
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ac", "2", "-c:a", "aac", str(src))
    out = tmp_path / "out.opus"
    res = subprocess.run(engine.build_command(profiles.get_profile("audio_opus"), src, out),
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert _probe(out)["streams"][0]["channels"] == 2
