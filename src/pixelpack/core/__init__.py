"""Core algorithms: scanning, image processing, archiving and optimisation.

Nothing in this package imports Qt, which keeps the algorithms testable in
isolation from the GUI.
"""

from __future__ import annotations

from .archive import ArchiveEntry, ArchiveStats, build_archive, verify_archive
from .optimizer import Evaluation, Optimizer
from .scanner import IMAGE_SUFFIXES, scan_directory

__all__ = [
    "IMAGE_SUFFIXES",
    "ArchiveEntry",
    "ArchiveStats",
    "Evaluation",
    "Optimizer",
    "build_archive",
    "scan_directory",
    "verify_archive",
]
