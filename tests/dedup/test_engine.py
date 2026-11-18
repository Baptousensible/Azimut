"""Dedup : inventaire — un lien physique n'est pas un doublon (même inode, même stockage)."""
from __future__ import annotations

import os
from pathlib import Path

from azimut.modules.dedup.engine import ScanOptions, scan


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_hard_link_in_the_same_folder_is_not_a_duplicate(tmp_path):
    """Deux noms sur le même inode : supprimer l'un ne libère aucun octet, ce n'est pas une copie."""
    a = tmp_path / "A"
    first = write(a / "photo.bin", b"JPEG" * 1000)
    os.link(first, a / "photo_lien.bin")
    res = scan(ScanOptions(folder_a=a))
    assert res.groups == []
    assert res.stats.repeated == 1
    assert res.stats.reclaimable == 0


def test_hard_link_between_a_and_b_is_not_a_duplicate(tmp_path):
    """Sauvegarde par liens physiques (rsync --link-dest) : B n'est pas une copie à supprimer."""
    a, b = tmp_path / "A", tmp_path / "B"
    original = write(a / "photo.bin", b"JPEG" * 1000)
    b.mkdir(parents=True)
    os.link(original, b / "photo.bin")
    res = scan(ScanOptions(folder_a=a, folder_b=b))
    assert res.groups == []
    assert res.stats.repeated == 1
    assert res.stats.reclaimable == 0
