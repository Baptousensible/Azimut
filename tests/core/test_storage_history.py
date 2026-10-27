"""Tests : storage (LocalJsonStorage, singleton) et history (HistoryStore)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from datetime import datetime

import pytest

from azimut.core import storage as storage_mod
from azimut.core.history import HistoryEntry, HistoryStore
from azimut.core.paths import user_data_dir
from azimut.core.storage import LocalJsonStorage, StorageBackend, get_storage, set_storage


class MemoryStorage(StorageBackend):
    """Backend en mémoire pour tester l'abstraction."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], dict] = {}

    def load(self, collection, key):
        return self.data.get((collection, key))

    def save(self, collection, key, data):
        self.data[(collection, key)] = json.loads(json.dumps(data))

    def delete(self, collection, key):
        self.data.pop((collection, key), None)

    def list_keys(self, collection):
        return sorted(k for c, k in self.data if c == collection)


# ── LocalJsonStorage ─────────────────────────────────────────────────────
def test_abstract_interface():
    with pytest.raises(TypeError):
        StorageBackend()                                              # type: ignore[abstract]


def test_roundtrip_and_layout(tmp_path):
    st = LocalJsonStorage(tmp_path)
    assert st.load("col", "a") is None
    st.save("col", "a", {"nom": "é", "n": [1, 2]})
    assert st.load("col", "a") == {"nom": "é", "n": [1, 2]}
    f = tmp_path / "col" / "a.json"
    assert f.is_file() and "é" in f.read_text(encoding="utf-8")       # UTF-8, non échappé
    st.save("col", "a", {"v": 2})                                     # écrasement
    assert st.load("col", "a") == {"v": 2}
    assert [p.name for p in (tmp_path / "col").iterdir()] == ["a.json"]   # aucun temporaire résiduel


def test_list_keys_and_delete(tmp_path):
    st = LocalJsonStorage(tmp_path)
    assert st.list_keys("vide") == []
    for k in ("b", "a", "c.d"):
        st.save("col", k, {"k": k})
    (tmp_path / "col" / "intrus.txt").write_text("x")
    assert st.list_keys("col") == ["a", "b", "c.d"]
    st.delete("col", "b")
    st.delete("col", "inexistant")                                    # sans erreur
    assert st.list_keys("col") == ["a", "c.d"]
    assert st.load("col", "b") is None


def test_corrupt_or_non_object_returns_none(tmp_path):
    st = LocalJsonStorage(tmp_path)
    (tmp_path / "col").mkdir()
    (tmp_path / "col" / "x.json").write_text("{cassé", encoding="utf-8")
    (tmp_path / "col" / "y.json").write_text("[1, 2]", encoding="utf-8")
    assert st.load("col", "x") is None
    assert st.load("col", "y") is None


def test_save_requires_dict(tmp_path):
    with pytest.raises(TypeError):
        LocalJsonStorage(tmp_path).save("c", "k", [1])                # type: ignore[arg-type]


def test_malicious_names_neutralized(tmp_path):
    root = tmp_path / "root"
    st = LocalJsonStorage(root)
    st.save("col", "../x", {"v": 1})
    assert not (tmp_path / "x.json").exists() and not (root / "x.json").exists()
    assert st.load("col", "../x") == {"v": 1}
    assert st.list_keys("col") == [".._x"]
    st.save("..", "evil", {"v": 2})                                   # collection « .. »
    st.save("a/b\\c", "k", {"v": 3})
    st.save(".", "", {"v": 4})
    written = [p for p in tmp_path.rglob("*.json")]
    assert written and all(root in p.parents for p in written)
    assert st.load("..", "evil") == {"v": 2}


def test_non_serializable_values_do_not_crash(tmp_path):
    st = LocalJsonStorage(tmp_path)
    st.save("c", "k", {"chemin": tmp_path})                           # converti en chaîne
    assert st.load("c", "k") == {"chemin": str(tmp_path)}


# ── singleton ────────────────────────────────────────────────────────────
def test_get_set_storage(monkeypatch):
    monkeypatch.setattr(storage_mod, "_storage", None)                # restauré à la fin du test
    default = get_storage()
    assert isinstance(default, LocalJsonStorage) and get_storage() is default
    assert default.root == user_data_dir() / "storage"
    mem = MemoryStorage()
    set_storage(mem)
    assert get_storage() is mem


# ── HistoryStore ─────────────────────────────────────────────────────────
def _e(module: str = "convert", n: int = 0, **kw) -> HistoryEntry:
    return HistoryEntry(module=module, action="Action", summary=f"résumé {n}", **kw)


def test_history_add_fills_timestamp_and_order():
    hs = HistoryStore(MemoryStorage())
    assert hs.recent() == []
    first = _e(n=1)
    hs.add(first)
    assert first.timestamp                                            # rempli sur l'entrée
    datetime.fromisoformat(first.timestamp)
    fixed = _e(n=2)
    fixed.timestamp = "2020-01-02T03:04:05"
    hs.add(fixed)
    hs.add(_e(n=3, ok=4, errors=1, details={"fichiers": ["a", "b"]}))
    rec = hs.recent()
    assert [r.summary for r in rec] == ["résumé 3", "résumé 2", "résumé 1"]   # plus récent d'abord
    assert rec[1].timestamp == "2020-01-02T03:04:05"
    assert (rec[0].ok, rec[0].errors, rec[0].details) == (4, 1, {"fichiers": ["a", "b"]})
    assert len(hs.recent(limit=2)) == 2 and hs.recent(limit=0) == []


def test_history_document_layout():
    mem = MemoryStorage()
    HistoryStore(mem).add(_e(n=1))
    assert list(mem.data) == [("history", "entries")]
    doc = mem.data[("history", "entries")]
    assert list(doc) == ["entries"] and doc["entries"][0]["module"] == "convert"


def test_history_filter_by_module():
    hs = HistoryStore(MemoryStorage())
    for i, m in enumerate(["convert", "tags", "convert", "dedup"]):
        hs.add(_e(m, i))
    assert [r.summary for r in hs.recent(module="convert")] == ["résumé 2", "résumé 0"]
    assert hs.recent(module="press") == []
    assert len(hs.recent()) == 4


def test_history_truncation():
    hs = HistoryStore(MemoryStorage(), max_entries=3)
    for i in range(5):
        hs.add(_e(n=i))
    assert [r.summary for r in hs.recent()] == ["résumé 4", "résumé 3", "résumé 2"]


def test_history_clear():
    hs = HistoryStore(MemoryStorage())
    for i, m in enumerate(["convert", "tags", "convert"]):
        hs.add(_e(m, i))
    hs.clear("convert")
    assert [r.module for r in hs.recent()] == ["tags"]
    hs.clear()
    assert hs.recent() == []
    hs.clear()                                                        # sur vide : sans erreur


def test_history_tolerates_bad_documents():
    mem = MemoryStorage()
    mem.data[("history", "entries")] = {"entries": [
        "pas un dict", {"action": "sans module"}, {"module": "tags", "ok": "x"},
        {"module": "tags", "action": "Ok", "summary": "bon", "details": "ignoré"}]}
    hs = HistoryStore(mem)
    rec = hs.recent()
    assert len(rec) == 1 and rec[0].summary == "bon" and rec[0].details == {}
    mem.data[("history", "entries")] = {"entries": "cassé"}
    assert hs.recent() == []
    hs.add(_e(n=1))                                                   # repart d'une liste saine
    assert len(hs.recent()) == 1


def test_history_follows_global_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_mod, "_storage", None)
    mem = MemoryStorage()
    set_storage(mem)
    hs = HistoryStore()                                               # sans backend explicite
    hs.add(_e(n=1))
    assert ("history", "entries") in mem.data
    other = LocalJsonStorage(tmp_path)
    set_storage(other)
    assert hs.recent() == []                                          # suit le nouveau singleton
    hs.add(_e(n=2))
    assert (tmp_path / "history" / "entries.json").is_file()


def test_history_with_local_storage(tmp_path):
    hs = HistoryStore(LocalJsonStorage(tmp_path), max_entries=2)
    for i in range(3):
        hs.add(_e(n=i, details={"d": i}))
    assert [r.details["d"] for r in HistoryStore(LocalJsonStorage(tmp_path)).recent()] == [2, 1]
