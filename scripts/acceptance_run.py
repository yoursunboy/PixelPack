"""The acceptance run: a real folder, a real 10 MB ceiling, a real report.

This is the check the project is judged on. It builds a folder of genuine
JPG/PNG/WebP images that comfortably exceeds 10 MB, runs the optimiser exactly
as the GUI would, and then verifies the finished archive the way a person
would: open it, count the files, walk the directory tree, decode every image,
and read the size straight off the disk.

    python scripts/acceptance_run.py
    python scripts/acceptance_run.py --target 10 --keep

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import io
import random
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from pixelpack.core.optimizer import Optimizer  # noqa: E402
from pixelpack.models.result import OptimizationError, PixelPackError  # noqa: E402
from pixelpack.models.settings import CompressionMode, OptimizationSettings  # noqa: E402
from pixelpack.utils.sizes import MB, format_percent, format_size  # noqa: E402

#: rel_path -> (size, mode, seed). Noise, so the bytes resist compression and
#: the optimiser has to do real work.
PHOTOS: dict[str, tuple[tuple[int, int], str, int]] = {
    "照片 素材/海边 01.jpg": ((2400, 1800), "RGB", 1),
    "照片 素材/海边 02.jpg": ((2400, 1800), "RGB", 2),
    "照片 素材/第一天/日落.jpg": ((2200, 1650), "RGB", 3),
    "照片 素材/第一天/晚餐 02.jpeg": ((2000, 1500), "RGB", 4),
    "照片 素材/第二天/合影 photo.jpg": ((2000, 1500), "RGB", 5),
    "照片 素材/第二天/山顶.webp": ((1800, 1350), "RGB", 6),
    "照片 素材/地图.png": ((1400, 1050), "RGB", 7),
    "照片 素材/图标 透明.png": ((1200, 900), "RGBA", 8),
}

DOCUMENTS = {
    "照片 素材/行程.txt": "第一天：海边\n第二天：山顶\n第三天：返程\n".encode("utf-8"),
    "照片 素材/票据.pdf": b"%PDF-1.4\n" + bytes(random.Random(99).randbytes(48 * 1024)),
}


def build_folder(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, (size, mode, seed) in PHOTOS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)

        channels = {"RGB": 3, "RGBA": 4}[mode]
        data = random.Random(seed).randbytes(size[0] * size[1] * channels)
        image = Image.frombytes(mode, size, data)

        if path.suffix.lower() == ".webp":
            image.save(path, "WEBP", quality=95)
        elif path.suffix.lower() == ".png":
            image.save(path, "PNG")
        else:
            image.convert("RGB").save(path, "JPEG", quality=95)

    for rel, payload in DOCUMENTS.items():
        (root / rel).write_bytes(payload)


def human(path: Path) -> str:
    return format_size(path.stat().st_size)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=float, default=10.0, help="目标大小（MB），默认 10")
    parser.add_argument("--mode", default=CompressionMode.BALANCED.value,
                        choices=[m.value for m in CompressionMode])
    parser.add_argument("--precision", type=int, default=7)
    parser.add_argument("--min-long-edge", type=int, default=800)
    parser.add_argument("--keep", action="store_true", help="保留生成的文件夹和 ZIP")
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args(argv)

    target_bytes = int(args.target * MB)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="pixelpack-acceptance-"))
    workdir.mkdir(parents=True, exist_ok=True)

    source = workdir / "照片 素材"
    output = workdir / f"{source.name}_optimized.zip"

    print("=" * 72)
    print("PixelPack 验收测试")
    print("=" * 72)
    print(f"工作目录 : {workdir}")
    print(f"源文件夹 : {source}")
    print(f"目标大小 : {format_size(target_bytes)}（{target_bytes} 字节）")
    print(f"压缩模式 : {args.mode}    最小长边: {args.min_long_edge} px    精度: {args.precision}")
    print()

    # ---------------------------------------------------------------- build
    print("正在生成测试图片…")
    build_folder(source)
    original_bytes = sum(f.stat().st_size for f in source.rglob("*") if f.is_file())
    source_files = sorted(p for p in source.rglob("*") if p.is_file())
    print(f"  生成 {len(source_files)} 个文件，共 {format_size(original_bytes)}")
    if original_bytes <= target_bytes:
        print(f"  !! 原始数据只有 {format_size(original_bytes)}，未超过目标，压缩路径不会被走到。")
        return 2
    print()

    # ------------------------------------------------------------------ run
    settings = OptimizationSettings(
        source_dir=source,
        target_bytes=target_bytes,
        output_path=output,
        mode=CompressionMode(args.mode),
        include_subdirs=True,
        keep_exif=True,
        min_long_edge=args.min_long_edge,
        precision=args.precision,
    ).validated()

    last_stage = None
    started = time.perf_counter()

    def on_progress(update) -> None:
        nonlocal last_stage
        if update.stage != last_stage:
            last_stage = update.stage
            detail = ""
            if update.current_zip_bytes:
                detail = f"  当前 ZIP {format_size(update.current_zip_bytes)}"
            print(f"[{update.fraction:5.1%}] {update.message}{detail}")

    print("正在优化…")
    try:
        result = Optimizer(settings, on_progress=on_progress).run()
    except PixelPackError as error:
        print(f"\n失败：{error}")
        return 1

    elapsed = time.perf_counter() - started
    print(f"  用时 {elapsed:.1f} 秒，共 {result.rounds} 轮\n")

    # ------------------------------------------------------------- verify
    print("=" * 72)
    print("验收检查")
    print("=" * 72)

    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))
        print(f"  [{'通过' if ok else '失败'}] {label}{('  — ' + detail) if detail else ''}")

    on_disk = output.stat().st_size
    check(
        f"实际 ZIP 大小 ≤ 目标（{format_size(target_bytes)}）",
        on_disk <= target_bytes,
        f"{format_size(on_disk)} / {format_size(target_bytes)}，利用率 {format_percent(on_disk / target_bytes)}",
    )
    check("ZIP 文件存在且可读", output.exists() and on_disk > 0, human(output))

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        check("ZIP 可以正常打开", True, f"{len(names)} 个成员")
        check("ZipFile.testzip() 无错误", archive.testzip() is None)
        check("没有重复成员", len(names) == len(set(names)))
        check("文件数量正确", len(names) == len(source_files),
              f"{len(names)} / {len(source_files)}")

        expected = {
            str(p.relative_to(source)).replace("\\", "/") for p in source_files
        }
        check("目录结构与源文件夹一致", set(names) == expected)
        check("ZIP 内路径使用正斜杠", not any("\\" in n for n in names))

        # Non-image files must be byte identical.
        documents_ok = all(
            archive.read(rel) == payload for rel, payload in DOCUMENTS.items()
        )
        check("非图片文件逐字节一致", documents_ok)

        # Every image must still decode, keep its format, and never be bigger
        # than the original.
        images_ok = True
        details: list[str] = []
        by_name = {o.arcname: o for o in result.outcomes}
        for name in names:
            outcome = by_name.get(name)
            if outcome is None:
                continue
            try:
                with Image.open(io.BytesIO(archive.read(name))) as stored:
                    stored.load()
                    if stored.size != (outcome.final_width, outcome.final_height):
                        images_ok = False
                        details.append(f"{name} 尺寸不符")
                    if stored.format != outcome.encode_format:
                        images_ok = False
                        details.append(f"{name} 格式不符")
            except Exception as error:  # noqa: BLE001
                images_ok = False
                details.append(f"{name}: {error}")

            if outcome.final_width > outcome.orig_width or outcome.final_height > outcome.orig_height:
                images_ok = False
                details.append(f"{name} 被放大")
            if outcome.final_long_edge < min(args.min_long_edge, outcome.orig_long_edge):
                images_ok = False
                details.append(f"{name} 低于最小长边")

        check("所有图片都能解码且尺寸/格式正确", images_ok, "; ".join(details[:3]))
        check("图片没有被放大", all(
            o.final_width <= o.orig_width and o.final_height <= o.orig_height
            for o in result.outcomes
        ))
        check("小图片保护生效", all(
            o.final_long_edge >= min(args.min_long_edge, o.orig_long_edge)
            for o in result.outcomes
        ))
        check("PNG 仍然是 PNG", all(
            o.encode_format == "PNG" for o in result.outcomes if o.rel_path.endswith(".png")
        ))
        check("透明图片保留 alpha 通道", _alpha_kept(archive, result))

    # Sources untouched.
    after_bytes = sum(f.stat().st_size for f in source.rglob("*") if f.is_file())
    check("源文件夹未被修改", after_bytes == original_bytes,
          f"{format_size(after_bytes)}")
    check("没有残留 .part 文件", not output.with_name(output.name + ".part").exists())

    # ------------------------------------------------------------- summary
    print()
    print("=" * 72)
    print("优化结果")
    print("=" * 72)
    print(f"  原始大小        {format_size(result.original_bytes)}")
    print(f"  最终 ZIP 大小   {format_size(result.final_bytes)}")
    print(f"  目标大小        {format_size(result.target_bytes)}")
    print(f"  压缩比例        {format_percent(result.compression_ratio)}")
    print(f"  ZIP 空间利用率  {format_percent(result.utilization)}")
    print(f"  图片数量        {result.image_count} 张")
    print(f"  平均原始尺寸    {result.avg_original_dimensions}")
    print(f"  平均最终尺寸    {result.avg_final_dimensions}")
    print(f"  优化轮次        {result.rounds}")
    print(f"  耗时            {elapsed:.1f} 秒")
    print(f"  直接打包原文件  {'是' if result.copied_original else '否'}")
    print()

    print("  文件名                              原尺寸        最终尺寸      原大小      最终大小    缩放")
    print("  " + "-" * 92)
    for outcome in result.outcomes:
        print(
            f"  {outcome.rel_path:<34} {outcome.orig_dimensions:>12} "
            f"{outcome.final_dimensions:>13} {format_size(outcome.orig_bytes):>11} "
            f"{format_size(outcome.final_bytes):>11}  {format_percent(outcome.scale):>7}"
        )
    print()

    failed = [label for label, ok, _ in checks if not ok]
    print("=" * 72)
    if failed:
        print(f"验收失败：{len(failed)} / {len(checks)} 项未通过")
        for label in failed:
            print(f"  - {label}")
        code = 1
    else:
        print(f"验收通过：{len(checks)} / {len(checks)} 项全部通过")
        code = 0
    print("=" * 72)

    if args.keep:
        print(f"\n保留文件：\n  源文件夹 {source}\n  输出     {output}")
        code = code
    else:
        shutil.rmtree(workdir, ignore_errors=True)
        print("\n临时文件已清理。")

    return code


def _alpha_kept(archive: zipfile.ZipFile, result) -> bool:
    """Every image that had an alpha channel must still have one."""
    transparent = [
        o for o in result.outcomes
        if o.rel_path.endswith((".png", ".webp")) and "透明" in o.rel_path
    ]
    if not transparent:
        return True
    for outcome in transparent:
        with Image.open(io.BytesIO(archive.read(outcome.arcname))) as stored:
            if "A" not in stored.getbands():
                return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
