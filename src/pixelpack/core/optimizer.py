"""The optimisation engine: estimate, bisect, verify, refine.

Pipeline (mirrors the specification):

1. **Scan** the folder and classify every file.
2. **Baseline** — build a real ZIP from the untouched originals. If it already
   fits, stop: no image is re-encoded at all.
3. **Estimate** a starting scale with ``sqrt(budget / image_bytes)``.
4. **Bisect** between a known-good and a known-bad scale. *Every* probe renders
   from the original at that scale and builds a real ZIP; the archive size is
   measured, never predicted. The best known-safe candidate is kept at all
   times.
5. **Refine** individual images upward with the leftover space.
6. **Finalise** — rebuild, verify the archive end to end and move it into place
   atomically.

The final ZIP size is a hard limit: a candidate is only ever accepted when its
measured size is ``<= target_bytes``.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Sequence

from ..models.image_info import ScanResult, SourceFile
from ..models.result import (
    ImageOutcome,
    ImpossibleTargetError,
    OperationCancelled,
    OptimizationError,
    OptimizationResult,
    ProgressUpdate,
)
from ..models.settings import CompressionMode, OptimizationSettings
from ..utils.temp import TempWorkspace
from .archive import ArchiveEntry, archive_size, build_archive, verify_archive
from .cache import RenderCache
from .image_processor import (
    ImageProcessingError,
    RenderedImage,
    encode_format_for,
    render_image,
    target_dimensions,
)
from .refinement import rank_candidates, refine_scales
from .scanner import scan_directory

logger = logging.getLogger(f"pixelpack.{__name__.rsplit('.', 1)[-1]}")

# -- progress stages -------------------------------------------------------
STAGE_SCAN = "scan"
STAGE_BASELINE = "baseline"
STAGE_SEARCH = "search"
STAGE_REFINE = "refine"
STAGE_FINAL = "final"

STAGE_MESSAGES: dict[str, str] = {
    STAGE_SCAN: "正在扫描图片",
    STAGE_BASELINE: "正在生成 ZIP",
    STAGE_SEARCH: "正在测试图片尺寸",
    STAGE_REFINE: "正在精调",
    STAGE_FINAL: "正在完成",
}

# Fraction of the progress bar each stage owns.
_SPAN_SCAN = (0.0, 0.05)
_SPAN_BASELINE = (0.05, 0.12)
_SPAN_SEARCH = (0.12, 0.72)
_SPAN_REFINE = (0.72, 0.92)
_SPAN_FINAL = (0.92, 1.0)

NON_IMAGE_TOO_LARGE_MESSAGE = (
    "无法达到目标压缩包大小，不可优化文件已经超过大小限制。"
)
IMPOSSIBLE_MESSAGE = (
    "无法达到目标压缩包大小：即使把所有图片缩小到最小保护尺寸，压缩包仍然超过上限。"
)
NO_FILES_MESSAGE = "所选文件夹中没有找到任何文件。"

ProgressCallback = Callable[[ProgressUpdate], None]


# --------------------------------------------------------------------------
# Internal evaluation record
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Evaluation:
    """One fully validated candidate: per-image scales plus a real ZIP size."""

    scales: dict[str, float]
    zip_size: int
    target_bytes: int
    outcomes: dict[str, ImageOutcome] = field(default_factory=dict)

    @property
    def fits(self) -> bool:
        return self.zip_size <= self.target_bytes

    @property
    def headroom(self) -> int:
        return self.target_bytes - self.zip_size


class Optimizer:
    """Runs one optimisation job.

    The class is deliberately GUI-free and synchronous: it reports progress
    through a callback and honours a :class:`threading.Event` for cancellation,
    which makes it straightforward to drive from a worker thread *and* from
    tests.
    """

    def __init__(
        self,
        settings: OptimizationSettings,
        *,
        scan: ScanResult | None = None,
        workspace: TempWorkspace | None = None,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
        cache: RenderCache | None = None,
    ) -> None:
        self.settings = settings.validated()
        self._scan = scan
        self._workspace = workspace
        self._owns_workspace = workspace is None
        self._on_progress = on_progress
        self._cancel = cancel if cancel is not None else threading.Event()
        self._cache = cache if cache is not None else RenderCache()

        self._image_dir: Path | None = None
        self._candidate_zip: Path | None = None
        self._source_dims: dict[str, tuple[int, int]] = {}
        self._evaluations = 0
        self._search_budget = 2 + 6 + self.settings.precision

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> OptimizationResult:
        """Execute the whole pipeline and return the final result."""
        started = time.perf_counter()

        workspace = self._workspace or TempWorkspace("run")
        if self._owns_workspace:
            workspace.create()
        self._image_dir = workspace.subdir("images")
        self._candidate_zip = workspace.path / "candidate.zip"

        try:
            return self._run_pipeline(started, workspace)
        finally:
            if self._owns_workspace:
                workspace.cleanup()
            self._cache.clear()

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    def _run_pipeline(self, started: float, workspace: TempWorkspace) -> OptimizationResult:
        settings = self.settings
        target = settings.target_bytes

        # -- 1. scan ---------------------------------------------------
        self._emit(STAGE_SCAN, 0.0)
        scan = self._scan or self._scan_source()
        self._scan = scan
        # A cancelled scan returns nothing, which would otherwise look like an
        # empty folder. Report the cancellation, not a misleading "no files".
        self._check_cancel()
        if not scan.files:
            raise OptimizationError(NO_FILES_MESSAGE)

        logger.info(
            "扫描完成：%d 个文件（%d 张图片），共 %.2f MB",
            scan.file_count,
            scan.image_count,
            scan.total_bytes / (1024 * 1024),
        )

        # -- non-image floor: files we must copy verbatim -----------------
        self._emit(STAGE_BASELINE, _SPAN_BASELINE[0])
        fixed_bytes = self._measure_fixed_files(workspace)
        if fixed_bytes > target:
            raise ImpossibleTargetError(NON_IMAGE_TOO_LARGE_MESSAGE)

        # -- 2. baseline: the untouched originals -------------------------
        baseline_entries = [ArchiveEntry(f.path, f.rel_path) for f in scan.files]
        baseline = build_archive(baseline_entries, workspace.path / "baseline.zip", cancel=self._cancel)
        self._check_cancel()
        logger.info("原始 ZIP 大小：%.2f MB", baseline.size_bytes / (1024 * 1024))

        if baseline.size_bytes <= target:
            self._emit(STAGE_FINAL, _SPAN_FINAL[0])
            return self._finish_from_originals(scan, baseline, target, started, workspace)

        if not scan.image_files:
            raise ImpossibleTargetError(IMPOSSIBLE_MESSAGE)

        # -- 3./4. estimate + bisect --------------------------------------
        best = self._search(scan, target, fixed_bytes)
        if best is None:
            raise ImpossibleTargetError(IMPOSSIBLE_MESSAGE)

        # -- 5. refinement -------------------------------------------------
        best = self._refine(scan, best, target)

        # -- 6. finalise ---------------------------------------------------
        return self._finalise(scan, best, target, started, workspace)

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------
    def _scan_source(self) -> ScanResult:
        settings = self.settings
        lo, hi = _SPAN_SCAN

        def on_scan_progress(seen: int, total: int) -> None:
            fraction = lo + (hi - lo) * (seen / total if total else 1.0)
            self._emit(STAGE_SCAN, fraction)

        return scan_directory(
            settings.source_dir,
            include_subdirs=settings.include_subdirs,
            exclude=[settings.output_path],
            cancel=self._cancel,
            on_progress=on_scan_progress,
        )

    def _measure_fixed_files(self, workspace: TempWorkspace) -> int:
        """Size the non-image files contribute to every archive.

        Measured by building a real ZIP of just those files, so headers and
        directory records are included rather than estimated.
        """
        entries = [
            ArchiveEntry(f.path, f.rel_path)
            for f in self._scan.non_image_files  # type: ignore[union-attr]
        ]
        if not entries:
            return 0
        stats = build_archive(entries, workspace.path / "fixed.zip", cancel=self._cancel)
        self._check_cancel()
        return stats.size_bytes

    def _search(self, scan: ScanResult, target: int, fixed_bytes: int) -> Evaluation | None:
        """Bisect for the largest scale whose archive still fits."""
        settings = self.settings
        lo_span, hi_span = _SPAN_SEARCH
        budget = max(1, self._search_budget)

        def search_fraction(rounds: int) -> float:
            return lo_span + (hi_span - lo_span) * min(1.0, rounds / budget)

        images = scan.image_files
        floor = self._minimum_scale(scan)

        best: Evaluation | None = None
        hi_scale = 1.0
        lo_scale: float | None = None

        # Probe full resolution first: re-encoding at quality 88 is often far
        # smaller than the originals, so the answer is frequently "no shrink at
        # all". This is the upper bracket and is expected to overflow.
        self._emit(STAGE_SEARCH, lo_span)
        top = self._evaluate(self._uniform(scale=1.0), fraction_fn=search_fraction)
        if top.fits:
            logger.info("全尺寸重新编码即可满足目标，无需缩小分辨率。")
            return top
        hi_scale = 1.0

        # Estimated starting point: ZIP size grows roughly with pixel count,
        # i.e. with scale squared.
        size_budget = max(1, target - fixed_bytes)
        image_bytes = max(1, scan.image_bytes)
        estimate = math.sqrt(size_budget / image_bytes)
        estimate = min(1.0, max(floor, estimate))

        probe = estimate
        while True:
            self._check_cancel()
            if probe >= hi_scale - 1e-9:
                break
            candidate = self._evaluate(self._uniform(scale=probe), fraction_fn=search_fraction)
            if candidate.fits:
                best = candidate
                lo_scale = probe
                break
            hi_scale = probe
            if probe <= floor + 1e-9:
                break
            probe = max(floor, probe * 0.5)

        # Nothing fit yet: try the hardest we are allowed to squeeze.
        if best is None and floor < hi_scale - 1e-9:
            candidate = self._evaluate(self._uniform(scale=floor), fraction_fn=search_fraction)
            if candidate.fits:
                best = candidate
                lo_scale = floor

        if best is None:
            logger.info("即使使用最小缩放比例（%.4f）仍然超出目标。", floor)
            return None

        # Bisection between the best known-good and the best known-bad scale.
        assert lo_scale is not None
        tolerance = max(1024, int(target * settings.tolerance_ratio))

        for _ in range(settings.precision):
            self._check_cancel()
            if hi_scale - lo_scale <= 1e-5:
                break
            if best.headroom <= tolerance:
                break

            mid = (lo_scale + hi_scale) / 2.0
            if mid <= lo_scale + 1e-9 or mid >= hi_scale - 1e-9:
                break

            candidate = self._evaluate(self._uniform(scale=mid), fraction_fn=search_fraction)
            if candidate.fits:
                best = candidate
                lo_scale = mid
            else:
                hi_scale = mid

        logger.info(
            "二分搜索结束：缩放 %.4f，ZIP %.2f MB（目标 %.2f MB），评估 %d 次",
            lo_scale,
            best.zip_size / (1024 * 1024),
            target / (1024 * 1024),
            self._evaluations,
        )
        return best

    def _refine(self, scan: ScanResult, best: Evaluation, target: int) -> Evaluation:
        settings = self.settings
        if settings.max_refine_rounds <= 0:
            return best

        candidates = rank_candidates(
            best.outcomes, best.scales, min_long_edge=settings.min_long_edge
        )
        if not candidates:
            return best

        lo_span, hi_span = _SPAN_REFINE
        span = hi_span - lo_span
        base_rounds = self._evaluations

        def refine_fraction(rounds: int) -> float:
            done = rounds - base_rounds
            return lo_span + span * min(1.0, done / max(1, settings.max_refine_rounds))

        def evaluate_for_refine(trial: dict[str, float]) -> Evaluation:
            return self._evaluate(trial, stage=STAGE_REFINE, fraction_fn=refine_fraction)

        refined_scales, refined = refine_scales(
            evaluate_for_refine,
            best.scales,
            best,
            candidates,
            max_rounds=settings.max_refine_rounds,
            cancel=self._cancel,
        )
        self._check_cancel()

        if refined.zip_size >= best.zip_size:
            logger.info(
                "精调结束：ZIP %.2f MB，利用率 %.1f%%",
                refined.zip_size / (1024 * 1024),
                refined.zip_size / target * 100.0,
            )
        return refined if refined.zip_size >= best.zip_size else best

    def _finalise(
        self,
        scan: ScanResult,
        best: Evaluation,
        target: int,
        started: float,
        workspace: TempWorkspace,
    ) -> OptimizationResult:
        """Re-render the winning scales, build the real archive, verify it."""
        self._emit(STAGE_FINAL, _SPAN_FINAL[0], zip_size=best.zip_size, scale=self._mean_scale(best.scales))

        # Re-running the winning evaluation reproduces exactly the same bytes;
        # with the render cache warm this is nearly free.
        final = self._evaluate(best.scales, stage=STAGE_FINAL)
        if not final.fits:
            # Should be unreachable (the same inputs are deterministic), but if
            # the cache evicted something the result must still be safe.
            logger.warning("最终重建结果超出目标，回退到上一个可行方案。")
            final = best
            if not final.fits:
                raise ImpossibleTargetError(IMPOSSIBLE_MESSAGE)

        entries = self._entries_for(final)
        expected_names = [entry.arcname for entry in entries]

        output_path = Path(self.settings.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build next to the destination so the final move is atomic and stays
        # on one volume; a failed run never leaves a half-written archive.
        staging = output_path.with_name(output_path.name + ".part")
        try:
            stats = build_archive(entries, staging, cancel=self._cancel)
            self._check_cancel()

            if stats.size_bytes > target:
                raise OptimizationError(
                    "最终 ZIP 超过了目标大小，已取消输出以保证不产生超限文件。"
                )

            verify_archive(staging, expected_names, max_bytes=target)
            os.replace(staging, output_path)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

        elapsed = time.perf_counter() - started
        result = OptimizationResult(
            source_dir=Path(self.settings.source_dir),
            output_path=output_path,
            target_bytes=target,
            original_bytes=scan.total_bytes,
            final_bytes=stats.size_bytes,
            image_count=scan.image_count,
            file_count=scan.file_count,
            copied_original=False,
            scale=self._mean_scale(final.scales),
            rounds=self._evaluations,
            elapsed_seconds=elapsed,
            mode=self.settings.mode,
            outcomes=self._ordered_outcomes(scan, final.outcomes),
            verified=True,
        )
        self._emit(STAGE_FINAL, 1.0, zip_size=result.final_bytes, scale=result.scale)
        logger.info(
            "完成：%.2f MB / %.2f MB（利用率 %.1f%%），耗时 %.1f 秒",
            result.final_bytes / (1024 * 1024),
            target / (1024 * 1024),
            result.utilization * 100.0,
            elapsed,
        )
        return result

    def _finish_from_originals(
        self,
        scan: ScanResult,
        baseline,
        target: int,
        started: float,
        workspace: TempWorkspace,
    ) -> OptimizationResult:
        """The originals already fit: copy them across untouched."""
        logger.info("原始文件已满足大小限制，直接打包，不重新压缩图片。")

        output_path = Path(self.settings.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        staging = output_path.with_name(output_path.name + ".part")

        expected_names = [f.rel_path for f in scan.files]
        try:
            entries = [ArchiveEntry(f.path, f.rel_path) for f in scan.files]
            stats = build_archive(entries, staging, cancel=self._cancel)
            self._check_cancel()
            verify_archive(staging, expected_names, max_bytes=target)
            os.replace(staging, output_path)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

        outcomes = [
            ImageOutcome(
                rel_path=f.rel_path,
                arcname=f.rel_path,
                orig_width=f.width,
                orig_height=f.height,
                final_width=f.width,
                final_height=f.height,
                orig_bytes=f.size_bytes,
                final_bytes=f.size_bytes,
                encode_format=f.format or "COPY",
            )
            for f in scan.image_files
        ]

        elapsed = time.perf_counter() - started
        result = OptimizationResult(
            source_dir=Path(self.settings.source_dir),
            output_path=output_path,
            target_bytes=target,
            original_bytes=scan.total_bytes,
            final_bytes=stats.size_bytes,
            image_count=scan.image_count,
            file_count=scan.file_count,
            copied_original=True,
            scale=1.0,
            rounds=0,
            elapsed_seconds=elapsed,
            mode=self.settings.mode,
            outcomes=outcomes,
            verified=True,
        )
        self._emit(STAGE_FINAL, 1.0, zip_size=result.final_bytes, scale=1.0)
        return result

    # ------------------------------------------------------------------
    # Evaluation primitives
    # ------------------------------------------------------------------
    def _uniform(self, *, scale: float) -> dict[str, float]:
        return {f.rel_path: scale for f in self._scan.image_files}  # type: ignore[union-attr]

    def _evaluate(
        self,
        scales: dict[str, float],
        *,
        stage: str = STAGE_SEARCH,
        fraction_fn: Callable[[int], float] | None = None,
    ) -> Evaluation:
        """Render every image *from its original* at *scales* and measure the ZIP."""
        self._check_cancel()
        outcomes = self._render_all(scales)
        entries = self._entries_from(scales, outcomes)
        stats = build_archive(entries, self._candidate_zip, cancel=self._cancel)  # type: ignore[arg-type]
        self._evaluations += 1

        if fraction_fn is not None:
            self._emit(
                stage,
                fraction_fn(self._evaluations),
                zip_size=stats.size_bytes,
                scale=self._mean_scale(scales),
                round_index=self._evaluations,
                total_rounds=self._search_budget,
            )

        return Evaluation(
            scales=dict(scales),
            zip_size=stats.size_bytes,
            target_bytes=self.settings.target_bytes,
            outcomes=outcomes,
        )

    def _render_all(self, scales: dict[str, float]) -> dict[str, ImageOutcome]:
        images = self._scan.image_files  # type: ignore[union-attr]
        if not images:
            return {}

        outcomes: dict[str, ImageOutcome] = {}
        workers = max(1, self.settings.workers)
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pixelpack")
        try:
            futures = {
                executor.submit(self._render_one, source, scales.get(source.rel_path, 1.0)): source
                for source in images
            }
            for future in as_completed(futures):
                if self._cancel.is_set():
                    raise OperationCancelled("用户已取消。")
                source = futures[future]
                try:
                    outcomes[source.rel_path] = future.result()
                except ImageProcessingError as exc:
                    logger.warning("%s；将原样复制该文件。", exc)
                    outcomes[source.rel_path] = self._fallback_outcome(source)
        finally:
            executor.shutdown(wait=True, cancel_futures=self._cancel.is_set())
        return outcomes

    def _render_one(self, source: SourceFile, scale: float) -> ImageOutcome:
        settings = self.settings
        encode_format = encode_format_for(source.format)
        quality = settings.jpeg_quality

        dims = self._source_dims.get(source.rel_path)
        if dims is not None:
            key = (
                source.rel_path,
                *target_dimensions(dims[0], dims[1], scale, settings.min_long_edge),
                quality,
                encode_format,
            )
            rendered = self._cache.get_encoded(key)
            if rendered is not None:
                return self._write_rendered(source, rendered, scale)

        rendered = render_image(
            source,
            scale,
            quality=quality,
            min_long_edge=settings.min_long_edge,
            keep_exif=settings.keep_exif,
            cache=self._cache,
        )

        real_dims = (rendered.orig_width, rendered.orig_height)
        self._source_dims[source.rel_path] = real_dims
        key = (
            source.rel_path,
            *target_dimensions(real_dims[0], real_dims[1], scale, settings.min_long_edge),
            quality,
            rendered.encode_format,
        )
        self._cache.put_encoded(key, rendered)
        return self._write_rendered(source, rendered, scale)

    def _write_rendered(
        self, source: SourceFile, rendered: RenderedImage, scale: float
    ) -> ImageOutcome:
        destination = self._rendered_path(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(rendered.data)

        return ImageOutcome(
            rel_path=source.rel_path,
            arcname=source.output_name,
            orig_width=rendered.orig_width,
            orig_height=rendered.orig_height,
            final_width=rendered.width,
            final_height=rendered.height,
            orig_bytes=source.size_bytes,
            final_bytes=len(rendered.data),
            encode_format=rendered.encode_format,
            requested_scale=scale,
        )

    def _fallback_outcome(self, source: SourceFile) -> ImageOutcome:
        """Copy a file we could not decode so the archive stays complete."""
        destination = self._rendered_path(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source.path, destination)
            size = destination.stat().st_size
        except OSError:
            logger.error("无法复制文件：%s", source.path, exc_info=True)
            size = source.size_bytes

        return ImageOutcome(
            rel_path=source.rel_path,
            arcname=source.output_name,
            orig_width=source.width,
            orig_height=source.height,
            final_width=source.width,
            final_height=source.height,
            orig_bytes=source.size_bytes,
            final_bytes=size,
            encode_format=source.format or "COPY",
            fallback=True,
        )

    def _rendered_path(self, source: SourceFile) -> Path:
        assert self._image_dir is not None
        return self._image_dir.joinpath(*PurePosixPath(source.output_name).parts)

    def _entries_from(
        self, scales: dict[str, float], outcomes: dict[str, ImageOutcome]
    ) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        for source in self._scan.files:  # type: ignore[union-attr]
            if source.is_image:
                outcome = outcomes.get(source.rel_path)
                if outcome is None:
                    continue
                entries.append(ArchiveEntry(self._rendered_path(source), outcome.arcname))
            else:
                entries.append(ArchiveEntry(source.path, source.rel_path))
        return entries

    def _entries_for(self, evaluation: Evaluation) -> list[ArchiveEntry]:
        return self._entries_from(evaluation.scales, evaluation.outcomes)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _minimum_scale(self, scan: ScanResult) -> float:
        """The smallest scale that can still change a single pixel.

        Below ``min_long_edge / max_long_edge`` every image is pinned to the
        protection floor, so probing lower would produce identical output.
        """
        settings = self.settings
        max_long = scan.max_long_edge
        if max_long <= 0 or settings.min_long_edge <= 0:
            return settings.min_scale
        return min(1.0, max(settings.min_scale, settings.min_long_edge / max_long))

    @staticmethod
    def _mean_scale(scales: dict[str, float]) -> float:
        if not scales:
            return 1.0
        return sum(scales.values()) / len(scales)

    @staticmethod
    def _ordered_outcomes(
        scan: ScanResult, outcomes: dict[str, ImageOutcome]
    ) -> list[ImageOutcome]:
        ordered = [outcomes[f.rel_path] for f in scan.image_files if f.rel_path in outcomes]
        ordered.sort(key=lambda o: o.rel_path.lower())
        return ordered

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise OperationCancelled("用户已取消。")

    def _emit(
        self,
        stage: str,
        fraction: float,
        *,
        zip_size: int | None = None,
        scale: float | None = None,
        round_index: int = 0,
        total_rounds: int = 0,
    ) -> None:
        if self._on_progress is None:
            return
        update = ProgressUpdate(
            stage=stage,
            message=STAGE_MESSAGES.get(stage, stage),
            fraction=min(1.0, max(0.0, fraction)),
            target_bytes=self.settings.target_bytes,
            current_zip_bytes=zip_size if zip_size is not None else 0,
            scale=scale if scale is not None else 0.0,
            round_index=round_index,
            total_rounds=total_rounds,
        )
        try:
            self._on_progress(update)
        except Exception:  # noqa: BLE001 - a broken UI callback must not stop the run
            logger.debug("进度回调抛出异常。", exc_info=True)
