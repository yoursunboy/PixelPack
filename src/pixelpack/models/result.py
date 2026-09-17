"""Result objects, progress updates and the package exception hierarchy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .settings import CompressionMode


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
class PixelPackError(Exception):
    """Base class for every error PixelPack raises on purpose."""


class OptimizationError(PixelPackError):
    """The optimisation could not be completed."""


class ImpossibleTargetError(OptimizationError):
    """No amount of image shrinking can reach the requested size."""


class ArchiveError(PixelPackError):
    """The produced ZIP failed verification."""


class OperationCancelled(PixelPackError):
    """The user cancelled the run."""


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ProgressUpdate:
    """A single progress notification emitted by the optimiser."""

    stage: str
    message: str
    fraction: float = 0.0
    target_bytes: int = 0
    current_zip_bytes: int = 0
    scale: float = 0.0
    round_index: int = 0
    total_rounds: int = 0


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ImageOutcome:
    """What happened to a single image."""

    rel_path: str
    arcname: str
    orig_width: int
    orig_height: int
    final_width: int
    final_height: int
    orig_bytes: int
    final_bytes: int
    encode_format: str
    fallback: bool = False
    #: The scale factor the optimiser asked for, before the "never upscale" and
    #: ``min_long_edge`` clamps were applied. Rendering the *original* at this
    #: scale reproduces exactly the bytes stored in the archive.
    requested_scale: float = 1.0

    @property
    def orig_long_edge(self) -> int:
        return max(self.orig_width, self.orig_height)

    @property
    def final_long_edge(self) -> int:
        return max(self.final_width, self.final_height)

    @property
    def scale(self) -> float:
        """Linear scale that was actually applied (long edge ratio)."""
        if self.orig_long_edge <= 0:
            return 1.0
        return self.final_long_edge / self.orig_long_edge

    @property
    def scale_percent(self) -> float:
        return self.scale * 100.0

    @property
    def bytes_saved(self) -> int:
        return max(0, self.orig_bytes - self.final_bytes)

    @property
    def orig_dimensions(self) -> str:
        return f"{self.orig_width}×{self.orig_height}"

    @property
    def final_dimensions(self) -> str:
        return f"{self.final_width}×{self.final_height}"


@dataclass(slots=True)
class OptimizationResult:
    """The outcome of a full optimisation run."""

    source_dir: Path
    output_path: Path
    target_bytes: int
    original_bytes: int
    final_bytes: int
    image_count: int
    file_count: int
    copied_original: bool
    scale: float
    rounds: int
    elapsed_seconds: float
    mode: CompressionMode = CompressionMode.BALANCED
    outcomes: list[ImageOutcome] = field(default_factory=list)
    verified: bool = True

    # -- derived numbers ---------------------------------------------------
    @property
    def utilization(self) -> float:
        """How much of the allowed size the final ZIP actually uses."""
        if self.target_bytes <= 0:
            return 0.0
        return self.final_bytes / self.target_bytes

    @property
    def utilization_percent(self) -> float:
        return self.utilization * 100.0

    @property
    def compression_ratio(self) -> float:
        """Final ZIP size divided by the original folder size."""
        if self.original_bytes <= 0:
            return 0.0
        return self.final_bytes / self.original_bytes

    @property
    def saved_bytes(self) -> int:
        return max(0, self.original_bytes - self.final_bytes)

    @property
    def headroom_bytes(self) -> int:
        return max(0, self.target_bytes - self.final_bytes)

    @property
    def avg_original_pixels(self) -> float:
        if not self.outcomes:
            return 0.0
        total = sum(o.orig_width * o.orig_height for o in self.outcomes)
        return total / len(self.outcomes)

    @property
    def avg_final_pixels(self) -> float:
        if not self.outcomes:
            return 0.0
        total = sum(o.final_width * o.final_height for o in self.outcomes)
        return total / len(self.outcomes)

    @property
    def avg_original_dimensions(self) -> str:
        return self._avg_dimensions(original=True)

    @property
    def avg_final_dimensions(self) -> str:
        return self._avg_dimensions(original=False)

    def _avg_dimensions(self, *, original: bool) -> str:
        if not self.outcomes:
            return "—"
        count = len(self.outcomes)
        if original:
            w = sum(o.orig_width for o in self.outcomes) / count
            h = sum(o.orig_height for o in self.outcomes) / count
        else:
            w = sum(o.final_width for o in self.outcomes) / count
            h = sum(o.final_height for o in self.outcomes) / count
        return f"{round(w):,}×{round(h):,}".replace(",", "")

    @property
    def has_fallbacks(self) -> bool:
        return any(o.fallback for o in self.outcomes)
