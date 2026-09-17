"""Background execution of an optimisation run.

The GUI never calls the optimiser directly: a full run takes seconds to
minutes, and doing it on the Qt event loop would freeze the window. Instead the
window moves an :class:`OptimizationWorker` onto a :class:`QThread` and talks to
it through signals.

Cancellation is a plain :class:`threading.Event` because it is set from the GUI
thread and read from the worker thread — no Qt machinery involved, so it is safe
at any moment, including while the worker is inside a long render.
"""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from ..core.optimizer import Optimizer
from ..models.result import (
    ImpossibleTargetError,
    OperationCancelled,
    OptimizationError,
    PixelPackError,
    ProgressUpdate,
)
from ..models.settings import OptimizationSettings
from ..utils.logging import get_logger

logger = get_logger(__name__)


class OptimizationWorker(QObject):
    """Runs one :class:`Optimizer` and reports back through signals.

    Signals (all emitted from the worker thread; connect them normally and Qt
    will queue them onto the GUI thread):

    * ``progress(object)``   — a :class:`ProgressUpdate`
    * ``finished(object)``   — an :class:`OptimizationResult`
    * ``failed(str)``        — a human-readable failure message
    * ``cancelled()``        — the user stopped the run
    """

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        settings: OptimizationSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cancel = threading.Event()

    # -- control -----------------------------------------------------------
    @property
    def settings(self) -> OptimizationSettings:
        return self._settings

    def cancel(self) -> None:
        """Ask the run to stop. Safe to call from any thread."""
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- the slot that runs on the worker thread ---------------------------
    @Slot()
    def run(self) -> None:
        optimizer = Optimizer(
            self._settings,
            on_progress=self._emit_progress,
            cancel=self._cancel,
        )

        try:
            result = optimizer.run()
        except OperationCancelled:
            logger.info("用户取消了优化任务。")
            self.cancelled.emit()
        except ImpossibleTargetError as error:
            logger.warning("目标无法达到：%s", error)
            self.failed.emit(str(error))
        except OptimizationError as error:
            logger.error("优化失败：%s", error)
            self.failed.emit(str(error))
        except PixelPackError as error:
            logger.error("PixelPack 错误：%s", error)
            self.failed.emit(str(error))
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("未预期的错误")
            self.failed.emit(f"发生未预期的错误：{error}\n\n{traceback.format_exc()}")
        else:
            self.finished.emit(result)

    # -- internals ---------------------------------------------------------
    def _emit_progress(self, update: ProgressUpdate) -> None:
        # A broken slot must never abort the run; the optimiser already guards
        # its own callback, this is belt and braces across the thread boundary.
        try:
            self.progress.emit(update)
        except RuntimeError:  # pragma: no cover - window closed mid-run
            logger.debug("进度信号发送失败，接收方可能已销毁。")
        except Exception:  # pragma: no cover - defensive
            logger.exception("发送进度时出错")


class OptimizationTask(QObject):
    """Owns the worker thread so the window only has to create and forget.

    Usage::

        task = OptimizationTask(settings)
        task.progress.connect(self._on_progress)
        task.finished.connect(self._on_finished)
        task.start()
        ...
        task.cancel()
    """

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    #: Emitted after the thread has fully stopped, whichever way it ended.
    done = Signal()

    def __init__(
        self,
        settings: OptimizationSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._thread.setObjectName("pixelpack-optimizer")
        self._worker = OptimizationWorker(settings)
        self._worker.moveToThread(self._thread)
        self._started = False
        self._finished = False

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        # Quit the thread's event loop the instant the run returns, on the
        # worker thread itself. Doing it via a queued connection would need the
        # GUI event loop to be spinning, and a task must be able to finish even
        # when it is not (headless runs, tests, shutdown).
        for signal in (self._worker.finished, self._worker.failed, self._worker.cancelled):
            signal.connect(self._quit_thread, Qt.ConnectionType.DirectConnection)

        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)

    # -- properties --------------------------------------------------------
    @property
    def settings(self) -> OptimizationSettings:
        return self._worker.settings

    @property
    def is_running(self) -> bool:
        return self._started and not self._finished

    # -- control -----------------------------------------------------------
    def start(self) -> None:
        if self._started:
            raise RuntimeError("该任务已经启动过了。")
        self._started = True
        self._thread.start()

    def cancel(self) -> None:
        self._worker.cancel()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until the thread stops. For tests and shutdown only."""
        return self._thread.wait(timeout_ms)

    def stop(self, timeout_ms: int = 30_000) -> bool:
        """Cancel and join the thread. Safe to call more than once."""
        self._worker.cancel()
        if self._thread.isRunning():
            self._thread.quit()
        return self._thread.wait(timeout_ms)

    # -- teardown ----------------------------------------------------------
    @Slot()
    def _quit_thread(self) -> None:
        """Runs on the worker thread the moment the run returns."""
        self._finished = True
        self._thread.quit()

    def _on_finished(self, result) -> None:
        self._thread.wait(10_000)
        self.finished.emit(result)
        self.done.emit()

    def _on_failed(self, message: str) -> None:
        self._thread.wait(10_000)
        self.failed.emit(message)
        self.done.emit()

    def _on_cancelled(self) -> None:
        self._thread.wait(10_000)
        self.cancelled.emit()
        self.done.emit()
