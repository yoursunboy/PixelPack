"""The Qt bridge: settings in, signals out, no blocking of the GUI thread."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelpack.core.optimizer import STAGE_FINAL, STAGE_SCAN  # noqa: E402
from pixelpack.models.result import ProgressUpdate  # noqa: E402
from pixelpack.utils.sizes import KB, MB  # noqa: E402
from pixelpack.workers.optimization_worker import (  # noqa: E402
    OptimizationTask,
    OptimizationWorker,
)
from tests.helpers import make_settings, noise_image, save_image  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def small_folder(tmp_path: Path) -> Path:
    root = tmp_path / "素材"
    root.mkdir()
    for index in range(4):
        save_image(noise_image((700, 520), seed=index), root / f"{index}.jpg", quality=95)
    return root


def _drain(app, predicate, timeout_ms: int = 120_000) -> bool:
    """Spin the event loop until *predicate* is true or time runs out."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# --------------------------------------------------------------------------
# The worker, driven synchronously
# --------------------------------------------------------------------------
def test_worker_emits_finished_with_a_real_result(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 200 * KB, precision=4, max_refine_rounds=8)
    worker = OptimizationWorker(settings)

    updates: list[ProgressUpdate] = []
    results = []
    failures = []
    worker.progress.connect(updates.append)
    worker.finished.connect(results.append)
    worker.failed.connect(failures.append)

    worker.run()  # same thread, so the signals arrive immediately

    assert not failures
    assert len(results) == 1

    result = results[0]
    assert result.final_bytes <= result.target_bytes
    assert result.output_path.exists()
    assert result.output_path.stat().st_size == result.final_bytes

    assert updates[0].stage == STAGE_SCAN
    assert updates[-1].stage == STAGE_FINAL
    assert all(u.message for u in updates)


def test_worker_reports_an_impossible_target_as_a_failure(qt_app, tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((200, 150), seed=1), root / "a.jpg")
    save_image(noise_image((200, 150), seed=2), root / "b.jpg")

    settings = make_settings(root, 8 * KB, min_long_edge=800)
    worker = OptimizationWorker(settings)

    failures: list[str] = []
    results = []
    worker.failed.connect(failures.append)
    worker.finished.connect(results.append)

    worker.run()

    assert not results
    assert len(failures) == 1
    assert failures[0], "失败时必须给出可读的原因"


def test_worker_reports_cancellation(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 120 * KB, precision=8, max_refine_rounds=60)
    worker = OptimizationWorker(settings)

    cancelled = []
    results = []
    failures = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.finished.connect(results.append)
    worker.failed.connect(failures.append)

    def on_progress(update: ProgressUpdate) -> None:
        if update.stage == STAGE_SCAN or update.current_zip_bytes:
            worker.cancel()

    worker.progress.connect(on_progress)
    worker.run()

    assert cancelled == [True]
    assert not results and not failures
    assert worker.is_cancelled


def test_worker_cancelled_before_it_starts_does_nothing(
    qt_app, small_folder: Path, tmp_path: Path
):
    output = tmp_path / "never.zip"
    settings = make_settings(small_folder, 120 * KB, output)

    worker = OptimizationWorker(settings)
    worker.cancel()

    cancelled = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.run()

    assert cancelled == [True]
    assert not output.exists()


def test_cancel_is_safe_from_another_thread(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 120 * KB, precision=8, max_refine_rounds=60)
    worker = OptimizationWorker(settings)

    cancelled = []
    worker.cancelled.connect(lambda: cancelled.append(True))

    threading.Timer(0.35, worker.cancel).start()
    worker.run()

    assert cancelled == [True], "另一个线程设置的取消标志必须生效"


# --------------------------------------------------------------------------
# The task: worker + thread lifecycle
# --------------------------------------------------------------------------
def test_task_runs_on_its_own_thread_and_finishes(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 200 * KB, precision=4, max_refine_rounds=8)
    task = OptimizationTask(settings)

    results = []
    task.finished.connect(results.append)

    task.start()
    assert task.is_running
    assert _drain(qt_app, lambda: bool(results)), "后台任务没有在超时前完成"

    task.wait(10_000)
    assert not task.is_running
    assert results[0].final_bytes <= results[0].target_bytes


def test_task_keeps_the_gui_thread_responsive(qt_app, small_folder: Path):
    """The window must keep processing events while the run is in flight."""
    settings = make_settings(small_folder, 150 * KB, precision=8, max_refine_rounds=40)
    task = OptimizationTask(settings)

    ticks = 0
    results = []

    def tick() -> None:
        nonlocal ticks
        ticks += 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(10)

    task.finished.connect(results.append)

    task.start()
    finished = _drain(qt_app, lambda: bool(results))
    timer.stop()

    assert finished
    assert ticks > 1, "事件循环在优化期间被阻塞了"
    task.wait(10_000)


def test_task_forwards_progress_to_the_gui_thread(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 200 * KB, precision=4, max_refine_rounds=8)
    task = OptimizationTask(settings)

    updates: list[ProgressUpdate] = []
    results = []
    task.progress.connect(updates.append)
    task.finished.connect(results.append)

    task.start()
    assert _drain(qt_app, lambda: bool(results))
    _drain(qt_app, lambda: len(updates) > 3)

    task.wait(10_000)
    assert updates
    assert updates[-1].stage == STAGE_FINAL


def test_task_can_be_cancelled_from_the_gui_thread(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 120 * KB, precision=9, max_refine_rounds=80)
    task = OptimizationTask(settings)

    cancelled = []
    results = []
    task.cancelled.connect(lambda: cancelled.append(True))
    task.finished.connect(results.append)
    task.progress.connect(lambda _u: task.cancel())

    task.start()
    assert _drain(qt_app, lambda: bool(cancelled) or bool(results))

    task.wait(20_000)
    assert cancelled == [True]
    assert not results
    assert not task.is_running


def test_task_refuses_to_start_twice(qt_app, small_folder: Path):
    settings = make_settings(small_folder, 5 * MB)
    task = OptimizationTask(settings)
    task.start()
    with pytest.raises(RuntimeError):
        task.start()
    task.cancel()
    task.wait(20_000)
