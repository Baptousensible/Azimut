"""Tests de fumée d'Atlas, hors écran, avec de faux modules."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from azimut.core import binaries
from azimut.core.history import HistoryEntry, HistoryStore
from azimut.core.module_spec import ModuleSpec
from azimut.core.storage import LocalJsonStorage, get_storage, set_storage
from azimut.launcher import atlas_window, dashboard


def _spec(module_id: str, order: int = 10, requires: tuple[str, ...] = ()) -> ModuleSpec:
    return ModuleSpec(id=module_id, name=f"Azimut {module_id.upper()}", tagline="Module factice.",
                      icon="🧪", entry=f"fake.{module_id}:Window", requires=requires, order=order)


def _missing(name: str, prefs=None) -> binaries.BinaryInfo:
    return binaries.BinaryInfo(name, None, "missing", "")


def _present(name: str, prefs=None) -> binaries.BinaryInfo:
    return binaries.BinaryInfo(name, Path("/x") / name, "system", "1.0")


class FakeWindow(QMainWindow):
    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.received: list[str] = []

    def open_paths(self, paths):
        self.received.extend(paths)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolation(qapp, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    previous = get_storage()
    set_storage(LocalJsonStorage(tmp_path / "storage"))
    monkeypatch.setattr(binaries, "resolve", _missing)
    monkeypatch.setattr(binaries, "refresh",
                        lambda prefs=None: {n: _missing(n) for n in binaries.KNOWN_BINARIES})
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(dashboard, "user_log_dir", lambda: tmp_path / "logs")
    yield
    set_storage(previous)


@pytest.fixture
def make_atlas():
    created: list[atlas_window.AtlasWindow] = []

    def factory(specs, errors=None, **kwargs):
        window = atlas_window.AtlasWindow(specs, errors or {}, **kwargs)
        created.append(window)
        return window

    yield factory
    for window in created:
        window.close()


def test_two_cards_sorted(make_atlas):
    win = make_atlas([_spec("fb", 20), _spec("fa", 10, ("ffmpeg",))])
    assert list(win.cards) == ["fa", "fb"]
    assert all(c.open_button.isEnabled() for c in win.cards.values())
    assert list(win.cards["fa"].chips) == ["ffmpeg"]
    assert win.windowTitle() == "Azimut — Atlas"


def test_failed_module_card_disabled(make_atlas):
    win = make_atlas([_spec("fa")], {"bad": "ImportError : boom"})
    card = win.cards["bad"]
    assert card.is_failed() and not card.open_button.isEnabled()
    assert "boom" in card.error_label.text()
    assert not win.cards["fa"].is_failed()


def test_warning_banner(make_atlas, monkeypatch):
    win = make_atlas([_spec("fa", 10, ("ffmpeg",))])
    assert not win.warning_banner.isHidden()
    assert "ffmpeg" in win.warning_banner.text()
    monkeypatch.setattr(binaries, "resolve", _present)
    win._refresh_binary_state()
    assert win.warning_banner.isHidden()


def test_open_module_reuses_window_and_forwards_paths(make_atlas, monkeypatch):
    monkeypatch.setattr(atlas_window, "load_window_class", lambda spec: FakeWindow)
    win = make_atlas([_spec("fa")])
    first = win.open_module("fa", ["a", "b"])
    assert isinstance(first, FakeWindow) and first.received == ["a", "b"]
    assert win.open_module("fa") is first
    win.cards["fa"].pathsDropped.emit("fa", ["c"])
    assert first.received == ["a", "b", "c"]
    win._on_module_closed("fa")
    assert "fa" not in win._windows


def test_open_module_failure_disables_card(make_atlas, monkeypatch):
    def boom(spec):
        raise ImportError("introuvable")

    monkeypatch.setattr(atlas_window, "load_window_class", boom)
    monkeypatch.setattr(atlas_window, "QMessageBox", types.SimpleNamespace(warning=lambda *a, **k: None))
    win = make_atlas([_spec("fa")])
    assert win.open_module("fa") is None
    assert win.cards["fa"].is_failed() and not win.cards["fa"].open_button.isEnabled()
    assert "introuvable" in win.cards["fa"].error_label.text()


def test_history_tab_filter_and_clear(make_atlas, monkeypatch, tmp_path):
    store = HistoryStore(LocalJsonStorage(tmp_path / "hist"))
    store.add(HistoryEntry(module="fa", action="Test", summary="un", ok=2))
    store.add(HistoryEntry(module="fb", action="Test", summary="deux", ok=1, errors=1))
    win = make_atlas([_spec("fa"), _spec("fb", 20)], history_store=store)
    tab = win.dashboard.history_tab
    assert len(tab.table.data_model.rows()) == 2
    tab.filter_box.setCurrentIndex(tab.filter_box.findData("fa"))
    assert len(tab.table.data_model.rows()) == 1
    yes = QMessageBox.StandardButton.Yes
    monkeypatch.setattr(dashboard, "QMessageBox", types.SimpleNamespace(
        question=lambda *a, **k: yes, StandardButton=QMessageBox.StandardButton))
    tab.filter_box.setCurrentIndex(0)
    tab._on_clear()
    assert store.recent() == [] and tab.table.data_model.rows() == []


def test_tail_lines_and_log_tab(make_atlas, tmp_path):
    log_file = tmp_path / "logs" / "azimut.log"
    log_file.write_text("\n".join(f"ligne {i}" for i in range(600)), encoding="utf-8")
    lines = dashboard.tail_lines(log_file)
    assert len(lines) == 500 and lines[-1] == "ligne 599"
    assert dashboard.tail_lines(tmp_path / "absent.log") == []
    win = make_atlas([_spec("fa")])
    text = win.dashboard.log_tab.view.toPlainText()
    assert "ligne 599" in text and "ligne 0\n" not in text


def test_binaries_tab_rows(make_atlas):
    win = make_atlas([_spec("fa")])
    rows = win.dashboard.binaries_tab.table.data_model.rows()
    assert [r["name"] for r in rows] == list(binaries.KNOWN_BINARIES)
    assert all(r["_tag"] == "err" for r in rows)
