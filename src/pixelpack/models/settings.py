"""User-configurable settings for one optimisation run."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CompressionMode(str, Enum):
    """How aggressively images are re-encoded."""

    QUALITY = "quality"
    BALANCED = "balanced"
    SIZE = "size"

    @property
    def label(self) -> str:
        return _MODE_LABELS[self]

    @property
    def jpeg_quality(self) -> int:
        return MODE_QUALITIES[self]

    @property
    def description(self) -> str:
        return _MODE_DESCRIPTIONS[self]


_MODE_LABELS: dict[CompressionMode, str] = {
    CompressionMode.QUALITY: "画质优先",
    CompressionMode.BALANCED: "平衡",
    CompressionMode.SIZE: "体积优先",
}

_MODE_DESCRIPTIONS: dict[CompressionMode, str] = {
    CompressionMode.QUALITY: "JPEG/WebP 质量 92，优先保留画质",
    CompressionMode.BALANCED: "JPEG/WebP 质量 88，画质与体积兼顾",
    CompressionMode.SIZE: "JPEG/WebP 质量 82，优先缩小体积",
}

MODE_QUALITIES: dict[CompressionMode, int] = {
    CompressionMode.QUALITY: 92,
    CompressionMode.BALANCED: 88,
    CompressionMode.SIZE: 82,
}

#: Image formats whose long edge is smaller than the configured minimum are
#: never shrunk below their own long edge.
DEFAULT_MIN_LONG_EDGE = 800

#: Hard floor for the search, used when ``min_long_edge`` is disabled (0).
ABSOLUTE_MIN_SCALE = 0.02


def default_worker_count() -> int:
    """``min(4, os.cpu_count() or 1)`` as required by the spec."""
    return max(1, min(4, os.cpu_count() or 1))


def default_output_path(source_dir: Path) -> Path:
    """``<source folder name>_optimized.zip`` next to the source folder."""
    source = Path(source_dir)
    name = source.name or "archive"
    return source.parent / f"{name}_optimized.zip"


@dataclass(slots=True)
class OptimizationSettings:
    """Everything the optimiser needs to know."""

    source_dir: Path
    target_bytes: int
    output_path: Path
    mode: CompressionMode = CompressionMode.BALANCED
    include_subdirs: bool = True
    keep_exif: bool = True
    min_long_edge: int = DEFAULT_MIN_LONG_EDGE

    #: Number of bisection steps. Higher = closer to the target, slower.
    precision: int = 7

    #: Maximum number of "use up the leftover space" rounds.
    max_refine_rounds: int = 40

    #: Stop searching once the leftover headroom drops below this fraction of
    #: the target (0.2 % by default).
    tolerance_ratio: float = 0.002

    workers: int = field(default_factory=default_worker_count)

    #: Lower bound for the global scale factor.
    min_scale: float = ABSOLUTE_MIN_SCALE

    @property
    def jpeg_quality(self) -> int:
        return MODE_QUALITIES[self.mode]

    def validated(self) -> "OptimizationSettings":
        """Return a copy with out-of-range values clamped to sane bounds."""
        if self.target_bytes <= 0:
            raise ValueError("目标压缩包大小必须大于 0。")
        if not Path(self.source_dir).is_dir():
            raise ValueError(f"源文件夹不存在：{self.source_dir}")

        return OptimizationSettings(
            source_dir=Path(self.source_dir),
            target_bytes=int(self.target_bytes),
            output_path=Path(self.output_path),
            mode=CompressionMode(self.mode),
            include_subdirs=bool(self.include_subdirs),
            keep_exif=bool(self.keep_exif),
            min_long_edge=max(0, int(self.min_long_edge)),
            precision=max(2, min(16, int(self.precision))),
            max_refine_rounds=max(0, min(200, int(self.max_refine_rounds))),
            tolerance_ratio=max(0.0, min(0.05, float(self.tolerance_ratio))),
            workers=max(1, min(16, int(self.workers))),
            min_scale=max(0.001, min(1.0, float(self.min_scale))),
        )
