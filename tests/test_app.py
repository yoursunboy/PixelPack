"""The entry point: argument parsing and the packaged-build self-check.

``--self-check`` is what ``scripts/build_windows.ps1`` runs against the frozen
executable, so its behaviour is a contract the packaging script depends on. It
is also the only thing that would catch a bundle where the modules import but
the widgets cannot be built.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelpack import APP_NAME, __version__  # noqa: E402
from pixelpack.app import build_parser, main  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
def test_a_source_folder_is_optional():
    assert build_parser().parse_args([]).folder is None
    assert build_parser().parse_args([r"D:\照片 素材"]).folder == Path(r"D:\照片 素材")


def test_version_prints_and_exits_before_any_gui_starts(capsys):
    # Argparse handles --version during parsing, so no QApplication is needed
    # and nothing is imported beyond the module itself.
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert f"{APP_NAME} {__version__}" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The self-check the packaging script relies on
# --------------------------------------------------------------------------
def test_self_check_builds_the_ui_and_succeeds(qt_app, capsys):
    assert main(["--self-check"]) == 0

    out = capsys.readouterr().out
    assert f"{APP_NAME} {__version__}" in out
    assert "自检通过" in out
    # The stylesheet must have reached the QApplication, not just been built.
    assert "样式表 0 字符" not in out


def test_self_check_accepts_a_source_folder(qt_app, photo_folder: Path, capsys):
    assert main(["--self-check", str(photo_folder)]) == 0
    assert "自检通过" in capsys.readouterr().out


def test_self_check_tolerates_a_missing_folder(qt_app, tmp_path: Path, capsys):
    # A stale path on the command line is a warning, not a crash.
    assert main(["--self-check", str(tmp_path / "not-here")]) == 0
    assert "自检通过" in capsys.readouterr().out
