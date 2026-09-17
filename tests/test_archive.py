"""ZIP construction, measurement and verification."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pixelpack.core.archive import (
    ArchiveEntry,
    archive_size,
    build_archive,
    compress_type_for,
    verify_archive,
)
from pixelpack.models.result import ArchiveError, OperationCancelled
from tests.helpers import noise_bytes, noise_image, save_image


def test_build_archive_preserves_directory_structure(tmp_path: Path):
    source = tmp_path / "src" / "A"
    source.mkdir(parents=True)
    first = save_image(noise_image((40, 30), seed=1), source / "1.jpg")
    second = save_image(noise_image((40, 30), seed=2), tmp_path / "src" / "B" / "2.jpg")

    out = tmp_path / "out.zip"
    stats = build_archive(
        [ArchiveEntry(first, "A/1.jpg"), ArchiveEntry(second, "B/2.jpg")], out
    )

    assert stats.entry_count == 2
    with zipfile.ZipFile(out) as archive:
        assert set(archive.namelist()) == {"A/1.jpg", "B/2.jpg"}


def test_build_archive_handles_chinese_entry_names(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((40, 30), seed=1), root / "照片 目录" / "风景 001.jpg")
    out = tmp_path / "out.zip"

    build_archive([ArchiveEntry(file, "照片 目录/风景 001.jpg")], out)

    with zipfile.ZipFile(out) as archive:
        info = archive.infolist()[0]
        assert info.filename == "照片 目录/风景 001.jpg"
        # UTF-8 flag must be set so other tools read the name correctly.
        assert info.flag_bits & 0x800
        assert archive.read(info.filename) == file.read_bytes()


def test_measured_size_matches_the_file_on_disk(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((400, 300), seed=1), root / "a.jpg")
    out = tmp_path / "out.zip"

    stats = build_archive([ArchiveEntry(file, "a.jpg")], out)

    assert stats.size_bytes == out.stat().st_size == archive_size(out)


def test_non_image_files_are_copied_verbatim(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir(parents=True)
    text = root / "说明.txt"
    text.write_text("PixelPack 文档内容\n" * 100, encoding="utf-8")

    out = tmp_path / "out.zip"
    build_archive([ArchiveEntry(text, "说明.txt")], out)

    with zipfile.ZipFile(out) as archive:
        assert archive.read("说明.txt") == text.read_bytes()


def test_precompressed_formats_are_stored(tmp_path: Path):
    assert compress_type_for("a/photo.jpg") == zipfile.ZIP_STORED
    assert compress_type_for("a/photo.png") == zipfile.ZIP_STORED
    assert compress_type_for("a/photo.webp") == zipfile.ZIP_STORED
    assert compress_type_for("a/doc.pdf") == zipfile.ZIP_STORED
    assert compress_type_for("a/notes.txt") == zipfile.ZIP_DEFLATED
    assert compress_type_for("a/data.json") == zipfile.ZIP_DEFLATED


def test_build_archive_replaces_a_previous_file(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((40, 30), seed=1), root / "a.jpg")
    out = tmp_path / "out.zip"
    out.write_bytes(noise_bytes(200_000, seed=9))
    stale_size = out.stat().st_size

    stats = build_archive([ArchiveEntry(file, "a.jpg")], out)

    # Replaced outright, not appended to: the stale payload is gone.
    assert out.stat().st_size == stats.size_bytes < stale_size
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist() == ["a.jpg"]
        assert archive.read("a.jpg") == file.read_bytes()


def test_build_archive_fails_loudly_for_a_missing_source(tmp_path: Path):
    with pytest.raises(ArchiveError):
        build_archive([ArchiveEntry(tmp_path / "nope.jpg", "nope.jpg")], tmp_path / "out.zip")


def test_build_archive_can_be_cancelled(tmp_path: Path):
    import threading

    root = tmp_path / "src"
    files = [save_image(noise_image((80, 60), seed=i), root / f"{i}.jpg") for i in range(5)]
    entries = [ArchiveEntry(f, f"{i}.jpg") for i, f in enumerate(files)]

    cancel = threading.Event()
    cancel.set()
    out = tmp_path / "out.zip"

    with pytest.raises(OperationCancelled):
        build_archive(entries, out, cancel=cancel)

    assert not out.exists(), "取消后不能留下半成品 ZIP"


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
def test_verify_archive_accepts_a_good_archive(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((40, 30), seed=1), root / "a.jpg")
    out = tmp_path / "out.zip"
    build_archive([ArchiveEntry(file, "a.jpg")], out)

    assert verify_archive(out, ["a.jpg"]) == out.stat().st_size


def test_verify_archive_detects_a_missing_member(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((40, 30), seed=1), root / "a.jpg")
    out = tmp_path / "out.zip"
    build_archive([ArchiveEntry(file, "a.jpg")], out)

    with pytest.raises(ArchiveError, match="缺少"):
        verify_archive(out, ["a.jpg", "b.jpg"])


def test_verify_archive_detects_corruption(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((400, 300), seed=1), root / "a.jpg")
    out = tmp_path / "out.zip"
    build_archive([ArchiveEntry(file, "a.jpg")], out)

    # Flip bytes in the middle of the stored payload.
    data = bytearray(out.read_bytes())
    for index in range(len(data) // 2, len(data) // 2 + 32):
        data[index] ^= 0xFF
    out.write_bytes(bytes(data))

    with pytest.raises(ArchiveError):
        verify_archive(out, ["a.jpg"])


def test_verify_archive_rejects_a_non_zip(tmp_path: Path):
    out = tmp_path / "out.zip"
    out.write_bytes(b"definitely not a zip file")

    with pytest.raises(ArchiveError):
        verify_archive(out)


def test_verify_archive_rejects_a_missing_file(tmp_path: Path):
    with pytest.raises(ArchiveError):
        verify_archive(tmp_path / "nope.zip")


def test_verify_archive_enforces_the_size_limit(tmp_path: Path):
    root = tmp_path / "src"
    file = save_image(noise_image((400, 300), seed=1), root / "a.jpg")
    out = tmp_path / "out.zip"
    build_archive([ArchiveEntry(file, "a.jpg")], out)

    with pytest.raises(ArchiveError, match="上限"):
        verify_archive(out, ["a.jpg"], max_bytes=10)
