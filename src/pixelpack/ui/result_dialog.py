"""What the user sees when a run finishes.

Two halves: the headline numbers (did it fit, how much did we save, how close
did we get to the ceiling) and the per-image table (what happened to each
file). The table is deliberately sortable — the interesting rows are usually
"the ones that shrank the most" or "the ones that did not shrink at all".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models.result import ImageOutcome, OptimizationResult
from ..utils.logging import get_logger
from ..utils.sizes import format_percent, format_size

logger = get_logger(__name__)

COLUMNS = ["文件名", "原尺寸", "最终尺寸", "原大小", "最终大小", "缩放比例"]

#: Sort keys attached to each row so numeric columns sort numerically.
_SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class _SortableItem(QTableWidgetItem):
    """A cell that sorts by its raw value rather than its display text.

    Without this, "10.00 MB" would sort before "9.00 MB" and "100%" before
    "99%", which makes the table useless for spotting outliers.
    """

    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        mine = self.data(_SORT_ROLE)
        theirs = other.data(_SORT_ROLE)
        if mine is None or theirs is None:
            return super().__lt__(other)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


def _reveal_in_explorer(path: Path) -> None:
    """Open the OS file manager with *path* selected."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        logger.exception("无法打开文件管理器")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class _Metric(QWidget):
    """A small "label over value" cell used in the summary grid."""

    def __init__(self, key: str, value: str, *, accent: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        key_label = QLabel(key)
        key_label.setObjectName("MetricKey")

        value_label = QLabel(value)
        value_label.setObjectName("MetricValueAccent" if accent else "MetricValue")
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout.addWidget(key_label)
        layout.addWidget(value_label)
        self.value_label = value_label
        self.key_label = key_label


class ResultDialog(QDialog):
    """Summary + per-image breakdown of a finished run."""

    def __init__(self, result: OptimizationResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result = result

        self.setWindowTitle("优化完成")
        self.setModal(True)
        self.resize(940, 640)
        self.setSizeGripEnabled(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(14)

        root.addWidget(self._build_header())
        root.addWidget(self._build_summary())
        root.addWidget(self._build_table(), 1)
        root.addLayout(self._build_buttons())

    # -- header ------------------------------------------------------------
    def _build_header(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("优化完成")
        title.setObjectName("Title")

        result = self._result
        if result.copied_original:
            detail = "原始文件已经满足大小限制，未重新编码，画质完全保留。"
        else:
            saved = format_percent(1.0 - result.compression_ratio)
            detail = (
                f"已将 {result.image_count} 张图片压缩到 "
                f"{format_size(result.final_bytes)}，比原始文件夹节省 {saved}。"
            )

        subtitle = QLabel(detail)
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return box

    # -- summary -----------------------------------------------------------
    def _build_summary(self) -> QWidget:
        result = self._result

        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(14)

        headroom = result.headroom_bytes
        metrics = [
            ("原始大小", format_size(result.original_bytes), False),
            ("最终 ZIP 大小", format_size(result.final_bytes), True),
            ("目标大小", format_size(result.target_bytes), False),
            ("压缩比例", format_percent(result.compression_ratio), False),
            ("ZIP 空间利用率", format_percent(result.utilization), True),
            ("图片数量", f"{result.image_count} 张", False),
            ("平均原始尺寸", result.avg_original_dimensions, False),
            ("平均最终尺寸", result.avg_final_dimensions, False),
        ]

        for index, (key, value, accent) in enumerate(metrics):
            grid.addWidget(_Metric(key, value, accent=accent), index // 4, index % 4)

        # A one-line verdict about how tight the fit is.
        verdict = QLabel(self._verdict_text(headroom))
        verdict.setObjectName("SuccessLabel" if result.verified else "ErrorLabel")
        verdict.setWordWrap(True)
        grid.addWidget(verdict, 2, 0, 1, 4)

        if result.has_fallbacks:
            note = QLabel(
                "部分图片无法解码，已按原文件直接打包（表格中标注为「原样保留」）。"
            )
            note.setObjectName("Muted")
            note.setWordWrap(True)
            grid.addWidget(note, 3, 0, 1, 4)

        return card

    def _verdict_text(self, headroom: int) -> str:
        result = self._result
        if result.copied_original:
            return "✓ 未超出目标大小，且原始画质 100% 保留。"
        if headroom <= 0:
            return "✓ 最终 ZIP 正好落在目标大小以内。"
        return (
            f"✓ 最终 ZIP 未超出目标大小，还剩余 {format_size(headroom)} 空间"
            f"（利用率 {format_percent(result.utilization)}）。"
        )

    # -- table -------------------------------------------------------------
    def _build_table(self) -> QWidget:
        table = QTableWidget(len(self._result.outcomes), len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Sorting stays off while filling: enabling it first makes Qt re-sort on
        # every setItem and the rows end up scrambled.
        table.setSortingEnabled(False)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        for row, outcome in enumerate(self._result.outcomes):
            self._fill_row(table, row, outcome)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        table.setSortingEnabled(True)
        table.sortItems(0, Qt.SortOrder.AscendingOrder)

        self._table = table
        return table

    def _fill_row(self, table: QTableWidget, row: int, outcome: ImageOutcome) -> None:
        cells = [
            (outcome.rel_path, outcome.rel_path.lower()),
            (outcome.orig_dimensions, outcome.orig_width * outcome.orig_height),
            (outcome.final_dimensions, outcome.final_width * outcome.final_height),
            (format_size(outcome.orig_bytes), outcome.orig_bytes),
            (format_size(outcome.final_bytes), outcome.final_bytes),
            (
                "原样保留" if outcome.fallback else format_percent(outcome.scale),
                outcome.scale,
            ),
        ]

        for column, (text, sort_key) in enumerate(cells):
            item = _SortableItem(text)
            item.setData(_SORT_ROLE, sort_key)

            if column == 0:
                tooltip = outcome.rel_path
                if outcome.fallback:
                    tooltip += "\n（无法解码，已按原文件打包）"
                item.setToolTip(tooltip)
            elif column in (3, 4, 5) and not outcome.fallback:
                item.setToolTip(
                    f"{format_size(outcome.orig_bytes)} → {format_size(outcome.final_bytes)}"
                    f"，节省 {format_size(outcome.bytes_saved)}"
                )
            else:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )

            table.setItem(row, column, item)

    # -- buttons -----------------------------------------------------------
    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        open_zip = QPushButton("打开 ZIP")
        open_zip.clicked.connect(self._open_zip)

        open_folder = QPushButton("打开所在文件夹")
        open_folder.clicked.connect(self._open_folder)

        copy_path = QPushButton("复制路径")
        copy_path.setObjectName("Link")
        copy_path.clicked.connect(self._copy_path)

        close = QPushButton("关闭")
        close.setObjectName("Primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)

        row.addWidget(open_zip)
        row.addWidget(open_folder)
        row.addWidget(copy_path)
        row.addStretch(1)
        row.addWidget(close)
        return row

    # -- actions -----------------------------------------------------------
    def _open_zip(self) -> None:
        path = self._result.output_path
        if not path.exists():
            logger.warning("输出文件已不存在：%s", path)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_folder(self) -> None:
        _reveal_in_explorer(self._result.output_path)

    def _copy_path(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(self._result.output_path))
