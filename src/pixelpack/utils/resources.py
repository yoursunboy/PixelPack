"""Locating files that ship inside the package.

A frozen build does not run from a source checkout. PyInstaller unpacks the
bundle into a temporary directory and mirrors the package tree inside it, so
``pixelpack/assets`` sits in the same place relative to this module either way.
One lookup therefore works for ``python -m pixelpack`` and for
``PixelPack.exe``, with no path configured and no external file to ship
alongside the executable.
"""

from __future__ import annotations

from pathlib import Path

ASSETS_DIR_NAME = "assets"


def assets_dir() -> Path:
    """The ``pixelpack/assets`` directory, in a checkout or in a bundle.

    ``utils/resources.py`` -> ``utils`` -> ``pixelpack``, then into ``assets``.
    PyInstaller is given the same relative destination (``pixelpack/assets``)
    when it collects the package, so this one expression is correct both for
    ``python -m pixelpack`` and for the frozen ``PixelPack.exe``.
    """
    return Path(__file__).resolve().parent.parent / ASSETS_DIR_NAME


def asset_path(name: str) -> Path | None:
    """The path of a bundled asset, or ``None`` when it is not there.

    Callers get ``None`` rather than an exception so a build that somehow
    dropped the asset degrades to a missing picture instead of a crash.
    """
    candidate = assets_dir() / name
    return candidate if candidate.is_file() else None
