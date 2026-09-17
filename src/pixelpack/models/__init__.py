"""Typed data models shared between the core, the workers and the UI."""

from __future__ import annotations

from .image_info import ScanResult, SourceFile
from .result import (
    ImpossibleTargetError,
    ImageOutcome,
    OperationCancelled,
    OptimizationError,
    OptimizationResult,
    PixelPackError,
    ProgressUpdate,
)
from .settings import (
    MODE_QUALITIES,
    CompressionMode,
    OptimizationSettings,
    default_output_path,
    default_worker_count,
)

__all__ = [
    "MODE_QUALITIES",
    "CompressionMode",
    "ImageOutcome",
    "ImpossibleTargetError",
    "OperationCancelled",
    "OptimizationError",
    "OptimizationResult",
    "OptimizationSettings",
    "PixelPackError",
    "ProgressUpdate",
    "ScanResult",
    "SourceFile",
    "default_output_path",
    "default_worker_count",
]
