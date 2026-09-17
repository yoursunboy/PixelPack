"""The application icon.

Windows takes the icon from two different places, and they are not
interchangeable:

* the executable's embedded resource, which Explorer draws and which the
  taskbar uses for the pinned button and for the window before it is shown —
  the build script puts that there with PyInstaller's ``--icon``;
* ``QApplication.setWindowIcon``, which Qt uses for the title bar, Alt-Tab and
  the running taskbar button.

Without the second one the app gets Qt's default logo in the title bar and on
the taskbar even though the .exe file itself looks right, which is exactly the
state this module exists to fix.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon

from ..utils.logging import get_logger
from ..utils.resources import asset_path

logger = get_logger(__name__)

#: The .ico first: it carries every size Windows asks for (16 for the title
#: bar, 32 for the taskbar, 256 for Alt-Tab). The PNG only covers a checkout
#: where the generator has not run yet, since it is a single large image.
ICON_NAMES = ("pixelpack.ico", "pixelpack.png")


def app_icon() -> QIcon:
    """The application icon, or a null ``QIcon`` when the asset is missing.

    Never raises. A missing icon costs a default glyph in the title bar, which
    is not worth refusing to start over.
    """
    for name in ICON_NAMES:
        path = asset_path(name)
        if path is None:
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
        logger.warning("图标文件无法读取：%s", path)

    logger.warning(
        "找不到应用图标，将使用系统默认图标（运行 python scripts/make_icon.py 生成）"
    )
    return QIcon()
