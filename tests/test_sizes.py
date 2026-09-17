"""Size parsing and formatting."""

from __future__ import annotations

import pytest

from pixelpack.utils.sizes import (
    GB,
    KB,
    MB,
    SizeParseError,
    format_percent,
    format_size,
    parse_size,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20 MB", 20 * MB),
        ("20MB", 20 * MB),
        ("20 mb", 20 * MB),
        ("1 GB", GB),
        ("1.5 GB", int(1.5 * GB)),
        ("512 KB", 512 * KB),
        ("1024", 1024),
        ("1024 B", 1024),
        ("0.5MB", MB // 2),
        ("  10   MB  ", 10 * MB),
    ],
)
def test_parse_size_accepts_common_spellings(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "20 XB", "-5 MB", "MB"])
def test_parse_size_rejects_garbage(text):
    with pytest.raises(SizeParseError):
        parse_size(text)


def test_megabyte_is_binary():
    """The spec pins 1 MB to 1024 * 1024 bytes."""
    assert MB == 1024 * 1024
    assert parse_size("1 MB") == 1048576


def test_format_size_units():
    assert format_size(19.82 * MB) == "19.82 MB"
    assert format_size(1536) == "1.50 KB"
    assert format_size(512) == "512 B"
    assert format_size(2 * GB) == "2.00 GB"


def test_format_percent():
    assert format_percent(0.991) == "99.10%"
    assert format_percent(1.0, precision=0) == "100%"
