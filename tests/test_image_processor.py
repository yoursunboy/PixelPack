"""Image decoding, geometry rules, encoding and format handling."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from pixelpack.core.image_processor import (
    ImageProcessingError,
    encode_format_for,
    open_source,
    output_name_for,
    render_image,
    target_dimensions,
)
from pixelpack.core.scanner import scan_directory
from tests.helpers import noise_image, save_image


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("size", "scale", "min_long_edge", "expected"),
    [
        # Plain proportional scaling, driven by the long edge.
        ((4000, 3000), 0.5, 0, (2000, 1500)),
        ((3000, 4000), 0.5, 0, (1500, 2000)),
        # Never upscale.
        ((4000, 3000), 1.0, 0, (4000, 3000)),
        ((4000, 3000), 1.5, 0, (4000, 3000)),
        ((4000, 3000), 3.0, 0, (4000, 3000)),
        # 900x600 may shrink (long edge 900 > 800) but never below 800.
        ((900, 600), 0.1, 800, (800, 533)),
        # Already at/below the protection floor: the floor must not upscale it.
        ((640, 480), 0.1, 800, (640, 480)),
        ((800, 600), 0.5, 800, (800, 600)),
        # Protection disabled.
        ((640, 480), 0.5, 0, (320, 240)),
    ],
)
def test_target_dimensions(size, scale, min_long_edge, expected):
    assert target_dimensions(*size, scale, min_long_edge) == expected


def test_target_dimensions_never_returns_zero():
    assert target_dimensions(10000, 3, 0.0001, 0) == (1, 1)


def test_target_dimensions_preserves_aspect_ratio():
    width, height = target_dimensions(6000, 4000, 0.42, 0)
    assert abs((width / height) - 1.5) < 0.01


# --------------------------------------------------------------------------
# Format mapping
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("pil_format", "expected"),
    [
        ("JPEG", "JPEG"),
        ("MPO", "JPEG"),
        ("PNG", "PNG"),
        ("WEBP", "WEBP"),
        ("BMP", "PNG"),
        ("TIFF", "PNG"),
    ],
)
def test_encode_format_for(pil_format, expected):
    assert encode_format_for(pil_format) == expected


@pytest.mark.parametrize(
    ("rel_path", "fmt", "expected"),
    [
        ("a/b.jpg", "JPEG", "a/b.jpg"),
        ("a/b.png", "PNG", "a/b.png"),
        ("a/b.webp", "WEBP", "a/b.webp"),
        ("a/b.bmp", "BMP", "a/b.png"),
        ("a/b.tiff", "TIFF", "a/b.png"),
    ],
)
def test_output_name_for(rel_path, fmt, expected):
    assert output_name_for(rel_path, fmt) == expected


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _source_for(root: Path, name: str):
    return next(f for f in scan_directory(root).files if f.rel_path == name)


def test_render_produces_smaller_image_at_lower_scale(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((800, 600), seed=1), root / "a.jpg", quality=95)
    source = _source_for(root, "a.jpg")

    full = render_image(source, 1.0, quality=88)
    half = render_image(source, 0.5, quality=88)

    assert (full.width, full.height) == (800, 600)
    assert (half.width, half.height) == (400, 300)
    assert len(half.data) < len(full.data)


def test_render_never_upscales(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((300, 200), seed=1), root / "a.jpg", quality=95)
    source = _source_for(root, "a.jpg")

    rendered = render_image(source, 4.0, quality=88)

    assert (rendered.width, rendered.height) == (300, 200)


def test_small_image_is_protected(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((900, 600), seed=1), root / "small.jpg", quality=95)
    source = _source_for(root, "small.jpg")

    rendered = render_image(source, 0.2, min_long_edge=800, quality=88)

    assert max(rendered.width, rendered.height) == 800
    assert (rendered.width, rendered.height) == (800, 533)


def test_exif_rotation_is_applied_and_dimensions_swap(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir(parents=True)
    image = noise_image((400, 200), seed=1)
    exif = Image.Exif()
    exif[0x0112] = 6  # stored landscape, displayed portrait
    image.save(root / "rotated.jpg", exif=exif)
    source = _source_for(root, "rotated.jpg")

    rendered = render_image(source, 1.0, quality=88)

    assert (rendered.orig_width, rendered.orig_height) == (200, 400)
    assert (rendered.width, rendered.height) == (200, 400)


def test_exif_rotation_is_baked_out_of_the_orientation_tag(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir(parents=True)
    image = noise_image((400, 200), seed=1)
    exif = Image.Exif()
    exif[0x0112] = 6
    image.save(root / "rotated.jpg", exif=exif)
    source = _source_for(root, "rotated.jpg")

    rendered = render_image(source, 1.0, keep_exif=True, quality=88)
    reopened = Image.open(io.BytesIO(rendered.data))

    # The pixels are already rotated, so the tag must not rotate them again.
    assert reopened.size == (200, 400)
    assert reopened.getexif().get(0x0112, 1) in (1, None)


def test_transparency_is_preserved_for_png(transparency_folder: Path):
    source = _source_for(transparency_folder, "透明 标志.png")

    rendered = render_image(source, 0.5, quality=88)
    reopened = Image.open(io.BytesIO(rendered.data))

    assert rendered.encode_format == "PNG"
    assert reopened.mode in ("RGBA", "LA", "P")
    assert reopened.convert("RGBA").getpixel((0, 0))[3] == 0


def test_transparency_is_preserved_for_webp(transparency_folder: Path):
    source = _source_for(transparency_folder, "透明 方块.webp")

    rendered = render_image(source, 0.5, quality=88)
    reopened = Image.open(io.BytesIO(rendered.data))

    assert rendered.encode_format == "WEBP"
    assert "A" in reopened.getbands()
    assert reopened.convert("RGBA").getpixel((0, 0))[3] == 0


def test_png_stays_png_and_bmp_becomes_png(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((200, 150), seed=1), root / "a.png")
    save_image(noise_image((200, 150), seed=2), root / "b.bmp")

    png = render_image(_source_for(root, "a.png"), 0.5)
    bmp = render_image(_source_for(root, "b.bmp"), 0.5)

    assert png.encode_format == "PNG"
    assert bmp.encode_format == "PNG"
    assert bmp.suffix == ".png"


def test_transparent_png_is_not_flattened_onto_jpeg(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir(parents=True)
    Image.new("RGBA", (200, 200), (255, 0, 0, 128)).save(root / "a.png")

    rendered = render_image(_source_for(root, "a.png"), 0.5)

    assert rendered.encode_format == "PNG"


def test_jpeg_output_has_no_alpha_channel(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((200, 150), seed=3), root / "b.jpg", quality=95)

    jpeg = render_image(_source_for(root, "b.jpg"), 0.5)
    reopened = Image.open(io.BytesIO(jpeg.data))

    assert reopened.mode == "RGB"


@pytest.mark.parametrize(
    ("quality", "expect_smaller"),
    [(92, False), (82, True)],
)
def test_quality_controls_output_size(tmp_path: Path, quality, expect_smaller):
    root = tmp_path / "src"
    save_image(noise_image((500, 400), seed=1), root / "a.jpg", quality=95)
    source = _source_for(root, "a.jpg")

    baseline = len(render_image(source, 1.0, quality=88).data)
    candidate = len(render_image(source, 1.0, quality=quality).data)

    if expect_smaller:
        assert candidate < baseline
    else:
        assert candidate > baseline


def test_render_is_deterministic(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((400, 300), seed=1), root / "a.jpg", quality=95)
    source = _source_for(root, "a.jpg")

    assert render_image(source, 0.6).data == render_image(source, 0.6).data


def test_open_source_reports_a_useful_error_for_garbage(tmp_path: Path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")

    with pytest.raises(ImageProcessingError):
        open_source(bad)


def test_source_files_are_never_modified(tmp_path: Path):
    root = tmp_path / "src"
    save_image(noise_image((400, 300), seed=1), root / "a.jpg", quality=95)
    target = root / "a.jpg"
    before = (target.stat().st_size, target.read_bytes())

    render_image(_source_for(root, "a.jpg"), 0.3)

    assert (target.stat().st_size, target.read_bytes()) == before
