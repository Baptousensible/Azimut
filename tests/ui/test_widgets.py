from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re

import pytest
from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from azimut.core.binaries import BinaryInfo
from azimut.core.prefs import Preferences
from azimut.ui import widgets
from azimut.ui.theme import ThemeManager, token
from azimut.ui.widgets import (
    Card,
    Column,
    DataTable,
    DropZone,
    ImagePreview,
    LogPanel,
    PathField,
    ProgressPanel,
    RowTableModel,
    StatusChip,
    load_preview,
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def manager(qt_app, tmp_path, monkeypatch):
    """ThemeManager isolé (préférences temporaires) pour tous les tests du module."""
    tm = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", tm)
    return tm


def _mime(*paths) -> QMimeData:
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return m


# ── StatusChip / Card ────────────────────────────────────────────────────────
def test_status_chip_states():
    chip = StatusChip("Prêt", "ok")
    assert chip.text() == "Prêt" and chip.property("level") == "ok"
    chip.set_state("Erreur", "err")
    assert chip.text() == "Erreur" and chip.property("level") == "err"
    chip.set_state("?", "inconnu")
    assert chip.property("level") == "neutre"


def test_card_title_and_body():
    card = Card("Options")
    assert card.title_label is not None and card.title_label.text() == "Options"
    card.body_layout.addWidget(QLabel("contenu"))
    assert card.body_layout.count() == 1
    assert Card().title_label is None


# ── PathField ────────────────────────────────────────────────────────────────
def test_path_field_set_path_emits_once():
    field = PathField("Dossier")
    got: list[str] = []
    field.pathChanged.connect(got.append)
    field.set_path("/tmp/x")
    field.set_path("/tmp/x")
    assert got == ["/tmp/x"] and field.path() == "/tmp/x"


def test_path_field_editing_finished_emits():
    field = PathField("Dossier")
    got: list[str] = []
    field.pathChanged.connect(got.append)
    field.edit.setText("/tmp/y")
    field.edit.editingFinished.emit()
    assert got == ["/tmp/y"]


def test_path_field_drop_dir_mode(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    field = PathField("Dossier", mode="dir")
    assert field.handle_drop(_mime(tmp_path)) is True
    assert field.path() == os.path.abspath(str(tmp_path))
    assert field.handle_drop(_mime(f)) is True               # un fichier → son dossier
    assert field.path() == os.path.abspath(str(tmp_path))


def test_path_field_drop_file_mode(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    field = PathField("Fichier", mode="file")
    assert field.handle_drop(_mime(tmp_path)) is False        # dossier refusé
    assert field.handle_drop(_mime(f)) is True
    assert field.path() == os.path.abspath(str(f))


def test_path_field_invalid_mode():
    with pytest.raises(ValueError):
        PathField("x", mode="autre")


# ── DropZone ─────────────────────────────────────────────────────────────────
def test_drop_zone_emits_files_and_dirs(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    d = tmp_path / "sub"
    d.mkdir()
    zone = DropZone("Déposez ici")
    got: list[list[str]] = []
    zone.pathsDropped.connect(got.append)
    out = zone.handle_drop(_mime(f, d, tmp_path / "absent.png"))
    assert out == [os.path.abspath(str(f)), os.path.abspath(str(d))]
    assert got == [out]


def test_drop_zone_options_and_extensions(tmp_path):
    png, txt = tmp_path / "a.PNG", tmp_path / "b.txt"
    png.write_bytes(b"x")
    txt.write_bytes(b"x")
    d = tmp_path / "sub"
    d.mkdir()
    only_files = DropZone("x", accept_dirs=False)
    assert only_files.filter_paths([str(png), str(d)]) == [str(png)]
    only_dirs = DropZone("x", accept_files=False)
    assert only_dirs.filter_paths([str(png), str(d)]) == [str(d)]
    by_ext = DropZone("x", extensions=["png"])                # extension sans point acceptée
    assert by_ext.filter_paths([str(png), str(txt), str(d)]) == [str(png), str(d)]
    assert DropZone("x", extensions=[".png"], accept_dirs=False).filter_paths([str(d)]) == []


def test_drop_zone_nothing_acceptable_emits_nothing(tmp_path):
    zone = DropZone("x")
    got: list = []
    zone.pathsDropped.connect(got.append)
    assert zone.handle_drop(_mime(tmp_path / "absent")) == []
    assert zone.handle_drop(QMimeData()) == []
    assert got == []


def test_drop_zone_real_drag_events(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    zone = DropZone("x")
    got: list = []
    zone.pathsDropped.connect(got.append)
    mime = _mime(f)
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    zone.dragEnterEvent(enter)
    assert enter.isAccepted() and zone.property("dragOver") is True
    zone.dragLeaveEvent(QDragLeaveEvent())
    assert zone.property("dragOver") is False
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    zone.dropEvent(drop)
    assert got == [[os.path.abspath(str(f))]]


def test_drop_zone_set_text():
    zone = DropZone("avant")
    zone.set_text("après")
    assert zone._label.text() == "après"


# ── ProgressPanel ────────────────────────────────────────────────────────────
def test_progress_panel():
    p = ProgressPanel()
    p.set_progress(3, 10, "photo.jpg")
    assert p.count_label.text() == "3 / 10 (30 %)"
    assert (p.bar.maximum(), p.bar.value()) == (10, 3)
    assert p.file_label.text() == "photo.jpg"
    p.set_status("En cours")
    assert p.status_label.text() == "En cours"
    p.set_progress(50, 10)                                    # dépassement borné
    assert p.count_label.text() == "10 / 10 (100 %)"
    p.set_progress(0, 0)                                      # total inconnu : sans erreur
    assert p.count_label.text() == "0 / 0 (0 %)"
    p.reset()
    assert p.bar.value() == 0 and p.file_label.text() == "" and p.status_label.text() == ""


# ── LogPanel ─────────────────────────────────────────────────────────────────
def _color_at(log: LogPanel, pos: int) -> str:
    cursor = QTextCursor(log.document())
    cursor.setPosition(pos)
    return cursor.charFormat().foreground().color().name()


def test_log_panel_lines_timestamp_and_levels():
    log = LogPanel()
    assert log.isReadOnly() and log.maximumBlockCount() == 5000
    log.append_log("INFO", "début")
    log.append_log("ERR", "boom")
    log.append_log("???", "niveau inconnu")
    lines = log.toPlainText().splitlines()
    assert len(lines) == 3
    assert all(re.match(r"\[\d\d:\d\d:\d\d\] ", ln) for ln in lines)
    assert "boom" in lines[1] and "niveau inconnu" in lines[2]
    assert _color_at(log, 1) == QColor(token("info_txt")).name()
    second = log.document().findBlockByNumber(1).position() + 1
    assert _color_at(log, second) == QColor(token("err_txt")).name()


def test_log_panel_recolors_on_theme_change(manager):
    log = LogPanel()
    log.append_log("ERR", "boom")
    manager.set_theme("light")
    assert _color_at(log, 1) == QColor(token("err_txt")).name()
    assert token("err_txt") == "#c93434"
    assert "boom" in log.toPlainText()


def test_log_panel_clear():
    log = LogPanel()
    log.append_log("OK", "x")
    log.clear_log()
    assert log.toPlainText() == ""
    log.append_log("OK", "y")
    assert log.toPlainText().count("\n") == 0


# ── RowTableModel ────────────────────────────────────────────────────────────
COLS = [Column("name", "Nom", stretch=True), Column("size", "Taille", width=80, align="right")]


def test_row_table_model_basic_operations():
    model = RowTableModel(COLS)
    assert model.columnCount() == 2 and model.rowCount() == 0
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Nom"
    model.set_rows([{"name": "a", "size": 1}, {"name": "b", "size": None}])
    assert model.rowCount() == 2
    assert model.data(model.index(0, 1)) == "1"
    assert model.data(model.index(0, 1), Qt.ItemDataRole.UserRole) == 1
    assert model.data(model.index(1, 1)) == ""
    assert model.append_row({"name": "c", "size": 3}) == 2
    assert [r["name"] for r in model.rows()] == ["a", "b", "c"]
    model.clear()
    assert model.rowCount() == 0 and model.rows() == []


def test_row_table_model_update_row_notifies():
    model = RowTableModel(COLS)
    model.set_rows([{"name": "a", "size": 1}])
    seen: list[tuple[int, int]] = []
    model.dataChanged.connect(lambda tl, br, *_: seen.append((tl.row(), br.column())))
    model.update_row(0, size=42)
    assert model.data(model.index(0, 1)) == "42"
    assert seen == [(0, 1)]
    with pytest.raises(IndexError):
        model.update_row(5, size=1)


def test_row_table_model_tag_background():
    model = RowTableModel(COLS)
    model.set_rows([{"name": "a", "size": 1, "_tag": "ok"},
                    {"name": "b", "size": 2, "_tag": "neutre"},
                    {"name": "c", "size": 3}])
    brush = model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole)
    assert brush.color().name() == QColor(token("ok_bg")).name()
    assert model.data(model.index(1, 0), Qt.ItemDataRole.BackgroundRole) is None
    assert model.data(model.index(2, 0), Qt.ItemDataRole.BackgroundRole) is None


def test_row_table_model_does_not_alias_input():
    rows = [{"name": "a", "size": 1}]
    model = RowTableModel(COLS)
    model.set_rows(rows)
    rows[0]["name"] = "modifié"
    assert model.rows()[0]["name"] == "a"


# ── DataTable ────────────────────────────────────────────────────────────────
def _select_proxy_rows(table: DataTable, *rows: int) -> None:
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    table.selectionModel().clearSelection()
    for r in rows:
        table.selectionModel().select(table.model().index(r, 0), flags)


def test_data_table_selected_rows_are_source_indices_after_reverse_sort():
    table = DataTable(COLS)
    table.data_model.set_rows([{"name": "a", "size": 1}, {"name": "b", "size": 2}, {"name": "c", "size": 3}])
    table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    assert table.model().index(0, 0).data() == "c"            # tri inversé : « c » en premier
    emitted: list[list[int]] = []
    table.selectionRowsChanged.connect(emitted.append)
    _select_proxy_rows(table, 0)
    assert table.selected_rows() == [2]
    assert emitted and emitted[-1] == [2]
    _select_proxy_rows(table, 0, 2)
    assert table.selected_rows() == [0, 2]


def test_data_table_numeric_sort():
    table = DataTable(COLS)
    table.data_model.set_rows([{"name": "a", "size": 9}, {"name": "b", "size": 100}, {"name": "c", "size": 10}])
    table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    order = [table.model().index(r, 1).data(Qt.ItemDataRole.UserRole) for r in range(3)]
    assert order == [9, 10, 100]                              # pas d'ordre alphabétique


def test_data_table_filter_is_case_insensitive():
    table = DataTable(COLS)
    table.data_model.set_rows([{"name": "Alpha", "size": 1}, {"name": "beta", "size": 2}])
    table.set_filter_text("ALP")
    assert table.model().rowCount() == 1
    table.set_filter_text("")
    assert table.model().rowCount() == 2


def test_data_table_row_activation_double_click_and_enter():
    table = DataTable(COLS)
    table.data_model.set_rows([{"name": "a", "size": 1}, {"name": "b", "size": 2}, {"name": "c", "size": 3}])
    table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    got: list[int] = []
    table.rowActivated.connect(got.append)
    table.doubleClicked.emit(table.model().index(0, 0))
    assert got == [2]
    table.setCurrentIndex(table.model().index(2, 0))
    QTest.keyClick(table, Qt.Key.Key_Return)
    assert got == [2, 0]


def test_data_table_recolors_on_theme_change(manager):
    table = DataTable(COLS)
    table.data_model.set_rows([{"name": "a", "size": 1, "_tag": "ok"}])
    notified: list[int] = []
    table.data_model.dataChanged.connect(lambda *_: notified.append(1))
    manager.set_theme("light")
    assert notified
    brush = table.data_model.data(table.data_model.index(0, 0), Qt.ItemDataRole.BackgroundRole)
    assert brush.color().name() == "#dff5e8"


# ── ImagePreview ─────────────────────────────────────────────────────────────
def _wait(qt_app) -> None:
    widgets._pool().waitForDone(10000)
    qt_app.processEvents()


def _make_png(path, size=(400, 200)):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", size, (255, 0, 0)).save(path)
    return path


def test_load_preview_image_is_thumbnail(tmp_path):
    png = _make_png(tmp_path / "a.png")
    image, text = load_preview(png, 120)
    assert image is not None and not image.isNull() and text == ""
    assert image.width() <= 120 and image.height() <= 120


def test_load_preview_fallbacks(tmp_path, monkeypatch):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"pas une image")
    image, text = load_preview(bad, 120)
    assert image is None and "indisponible" in text.lower()
    assert load_preview(tmp_path / "absent.png", 120)[0] is None
    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    assert "Audio" in load_preview(audio, 120)[1]
    video = tmp_path / "x.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(widgets.binaries, "resolve",
                        lambda name, prefs=None: BinaryInfo(name, None, "missing", ""))
    image, text = load_preview(video, 120)
    assert image is None and "ffmpeg" in text


def test_image_preview_loads_asynchronously(qt_app, tmp_path):
    png = _make_png(tmp_path / "a.png")
    prev = ImagePreview(size=120)
    prev.show_image(png)
    assert prev.label.text() == "Chargement…"
    _wait(qt_app)
    assert not prev.label.pixmap().isNull() and prev.label.text() == ""


def test_image_preview_ignores_stale_results(qt_app, tmp_path):
    png = _make_png(tmp_path / "a.png")
    prev = ImagePreview(size=120)
    prev.show_image(png)
    prev.show_text("Sélection multiple")                      # invalide le chargement en cours
    _wait(qt_app)
    assert prev.label.text() == "Sélection multiple" and prev.label.pixmap().isNull()
    prev.show_image(png)
    prev.clear()
    _wait(qt_app)
    assert prev.label.text() == "" and prev.label.pixmap().isNull()


def test_image_preview_missing_file_and_broken_image(qt_app, tmp_path):
    prev = ImagePreview(size=120)
    prev.show_image(tmp_path / "absent.png")
    assert "indisponible" in prev.label.text().lower()
    bad = tmp_path / "x.png"
    bad.write_bytes(b"junk")
    prev.show_image(bad)
    _wait(qt_app)
    assert "indisponible" in prev.label.text().lower()
