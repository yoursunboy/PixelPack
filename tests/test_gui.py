"""The window itself: settings collection, live progress, and the real run.

The important test in here is
:func:`test_starting_from_the_window_produces_a_real_archive`. It clicks the
actual button, waits for the actual worker thread, and then opens the ZIP that
landed on disk. If the GUI were a shell around a fake pipeline, that test would
fail.
"""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from pixelpack import COPYRIGHT  # noqa: E402
from pixelpack.core.optimizer import Optimizer  # noqa: E402
from pixelpack.models.settings import CompressionMode  # noqa: E402
from pixelpack.ui import main_window as main_window_module  # noqa: E402
from pixelpack.ui.main_window import MainWindow  # noqa: E402
from pixelpack.ui.result_dialog import _SORT_ROLE, ResultDialog  # noqa: E402
from pixelpack.utils.sizes import GB, KB, MB  # noqa: E402
from tests.helpers import make_settings, noise_image, save_image  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app):
    win = MainWindow(restore=False)
    # No modal may ever appear during a test: offscreen Qt would block for ever.
    win.notify_warning = lambda *a, **k: None
    win.notify_error = lambda *a, **k: None
    win.ask_confirm = lambda *a, **k: False
    yield win
    if win._task is not None:
        win._task.stop(15_000)
    win.deleteLater()
    qt_app.processEvents()


@pytest.fixture
def photo_folder(tmp_path: Path) -> Path:
    root = tmp_path / "照片 素材"
    (root / "子目录").mkdir(parents=True)
    for index in range(3):
        save_image(noise_image((800, 600), seed=index), root / f"{index}.jpg", quality=95)
    save_image(noise_image((700, 560), seed=9), root / "子目录" / "图.png")
    return root


def _drain(app, predicate, timeout_ms: int = 120_000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class _Drag:
    """A drag-enter + drop pair that keeps its QMimeData alive.

    PySide6 does not take ownership of the QMimeData passed to the event
    constructors, so a temporary would be collected before the handler runs and
    the event would carry a bare QObject.
    """

    def __init__(self, path: Path) -> None:
        self.mime = QMimeData()
        self.mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.enter = QDragEnterEvent(
            QPoint(10, 10),
            Qt.DropAction.CopyAction,
            self.mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.drop = QDropEvent(
            QPoint(10, 10),
            Qt.DropAction.CopyAction,
            self.mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )


class _RecordingDialog:
    """Stands in for ResultDialog so a test run does not block on a modal."""

    instances: list["_RecordingDialog"] = []

    def __init__(self, result, parent=None) -> None:
        self.result = result
        self.parent = parent
        _RecordingDialog.instances.append(self)

    def exec(self) -> int:
        return 1


@pytest.fixture
def no_modal(monkeypatch):
    _RecordingDialog.instances.clear()
    monkeypatch.setattr(main_window_module, "ResultDialog", _RecordingDialog)
    return _RecordingDialog


@pytest.fixture
def dialogs(window):
    """Record every message box the window would have shown."""
    captured: dict[str, list] = {"warning": [], "error": [], "confirm": []}

    window.notify_warning = lambda title, message: captured["warning"].append((title, message))
    window.notify_error = lambda title, message: captured["error"].append((title, message))

    def confirm(title, message):
        captured["confirm"].append((title, message))
        return False

    window.ask_confirm = confirm
    return captured


def _run_window(window, qt_app, folder: Path, *, mb: float, precision: int = 4, **advanced):
    """Point the window at *folder*, press the button, wait for it to finish."""
    window.set_source_dir(folder)
    window.unit_combo.setCurrentText("MB")
    window.size_spin.setValue(mb)
    window.precision_spin.setValue(precision)
    # The window's shipped default (800 px) pins these fixtures at full size,
    # which would make small targets unreachable. Tests that care about the
    # protection floor pass their own value.
    window.min_edge_spin.setValue(advanced.pop("min_long_edge", 100))
    if advanced:
        window.advanced_toggle.setChecked(True)

    window.start_button.click()
    assert window._task is not None
    return window._task


# --------------------------------------------------------------------------
# Form behaviour
# --------------------------------------------------------------------------
def test_window_starts_idle(window):
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.progress_bar.value() == 0
    assert window.source_dir() is None


def test_output_path_follows_the_source_folder(window, photo_folder: Path):
    window.set_source_dir(photo_folder)

    assert window.output_path() == photo_folder.parent / f"{photo_folder.name}_optimized.zip"


def test_a_hand_typed_output_path_is_not_overwritten(
    window, photo_folder: Path, tmp_path: Path
):
    window.set_source_dir(photo_folder)
    custom = tmp_path / "custom.zip"
    window.output_edit.setText(str(custom))
    window._on_output_edited(str(custom))

    window.set_source_dir(tmp_path / "another")

    assert window.output_path() == custom


def test_output_gets_a_zip_suffix(window, photo_folder: Path):
    window.set_source_dir(photo_folder)
    window.output_edit.setText(str(photo_folder.parent / "no_suffix"))
    window._on_output_edited("x")

    assert window.output_path().suffix == ".zip"


@pytest.mark.parametrize(
    "unit,value,expected",
    [("MB", 20.0, 20 * MB), ("MB", 1.0, MB), ("GB", 1.0, GB), ("GB", 1.5, int(1.5 * GB))],
)
def test_size_units_are_binary(window, unit: str, value: float, expected: int):
    window.unit_combo.setCurrentText(unit)
    window.size_spin.setValue(value)

    assert window.target_bytes() == expected


def test_target_hint_reflects_the_size(window):
    window.unit_combo.setCurrentText("MB")
    window.size_spin.setValue(20.0)

    assert "20,971,520" in window.target_hint.text()


def test_mode_selector_lists_the_three_modes(window):
    labels = [window.mode_combo.itemText(i) for i in range(window.mode_combo.count())]
    assert labels == ["画质优先", "平衡", "体积优先"]

    window.mode_combo.setCurrentIndex(0)
    assert window.selected_mode() is CompressionMode.QUALITY
    assert window.mode_hint.text() == CompressionMode.QUALITY.description

    # Every entry must round-trip back to a real enum member, not a bare str.
    for index in range(window.mode_combo.count()):
        window.mode_combo.setCurrentIndex(index)
        assert isinstance(window.selected_mode(), CompressionMode)


def test_advanced_options_reach_the_settings(window, photo_folder: Path):
    window.set_source_dir(photo_folder)
    window.advanced_toggle.setChecked(True)
    window.subdirs_check.setChecked(False)
    window.exif_check.setChecked(False)
    window.min_edge_spin.setValue(1234)
    window.precision_spin.setValue(5)
    window.mode_combo.setCurrentIndex(list(CompressionMode).index(CompressionMode.SIZE))

    settings = window.build_settings()

    assert settings.include_subdirs is False
    assert settings.keep_exif is False
    assert settings.min_long_edge == 1234
    assert settings.precision == 5
    assert settings.mode is CompressionMode.SIZE
    assert settings.source_dir == photo_folder


def test_advanced_panel_starts_collapsed(window):
    # isHidden() rather than isVisible(): the window itself is never shown in
    # an offscreen test, so every child reports isVisible() == False.
    assert window.advanced_panel.isHidden()

    window.advanced_toggle.setChecked(True)
    assert not window.advanced_panel.isHidden()

    window.advanced_toggle.setChecked(False)
    assert window.advanced_panel.isHidden()


# --------------------------------------------------------------------------
# Status bar
# --------------------------------------------------------------------------
def test_the_status_bar_carries_the_copyright_on_the_right(qt_app, window):
    """The copyright sits at the right, and a status message cannot cover it.

    addPermanentWidget is what puts it there and keeps it out of showMessage()'s
    way; addWidget would have parked it on the left where the first progress
    update would have painted over it.
    """
    bar = window.statusBar()
    labels = [label for label in bar.findChildren(QLabel) if label.text() == COPYRIGHT]
    assert labels, "状态栏没有显示版权信息"

    label = labels[0]

    # show() is what activates the layout -- until then the status bar is still
    # at its unlaid-out default geometry and every child position is a fiction.
    # Resizing, processEvents and layout().activate() all leave it wrong.
    window.resize(880, 720)
    window.show()
    qt_app.processEvents()
    try:
        # Measured against the right edge, not the midpoint: the label is only
        # as wide as its own text, and that text is wide enough to reach past
        # the middle of the bar. Where it ends is what says "on the right".
        gap = bar.width() - (label.x() + label.width())
        assert 0 <= gap < 40, f"版权信息应当贴着状态栏右侧（右边距 {gap}px）"

        # A running message must not displace it.
        bar.showMessage("正在优化…")
        qt_app.processEvents()
        assert label.isHidden() is False
        assert label.text() == COPYRIGHT
        assert bar.width() - (label.x() + label.width()) == gap, "状态消息挤动了版权信息"
    finally:
        window.hide()


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def test_starting_without_a_folder_is_refused(window, dialogs):
    window._start()

    assert dialogs["warning"], "未选择文件夹时应当提示用户"
    assert window._task is None


def test_starting_with_a_missing_folder_is_refused(window, tmp_path: Path, dialogs):
    window.set_source_dir(tmp_path / "does-not-exist")
    window._start()

    assert dialogs["warning"]
    assert window._task is None


# --------------------------------------------------------------------------
# Drag and drop
# --------------------------------------------------------------------------
def test_dropping_a_folder_sets_the_source(window, photo_folder: Path):
    drag = _Drag(photo_folder)

    window.dragEnterEvent(drag.enter)
    assert drag.enter.isAccepted(), "拖入文件夹应当被接受"

    window.dropEvent(drag.drop)

    assert drag.drop.isAccepted()
    assert window.source_dir() == photo_folder


def test_dropping_a_file_is_ignored(window, tmp_path: Path):
    a_file = tmp_path / "note.txt"
    a_file.write_text("hi", encoding="utf-8")

    drag = _Drag(a_file)
    window.dragEnterEvent(drag.enter)

    assert not drag.enter.isAccepted()


# --------------------------------------------------------------------------
# The run, end to end, through the window
# --------------------------------------------------------------------------
def test_starting_from_the_window_produces_a_real_archive(
    qt_app, window, photo_folder: Path, no_modal
):
    """Click 开始优化 and check the ZIP that lands on disk."""
    _run_window(window, qt_app, photo_folder, mb=0.25)
    output = window.output_path()

    seen: list = []
    window.optimization_finished.connect(seen.append)

    assert not window.start_button.isEnabled(), "运行期间「开始优化」应当禁用"
    assert window.cancel_button.isEnabled()

    assert _drain(qt_app, lambda: bool(seen)), "任务没有在超时前完成"
    assert _drain(qt_app, lambda: window.start_button.isEnabled())

    result = seen[0]
    target = int(0.25 * MB)
    assert result.target_bytes == target
    assert result.final_bytes <= target
    assert output.exists()
    assert output.stat().st_size == result.final_bytes
    assert output.stat().st_size <= target

    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == 4
        assert "子目录/图.png" in archive.namelist()

    # The result dialog was shown with the very same result object.
    assert no_modal.instances
    assert no_modal.instances[-1].result is result

    # The window is usable again, and the live labels kept their values.
    assert window.source_edit.isEnabled()
    assert window.metric_zip.value() != "—"
    assert window.stage_label.text() == "优化完成"


def test_progress_reaches_the_widgets(qt_app, window, photo_folder: Path, no_modal):
    task = _run_window(window, qt_app, photo_folder, mb=0.3)

    stages: list[str] = []
    task.progress.connect(lambda update: stages.append(update.stage))

    assert _drain(qt_app, lambda: window.start_button.isEnabled())

    assert stages, "进度信号没有到达界面层"
    assert stages[0] == "scan"
    assert stages[-1] == "final"
    assert "search" in stages
    assert window.progress_bar.value() == 1000


def test_cancelling_from_the_window_leaves_nothing_behind(
    qt_app, window, photo_folder: Path, no_modal
):
    task = _run_window(window, qt_app, photo_folder, mb=0.18, precision=9, min_long_edge=100)
    output = window.output_path()

    cancelled: list = []
    task.cancelled.connect(lambda: cancelled.append(True))

    window.cancel_button.click()
    assert not window.cancel_button.isEnabled(), "取消按钮应当立刻禁用"

    assert _drain(qt_app, lambda: bool(cancelled)), "取消没有生效"
    assert _drain(qt_app, lambda: window.start_button.isEnabled())

    assert not output.exists()
    assert not output.with_name(output.name + ".part").exists()
    assert window.stage_label.text() == "已取消"


def test_a_failure_is_shown_in_the_window(
    qt_app, window, tmp_path: Path, no_modal, dialogs
):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((600, 400), seed=1), root / "a.jpg", quality=95)
    save_image(noise_image((600, 400), seed=2), root / "b.jpg", quality=95)

    _run_window(window, qt_app, root, mb=0.1, min_long_edge=800)

    assert _drain(qt_app, lambda: window.start_button.isEnabled())
    assert dialogs["error"], "失败时必须弹出错误提示"
    assert not window.error_label.isHidden()
    assert window.error_label.text()
    assert window.stage_label.text() == "优化失败"


def test_closing_while_running_asks_first(
    qt_app, window, photo_folder: Path, no_modal, dialogs
):
    _run_window(window, qt_app, photo_folder, mb=0.18, precision=9, min_long_edge=100)

    event = QCloseEvent()
    window.closeEvent(event)

    assert dialogs["confirm"], "运行中关闭窗口应当先询问"
    assert not event.isAccepted(), "选择「否」时窗口应当继续开着"
    assert window._task is not None and window._task.is_running

    window.cancel_button.click()
    assert _drain(qt_app, lambda: window.start_button.isEnabled())


# --------------------------------------------------------------------------
# The result dialog
# --------------------------------------------------------------------------
@pytest.fixture
def compressed_result(photo_folder: Path):
    settings = make_settings(photo_folder, 300 * KB, precision=4, max_refine_rounds=8)
    return Optimizer(settings).run()


def test_result_dialog_shows_every_metric(qt_app, compressed_result):
    dialog = ResultDialog(compressed_result)
    try:
        assert dialog.windowTitle() == "优化完成"

        table = dialog._table
        assert table.rowCount() == compressed_result.image_count
        assert table.columnCount() == 6

        headers = [table.horizontalHeaderItem(i).text() for i in range(6)]
        assert headers == ["文件名", "原尺寸", "最终尺寸", "原大小", "最终大小", "缩放比例"]

        names = {table.item(row, 0).text() for row in range(table.rowCount())}
        assert names == {o.rel_path for o in compressed_result.outcomes}
    finally:
        dialog.deleteLater()


def test_result_dialog_sorts_sizes_numerically(qt_app, compressed_result):
    dialog = ResultDialog(compressed_result)
    try:
        table = dialog._table
        table.sortItems(3, Qt.SortOrder.AscendingOrder)

        sizes = [table.item(row, 3).data(_SORT_ROLE) for row in range(table.rowCount())]
        assert sizes == sorted(sizes), "大小列必须按数值排序，而不是按字符串"
    finally:
        dialog.deleteLater()


def test_result_dialog_handles_the_copied_original_case(qt_app, photo_folder: Path):
    settings = make_settings(photo_folder, 50 * MB)
    result = Optimizer(settings).run()
    assert result.copied_original

    dialog = ResultDialog(result)
    try:
        assert dialog._table.rowCount() == result.image_count
        # Every image kept its original size, so the scale column reads 100.00%.
        assert all(
            dialog._table.item(row, 5).data(_SORT_ROLE) == 1.0
            for row in range(dialog._table.rowCount())
        )
    finally:
        dialog.deleteLater()
