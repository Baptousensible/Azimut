"""Corrections d'interface : aperçus, HDR, copie des autres fichiers, sous-onglets Tags."""
from __future__ import annotations

import json

from azimut.modules.convert import engine as ceng
from azimut.modules.convert.profiles import COPY_PROFILE, OTHER_KIND, get_profile
from azimut.ui.widgets import TEXT_PREVIEW_MARK, load_preview


def test_preview_json_is_text(tmp_path, qapp):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"a": [1, 2]}))
    image, text = load_preview(f, 200)
    assert image is None and text.startswith(TEXT_PREVIEW_MARK) and '"a"' in text


def test_preview_pdf_renders_page(tmp_path, qapp):
    from PySide6.QtGui import QPainter, QPdfWriter
    f = tmp_path / "p.pdf"
    w = QPdfWriter(str(f))
    painter = QPainter(w)
    painter.drawText(300, 300, "x")
    painter.end()
    image, _ = load_preview(f, 200)
    assert image is not None and not image.isNull()


def test_scan_copies_other_files_only_when_asked(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "doc.pdf").write_bytes(b"y")
    assert [i.kind for i in ceng.scan_inputs([tmp_path]).items] == ["image"]
    kinds = sorted(i.kind for i in ceng.scan_inputs([tmp_path], include_others=True).items)
    assert kinds == ["image", OTHER_KIND]


def test_other_files_planned_as_plain_copy(tmp_path):
    (tmp_path / "doc.pdf").write_bytes(b"y")
    items = ceng.scan_inputs([tmp_path], include_others=True).items
    jobs = ceng.plan_jobs(items, {}, tmp_path / "out")
    assert jobs[0].profile is COPY_PROFILE and jobs[0].passthrough
    assert jobs[0].dest == tmp_path / "out" / "doc.pdf"


def test_hdr_native_only_with_av1(qapp):
    from azimut.modules.convert.codec_tree import CodecPanel
    panel = CodecPanel()
    panel.set_selected("video", "video_h264") if _has("video_h264") else None
    panel.set_hdr_mode("H")
    non_av1 = [p for p in _video_ids() if not _is_av1(p)]
    av1 = [p for p in _video_ids() if _is_av1(p)]
    panel.set_selected("video", non_av1[0])
    assert panel.hdr.combo.currentData() == "T" and panel.hdr_mode() == "H"   # choix gardé, affichage SDR
    panel.set_selected("video", av1[0])
    assert panel.hdr.combo.currentData() == "H"


def _video_ids():
    from azimut.modules.convert.profiles import profiles_for
    return [p.id for p in profiles_for("video")]


def _has(pid):
    return pid in _video_ids()


def _is_av1(pid):
    from azimut.modules.convert.advanced import family
    return family(get_profile(pid)) == "av1"


def test_rename_knows_jxl():
    from azimut.modules.rename import engine as reng
    assert ".jxl" in reng.SUPPORTED_EXTS and reng.type_acronym(".jxl") == "IMG"


def test_codec_panel_indents_settings_by_level(qapp):
    """Sections → listes de codecs → réglages → action : chaque niveau est décalé d'un cran."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel

    from azimut.modules.convert.codec_tree import CodecPanel

    panel = CodecPanel()
    panel.set_selected("image", "image_avif")
    panel.resize(360, 900)
    panel.show()
    qapp.processEvents()
    try:
        section = next(label for label in panel.findChildren(QLabel) if label.text() == "Images")
        combo_label = panel._combos["image"].parentWidget().layout().itemAt(0).widget()
        quality = panel.rows("image_avif")["crf"]
        reset = panel.reset_button("image_avif")

        def left(widget):
            return widget.mapTo(panel, QPoint(0, 0)).x()

        assert left(section) < left(combo_label) < left(quality.label)
        assert left(panel.hdr.label) == left(quality.label)
        assert left(panel.hdr.combo) == left(quality.slider)
        assert left(reset) == left(quality.label)
        assert left(panel.slider_threads.label) == left(combo_label)
        assert left(panel.chk_low_priority) == left(combo_label)
    finally:
        panel.close()
