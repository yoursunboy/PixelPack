"""ZIP creation, measurement and verification.

PixelPack always measures the *real* archive on disk — never an estimate. Two
choices keep that affordable across the dozens of candidates the search builds:

* files that are already compressed (JPEG, PNG, WebP, PDF, ...) are **stored**,
  because deflating them costs time and saves nothing;
* everything else is deflated at a moderate level.
"""

from __future__ import annotations

import logging
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..models.result import ArchiveError, OperationCancelled

logger = logging.getLogger(f"pixelpack.{__name__.rsplit('.', 1)[-1]}")

#: Already-compressed payloads: store them verbatim.
_STORED_SUFFIXES: frozenset[str] = frozenset(
    {
        # images
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".gif", ".avif", ".heic",
        # archives
        ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".lz4", ".cab",
        # media
        ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
        ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv",
        # office / pdf
        ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
        # misc
        ".jar", ".apk", ".woff", ".woff2",
    }
)

_COMPRESS_LEVEL = 6


@dataclass(slots=True)
class ArchiveEntry:
    """One member of the archive."""

    source: Path
    arcname: str

    def __post_init__(self) -> None:
        # ZIP entry names always use forward slashes.
        self.arcname = self.arcname.replace("\\", "/").lstrip("/")
        if not self.arcname:
            raise ValueError("ZIP 条目名不能为空。")


@dataclass(slots=True)
class ArchiveStats:
    path: Path
    size_bytes: int
    entry_count: int


def compress_type_for(arcname: str) -> int:
    """ZIP compression method to use for *arcname*."""
    suffix = Path(arcname).suffix.lower()
    if suffix in _STORED_SUFFIXES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def build_archive(
    entries: Sequence[ArchiveEntry],
    out_path: Path | str,
    *,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ArchiveStats:
    """Write a real ZIP to *out_path* and return its measured size.

    The archive is written from scratch on every call, so the returned size is
    always the true size of a genuine archive.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        try:
            out_path.unlink()
        except OSError as exc:  # pragma: no cover - defensive
            raise ArchiveError(f"无法覆盖已存在的文件：{out_path}") from exc

    total = len(entries)
    try:
        with zipfile.ZipFile(
            out_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=_COMPRESS_LEVEL,
            allowZip64=True,
        ) as archive:
            for index, entry in enumerate(entries, start=1):
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("用户已取消。")
                source = Path(entry.source)
                if not source.is_file():
                    raise ArchiveError(f"打包时找不到文件：{source}")
                method = compress_type_for(entry.arcname)
                if method == zipfile.ZIP_STORED:
                    archive.write(source, entry.arcname, compress_type=zipfile.ZIP_STORED)
                else:
                    archive.write(
                        source,
                        entry.arcname,
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=_COMPRESS_LEVEL,
                    )
                if on_progress is not None:
                    on_progress(index, total)
    except OperationCancelled:
        _safe_unlink(out_path)
        raise
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        _safe_unlink(out_path)
        raise ArchiveError(f"生成 ZIP 失败：{exc}") from exc

    try:
        size = out_path.stat().st_size
    except OSError as exc:  # pragma: no cover - defensive
        raise ArchiveError(f"无法读取 ZIP 大小：{exc}") from exc

    return ArchiveStats(path=out_path, size_bytes=size, entry_count=total)


def archive_size(path: Path | str) -> int:
    """Size on disk of an existing archive."""
    return Path(path).stat().st_size


def verify_archive(
    path: Path | str,
    expected_names: Iterable[str] | None = None,
    *,
    max_bytes: int | None = None,
) -> int:
    """Check that *path* is a valid, complete ZIP. Returns its size.

    Verifies, in order:

    1. the file exists and is a readable ZIP;
    2. every expected member is present and no name appears twice;
    3. ``ZipFile.testzip()`` reports no CRC failures;
    4. the size on disk does not exceed *max_bytes*, when given.

    Raises:
        ArchiveError: with a human readable reason for the first failure.
    """
    path = Path(path)
    if not path.is_file():
        raise ArchiveError("输出文件不存在。")

    size = path.stat().st_size
    problems: list[str] = []

    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            names = archive.namelist()

            duplicates = {n for n in names if names.count(n) > 1}
            if duplicates:
                problems.append(f"存在重复条目：{sorted(duplicates)[:3]}")

            if expected_names is not None:
                expected = set(expected_names)
                missing = expected.difference(names)
                if missing:
                    problems.append(f"缺少 {len(missing)} 个文件，例如 {sorted(missing)[:3]}")

            bad_member = archive.testzip()
            if bad_member is not None:
                problems.append(f"CRC 校验失败：{bad_member}")
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"ZIP 文件已损坏：{exc}") from exc
    except OSError as exc:
        raise ArchiveError(f"无法打开 ZIP 文件：{exc}") from exc

    if max_bytes is not None and size > max_bytes:
        problems.append("ZIP 大小超过了设定的上限。")

    if problems:
        raise ArchiveError("；".join(problems))

    return size


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensive
        logger.warning("无法删除损坏的输出文件：%s", path, exc_info=True)
