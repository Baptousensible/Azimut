"""Préparation des images d'Azimut Convert (Pillow, pillow-heif et rawpy facultatifs).

ffmpeg ne décode ni les RAW, ni toujours le HEIC, et n'applique pas l'orientation EXIF : ces images sont
d'abord développées par Pillow dans un PNG temporaire, que ffmpeg encode ensuite. Les GIF animés et TIFF
multi-pages ne sont jamais convertis (une seule image serait conservée) : ils sont copiés tels quels.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from azimut.modules.convert.profiles import RAW_EXTS

HEIF_EXTS = frozenset({".heic", ".heif"})
MULTIFRAME_EXTS = frozenset({".gif", ".tif", ".tiff"})
ORIENTATION_TAG = 0x0112

_heif_state: bool | None = None


def heif_available() -> bool:
    """pillow-heif est installé (et enregistré auprès de Pillow)."""
    global _heif_state
    if _heif_state is None:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            _heif_state = True
        except Exception:  # noqa: BLE001 - paquet absent ou binaire incompatible
            _heif_state = False
    return _heif_state


def raw_available() -> bool:
    try:
        import rawpy  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def optional_support() -> dict[str, bool]:
    return {"pillow": pillow_available(), "heif": heif_available(), "raw": raw_available()}


def is_multiframe(path: Path) -> bool:
    """GIF animé ou TIFF multi-pages."""
    if path.suffix.lower() not in MULTIFRAME_EXTS or not pillow_available():
        return False
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(getattr(im, "n_frames", 1) or 1) > 1
    except Exception:  # noqa: BLE001 - fichier illisible : ffmpeg tranchera
        return False


def needs_pillow(path: Path) -> bool:
    """Vrai pour RAW, HEIC/HEIF (si pillow-heif est là), TIFF, ou orientation EXIF différente de 1."""
    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        return True
    if ext in HEIF_EXTS:
        return heif_available()
    if ext in {".tif", ".tiff"}:
        return pillow_available()
    if not pillow_available():
        return False
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.getexif().get(ORIENTATION_TAG, 1) != 1
    except Exception:  # noqa: BLE001
        return False


def prepare_png(path: Path, tmp_dir: Path | None = None) -> Path:
    """Développe `path` en PNG temporaire (à supprimer par l'appelant). RuntimeError si impossible."""
    from PIL import Image, ImageOps

    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        if not raw_available():
            raise RuntimeError("rawpy non installé (pip install rawpy) : RAW non pris en charge")
        import rawpy
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_color=rawpy.ColorSpace.sRGB, no_auto_bright=True)
        im = Image.fromarray(rgb)
    else:
        if ext in HEIF_EXTS and not heif_available():
            raise RuntimeError("pillow-heif non installé (pip install pillow-heif) : HEIC non pris en charge")
        with Image.open(path) as src:
            im = ImageOps.exif_transpose(src)
            im.load()
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGB")
    fd, tmp = tempfile.mkstemp(suffix=".png", dir=str(tmp_dir) if tmp_dir else None)
    os.close(fd)
    try:
        im.save(tmp, format="PNG")
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)
