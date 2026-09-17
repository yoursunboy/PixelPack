"""The one window the user ever sees.

Layout, top to bottom: where the pictures are, how big the ZIP may be, the
mode, a collapsible advanced panel, and the progress area. Everything the
optimiser does happens on a worker thread — this file only collects settings,
relays progress and shows the result.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core.optimizer import STAGE_FINAL, STAGE_MESSAGES, STAGE_SCAN
from ..models.result import OptimizationResult, ProgressUpdate
from ..models.settings import (
    DEFAULT_MIN_LONG_EDGE,
    CompressionMode,
    OptimizationSettings,
    default_output_path,
)
from ..utils.logging import default_log_dir, get_logger
from ..utils.sizes import GB, MB, format_percent, format_size
from ..workers.optimization_worker import OptimizationTask
from .about_dialog import AboutDialog
from .result_dialog import ResultDialog

logger = get_logger(__name__)

_ORG = "PixelPack"
_APP_KEY = "PixelPack"

#: Sensible bounds for the size spinner, expressed in the selected unit.
_MIN_SIZE = 0.1
_MAX_SIZE_MB = 64 * 1024  # 64 GB

#: Below this the archive would be useless; we only warn, never block.
_TINY_TARGET_BYTES = 64 * 1024


class MainWindow(QMainWindow):
    """PixelPack's main window."""

    #: Emitted when a run completes successfully (used by tests).
    optimization_finished = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, restore: bool = True) -> None:
        super().__init__(parent)
        self._task: OptimizationTask | None = None
        self._result: OptimizationResult | None = None
        self._output_touched = False
        #: False until every widget exists; the size spinner's valueChanged can
        #: fire during construction, before the metric labels are built.
        self._ui_ready = False
        self._settings_store = QSettings(_ORG, _APP_KEY) if restore else None

        self.setWindowTitle(f"{APP_NAME} — 智能图片压缩打包工具")
        self.setMinimumSize(760, 620)
        self.resize(880, 720)
        self.setAcceptDrops(True)

        self._build_ui()
        self._ui_ready = True
        if self._settings_store is not None:
            self._restore_settings()
        self._sync_output_path()
        self._refresh_target_labels()
        self._update_idle_metrics()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        root.addWidget(self._build_header())
        root.addWidget(self._build_source_card())
        root.addWidget(self._build_target_card())
        root.addWidget(self._build_advanced_card())
        root.addWidget(self._build_progress_card())
        root.addLayout(self._build_actions())
        root.addStretch(0)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(f"就绪 · {APP_NAME} {__version__}")

    def _build_header(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title = QLabel(APP_NAME)
        title.setObjectName("Title")

        subtitle = QLabel("把整个文件夹的图片压缩打包成一个 ZIP，正好不超过你指定的大小。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return box

    # -- 1. source folder --------------------------------------------------
    def _build_source_card(self) -> QWidget:
        card, layout = _card("源文件夹")

        self.source_edit = QLineEdit()
        self.source_edit.setObjectName("DropZone")
        self.source_edit.setPlaceholderText("把文件夹拖到这里，或点击「选择文件夹…」")
        self.source_edit.setClearButtonEnabled(True)
        self.source_edit.textChanged.connect(self._on_source_changed)

        browse = QPushButton("选择文件夹…")
        browse.clicked.connect(self._choose_source)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.source_edit, 1)
        row.addWidget(browse)

        hint = QLabel("支持 JPG / PNG / WebP / BMP / TIFF，子文件夹会被保留；其它文件原样打包。")
        hint.setObjectName("SectionHint")
        hint.setWordWrap(True)

        layout.addLayout(row)
        layout.addWidget(hint)
        return card

    # -- 2. target size ----------------------------------------------------
    def _build_target_card(self) -> QWidget:
        card, layout = _card("压缩包设置")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.size_spin = QDoubleSpinBox()
        self.size_spin.setDecimals(2)
        self.size_spin.setRange(_MIN_SIZE, _MAX_SIZE_MB)
        self.size_spin.setValue(20.0)
        self.size_spin.setSingleStep(5.0)
        self.size_spin.setAccelerated(True)
        self.size_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.size_spin.setMinimumWidth(120)
        self.size_spin.valueChanged.connect(self._on_target_changed)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["MB", "GB"])
        self.unit_combo.setFixedWidth(78)
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.addWidget(self.size_spin)
        size_row.addWidget(self.unit_combo)
        size_row.addStretch(1)

        self.target_hint = QLabel()
        self.target_hint.setObjectName("SectionHint")

        size_box = QWidget()
        size_layout = QVBoxLayout(size_box)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(2)
        size_layout.addLayout(size_row)
        size_layout.addWidget(self.target_hint)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("留空则自动生成")
        self.output_edit.textEdited.connect(self._on_output_edited)

        save_as = QPushButton("另存为…")
        save_as.clicked.connect(self._choose_output)

        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(save_as)

        self.mode_combo = QComboBox()
        for mode in CompressionMode:
            # Store the value, not the enum: PySide6 converts a str-Enum to a
            # plain str on the way into the QVariant, and the identity would be
            # lost on the way back out.
            self.mode_combo.addItem(mode.label, mode.value)
        self.mode_combo.setCurrentIndex(list(CompressionMode).index(CompressionMode.BALANCED))
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("SectionHint")

        mode_box = QWidget()
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(2)
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addWidget(self.mode_hint)

        form.addRow("最大 ZIP 大小", size_box)
        form.addRow("输出文件", output_row)
        form.addRow("压缩模式", mode_box)

        layout.addLayout(form)
        self._on_mode_changed()
        return card

    # -- 3. advanced -------------------------------------------------------
    def _build_advanced_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        self.advanced_toggle = QPushButton("▸  高级设置")
        self.advanced_toggle.setObjectName("Link")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)

        self.advanced_panel = QWidget()
        panel = QFormLayout(self.advanced_panel)
        panel.setContentsMargins(0, 4, 0, 0)
        panel.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        panel.setHorizontalSpacing(12)
        panel.setVerticalSpacing(10)

        self.subdirs_check = QCheckBox("包含子文件夹（保留目录结构）")
        self.subdirs_check.setChecked(True)

        self.exif_check = QCheckBox("保留 EXIF 信息（拍摄参数、方向等）")
        self.exif_check.setChecked(True)

        self.min_edge_spin = QSpinBox()
        self.min_edge_spin.setRange(0, 20000)
        self.min_edge_spin.setValue(DEFAULT_MIN_LONG_EDGE)
        self.min_edge_spin.setSingleStep(100)
        self.min_edge_spin.setAccelerated(True)
        self.min_edge_spin.setSuffix(" px")
        self.min_edge_spin.setFixedWidth(120)
        self.min_edge_spin.setToolTip(
            "长边已经小于或等于这个值的图片不会再被缩小。设为 0 表示关闭保护。"
        )

        self.precision_spin = QSpinBox()
        self.precision_spin.setRange(2, 16)
        self.precision_spin.setValue(7)
        self.precision_spin.setFixedWidth(120)
        self.precision_spin.setToolTip(
            "二分搜索的步数。数值越大越贴近目标大小，但耗时更长。"
        )

        panel.addRow(self.subdirs_check)
        panel.addRow(self.exif_check)
        panel.addRow("最小图片长边", self.min_edge_spin)
        panel.addRow("优化精度", self.precision_spin)

        self.advanced_panel.setVisible(False)

        layout.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.advanced_panel)
        return card

    def _toggle_advanced(self, expanded: bool) -> None:
        self.advanced_toggle.setText(("▾  高级设置" if expanded else "▸  高级设置"))
        self.advanced_panel.setVisible(expanded)
        self.adjustSize()

    # -- 4. progress -------------------------------------------------------
    def _build_progress_card(self) -> QWidget:
        card, layout = _card("进度")

        self.stage_label = QLabel("尚未开始")
        self.stage_label.setObjectName("StageLabel")

        self.round_label = QLabel()
        self.round_label.setObjectName("Muted")

        stage_row = QHBoxLayout()
        stage_row.addWidget(self.stage_label)
        stage_row.addStretch(1)
        stage_row.addWidget(self.round_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)

        self.metric_target = _MetricLabel("目标")
        self.metric_zip = _MetricLabel("当前 ZIP")
        self.metric_scale = _MetricLabel("当前缩放")
        self.metric_round = _MetricLabel("优化轮次")

        metrics = QHBoxLayout()
        metrics.setSpacing(22)
        for widget in (self.metric_target, self.metric_zip, self.metric_scale, self.metric_round):
            metrics.addWidget(widget)
        metrics.addStretch(1)

        self.error_label = QLabel()
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)

        layout.addLayout(stage_row)
        layout.addWidget(self.progress_bar)
        layout.addLayout(metrics)
        layout.addWidget(self.error_label)
        return card

    # -- 5. actions --------------------------------------------------------
    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.start_button = QPushButton("开始优化")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.logs_button = QPushButton("日志")
        self.logs_button.setObjectName("Link")
        self.logs_button.clicked.connect(self._show_log_dir)

        self.about_button = QPushButton("关于")
        self.about_button.setObjectName("Link")
        self.about_button.setToolTip(f"关于 {APP_NAME}")
        self.about_button.clicked.connect(self.show_about)

        row.addWidget(self.logs_button)
        row.addWidget(self.about_button)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.start_button)
        return row

    # ------------------------------------------------------- source / output
    def _choose_source(self) -> None:
        start = self.source_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择包含图片的文件夹", start)
        if chosen:
            self.set_source_dir(Path(chosen))

    def set_source_dir(self, path: Path) -> None:
        """Point the window at *path* (also used by drag & drop and tests)."""
        self.source_edit.setText(str(path))

    def source_dir(self) -> Path | None:
        text = self.source_edit.text().strip().strip('"')
        if not text:
            return None
        return Path(text)

    def _on_source_changed(self, _text: str) -> None:
        if not self._output_touched:
            self._sync_output_path()
        self._update_idle_metrics()

    def _sync_output_path(self) -> None:
        source = self.source_dir()
        if not source:
            self.output_edit.clear()
            return
        self.output_edit.setText(str(default_output_path(source)))

    def _on_output_edited(self, _text: str) -> None:
        self._output_touched = True

    def _choose_output(self) -> None:
        source = self.source_dir()
        suggested = self.output_edit.text().strip()
        if not suggested:
            suggested = str(
                default_output_path(source) if source else Path.home() / "archive.zip"
            )
        chosen, _ = QFileDialog.getSaveFileName(
            self, "保存 ZIP 到", suggested, "ZIP 压缩包 (*.zip)"
        )
        if chosen:
            self._output_touched = True
            self.output_edit.setText(chosen)

    def output_path(self) -> Path | None:
        text = self.output_edit.text().strip().strip('"')
        if not text:
            source = self.source_dir()
            return default_output_path(source) if source else None
        path = Path(text)
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        return path

    # -------------------------------------------------------------- target
    def _unit_factor(self) -> int:
        return GB if self.unit_combo.currentText() == "GB" else MB

    def target_bytes(self) -> int:
        return int(round(self.size_spin.value() * self._unit_factor()))

    def _on_unit_changed(self) -> None:
        is_gb = self.unit_combo.currentText() == "GB"
        # Keep the same numeric ceiling in whichever unit is selected.
        self.size_spin.setRange(_MIN_SIZE, _MAX_SIZE_MB / 1024 if is_gb else _MAX_SIZE_MB)
        self._on_target_changed()

    def _on_target_changed(self) -> None:
        if not self._ui_ready:
            return
        self._refresh_target_labels()

    def _refresh_target_labels(self) -> None:
        target = self.target_bytes()
        self.metric_target.set(format_size(target), emphasise=True)

        if target < _TINY_TARGET_BYTES:
            self._warn(f"目标大小只有 {format_size(target)}，可能无法装下任何图片。")
        else:
            self._warn(None)

    def selected_mode(self) -> CompressionMode:
        """The compression mode the user picked."""
        try:
            return CompressionMode(self.mode_combo.currentData())
        except ValueError:
            return CompressionMode.BALANCED

    def _on_mode_changed(self) -> None:
        self.mode_hint.setText(self.selected_mode().description)

    def _update_idle_metrics(self) -> None:
        if self._task is not None:
            return
        self.metric_target.set(format_size(self.target_bytes()), emphasise=True)
        self.metric_zip.set("—")
        self.metric_scale.set("—")
        self.metric_round.set("—")

    # ---------------------------------------------------------------- drag
    def _drop_target(self, event) -> Path | None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._drop_target(event) is not None:
            self.source_edit.setProperty("dragActive", True)
            self._repolish(self.source_edit)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.source_edit.setProperty("dragActive", False)
        self._repolish(self.source_edit)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.source_edit.setProperty("dragActive", False)
        self._repolish(self.source_edit)
        path = self._drop_target(event)
        if path is None:
            event.ignore()
            return
        self.set_source_dir(path)
        event.acceptProposedAction()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    # ----------------------------------------------------------- run cycle
    def build_settings(self) -> OptimizationSettings:
        """Collect the form into a validated settings object.

        Raises:
            ValueError: with a message that is safe to show to the user.
        """
        source = self.source_dir()
        if source is None:
            raise ValueError("请先选择包含图片的源文件夹。")
        if not source.is_dir():
            raise ValueError(f"源文件夹不存在：{source}")

        output = self.output_path()
        if output is None:
            raise ValueError("请指定输出 ZIP 的位置。")
        if output.is_dir():
            raise ValueError(f"输出路径是一个文件夹：{output}")

        mode = self.selected_mode()

        return OptimizationSettings(
            source_dir=source,
            target_bytes=self.target_bytes(),
            output_path=output,
            mode=mode,
            include_subdirs=self.subdirs_check.isChecked(),
            keep_exif=self.exif_check.isChecked(),
            min_long_edge=self.min_edge_spin.value(),
            precision=self.precision_spin.value(),
        ).validated()

    def _start(self) -> None:
        if self._task is not None:
            return

        try:
            settings = self.build_settings()
        except ValueError as error:
            self.notify_warning("无法开始", str(error))
            return

        self._save_settings(settings)
        self._set_running(True)
        self.error_label.setVisible(False)
        self.progress_bar.setValue(0)
        self.stage_label.setText(STAGE_MESSAGES[STAGE_SCAN])
        self.round_label.clear()
        self.statusBar().showMessage("正在优化…")

        self._task = OptimizationTask(settings, parent=self)
        self._task.progress.connect(self._on_progress)
        self._task.finished.connect(self._on_finished)
        self._task.failed.connect(self._on_failed)
        self._task.cancelled.connect(self._on_cancelled)
        self._task.done.connect(self._on_task_done)
        self._task.start()

    def _cancel(self) -> None:
        if self._task is None:
            return
        self.cancel_button.setEnabled(False)
        self.stage_label.setText("正在取消…")
        self.statusBar().showMessage("正在取消，请稍候…")
        self._task.cancel()

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        for widget in (
            self.source_edit,
            self.size_spin,
            self.unit_combo,
            self.output_edit,
            self.mode_combo,
            self.subdirs_check,
            self.exif_check,
            self.min_edge_spin,
            self.precision_spin,
        ):
            widget.setEnabled(not running)

    # -------------------------------------------------------- progress slots
    def _on_progress(self, update: ProgressUpdate) -> None:
        self.stage_label.setText(update.message)
        self.progress_bar.setValue(int(max(0.0, min(1.0, update.fraction)) * 1000))

        if update.target_bytes:
            self.metric_target.set(format_size(update.target_bytes), emphasise=True)
        if update.current_zip_bytes:
            self.metric_zip.set(format_size(update.current_zip_bytes))
        if update.scale:
            self.metric_scale.set(format_percent(update.scale))
        if update.round_index or update.total_rounds:
            if update.total_rounds:
                self.metric_round.set(f"{update.round_index} / {update.total_rounds}")
            else:
                self.metric_round.set(str(update.round_index))

        if update.stage != STAGE_FINAL:
            self.round_label.setText(f"第 {max(1, update.round_index)} 轮")
        else:
            self.round_label.clear()

    def _on_finished(self, result: OptimizationResult) -> None:
        self._result = result
        self.progress_bar.setValue(1000)
        self.stage_label.setText("优化完成")
        self.round_label.clear()
        self.metric_zip.set(format_size(result.final_bytes))
        self.metric_scale.set(format_percent(result.scale))
        self.metric_round.set(str(result.rounds))

        self.statusBar().showMessage(
            f"完成：{format_size(result.final_bytes)} / 目标 {format_size(result.target_bytes)}"
            f"（利用率 {format_percent(result.utilization)}）"
        )
        self.optimization_finished.emit(result)

        dialog = ResultDialog(result, self)
        dialog.exec()

    def _on_failed(self, message: str) -> None:
        self.stage_label.setText("优化失败")
        self.round_label.clear()
        self._warn(None)
        self.error_label.setText(message)
        self.error_label.setVisible(True)
        self.statusBar().showMessage("优化失败")
        self.notify_error("优化失败", message)

    def _on_cancelled(self) -> None:
        self.stage_label.setText("已取消")
        self.round_label.clear()
        self.progress_bar.setValue(0)
        self._update_idle_metrics()
        self.statusBar().showMessage("已取消，未生成任何文件。")

    def _on_task_done(self) -> None:
        if self._task is not None:
            self._task.deleteLater()
            self._task = None
        self._set_running(False)
        self.cancel_button.setEnabled(False)

    # -------------------------------------------------------------- helpers
    def notify_warning(self, title: str, message: str) -> None:
        """Show a modal warning. Overridden in tests."""
        QMessageBox.warning(self, title, message)

    def notify_error(self, title: str, message: str) -> None:
        """Show a modal error. Overridden in tests."""
        QMessageBox.critical(self, title, message)

    def ask_confirm(self, title: str, message: str) -> bool:
        """Ask a yes/no question, defaulting to "no". Overridden in tests."""
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _warn(self, message: str | None) -> None:
        """Show a non-blocking caution next to the size spinner."""
        if message:
            self.target_hint.setText(f"⚠ {message}")
            self.target_hint.setObjectName("ErrorLabel")
        else:
            self.target_hint.setText(
                f"= {self.target_bytes():,} 字节（1 MB = 1024 × 1024 字节）"
            )
            self.target_hint.setObjectName("SectionHint")
        self._repolish(self.target_hint)

    def _show_log_dir(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(default_log_dir())))

    def show_about(self) -> None:
        """Open the About dialog. Modal; tests patch ``AboutDialog``."""
        AboutDialog(self).exec()

    # ----------------------------------------------------------- persistence
    def _save_settings(self, settings: OptimizationSettings) -> None:
        if self._settings_store is None:
            return
        try:
            store = self._settings_store
            store.setValue("source_dir", str(settings.source_dir))
            store.setValue("size_value", self.size_spin.value())
            store.setValue("size_unit", self.unit_combo.currentText())
            store.setValue("output_path", str(settings.output_path))
            store.setValue("mode", settings.mode.value)
            store.setValue("include_subdirs", settings.include_subdirs)
            store.setValue("keep_exif", settings.keep_exif)
            store.setValue("min_long_edge", settings.min_long_edge)
            store.setValue("precision", settings.precision)
            store.setValue("advanced_open", self.advanced_toggle.isChecked())
            store.sync()
        except Exception:  # pragma: no cover - a broken registry must not matter
            logger.exception("保存设置失败")

    def _restore_settings(self) -> None:
        try:
            store = self._settings_store
            if store is None:
                return

            unit = str(store.value("size_unit", "MB"))
            index = self.unit_combo.findText(unit)
            if index >= 0:
                self.unit_combo.setCurrentIndex(index)

            self.size_spin.setValue(float(store.value("size_value", 20.0)))

            mode_value = str(store.value("mode", CompressionMode.BALANCED.value))
            for index in range(self.mode_combo.count()):
                if str(self.mode_combo.itemData(index)) == mode_value:
                    self.mode_combo.setCurrentIndex(index)
                    break

            self.subdirs_check.setChecked(_as_bool(store.value("include_subdirs", True)))
            self.exif_check.setChecked(_as_bool(store.value("keep_exif", True)))
            self.min_edge_spin.setValue(int(store.value("min_long_edge", DEFAULT_MIN_LONG_EDGE)))
            self.precision_spin.setValue(int(store.value("precision", 7)))

            source = str(store.value("source_dir", ""))
            if source and Path(source).is_dir():
                self.source_edit.setText(source)

            output = str(store.value("output_path", ""))
            if output:
                self.output_edit.setText(output)
                self._output_touched = True

            if _as_bool(store.value("advanced_open", False)):
                self.advanced_toggle.setChecked(True)
        except Exception:  # pragma: no cover - restore is strictly best effort
            logger.exception("读取上次设置失败")

    # ---------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._task is not None and self._task.is_running:
            if not self.ask_confirm(
                "仍在优化",
                "优化还在进行中。要取消并退出吗？\n\n源文件不会被修改，也不会留下损坏的 ZIP。",
            ):
                event.ignore()
                return

            self._task.stop(15_000)

        event.accept()


# --------------------------------------------------------------------------
# Small building blocks
# --------------------------------------------------------------------------
def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """A titled card frame with a ready-to-fill vertical layout."""
    card = QFrame()
    card.setObjectName("Card")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(10)

    heading = QLabel(title)
    heading.setObjectName("MetricKey")
    heading.setStyleSheet("font-weight: 600;")

    outer.addWidget(heading)
    return card, outer


class _MetricLabel(QWidget):
    """A caption over a value, shown in the progress card."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._caption = QLabel(caption)
        self._caption.setObjectName("MetricKey")

        self._value = QLabel("—")
        self._value.setObjectName("MetricValue")
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout.addWidget(self._caption)
        layout.addWidget(self._value)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set(self, text: str, *, emphasise: bool = False) -> None:
        self._value.setText(text)
        self._value.setObjectName("MetricValueAccent" if emphasise else "MetricValue")
        style = self._value.style()
        style.unpolish(self._value)
        style.polish(self._value)

    def value(self) -> str:
        return self._value.text()


def _as_bool(value) -> bool:
    """QSettings hands back strings on some platforms and bools on others."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}
