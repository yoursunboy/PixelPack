"""The optimisation pipeline: guarantees, edge cases and safety."""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from pixelpack.core.image_processor import render_image
from pixelpack.core.optimizer import (
    IMPOSSIBLE_MESSAGE,
    NON_IMAGE_TOO_LARGE_MESSAGE,
    STAGE_BASELINE,
    STAGE_FINAL,
    STAGE_SCAN,
    STAGE_SEARCH,
    Optimizer,
)
from pixelpack.core.scanner import scan_directory
from pixelpack.models.result import (
    ImpossibleTargetError,
    OperationCancelled,
    OptimizationError,
    ProgressUpdate,
)
from pixelpack.models.settings import CompressionMode
from pixelpack.utils.sizes import KB, MB
from pixelpack.utils.temp import temp_root
from tests.helpers import (
    folder_fingerprint,
    make_settings,
    noise_bytes,
    noise_image,
    save_image,
)


def run(source: Path, target: int, **overrides):
    settings = make_settings(source, target, **overrides)
    return Optimizer(settings).run()


# --------------------------------------------------------------------------
# The single most important guarantee
# --------------------------------------------------------------------------
@pytest.mark.parametrize("target_kb", [150, 250, 400, 700])
def test_final_zip_never_exceeds_the_target(photo_folder: Path, target_kb: int):
    result = run(photo_folder, target_kb * KB)

    assert result.final_bytes <= result.target_bytes
    assert result.output_path.stat().st_size == result.final_bytes
    assert result.output_path.stat().st_size <= target_kb * KB


def test_target_is_a_hard_limit_even_between_two_sizes(photo_folder: Path):
    """A target that falls between two achievable sizes must round down."""
    sizes = {}
    for target_kb in (180, 260, 380):
        result = run(photo_folder, target_kb * KB)
        sizes[target_kb] = result.final_bytes
        assert result.final_bytes <= target_kb * KB

    # Tightening the target must never produce a larger archive.
    assert sizes[180] <= sizes[260] <= sizes[380]


# --------------------------------------------------------------------------
# Step 1: originals that already fit are not re-encoded
# --------------------------------------------------------------------------
def test_originals_that_already_fit_are_copied_untouched(photo_folder: Path):
    result = run(photo_folder, 50 * MB)

    assert result.copied_original is True
    assert result.rounds == 0
    assert result.final_bytes <= 50 * MB

    # Byte-for-byte identical to the sources: no quality was thrown away.
    with zipfile.ZipFile(result.output_path) as archive:
        for source in scan_directory(photo_folder).files:
            assert archive.read(source.rel_path) == source.path.read_bytes()


def test_originals_that_already_fit_report_scale_one(photo_folder: Path):
    result = run(photo_folder, 50 * MB)

    assert result.scale == 1.0
    assert all(o.final_width == o.orig_width for o in result.outcomes)
    assert all(o.final_height == o.orig_height for o in result.outcomes)


# --------------------------------------------------------------------------
# Step 3-4: the search
# --------------------------------------------------------------------------
def test_compression_actually_shrinks_the_archive(photo_folder: Path):
    original = sum(f.stat().st_size for f in photo_folder.rglob("*") if f.is_file())
    result = run(photo_folder, 400 * KB)

    assert result.copied_original is False
    assert result.original_bytes == original
    assert result.final_bytes < original
    assert result.scale < 1.0


def test_search_uses_the_leftover_space(photo_folder: Path):
    """A good optimiser lands close to the ceiling, not far below it."""
    result = run(photo_folder, 400 * KB)

    assert result.utilization > 0.90, f"利用率只有 {result.utilization:.1%}"


def test_images_get_smaller_as_the_target_tightens(photo_folder: Path):
    loose = run(photo_folder, 700 * KB)
    tight = run(photo_folder, 200 * KB)

    assert tight.scale < loose.scale
    assert tight.final_bytes <= 200 * KB
    assert loose.final_bytes <= 700 * KB


def test_every_candidate_is_rendered_from_the_original(photo_folder: Path):
    """Scaling must never compound.

    Rendering each *original* at the scale the optimiser asked for must
    reproduce the archived bytes exactly. A pipeline that chained renders
    (100% -> 80% -> 60%) would not match.
    """
    result = run(photo_folder, 250 * KB, precision=6)
    sources = {f.rel_path: f for f in scan_directory(photo_folder).image_files}

    with zipfile.ZipFile(result.output_path) as archive:
        for outcome in result.outcomes:
            if outcome.fallback:
                continue
            expected = render_image(
                sources[outcome.rel_path],
                outcome.requested_scale,
                quality=88,
                min_long_edge=100,
                keep_exif=True,
            )
            assert archive.read(outcome.arcname) == expected.data, outcome.rel_path


def test_archive_members_match_the_reported_dimensions(photo_folder: Path):
    result = run(photo_folder, 250 * KB)

    with zipfile.ZipFile(result.output_path) as archive:
        for outcome in result.outcomes:
            stored = Image.open(io.BytesIO(archive.read(outcome.arcname)))
            assert stored.size == (outcome.final_width, outcome.final_height)
            assert stored.size[0] <= outcome.orig_width
            assert stored.size[1] <= outcome.orig_height


def test_repeated_runs_are_deterministic(photo_folder: Path):
    first = run(photo_folder, 250 * KB)
    second = run(photo_folder, 250 * KB)

    assert first.final_bytes == second.final_bytes
    assert [o.final_width for o in first.outcomes] == [o.final_width for o in second.outcomes]
    assert first.output_path.read_bytes() == second.output_path.read_bytes()


# --------------------------------------------------------------------------
# Geometry guarantees
# --------------------------------------------------------------------------
def test_images_are_never_upscaled(photo_folder: Path):
    result = run(photo_folder, 400 * KB)

    for outcome in result.outcomes:
        assert outcome.final_width <= outcome.orig_width
        assert outcome.final_height <= outcome.orig_height


def test_small_images_are_protected(tmp_path: Path):
    root = tmp_path / "mixed"
    root.mkdir()
    save_image(noise_image((2400, 1800), seed=1), root / "big.jpg", quality=95)
    save_image(noise_image((1000, 700), seed=2), root / "medium.jpg", quality=95)
    save_image(noise_image((640, 480), seed=3), root / "small.jpg", quality=95)

    # Tight enough to force real shrinking, loose enough to stay possible once
    # every image is pinned at the 800 px protection floor.
    settings = make_settings(root, 2500 * KB, min_long_edge=800)
    result = Optimizer(settings).run()

    by_name = {o.rel_path: o for o in result.outcomes}
    small = by_name["small.jpg"]
    big = by_name["big.jpg"]

    assert small.final_long_edge == 640, "小图片不应被缩小"
    assert (small.final_width, small.final_height) == (640, 480)
    assert big.final_long_edge >= 800, "大图不得低于最小长边"
    assert big.final_long_edge < 2400, "大图应当被缩小"
    assert result.final_bytes <= 2500 * KB


def test_min_long_edge_zero_disables_the_protection(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((2400, 1800), seed=1), root / "a.jpg", quality=95)
    save_image(noise_image((900, 600), seed=2), root / "b.jpg", quality=95)

    result = run(root, 120 * KB, min_long_edge=0)
    by_name = {o.rel_path: o for o in result.outcomes}

    assert by_name["b.jpg"].final_long_edge < 900


# --------------------------------------------------------------------------
# Folder and file handling
# --------------------------------------------------------------------------
def test_directory_structure_is_preserved_inside_the_zip(photo_folder: Path):
    result = run(photo_folder, 400 * KB)

    with zipfile.ZipFile(result.output_path) as archive:
        names = set(archive.namelist())

    assert "001 风景.jpg" in names
    assert "子目录 A/003 photo.jpg" in names
    assert "子目录 A/更深一层/004.jpg" in names
    assert "005 图.png" in names
    assert not any("\\" in name for name in names)


def test_every_file_reaches_the_archive(photo_folder: Path, tmp_path: Path):
    (photo_folder / "说明.txt").write_text("附带的说明文件", encoding="utf-8")
    (photo_folder / "子目录 A" / "meta.json").write_text('{"k": 1}', encoding="utf-8")

    result = run(photo_folder, 400 * KB)
    scan = scan_directory(photo_folder)

    with zipfile.ZipFile(result.output_path) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None
        assert archive.read("说明.txt").decode("utf-8") == "附带的说明文件"
        assert archive.read("子目录 A/meta.json").decode("utf-8") == '{"k": 1}'

    expected = {f.output_name for f in scan.image_files} | {f.rel_path for f in scan.non_image_files}
    assert names == expected
    assert result.file_count == scan.file_count


def test_non_image_files_count_towards_the_size(photo_folder: Path, tmp_path: Path):
    blob = photo_folder / "blob.bin"
    blob.write_bytes(noise_bytes(50 * KB, seed=11))

    result = run(photo_folder, 400 * KB)

    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.read("blob.bin") == blob.read_bytes()


def test_include_subdirs_off_packs_only_the_top_level(photo_folder: Path):
    result = run(photo_folder, 400 * KB, include_subdirs=False)

    with zipfile.ZipFile(result.output_path) as archive:
        names = set(archive.namelist())

    assert names == {"001 风景.jpg", "002 人像.jpg", "005 图.png"}


# --------------------------------------------------------------------------
# Impossible targets
# --------------------------------------------------------------------------
def test_impossible_when_non_image_files_exceed_the_target(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((200, 150), seed=1), root / "a.jpg")
    # Random payload: deflate cannot shrink it, so it cannot fit the budget.
    (root / "video.bin").write_bytes(noise_bytes(MB, seed=3))

    with pytest.raises(ImpossibleTargetError) as info:
        run(root, 100 * KB)

    assert str(info.value) == NON_IMAGE_TOO_LARGE_MESSAGE
    assert "不可优化文件已经超过大小限制" in str(info.value)


def test_impossible_when_every_image_is_protected(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    for index in range(8):
        save_image(noise_image((600, 400), seed=index), root / f"{index}.jpg", quality=95)

    settings = make_settings(root, 60 * KB, min_long_edge=800)
    with pytest.raises(ImpossibleTargetError) as info:
        Optimizer(settings).run()

    assert str(info.value) == IMPOSSIBLE_MESSAGE


def test_failed_run_leaves_no_output_file(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    for index in range(8):
        save_image(noise_image((600, 400), seed=index), root / f"{index}.jpg", quality=95)

    output = tmp_path / "out.zip"
    settings = make_settings(root, 60 * KB, output, min_long_edge=800)
    with pytest.raises(ImpossibleTargetError):
        Optimizer(settings).run()

    assert not output.exists()
    assert not output.with_name(output.name + ".part").exists()


def test_empty_folder_is_rejected(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()

    with pytest.raises(OptimizationError):
        run(root, MB)


def test_missing_source_folder_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        make_settings(tmp_path / "nope", MB)


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------
def test_source_folder_is_never_modified(photo_folder: Path):
    before = folder_fingerprint(photo_folder)

    run(photo_folder, 300 * KB)

    assert folder_fingerprint(photo_folder) == before


def test_output_can_live_inside_the_source_folder(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((600, 400), seed=1), root / "a.jpg", quality=95)
    output = root / "packed.zip"

    result = run(root, 100 * KB, output_path=output)

    assert result.output_path == output
    with zipfile.ZipFile(output) as archive:
        assert "packed.zip" not in archive.namelist()


def test_existing_output_file_is_replaced(tmp_path: Path, photo_folder: Path):
    output = photo_folder.parent / "out.zip"
    output.write_bytes(b"stale")

    result = run(photo_folder, 400 * KB, output_path=output)

    assert output.stat().st_size == result.final_bytes
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None


def test_temp_workspace_is_cleaned_up(photo_folder: Path):
    root = temp_root()
    before = set(root.iterdir()) if root.is_dir() else set()

    run(photo_folder, 400 * KB)

    after = set(root.iterdir()) if root.is_dir() else set()
    assert after <= before, f"临时目录未清理：{after - before}"


def test_cancellation_stops_the_run_cleanly(photo_folder: Path):
    root = temp_root()
    before = set(root.iterdir()) if root.is_dir() else set()

    output = photo_folder.parent / "cancelled.zip"
    settings = make_settings(photo_folder, 200 * KB, output, precision=8)
    cancel = threading.Event()
    seen = []

    def on_progress(update: ProgressUpdate) -> None:
        seen.append(update)
        if update.stage == STAGE_SEARCH and len(seen) > 1:
            cancel.set()

    with pytest.raises(OperationCancelled):
        Optimizer(settings, on_progress=on_progress, cancel=cancel).run()

    assert seen, "取消前应当至少报告一次进度"
    assert not output.exists(), "取消后不能留下输出文件"
    assert not output.with_name(output.name + ".part").exists()

    after = set(root.iterdir()) if root.is_dir() else set()
    assert after <= before, f"取消后临时目录未清理：{after - before}"


# --------------------------------------------------------------------------
# Progress reporting
# --------------------------------------------------------------------------
def test_progress_reports_every_stage(photo_folder: Path):
    updates: list[ProgressUpdate] = []
    settings = make_settings(photo_folder, 300 * KB)

    Optimizer(settings, on_progress=updates.append).run()

    stages = [u.stage for u in updates]
    assert STAGE_SCAN in stages
    assert STAGE_BASELINE in stages
    assert STAGE_SEARCH in stages
    assert STAGE_FINAL in stages
    assert stages[-1] == STAGE_FINAL

    assert all(0.0 <= u.fraction <= 1.0 for u in updates)
    assert all(u.message for u in updates)
    assert all(u.target_bytes == 300 * KB for u in updates)

    messages = {u.message for u in updates}
    assert "正在扫描图片" in messages
    assert "正在生成 ZIP" in messages
    assert "正在测试图片尺寸" in messages
    assert "正在完成" in messages

    # Zip sizes and scales are reported live.
    assert any(u.current_zip_bytes > 0 for u in updates)
    assert any(u.scale > 0 for u in updates)


def test_a_broken_progress_callback_does_not_stop_the_run(photo_folder: Path):
    def explode(_update):
        raise RuntimeError("boom")

    settings = make_settings(photo_folder, 400 * KB)

    result = Optimizer(settings, on_progress=explode).run()

    assert result.final_bytes <= result.target_bytes


# --------------------------------------------------------------------------
# Compression modes
# --------------------------------------------------------------------------
def test_modes_trade_quality_against_size(photo_folder: Path):
    quality = run(photo_folder, 400 * KB, mode=CompressionMode.QUALITY)
    size_first = run(photo_folder, 400 * KB, mode=CompressionMode.SIZE)

    assert CompressionMode.QUALITY.jpeg_quality == 92
    assert CompressionMode.BALANCED.jpeg_quality == 88
    assert CompressionMode.SIZE.jpeg_quality == 82

    # Both respect the same budget. The size-first profile encodes smaller at
    # any given scale, so it can afford to keep *more* resolution for the same
    # number of bytes — that is the whole trade-off.
    assert quality.final_bytes <= 400 * KB
    assert size_first.final_bytes <= 400 * KB
    assert size_first.scale >= quality.scale


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
def test_result_reports_complete_statistics(photo_folder: Path):
    result = run(photo_folder, 400 * KB)

    assert result.image_count == 6
    assert result.file_count == 6
    assert result.original_bytes > 0
    assert 0 < result.utilization <= 1.0
    assert 0 < result.compression_ratio < 1
    assert result.elapsed_seconds >= 0
    assert result.rounds > 0
    assert result.avg_original_dimensions != "—"
    assert result.avg_final_dimensions != "—"
    assert len(result.outcomes) == 6
    assert all(o.scale <= 1.0 for o in result.outcomes)
    assert result.verified is True


def test_outcomes_are_sorted_by_name(photo_folder: Path):
    result = run(photo_folder, 400 * KB)

    names = [o.rel_path for o in result.outcomes]
    assert names == sorted(names, key=str.lower)


def test_archive_verifies_end_to_end(photo_folder: Path):
    result = run(photo_folder, 300 * KB)

    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.testzip() is None
        infos = archive.infolist()
        assert len(infos) == result.file_count
        for info in infos:
            assert info.file_size > 0
            payload = archive.read(info.filename)
            assert len(payload) == info.file_size


# --------------------------------------------------------------------------
# Format fidelity
# --------------------------------------------------------------------------
def test_transparency_survives_the_whole_pipeline(transparency_folder: Path):
    result = run(transparency_folder, 60 * KB)

    assert result.final_bytes <= 60 * KB
    with zipfile.ZipFile(result.output_path) as archive:
        for name in archive.namelist():
            image = Image.open(io.BytesIO(archive.read(name)))
            assert "A" in image.getbands(), f"{name} 丢失了透明通道"


def test_exif_orientation_survives_the_pipeline(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    image = noise_image((600, 400), seed=1)
    exif = Image.Exif()
    exif[0x0112] = 6
    image.save(root / "rotated.jpg", exif=exif, quality=95)

    result = run(root, 40 * KB)
    outcome = result.outcomes[0]

    # Displayed orientation is portrait, and the output keeps that.
    assert outcome.orig_height > outcome.orig_width
    with zipfile.ZipFile(result.output_path) as archive:
        stored = Image.open(io.BytesIO(archive.read("rotated.jpg")))
        assert stored.height > stored.width


def test_png_stays_png_and_bmp_becomes_png(tmp_path: Path):
    root = tmp_path / "src"
    root.mkdir()
    save_image(noise_image((600, 400), seed=1), root / "a.png")
    save_image(noise_image((600, 400), seed=2), root / "b.bmp")

    result = run(root, 150 * KB)

    with zipfile.ZipFile(result.output_path) as archive:
        names = set(archive.namelist())
        assert "a.png" in names
        assert "b.png" in names
        assert "b.bmp" not in names
