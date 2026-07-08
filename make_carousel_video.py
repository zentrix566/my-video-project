"""横屏卡片轮播视频剪映草稿生成器（球员评分 / 商品推荐 / 图集BGM 类）。

两种输入模式：

  模式 A — 本地图片目录（图片本身已经是带 UI 的完整卡片，如截图）：
    python make_carousel_video.py --source "C:/photos/players" \\
        --bgm "D:/Music/bgm.mp3" --fit-to-bgm

  模式 B — JSON 数据驱动（Pillow 自动渲染白底圆角卡片 + 标题/副标题/星级/评论）：
    python make_carousel_video.py --data "C:/photos/players/cards.json" \\
        --bgm "D:/Music/bgm.mp3"

JSON 格式示例：
{
  "cards": [
    {"image": "photos/player1.jpg", "title": "拉亚", "subtitle": "JRs评分 248", "stars": 4.0, "comment": "该球员未上场"},
    {"image": "photos/player2.jpg", "title": "努内斯", "subtitle": "JRs评分 274", "stars": 4.0, "comment": "你为什么不给上"}
  ],
  "bgm": "D:/Music/sore-sore.mp3",
  "bg_color": "#18181c"
}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pipeline.card_renderer import CardData
from pipeline.carousel_composer import compose_carousel_draft, get_audio_duration_us
from pipeline.helpers import PipelineLogger
from pipeline.paths import (
    JIANYING_DRAFT_FOLDER as DEFAULT_DRAFT_FOLDER,
    LOG_DIR as DEFAULT_LOG_DIR,
)
from pipeline.photo_filter import curate_directory

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------
#  工具函数
# ---------------------------------------------------------------------

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


def _parse_range(spec: str, max_index: int) -> list[int]:
    """把 '1-20' / '1-10,15-25' / '3' 解析为 1-based 序号列表。"""
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
    seen: set[int] = set()
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
    """按 sort 或 range 挑图，返回 [PhotoInfo...]。"""
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
        return [picks_all[i - 1] for i in indices]
    return picks_all[:count]


def _resolve_image_path(p: str, base_dir: Path) -> str:
    """把 JSON 里的相对路径解析为绝对路径。"""
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str((base_dir / pp).resolve())


def _load_data_json(data_path: Path) -> tuple[list[CardData], dict]:
    """加载 --data JSON，返回 (cards, meta)。"""
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list):
        raw_cards = raw
        meta: dict = {}
    elif isinstance(raw, dict):
        raw_cards = raw.get("cards") or raw.get("items") or []
        meta = {k: v for k, v in raw.items() if k not in ("cards", "items")}
    else:
        raise SystemExit(f"JSON 顶层必须是 list 或 dict，实际是 {type(raw).__name__}")

    if not raw_cards:
        raise SystemExit("JSON 里没有 cards 数组")

    base_dir = data_path.parent
    cards: list[CardData] = []
    for i, item in enumerate(raw_cards, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"cards[{i}] 必须是 object")
        img = item.get("image") or item.get("image_path") or item.get("path")
        if not img:
            raise SystemExit(f"cards[{i}] 缺少 image 字段")
        flag = item.get("flag")
        cards.append(CardData(
            image_path=_resolve_image_path(img, base_dir),
            title=str(item.get("title", "") or ""),
            subtitle=str(item.get("subtitle", "") or ""),
            stars=float(item.get("stars", 0) or 0),
            comment=str(item.get("comment", "") or ""),
            flag_path=_resolve_image_path(flag, base_dir) if flag else None,
        ))
    return cards, meta


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    s = hex_str.lstrip("#")
    if len(s) != 6:
        raise SystemExit(f"--bg-color 必须是 #RRGGBB 格式，收到 {hex_str!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# ---------------------------------------------------------------------
#  主流程
# ---------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    logger = PipelineLogger(DEFAULT_LOG_DIR)

    # ---- 输入模式判定 ----
    data_path: Path | None = None
    source_path: Path | None = None

    if args.data:
        data_path = Path(args.data).expanduser().resolve()
        if not data_path.exists():
            raise SystemExit(f"JSON 文件不存在: {data_path}")
    if args.source:
        source_path = Path(args.source).expanduser().resolve()
        if not source_path.is_dir():
            raise SystemExit(f"source 目录不存在: {source_path}")

    if not data_path and not source_path:
        raise SystemExit("必须传 --source <图片目录> 或 --data <json文件>（至少一个）")

    # ---- 加载卡片列表 ----
    cards: list[CardData] = []
    meta: dict = {}

    if data_path:
        cards, meta = _load_data_json(data_path)
        if meta.get("bgm") and not args.bgm:
            args.bgm = meta["bgm"]
        if meta.get("bg_color") and not args.bg_color:
            args.bg_color = meta["bg_color"]
        print(f"  从 {data_path.name} 加载 {len(cards)} 张卡片（数据驱动模式，文字烘焙到卡片）")

    source_cards: list[CardData] = []
    if source_path:
        # 宽松筛选（轮播图任何尺寸都能 work）
        result = curate_directory(
            source_path,
            min_dim=args.min_dim,
            min_ratio=0.2,
            max_ratio=5.0,
            recursive=args.recursive,
        )
        print("=" * 60)
        print("  轮播视频 · 图源扫描")
        print("=" * 60)
        print(result.summary())
        if not result.usable:
            raise SystemExit("source 目录没有可用的静态图")
        if args.scan_only:
            print("--scan-only 模式，仅完成扫描。")
            return 0
        picks = _pick_photos(result.usable, args.count, args.sort, args.seed, args.range)
        source_cards = [CardData(image_path=str(p.path)) for p in picks]
        print(f"  从目录挑选 {len(picks)} 张图片")
        cards = source_cards + cards  # source 在前，data 在后（如有）

    if not cards:
        raise SystemExit("没有可渲染的卡片")

    # ---- 画布/样式参数 ----
    canvas_w = args.canvas_w
    canvas_h = args.canvas_h
    bg_color_hex = args.bg_color or "#18181c"
    bg_color = _hex_to_rgb(bg_color_hex)

    # 判断是否需要烘焙文字：当卡片里有 title/subtitle 且没有 --no-text
    has_text_content = any(c.title or c.subtitle or c.stars or c.comment for c in cards)
    render_text_on_card = has_text_content and not args.no_text

    # ---- BGM 预探测 ----
    bgm_info = None
    if args.bgm:
        bgm_path = Path(args.bgm).expanduser().resolve()
        if not bgm_path.exists():
            raise SystemExit(f"BGM 文件不存在: {bgm_path}")
        try:
            bgm_dur_us = get_audio_duration_us(str(bgm_path))
            bgm_info = (bgm_path, bgm_dur_us)
        except Exception as exc:
            raise SystemExit(f"BGM 读不出时长: {exc}")

    # ---- 时长计算 ----
    if args.fit_to_bgm:
        if not bgm_info:
            raise SystemExit("--fit-to-bgm 需要同时指定 --bgm")
        total_duration_s = None
        each_seconds = max(0.3, bgm_info[1] / 1_000_000 / len(cards))
    elif args.duration:
        total_duration_s = args.duration
        each_seconds = total_duration_s / len(cards)
    else:
        total_duration_s = None
        each_seconds = args.seconds_per_card

    total_preview_s = each_seconds * len(cards)

    # ---- 打印摘要 ----
    print("=" * 60)
    print("  轮播卡片视频 · 草稿预览")
    print("=" * 60)
    print(f"  卡片数:     {len(cards)}")
    print(f"  画布尺寸:   {canvas_w}x{canvas_h}")
    print(f"  一屏可见:   {args.cards_visible} 张")
    print(f"  卡片文字:   {'烘焙到图片' if render_text_on_card else '关闭（图片自带）'}")
    print(f"  每张秒数:   {each_seconds:.2f}s  ·  总时长: {total_preview_s:.1f}s")
    print(f"  滚动方向:   {args.direction}  ·  背景色: {bg_color_hex}")
    if bgm_info:
        p, dur = bgm_info
        print(f"  BGM:        {p.name}  ·  {dur/1_000_000:.1f}s  ·  音量 {args.bgm_volume}"
              f"  ·  {'(fit-to-bgm)' if args.fit_to_bgm else '(视频若长于BGM会循环，短则截断)'}")
    else:
        print("  BGM:        (未指定，可在剪映里手动加)")
    print("  卡片列表:")
    for i, c in enumerate(cards, 1):
        p_name = Path(c.image_path).name
        title_part = f"  「{c.title}」" if c.title else ""
        sub_part = f" / {c.subtitle}" if c.subtitle else ""
        star_part = f"  ★{c.stars}" if c.stars else ""
        print(f"    {i:>2}. {p_name}{title_part}{sub_part}{star_part}")

    if not args.yes:
        choice = _confirm("\n是否生成剪映草稿？")
        if choice != "y":
            print("已中止。")
            logger.info("carousel.abort", reason="user rejected")
            return 0

    # ---- 生成草稿 ----
    name_base = args.draft_name or (f"轮播_{data_path.stem}" if data_path
                                    else f"轮播_{source_path.name}" if source_path
                                    else "轮播")
    draft_name = f"{name_base}_{time.strftime('%Y%m%d_%H%M%S')}"
    draft_folder = args.draft_folder or DEFAULT_DRAFT_FOLDER

    print("\n" + "=" * 60)
    print(f"  写入剪映草稿: {draft_name}")
    print(f"  剪映目录:     {draft_folder}")
    print("=" * 60)
    logger.info("carousel.compose.start", draft_name=draft_name,
                count=len(cards), canvas=f"{canvas_w}x{canvas_h}")

    t0 = time.time()
    try:
        msg = compose_carousel_draft(
            cards,
            draft_name=draft_name,
            draft_folder_path=draft_folder,
            canvas_width=canvas_w,
            canvas_height=canvas_h,
            total_duration_s=total_duration_s,
            seconds_per_card=args.seconds_per_card,
            cards_visible=args.cards_visible,
            card_radius=args.card_radius,
            card_gap=args.card_gap,
            bg_color=bg_color,
            strip_height_ratio=args.strip_height,
            render_text_on_card=render_text_on_card,
            title_font_size_px=args.title_size_px,
            subtitle_font_size_px=args.subtitle_size_px,
            bgm_path=str(bgm_info[0]) if bgm_info else None,
            bgm_volume=args.bgm_volume,
            fit_to_bgm=args.fit_to_bgm,
            bg_blur_first=args.bg_blur,
            scroll_direction=args.direction,
            on_progress=lambda m, p: logger.info("carousel.compose.progress", message=m, pct=p),
        )
    except Exception as exc:
        logger.error("carousel.compose.failed", error=str(exc))
        import traceback
        traceback.print_exc()
        print(f"\n❌ 装配失败: {exc}")
        return 1

    elapsed = round(time.time() - t0, 1)
    logger.info("carousel.compose.done", elapsed_s=elapsed)

    print("\n" + "=" * 60)
    print("  完成")
    print("=" * 60)
    print(f"  {msg}")
    print(f"  耗时:      {elapsed:.1f}s")
    print(f"  日志:      {logger.log_path}")
    print("=" * 60)
    if bgm_info:
        print("\n  已加 BGM，直接在剪映里打开新草稿即可预览/微调/导出。")
    else:
        print("\n  下一步：打开剪映专业版 → 找到新建的草稿 → 拖入BGM即可。")
        print("  或者下次运行加  --bgm \"D:/Music/xxx.mp3\"  自动挂上。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成横向滚动卡片轮播 + BGM 的剪映草稿（球员评分/商品推荐/图集BGM类视频）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 输入
    src_group = parser.add_argument_group("输入")
    src_group.add_argument("--source", type=str, default=None,
                           help="图片目录（目录里所有图作为卡片，适用于图片已自带UI如截图）")
    src_group.add_argument("--data", type=str, default=None,
                           help="JSON 数据文件路径（结构化卡片数据，Pillow 自动渲染标题/副标题/星级/评论到卡片上）")

    # 图片挑选
    pick_group = parser.add_argument_group("图片挑选（仅 --source 模式生效）")
    pick_group.add_argument("--count", type=int, default=30,
                            help="最多取多少张（默认 30）")
    pick_group.add_argument("--range", type=str, default=None,
                            help='指定序号，如 "1-10" 或 "1-5,8-12"')
    pick_group.add_argument("--sort", type=str, default="name",
                            choices=["name", "newest", "oldest", "random"],
                            help="排序方式（默认 name 按文件名）")
    pick_group.add_argument("--seed", type=int, default=None,
                            help="random 模式的随机种子")
    pick_group.add_argument("--recursive", action="store_true",
                            help="递归扫描子目录")
    pick_group.add_argument("--min-dim", type=int, default=200,
                            help="最小边长阈值（默认 200，过滤真正的缩略图垃圾）")

    # 画布/卡片样式
    style_group = parser.add_argument_group("画布与样式")
    style_group.add_argument("--canvas-w", type=int, default=1920,
                             help="画布宽（默认 1920 横屏）")
    style_group.add_argument("--canvas-h", type=int, default=1080,
                             help="画布高（默认 1080）")
    style_group.add_argument("--cards-visible", type=float, default=3.5,
                             help="一屏可见几张卡（默认 3.5，决定卡片大小）")
    style_group.add_argument("--card-radius", type=int, default=18,
                             help="卡片圆角像素（默认 18）")
    style_group.add_argument("--card-gap", type=int, default=30,
                             help="卡片间距像素（默认 30）")
    style_group.add_argument("--strip-height", type=float, default=0.85,
                             help="卡片条带高度占画布比例（默认 0.85）")
    style_group.add_argument("--bg-color", type=str, default=None,
                             help="背景色 #RRGGBB（默认 #18181c 深灰）")
    style_group.add_argument("--bg-blur", action="store_true",
                             help="用第一张图的模糊版做背景（替代纯色背景）")
    style_group.add_argument("--direction", type=str, default="left",
                             choices=["left", "right"],
                             help="滚动方向：left=从右往左（默认），right=从左往右")

    # 卡片文字（Pillow 烘焙）
    text_group = parser.add_argument_group("卡片文字（仅 --data 模式，Pillow 烘焙到卡片）")
    text_group.add_argument("--no-text", action="store_true",
                            help="关闭文字渲染（即使 data JSON 里有文字字段也不画）")
    text_group.add_argument("--title-size-px", type=int, default=42,
                            help="标题字号像素（默认 42）")
    text_group.add_argument("--subtitle-size-px", type=int, default=26,
                            help="副标题字号像素（默认 26）")

    # 时长 / BGM
    time_group = parser.add_argument_group("时长与BGM")
    time_group.add_argument("--seconds-per-card", type=float, default=3.0,
                            help="每张卡片分配秒数（默认 3.0，总时长 = 卡片数 × 秒数）")
    time_group.add_argument("--duration", type=float, default=None,
                            help="直接指定总时长秒数（覆盖 seconds-per-card）")
    time_group.add_argument("--bgm", type=str, default=None,
                            help="BGM 文件路径（mp3/wav）；短则循环，长则截断")
    time_group.add_argument("--bgm-volume", type=float, default=0.8,
                            help="BGM 音量 0-1（默认 0.8）")
    time_group.add_argument("--fit-to-bgm", action="store_true",
                            help="视频时长 = BGM 时长（忽略 seconds-per-card）")

    # 输出
    out_group = parser.add_argument_group("输出")
    out_group.add_argument("--draft-name", type=str, default=None,
                           help="草稿名前缀（默认 轮播_<名>）；实际名加时间戳")
    out_group.add_argument("--draft-folder", type=str, default=None,
                           help=f"剪映草稿目录（默认 {DEFAULT_DRAFT_FOLDER}）")
    out_group.add_argument("--scan-only", action="store_true",
                           help="只做扫描预览，不生成草稿")
    out_group.add_argument("--yes", "-y", action="store_true",
                           help="关闭确认，一路跑到底")

    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
