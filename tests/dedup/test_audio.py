"""Dedup : les fichiers audio sont des médias à part entière (doublons exacts, lecteur intégré)."""
from __future__ import annotations

from pathlib import Path

from azimut.modules.dedup.engine import AUDIO_EXTS, MEDIA_EXTS, SIMILAR_EXTS, ScanOptions, scan


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_audio_extensions_are_media_but_never_compared_visually():
    for ext in (".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac"):
        assert ext in AUDIO_EXTS and ext in MEDIA_EXTS
        assert ext not in SIMILAR_EXTS                    # pas de hachage visuel pour un son


def test_media_only_keeps_duplicate_audio_and_drops_documents(tmp_path):
    a = tmp_path / "A"
    song = b"ID3" + bytes(range(256)) * 40
    write(a / "morceau.mp3", song)
    write(a / "copie du morceau.mp3", song)
    doc = b"%PDF-1.4 " + b"x" * 3000
    write(a / "notes.pdf", doc)
    write(a / "notes (1).pdf", doc)
    res = scan(ScanOptions(folder_a=a, media_only=True))
    names = sorted(i.name for g in res.groups for i in g.items)
    assert names == ["copie du morceau.mp3", "morceau.mp3"]      # le PDF dupliqué est hors périmètre « médias »


def test_without_media_only_everything_is_compared(tmp_path):
    a = tmp_path / "A"
    song = b"OggS" + bytes(range(256)) * 40
    write(a / "x.ogg", song)
    write(a / "y.ogg", song)
    res = scan(ScanOptions(folder_a=a))
    assert len(res.groups) == 1 and len(res.groups[0].items) == 2
