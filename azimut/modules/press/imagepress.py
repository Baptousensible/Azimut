"""Azimut Press — logique pure d'optimisation d'image (Pillow, sans Qt).

Le résultat est produit en mémoire : `compress_image` ne touche jamais au disque, ce qui
garantit qu'aucun fichier partiel n'est créé ; l'écriture atomique relève de l'appelant.
"""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

STATUS_DONE = "done"
STATUS_ALREADY = "already"
STATUS_ANIMATED = "animated"

FORMAT_KEEP = "keep"
OUTPUT_FORMATS: tuple[str, ...] = ("keep", "jpeg", "webp", "png", "avif")
OUTPUT_LABELS: dict[str, str] = {
    "keep": "Conserver le format",
    "jpeg": "JPEG",
    "webp": "WebP",
    "png": "PNG",
    "avif": "AVIF",
}

BASE_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif")

# Format Pillow selon l'extension (sert à « Conserver le format ») et extension par défaut.
_EXT_FORMAT: dict[str, str] = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF",
    ".bmp": "BMP", ".gif": "GIF", ".heic": "HEIF", ".heif": "HEIF", ".avif": "AVIF",
}
_FORMAT_SUFFIX: dict[str, str] = {
    "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "AVIF": ".avif", "TIFF": ".tif",
    "BMP": ".bmp", "GIF": ".gif", "HEIF": ".heic",
}
_CHOICE_FORMAT: dict[str, str] = {"jpeg": "JPEG", "webp": "WEBP", "png": "PNG", "avif": "AVIF"}
_EXIF_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "AVIF", "HEIF"})
_ICC_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "AVIF", "HEIF", "TIFF"})


class ImagePressError(Exception):
    """Image illisible ou format de sortie impossible."""


@dataclass
class ImageSettings:
    output_format: str = FORMAT_KEEP
    quality: int = 82
    max_dimension: int | None = None
    strip_metadata: bool = False
    reduce_colors: bool = False
    webp_lossless: bool = False
    keep_smallest: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "ImageSettings":
        """Reconstruit les réglages depuis les préférences, en tolérant toute valeur douteuse."""
        base = cls()
        if not isinstance(data, dict):
            return base

        def as_int(key: str, default: int | None) -> int | None:
            try:
                value = data.get(key, default)
                return None if value is None else int(value)
            except (TypeError, ValueError):
                return default

        fmt = data.get("output_format", base.output_format)
        quality = as_int("quality", base.quality) or base.quality
        max_dim = as_int("max_dimension", None)
        return cls(
            output_format=fmt if fmt in OUTPUT_FORMATS else base.output_format,
            quality=min(100, max(1, quality)),
            max_dimension=max_dim if max_dim and max_dim > 0 else None,
            strip_metadata=bool(data.get("strip_metadata", base.strip_metadata)),
            reduce_colors=bool(data.get("reduce_colors", base.reduce_colors)),
            webp_lossless=bool(data.get("webp_lossless", base.webp_lossless)),
            keep_smallest=bool(data.get("keep_smallest", base.keep_smallest)),
        )


@dataclass
class ImageResult:
    status: str                 # STATUS_DONE | STATUS_ALREADY | STATUS_ANIMATED
    data: bytes | None          # image à écrire ; None = copier l'original tel quel
    suffix: str                 # extension du fichier de sortie (celle de l'original si copie)
    size_in: int
    size_out: int
    notes: list[str] = field(default_factory=list)   # avertissements à journaliser


# --- Capacités de Pillow -----------------------------------------------------------------------

@lru_cache(maxsize=1)
def has_heif() -> bool:
    """True si pillow-heif est installé (il enregistre alors HEIC/HEIF dans Pillow)."""
    try:
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
        return True
    except Exception:  # noqa: BLE001 — module absent ou binaire incompatible
        return False


def can_write(fmt: str) -> bool:
    """True si Pillow sait écrire ce format (nom Pillow : « WEBP », « AVIF »…)."""
    has_heif()
    Image.init()
    return fmt in Image.SAVE


def can_open_avif() -> bool:
    Image.init()
    return "AVIF" in Image.OPEN


@lru_cache(maxsize=1)
def supported_extensions() -> tuple[str, ...]:
    """Extensions d'entrée utilisables (minuscules, avec le point)."""
    exts = list(BASE_EXTENSIONS)
    if has_heif():
        exts += [".heic", ".heif"]
    if can_open_avif():
        exts.append(".avif")
    return tuple(exts)


def format_availability() -> dict[str, tuple[bool, str]]:
    """Pour chaque format de sortie : (disponible, explication si indisponible)."""
    return {
        "keep": (True, ""),
        "jpeg": (True, ""),
        "png": (True, ""),
        "webp": (can_write("WEBP"), "Cette version de Pillow n'a pas été compilée avec le support WebP."),
        "avif": (
            can_write("AVIF"),
            "L'écriture AVIF n'est pas disponible dans cette version de Pillow "
            "(Pillow 11.3 ou plus récent, ou le module pillow-avif-plugin, est nécessaire).",
        ),
    }


# --- Utilitaires de pixels ---------------------------------------------------------------------

def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in img.info


def _flatten(img: Image.Image) -> Image.Image:
    """Aplatit la transparence sur fond blanc (JPEG ne gère pas l'alpha)."""
    rgba = img.convert("RGBA")
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.getchannel("A"))
    return bg


def _to_8bit(img: Image.Image) -> Image.Image:
    """Ramène un mode 16 bits / entier / flottant à 8 bits niveaux de gris."""
    if img.mode.startswith("I;16"):
        img = img.convert("I")
        img = img.point(lambda v: v * (1 / 256))
    return img.convert("L")


def _normalize_for_resize(img: Image.Image) -> Image.Image:
    """Palette → RVB(A) et 1 bit → gris : Pillow ne rééchantillonne pas ces modes avec LANCZOS."""
    if img.mode in ("P", "PA"):
        return img.convert("RGBA" if _has_alpha(img) else "RGB")
    if img.mode == "1":
        return img.convert("L")
    if "transparency" in img.info and img.mode in ("L", "RGB"):
        return img.convert("RGBA")
    return img


def _resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    try:
        return img.resize(size, Image.Resampling.LANCZOS)
    except (ValueError, OSError):
        return _to_8bit(img).resize(size, Image.Resampling.LANCZOS)


def _quantize(img: Image.Image) -> Image.Image:
    """Réduit à 256 couleurs (avec perte) ; les modes déjà économes sont laissés tels quels."""
    if img.mode == "RGBA":
        return img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    if img.mode == "RGB":
        return img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    return img


def _prepare(img: Image.Image, target: str) -> Image.Image:
    """Adapte le mode de l'image au format de sortie."""
    mode = img.mode
    exotic = mode.startswith("I;16") or mode in ("I", "F")
    if target == "JPEG":
        if mode in ("L", "RGB", "CMYK") and "transparency" not in img.info:
            return img
        if exotic:
            return _to_8bit(img)
        return _flatten(img) if _has_alpha(img) else img.convert("RGB")
    if target in ("WEBP", "AVIF", "HEIF"):
        if mode in ("RGB", "RGBA") and "transparency" not in img.info:
            return img
        if exotic:
            img = _to_8bit(img)
        return img.convert("RGBA" if _has_alpha(img) else "RGB")
    if target == "PNG":
        if mode in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16"):
            return img
        return img.convert("RGBA" if _has_alpha(img) else "RGB")
    if target == "BMP":
        if mode in ("1", "L", "P", "RGB", "RGBA"):
            return img
        return img.convert("RGBA" if _has_alpha(img) else "RGB")
    return img   # TIFF, GIF : Pillow gère les modes courants


# --- Détection et choix du format --------------------------------------------------------------

def _is_animated(img: Image.Image) -> bool:
    """GIF/WebP/PNG animés et TIFF multi-pages. Les MPO (photos de téléphone) sont traités comme du JPEG."""
    if (img.format or "").upper() == "MPO":
        return False
    return bool(getattr(img, "is_animated", False)) or getattr(img, "n_frames", 1) > 1


def _target_format(src: Path, settings: ImageSettings) -> str:
    if settings.output_format in _CHOICE_FORMAT:
        return _CHOICE_FORMAT[settings.output_format]
    fmt = _EXT_FORMAT.get(src.suffix.lower())
    if fmt is None:
        raise ImagePressError(f"extension non prise en charge : {src.suffix or '(aucune)'}")
    return fmt


def _target_suffix(src: Path, target: str) -> str:
    """Nom inchangé quand le format ne change pas ; sinon extension standard du nouveau format."""
    if _EXT_FORMAT.get(src.suffix.lower()) == target:
        return src.suffix
    return _FORMAT_SUFFIX[target]


def _encode(img: Image.Image, target: str, settings: ImageSettings, exif: Image.Exif | None,
            icc: bytes | None, notes: list[str]) -> bytes:
    q = settings.quality
    kwargs: dict[str, Any] = {}
    if target == "JPEG":
        kwargs.update(quality=q, optimize=True, progressive=True)
    elif target == "PNG":
        kwargs.update(optimize=True, compress_level=9)
    elif target == "WEBP":
        kwargs.update(quality=q, method=6, lossless=settings.webp_lossless)
    elif target in ("AVIF", "HEIF"):
        kwargs.update(quality=q)
    elif target == "TIFF":
        kwargs.update(compression="tiff_adobe_deflate")
    elif target == "GIF":
        kwargs.update(optimize=True)
    if icc and target in _ICC_FORMATS:
        kwargs["icc_profile"] = icc
    if exif is not None and len(exif) and target in _EXIF_FORMATS:
        kwargs["exif"] = exif
    try:
        return _save(img, target, kwargs)
    except (ValueError, OSError, TypeError, OverflowError):
        if "exif" not in kwargs:
            raise
        kwargs.pop("exif")   # EXIF trop volumineux ou incompatible : on réessaie sans
        notes.append("Données EXIF non conservées (incompatibles avec le format de sortie).")
        return _save(img, target, kwargs)


def _save(img: Image.Image, target: str, kwargs: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=target, **kwargs)
    return buf.getvalue()


# --- Point d'entrée ----------------------------------------------------------------------------

def compress_image(src: Path, settings: ImageSettings) -> ImageResult:
    """Optimise une image en mémoire. Lève ImagePressError si l'image est illisible."""
    size_in = src.stat().st_size
    has_heif()
    try:
        img = Image.open(src)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImagePressError(f"image illisible ({exc})") from exc
    with img:
        if _is_animated(img):
            return ImageResult(STATUS_ANIMATED, None, src.suffix, size_in, size_in)
        try:
            img.load()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ImagePressError(f"image corrompue ({exc})") from exc
        return _compress_loaded(img, src, size_in, settings)


def _compress_loaded(img: Image.Image, src: Path, size_in: int, settings: ImageSettings) -> ImageResult:
    target = _target_format(src, settings)
    if not can_write(target):
        raise ImagePressError(f"écriture {target} indisponible avec cette installation de Pillow")

    notes: list[str] = []
    src_mode = img.mode
    icc: bytes | None = img.info.get("icc_profile")
    exif: Image.Exif | None = img.getexif()
    has_meta = bool(icc) or len(exif) > 0 or "xmp" in img.info or "XML:com.adobe.xmp" in img.info

    work = img
    if settings.strip_metadata:
        # L'orientation est appliquée aux pixels avant de perdre l'EXIF qui la portait.
        work = ImageOps.exif_transpose(work)
        icc, exif = None, None

    was_palette = work.mode in ("P", "PA")
    w, h = work.size
    limit = settings.max_dimension
    if limit and max(w, h) > limit:   # jamais d'agrandissement
        scale = limit / max(w, h)
        new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
        work = _resize(_normalize_for_resize(work), new_size)
        if was_palette and target == "PNG":
            work = _quantize(work)

    work = _prepare(work, target)
    if settings.reduce_colors and target == "PNG":
        work = _quantize(work)
    if src_mode == "CMYK" and work.mode != "CMYK":
        icc = None   # le profil décrivait du CMJN, il ne convient plus à l'image RVB

    data = _encode(work, target, settings, exif, icc, notes)

    # Garde anti-grossissement. Exception : si l'utilisateur veut supprimer des métadonnées présentes,
    # recopier l'original les conserverait — on garde alors le résultat même s'il n'est pas plus petit.
    keeps_metadata_by_copy = settings.strip_metadata and has_meta
    if settings.keep_smallest and len(data) >= size_in and not keeps_metadata_by_copy:
        return ImageResult(STATUS_ALREADY, None, src.suffix, size_in, size_in, notes)
    return ImageResult(STATUS_DONE, data, _target_suffix(src, target), size_in, len(data), notes)
