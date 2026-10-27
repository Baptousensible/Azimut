"""Tests d'Azimut Press : logique JSON et image, workers, fenêtre."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import io
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from azimut.core.prefs import Preferences
from azimut.modules.press import MODULE, imagepress, jsonpress
from azimut.modules.press.imagepress import ImageSettings, compress_image
from azimut.modules.press.jsonpress import (JsonInvalidError, JsonSettings, JsonVerificationError,
                                            compress_json, minify_text)
from azimut.modules.press.workers import (STATE_LABELS, ST_ALREADY, ST_ANIMATED, ST_DONE, ST_ERROR, ST_INVALID,
                                          PressItem, PressOptions, PressWorker, ScanWorker, kind_of,
                                          pressed_dir)

ROOT = Path(__file__).resolve().parents[2]


# --- Outils ------------------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def press_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def noise_image(size: tuple[int, int] = (160, 120), seed: int = 1) -> Image.Image:
    data = random.Random(seed).randbytes(size[0] * size[1] * 3)
    return Image.frombytes("RGB", size, data)


def gradient_image(size: tuple[int, int] = (256, 256)) -> Image.Image:
    return Image.linear_gradient("L").resize(size).convert("RGB")


def save_noise_jpeg(path: Path, quality: int = 95, size: tuple[int, int] = (160, 120), **kw) -> Path:
    noise_image(size).save(path, "JPEG", quality=quality, **kw)
    return path


def make_item(path: Path, root: Path | None = None) -> PressItem:
    root = root or path.parent
    kind = kind_of(path)
    assert kind is not None
    return PressItem(path, root, kind, path.stat().st_size, path.name)


class Run:
    """Résultat d'une exécution synchrone de PressWorker."""

    def __init__(self) -> None:
        self.results: dict[int, object] = {}
        self.logs: list[tuple[str, str]] = []
        self.completed: object = None
        self.was_cancelled = False
        self.failed: str | None = None


def run_press(items: list[PressItem], worker_hook=None, **opts) -> Run:
    opts.setdefault("workers", 2)
    options = PressOptions(**opts)
    worker = PressWorker(list(enumerate(items)), options)
    run = Run()
    worker.item_done.connect(lambda row, res: run.results.__setitem__(row, res))
    worker.log.connect(lambda level, text: run.logs.append((level, text)))
    worker.completed.connect(lambda res: setattr(run, "completed", res))
    worker.cancelled.connect(lambda: setattr(run, "was_cancelled", True))
    worker.failed.connect(lambda msg: setattr(run, "failed", msg))
    if worker_hook:
        worker_hook(worker)
    worker.run()   # exécution dans le thread du test : déterministe
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()   # livre les signaux émis depuis les threads du pool
    assert run.failed is None, run.failed
    if not run.was_cancelled:
        assert sorted(run.results) == list(range(len(items)))   # un résultat par fichier, sans exception
    return run


def files_under(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


# --- Minifieur JSON ----------------------------------------------------------------------------

def test_minify_example_from_spec():
    out = minify_text('{ "a" : "x  y", "b": 1E5, "c": [1, 2] }')
    assert out == '{"a":"x  y","b":1E5,"c":[1,2]}'


def test_minify_keeps_escapes_and_numbers_untouched():
    text = r'{ "k" : "a\"  b" , "p": "C:\\" , "u": "\u00e9  " , "arr" : [ 1 , 2.50 , -0.0E+3 , true , null ] }'
    out = minify_text(text)
    assert out == r'{"k":"a\"  b","p":"C:\\","u":"\u00e9  ","arr":[1,2.50,-0.0E+3,true,null]}'
    assert json.loads(out) == json.loads(text)


def test_minify_multiline_with_tabs_and_crlf():
    text = '{\r\n\t"a": [\r\n\t\t1,\r\n\t\t2\r\n\t],\r\n\t"b": "x\\ty z"\r\n}\r\n'
    assert minify_text(text) == '{"a":[1,2],"b":"x\\ty z"}'


def test_bom_is_tolerated_and_dropped(tmp_path):
    src = tmp_path / "bom.json"
    src.write_bytes(b"\xef\xbb\xbf" + b'{ "a" : 1 }')
    res = compress_json(src, JsonSettings())
    assert res.status == jsonpress.STATUS_DONE
    assert res.data == b'{"a":1}'


def test_invalid_json_reports_position(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text('{"a": }', encoding="utf-8")
    with pytest.raises(JsonInvalidError) as exc:
        compress_json(src, JsonSettings())
    assert "ligne 1" in str(exc.value) and "colonne 7" in str(exc.value)


def test_invalid_utf8_is_invalid_json(tmp_path):
    src = tmp_path / "latin.json"
    src.write_bytes('{"a": "é"}'.encode("latin-1"))
    with pytest.raises(JsonInvalidError):
        compress_json(src, JsonSettings())


def test_already_minified_is_left_alone(tmp_path):
    src = tmp_path / "min.json"
    src.write_text('{"a":1}', encoding="utf-8")
    res = compress_json(src, JsonSettings())
    assert res.status == jsonpress.STATUS_ALREADY and res.data is None


def test_indent_mode(tmp_path):
    src = tmp_path / "x.json"
    src.write_text('{"b":1,"a":[1,2],"é":"日本"}', encoding="utf-8")
    res = compress_json(src, JsonSettings(mode="indent", indent=4, sort_keys=True))
    expected = json.dumps({"b": 1, "a": [1, 2], "é": "日本"}, indent=4, ensure_ascii=False, sort_keys=True)
    assert res.data is not None and res.data.decode("utf-8") == expected
    assert "é" in res.data.decode("utf-8")   # pas de \u00e9
    assert res.data.decode("utf-8").index('"a"') < res.data.decode("utf-8").index('"b"')   # clés triées


def test_json_equal_handles_nan_and_bool():
    assert jsonpress.json_equal(json.loads('{"a": NaN}'), json.loads('{"a": NaN}'))
    assert not jsonpress.json_equal([True], [1])


# --- Images : logique pure ---------------------------------------------------------------------

def test_jpeg_noise_is_recompressed_smaller(tmp_path):
    src = save_noise_jpeg(tmp_path / "n.jpg", quality=95)
    res = compress_image(src, ImageSettings(quality=60))
    assert res.status == imagepress.STATUS_DONE and res.data is not None
    assert res.size_out < res.size_in
    assert res.suffix == ".jpg"
    assert Image.open(io.BytesIO(res.data)).format == "JPEG"


def test_jpeg_output_is_progressive(tmp_path):
    src = save_noise_jpeg(tmp_path / "n.jpg", quality=95)
    res = compress_image(src, ImageSettings(quality=60))
    assert Image.open(io.BytesIO(res.data)).info.get("progressive") or \
        Image.open(io.BytesIO(res.data)).info.get("progression")


def test_resize_never_enlarges(tmp_path):
    src = save_noise_jpeg(tmp_path / "n.jpg", quality=95, size=(400, 200))
    small = compress_image(src, ImageSettings(quality=70, max_dimension=100))
    assert Image.open(io.BytesIO(small.data)).size == (100, 50)
    same = compress_image(src, ImageSettings(quality=70, max_dimension=1000))
    assert Image.open(io.BytesIO(same.data)).size == (400, 200)


def test_png_converted_to_webp(tmp_path):
    src = tmp_path / "g.png"
    gradient_image().save(src)
    res = compress_image(src, ImageSettings(output_format="webp", quality=80))
    assert res.status == imagepress.STATUS_DONE and res.suffix == ".webp"
    assert Image.open(io.BytesIO(res.data)).format == "WEBP"


def test_png_with_alpha_keeps_transparency_in_webp(tmp_path):
    src = tmp_path / "a.png"
    img = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    img.paste((0, 0, 255, 255), (0, 0, 32, 64))
    img.save(src)
    res = compress_image(src, ImageSettings(output_format="webp", keep_smallest=False))
    out = Image.open(io.BytesIO(res.data)).convert("RGBA")
    assert out.getpixel((60, 10))[3] == 0 and out.getpixel((5, 10))[3] == 255


def test_rgba_to_jpeg_is_flattened_on_white(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(src)
    res = compress_image(src, ImageSettings(output_format="jpeg", keep_smallest=False))
    assert Image.open(io.BytesIO(res.data)).convert("RGB").getpixel((5, 5)) == (255, 255, 255)


def test_png_reduce_colors_goes_to_palette(tmp_path):
    src = tmp_path / "c.png"
    noise_image((120, 120)).save(src)
    res = compress_image(src, ImageSettings(reduce_colors=True))
    assert res.status == imagepress.STATUS_DONE
    assert Image.open(io.BytesIO(res.data)).mode == "P"
    assert res.size_out < res.size_in


def test_already_optimized_is_copied(tmp_path):
    src = tmp_path / "opt.jpg"
    gradient_image().save(src, "JPEG", quality=20, optimize=True, progressive=True)
    res = compress_image(src, ImageSettings(quality=95))
    assert res.status == imagepress.STATUS_ALREADY and res.data is None
    assert res.size_out == res.size_in and res.suffix == ".jpg"


def test_keep_smallest_disabled_writes_bigger_result(tmp_path):
    src = tmp_path / "opt.jpg"
    gradient_image().save(src, "JPEG", quality=20, optimize=True, progressive=True)
    res = compress_image(src, ImageSettings(quality=95, keep_smallest=False))
    assert res.status == imagepress.STATUS_DONE and res.size_out > res.size_in


def test_animated_gif_is_not_touched(tmp_path):
    src = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=80, loop=0)
    res = compress_image(src, ImageSettings())
    assert res.status == imagepress.STATUS_ANIMATED and res.data is None


def test_multipage_tiff_is_not_touched(tmp_path):
    src = tmp_path / "multi.tif"
    frames = [Image.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(src, save_all=True, append_images=frames[1:])
    assert compress_image(src, ImageSettings()).status == imagepress.STATUS_ANIMATED


def _jpeg_with_orientation(path: Path, *, icc: bytes | None = None) -> Path:
    exif = Image.Exif()
    exif[0x0112] = 6   # à faire pivoter de 90° pour l'affichage
    exif[0x010F] = "AzimutCam"
    kwargs = {"icc_profile": icc} if icc else {}
    noise_image((60, 30)).save(path, "JPEG", quality=95, exif=exif, **kwargs)
    return path


def test_strip_metadata_applies_orientation_to_pixels(tmp_path):
    src = _jpeg_with_orientation(tmp_path / "o.jpg")
    res = compress_image(src, ImageSettings(strip_metadata=True, quality=60))
    assert res.status == imagepress.STATUS_DONE and res.data is not None
    out = Image.open(io.BytesIO(res.data))
    assert out.size == (30, 60)                     # pixels pivotés (60x30 → 30x60)
    exif = out.getexif()
    assert 0x0112 not in exif and 0x010F not in exif
    assert "icc_profile" not in out.info


def test_strip_metadata_overrides_the_keep_smallest_guard(tmp_path):
    """Recopier l'original conserverait les métadonnées que l'utilisateur veut retirer."""
    src = tmp_path / "tiny.jpg"
    exif = Image.Exif()
    exif[0x010F] = "AzimutCam"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(src, "JPEG", quality=10, exif=exif)
    res = compress_image(src, ImageSettings(strip_metadata=True, quality=95))
    assert res.status == imagepress.STATUS_DONE
    assert 0x010F not in Image.open(io.BytesIO(res.data)).getexif()


def test_metadata_kept_by_default(tmp_path):
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _jpeg_with_orientation(tmp_path / "o.jpg", icc=icc)
    res = compress_image(src, ImageSettings(quality=50))
    assert res.status == imagepress.STATUS_DONE
    out = Image.open(io.BytesIO(res.data))
    assert out.size == (60, 30)                     # pixels non pivotés : l'orientation reste dans l'EXIF
    assert out.getexif().get(0x0112) == 6 and out.getexif().get(0x010F) == "AzimutCam"
    assert out.info.get("icc_profile") == icc


def test_mpo_style_multiframe_jpeg_is_not_treated_as_animation():
    # Les MPO (photos de téléphone) ont plusieurs « images » mais restent des JPEG ordinaires.
    class FakeMpo:
        format = "MPO"
        is_animated = True
        n_frames = 2

    assert imagepress._is_animated(FakeMpo()) is False  # type: ignore[arg-type]


@pytest.mark.skipif(not imagepress.can_write("AVIF"), reason="écriture AVIF indisponible")
def test_avif_output(tmp_path):
    src = tmp_path / "g.png"
    gradient_image().save(src)
    res = compress_image(src, ImageSettings(output_format="avif", quality=50, keep_smallest=False))
    assert res.suffix == ".avif" and Image.open(io.BytesIO(res.data)).format == "AVIF"


def test_heic_roundtrip_when_available(tmp_path):
    pytest.importorskip("pillow_heif")
    assert ".heic" in imagepress.supported_extensions()
    src = tmp_path / "p.heic"
    imagepress.has_heif()
    noise_image((96, 64)).save(src, "HEIF", quality=95)
    res = compress_image(src, ImageSettings(quality=40))
    assert res.suffix == ".heic" and res.data is not None and res.size_out < res.size_in


def test_unreadable_image_raises(tmp_path):
    src = tmp_path / "fake.jpg"
    src.write_bytes(b"ceci n'est pas une image")
    with pytest.raises(imagepress.ImagePressError):
        compress_image(src, ImageSettings())


def test_settings_from_dict_is_tolerant():
    s = ImageSettings.from_dict({"output_format": "gif", "quality": "abc", "max_dimension": -5, "strip_metadata": 1})
    assert s.output_format == "keep" and s.quality == 82 and s.max_dimension is None and s.strip_metadata
    assert ImageSettings.from_dict({"quality": 500}).quality == 100
    assert ImageSettings.from_dict(None) == ImageSettings()
    assert JsonSettings.from_dict({"mode": "x", "indent": 3}) == JsonSettings()
    roundtrip = ImageSettings(output_format="webp", quality=40, max_dimension=800, reduce_colors=True)
    assert ImageSettings.from_dict(roundtrip.to_dict()) == roundtrip


def test_supported_extensions_and_kinds(tmp_path):
    exts = imagepress.supported_extensions()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"):
        assert ext in exts
    assert kind_of(tmp_path / "a.JSON") == "json" and kind_of(tmp_path / "a.PNG") == "image"
    assert kind_of(tmp_path / "a.txt") is None


# --- Workers -----------------------------------------------------------------------------------

def test_scan_worker_recursion_and_filtering(tmp_path):
    (tmp_path / "src" / "sub").mkdir(parents=True)
    save_noise_jpeg(tmp_path / "src" / "a.jpg")
    save_noise_jpeg(tmp_path / "src" / "sub" / "b.JPG")
    (tmp_path / "src" / "c.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src" / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "src" / ".hidden.jpg").write_bytes(b"x")

    deep = ScanWorker([tmp_path / "src"], recursive=True).do_work()
    assert sorted(i.display for i in deep) == ["src/a.jpg", "src/c.json", "src/sub/b.JPG"]
    flat = ScanWorker([tmp_path / "src"], recursive=False).do_work()
    assert sorted(i.display for i in flat) == ["src/a.jpg", "src/c.json"]
    single = ScanWorker([tmp_path / "src" / "a.jpg", tmp_path / "src" / "a.jpg", tmp_path / "src" / "notes.txt"],
                        recursive=True).do_work()
    assert [i.display for i in single] == ["a.jpg"] and single[0].root == tmp_path / "src"


def test_default_output_folder_keeps_tree_and_never_touches_originals(tmp_path):
    root = tmp_path / "photos"
    (root / "sub").mkdir(parents=True)
    a = save_noise_jpeg(root / "a.jpg")
    b = save_noise_jpeg(root / "sub" / "b.jpg", size=(80, 60))
    before = {p: p.read_bytes() for p in (a, b)}
    run = run_press([make_item(a, root), make_item(b, root)], image_settings=ImageSettings(quality=50))

    assert pressed_dir(root) == tmp_path / "photos_Pressed"
    assert (tmp_path / "photos_Pressed" / "a.jpg").exists()
    assert (tmp_path / "photos_Pressed" / "sub" / "b.jpg").exists()
    assert all(p.read_bytes() == data for p, data in before.items())
    assert all(r.status == ST_DONE for r in run.results.values())
    summary = run.completed
    assert summary.ok == 2 and summary.errors == 0 and summary.total_after < summary.total_before


def test_output_name_collision_uses_unique_path(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    a = save_noise_jpeg(root / "a.jpg")
    run_press([make_item(a, root)])
    run_press([make_item(a, root)])
    assert sorted(p.name for p in (tmp_path / "photos_Pressed").iterdir()) == ["a.jpg", "a__1.jpg"]


def test_custom_output_dir(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    a = save_noise_jpeg(root / "a.jpg")
    out = tmp_path / "elsewhere"
    run_press([make_item(a, root)], out_dir=out)
    assert (out / "a.jpg").exists() and not (tmp_path / "in_Pressed").exists()


def test_format_change_renames_extension(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "g.png"
    gradient_image().save(p)
    run = run_press([make_item(p, root)], image_settings=ImageSettings(output_format="webp"))
    assert run.results[0].status == ST_DONE
    assert (tmp_path / "in_Pressed" / "g.webp").exists() and not (tmp_path / "in_Pressed" / "g.png").exists()


def test_already_optimized_is_copied_by_worker(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "opt.jpg"
    gradient_image().save(p, "JPEG", quality=20, optimize=True, progressive=True)
    run = run_press([make_item(p, root)], image_settings=ImageSettings(quality=95))
    res = run.results[0]
    assert res.status == ST_ALREADY and STATE_LABELS[res.status] == "Déjà optimisé"
    assert (tmp_path / "in_Pressed" / "opt.jpg").read_bytes() == p.read_bytes()
    assert res.size_after == res.size_before


def test_animated_gif_is_copied_as_is_by_worker(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "anim.gif"
    frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=80, loop=0)
    run = run_press([make_item(p, root)])
    assert run.results[0].status == ST_ANIMATED and STATE_LABELS[ST_ANIMATED] == "Ignoré (animée)"
    assert (tmp_path / "in_Pressed" / "anim.gif").read_bytes() == p.read_bytes()


def test_invalid_json_is_never_modified_nor_written(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    bad = root / "bad.json"
    bad.write_text('{"a": }', encoding="utf-8")
    good = root / "good.json"
    good.write_text('{ "a" : 1E5 }', encoding="utf-8")
    original = bad.read_bytes()
    run = run_press([make_item(bad, root), make_item(good, root)])

    assert run.results[0].status == ST_INVALID and STATE_LABELS[ST_INVALID] == "JSON invalide"
    assert bad.read_bytes() == original
    assert not (tmp_path / "in_Pressed" / "bad.json").exists()
    assert any(level == "ERR" and "ligne 1" in text and "colonne 7" in text for level, text in run.logs)
    assert (tmp_path / "in_Pressed" / "good.json").read_text(encoding="utf-8") == '{"a":1E5}'
    assert run.completed.ok == 1 and run.completed.errors == 1


def test_json_output_is_utf8_without_bom(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{ "é" : "日本" }'.encode("utf-8"))
    run_press([make_item(p, root)])
    out = (tmp_path / "in_Pressed" / "bom.json").read_bytes()
    assert out == '{"é":"日本"}'.encode("utf-8") and not out.startswith(b"\xef\xbb\xbf")


def test_failed_equivalence_check_writes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "n.json"
    p.write_text('{ "a" : 1E5 }', encoding="utf-8")
    monkeypatch.setattr(jsonpress, "minify_text", lambda text: text.replace("1E5", "1E6").replace(" ", ""))
    with pytest.raises(JsonVerificationError):
        jsonpress.process_text(p.read_text(encoding="utf-8"), JsonSettings())
    run = run_press([make_item(p, root)])
    assert run.results[0].status == ST_ERROR
    assert not (tmp_path / "in_Pressed" / "n.json").exists()
    assert p.read_text(encoding="utf-8") == '{ "a" : 1E5 }'


def test_indent_mode_logs_the_number_warning_once(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    items = []
    for name in ("a.json", "b.json"):
        (root / name).write_text('{"x":1E5}', encoding="utf-8")
        items.append(make_item(root / name, root))
    run = run_press(items, json_settings=JsonSettings(mode="indent"))
    assert sum("normaliser les nombres" in text for _, text in run.logs) == 1


def test_unreadable_image_is_an_error_row(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "fake.jpg"
    p.write_bytes(b"pas une image")
    run = run_press([make_item(p, root)])
    assert run.results[0].status == ST_ERROR and run.completed.errors == 1
    assert not (tmp_path / "in_Pressed").exists() or not files_under(tmp_path / "in_Pressed")


def test_in_place_moves_original_to_backup(tmp_path):
    root = tmp_path / "in"
    (root / "sub").mkdir(parents=True)
    a = save_noise_jpeg(root / "sub" / "a.jpg")
    original = a.read_bytes()
    backup = tmp_path / "backup"
    run = run_press([make_item(a, root)], image_settings=ImageSettings(quality=50),
                    in_place=True, backup_dir=backup)

    assert run.results[0].status == ST_DONE
    assert (backup / "sub" / "a.jpg").read_bytes() == original          # l'original est sauvegardé
    assert a.exists() and a.stat().st_size < len(original)               # le résultat est à sa place
    assert Image.open(a).format == "JPEG"
    assert not [p for p in files_under(tmp_path) if p.name.endswith(".tmp")]
    assert not (tmp_path / "in_Pressed").exists()


def test_in_place_with_format_change(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "g.png"
    gradient_image().save(p)
    original = p.read_bytes()
    backup = tmp_path / "bak"
    run_press([make_item(p, root)], image_settings=ImageSettings(output_format="webp"),
              in_place=True, backup_dir=backup)
    assert (backup / "g.png").read_bytes() == original
    assert (root / "g.webp").exists() and not (root / "g.png").exists()


def test_in_place_leaves_already_optimized_untouched(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    p = root / "opt.jpg"
    gradient_image().save(p, "JPEG", quality=20, optimize=True, progressive=True)
    original = p.read_bytes()
    backup = tmp_path / "bak"
    run = run_press([make_item(p, root)], image_settings=ImageSettings(quality=95), in_place=True, backup_dir=backup)
    assert run.results[0].status == ST_ALREADY and p.read_bytes() == original and not backup.exists()


def test_cancel_mid_batch_leaves_no_partial_file(tmp_path, monkeypatch):
    root = tmp_path / "in"
    root.mkdir()
    items = [make_item(save_noise_jpeg(root / f"{n}.jpg", size=(120, 90)), root) for n in range(8)]
    real = imagepress.compress_image
    calls = {"n": 0}
    holder: dict[str, PressWorker] = {}

    def cancelling(src, settings):
        calls["n"] += 1
        if calls["n"] == 3:
            holder["w"].cancel()   # annulation pendant le traitement du 3e fichier
        return real(src, settings)

    monkeypatch.setattr(imagepress, "compress_image", cancelling)
    run = run_press(items, worker_hook=lambda w: holder.__setitem__("w", w), workers=1,
                    image_settings=ImageSettings(quality=50))

    assert run.was_cancelled and run.completed is None
    out = tmp_path / "in_Pressed"
    written = files_under(out) if out.exists() else []
    assert len(written) < len(items)
    assert not [p for p in written if p.name.endswith(".tmp") or p.name.startswith(".press_")]
    for p in written:   # tout fichier présent est complet
        with Image.open(p) as img:
            img.load()
        assert p.stat().st_size > 0


def test_cancel_in_place_keeps_every_original_reachable(tmp_path, monkeypatch):
    root = tmp_path / "in"
    root.mkdir()
    paths = [save_noise_jpeg(root / f"{n}.jpg", size=(100, 80)) for n in range(5)]
    originals = {p.name: p.read_bytes() for p in paths}
    backup = tmp_path / "bak"
    real = imagepress.compress_image
    calls = {"n": 0}
    holder: dict[str, PressWorker] = {}

    def cancelling(src, settings):
        calls["n"] += 1
        if calls["n"] == 2:
            holder["w"].cancel()
        return real(src, settings)

    monkeypatch.setattr(imagepress, "compress_image", cancelling)
    run = run_press([make_item(p, root) for p in paths], worker_hook=lambda w: holder.__setitem__("w", w),
                    workers=1, in_place=True, backup_dir=backup, image_settings=ImageSettings(quality=50))
    assert run.was_cancelled
    assert not [p for p in files_under(tmp_path) if p.name.endswith(".tmp")]
    for name, data in originals.items():   # chaque fichier est soit intact, soit sauvegardé
        assert (root / name).read_bytes() == data or (backup / name).read_bytes() == data


# --- Module et fenêtre -------------------------------------------------------------------------

def test_module_spec_fields():
    assert (MODULE.id, MODULE.name, MODULE.icon, MODULE.order) == ("press", "Azimut Press", "📦", 40)
    assert MODULE.entry == "azimut.modules.press.window:PressWindow" and MODULE.requires == ()


def test_importing_module_does_not_load_qtwidgets():
    code = ("import sys; from azimut.modules.press import MODULE; "
            "assert 'PySide6.QtWidgets' not in sys.modules, 'QtWidgets importé'; print(MODULE.id)")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "press"


def wait_until(condition, timeout: float = 30.0) -> bool:
    from PySide6.QtCore import QCoreApplication
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(press_app, tmp_path, monkeypatch):
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA", "HOME"):
        monkeypatch.setenv(var, str(tmp_path / "home" / var))
    prefs = Preferences(path=tmp_path / "prefs.json")
    monkeypatch.setattr(Preferences, "instance", classmethod(lambda cls: prefs))
    from azimut.modules.press.window import PressWindow
    win = PressWindow(MODULE)
    yield win
    win.close()


def test_window_builds_with_expected_controls(window):
    assert window.cmb_format.count() == 5 and window.spin_quality.value() == 82
    assert window.chk_smallest.isChecked() and not window.chk_strip.isChecked()
    assert not window.chk_inplace.isChecked() and not window.backup_field.isEnabled()
    if not imagepress.can_write("AVIF"):   # option grisée, avec explication
        index = window.cmb_format.findData("avif")
        assert not window.cmb_format.model().item(index).isEnabled()
        assert window.cmb_format.model().item(index).toolTip()


def test_window_scan_run_and_totals(window, tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    save_noise_jpeg(src / "a.jpg")
    save_noise_jpeg(src / "sub" / "b.jpg", size=(80, 60))
    (src / "c.json").write_text('{ "a" : [ 1 , 2 ] }', encoding="utf-8")
    window.spin_quality.setValue(50)

    window.open_paths([str(src)])
    assert wait_until(lambda: len(window._items) == 3 and not window.is_busy())
    window.open_paths([str(src)])   # doublons ignorés
    assert wait_until(lambda: not window.is_busy())
    assert len(window._items) == 3 and window.table.data_model.rowCount() == 3

    window.on_run()
    states = lambda: [r["state"] for r in window.table.data_model.rows()]  # noqa: E731
    assert wait_until(lambda: not window.is_busy() and "En attente" not in states())
    assert sorted(states()) == ["Terminé"] * 3
    assert (tmp_path / "src_Pressed" / "sub" / "b.jpg").exists()
    assert (tmp_path / "src_Pressed" / "c.json").read_text(encoding="utf-8") == '{"a":[1,2]}'
    assert "Gain total" in window.total_label.text()

    saved = window.prefs.get("image")
    assert saved["quality"] == 50
    window.on_run()   # tout est déjà traité : rien à refaire
    assert not window.is_busy()


def test_window_remove_and_clear(window, tmp_path):
    for n in range(3):
        save_noise_jpeg(tmp_path / f"{n}.jpg", size=(40, 40), seed=n)
    window.open_paths([str(tmp_path / f"{n}.jpg") for n in range(3)])
    assert wait_until(lambda: len(window._items) == 3 and not window.is_busy())
    window.table.selectRow(0)
    window._remove_selected()
    assert len(window._items) == 2 and window.table.data_model.rowCount() == 2
    window._clear_all()
    assert window._items == [] and window.table.data_model.rowCount() == 0


def test_window_in_place_requires_backup_dir(window, tmp_path, monkeypatch):
    from azimut.modules.press import window as window_module
    warnings: list[str] = []
    monkeypatch.setattr(window_module.QMessageBox, "warning", lambda *a, **k: warnings.append(a[1]))
    p = save_noise_jpeg(tmp_path / "a.jpg", size=(40, 40))
    window.open_paths([str(p)])
    assert wait_until(lambda: len(window._items) == 1 and not window.is_busy())
    window.chk_inplace.setChecked(True)
    window.on_run()
    assert warnings == ["Sauvegarde requise"] and not window.is_busy() and window._items[0].result is None
