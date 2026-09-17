"""PixelPack - Smart Image Archive Optimizer.

Resizes the images inside a folder until the resulting ZIP archive fits under a
user supplied size limit, keeping the highest resolution that still fits.

The constants below are the single source for everything user-visible about the
application: the About dialog reads them, and ``scripts/make_version_info.py``
turns them into the Windows executable's version resource, so the two can never
drift apart.
"""

from __future__ import annotations

__all__ = [
    "APP_NAME",
    "APP_TITLE",
    "COMPANY",
    "COPYRIGHT",
    "DESCRIPTION",
    "DEVELOPER",
    "EMAIL",
    "IM_CONTACT",
    "VERSION",
    "__version__",
]

#: The single place the version is written down. ``pyproject.toml`` declares it
#: dynamic and reads it back from here, so there is no second copy to update.
VERSION = "1.0.1"

#: Kept as an alias because ``python -m pixelpack --version`` and the packaging
#: scripts have always used the dunder spelling.
__version__ = VERSION

APP_NAME = "PixelPack"
APP_TITLE = "PixelPack — Smart Image Archive Optimizer"

DESCRIPTION = (
    "把整个文件夹的图片压缩打包成一个 ZIP，正好不超过你指定的大小，"
    "并用真实生成的压缩包反复验证，尽量保留分辨率与画质。"
)

DEVELOPER = "Yifan Li (SUNBOY)"
COMPANY = "HMYS Tech"
EMAIL = "yoursunboy@msn.com"
IM_CONTACT = "18163708"
COPYRIGHT = "© 2026 Yifan Li / HMYS Tech. All rights reserved."
