"""轮播卡片渲染器：用 Pillow 把图片+文字加工为白底圆角卡片，并拼接成超宽条带。

两种卡片形态：
- render_image_card: 纯图片圆角卡片（--source 模式，图片已自带文字/UI）
- render_data_card:  白底 UI 卡片，顶部图片 + 下方标题/副标题/星级/评论（--data 模式）

输出的 strip 图保存到 outputs/carousels/ 方便复用/调试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CardData:
    """一张轮播卡片的数据。

    image_path 必填；title/subtitle/stars/comment 可选，用于数据驱动模式。
    额外的 image_scale_override 可让某张卡的图片区域缩放不同（比如横图不想裁太多）。
    """
    image_path: str
    title: str = ""
    subtitle: str = ""
    stars: float = 0.0          # 0~5，支持半星（如 3.5）
    comment: str = ""           # 底部橙色评论
    flag_path: Optional[str] = None  # 可选：右上角小国旗/角标图片


# ---------------------------------------------------------------------------
# 字体加载（Windows 默认微软雅黑，找不到则回退到 PIL 默认字体）
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[str, int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size_px: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """加载中文字体；优先微软雅黑粗/常规，找不到回退 PIL 默认。"""
    key = ("msyh", size_px, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    candidates = []
    if bold:
        # Windows 微软雅黑粗体（msyhbd.ttc）或苹方粗体
        candidates += [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",  # 回退到常规
            "C:/Windows/Fonts/simhei.ttf",
        ]
    else:
        candidates += [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyh.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        ]

    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    for path in candidates:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size_px)
                _FONT_CACHE[key] = font
                return font
            except OSError:
                continue

    # 最后回退：PIL 默认字体（仅支持 ASCII，中文会变方块）
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ---------------------------------------------------------------------------
# 图像工具
# ---------------------------------------------------------------------------

def _rounded_rect_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """生成一张 L 模式的圆角矩形蒙版（白色=不透明，黑色=透明）。"""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """按 cover 模式裁剪居中区域到 target_w × target_h（类似 CSS object-fit: cover）。"""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img_resized.crop((left, top, left + target_w, top + target_h))


def _draw_shadowed_rounded_rect(
    canvas: Image.Image,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    shadow_offset: int = 10,
    shadow_blur: int = 18,
    shadow_color: tuple[int, int, int, int] = (0, 0, 0, 120),
) -> None:
    """在 canvas 上画一个带软阴影的圆角矩形（阴影先画在最底层）。"""
    x1, y1, x2, y2 = xy
    w = x2 - x1
    h = y2 - y1

    # 阴影层：比卡片稍大的黑色圆角矩形 + 高斯模糊
    shadow = Image.new("RGBA", (w + shadow_blur * 4, h + shadow_blur * 4), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle(
        (shadow_blur * 2, shadow_blur * 2, shadow_blur * 2 + w, shadow_blur * 2 + h),
        radius=radius,
        fill=shadow_color,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

    # 把阴影贴到 canvas（需要 canvas 是 RGBA）
    px = x1 - shadow_blur * 2 + shadow_offset // 2
    py = y1 - shadow_blur * 2 + shadow_offset
    canvas.alpha_composite(shadow, (px, py))

    # 画白底卡片本体
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


# ---------------------------------------------------------------------------
# 卡片渲染
# ---------------------------------------------------------------------------

def render_image_card(
    img_path: str | Path,
    card_w: int,
    card_h: int,
    *,
    radius: int = 20,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    padding: int = 8,
    shadow_offset: int = 8,
) -> Image.Image:
    """纯图片模式：把源图加白底圆角边框和阴影。

    图片会被 cover-crop 到 (card_w - 2*padding) × (card_h - 2*padding)，
    外层是 bg_color 圆角矩形底板+阴影。
    返回 RGBA 模式图。
    """
    inner_w = card_w - padding * 2
    inner_h = card_h - padding * 2

    canvas = Image.new("RGBA", (card_w + shadow_offset * 3, card_h + shadow_offset * 3), (0, 0, 0, 0))

    # 卡片底板+阴影
    _draw_shadowed_rounded_rect(
        canvas,
        (shadow_offset, shadow_offset, shadow_offset + card_w, shadow_offset + card_h),
        radius=radius,
        fill=bg_color + (255,),
        shadow_offset=shadow_offset,
    )

    # 封面图（圆角矩形贴在白底之上）
    with Image.open(img_path) as src:
        src = src.convert("RGB")
        photo = _cover_crop(src, inner_w, inner_h)

    # 把照片也裁成圆角（圆角比底板稍小）
    photo_radius = max(radius - padding, 4)
    photo_mask = _rounded_rect_mask((inner_w, inner_h), photo_radius)
    photo_rgba = photo.convert("RGBA")
    photo_rgba.putalpha(photo_mask)

    canvas.alpha_composite(photo_rgba, (shadow_offset + padding, shadow_offset + padding))

    # 裁掉多余的透明边缘，返回正好 card_w × card_h 的图（阴影已烘焙进 alpha）
    # 为了拼接条带时方便对齐，统一裁到 card_w × card_h，阴影允许溢出到 alpha
    # 实际上 _draw_shadowed_rounded_rect 的 shadow 贴在偏移位置，我们返回时把画布偏移回来
    # 简化：直接返回 canvas 的中心 card_w × card_h 区域（这样阴影下半/右半被裁掉，不好看）
    # 改为返回完整 canvas 让调用方处理（不，更简单：在 card 本身的尺寸上预留阴影空间）
    # 重新用干净方案：
    return _trim_to_card(canvas, card_w, card_h, shadow_offset)


def _trim_to_card(canvas: Image.Image, card_w: int, card_h: int, shadow_offset: int) -> Image.Image:
    """从带阴影的 canvas 中裁出正好包含卡片+完整阴影的 bbox，返回 RGBA 图。"""
    bbox = canvas.getbbox()
    if bbox is None:
        return canvas.crop((0, 0, card_w, card_h))
    return canvas.crop(bbox)


def render_data_card(
    data: CardData,
    card_w: int,
    card_h: int,
    *,
    radius: int = 20,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    shadow_offset: int = 8,
    photo_area_ratio: float = 0.62,  # 图片区占卡片高度的比例
    inner_padding: int = 14,         # 卡片内部边距
    gap_below_photo: int = 14,       # 图片与标题间距
    title_size: int = 40,
    subtitle_size: int = 26,
    star_size: int = 30,
    comment_size: int = 28,
    title_color: tuple[int, int, int] = (30, 30, 30),
    subtitle_color: tuple[int, int, int] = (120, 120, 120),
    star_filled_color: tuple[int, int, int] = (255, 168, 0),
    star_empty_color: tuple[int, int, int] = (210, 210, 210),
    comment_color: tuple[int, int, int] = (255, 105, 30),
) -> Image.Image:
    """数据驱动模式：渲染一张带图片+标题+副标题+星级+评论的完整UI卡片。

    布局（从上到下）：
      ┌─────────────────┐
      │    图片区域      │ ← photo_area_ratio × card_h
      │    (圆角)       │
      ├─────────────────┤
      │    标题 (大字)   │
      │  ★★★★☆  评分    │
      │ 副标题 / 补充信息 │
      │  橙色评论(可选)  │
      └─────────────────┘
    """
    # 画布设大一些以容纳阴影
    bleed = shadow_offset * 3
    canvas = Image.new("RGBA", (card_w + bleed, card_h + bleed), (0, 0, 0, 0))

    card_x0 = bleed // 2
    card_y0 = bleed // 2
    card_x1 = card_x0 + card_w
    card_y1 = card_y0 + card_h

    _draw_shadowed_rounded_rect(
        canvas,
        (card_x0, card_y0, card_x1, card_y1),
        radius=radius,
        fill=bg_color + (255,),
        shadow_offset=shadow_offset,
    )

    draw = ImageDraw.Draw(canvas)

    # ---- 图片区 ----
    photo_w = card_w - inner_padding * 2
    photo_h = int((card_h - inner_padding * 2) * photo_area_ratio)
    photo_x0 = card_x0 + inner_padding
    photo_y0 = card_y0 + inner_padding
    photo_radius = max(radius - inner_padding, 6)

    with Image.open(data.image_path) as src:
        src = src.convert("RGB")
        photo = _cover_crop(src, photo_w, photo_h)

    photo_mask = _rounded_rect_mask((photo_w, photo_h), photo_radius)
    photo_rgba = photo.convert("RGBA")
    photo_rgba.putalpha(photo_mask)
    canvas.alpha_composite(photo_rgba, (photo_x0, photo_y0))

    # ---- 右上角国旗/角标（可选）----
    if data.flag_path and Path(data.flag_path).exists():
        flag_size = int(photo_h * 0.12)  # 约为图片高度的12%
        try:
            with Image.open(data.flag_path) as flag:
                flag = flag.convert("RGBA")
                flag.thumbnail((flag_size, flag_size), Image.LANCZOS)
                fx = photo_x0 + photo_w - flag.width - 8
                fy = photo_y0 + 8
                canvas.alpha_composite(flag, (fx, fy))
        except OSError:
            pass

    # ---- 文字区 ----
    cursor_y = photo_y0 + photo_h + gap_below_photo

    # 标题（粗体大字）
    if data.title:
        font_title = _load_font(title_size, bold=True)
        draw.text(
            (card_x0 + card_w // 2, cursor_y),
            data.title,
            font=font_title,
            fill=title_color,
            anchor="mt",
        )
        bbox = draw.textbbox((0, 0), data.title, font=font_title)
        cursor_y += (bbox[3] - bbox[1]) + 10

    # 星级 ★★★★☆
    if data.stars > 0:
        font_star = _load_font(star_size, bold=False)
        full = int(data.stars)
        half = data.stars - full >= 0.5
        empty = 5 - full - (1 if half else 0)
        star_str = "★" * full + ("★" if half else "") + "☆" * (empty - (1 if half else 0))
        if half:
            # 简化：半星用实星代替（PIL 不方便画半星）
            star_str = "★" * (full + 1) + "☆" * empty
        star_str = star_str.ljust(5, "☆")[:5]
        # 画星
        total_star_w = 0
        star_gap = 0
        star_cw = font_star.getlength("★")
        total_star_w = int(star_cw * 5 + star_gap * 4)
        sx_start = card_x0 + card_w // 2 - total_star_w // 2
        # 逐颗星上色（前 full+(half?1:0) 颗金色，其余灰色）
        filled_count = full + (1 if half else 0)
        for idx, ch in enumerate(star_str):
            color = star_filled_color if idx < filled_count else star_empty_color
            draw.text(
                (sx_start + idx * (star_cw + star_gap), cursor_y),
                ch,
                font=font_star,
                fill=color,
                anchor="lt",
            )
        cursor_y += star_size + 8

    # 副标题（灰色小字，如评分/JRs编号）
    if data.subtitle:
        font_sub = _load_font(subtitle_size, bold=False)
        draw.text(
            (card_x0 + card_w // 2, cursor_y),
            data.subtitle,
            font=font_sub,
            fill=subtitle_color,
            anchor="mt",
        )
        bbox = draw.textbbox((0, 0), data.subtitle, font=font_sub)
        cursor_y += (bbox[3] - bbox[1]) + 14

    # 评论（橙色大字，底部区域，文字过长时截断）
    if data.comment:
        font_cmt = _load_font(comment_size, bold=True)
        cmt = data.comment
        max_cmt_w = card_w - inner_padding * 2
        # 简单截断
        while font_cmt.getlength(cmt) > max_cmt_w and len(cmt) > 1:
            cmt = cmt[:-1]
        if cmt != data.comment:
            cmt = cmt[:-1] + "…"
        draw.text(
            (card_x0 + card_w // 2, card_y1 - inner_padding - comment_size // 2),
            cmt,
            font=font_cmt,
            fill=comment_color,
            anchor="mm",
        )

    return canvas.crop(canvas.getbbox() or (card_x0, card_y0, card_x1, card_y1))


# ---------------------------------------------------------------------------
# 条带拼接
# ---------------------------------------------------------------------------

def assemble_strip(
    card_images: list[Image.Image],
    *,
    gap: int = 24,
    side_padding: int = 80,
    bg_color: tuple[int, int, int] = (24, 24, 28),
) -> tuple[Image.Image, list[int]]:
    """把多张卡片横向拼接为一张超宽条带图。

    卡片按自身实际尺寸贴入（card_renderer 已包含阴影 alpha），水平居中对齐（按最大卡高）。
    返回 (strip_rgb_image, card_center_x_list)：
      - strip_rgb_image 是 RGB 模式（为了能被 pyJianYingDraft 正常读取，alpha 填 bg_color）
      - card_center_x_list 是每张卡片在 strip 中的中心 X 像素坐标（用于文字同步定位）
    """
    if not card_images:
        raise ValueError("至少需要一张卡片")

    # 卡片本身是 RGBA（带阴影），先确定条带尺寸
    max_h = max(im.height for im in card_images)
    total_w = side_padding * 2 + sum(im.width for im in card_images) + gap * (len(card_images) - 1)

    strip_rgba = Image.new("RGBA", (total_w, max_h), bg_color + (255,))

    x_cursor = side_padding
    centers: list[int] = []
    for im in card_images:
        y_offset = (max_h - im.height) // 2  # 垂直居中
        strip_rgba.alpha_composite(im, (x_cursor, y_offset))
        centers.append(x_cursor + im.width // 2)
        x_cursor += im.width + gap

    # 转 RGB（alpha 已经预乘在 bg_color 上了）
    strip_rgb = Image.new("RGB", strip_rgba.size, bg_color)
    strip_rgb.paste(strip_rgba, mask=strip_rgba.split()[3])
    return strip_rgb, centers


def render_background(
    canvas_w: int,
    canvas_h: int,
    bg_color: tuple[int, int, int] = (24, 24, 28),
) -> Image.Image:
    """生成一张纯色背景图（canvas_w × canvas_h，RGB）。"""
    return Image.new("RGB", (canvas_w, canvas_h), bg_color)


def save_strip(strip_img: Image.Image, out_dir: Path, name_hint: str = "carousel_strip") -> Path:
    """保存 strip 到 out_dir，文件名加时间戳避免覆盖。"""
    import time
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{name_hint}_{ts}.png"
    strip_img.save(out_path, "PNG")
    return out_path
