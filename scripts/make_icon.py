"""Generate ``assets/pixelpack.ico`` from code.

Keeping the icon as a script rather than a binary blob means it can be
regenerated, reviewed and tweaked like any other source file.

    python scripts/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICO_PATH = ASSETS / "pixelpack.ico"
PNG_PATH = ASSETS / "pixelpack.png"

#: Windows picks the closest size; supplying the full set keeps the taskbar,
#: Alt-Tab and Explorer views all crisp.
SIZES = [16, 24, 32, 48, 64, 128, 256]

ACCENT_TOP = (26, 127, 212, 255)
ACCENT_BOTTOM = (12, 88, 150, 255)
PAPER = (255, 255, 255, 255)
PAPER_EDGE = (214, 232, 246, 255)
ZIPPER = (15, 108, 189, 255)
SHADOW = (0, 0, 0, 46)


def _vertical_gradient(size: int) -> Image.Image:
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        gradient.putpixel(
            (0, y),
            tuple(
                round(ACCENT_TOP[i] + (ACCENT_BOTTOM[i] - ACCENT_TOP[i]) * t)
                for i in range(4)
            ),
        )
    return gradient.resize((size, size), Image.Resampling.NEAREST)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return mask


def draw_icon(size: int) -> Image.Image:
    """A stack of photos going into a zip: two sheets plus a zipper seam."""
    scale = size / 256.0
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Background plate.
    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    plate.paste(_vertical_gradient(size), (0, 0))
    plate.putalpha(_rounded_mask(size, max(2, round(52 * scale))))
    canvas.alpha_composite(plate)

    draw = ImageDraw.Draw(canvas)

    def px(value: float) -> int:
        return round(value * scale)

    # Two offset "photos" so the icon reads as a stack.
    back_box = (px(66), px(44), px(196), px(150))
    draw.rounded_rectangle(
        (back_box[0] + px(6), back_box[1] + px(6), back_box[2] + px(6), back_box[3] + px(6)),
        radius=px(12),
        fill=SHADOW,
    )
    draw.rounded_rectangle(back_box, radius=px(12), fill=PAPER_EDGE)

    front_box = (px(52), px(62), px(204), px(180))
    draw.rounded_rectangle(
        (front_box[0] + px(5), front_box[1] + px(7), front_box[2] + px(5), front_box[3] + px(7)),
        radius=px(12),
        fill=SHADOW,
    )
    draw.rounded_rectangle(front_box, radius=px(12), fill=PAPER)

    # A tiny landscape inside the front photo.
    draw.ellipse(
        (px(74), px(84), px(102), px(112)),
        fill=(255, 196, 61, 255),
    )
    draw.polygon(
        [
            (px(66), px(166)),
            (px(112), px(112)),
            (px(146), px(150)),
            (px(168), px(126)),
            (px(196), px(166)),
        ],
        fill=(76, 176, 118, 255),
    )

    # The zipper seam, straight down the middle.
    seam_x = px(128)
    draw.line(
        [(seam_x, px(62)), (seam_x, px(180))],
        fill=ZIPPER,
        width=max(1, px(6)),
    )

    teeth = px(13)
    y = px(70)
    while y < px(178):
        draw.line(
            [(seam_x - teeth // 2, y), (seam_x + teeth // 2, y)],
            fill=PAPER,
            width=max(1, px(4)),
        )
        y += px(16)

    # Pull tab at the bottom of the seam.
    draw.rounded_rectangle(
        (seam_x - px(16), px(180), seam_x + px(16), px(208)),
        radius=px(7),
        fill=ZIPPER,
    )
    draw.ellipse(
        (seam_x - px(6), px(186), seam_x + px(6), px(198)),
        fill=PAPER,
    )

    return canvas


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)

    images = [draw_icon(size) for size in SIZES]
    images[-1].save(
        ICO_PATH,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=images[:-1],
    )
    images[-1].save(PNG_PATH, format="PNG")

    print(f"已生成 {ICO_PATH}  ({ICO_PATH.stat().st_size} 字节)")
    print(f"已生成 {PNG_PATH}  ({PNG_PATH.stat().st_size} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
