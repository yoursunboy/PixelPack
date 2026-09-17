"""The window and taskbar icon.

Windows takes the icon from two places that are easy to confuse:

* the executable's embedded resource, which Explorer draws — the build script
  puts that there with PyInstaller's ``--icon``;
* ``QApplication.setWindowIcon``, which Qt uses for the title bar, Alt-Tab and
  the running taskbar button.

Having only the first looks correct in Explorer and wrong everywhere else,
which is precisely the bug these tests exist to keep fixed.
"""

from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("PIL")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelpack.app import main  # noqa: E402
from pixelpack.ui import icons as icons_module  # noqa: E402
from pixelpack.ui.icons import app_icon  # noqa: E402
from pixelpack.utils.resources import assets_dir  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Everything Windows asks for: 16 in the title bar, 32 on the taskbar, 48 for
#: Alt-Tab, 256 for Explorer's large-icon view. Supplying one size and letting
#: Windows scale it is what makes an icon look blurry.
REQUIRED_SIZES = {16, 24, 32, 48, 64, 128, 256}


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def icon_generator():
    """``scripts/make_icon.py``, loaded from its path (it is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "make_icon", REPO_ROOT / "scripts" / "make_icon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated_ico_bytes(icon_generator) -> bytes:
    """Exactly the bytes ``scripts/make_icon.py`` writes for the .ico."""
    images = [icon_generator.draw_icon(size) for size in icon_generator.SIZES]
    buffer = io.BytesIO()
    images[-1].save(
        buffer,
        format="ICO",
        sizes=[(size, size) for size in icon_generator.SIZES],
        append_images=images[:-1],
    )
    return buffer.getvalue()


@pytest.fixture
def ico_file(generated_ico_bytes, tmp_path) -> Path:
    path = tmp_path / "pixelpack.ico"
    path.write_bytes(generated_ico_bytes)
    return path


@pytest.fixture
def png_file(icon_generator, tmp_path) -> Path:
    path = tmp_path / "pixelpack.png"
    icon_generator.draw_icon(256).save(path, format="PNG")
    return path


# ------------------------------------------------------------------ the asset
def test_the_committed_icon_carries_every_size_windows_asks_for():
    from PIL import Image

    path = assets_dir() / "pixelpack.ico"
    assert path.is_file(), "src/pixelpack/assets/pixelpack.ico 不见了"

    with Image.open(path) as image:
        sizes = {width for width, _ in image.ico.sizes()}

    assert sizes >= REQUIRED_SIZES, f"缺少尺寸：{sorted(REQUIRED_SIZES - sizes)}"


def test_the_committed_icon_matches_the_generator(generated_ico_bytes):
    """The build regenerates the icon on every pass.

    The committed copy is what a checkout runs with, so it has to stay
    identical to what the build would produce — otherwise the icon a developer
    sees and the icon that ships are two different pictures.
    """
    assert (assets_dir() / "pixelpack.ico").read_bytes() == generated_ico_bytes


# ----------------------------------------------------------------- the loader
def test_the_packaged_icon_loads(qt_app):
    icon = app_icon()

    assert not icon.isNull(), "打包的图标没有加载"
    assert {size.width() for size in icon.availableSizes()} >= REQUIRED_SIZES
    # The sizes Windows actually requests, rendered rather than merely listed.
    for pixels in (16, 32, 48, 256):
        assert not icon.pixmap(pixels, pixels).isNull()


def test_the_ico_is_preferred_over_the_png(qt_app, ico_file, png_file, monkeypatch):
    """The .ico wins because it carries the small sizes the title bar needs."""
    monkeypatch.setattr(
        icons_module,
        "asset_path",
        lambda name: ico_file if name.endswith(".ico") else png_file,
    )

    assert {size.width() for size in app_icon().availableSizes()} >= REQUIRED_SIZES


def test_the_png_covers_a_checkout_where_the_generator_has_not_run(
    qt_app, png_file, monkeypatch
):
    monkeypatch.setattr(
        icons_module,
        "asset_path",
        lambda name: png_file if name.endswith(".png") else None,
    )

    assert not app_icon().isNull()


def test_a_missing_icon_is_not_fatal(qt_app, monkeypatch):
    """No icon costs a default glyph in the title bar, not a failed start."""
    monkeypatch.setattr(icons_module, "asset_path", lambda name: None)

    assert app_icon().isNull()


# ---------------------------------------------------------------- the wiring
def test_the_application_actually_sets_the_window_icon(qt_app, capsys):
    """Loading the icon is not enough — it has to reach the QApplication.

    Without setWindowIcon the title bar and the taskbar button keep Qt's
    default logo however healthy the packaged asset is, and the self-check is
    what reports it in the frozen build.
    """
    assert main(["--self-check"]) == 0

    out = capsys.readouterr().out
    assert "应用图标 已加载" in out
    assert not qt_app.windowIcon().isNull()
