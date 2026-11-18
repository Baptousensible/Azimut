"""Diagnostics FFmpeg d'Azimut Convert : résumé d'erreur lisible, causes reconnues, reprises de lecture audio.

Logique pure (aucun Qt, aucun ffmpeg). Les causes réelles sont éprouvées de bout en bout dans
`test_audio_robustness.py`.
"""
from __future__ import annotations

from pathlib import Path

from azimut.modules.convert import engine
from azimut.modules.convert import probe as probing

# Sortie réelle (FFmpeg 7) rapportée par un utilisateur : la queue seule coupait même le nom du filtre.
NOKIA_STDERR = (
    "[aost#0:0/libopus @ 0x55a47b2aa700] [enc:libopus @ 0x55a47b2cd640] Could not open encoder before EOF\n"
    "[aost#0:0/libopus @ 0x55a47b2aa700] Task finished with error code: -22 (Invalid argument)\n"
    "[aost#0:0/libopus @ 0x55a47b2aa700] Terminating thread with return code -22 (Invalid argument)\n"
    "[out#0/opus @ 0x55a47b2aa380] Nothing was written into output file, because at least one of its streams "
    "received no packets.\n"
)


def test_digest_keeps_both_ends_of_a_long_stderr():
    lines = ["[aac @ 0x1] premiere cause reelle"] + [f"bruit {i}" for i in range(200)] + ["Conversion failed!"]
    out = engine.stderr_digest("\n".join(lines), limit=300)
    assert "premiere cause reelle" in out                      # le début : la cause (perdu par stderr_tail)
    assert "Conversion failed!" in out                         # la fin : la conclusion
    assert "omise(s)" in out
    assert len(out) <= 340


def test_digest_groups_repeated_lines_even_with_changing_addresses():
    text = "\n".join(f"[aac @ 0x55{i:06x}] channel element 0.0 is not allocated" for i in range(50))
    out = engine.stderr_digest(text)
    assert out.count("channel element 0.0") == 1
    assert "×50" in out
    assert "0x…" in out


def test_digest_handles_empty_and_short_text():
    assert engine.stderr_digest("") == ""
    assert engine.stderr_digest(None) == ""                     # type: ignore[arg-type]
    assert engine.stderr_digest("une ligne") == "une ligne"


def test_digest_of_the_reported_nokia_failure_is_complete():
    out = engine.stderr_digest(NOKIA_STDERR, limit=900)
    assert "Could not open encoder before EOF" in out
    assert "received no packets" in out


def test_failure_hint_recognises_empty_decode_and_damage():
    hint = engine.failure_hint(NOKIA_STDERR)
    assert hint is not None and "aucun échantillon" in hint
    assert "endommagée" in (engine.failure_hint("Decode error rate 1 exceeds maximum 0.666667") or "")
    assert "plein" in (engine.failure_hint("av_write_frame(): No space left on device") or "")
    assert "refusé" in (engine.failure_hint("Permission denied") or "")
    assert engine.failure_hint("Conversion failed!") is None
    assert engine.failure_hint("") is None


def test_no_frame_failure_is_a_plain_error_not_unsupported():
    """Il doit rester « Erreur » pour que les reprises de lecture aient lieu (pas d'arrêt immédiat)."""
    assert engine.classify_failure(NOKIA_STDERR, ".m4a") == engine.STATE_ERROR


def test_audio_attempts_depend_on_container():
    assert probing.audio_attempts(Path("Sonnerie - Nokia.m4a")) == 4
    assert probing.audio_attempts("tone.M4R") == 4
    assert probing.audio_attempts("a.mp3") == 3
    assert probing.audio_attempts("a.flac") == 3
    assert probing.VIDEO_ATTEMPTS == 3


def test_editlist_flag_only_for_mp4_family():
    last = len(probing.READ_STRATEGIES) - 1
    assert "-ignore_editlist" in probing.input_flags(last, "x.m4a")
    for name in ("x.mp3", "x.wav", "x.flac", "x.ogg", "x.opus", "x.wma"):
        flags = probing.input_flags(last, name)
        assert "-ignore_editlist" not in flags, name         # ffmpeg répond « Option not found » ailleurs
        assert "1" not in flags[flags.index("-err_detect") + 2:] if "-err_detect" in flags else True


def test_protocol_whitelist_survives_every_attempt():
    for attempt in range(len(probing.READ_STRATEGIES) + 2):
        for name in ("x.m4a", "x.mp3", "x.mts"):
            flags = probing.input_flags(attempt, name)
            assert flags[:2] == ["-protocol_whitelist", "file"]


def test_first_attempt_is_the_normal_read():
    assert probing.input_flags(0, "x.m4a") == ["-protocol_whitelist", "file"]
