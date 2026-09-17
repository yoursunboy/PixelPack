"""The refinement pass: ranking candidates and never overshooting."""

from __future__ import annotations

import threading

import pytest

from pixelpack.core.refinement import rank_candidates, refine_scales
from pixelpack.models.result import ImageOutcome


def outcome(rel_path, orig, final, orig_bytes, final_bytes, fmt="JPEG"):
    return ImageOutcome(
        rel_path=rel_path,
        arcname=rel_path,
        orig_width=orig[0],
        orig_height=orig[1],
        final_width=final[0],
        final_height=final[1],
        orig_bytes=orig_bytes,
        final_bytes=final_bytes,
        encode_format=fmt,
    )


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------
def test_rank_prefers_large_originals_and_large_reductions():
    outcomes = {
        # huge original, heavily reduced -> best value
        "big.jpg": outcome("big.jpg", (6000, 4000), (1500, 1000), 8_000_000, 500_000),
        # already at full size -> nothing to gain
        "full.jpg": outcome("full.jpg", (2000, 1500), (2000, 1500), 2_000_000, 2_000_000),
        # modest original, modest reduction
        "small.jpg": outcome("small.jpg", (1200, 900), (1000, 750), 400_000, 300_000),
    }
    scales = {"big.jpg": 0.25, "full.jpg": 1.0, "small.jpg": 0.83}

    ranked = rank_candidates(outcomes, scales)

    assert ranked[0] == "big.jpg"
    assert "full.jpg" not in ranked, "已经全尺寸的图片没有提升空间"


def test_rank_excludes_images_pinned_to_the_protection_floor():
    outcomes = {
        "pinned.jpg": outcome("pinned.jpg", (3000, 2000), (800, 533), 900_000, 120_000),
        "free.jpg": outcome("free.jpg", (3000, 2000), (900, 600), 900_000, 130_000),
    }
    scales = {"pinned.jpg": 0.2, "free.jpg": 0.3}

    ranked = rank_candidates(outcomes, scales, min_long_edge=800)

    assert ranked == ["free.jpg"]


def test_rank_returns_empty_without_candidates():
    outcomes = {"a.jpg": outcome("a.jpg", (1000, 800), (1000, 800), 100, 100)}

    assert rank_candidates(outcomes, {"a.jpg": 1.0}) == []


# --------------------------------------------------------------------------
# The greedy loop
# --------------------------------------------------------------------------
class FakeModel:
    """A deterministic stand-in for the render + ZIP pipeline.

    Encoded size grows with the square of each image's scale, which is close to
    how real photo archives behave.
    """

    def __init__(self, weights: dict[str, float], fixed: float, target: int) -> None:
        self.weights = weights
        self.fixed = fixed
        self.target = target
        self.calls: list[dict[str, float]] = []

    def size(self, scales: dict[str, float]) -> int:
        total = self.fixed
        for name, weight in self.weights.items():
            total += weight * scales.get(name, 1.0) ** 2
        return int(total)

    def __call__(self, scales: dict[str, float]):
        self.calls.append(dict(scales))
        return _Eval(scales, self.size(scales), self.target)


class _Eval:
    def __init__(self, scales, zip_size, target_bytes):
        self.scales = scales
        self.zip_size = zip_size
        self.target_bytes = target_bytes
        self.outcomes = {}

    @property
    def fits(self):
        return self.zip_size <= self.target_bytes

    @property
    def headroom(self):
        return self.target_bytes - self.zip_size

    @property
    def utilization(self):
        return self.zip_size / self.target_bytes


WEIGHTS = {"a": 4_000_000, "b": 3_000_000, "c": 2_000_000, "d": 1_000_000}

#: A feasible starting point for a 1.5 MB budget: 10M * 0.3^2 = 0.9 MB.
FEASIBLE = 0.3


def test_refine_never_returns_a_map_that_overflows():
    model = FakeModel(WEIGHTS, fixed=200_000, target=1_500_000)
    start = {name: FEASIBLE for name in WEIGHTS}
    initial = model(start)
    assert initial.fits

    scales, evaluation = refine_scales(model, start, initial, list(WEIGHTS), max_rounds=25)

    assert evaluation.zip_size <= evaluation.target_bytes
    assert model.size(scales) <= model.target


def test_refine_refuses_an_already_overflowing_start():
    """Defensive: never hand back something worse than what came in."""
    model = FakeModel(WEIGHTS, fixed=200_000, target=1_000_000)
    start = {name: 0.9 for name in WEIGHTS}
    initial = model(start)
    assert not initial.fits
    calls_before = len(model.calls)

    scales, evaluation = refine_scales(model, start, initial, list(WEIGHTS), max_rounds=25)

    assert scales == start
    assert evaluation is initial
    assert len(model.calls) == calls_before


def test_refine_uses_up_the_leftover_space():
    model = FakeModel(WEIGHTS, fixed=200_000, target=1_500_000)
    start = {name: FEASIBLE for name in WEIGHTS}
    initial = model(start)

    scales, evaluation = refine_scales(model, start, initial, list(WEIGHTS), max_rounds=25)

    assert evaluation.zip_size > initial.zip_size, "refinement must chase the ceiling"
    assert evaluation.utilization > initial.utilization


def test_refine_never_lowers_a_scale():
    model = FakeModel(WEIGHTS, fixed=200_000, target=1_500_000)
    start = {name: FEASIBLE for name in WEIGHTS}
    initial = model(start)

    scales, _ = refine_scales(model, start, initial, list(WEIGHTS), max_rounds=25)

    for name in WEIGHTS:
        assert scales[name] >= start[name]


def test_refine_never_exceeds_scale_one():
    model = FakeModel(WEIGHTS, fixed=0, target=10 ** 9)  # effectively no limit
    start = {name: 0.9 for name in WEIGHTS}
    initial = model(start)

    scales, evaluation = refine_scales(model, start, initial, list(WEIGHTS), max_rounds=30)

    assert all(value <= 1.0 for value in scales.values())
    assert evaluation.zip_size <= evaluation.target_bytes


def test_refine_stops_when_there_is_no_headroom():
    model = FakeModel(WEIGHTS, fixed=200_000, target=1_000_000)
    start = {name: FEASIBLE for name in WEIGHTS}
    initial = model(start)
    # Pretend the archive already sits exactly on the limit.
    initial.zip_size = initial.target_bytes
    calls_before = len(model.calls)

    scales, _ = refine_scales(model, start, initial, list(WEIGHTS), max_rounds=10)

    assert scales == start
    assert len(model.calls) == calls_before, "no headroom means no more validation builds"


def test_refine_stops_at_the_round_budget():
    model = FakeModel(WEIGHTS, fixed=0, target=10 ** 9)
    start = {name: 0.1 for name in WEIGHTS}
    initial = model(start)
    calls_before = len(model.calls)

    refine_scales(model, start, initial, list(WEIGHTS), max_rounds=3)

    assert len(model.calls) - calls_before <= 3


@pytest.mark.parametrize("candidates", [["a"], ["a", "b"], ["a", "b", "c", "d"]])
def test_refine_respects_the_candidate_subset(candidates):
    model = FakeModel(WEIGHTS, fixed=0, target=1_200_000)
    start = {name: FEASIBLE for name in WEIGHTS}
    initial = model(start)
    assert initial.fits

    scales, evaluation = refine_scales(model, start, initial, candidates, max_rounds=20)

    assert evaluation.zip_size <= evaluation.target_bytes
    for name in set(WEIGHTS) - set(candidates):
        assert scales[name] == start[name], "images outside the subset must not move"


def test_refine_can_be_cancelled():
    cancel = threading.Event()
    cancel.set()
    model = FakeModel(WEIGHTS, fixed=0, target=10 ** 9)
    start = {name: 0.2 for name in WEIGHTS}
    initial = model(start)
    calls_before = len(model.calls)

    scales, _ = refine_scales(
        model, start, initial, list(WEIGHTS), max_rounds=10, cancel=cancel
    )

    assert scales == start
    assert len(model.calls) == calls_before
