"""Isolated temporary workspaces.

Every optimisation run gets its own ``%TEMP%/PixelPack/<prefix>-<uuid>/`` folder.
Source files are only ever *read*; everything PixelPack writes lives in here (or
in the final output archive).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(f"pixelpack.{__name__.rsplit('.', 1)[-1]}")

APP_TEMP_DIRNAME = "PixelPack"
_STALE_AGE_SECONDS = 24 * 60 * 60


def temp_root() -> Path:
    """Return (without creating) the PixelPack temp root directory."""
    return Path(tempfile.gettempdir()) / APP_TEMP_DIRNAME


def _rmtree(path: Path, attempts: int = 5) -> bool:
    """Best-effort recursive delete; Windows keeps files locked briefly."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == attempts - 1:
                logger.warning("无法删除临时目录：%s", path, exc_info=True)
                return False
            time.sleep(0.1 * (attempt + 1))
    return False


def purge_stale_workspaces() -> int:
    """Remove leftover workspaces from crashed runs. Returns how many were removed."""
    root = temp_root()
    if not root.is_dir():
        return 0

    removed = 0
    cutoff = time.time() - _STALE_AGE_SECONDS
    for child in root.iterdir():
        try:
            if not child.is_dir():
                continue
            if child.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        if _rmtree(child):
            removed += 1
    return removed


class TempWorkspace:
    """A per-run scratch directory that cleans itself up.

    Usage::

        with TempWorkspace("run") as ws:
            ws.subdir("images")
    """

    def __init__(self, prefix: str = "run", *, parent: Path | None = None) -> None:
        self.prefix = prefix
        self._parent = Path(parent) if parent is not None else temp_root()
        self.path = self._parent / f"{prefix}-{uuid.uuid4().hex}"
        self._created = False
        self._cleaned = False

    # -- lifecycle ---------------------------------------------------------
    def create(self) -> Path:
        """Create the workspace directory (idempotent) and return its path."""
        if not self._created:
            self.path.mkdir(parents=True, exist_ok=True)
            self._created = True
        return self.path

    def subdir(self, name: str) -> Path:
        """Create and return a named sub-directory inside the workspace."""
        target = self.create() / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def cleanup(self) -> bool:
        """Delete the workspace. Safe to call more than once."""
        if self._cleaned:
            return True
        self._cleaned = True
        if not self.path.exists():
            return True
        return _rmtree(self.path)

    def __enter__(self) -> "TempWorkspace":
        self.create()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def __fspath__(self) -> str:  # pragma: no cover - convenience
        return str(self.path)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TempWorkspace {self.path}>"
