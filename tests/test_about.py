"""The About box, and the version resource baked into the executable.

Two things are being protected here beyond "the dialog opens".

The first is that the version is read rather than typed. It is easy for a
release to bump ``pyproject.toml`` and leave a hardcoded label behind, so the
tests drive everything from ``pixelpack.__version__`` and separately assert
that no literal version string appears in the dialog's source.

The second is the correspondence between the About box and the EXE's
Properties page. Both are generated from ``pixelpack/__init__.py``; the test
that parses ``assets/version_info.txt`` is what keeps them from drifting.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from pixelpack import (  # noqa: E402
    APP_NAME,
    COMPANY,
    COPYRIGHT,
    DESCRIPTION,
    DEVELOPER,
    EMAIL,
    IM_CONTACT,
    VERSION,
    __version__,
)
from pixelpack.ui import about_dialog as about_dialog_module  # noqa: E402
from pixelpack.ui import main_window as main_window_module  # noqa: E402
from pixelpack.ui.about_dialog import (  # noqa: E402
    COPY_FEEDBACK_MS,
    DIALOG_WIDTH,
    LOGO_SIZE,
    AboutDialog,
)
from pixelpack.ui.main_window import MainWindow  # noqa: E402
from pixelpack.utils.resources import asset_path, assets_dir  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialog(qt_app):
    about = AboutDialog()
    yield about
    about.deleteLater()
    qt_app.processEvents()


def label_texts(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


# --------------------------------------------------------------- what it says
def test_shows_the_packages_own_version(dialog):
    """The label follows pixelpack.__version__, whatever that becomes."""
    assert f"版本 {__version__}" in label_texts(dialog)
    assert VERSION == __version__


def test_the_dialog_source_does_not_hardcode_a_version():
    """Guards the requirement that the version comes from the configuration.

    If someone pastes the current version into the label instead of reading it,
    a later release bumps the package and the About box quietly starts lying.
    """
    source = (REPO_ROOT / "src" / "pixelpack" / "ui" / "about_dialog.py").read_text(
        encoding="utf-8"
    )
    assert VERSION not in source, "版本号被硬编码在 about_dialog.py 中"


def test_shows_name_description_developer_and_contacts(dialog):
    texts = label_texts(dialog)

    assert APP_NAME in texts
    assert DESCRIPTION in texts
    assert DEVELOPER in texts
    assert COPYRIGHT in texts
    # The contacts are buttons rather than labels, because they are clickable.
    assert dialog.email_button.text() == EMAIL
    assert dialog.contact_button.text() == IM_CONTACT


def test_window_title_names_the_application(dialog):
    assert APP_NAME in dialog.windowTitle()
    assert dialog.isModal()


def test_readme_images_actually_resolve():
    """Every relative <img src> in the README must exist in the repository.

    A path that does not resolve renders as a broken image on GitHub, which
    nobody notices until a reader mentions it.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    referenced = re.findall(r'<img\s+src="([^"]+)"', readme)

    assert referenced, "README 里没有引用任何图片"
    for path in referenced:
        assert not path.startswith(("http://", "https://")), (
            f"README 图片应随仓库分发，而不是外链：{path}"
        )
        assert (REPO_ROOT / path).is_file(), f"README 引用的图片不存在：{path}"


# ------------------------------------------------------------------ the logo
def test_logo_asset_ships_inside_the_package():
    """Requirement: bundled as a project asset, not an external path."""
    path = asset_path("logo.png")

    assert path is not None, "找不到打包的 logo.png"
    assert path.is_file()
    # Under the package, not beside the script or at the repository root.
    assert path.parent == assets_dir()
    assert path.parent.parent.name == "pixelpack"


def test_the_readme_logo_matches_the_packaged_one():
    """images/logo.png is the README's copy of the same artwork.

    The move left two files holding one picture. Updating only one of them
    would leave the About dialog and the README showing different logos, and
    nothing else would notice.
    """
    packaged = assets_dir() / "logo.png"
    documentation = REPO_ROOT / "images" / "logo.png"

    assert documentation.is_file(), "README 用的 images/logo.png 不见了"
    assert documentation.read_bytes() == packaged.read_bytes(), (
        "images/logo.png 与 src/pixelpack/assets/logo.png 不一致"
    )


def test_logo_is_loaded_and_sized_to_the_layout(dialog):
    assert dialog.logo_loaded is True

    logo = dialog.findChild(QLabel, "AboutLogo")
    assert logo is not None
    assert logo.pixmap() is not None and not logo.pixmap().isNull()

    # The label is LOGO_SIZE square and the art is scaled to fit inside it.
    assert logo.width() == LOGO_SIZE
    assert logo.height() == LOGO_SIZE
    assert logo.pixmap().width() <= LOGO_SIZE * 2
    assert logo.pixmap().height() <= LOGO_SIZE * 2


def test_logo_is_about_200px(dialog):
    """Requirement: around 200px, and not oversized."""
    logo = dialog.findChild(QLabel, "AboutLogo")
    assert logo.width() == LOGO_SIZE
    assert 150 <= logo.width() <= 260


def test_a_missing_logo_degrades_to_text(qt_app, monkeypatch):
    """A build that dropped the asset must not take the dialog down with it."""
    monkeypatch.setattr(about_dialog_module, "asset_path", lambda name: None)

    about = AboutDialog()
    try:
        assert about.logo_loaded is False
        assert APP_NAME in label_texts(about)
    finally:
        about.deleteLater()
        qt_app.processEvents()


# ------------------------------------------------------------------ the links
class _RecordingDesktopServices:
    """Stands in for QDesktopServices so no real mail client is launched."""

    opened: list[QUrl] = []

    @staticmethod
    def openUrl(url: QUrl) -> bool:  # noqa: N802 - matches the Qt name
        _RecordingDesktopServices.opened.append(url)
        return True


def test_email_opens_the_default_mail_client(dialog, monkeypatch):
    _RecordingDesktopServices.opened = []
    monkeypatch.setattr(
        about_dialog_module, "QDesktopServices", _RecordingDesktopServices
    )

    dialog.email_button.click()

    assert len(_RecordingDesktopServices.opened) == 1
    url = _RecordingDesktopServices.opened[0]
    assert url.scheme() == "mailto"
    assert url.path() == EMAIL


def test_contact_is_copied_to_the_clipboard(dialog, qt_app):
    dialog.contact_button.click()
    qt_app.processEvents()

    assert dialog._copy_hint.text() == "已复制"

    clipboard = qt_app.clipboard()
    if clipboard is not None:
        assert clipboard.text() == IM_CONTACT


def test_copy_confirmation_is_wired_to_a_timer_owned_by_the_dialog(dialog, qt_app):
    dialog.contact_button.click()
    qt_app.processEvents()
    assert dialog._copy_hint.text() == "已复制"

    # Parented, so it cannot outlive the dialog and fire into a dead widget.
    assert dialog._copy_timer.parent() is dialog
    assert dialog._copy_timer.isActive()
    assert dialog._copy_timer.interval() == COPY_FEEDBACK_MS

    dialog._copy_timer.timeout.emit()
    assert dialog._copy_hint.text() == ""


def test_close_button_dismisses_the_dialog(dialog):
    close = next(
        button
        for button in dialog.findChildren(QPushButton)
        if button.objectName() == "Primary"
    )
    close.click()
    assert dialog.result() == int(AboutDialog.DialogCode.Accepted)


def test_dialog_has_a_fixed_compact_width(dialog):
    """Compact and predictable: the width is pinned, the height follows content.

    Note this asserts the width the dialog actually takes, not its sizeHint:
    the wrapped description reports a wide single-line hint (720px) that
    setFixedWidth deliberately overrides.
    """
    assert dialog.width() == DIALOG_WIDTH <= 640
    assert dialog.sizeHint().height() <= 420
    # No "?" button in the title bar: there is no context help to offer.
    assert not dialog.windowFlags() & Qt.WindowType.WindowContextHelpButtonHint


# ------------------------------------------------------- reaching it from the UI
def test_main_window_opens_the_about_dialog(qt_app, monkeypatch):
    opened = []

    class FakeAboutDialog:
        def __init__(self, parent=None):
            opened.append(parent)

        def exec(self):
            opened.append("exec")
            return 0

    monkeypatch.setattr(main_window_module, "AboutDialog", FakeAboutDialog)

    window = MainWindow(restore=False)
    try:
        assert window.about_button.text() == "关于"
        window.about_button.click()
    finally:
        window.deleteLater()
        qt_app.processEvents()

    assert opened[0] is window
    assert opened[1] == "exec"


def test_opening_about_does_not_start_a_compression_run(qt_app, monkeypatch):
    """The button must not have been wired into the worker by accident."""
    monkeypatch.setattr(main_window_module, "AboutDialog", lambda parent=None: _Noop())

    window = MainWindow(restore=False)
    try:
        window.about_button.click()
        assert window._task is None
    finally:
        window.deleteLater()
        qt_app.processEvents()


class _Noop:
    def exec(self) -> int:
        return 0


# ------------------------------------------------- the Windows version resource
@pytest.fixture(scope="module")
def version_info_module():
    """The generator itself, loaded from scripts/ (it is not a package).

    assets/version_info.txt is a build artifact and is not in the repository,
    so these tests exercise what produces it rather than the checked-in copy.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_version_info", REPO_ROOT / "scripts" / "make_version_info.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def version_resource(version_info_module, tmp_path):
    """Generate the resource the way the build does, then parse it back."""
    versioninfo = pytest.importorskip(
        "PyInstaller.utils.win32.versioninfo",
        reason="PyInstaller is a build-time dependency",
    )

    path = tmp_path / "version_info.txt"
    path.write_text(version_info_module.build(), encoding="ascii")

    loaded = versioninfo.load_version_info_from_text_file(str(path))
    return {entry.name: entry.val for entry in loaded.kids[0].kids[0].kids}


def test_version_info_matches_the_package_constants(version_resource):
    """The EXE's Properties page is generated from the same constants."""
    assert version_resource["CompanyName"] == COMPANY == "HMYS Tech"
    assert version_resource["LegalCopyright"] == COPYRIGHT
    assert version_resource["FileDescription"] == DESCRIPTION
    assert version_resource["FileVersion"] == VERSION
    assert version_resource["ProductVersion"] == VERSION
    assert version_resource["ProductName"] == APP_NAME
    assert version_resource["InternalName"] == APP_NAME
    assert version_resource["OriginalFilename"] == f"{APP_NAME}.exe"


def test_version_info_is_pure_ascii(version_info_module):
    """PyInstaller reads this file with an encoding we should not have to guess."""
    version_info_module.build().encode("ascii")  # raises on a non-ASCII byte


def test_version_tuple_pads_to_four_numbers(version_info_module):
    """Windows wants exactly four; a two-part version must not break the build."""
    assert version_info_module.version_tuple("1.0.0") == (1, 0, 0, 0)
    assert version_info_module.version_tuple("2.5") == (2, 5, 0, 0)
    assert version_info_module.version_tuple("3") == (3, 0, 0, 0)
    assert version_info_module.version_tuple("1.2.3.4.5") == (1, 2, 3, 4)
    assert version_info_module.version_tuple("1.0.0-beta.2") == (1, 0, 0, 2)
    assert version_info_module.version_tuple(VERSION) == version_info_module.version_tuple(
        __version__
    )


# ------------------------------------------------------------ version plumbing
def test_pyproject_reads_the_version_from_the_package():
    """Requirement: one source of truth, not a second hardcoded copy."""
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" in data["project"]["dynamic"]
    assert "version" not in data["project"], "版本号不应在 pyproject.toml 中硬编码"
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "pixelpack.VERSION"
    }
    assert data["tool"]["setuptools"]["package-data"]["pixelpack"] == [
        "assets/*.png",
        "assets/*.ico",
    ]
