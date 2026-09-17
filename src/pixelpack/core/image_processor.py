"""Decoding, resizing and re-encoding a single image.

Two rules drive everything in this module:

* **Never upscale.** The applied factor is always ``<= 1.0``.
* **Never shrink a small image further.** An image whose long edge is already
  at or below ``min_long_edge`` keeps its own size (see :func:`target_dimensions`).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PIL import Image, ImageFile, ImageOps

from ..models.image_info import SourceFile
from ..models.result import PixelPackError

logger = logging.getLogger(f"pixelpack.{__name__.rsplit('.', 1)[-1]}")

# Slightly damaged JPEGs are common in real photo folders; decode what we can
# instead of failing the whole run.
ImageFile.LOAD_TRUNCATED_IMAGES = True

LANCZOS = Image.Resampling.LANCZOS

#: Pillow format names that PixelPack can write back out.
_JPEG_FORMATS = frozenset({"JPEG", "MPO", "JPE"})

#: Pillow modes PNG can store without a conversion.
_PNG_MODES = frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16", "I;16B"})


class ImageProcessingError(PixelPackError):
    """A single image could not be decoded, resized or encoded."""


@dataclass(slots=True)
class RenderedImage:
    """An encoded image, ready to be written into the archive."""

    data: bytes
    width: int
    height: int
    encode_format: str
    suffix: str
    orig_width: int
    orig_height: int


# --------------------------------------------------------------------------
# Format / naming helpers
# --------------------------------------------------------------------------
def encode_format_for(pil_format: str) -> str:
    """Map a Pillow format name onto the format PixelPack writes."""
    upper = (pil_format or "").upper()
    if upper in _JPEG_FORMATS:
        return "JPEG"
    if upper == "PNG":
        return "PNG"
    if upper == "WEBP":
        return "WEBP"
    # BMP, TIFF, TGA, ... cannot be written back with the guarantees we need
    # (alpha, losslessness, size), so they become PNG.
    return "PNG"


def suffix_for(encode_format: str, source_suffix: str) -> str:
    """File extension to use for *encode_format*."""
    suffix = (source_suffix or "").lower()
    if encode_format == "JPEG":
        return suffix if suffix in {".jpg", ".jpeg", ".jpe", ".jfif"} else ".jpg"
    if encode_format == "WEBP":
        return ".webp"
    return ".png"


def output_name_for(rel_path: str, pil_format: str) -> str:
    """ZIP entry name for an image, accounting for format conversion."""
    path = PurePosixPath(rel_path)
    suffix = suffix_for(encode_format_for(pil_format), path.suffix)
    if path.suffix.lower() == suffix:
        return rel_path
    return str(path.with_suffix(suffix))


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
def target_dimensions(
    width: int,
    height: int,
    scale: float,
    min_long_edge: int = 0,
) -> tuple[int, int]:
    """Size to resize ``width x height`` to.

    The long edge is scaled by *scale*, then clamped so that it never grows
    past the original and never drops below ``min_long_edge`` (the "small image
    protection" floor). Scaling is aspect preserving and rounding happens once,
    on the final pixel counts.
    """
    if width <= 0 or height <= 0:
        return max(1, width), max(1, height)

    long_edge = max(width, height)
    if long_edge <= 0:
        return width, height

    # ``min(floor, long_edge)`` means an image that is already small keeps its
    # own long edge: the protection can never cause an upscale.
    floor_long = min(max(0, int(min_long_edge)), long_edge) if min_long_edge else 0

    wanted = long_edge * float(scale)
    target_long = max(wanted, float(floor_long))
    target_long = min(target_long, float(long_edge))
    target_long = max(1.0, target_long)

    if target_long >= long_edge:
        return width, height

    factor = target_long / long_edge
    new_width = max(1, int(round(width * factor)))
    new_height = max(1, int(round(height * factor)))
    return new_width, new_height


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------
def open_source(path: Path, cache=None) -> Image.Image:
    """Decode *path* and apply EXIF rotation.

    Returns an image that is fully detached from the file handle, so the
    caller may close the file immediately. A cache, when supplied, is consulted
    first: re-decoding every source for every optimisation round would dominate
    the runtime.
    """
    if cache is not None:
        cached = cache.get_decoded(path)
        if cached is not None:
            return cached

    try:
        with Image.open(path) as raw:
            # ``exif_transpose`` always returns a new, detached image (a copy
            # when no rotation was needed), including for 90°/180°/270°.
            image = ImageOps.exif_transpose(raw)
            if image is None:  # pragma: no cover - very old Pillow
                raw.load()
                image = raw.copy()
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        raise ImageProcessingError(f"无法读取图片 {path.name}：{exc}") from exc

    if cache is not None:
        cache.put_decoded(path, image)
    return image


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def _prepare_mode(image: Image.Image, encode_format: str) -> Image.Image:
    """Coerce *image* into a mode the target encoder accepts.

    Transparency is preserved for PNG and WebP. JPEG has no alpha channel, so
    RGBA sources are composited onto white rather than silently dropped.
    """
    if encode_format == "JPEG":
        if image.mode == "P" and "transparency" in image.info:
            rgba = image.convert("RGBA")
            return _flatten_on_white(rgba)
        if image.mode in ("RGBA", "LA", "PA"):
            return _flatten_on_white(image.convert("RGBA"))
        if image.mode not in ("RGB", "L"):
            return image.convert("RGB")
        return image

    if encode_format == "PNG":
        if image.mode in _PNG_MODES:
            if image.mode == "P" and "transparency" in image.info:
                return image.convert("RGBA")
            return image if image.mode != "P" else image.convert("RGB")
        return image.convert("RGBA" if _has_alpha(image) else "RGB")

    if encode_format == "WEBP":
        if image.mode in ("RGB", "RGBA"):
            return image
        if image.mode in ("L", "LA"):
            return image.convert("RGBA" if image.mode == "LA" else "RGB")
        return image.convert("RGBA" if _has_alpha(image) else "RGB")

    return image.convert("RGBA" if _has_alpha(image) else "RGB")


def _flatten_on_white(rgba: Image.Image) -> Image.Image:
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------
def _encode(image: Image.Image, encode_format: str, quality: int, exif: bytes | None) -> bytes:
    buffer = io.BytesIO()

    if encode_format == "JPEG":
        kwargs: dict = {"quality": int(quality), "optimize": True}
        if exif:
            kwargs["exif"] = exif
        try:
            image.save(buffer, "JPEG", **kwargs)
        except (OSError, ValueError):
            kwargs.pop("exif", None)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", **kwargs)

    elif encode_format == "WEBP":
        kwargs = {"quality": int(quality), "method": 4}
        if exif:
            kwargs["exif"] = exif
        try:
            image.save(buffer, "WEBP", **kwargs)
        except (OSError, ValueError):
            kwargs.pop("exif", None)
            buffer = io.BytesIO()
            image.save(buffer, "WEBP", **kwargs)

    else:  # PNG
        kwargs = {"optimize": False, "compress_level": 6}
        if exif:
            kwargs["exif"] = exif
        try:
            image.save(buffer, "PNG", **kwargs)
        except (OSError, ValueError):
            kwargs.pop("exif", None)
            buffer = io.BytesIO()
            image.save(buffer, "PNG", **kwargs)

    return buffer.getvalue()


def render_image(
    source: SourceFile,
    scale: float,
    *,
    quality: int = 88,
    min_long_edge: int = 0,
    keep_exif: bool = True,
    cache=None,
) -> RenderedImage:
    """Decode, resize (never upscale) and re-encode *source* at *scale*.

    The source file is only ever read. Raises :class:`ImageProcessingError` if
    the image cannot be processed.
    """
    encode_format = encode_format_for(source.format)
    suffix = suffix_for(encode_format, source.path.suffix)

    image = open_source(source.path, cache)
    orig_width, orig_height = image.size

    exif_bytes: bytes | None = None
    if keep_exif:
        raw_exif = image.info.get("exif")
        if raw_exif:
            exif_bytes = bytes(raw_exif)

    target_width, target_height = target_dimensions(
        orig_width, orig_height, scale, min_long_edge
    )

    prepared = _prepare_mode(image, encode_format)
    if (target_width, target_height) != prepared.size:
        prepared = prepared.resize((target_width, target_height), LANCZOS)

    try:
        data = _encode(prepared, encode_format, quality, exif_bytes)
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        raise ImageProcessingError(f"无法编码图片 {source.rel_path}：{exc}") from exc

    return RenderedImage(
        data=data,
        width=prepared.width,
        height=prepared.height,
        encode_format=encode_format,
        suffix=suffix,
        orig_width=orig_width,
        orig_height=orig_height,
    )
