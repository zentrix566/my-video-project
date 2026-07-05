"""从一个图片目录（如 C:/Users/EDY/Pictures/2026-06/可用）里挑图做梗图剪映草稿。

推荐工作流（配合 curate_photos.py 先分类重命名）：
    # Step 1: 分类整理（在 2026-06 下建 可用/不可用/动图 子目录，可用重命名为 001 002 ...）
    python curate_photos.py --source "C:/Users/EDY/Pictures/2026-06"

    # Step 2: 取序号 1-20 生成方屏梗图剪映草稿
    python make_meme_video.py --source "C:/Users/EDY/Pictures/2026-06/可用" --range 1-20

    # 取多段
    python make_meme_video.py --source "..." --range 1-10,21-30

    # 加 BGM（视频时长跟着 BGM 走）
    python make_meme_video.py --source "..." --range 1-20 \
        --bgm "D:/Music/sore-sore.mp3" --fit-to-bgm

    # 想要满屏铺满（可能裁剪超出部分；默认 contain 完整显示）
    python make_meme_video.py --source "..." --range 1-20 --fit-mode cover

    # 只做筛图预览
    python make_meme_video.py --source "..." --scan-only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pipeline.helpers import PipelineLogger, safe_slug
from pipeline.photo_filter import curate_directory
from pipeline.photo_ledger import UsedPhotosLedger, default_ledger_path
from pipeline.meme_composer import compose_meme_draft


ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = ROOT / "outputs" / "logs"
DEFAULT_DRAFT_FOLDER = "D:/Program Files/JianyingPro Drafts"


def _confirm(prompt: str) -> str:
    """[Y/n] 交互，回车默认 y。非交互环境返回 n。"""
    while True:
        try:
            choice = input(f"{prompt} [Y=继续 / n=中止]: ").strip().lower()
        except EOFError:
            print("(非交互环境，默认中止；如需自动化请加 -y)")
            return "n"
        if choice in ("", "y", "yes"):
            return "y"
        if choice in ("n", "no"):
            return "n"
        print("  请输入 y 或 n（回车默认 y）")


def _print_ledger(ledger: UsedPhotosLedger, source: Path) -> None:
    """打印账本状态：已用 N / 未用 M，以及最近 10 条使用记录。"""
    print("=" * 60)
    print(f"  账本文件: {ledger.path}")
    print("=" * 60)
    used = ledger.count_used()
    print(f"  已用记录:   {used} 条")

    if source.is_dir():
        from pipeline.photo_filter import STATIC_EXTS
        all_names = {f.name for f in source.iterdir()
                     if f.is_file() and f.suffix.lower() in STATIC_EXTS}
        used_names = ledger.used_names()
        unused = all_names - used_names
        gone   = used_names - all_names   # 账本里但目录里已没有的
        print(f"  目录当前:   {len(all_names)} 张静态图")
        print(f"  未用池:     {len(unused)} 张")
        if gone:
            print(f"  ⚠ 账本里有 {len(gone)} 张已不在目录（被删/移走）")

    entries = ledger.entries()
    if entries:
        print("-" * 60)
        print("  最近 10 条使用记录：")
        for e in entries[-10:]:
            when = str(e.get("used_at", "")).replace("T", " ")
            draft = e.get("draft", "")
            print(f"    [{when}]  {str(e.get('file', '')):<20}  ← {draft}")
    print("=" * 60)


def _parse_range(spec: str, max_index: int) -> list[int]:
    """把 '1-20' / '1-10,15-25' / '3' 之类字符串解析为 1-based 序号列表。"""
    result: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise SystemExit(f"--range 段格式错: {part}")
            if start < 1 or end < start:
                raise SystemExit(f"--range 段范围无效: {part}")
            result.extend(range(start, end + 1))
        else:
            try:
                result.append(int(part))
            except ValueError:
                raise SystemExit(f"--range 段格式错: {part}")
    # 去重保序
    seen = set()
    ordered: list[int] = []
    for n in result:
        if n in seen:
            continue
        if n < 1 or n > max_index:
            raise SystemExit(f"--range 值 {n} 超出可用范围 1-{max_index}")
        seen.add(n)
        ordered.append(n)
    if not ordered:
        raise SystemExit("--range 为空")
    return ordered


def _pick_photos(usable, count: int, sort: str, seed: int | None,
                 range_spec: str | None = None):
    """按 sort 或 range 挑图，返回 [PhotoInfo...]（保持展示顺序）。"""
    # 先按 sort 决定"全排序"
    if sort == "name":
        picks_all = sorted(usable, key=lambda p: p.path.name.lower())
    elif sort == "newest":
        picks_all = sorted(usable, key=lambda p: p.path.stat().st_mtime, reverse=True)
    elif sort == "oldest":
        picks_all = sorted(usable, key=lambda p: p.path.stat().st_mtime)
    elif sort == "random":
        import random
        rng = random.Random(seed)
        picks_all = list(usable)
        rng.shuffle(picks_all)
    else:
        raise SystemExit(f"未知 sort: {sort}")

    if range_spec:
        indices = _parse_range(range_spec, len(picks_all))
        picks = [picks_all[i - 1] for i in indices]
    else:
        picks = picks_all[:count]

    return picks


def run(args: argparse.Namespace) -> int:
    logger = PipelineLogger(DEFAULT_LOG_DIR)

    source = Path(args.source).expanduser().resolve()

    # ---- 账本预处理（在扫描前，show/reset 是短路操作）----
    ledger_path = (Path(args.ledger_file).expanduser().resolve()
                   if args.ledger_file else default_ledger_path(source))
    ledger = UsedPhotosLedger(ledger_path, source_dir=source)

    if args.show_ledger:
        _print_ledger(ledger, source)
        return 0

    if args.reset_ledger:
        n = ledger.count_used()
        ledger.reset()
        ledger.save()
        print(f"账本已清空（原有 {n} 条已用记录）")
        # 不 return —— 允许 --reset-ledger 与本次挑图串联

    result = curate_directory(
        source,
        min_dim=args.min_dim,
        min_ratio=args.min_ratio,
        max_ratio=args.max_ratio,
        recursive=args.recursive,
    )

    print("=" * 60)
    print("  梗图剪映草稿制作器 · 图源扫描")
    print("=" * 60)
    print(result.summary())
    print(result.rejected_report(limit=args.list_rejected))
    print("=" * 60)

    logger.info("meme.scan.done", source=str(source),
                usable=len(result.usable),
                rejected=len(result.rejected),
                animated=len(result.animated))

    if args.scan_only:
        print("--scan-only 模式，仅完成图源扫描。")
        return 0

    if not result.usable:
        raise SystemExit("没有可用的静态图 —— 请检查 --source 或放宽 --min-dim / --min-ratio。")

    # ---- 账本过滤：仅保留未用过的 ----
    used_before = ledger.count_used()
    if args.use_ledger:
        used_names = ledger.used_names()
        original_pool = len(result.usable)
        result.usable = [p for p in result.usable if p.path.name not in used_names]
        skipped = original_pool - len(result.usable)
        print(f"  [账本] {ledger_path}")
        print(f"  [账本] 历史已用 {used_before} 张，本次未用池 {len(result.usable)} 张（跳过 {skipped}）")
        if not result.usable:
            raise SystemExit("账本里所有图都用过了。加 --reset-ledger 清空重来，或换个 --source。")

    # BGM 预探测
    bgm_info = None
    if args.bgm:
        bgm_path = Path(args.bgm).expanduser().resolve()
        if not bgm_path.exists():
            raise SystemExit(f"BGM 文件不存在: {bgm_path}")
        try:
            import pyJianYingDraft as draft
            bgm_dur_us = int(draft.AudioMaterial(str(bgm_path)).duration)
            bgm_info = (bgm_path, bgm_dur_us)
        except Exception as exc:
            raise SystemExit(f"BGM 读不出时长: {exc}")

    # --auto-count：以「每张 --seconds 秒」为目标，用 BGM 时长反推需要几张
    # 相当于 --fit-to-bgm 但 count 也自动算出来，用户只指定每张几秒即可。
    if args.auto_count:
        if not bgm_info:
            raise SystemExit("--auto-count 需要同时指定 --bgm")
        if args.range:
            raise SystemExit("--auto-count 与 --range 互斥；--auto-count 会自动算张数")
        bgm_seconds = bgm_info[1] / 1_000_000
        auto_n = max(1, round(bgm_seconds / args.seconds))
        auto_n = min(auto_n, len(result.usable))
        args.count = auto_n
        args.fit_to_bgm = True   # 自动进入 fit-to-bgm，让视频 = 音频长度
        print(f"  [auto-count] BGM {bgm_seconds:.1f}s / 每张 ~{args.seconds:.1f}s "
              f"→ 自动选 {auto_n} 张（每张实际 {bgm_seconds/auto_n:.2f}s）")

    picks = _pick_photos(result.usable, args.count, args.sort, args.seed, args.range)

    # 计算每张图时长（用于预览）
    if args.fit_to_bgm:
        if not bgm_info:
            raise SystemExit("--fit-to-bgm 需要同时指定 --bgm")
        each_seconds = max(0.3, bgm_info[1] / 1_000_000 / len(picks))
    else:
        each_seconds = args.seconds

    print(f"\n  本次将挑选 {len(picks)} 张（sort={args.sort}），每张 {each_seconds:.2f}s，"
          f"总时长 {each_seconds * len(picks):.1f}s")
    if bgm_info:
        p, dur = bgm_info
        dur_s = dur / 1_000_000
        print(f"  BGM: {p.name}   时长 {dur_s:.1f}s   音量 {args.bgm_volume}"
              f"   {'（fit-to-bgm）' if args.fit_to_bgm else '（视频若长于 BGM 会循环，短则截断）'}")
    else:
        print("  BGM: (未指定，可在剪映里手动加)")
    print(f"  运镜: {'开启' if args.movement else '关闭'}  ·  叠化: {'开启' if not args.no_fade else '关闭'}")
    print("  图片列表：")
    for i, p in enumerate(picks, 1):
        t = time.strftime("%m-%d %H:%M", time.localtime(p.path.stat().st_mtime))
        print(f"    {i:>2}.  {p.width:>4}x{p.height:<4}  [{t}]  {p.path.name}")

    if not args.yes:
        choice = _confirm("\n是否用这批图生成剪映草稿？")
        if choice != "y":
            logger.info("meme.abort", reason="user rejected picks")
            print("已中止。")
            return 0

    # 生成草稿
    draft_name_base = args.draft_name or f"梗图_{source.name}"
    draft_name = f"{draft_name_base}_{time.strftime('%Y%m%d_%H%M%S')}"
    draft_folder = args.draft_folder or DEFAULT_DRAFT_FOLDER

    print("\n" + "=" * 60)
    print(f"  写入剪映草稿：{draft_name}")
    print(f"  剪映目录:     {draft_folder}")
    print("=" * 60)
    logger.info("meme.compose.start", draft_name=draft_name, count=len(picks),
                canvas=f"{args.canvas}x{args.canvas}", seconds=args.seconds)

    t0 = time.time()
    try:
        msg = compose_meme_draft(
            [str(p.path) for p in picks],
            draft_name=draft_name,
            draft_folder_path=draft_folder,
            canvas_width=args.canvas,
            canvas_height=args.canvas,
            seconds_per_image=args.seconds,
            add_movement=args.movement,
            fade_transition=not args.no_fade,
            camera_preset=args.camera_preset,
            fit_mode=args.fit_mode,
            bg_blur=args.bg_blur,
            bgm_path=args.bgm,
            bgm_volume=args.bgm_volume,
            fit_to_bgm=args.fit_to_bgm,
            on_progress=lambda m, p: logger.info("meme.compose.progress", message=m, pct=p),
        )
    except Exception as exc:
        logger.error("meme.compose.failed", error=str(exc))
        print(f"❌ 装配失败: {exc}")
        return 1

    elapsed = round(time.time() - t0, 1)
    logger.info("meme.compose.done", elapsed_s=elapsed)

    # ---- 写账本（仅当 --use-ledger 开启，且本次真的生成了草稿）----
    if args.use_ledger:
        added = ledger.mark_used([p.path.name for p in picks], draft_name)
        ledger.save()
        print(f"\n  [账本] 新增 {added} 张已用记录 → {ledger_path}")
        print(f"  [账本] 总计已用 {ledger.count_used()} 张")

    print("\n" + "=" * 60)
    print("  完成")
    print("=" * 60)
    print(f"  {msg}")
    print(f"  耗时:      {elapsed:.1f}s")
    print(f"  日志:      {logger.log_path}")
    print("=" * 60)
    if args.bgm:
        print("\n  已加 BGM，直接在剪映里打开新草稿即可预览/微调/导出。")
    else:
        print("\n  下一步：打开剪映专业版 → 找到新建的草稿 →")
        print("         右键「音频轨道」新建一条 → 拖入你想加的 BGM 即可。")
        print("  或者下次运行时加  --bgm \"D:/Music/xxx.mp3\"  自动挂上。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从图片目录挑图生成方屏梗图剪映草稿",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", type=str, required=True,
                        help="图源目录（如 C:/Users/EDY/Pictures/2026-06）")
    parser.add_argument("--count", type=int, default=20,
                        help="要挑的图片张数（默认 20；被 --range 覆盖）")
    parser.add_argument("--range", type=str, default=None,
                        help='指定序号，如 "1-20" 或 "1-10,15-25"；配合 --sort name 后精确取序号图')
    parser.add_argument("--sort", type=str, default="name",
                        choices=["name", "newest", "oldest", "random"],
                        help="挑图顺序：name 按文件名（默认，配合 001,002... 命名） / newest 最新 / oldest 最旧 / random 随机")
    parser.add_argument("--seed", type=int, default=None,
                        help="random 模式下的随机种子，可复现同一批")

    parser.add_argument("--canvas", type=int, default=1080,
                        help="方屏边长像素（默认 1080）")
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="每张图停留秒数（默认 8.0；被 --fit-to-bgm 覆盖）")
    parser.add_argument("--fit-mode", type=str, default="contain",
                        choices=["contain", "cover"],
                        help="contain=图片完整显示（默认，梗图推荐）；cover=铺满画布可能裁剪")
    parser.add_argument("--bg-blur", action="store_true", default=True,
                        help="contain 模式下用模糊的同款图填 letterbox 位置（默认开启，去黑边）")
    parser.add_argument("--no-bg-blur", dest="bg_blur", action="store_false",
                        help="关闭模糊背景，contain 模式下留纯黑边")

    parser.add_argument("--camera-preset", type=str, default="mixed",
                        help="运镜预设名 mixed/all/pan/zoom（默认 mixed，仅在 --movement 打开时生效）")
    parser.add_argument("--movement", action="store_true",
                        help="启用运镜关键帧；梗图默认关闭（纯图片切换更符合 B 站梗图节奏）")
    parser.add_argument("--no-movement", action="store_true",
                        help="（旧参数，已默认关闭运镜，保留兼容）")
    parser.add_argument("--no-fade", action="store_true",
                        help="禁用相邻图之间的叠化转场")

    parser.add_argument("--bgm", type=str, default=None,
                        help='BGM 音频路径（mp3/wav）。BGM 会写入音频轨；'
                             '比视频短则循环、长则截断到视频总时长')
    parser.add_argument("--bgm-volume", type=float, default=0.8,
                        help="BGM 音量 0.0-1.0（默认 0.8，梗图 BGM 是主角）")
    parser.add_argument("--fit-to-bgm", action="store_true",
                        help="让视频时长跟随 BGM 时长：每张图时长 = BGM 时长 / 张数（忽略 --seconds）")
    parser.add_argument("--auto-count", action="store_true",
                        help="以 --seconds 为每张目标秒数，自动算需要几张图让视频 = BGM 时长 "
                             "（等价于 --fit-to-bgm，但张数自动，你只管定节奏）")

    parser.add_argument("--min-dim", type=int, default=400,
                        help="判定 usable 的最小边长（默认 400）")
    parser.add_argument("--min-ratio", type=float, default=0.5,
                        help="判定 usable 的最小宽高比（默认 0.5，超窄剔除）")
    parser.add_argument("--max-ratio", type=float, default=2.5,
                        help="判定 usable 的最大宽高比（默认 2.5，超宽剔除）")
    parser.add_argument("--recursive", action="store_true",
                        help="递归扫描 --source 下的子目录")
    parser.add_argument("--list-rejected", type=int, default=10,
                        help="展示前 N 条剔除清单（默认 10）")

    parser.add_argument("--draft-name", type=str, default=None,
                        help="剪映草稿名前缀（默认 梗图_<源目录名>）；实际名会自动加时间戳后缀")
    parser.add_argument("--draft-folder", type=str, default=None,
                        help=f"剪映草稿目录（默认 {DEFAULT_DRAFT_FOLDER}）")

    parser.add_argument("--scan-only", action="store_true",
                        help="只做筛图预览、不写草稿")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="关闭确认卡点，一路跑到底")

    # 使用账本（跨次运行标记哪些图已经用过）
    parser.add_argument("--use-ledger", action="store_true",
                        help="启用「已用清单」：本次只从未用图里挑；结束后把选中的图写进账本")
    parser.add_argument("--ledger-file", type=str, default=None,
                        help='账本文件路径（默认 <source>/.used_photos.json）')
    parser.add_argument("--show-ledger", action="store_true",
                        help="打印账本当前状态（已用 N / 未用 M / 最近记录），不生成草稿")
    parser.add_argument("--reset-ledger", action="store_true",
                        help="清空账本（可与本次挑图串联；单独用等于清空重来）")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
