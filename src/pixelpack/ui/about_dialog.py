"""The About box.

Deliberately not a QMessageBox: the logo and the two clickable contact rows do
not fit that widget, and the rest of the application does not look like one.
Every colour and font comes from the shared stylesheet, so the dialog follows
the light and dark themes without knowing they exist.

The version string is read from the package, never written here — see
``pixelpack/__init__.py``, which ``pyproject.toml`` also reads the version from.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import (
    APP_NAME,
    COPYRIGHT,
    DESCRIPTION,
    DEVELOPER,
    EMAIL,
    IM_CONTACT,
    __version__,
)
from ..utils.logging import get_logger
from ..utils.resources import asset_path

logger = get_logger(__name__)

#: The source logo is 200x200, so this is a 1:1 blit on a normal display and a
#: clean 2x downscale on a HiDPI one.
LOGO_SIZE = 200

DIALOG_WIDTH = 620

#: How long the "copied" confirmation stays up.
COPY_FEEDBACK_MS = 1600


class AboutDialog(QDialog):
    """Logo, version and contact details."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setModal(True)
        # The width is fixed so the description wraps predictably; the height
        # follows from the content.
        self.setFixedWidth(DIALOG_WIDTH)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        # Owned by the dialog, so it dies with it. A bare singleShot() holding
        # a closure would still fire after the dialog is gone.
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(COPY_FEEDBACK_MS)
        self._copy_timer.timeout.connect(self._clear_copy_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(16)

        body = QHBoxLayout()
        body.setSpacing(20)
        body.addWidget(self._build_logo(), 0, Qt.AlignmentFlag.AlignTop)
        body.addWidget(self._build_info(), 1)
        root.addLayout(body)

        root.addWidget(self._divider())
        root.addLayout(self._build_footer())

    # -- left ---------------------------------------------------------------
    def _build_logo(self) -> QWidget:
        label = QLabel()
        label.setObjectName("AboutLogo")
        label.setFixedSize(LOGO_SIZE, LOGO_SIZE)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        path = asset_path("logo.png")
        pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        if pixmap.isNull():
            # Never fatal: the dialog is still useful without its picture.
            logger.warning("找不到 Logo 资源，关于窗口将以纯文字显示")
            self.logo_loaded = False
            label.setText(APP_NAME)
            label.setObjectName("Title")
            return label

        ratio = self.devicePixelRatioF() or 1.0
        scaled = pixmap.scaled(
            round(LOGO_SIZE * ratio),
            round(LOGO_SIZE * ratio),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)

        label.setPixmap(scaled)
        # The artwork has a near-white background baked in rather than an alpha
        # channel, so it gets a white plate with rounded corners: that matches
        # the image exactly in the light theme and reads as a deliberate tile
        # rather than a bright rectangle in the dark one.
        label.setStyleSheet("background-color: #ffffff; border-radius: 14px;")
        self.logo_loaded = True
        return label

    # -- right --------------------------------------------------------------
    def _build_info(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(4)

        name = QLabel(APP_NAME)
        name.setObjectName("Title")

        # Read from the package constants -- see the module docstring.
        version = QLabel(f"版本 {__version__}")
        version.setObjectName("Subtitle")

        description = QLabel(DESCRIPTION)
        description.setObjectName("Muted")
        description.setWordWrap(True)

        layout.addWidget(name)
        layout.addWidget(version)
        layout.addSpacing(6)
        layout.addWidget(description)
        layout.addSpacing(12)
        layout.addWidget(self._build_contacts())
        layout.addStretch(1)
        return box

    def _build_contacts(self) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)

        self._add_row(grid, 0, "开发者信息", QLabel(DEVELOPER))

        email = self._link(EMAIL, "点击用默认邮件客户端写信")
        email.clicked.connect(self._compose_email)
        self.email_button = email
        self._add_row(grid, 1, "Email", email)

        contact = self._link(IM_CONTACT, "点击复制到剪贴板")
        contact.clicked.connect(self._copy_contact)
        self.contact_button = contact
        self._add_row(grid, 2, "QQ / WeChat", contact)

        self._copy_hint = QLabel("")
        self._copy_hint.setObjectName("SuccessLabel")
        grid.addWidget(self._copy_hint, 2, 2)

        return box

    @staticmethod
    def _add_row(grid: QGridLayout, row: int, key: str, value: QWidget) -> None:
        caption = QLabel(key)
        caption.setObjectName("MetricKey")
        grid.addWidget(caption, row, 0)
        grid.addWidget(value, row, 1, Qt.AlignmentFlag.AlignLeft)

    @staticmethod
    def _link(text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Link")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        return button

    # -- footer -------------------------------------------------------------
    @staticmethod
    def _divider() -> QWidget:
        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        return line

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        copyright_label = QLabel(COPYRIGHT)
        copyright_label.setObjectName("Muted")

        close = QPushButton("关闭")
        close.setObjectName("Primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)

        row.addWidget(copyright_label)
        row.addStretch(1)
        row.addWidget(close)
        return row

    # -- actions ------------------------------------------------------------
    def _compose_email(self) -> None:
        """Hand the address to whatever the system uses for mail."""
        QDesktopServices.openUrl(QUrl(f"mailto:{EMAIL}"))

    def _copy_contact(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # pragma: no cover - no clipboard on this platform
            return
        clipboard.setText(IM_CONTACT)
        self._copy_hint.setText("已复制")
        self._copy_timer.start()

    def _clear_copy_hint(self) -> None:
        self._copy_hint.setText("")
