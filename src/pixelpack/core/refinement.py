"""Spend the space the bisection left unused.

After the global scale search there is usually a little headroom left: the
search can only settle on a step, and the step below the limit is always
strictly smaller than the limit. This module raises *individual* images back
towards their original resolution, best-value images first, until the archive
is as close to the target as it can get without exceeding it.

Every candidate produced here is validated by the same real "render + build a
real ZIP + measure it" path the search uses, and anything that overflows is
rolled back immediately. The returned scales are therefore always safe.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Sequence

logger = logging.getLogger(f"pixelpack.{__name__.rsplit('.', 1)[-1]}")

#: Smallest headroom worth chasing (below this we stop).
MIN_HEADROOM_BYTES = 4096

#: Bound on the per-round scale bump.
MIN_DELTA = 0.002
MAX_DELTA = 0.06


def rank_candidates(outcomes, scales, *, min_long_edge: int = 0) -> list[str]:
    """Order images by how much resolution a byte buys back.

    The score favours, in the order the spec asks for:

    * images with a large original resolution,
    * images that are currently scaled down the most,
    * images where a small size increase returns a lot of pixels.

    Images that cannot grow any more are dropped: those already at their
    original size, and those pinned to the ``min_long_edge`` protection floor.
    """
    scored: list[tuple[float, str]] = []

    for rel_path, outcome in outcomes.items():
        scale = float(scales.get(rel_path, 1.0))
        if scale >= 1.0 - 1e-9:
            continue

        orig_long = outcome.orig_long_edge
        final_long = outcome.final_long_edge
        if final_long >= orig_long:
            continue

        # Pinned to the protection floor: raising the global scale would not
        # change a single pixel for this image.
        if min_long_edge and final_long <= min_long_edge < orig_long:
            continue

        headroom = 1.0 - (final_long / orig_long if orig_long else 1.0)
        if headroom <= 1e-6:
            continue

        pixels = max(1, outcome.orig_width * outcome.orig_height)
        current_bytes = max(1, outcome.final_bytes)
        scored.append(((pixels * headroom) / current_bytes, rel_path))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [rel_path for _, rel_path in scored]


def refine_scales(
    evaluate: Callable[[dict[str, float]], object],
    scales: dict[str, float],
    evaluation,
    candidates: Sequence[str],
    *,
    max_rounds: int = 40,
    min_headroom_bytes: int = MIN_HEADROOM_BYTES,
    cancel: threading.Event | None = None,
    on_round: Callable[[int, object], None] | None = None,
) -> tuple[dict[str, float], object]:
    """Greedily grow individual images until the target is nearly reached.

    Args:
        evaluate: Renders *all* images from their originals at the supplied
            per-image scales, builds a real ZIP and returns its evaluation.
        scales: The scale map the search settled on.
        evaluation: The evaluation that produced *scales*.
        candidates: Ordered rel-paths to try growing (see :func:`rank_candidates`).
        max_rounds: Cap on the number of validation builds.
        min_headroom_bytes: Stop once less than this is left unused.
        cancel: Optional cancellation event.
        on_round: Called with ``(round_index, evaluation)`` after each build.

    Returns:
        ``(best_scales, best_evaluation)`` — always a combination whose archive
        was measured at or below the target.
    """
    current_scales = dict(scales)
    current_eval = evaluation
    target_bytes = int(current_eval.target_bytes)

    if int(current_eval.zip_size) > target_bytes:
        # The caller handed us something that already overflows. Nothing here
        # can make it safe, so leave it exactly as it is.
        logger.debug("精调收到超限方案，直接返回不做改动。")
        return current_scales, current_eval

    active = [rel for rel in candidates if current_scales.get(rel, 1.0) < 1.0 - 1e-9]
    completed = 0

    while active and completed < max_rounds:
        if cancel is not None and cancel.is_set():
            break

        headroom = target_bytes - int(current_eval.zip_size)
        if headroom <= min_headroom_bytes:
            break

        # ZIP size grows roughly with the square of a linear scale, so a
        # relative size gain of ``h`` needs a scale bump of about ``h / 2``.
        # The bump is then tried at a half and a quarter of that size.
        ratio = headroom / max(1, int(current_eval.zip_size))
        base_delta = min(MAX_DELTA, max(MIN_DELTA, ratio * 0.6))

        progressed = False
        for delta in (base_delta, base_delta / 2.0, base_delta / 4.0):
            if delta < MIN_DELTA:
                continue

            trial = dict(current_scales)
            changed = False
            for rel_path in active:
                new_scale = min(1.0, trial.get(rel_path, 1.0) + delta)
                if new_scale > trial.get(rel_path, 1.0) + 1e-12:
                    trial[rel_path] = new_scale
                    changed = True
            if not changed:
                continue

            completed += 1
            candidate_eval = evaluate(trial)
            if on_round is not None:
                on_round(completed, candidate_eval)
            if cancel is not None and cancel.is_set():
                break

            size = int(candidate_eval.zip_size)
            if size <= target_bytes and size >= int(current_eval.zip_size):
                current_scales, current_eval = trial, candidate_eval
                progressed = True
                break

        if not progressed:
            # Growing everything at once does not fit: concentrate on the
            # highest-value images instead of giving up.
            if len(active) == 1:
                break
            active = active[: max(1, len(active) // 2)]
        else:
            active = [rel for rel in active if current_scales.get(rel, 1.0) < 1.0 - 1e-9]

    return current_scales, current_eval
