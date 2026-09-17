"""Shared fixtures: small synthetic photo folders."""

from __future__ import annotations

import os
from pathlib import Path

# Must be set before anything imports PySide6: the Qt widget tests build real
# windows, and a headless CI box has no display to put them on.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from pixelpack.utils.sizes import MB  # noqa: E402
from tests.helpers import noise_image, save_image  # noqa: E402


@pytest.fixture
def photo_folder(tmp_path: Path) -> Path:
    """A small folder mixing formats, sub-folders and non-ASCII names."""
    root = tmp_path / "照片 素材"
    (root / "子目录 A" / "更深一层").mkdir(parents=True)

    save_image(noise_image((600, 400), seed=1), root / "001 风景.jpg", quality=95)
    save_image(noise_image((560, 420), seed=2), root / "002 人像.jpg", quality=95)
    save_image(noise_image((640, 480), seed=3), root / "子目录 A" / "003 photo.jpg", quality=95)
    save_image(noise_image((512, 512), seed=4), root / "子目录 A" / "更深一层" / "004.jpg", quality=95)
    save_image(noise_image((500, 400), seed=5), root / "005 图.png")
    save_image(noise_image((480, 360), seed=6), root / "子目录 A" / "006.webp", quality=95)

    return root


@pytest.fixture
def transparency_folder(tmp_path: Path) -> Path:
    """Transparent PNG and WebP images with a noisy opaque centre.

    The fully transparent border survives downscaling, so "did we keep the
    alpha channel" can be checked on a corner pixel.
    """
    root = tmp_path / "透明图"
    root.mkdir()

    rgba = Image.new("RGBA", (600, 400), (0, 0, 0, 0))
    opaque = noise_image((400, 200), seed=7).convert("RGBA")
    opaque.putalpha(255)
    rgba.paste(opaque, (100, 100))
    rgba.save(root / "透明 标志.png")

    webp = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    inner = noise_image((300, 300), seed=8).convert("RGBA")
    inner.putalpha(255)
    webp.paste(inner, (100, 100))
    webp.save(root / "透明 方块.webp", quality=95)

    return root


@pytest.fixture
def mb() -> int:
    return MB
