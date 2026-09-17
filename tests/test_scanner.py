"""Directory scanning: classification, dimensions, Unicode paths."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pixelpack.core.scanner import IMAGE_SUFFIXES, scan_directory
from tests.helpers import noise_image, save_image


def test_scans_all_supported_formats(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((40, 30), seed=1), root / "a.jpg")
    save_image(noise_image((40, 30), seed=2), root / "b.jpeg")
    save_image(noise_image((40, 30), seed=3), root / "c.png")
    save_image(noise_image((40, 30), seed=4), root / "d.webp")
    save_image(noise_image((40, 30), seed=5), root / "e.bmp")
    save_image(noise_image((40, 30), seed=6), root / "f.tiff")

    scan = scan_directory(root)

    assert scan.image_count == 6
    assert {f.suffix for f in scan.image_files} == {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".tiff",
    }
    assert all(f.width == 40 and f.height == 30 for f in scan.image_files)
    assert all(f.size_bytes > 0 for f in scan.files)


def test_scans_chinese_and_unicode_names_and_nested_dirs(photo_folder: Path):
    scan = scan_directory(photo_folder)

    rel_paths = {f.rel_path for f in scan.files}
    assert "001 风景.jpg" in rel_paths
    assert "子目录 A/003 photo.jpg" in rel_paths
    # Multi-level nesting keeps its structure, with forward slashes.
    assert "子目录 A/更深一层/004.jpg" in rel_paths
    assert all("\\" not in rel for rel in rel_paths)
    assert scan.image_count == 6


def test_include_subdirs_can_be_disabled(photo_folder: Path):
    scan = scan_directory(photo_folder, include_subdirs=False)

    assert all("/" not in f.rel_path for f in scan.files)
    assert scan.image_count == 3  # 001, 002, 005 only


def test_non_image_files_are_kept_and_classified(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((40, 30), seed=1), root / "photo.jpg")
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    (root / "data.json").write_text('{"k": 1}', encoding="utf-8")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4 not really")

    scan = scan_directory(root)

    assert scan.image_count == 1
    assert {f.rel_path for f in scan.non_image_files} == {"readme.txt", "data.json", "doc.pdf"}
    assert scan.non_image_bytes > 0


def test_corrupt_image_is_treated_as_opaque_file(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "broken.jpg").write_bytes(b"this is definitely not a jpeg")

    scan = scan_directory(root)

    assert scan.image_count == 0
    assert scan.file_count == 1
    assert scan.non_image_files[0].rel_path == "broken.jpg"


def test_output_name_normalises_unwritable_formats(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((40, 30), seed=1), root / "photo.bmp")
    save_image(noise_image((40, 30), seed=2), root / "scan.tiff")
    save_image(noise_image((40, 30), seed=3), root / "keep.png")
    save_image(noise_image((40, 30), seed=4), root / "keep.jpg")

    scan = scan_directory(root)
    names = {f.rel_path: f.output_name for f in scan.image_files}

    assert names["photo.bmp"] == "photo.png"
    assert names["scan.tiff"] == "scan.png"
    assert names["keep.png"] == "keep.png"
    assert names["keep.jpg"] == "keep.jpg"


def test_colliding_output_names_are_deduplicated(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((40, 30), seed=1), root / "logo.bmp")
    save_image(noise_image((40, 30), seed=2), root / "logo.png")

    scan = scan_directory(root)
    names = sorted(f.output_name for f in scan.image_files)

    assert len(set(names)) == 2, "两个文件不能占用同一个 ZIP 条目名"
    assert "logo.png" in names


def test_excluded_paths_are_skipped(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((40, 30), seed=1), root / "photo.jpg")
    output = root / "photo_optimized.zip"
    output.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    scan = scan_directory(root, exclude=[output])

    assert {f.rel_path for f in scan.files} == {"photo.jpg"}


def test_scanner_records_exif_rotated_dimensions_as_stored(tmp_path: Path):
    """The scanner reports the stored pixel grid; rotation is applied later."""
    root = tmp_path / "src"
    root.mkdir()
    image = noise_image((400, 200), seed=1)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW on display
    image.save(root / "rotated.jpg", exif=exif)

    scan = scan_directory(root)
    source = scan.image_files[0]

    assert (source.width, source.height) == (400, 200)
    assert source.format == "JPEG"


def test_empty_folder_returns_empty_scan(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()

    scan = scan_directory(root)

    assert scan.file_count == 0
    assert scan.total_bytes == 0


def test_image_suffixes_cover_the_spec():
    assert {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} <= IMAGE_SUFFIXES
