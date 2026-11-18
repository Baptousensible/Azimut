"""Extensions reconnues par Convert : une sonnerie .m4r est de l'audio, pas un « autre fichier » copié tel quel."""
from __future__ import annotations

from azimut.modules.convert import engine
from azimut.modules.convert.profiles import ALL_EXTS, AUDIO_EXTS, IMAGE_EXTS, RAW_EXTS, VIDEO_EXTS


def test_families_do_not_overlap():
    families = [IMAGE_EXTS, RAW_EXTS, VIDEO_EXTS, AUDIO_EXTS]
    for i, a in enumerate(families):
        for b in families[i + 1:]:
            assert not (a & b), a & b
    assert ALL_EXTS == IMAGE_EXTS | RAW_EXTS | VIDEO_EXTS | AUDIO_EXTS


def test_every_extension_is_lowercase_with_a_dot():
    for ext in ALL_EXTS:
        assert ext.startswith(".") and ext == ext.lower() and len(ext) > 1, ext


def test_iphone_ringtone_and_audiobook_are_audio():
    for ext in (".m4r", ".m4b", ".oga", ".caf"):
        assert ext in AUDIO_EXTS, ext


def test_scan_classifies_a_ringtone_as_audio(tmp_path):
    (tmp_path / "Sonnerie.m4r").write_bytes(b"x" * 64)
    (tmp_path / "photo.jfif").write_bytes(b"x" * 64)
    (tmp_path / "notes.txt").write_bytes(b"x" * 64)
    scan = engine.scan_inputs([tmp_path], True)
    kinds = {i.path.name: i.kind for i in scan.items}
    assert kinds == {"Sonnerie.m4r": "audio", "photo.jfif": "image"}          # .txt ignoré sans « autres fichiers »
    scan_all = engine.scan_inputs([tmp_path], True, include_others=True)
    assert {i.path.name: i.kind for i in scan_all.items}["notes.txt"] == "other"
