"""AZ-C10 : le JSON d'un média s'apparie quelle que soit la forme Unicode (NFC / NFD) des noms."""
from __future__ import annotations

import unicodedata
from pathlib import Path

from azimut.modules.takeout.engine import DirIndex, name_key

NFC = lambda s: unicodedata.normalize("NFC", s)    # noqa: E731
NFD = lambda s: unicodedata.normalize("NFD", s)    # noqa: E731


def test_the_two_forms_really_differ_byte_for_byte():
    assert NFC("été") != NFD("été") and name_key(NFC("été")) == name_key(NFD("été"))


def test_nfd_media_finds_nfc_json():
    folder = Path("/x")
    index = DirIndex(folder, names=[NFC("été à Paris.jpg.json")])
    assert index.find(folder / NFD("été à Paris.jpg")) == folder / NFC("été à Paris.jpg.json")


def test_nfc_media_finds_nfd_json():
    folder = Path("/x")
    index = DirIndex(folder, names=[NFD("Noël.mp4.json")])
    assert index.find(folder / NFC("Noël.mp4")) == folder / NFD("Noël.mp4.json")


def test_converted_name_and_duplicate_suffix_survive_normalisation():
    folder = Path("/x")
    index = DirIndex(folder, names=[NFC("Réunion.jpg.json"), NFC("Réunion.jpg(1).json")])
    assert index.find(folder / NFD("Réunion_jpg.avif")) == folder / NFC("Réunion.jpg.json")
    assert index.find(folder / NFD("Réunion(1).jpg")) == folder / NFC("Réunion.jpg(1).json")


def test_truncated_title_prefix_matches_across_forms():
    folder = Path("/x")
    long_title = NFC("é" * 30 + "x" * 30 + ".jpg")                 # Google tronque le titre à 46 caractères
    index = DirIndex(folder, names=[long_title[:46] + ".json"])
    assert index.find(folder / NFD(long_title)) == folder / (long_title[:46] + ".json")


def test_unrelated_names_are_still_not_matched():
    folder = Path("/x")
    index = DirIndex(folder, names=[NFC("été.jpg.json")])
    assert index.find(folder / NFC("hiver.jpg")) is None
