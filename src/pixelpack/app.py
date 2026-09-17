"""Application entry point: ``python -m pixelpack`` / ``PixelPack.exe``."""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_TITLE, __version__
from .ui.icons import app_icon
from .ui.main_window import MainWindow
from .ui.style import build_stylesheet
from .utils.logging import get_logger, setup_logging


def _set_windows_app_id() -> None:
    """Give Windows a stable identity so the taskbar uses our icon."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PixelPack.SmartImageArchiveOptimizer.1"
        )
    except Exception:  # pragma: no cover - cosmetic only
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixelpack",
        description=f"{APP_TITLE} — 把文件夹里的图片压缩打包成指定大小的 ZIP。",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="输出调试日志"
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="只构建界面然后退出，用于检查打包结果是否完整",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        help="启动时直接载入的源文件夹",
    )
    return parser


def _self_check(window: MainWindow, theme_name: str) -> int:
    """Build the whole UI and report, without entering the event loop.

    ``--version`` only proves the modules import. A frozen bundle that is
    missing a Qt plugin still imports fine and only fails once a widget is
    actually constructed, so the packaging script runs this instead. The About
    dialog is built for the same reason: a logo that was not collected into the
    bundle is invisible until something tries to load it.
    """
    from .ui.about_dialog import AboutDialog
    from .ui.result_dialog import ResultDialog

    # QWidget.styleSheet() only reports a sheet set on that widget; the one
    # build_stylesheet() applies lives on the QApplication.
    app = QApplication.instance()
    applied = app.styleSheet() if app is not None else ""

    about = AboutDialog()
    logo = "已加载" if about.logo_loaded else "缺失"
    about.deleteLater()

    icon = app.windowIcon() if app is not None else None
    icon_state = "已加载" if icon is not None and not icon.isNull() else "缺失"

    print(f"{APP_NAME} {__version__}")
    print(f"主题 {theme_name}，样式表 {len(applied)} 字符")
    print(f"应用图标 {icon_state}")
    print(f"窗口标题 {window.windowTitle()}")
    print(f"目标大小 {window.target_bytes()} 字节")
    print(f"压缩模式 {window.selected_mode().value}")
    print(f"结果对话框可构建 {ResultDialog.__name__}")
    print(f"关于窗口可构建，Logo {logo}")
    print("自检通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    logger = get_logger(__name__)
    logger.info("%s %s 启动", APP_NAME, __version__)

    _set_windows_app_id()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Reuse an existing instance: Qt forbids a second QApplication, and the
    # entry point is worth being able to drive more than once per process.
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName("pixelpack")
    # Set before any window is constructed so every one of them — main window,
    # About, results — inherits it for the title bar and the taskbar.
    app.setWindowIcon(app_icon())

    stylesheet, theme = build_stylesheet(app)
    app.setStyleSheet(stylesheet)
    logger.debug("已应用 %s 主题", theme.name)

    window = MainWindow()
    if args.folder is not None:
        folder = args.folder.expanduser()
        if folder.is_dir():
            window.set_source_dir(folder.resolve())
        else:
            logger.warning("启动参数中的文件夹不存在：%s", folder)

    if args.self_check:
        return _self_check(window, theme.name)

    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
