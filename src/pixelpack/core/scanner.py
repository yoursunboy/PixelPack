"""Directory scanning.

Walks a folder, classifies every file as image / non-image and reads image
headers (width, height, format, alpha) without decoding pixel data.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Sequence

from PIL import Image, UnidentifiedImageError

from ..models.image_info import ScanResult, SourceFile
from .image_processor import output_name_for

logger = logging.getLogger(f"pixelpack.{__name__.rsplit('.', 1)[-1]}")

#: File extensions PixelPack will try to decode. Everything else is copied
#: through to the ZIP untouched.
IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}
)

_ScanCallback = Callable[[int, int], None]


def _iter_files(root: Path, include_subdirs: bool) -> Iterable[Path]:
    """Yield files under *root* in a deterministic, sorted order."""
    if not include_subdirs:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_file():
                yield entry
        return

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort(key=str.lower)
        filenames.sort(key=str.lower)
        base = Path(dirpath)
        for name in filenames:
            yield base / name


def _relative_posix(path: Path, root: Path) -> str:
    """Path of *path* relative to *root*, always with ``/`` separators."""
    try:
        rel = path.relative_to(root)
    except ValueError:  # pragma: no cover - defensive
        rel = Path(path.name)
    return PurePosixPath(*rel.parts).as_posix()


def _probe_image(path: Path) -> tuple[int, int, str, bool]:
    """Read an image header. Returns ``(width, height, format, has_alpha)``."""
    with Image.open(path) as im:
        width, height = im.size
        fmt = (im.format or "").upper()
        has_alpha = "A" in im.getbands() or "transparency" in im.info
    return int(width), int(height), fmt, bool(has_alpha)


def _dedupe_output_names(files: Sequence[SourceFile]) -> None:
    """Make sure no two files claim the same ZIP entry name.

    Happens when a folder holds both ``logo.bmp`` and ``logo.png``: both would
    be stored as ``logo.png``.
    """
    used: set[str] = set()
    for source in files:
        if not source.is_image:
            source.output_name = source.rel_path
            continue

        name = output_name_for(source.rel_path, source.format)
        if name.lower() not in used:
            used.add(name.lower())
            source.output_name = name
            continue

        path = PurePosixPath(name)
        stem, suffix = path.stem, path.suffix
        counter = 1
        while True:
            candidate = str(path.with_name(f"{stem}_{counter}{suffix}"))
            if candidate.lower() not in used:
                used.add(candidate.lower())
                source.output_name = candidate
                logger.debug("输出重名，重命名为 %s", candidate)
                break
            counter += 1


def scan_directory(
    root: Path | str,
    *,
    include_subdirs: bool = True,
    exclude: Iterable[Path] = (),
    cancel: threading.Event | None = None,
    on_progress: _ScanCallback | None = None,
) -> ScanResult:
    """Inventory *root*.

    Args:
        root: Folder to scan.
        include_subdirs: Descend into sub-folders.
        exclude: Absolute paths that must be skipped (e.g. the output ZIP).
        cancel: Optional event; when set the scan stops early.
        on_progress: Called with ``(files_seen, total_files)``.

    Returns:
        A :class:`ScanResult` sorted by relative path.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"源文件夹不存在：{root}")

    excluded = {Path(p).resolve() for p in exclude}
    candidates = [p for p in _iter_files(root, include_subdirs) if p.resolve() not in excluded]

    total = len(candidates)
    files: list[SourceFile] = []

    for index, path in enumerate(candidates, start=1):
        if cancel is not None and cancel.is_set():
            break

        rel_path = _relative_posix(path, root)
        try:
            size_bytes = path.stat().st_size
        except OSError:
            logger.warning("无法读取文件信息：%s", path)
            if on_progress:
                on_progress(index, total)
            continue

        suffix = path.suffix.lower()
        source = SourceFile(path=path, rel_path=rel_path, size_bytes=size_bytes)

        if suffix in IMAGE_SUFFIXES:
            try:
                width, height, fmt, has_alpha = _probe_image(path)
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                # Not actually an image (or a corrupt one): treat it as an
                # opaque file so it still ends up in the archive.
                source.error = str(exc)
                logger.info("无法解析图片，将原样复制：%s (%s)", rel_path, exc)
            else:
                source.is_image = True
                source.width = width
                source.height = height
                source.format = fmt
                source.has_alpha = has_alpha

        files.append(source)
        if on_progress:
            on_progress(index, total)

    files.sort(key=lambda f: f.rel_path.lower())
    _dedupe_output_names(files)
    return ScanResult(root=root, files=files)
