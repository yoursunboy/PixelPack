"""Helpers shared by the test modules.

The synthetic images are pseudo-random noise on purpose: noise is
incompressible, so an encoded image's size tracks its pixel count closely.
That makes the optimiser's behaviour predictable and the assertions meaningful.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

from pixelpack.models.settings import CompressionMode, OptimizationSettings

FORMAT_BY_SUFFIX: dict[str, str] = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}

_CHANNELS = {"L": 1, "RGB": 3, "RGBA": 4}


def noise_image(size: tuple[int, int], mode: str = "RGB", seed: int = 0) -> Image.Image:
    """A deterministic, incompressible test image."""
    width, height = size
    rng = random.Random(seed)
    data = rng.randbytes(width * height * _CHANNELS[mode])
    return Image.frombytes(mode, size, data)


def noise_bytes(size: int, seed: int = 0) -> bytes:
    """Deterministic random bytes that deflate cannot shrink.

    Handy for "a non-image file that cannot be squeezed into the budget" — a
    repeating pattern would compress away and the test would pass for the
    wrong reason.
    """
    return random.Random(seed).randbytes(size)


def save_image(image: Image.Image, path: Path, **kwargs) -> Path:
    """Save *image* to *path*, choosing the format from the suffix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = FORMAT_BY_SUFFIX[path.suffix.lower()]
    if fmt == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.save(path, fmt, **kwargs)
    return path


def make_settings(
    source_dir: Path,
    target_bytes: int,
    output_path: Path | None = None,
    **overrides,
) -> OptimizationSettings:
    """Settings with fast defaults, suitable for tests."""
    defaults = dict(
        mode=CompressionMode.BALANCED,
        include_subdirs=True,
        keep_exif=True,
        # Small enough that the fixture images are not all pinned to the
        # protection floor; the floor itself is covered by its own tests.
        min_long_edge=100,
        precision=4,
        max_refine_rounds=12,
        workers=2,
    )
    defaults.update(overrides)
    return OptimizationSettings(
        source_dir=source_dir,
        target_bytes=target_bytes,
        output_path=output_path or (source_dir.parent / f"{source_dir.name}_optimized.zip"),
        **defaults,
    ).validated()


def folder_fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    """Map every file in *root* to ``(size, mtime_ns)`` for change detection."""
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
