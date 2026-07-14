"""Carousel (横向滚动卡片) Jianying draft composer.

核心思路：
  1. Pillow 预渲染：
     - 纯图模式 (image card)：图片 cover-crop + 白底圆角 + 投影阴影
     - 数据模式 (data card)：在 image card 基础上，Pillow 绘制标题/副标题/星级/评论
     - 所有卡片横向拼接为一张超宽 strip PNG
     - 生成纯色（或首张模糊）背景图
  2. 剪映草稿三层轨道：
     - bg_track:    背景图铺满全程，静止
     - strip_track: strip 长图作为一段 VideoSegment，用 uniform_scale 让条带高度适配画布，
                    position_x 关键帧从右到左线性滚动
     - bgm_track:   BGM 循环/截断（挂在 strip_track 音频子轨），支持 fit_to_bgm

坐标系要点（来自 pyJianYingDraft）：
  - uniform_scale 相对于剪映"contain 适配画布"后的尺寸
  - position_x / position_y 单位 = 半个画布宽/高，正值表示右/上方向
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pyJianYingDraft as draft
from pyJianYingDraft import (
    ClipSettings, KeyframeProperty, TrackType, trange,
)
from PIL import Image

from pipeline.card_renderer import (
    CardData,
    assemble_strip,
    render_background,
    render_data_card,
    render_image_card,
    save_strip,
)
from pipeline.paths import CAROUSEL_DIR

ProgressFn = Callable[[str, int], None]


def get_audio_duration_us(path: str) -> int:
    """BGM 时长（微秒）。"""
    return int(draft.AudioMaterial(path).duration)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """把 '#18181c' / '18181c' 转成 (r,g,b)。"""
    s = hex_str.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"bg_color 必须是 #RRGGBB 格式，收到 {hex_str!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def compose_carousel_draft(
    cards: list[CardData],
    *,
    draft_name: str,
    draft_folder_path: str,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
    total_duration_s: float | None = None,
    seconds_per_card: float = 3.0,
    cards_visible: float = 3.5,
    card_radius: int = 18,
    card_gap: int = 30,
    side_padding: int = 100,
    bg_color: tuple[int, int, int] | str = (24, 24, 28),
    strip_height_ratio: float = 0.85,
    shadow_offset: int = 10,
    scroll_direction: str = "left",
    # 文字相关（仅数据驱动模式，Pillow 烘焙到卡片图上）
    render_text_on_card: bool = True,
    title_font_size_px: int = 42,
    subtitle_font_size_px: int = 26,
    title_color: tuple[int, int, int] = (30, 30, 30),
    subtitle_color: tuple[int, int, int] = (120, 120, 120),
    star_filled_color: tuple[int, int, int] = (255, 168, 0),
    comment_color: tuple[int, int, int] = (255, 105, 30),
    # BGM
    bgm_path: str | None = None,
    bgm_volume: float = 0.8,
    fit_to_bgm: bool = False,
    bg_blur_first: bool = False,
    on_progress: ProgressFn | None = None,
) -> str:
    """把一组 CardData 装成横向滚动卡片轮播的剪映草稿。

    Args:
        cards: 卡片数据列表（按展示顺序）。
        draft_name: 剪映草稿名（建议加时间戳避免冲突）。
        draft_folder_path: 剪映草稿根目录。
        canvas_width/height: 画布尺寸（默认 1920×1080 横屏）。
        total_duration_s: 指定总时长（秒）；None 时按卡片数 × seconds_per_card 计算。
        seconds_per_card: 每张卡片分配的秒数（total_duration_s 为 None 时生效）。
        cards_visible: 一屏可见几张卡（决定卡片大小）；默认 3.5。
        card_radius: 卡片圆角像素。
        card_gap: 卡片间距像素。
        side_padding: 条带首尾留白像素。
        bg_color: 背景色 (r,g,b) 0-255 或 '#RRGGBB' 字符串。
        strip_height_ratio: 条带显示高度占画布高度比例（0-1），默认 0.85。
        shadow_offset: 卡片阴影偏移像素。
        scroll_direction: 'left'（从右往左，默认）或 'right'（从左往右）。
        render_text_on_card: 数据模式下是否用 Pillow 把标题/副标题/星级/评论烘焙到卡片上。
        title_font_size_px: 卡片标题字号（像素，PIL 用）。
        subtitle_font_size_px: 副标题字号（像素）。
        bgm_path: BGM 文件路径；None 不加。
        bgm_volume: BGM 音量 0-1。
        fit_to_bgm: 视频总时长 = BGM 时长（需要 bgm_path）。
        bg_blur_first: True 时用第一张图的模糊版做背景，替代纯色背景。
        on_progress: 进度回调 (msg, pct)。
    """
    if not cards:
        raise ValueError("cards 不能为空")
    if scroll_direction not in ("left", "right"):
        raise ValueError(f"scroll_direction 必须是 'left' 或 'right'，收到 {scroll_direction!r}")

    # 解析颜色
    if isinstance(bg_color, str):
        bg_color_rgb = _hex_to_rgb(bg_color)
    else:
        bg_color_rgb = tuple(bg_color)

    # 短别名
    canvas_w = canvas_width
    canvas_h = canvas_height

    def prog(msg: str, pct: int) -> None:
        if on_progress:
            on_progress(msg, pct)

    prog("init carousel", 0)

    # ---- 1. 计算总时长 ----
    if fit_to_bgm:
        if not bgm_path:
            raise ValueError("--fit-to-bgm 需要同时指定 --bgm")
        bgm_dur_us = get_audio_duration_us(bgm_path)
        total_duration_us = bgm_dur_us
    elif total_duration_s is not None:
        total_duration_us = int(total_duration_s * 1_000_000)
    else:
        total_duration_us = int(len(cards) * seconds_per_card * 1_000_000)

    # ---- 2. 计算卡片像素尺寸 ----
    # 目标：一屏可见 cards_visible 张卡（含 gap），卡片高度由 strip_height_ratio 决定
    # cards_visible 张卡总宽度 = cards_visible * card_w + (cards_visible-1) * card_gap = canvas_w
    # 但图片卡和数据卡比例不同（数据卡底部要留文字区）
    card_h_display = int(canvas_h * strip_height_ratio)  # 条带整体高度
    # 纯图片卡的高度 = card_h_display（数据卡会更高因为有文字区，下面会再调整）
    img_card_h = int(card_h_display * 0.82)  # 图片部分占条带高的82%（留阴影空间）
    # 反推卡宽
    card_w = int(round((canvas_w - (cards_visible - 1) * card_gap) / cards_visible))
    card_w = min(card_w, int(img_card_h * 0.82))  # 球员卡/商品卡 3:4 竖版
    # 数据卡总高度（含底部文字区）
    data_card_h = int(card_h_display * 1.0)  # 数据卡整体高度 = 条带高度（含文字区）

    prog(f"card size: {card_w}x{img_card_h} (image) / {card_w}x{data_card_h} (data)", 2)

    # ---- 3. 渲染卡片 ----
    has_any_text = any(c.title or c.subtitle or c.stars or c.comment for c in cards)
    use_data_card = has_any_text and render_text_on_card

    card_images = []
    for i, card in enumerate(cards, start=1):
        if use_data_card:
            img = render_data_card(
                card,
                card_w,
                data_card_h,
                radius=card_radius,
                bg_color=(255, 255, 255),
                shadow_offset=shadow_offset,
                photo_area_ratio=0.62,
                inner_padding=14,
                gap_below_photo=14,
                title_size=title_font_size_px,
                subtitle_size=subtitle_font_size_px,
                star_size=int(title_font_size_px * 0.75),
                comment_size=int(title_font_size_px * 0.7),
                title_color=title_color,
                subtitle_color=subtitle_color,
                star_filled_color=star_filled_color,
                comment_color=comment_color,
            )
        else:
            img = render_image_card(
                card.image_path,
                card_w,
                img_card_h,
                radius=card_radius,
                bg_color=(255, 255, 255),
                padding=8,
                shadow_offset=shadow_offset,
            )
        card_images.append(img)
    prog(f"rendered {len(card_images)} cards ({'data' if use_data_card else 'image'})", 30)

    # ---- 4. 拼接条带 ----
    strip_img, card_centers_px = assemble_strip(
        card_images,
        gap=card_gap,
        side_padding=side_padding,
        bg_color=bg_color_rgb,
    )
    strip_w, strip_h = strip_img.size

    # 保存 strip 到 outputs/carousels/ 方便调试
    CAROUSEL_DIR.mkdir(parents=True, exist_ok=True)
    strip_path = save_strip(strip_img, CAROUSEL_DIR, f"strip_{len(cards)}c")
    prog(f"strip saved: {strip_path.name} ({strip_w}x{strip_h})", 35)

    # ---- 5. 准备背景图 ----
    import time as _time
    if bg_blur_first:
        from pipeline.blur_bg import get_blurred_bg
        bg_path = str(get_blurred_bg(cards[0].image_path, (canvas_w, canvas_h)))
    else:
        bg_img = render_background(canvas_w, canvas_h, bg_color=bg_color_rgb)
        bg_path = str(CAROUSEL_DIR / f"bg_{_time.strftime('%Y%m%d_%H%M%S')}.png")
        bg_img.save(bg_path)
    prog("background ready", 40)

    # ---- 6. 打开草稿文件夹，创建草稿 ----
    df = draft.DraftFolder(draft_folder_path)
    script = df.create_draft(draft_name, canvas_w, canvas_h, allow_replace=True)

    script.add_track(TrackType.video, "bg_track")
    # strip_track 是主视频轨，BGM音频轨单独添加
    strip_track_name = "strip_track"
    script.add_track(TrackType.video, strip_track_name)
    if bgm_path:
        script.add_track(TrackType.audio, "bgm_track")
    prog("tracks created", 50)

    # ---- 7. 计算滚动参数 ----
    # contain_base: 剪映 contain 模式下把 strip 塞进画布的基准缩放
    contain_base = min(canvas_w / strip_w, canvas_h / strip_h)
    # 目标：strip 显示高度 = canvas_h × strip_height_ratio（纯图模式）或 canvas_h × 0.95（数据模式，条带更高）
    if use_data_card:
        target_strip_display_h = canvas_h * 0.92
    else:
        target_strip_display_h = canvas_h * strip_height_ratio
    current_strip_display_h = strip_h * contain_base
    uniform_scale = target_strip_display_h / current_strip_display_h if current_strip_display_h > 0 else 1.0

    display_w = strip_w * contain_base * uniform_scale
    excess_w = max(0.0, display_w - canvas_w)
    # position_x 单位 = 半个画布宽；scroll_offset 是 strip 从"右边缘对齐"滚到"左边缘对齐"的半宽单位偏移
    # 推导：总移动距离像素 = excess_w；半画布宽 = canvas_w/2；半宽单位 = excess_w / (canvas_w/2) = 2*excess_w/canvas_w
    scroll_offset = excess_w * 2 / canvas_w
    if scroll_direction == "right":
        start_x, end_x = -scroll_offset, +scroll_offset
    else:
        start_x, end_x = +scroll_offset, -scroll_offset

    # Y 位置：条带垂直居中（数据模式因为有底部文字区稍微上移一些）
    strip_y = 0.0 if use_data_card else 0.05

    # ---- 8. 背景段（静止，全程）----
    full_trange = trange(0, total_duration_us)
    bg_seg = draft.VideoSegment(bg_path, full_trange)
    bg_seg.clip_settings = ClipSettings(transform_x=0, transform_y=0)
    bg_seg.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.0)
    for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
        bg_seg.add_keyframe(prop, 0, 0)
        bg_seg.add_keyframe(prop, total_duration_us, 0)
    script.add_segment(bg_seg, "bg_track")
    prog("background segment added", 55)

    # ---- 9. Strip 段（滚动，全程一段）----
    strip_seg = draft.VideoSegment(str(strip_path), full_trange)
    strip_seg.clip_settings = ClipSettings(transform_x=0, transform_y=strip_y)
    strip_seg.add_keyframe(KeyframeProperty.uniform_scale, 0, uniform_scale)
    strip_seg.add_keyframe(KeyframeProperty.uniform_scale, total_duration_us, uniform_scale)
    strip_seg.add_keyframe(KeyframeProperty.position_x, 0, start_x)
    strip_seg.add_keyframe(KeyframeProperty.position_x, total_duration_us, end_x)
    strip_seg.add_keyframe(KeyframeProperty.position_y, 0, strip_y)
    strip_seg.add_keyframe(KeyframeProperty.position_y, total_duration_us, strip_y)
    script.add_segment(strip_seg, strip_track_name)
    prog("strip segment added", 80)

    # ---- 10. BGM（循环/截断照搬 meme_composer 逻辑）----
    if bgm_path:
        prog("attach bgm", 90)
        bgm_dur = get_audio_duration_us(bgm_path)
        cur = 0
        while cur < total_duration_us:
            seg_dur = min(bgm_dur, total_duration_us - cur)
            script.add_segment(
                draft.AudioSegment(
                    bgm_path,
                    trange(cur, seg_dur),
                    volume=bgm_volume,
                    source_timerange=trange(0, seg_dur),
                ),
                "bgm_track",
            )
            cur += seg_dur

    # ---- 11. 保存 ----
    prog("saving draft", 96)
    script.save()
    prog("done", 100)

    total_seconds = total_duration_us / 1_000_000
    cards_count = len(cards)
    notes = [f"{cards_count} cards", f"{total_seconds:.1f}s", f"{canvas_w}x{canvas_h}"]
    if bgm_path:
        notes.append(f"bgm={Path(bgm_path).name}")
    if use_data_card:
        notes.append("text-baked")
    if bg_blur_first:
        notes.append("bg-blur")
    tail = " | ".join(notes)
    return f"Carousel draft '{draft_name}' created. ({tail})"
