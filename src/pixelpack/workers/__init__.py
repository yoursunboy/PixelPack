"""Qt-facing glue: runs the optimiser off the GUI thread."""

from __future__ import annotations

from .optimization_worker import OptimizationTask, OptimizationWorker

__all__ = ["OptimizationTask", "OptimizationWorker"]
