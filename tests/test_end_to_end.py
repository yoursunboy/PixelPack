"""End-to-end acceptance: a realistic folder, a real 10 MB ceiling.

These tests build a folder that looks like something a person would actually
pack — several formats, nested folders, non-ASCII names, a couple of documents
— and then check the finished archive the way a user would: open it, count the
files, walk the structure, decode every image, and confirm the size on disk.

Nothing here mocks the pipeline. The number asserted against the budget is
``Path.stat().st_size`` of the ZIP the optimiser wrote.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from pixelpack.core.optimizer import Optimizer, STAGE_FINAL
from pixelpack.models.result import ProgressUpdate
from pixelpack.utils.sizes import KB, MB
from pixelpack.utils.temp import temp_root
from tests.helpers import make_settings, noise_bytes, noise_image, save_image

#: The budget the acceptance run must respect.
TEN_MB = 10 * MB


def build_archive_folder(root: Path) -> dict[str, tuple[int, int]]:
    """A mixed folder whose images comfortably exceed 10 MB at full size."""
    (root / "假期 2025" / "第一天").mkdir(parents=True)
    (root / "假期 2025" / "第二天").mkdir(parents=True)

    photos = {
        "假期 2025/海边 01.jpg": ((2000, 1500), 1),
        "假期 2025/海边 02.jpg": ((2000, 1500), 2),
        "假期 2025/第一天/日落.jpg": ((1900, 1400), 3),
        "假期 2025/第二天/合影 photo.jpg": ((1800, 1350), 4),
        "假期 2025/第二天/山顶.webp": ((1600, 1200), 5),
        "假期 2025/地图.png": ((1200, 900), 6),
    }

    expected: dict[str, tuple[int, int]] = {}
    for rel, (size, seed) in photos.items():
        save_image(noise_image(size, seed=seed), root / rel, quality=95)
        expected[rel] = size

    # Non-image files ride along unchanged and count towards the budget.
    (root / "假期 2025" / "行程.txt").write_text(
        "第一天：海边\n第二天：山顶\n", encoding="utf-8"
    )
    (root / "假期 2025" / "票据.pdf").write_bytes(noise_bytes(64 * KB, seed=21))

    return expected


def test_ten_megabyte_ceiling_is_respected(tmp_path: Path):
    """The headline requirement: the archive lands at or under 10 MB."""
    root = tmp_path / "照片素材"
    root.mkdir()
    build_archive_folder(root)

    original_bytes = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    assert original_bytes > TEN_MB, "验收用例本身应当超过 10 MB，否则压缩路径没被走到"

    settings = make_settings(root, TEN_MB, precision=6, max_refine_rounds=20)
    result = Optimizer(settings).run()

    # 1. The real file on disk is at or below the ceiling.
    assert result.final_bytes <= TEN_MB
    assert result.output_path.stat().st_size == result.final_bytes
    assert result.output_path.stat().st_size <= TEN_MB
    assert result.copied_original is False
    assert result.verified is True

    # 2. The archive opens and every member passes its CRC.
    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()

        # 3. The file count is right, and nothing was duplicated.
        assert len(names) == len(set(names))
        assert len(names) == result.file_count
        assert result.file_count == 8  # 6 photos + 行程.txt + 票据.pdf

        # 4. The folder structure survived.
        assert "假期 2025/海边 01.jpg" in names
        assert "假期 2025/第一天/日落.jpg" in names
        assert "假期 2025/第二天/山顶.webp" in names
        assert "假期 2025/行程.txt" in names
        assert "假期 2025/票据.pdf" in names
        assert not any("\\" in name for name in names)

        # 5. The non-image files are byte-identical.
        assert archive.read("假期 2025/行程.txt") == (
            root / "假期 2025" / "行程.txt"
        ).read_bytes()
        assert archive.read("假期 2025/票据.pdf") == (
            root / "假期 2025" / "票据.pdf"
        ).read_bytes()

        # 6. Every image decodes, keeps its format, and was never upscaled.
        by_name = {o.arcname: o for o in result.outcomes}
        for name in names:
            if name not in by_name:
                continue
            outcome = by_name[name]
            with Image.open(io.BytesIO(archive.read(name))) as stored:
                stored.load()
                assert stored.size == (outcome.final_width, outcome.final_height)
                assert stored.format == outcome.encode_format
            assert outcome.final_width <= outcome.orig_width
            assert outcome.final_height <= outcome.orig_height
            assert outcome.final_bytes > 0

        # A full-size PNG stays a PNG: the format is not thrown away wholesale.
        assert by_name["假期 2025/地图.png"].encode_format == "PNG"


def test_ten_megabyte_ceiling_when_originals_already_fit(tmp_path: Path):
    """When the folder is under budget, nothing is re-encoded at all."""
    root = tmp_path / "小相册"
    root.mkdir()
    save_image(noise_image((1200, 900), seed=1), root / "a.jpg", quality=95)
    save_image(noise_image((1000, 800), seed=2), root / "子目录/b.png")
    (root / "readme.txt").write_text("相册说明", encoding="utf-8")

    settings = make_settings(root, TEN_MB)
    result = Optimizer(settings).run()

    assert result.copied_original is True
    assert result.rounds == 0
    assert result.scale == 1.0
    assert result.final_bytes <= TEN_MB

    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"a.jpg", "子目录/b.png", "readme.txt"}
        assert archive.read("a.jpg") == (root / "a.jpg").read_bytes()
        assert archive.read("子目录/b.png") == (root / "子目录" / "b.png").read_bytes()


def test_acceptance_run_leaves_no_trace(tmp_path: Path):
    """Sources untouched, temp cleaned, no half-written output."""
    root = tmp_path / "素材"
    root.mkdir()
    build_archive_folder(root)
    before = {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }

    workspace_root = temp_root()
    temp_before = set(workspace_root.iterdir()) if workspace_root.is_dir() else set()

    settings = make_settings(root, 3 * MB, precision=5, max_refine_rounds=10)
    result = Optimizer(settings).run()

    after = {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }

    assert after == before, "源文件夹被修改了"
    assert result.output_path.parent != root

    temp_after = set(workspace_root.iterdir()) if workspace_root.is_dir() else set()
    assert temp_after <= temp_before, f"临时目录残留：{temp_after - temp_before}"
    assert not result.output_path.with_name(result.output_path.name + ".part").exists()


def test_a_tight_budget_still_produces_a_valid_archive(tmp_path: Path):
    """Squeeze hard: correctness must not degrade with the budget."""
    root = tmp_path / "紧预算"
    root.mkdir()
    build_archive_folder(root)

    settings = make_settings(root, 1200 * KB, precision=6, max_refine_rounds=20)
    result = Optimizer(settings).run()

    assert result.final_bytes <= 1200 * KB
    assert result.utilization > 0.85, f"利用率偏低：{result.utilization:.1%}"

    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            assert archive.read(name)


def test_progress_is_reported_from_start_to_finish(tmp_path: Path):
    root = tmp_path / "进度"
    root.mkdir()
    build_archive_folder(root)

    updates: list[ProgressUpdate] = []
    settings = make_settings(root, 2 * MB, precision=5, max_refine_rounds=10)
    Optimizer(settings, on_progress=updates.append).run()

    assert updates
    assert updates[-1].stage == STAGE_FINAL
    assert updates[-1].fraction == pytest.approx(1.0)

    fractions = [u.fraction for u in updates]
    assert fractions == sorted(fractions), "进度条不能倒退"
    assert all(0.0 <= f <= 1.0 for f in fractions)
    assert all(u.message for u in updates)
    assert all(u.target_bytes == 2 * MB for u in updates)

    # Live telemetry the GUI shows while the run is in flight.
    assert any(u.current_zip_bytes > 0 for u in updates)
    assert any(u.scale > 0 for u in updates)


def test_unicode_names_survive_a_full_run(tmp_path: Path):
    root = tmp_path / "中文 目录"
    (root / "子 目录" / "更 深").mkdir(parents=True)
    save_image(noise_image((900, 700), seed=1), root / "子 目录" / "更 深" / "图片 ①.jpg")
    save_image(noise_image((900, 700), seed=2), root / "émoji 🎈.png")

    settings = make_settings(root, 200 * KB, precision=5, max_refine_rounds=10)
    result = Optimizer(settings).run()

    assert result.final_bytes <= 200 * KB
    with zipfile.ZipFile(result.output_path) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None
    assert "子 目录/更 深/图片 ①.jpg" in names
    assert "émoji 🎈.png" in names
