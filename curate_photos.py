"""在源目录内直接分类整理：可用 / 不可用 / 动图 三档，可用重命名为 001 002 ...

    # 干跑（不动文件），看看会怎么分
    python curate_photos.py --source "C:/Users/EDY/Pictures/2026-06" --dry-run

    # 正式执行：产出
    #   C:/Users/EDY/Pictures/2026-06/可用/001.jpg 002.jpg ... 221.jpg   (按 mtime 排序后重命名)
    #   C:/Users/EDY/Pictures/2026-06/不可用/xxx.jpg                      (原名保留)
    #   C:/Users/EDY/Pictures/2026-06/动图/*.gif *.mp4                    (原名保留)
    python curate_photos.py --source "C:/Users/EDY/Pictures/2026-06"

    # 不想重命名（可用/ 里保留原文件名）
    python curate_photos.py --source "..." --no-rename

    # 自定义子目录名（默认 可用/不可用/动图）
    python curate_photos.py --source "..." --usable-dir usable --rejected-dir rejected --animated-dir animated
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable

# 让脚本能从任何工作目录运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.photo_filter import curate_directory, PhotoInfo


def _unique_dest(dst: Path) -> Path:
    """dst 已存在则追加 _1 _2 直到不冲突。"""
    if not dst.exists():
        return dst
    stem, suf = dst.stem, dst.suffix
    for i in range(1, 10000):
        cand = dst.with_name(f"{stem}_{i}{suf}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"too many conflicts on {dst}")


def _pad_width(n: int) -> int:
    """221 张要 3 位、9999 张要 4 位。默认最少 3 位。"""
    return max(3, len(str(n)))


def run(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"source not a directory: {source}")

    # 先做一次筛图（不递归；避免误扫子目录）
    result = curate_directory(
        source,
        min_dim=args.min_dim,
        min_ratio=args.min_ratio,
        max_ratio=args.max_ratio,
        recursive=False,
    )

    # 可用按 mtime 排序后重命名
    usable_sorted = sorted(result.usable, key=lambda p: p.path.stat().st_mtime)
    pad = _pad_width(len(usable_sorted))

    usable_dir  = source / args.usable_dir
    rejected_dir = source / args.rejected_dir
    animated_dir = source / args.animated_dir

    # 忽略已经在子目录里的文件（第二次跑时不会把自己搬来搬去）
    for existing in (usable_dir, rejected_dir, animated_dir):
        usable_sorted = [p for p in usable_sorted if existing not in p.path.parents]
    result.rejected  = [(p, r) for p, r in result.rejected  if not any(d in p.path.parents for d in (usable_dir, rejected_dir, animated_dir))]
    result.animated  = [p for p in result.animated if not any(d in p.parents for d in (usable_dir, rejected_dir, animated_dir))]

    total_usable   = len(usable_sorted)
    total_rejected = len(result.rejected)
    total_animated = len(result.animated)

    # 打印计划
    print("=" * 62)
    print(f"  按创建时间已分月的目录内再分类整理 · {source}")
    print("=" * 62)
    print(f"  可用   → {usable_dir}/    ({total_usable} 张，"
          f"{'重命名' if args.rename else '保留原名'})")
    print(f"  不可用 → {rejected_dir}/  ({total_rejected} 张，保留原名)")
    print(f"  动图   → {animated_dir}/  ({total_animated} 张，保留原名)")
    if result.unreadable:
        print(f"  ⚠ 读不出: {len(result.unreadable)} 张，将跳过")

    # 可用示例（前 3 / 后 3）
    if usable_sorted:
        print("-" * 62)
        print("  可用重命名示例：")
        for i, info in enumerate(usable_sorted[:3], 1):
            new_name = (f"{i:0{pad}d}{info.path.suffix.lower()}"
                        if args.rename else info.path.name)
            t = time.strftime("%m-%d %H:%M", time.localtime(info.path.stat().st_mtime))
            print(f"    {i:>4}.  [{t}]  {info.path.name}  →  {new_name}")
        if len(usable_sorted) > 6:
            print(f"    ... 中间省略 {len(usable_sorted) - 6} 张 ...")
        for i, info in enumerate(usable_sorted[-3:], len(usable_sorted) - 2):
            new_name = (f"{i:0{pad}d}{info.path.suffix.lower()}"
                        if args.rename else info.path.name)
            t = time.strftime("%m-%d %H:%M", time.localtime(info.path.stat().st_mtime))
            print(f"    {i:>4}.  [{t}]  {info.path.name}  →  {new_name}")
    print("=" * 62)

    if args.dry_run:
        print("--dry-run，仅预览。加 --yes 或去掉 --dry-run 后再跑真的移动。")
        return 0

    if not args.yes:
        try:
            choice = input("确认执行以上分类与重命名？ [Y/n]: ").strip().lower()
        except EOFError:
            print("(非交互环境，默认中止；如需自动化请加 -y)")
            return 0
        if choice not in ("", "y", "yes"):
            print("已中止。")
            return 0

    # 建目录
    usable_dir.mkdir(exist_ok=True)
    rejected_dir.mkdir(exist_ok=True)
    animated_dir.mkdir(exist_ok=True)

    # 移动可用（可选重命名）
    moved_u = moved_r = moved_a = 0
    conflicts = 0
    for i, info in enumerate(usable_sorted, 1):
        if args.rename:
            new_name = f"{i:0{pad}d}{info.path.suffix.lower()}"
        else:
            new_name = info.path.name
        target = _unique_dest(usable_dir / new_name)
        if target.name != new_name:
            conflicts += 1
        shutil.move(str(info.path), str(target))
        moved_u += 1

    # 移动不可用
    for info, reason in result.rejected:
        target = _unique_dest(rejected_dir / info.path.name)
        if target.name != info.path.name:
            conflicts += 1
        shutil.move(str(info.path), str(target))
        moved_r += 1

    # 移动动图
    for p in result.animated:
        target = _unique_dest(animated_dir / p.name)
        if target.name != p.name:
            conflicts += 1
        shutil.move(str(p), str(target))
        moved_a += 1

    print("\n" + "=" * 62)
    print("  完成")
    print("=" * 62)
    print(f"  可用   移动 {moved_u} 张")
    print(f"  不可用 移动 {moved_r} 张")
    print(f"  动图   移动 {moved_a} 张")
    if conflicts:
        print(f"  同名冲突自动改名 {conflicts} 处")
    print()
    print(f"  下一步可以直接跑：")
    print(f"    python make_meme_video.py --source \"{usable_dir}\" --range 1-20")
    print("=" * 62)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="把图源目录里的图按尺寸/比例分成 可用/不可用/动图 三档，可用按 mtime 重命名 001..N",
    )
    p.add_argument("--source", type=str, required=True, help="图源目录，如 C:/Users/EDY/Pictures/2026-06")
    p.add_argument("--usable-dir",   type=str, default="可用",   help="可用图目标子目录名（默认 可用）")
    p.add_argument("--rejected-dir", type=str, default="不可用", help="剔除图目标子目录名（默认 不可用）")
    p.add_argument("--animated-dir", type=str, default="动图",   help="动图/视频目标子目录名（默认 动图）")

    p.add_argument("--rename", action="store_true", default=True,
                   help="可用图按 mtime 排序后重命名为 001 002 ...（默认开启）")
    p.add_argument("--no-rename", dest="rename", action="store_false",
                   help="可用图保留原文件名")

    p.add_argument("--min-dim",   type=int,   default=400)
    p.add_argument("--min-ratio", type=float, default=0.5)
    p.add_argument("--max-ratio", type=float, default=2.5)

    p.add_argument("--dry-run", action="store_true", help="只打印计划，不移动文件")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认提示")
    return p


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
