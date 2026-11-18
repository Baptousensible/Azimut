"""LOG-008 : deux médias dont le nom commence pareil ne doivent pas recevoir le même JSON tronqué.

Google tronque à 46 caractères le nom du JSON ; le titre COMPLET reste écrit dans son contenu. Quand ce titre désigne
exactement un autre média du dossier, le JSON est le sien : l'autre média n'hérite plus de sa date ni de sa position.
Les cas légitimes (photo modifiée, doublon, titre absent, fichier renommé) gardent l'appariement d'origine.
"""
from __future__ import annotations

import json
from pathlib import Path

from azimut.modules.takeout import engine as eng

PREFIX = "PXL_20230101_123456789_longname_abcdefghijk"         # 43 caractères : nom de JSON tronqué par Google
assert len(PREFIX) == 43


def _json(folder: Path, name: str, title: str, ts: int = 1_600_000_000) -> Path:
    path = folder / name
    path.write_text(json.dumps({"title": title, "photoTakenTime": {"timestamp": str(ts)}}), encoding="utf-8")
    return path


def _media(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"x")
    return path


def test_a_truncated_json_goes_to_the_media_it_names_only(tmp_path):
    a, b = _media(tmp_path, PREFIX + "-A.jpg"), _media(tmp_path, PREFIX + "-B.jpg")
    js = _json(tmp_path, PREFIX + ".json", PREFIX + "-A.jpg")
    index = eng.DirIndex(tmp_path)
    assert index.find(a) == js
    assert index.find(b) is None                                       # avant : b recevait aussi la date de a


def test_analyze_marks_the_other_media_without_json(tmp_path):
    _media(tmp_path, PREFIX + "-A.jpg")
    _media(tmp_path, PREFIX + "-B.jpg")
    _json(tmp_path, PREFIX + ".json", PREFIX + "-A.jpg")
    result = eng.analyze(tmp_path)
    states = {i.path.name: i.state for i in result.items}
    assert states[PREFIX + "-A.jpg"] == eng.STATE_READY
    assert states[PREFIX + "-B.jpg"] == eng.STATE_NO_JSON


def test_edited_photo_still_shares_the_json_of_its_original(tmp_path):
    original, edited = _media(tmp_path, PREFIX + "-A.jpg"), _media(tmp_path, PREFIX + "-A-edited.jpg")
    js = _json(tmp_path, PREFIX + ".json", PREFIX + "-A.jpg")
    index = eng.DirIndex(tmp_path)
    assert index.find(original) == js
    assert index.find(edited) == js                                    # cas légitime : « -edited » partage le JSON


def test_json_without_title_keeps_the_original_behaviour(tmp_path):
    a = _media(tmp_path, PREFIX + "-A.jpg")
    js = tmp_path / (PREFIX + ".json")
    js.write_text(json.dumps({"photoTakenTime": {"timestamp": "1600000000"}}), encoding="utf-8")
    assert eng.DirIndex(tmp_path).find(a) == js


def test_title_naming_a_media_that_is_not_there_keeps_the_original_behaviour(tmp_path):
    """Fichier renommé ou déplacé : le titre ne désigne aucun voisin, le préfixe suffit comme avant."""
    renamed = _media(tmp_path, PREFIX + "-renamed.jpg")
    js = _json(tmp_path, PREFIX + ".json", PREFIX + "-A.jpg")
    assert eng.DirIndex(tmp_path).find(renamed) == js


def test_corrupt_json_does_not_break_the_matching(tmp_path):
    a = _media(tmp_path, PREFIX + "-A.jpg")
    js = tmp_path / (PREFIX + ".json")
    js.write_text("{oops", encoding="utf-8")
    assert eng.DirIndex(tmp_path).find(a) == js


def test_names_given_explicitly_work_without_siblings():
    folder = Path("/nonexistent")
    index = eng.DirIndex(folder, names=[PREFIX + ".json"])
    assert index.find(folder / (PREFIX + "-A.jpg")) == folder / (PREFIX + ".json")


def test_json_alone_in_a_folder_counts_as_orphan(tmp_path):
    """LOG-009 : un dossier qui ne contient que des JSON n'était jamais compté dans « JSON sans média »."""
    (tmp_path / "photos").mkdir()
    _media(tmp_path / "photos", "ok.jpg")
    _json(tmp_path / "photos", "ok.jpg.json", "ok.jpg")
    (tmp_path / "ghosts").mkdir()
    _json(tmp_path / "ghosts", "ghost.jpg.json", "ghost.jpg")
    _json(tmp_path / "ghosts", "other.jpg.json", "other.jpg")
    result = eng.analyze(tmp_path)
    assert [i.state for i in result.items] == [eng.STATE_READY]
    assert result.orphan_json == 2
