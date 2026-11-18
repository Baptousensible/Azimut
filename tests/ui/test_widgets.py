from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
import tracemalloc
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, QItemSelectionModel, QMimeData, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsBlurEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from azimut.core.binaries import BinaryInfo
from azimut.core.prefs import Preferences
from azimut.ui import glass, widgets
from azimut.ui.glass import GlassFrame
from azimut.ui.theme import ThemeManager, token
from azimut.ui.widgets import (
    Card,
    Column,
    DataTable,
    DropZone,
    ImagePreview,
    ImportProgress,
    LiquidProgressBar,
    LogPanel,
    PathDropZone,
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


def _drag_enter_event(mime: QMimeData) -> QDragEnterEvent:
    """Constructeur QPointF (Qt récent) ; repli sur QPoint pour un PySide6 plus ancien."""
    args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    try:
        return QDragEnterEvent(QPointF(5, 5), *args)
    except TypeError:
        return QDragEnterEvent(QPoint(5, 5), *args)


def test_drop_zone_real_drag_events(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    zone = DropZone("x")
    got: list = []
    zone.pathsDropped.connect(got.append)
    mime = _mime(f)
    enter = _drag_enter_event(mime)
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


def test_drop_zone_keeps_its_size_when_import_progress_appears(qt_app):
    """La barre d'import ne doit ni réduire ni agrandir la zone de dépôt."""
    host = QWidget()
    layout = QVBoxLayout(host)
    zone = DropZone("Déposez ici\n(ou cliquez pour parcourir)")
    layout.addWidget(zone)
    layout.addStretch(1)
    host.resize(500, 300)
    host.show()
    qt_app.processEvents()
    before = zone.size()
    assert before.height() > zone.minimumHeight()          # la zone a une hauteur naturelle à préserver

    zone.import_progress.begin_import()
    qt_app.processEvents()
    assert zone.size() == before

    zone.import_progress._stop_animations()
    zone.import_progress._finish_restore()
    host.close()


def test_drop_zone_bounces_continuously_while_hovered(qt_app, monkeypatch):
    """Le pictogramme rebondit en boucle tant que le curseur reste dans la zone."""
    host = QWidget()
    host.resize(500, 220)
    layout = QVBoxLayout(host)
    zone = DropZone("Déposez ici")
    zone.bounce_ms = 30
    layout.addWidget(zone)
    host.show()
    qt_app.processEvents()

    QTest.mouseMove(zone, zone.rect().center())
    qt_app.processEvents()
    anim = zone._bounce_anim
    assert anim is not None and zone._bouncing
    assert anim.duration() == 30 and anim.loopCount() == -1
    assert anim.state() == QAbstractAnimation.State.Running

    QTest.qWait(80)                          # plus de deux cycles : la boucle continue
    assert anim.state() == QAbstractAnimation.State.Running

    # Sortie réelle : le contrôle différé de GlassFrame arrête le rebond.
    monkeypatch.setattr(QApplication, "widgetAt", staticmethod(lambda *_args: None))
    zone.leaveEvent(QEvent(QEvent.Type.Leave))
    QTest.qWait(20)
    assert anim.state() == QAbstractAnimation.State.Stopped
    assert not zone._bouncing
    assert zone._icon_label.pos() == QPoint(0, zone.JUMP)
    host.close()


def test_hover_halo_is_light_on_dark_and_dark_on_light(manager):
    manager.set_theme("dark")
    assert glass._hover_tint().lightness() > 200
    manager.set_theme("light")
    assert glass._hover_tint().lightness() < 80


def test_glass_hover_clears_when_a_modal_opens(qt_app):
    """Un Leave reçu pendant l'ouverture d'une modale ne doit pas figer le halo."""
    host = QWidget()
    host.resize(500, 200)
    layout = QHBoxLayout(host)
    frame = GlassFrame(interactive=True)
    frame.setMinimumSize(200, 100)
    layout.addWidget(frame)
    host.show()
    qt_app.processEvents()

    QTest.mouseMove(frame, QPoint(10, 10))
    QTest.qWait(260)
    assert frame._hover.value > 0.9

    dialog = QDialog(host)
    dialog.setModal(True)
    dialog.show()
    qt_app.processEvents()
    assert QApplication.activeModalWidget() is dialog

    frame.leaveEvent(QEvent(QEvent.Type.Leave))
    QTest.qWait(260)
    assert frame._hover.value < 0.1

    dialog.close()
    host.close()


def test_glass_hover_stays_on_when_the_cursor_is_over_a_child(qt_app):
    """Le Leave transitoire vers un enfant ne doit pas faire clignoter le reflet."""
    host = QWidget()
    host.resize(500, 200)
    layout = QHBoxLayout(host)
    frame = GlassFrame(interactive=True)
    frame.setMinimumSize(200, 100)
    child = QPushButton("Enfant", frame)
    child.setGeometry(40, 30, 100, 30)
    layout.addWidget(frame)
    host.show()
    qt_app.processEvents()

    QTest.mouseMove(frame, QPoint(10, 10))
    QTest.qWait(260)
    QTest.mouseMove(child, child.rect().center())

    frame.leaveEvent(QEvent(QEvent.Type.Leave))
    QTest.qWait(260)
    assert frame._hover.value > 0.9

    host.close()


def test_path_drop_zones_animate_swap(qt_app, tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    host = QWidget()
    layout = QHBoxLayout(host)
    first, second = PathDropZone("Gauche"), PathDropZone("Droite")
    layout.addWidget(first)
    layout.addWidget(second)
    host.resize(640, 160)
    host.show()
    first.set_path(str(a))
    second.set_path(str(b))
    first.swap_ms = 30
    first.animate_swap(second)
    assert first.path() == str(b) and second.path() == str(a)
    assert len(first._swap_overlays) == 4
    assert all(isinstance(o.graphicsEffect(), QGraphicsBlurEffect) for o in first._swap_overlays)
    assert sum(not o.isHidden() for o in first._swap_overlays) == 2
    # Les zones réelles sont masquées sous les copies pour éviter les fantômes fixes.
    assert first.graphicsEffect().opacity() == 0.0
    assert second.graphicsEffect().opacity() == 0.0

    first._swap_group.stop()
    first._apply_swap_frame(0.5)
    visible = [o for o in first._swap_overlays if not o.isHidden()]
    assert len(visible) == 2
    assert all(o.width() < first.width() // 4 for o in visible)
    assert all(o.graphicsEffect().blurRadius() > 0 for o in visible)
    first._finish_swap_animation()
    assert first._swap_overlays == [] and first._swap_group is None
    assert first.graphicsEffect() is None and second.graphicsEffect() is None

    first.animate_swap(second)                  # l'échange inverse fonctionne aussi
    QTest.qWait(200)
    assert first._swap_overlays == [] and first._swap_group is None
    assert first.graphicsEffect() is None and second.graphicsEffect() is None
    host.close()


def test_path_drop_zone_tracks_folder_and_clears(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    zone = PathDropZone("Dossier A")
    zone.show()
    got: list[str] = []
    zone.pathChanged.connect(got.append)
    zone.set_path(str(a))
    assert zone.path() == str(a) and str(a) in zone.toolTip()
    assert zone.import_progress.parent() is zone
    zone.set_path(str(a))
    assert got == [str(a)]                       # pas de doublon
    zone.handle_drop(_mime(b))
    assert zone.path() == str(b) and got == [str(a), str(b)]
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")
    zone.handle_drop(_mime(f))
    assert zone.path() == str(tmp_path)          # un fichier désigne son dossier
    zone._clear.click()
    assert zone.path() == "" and zone._label.text() == "Dossier A\nGlissez un dossier ici…"


def test_import_progress_tracks_counts_and_is_temporary(qt_app):
    p = ImportProgress()
    assert isinstance(p.bar, LiquidProgressBar)
    p.completion_ms = 30
    p.min_progress_ms = 0
    p.value_ms = 0
    p.begin_import()
    assert p.is_importing() and not p.isHidden()
    p.set_import_progress(3, 10, "photo.jpg")
    assert (p.bar.maximum(), p.bar.value()) == (10, 3)
    assert p.bar.isTextVisible() and "%" in p.bar.text()
    p.set_import_progress(0, 0)              # un total inconnu ne remet pas la barre à zéro
    assert (p.bar.minimum(), p.bar.maximum(), p.bar.value()) == (0, 10, 3)
    p.end_import()
    assert not p.is_importing() and p.is_completed()
    assert p.done_label.isVisible() and p.bar.isHidden()
    QTest.qWait(80)
    assert p.isHidden() and not p.is_completed()


def test_import_progress_smooths_and_keeps_a_minimum_duration(qt_app):
    """Une importation instantanée laisse quand même voir la barre se remplir."""
    p = ImportProgress()
    p.value_ms = 260
    p.min_progress_ms = 600
    p.completion_ms = 2000
    p.begin_import()
    p.set_import_progress(5, 10)
    assert p.bar.value() < 5                      # pas de saut direct à la valeur cible
    p.end_import()
    assert not p.is_importing() and not p.is_completed()
    assert p.bar.isVisible()
    QTest.qWait(300)
    assert p.bar.value() == 10 and not p.is_completed()
    QTest.qWait(400)
    assert p.is_completed() and p.bar.isHidden()
    p.close()


def test_drop_zone_import_animation_and_restoration(qt_app):
    zone = DropZone("Glissez ici un dossier à renommer")
    zone.resize(420, 130)
    zone.show()
    prompt = zone._label.text()
    progress = zone.import_progress
    progress.entry_ms = 40
    progress.morph_ms = 40
    progress.completion_ms = 90
    progress.min_progress_ms = 0
    progress.value_ms = 0
    progress.restore_ms = 60
    zone.restore_visual_ms = 60
    progress.begin_import()
    assert progress.is_importing() and not progress.isHidden()
    assert zone._label.isHidden() and zone._icon_box.isHidden()
    assert progress.bar.isVisible() and progress.bar.isTextVisible()
    assert (progress.bar.minimum(), progress.bar.maximum()) == (0, 0)  # pas de remplissage en boucle
    QTest.qWait(80)                          # fin de l'entrée/du déploiement de la barre
    icon, bar = progress.morph, progress.bar
    assert abs(icon.geometry().center().y() - progress.rect().center().y()) <= 2
    assert bar.x() >= icon.geometry().right()
    left_margin = icon.geometry().left()
    right_margin = progress.width() - 1 - bar.geometry().right()
    assert abs(left_margin - right_margin) <= 2
    progress.set_import_progress(4, 4)
    assert (bar.maximum(), bar.value()) == (4, 4) and "%" in bar.text()
    progress.end_import()
    assert progress.is_completed()
    assert progress.done_label.isVisible()
    assert progress.done_label.text() == "Importation terminée !"
    assert progress.bar.isHidden() and progress.morph.isVisible()
    QTest.qWait(60)                          # fin de la transformation dossier → coche
    assert progress.morph.progress() == 1.0
    assert progress.isVisible()
    QTest.qWait(40)                          # début de la sortie, après le maintien
    assert progress.isVisible()
    assert zone._label.isHidden()
    QTest.qWait(100)                         # overlay disparu, l'invite entre en fondu
    assert progress.isHidden()
    assert zone._label.isVisible() and zone._icon_box.isVisible()
    assert zone._label.graphicsEffect() is not None
    QTest.qWait(70)                          # fin du fondu d'entrée de l'invite
    assert zone._label.graphicsEffect() is None
    assert zone._label.text() == prompt


# ── ProgressPanel ────────────────────────────────────────────────────────────
def test_progress_panel():
    p = ProgressPanel()
    assert isinstance(p.bar, LiquidProgressBar)
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
    assert token("err_txt") == "#b02a2a"        # assombri pour atteindre 4,5:1 sur err_bg
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
                    {"name": "b", "size": 2, "_tag": "neutre", "_fg": "muted"},
                    {"name": "c", "size": 3}])
    brush = model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole)
    assert brush.color().name() == QColor(token("ok_bg")).name()
    fg = model.data(model.index(1, 0), Qt.ItemDataRole.ForegroundRole)
    assert fg.color().name() == QColor(token("muted")).name()
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


def test_data_table_keeps_headers_readable_at_narrow_width():
    """Dans un tableau étroit, les colonnes gardent la largeur de leur en-tête."""
    cols = [Column(f"c{i}", f"En-tête long {i}", width=150) for i in range(6)]
    table = DataTable(cols)
    table.resize(430, 240)
    table.show()
    QTest.qWait(10)
    header = table.horizontalHeader()
    for i in range(6):
        text = table.model().headerData(i, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        assert table.columnWidth(i) >= header.fontMetrics().horizontalAdvance(str(text))


def test_path_drop_zone_height_follows_zoom(manager):
    """À 150 %, la zone de dépôt grandit pour que ses deux lignes restent visibles."""
    zone = PathDropZone("Dossier")
    assert zone.minimumHeight() == widgets.scaled(104)
    manager.set_zoom(1.5)
    assert zone.minimumHeight() == widgets.scaled(104)
    assert zone.minimumHeight() > 104


def test_data_table_recolors_on_theme_change(manager):
    table = DataTable(COLS)
    table.data_model.set_rows([{"name": "a", "size": 1, "_tag": "ok"}])
    notified: list[int] = []
    table.data_model.dataChanged.connect(lambda *_: notified.append(1))
    manager.set_theme("light")
    assert notified
    brush = table.data_model.data(table.data_model.index(0, 0), Qt.ItemDataRole.BackgroundRole)
    assert brush.color().name() == "#aacdba"


# ── ImagePreview ─────────────────────────────────────────────────────────────
def _wait(qt_app) -> None:
    widgets._pool().waitForDone(10000)
    qt_app.processEvents()


def _make_png(path, size=(400, 200)):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", size, (255, 0, 0)).save(path)
    return path


def _make_avif(path):
    """Crée un AVIF réel avec ffmpeg ; ignore le test si l'outil manque."""
    png = _make_png(path.with_suffix(".png"), (160, 96))
    ffmpeg = widgets.binaries.resolve("ffmpeg")
    if not ffmpeg.available:
        pytest.skip("ffmpeg est requis pour créer un AVIF de test")
    result = subprocess.run(
        [str(ffmpeg.path), "-y", "-loglevel", "error", "-i", str(png),
         "-frames:v", "1", str(path)], capture_output=True, timeout=30)
    if result.returncode != 0:
        pytest.skip("ffmpeg ne sait pas écrire l'AVIF")
    return path


def test_load_preview_image_is_thumbnail(tmp_path):
    png = _make_png(tmp_path / "a.png")
    image, text = load_preview(png, 120)
    assert image is not None and not image.isNull() and text == ""
    assert image.width() <= 120 and image.height() <= 120


def test_load_preview_avif_falls_back_to_ffmpeg(tmp_path, monkeypatch):
    avif = _make_avif(tmp_path / "a.avif")
    monkeypatch.setattr(widgets, "_load_image_file", lambda *_: None)
    image, text = load_preview(avif, 120)
    assert image is not None and not image.isNull() and text == ""
    assert image.width() <= 120 and image.height() <= 120


def test_load_preview_avif_without_ffmpeg_is_explicit(tmp_path, monkeypatch):
    avif = _make_avif(tmp_path / "a.avif")
    monkeypatch.setattr(widgets, "_load_image_file", lambda *_: None)
    monkeypatch.setattr(widgets.binaries, "resolve",
                        lambda name, prefs=None: BinaryInfo(name, None, "missing", ""))
    image, text = load_preview(avif, 120)
    assert image is None and "ffmpeg" in text


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


def _fake_tool(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(sys.platform == "win32", reason="script à shebang")
def test_video_preview_stops_a_stale_ffmpeg(tmp_path, monkeypatch):
    """Un aperçu remplacé pendant ffmpeg rend la main sans attendre la fin de l'extraction (SUBPROC-003)."""
    slow = _fake_tool(tmp_path / "ffmpeg-lent", """
        import time
        time.sleep(60)
        """)
    monkeypatch.setattr(widgets.binaries, "resolve",
                        lambda name, prefs=None: BinaryInfo(name, slow, "custom", "7.0"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    start = time.monotonic()
    image, text = load_preview(video, 120, lambda: not stop.is_set())
    assert time.monotonic() - start < 5
    assert image is None and text == ""


@pytest.mark.skipif(sys.platform == "win32", reason="script à shebang")
def test_pdf_preview_stops_a_stale_tool(tmp_path, monkeypatch):
    """Un outil PDF qui traîne est arrêté dès que l'aperçu n'est plus courant (SUBPROC-003)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tool(bin_dir / "pdftoppm", """
        import time
        time.sleep(60)
        """)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"x")
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    start = time.monotonic()
    assert widgets._render_pdf_external(pdf, 120, lambda: not stop.is_set()) is None
    assert time.monotonic() - start < 5


@pytest.mark.skipif(sys.platform == "win32", reason="script à shebang")
def test_image_preview_does_not_buffer_ffmpeg_output(tmp_path, monkeypatch):
    """50 Mo sur stdout ne sont pas mis en mémoire : la sortie d'un aperçu n'est pas capturée (SUBPROC-003)."""
    flood = _fake_tool(tmp_path / "ffmpeg-bavard", """
        import sys
        sys.stdout.write("x" * (50 * 1024 * 1024))
        """)
    monkeypatch.setattr(widgets.binaries, "resolve",
                        lambda name, prefs=None: BinaryInfo(name, flood, "custom", "7.0"))
    monkeypatch.setattr(widgets, "_load_image_file", lambda *_: None)
    image = tmp_path / "a.avif"
    image.write_bytes(b"x")
    tracemalloc.start()
    try:
        preview, _text = load_preview(image, 120)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert preview is None
    assert peak < 8 * 1024 * 1024, f"pic mémoire {peak / 1024 / 1024:.1f} Mio"


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
