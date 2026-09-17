"""Byte-size parsing and formatting.

PixelPack defines the size units the way Windows Explorer does::

    1 KB = 1024 bytes
    1 MB = 1024 * 1024 bytes
    1 GB = 1024 * 1024 * 1024 bytes
"""

from __future__ import annotations

import re

KB = 1024
MB = 1024 * 1024
GB = 1024 * 1024 * 1024

_UNIT_FACTORS: dict[str, int] = {
    "": 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": KB,
    "kb": KB,
    "kib": KB,
    "m": MB,
    "mb": MB,
    "mib": MB,
    "g": GB,
    "gb": GB,
    "gib": GB,
}

_SIZE_RE = re.compile(r"^\s*(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>[a-zA-Z]*)\s*$")


class SizeParseError(ValueError):
    """Raised when a human readable size string cannot be understood."""


def parse_size(text: str) -> int:
    """Parse ``"20 MB"`` / ``"1.5GB"`` / ``"512"`` into a byte count.

    Raises:
        SizeParseError: if the text is not a recognised size.
    """
    if isinstance(text, (int, float)):
        value = float(text)
        if value < 0:
            raise SizeParseError("大小不能为负数。")
        return int(value)

    match = _SIZE_RE.match(str(text or ""))
    if not match:
        raise SizeParseError(f"无法识别的大小格式：{text!r}")

    number = float(match.group("number").replace(",", "."))
    unit = match.group("unit").lower()
    if unit not in _UNIT_FACTORS:
        raise SizeParseError(f"未知的大小单位：{match.group('unit')!r}")

    if number < 0:
        raise SizeParseError("大小不能为负数。")
    return int(number * _UNIT_FACTORS[unit])


def format_size(num_bytes: int | float, precision: int = 2) -> str:
    """Format a byte count as ``"19.82 MB"``.

    Values below one kilobyte are rendered in bytes so that tiny ZIP files do
    not all collapse to ``"0.00 MB"``.
    """
    value = float(num_bytes)
    negative = value < 0
    value = abs(value)

    if value < KB:
        text = f"{int(round(value))} B"
    elif value < MB:
        text = f"{value / KB:.{precision}f} KB"
    elif value < GB:
        text = f"{value / MB:.{precision}f} MB"
    else:
        text = f"{value / GB:.{precision}f} GB"

    return f"-{text}" if negative else text


def format_percent(ratio: float, precision: int = 2) -> str:
    """Format a 0..1 ratio as a percentage string."""
    return f"{ratio * 100:.{precision}f}%"


def megabytes(num_bytes: int) -> float:
    """Return *num_bytes* expressed in mebibytes."""
    return num_bytes / MB
