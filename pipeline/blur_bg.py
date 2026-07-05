"""预生成"画布尺寸的模糊背景图"，用于替代 contain 模式下的黑边。

用法：
    from pipeline.blur_bg import get_blurred_bg
    bg_path = get_blurred_bg(source_image, canvas=(1080, 1080))
    # 之后把 bg_path 作为背景视频轨的素材加入剪映草稿

缓存策略：
    outputs/blur_cache/<hash>.jpg
    hash = md5(source_absolute_path | canvas_w x canvas_h | source_mtime)
    源图更新会自动失效重建；同一源图不同画布共存不同缓存。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageFilter


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / "outputs" / "blur_cache"

# 高斯模糊半径。半径越大越糊，30 大约相当于抖音那种"看不出主体、只留色块氛围"的效果
DEFAULT_BLUR_RADIUS = 30
# 稍作暗化，让主图更"跳"；1.0 = 不变，0.7 = 稍暗
DEFAULT_DARKEN = 0.75


def _cache_key(src: Path, canvas: tuple[int, int]) -> str:
    src = src.resolve()
    mtime = src.stat().st_mtime_ns
    raw = f"{src}|{canvas[0]}x{canvas[1]}|{mtime}|r{DEFAULT_BLUR_RADIUS}|d{DEFAULT_DARKEN}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _cover_crop(im: Image.Image, canvas: tuple[int, int]) -> Image.Image:
    """把图放大到能覆盖 canvas，再从中心裁剪到 canvas 尺寸。"""
    cw, ch = canvas
    iw, ih = im.size
    if iw <= 0 or ih <= 0:
        raise ValueError(f"bad image size {iw}x{ih}")
    scale = max(cw / iw, ch / ih)
    new_size = (max(1, int(round(iw * scale))), max(1, int(round(ih * scale))))
    resized = im.resize(new_size, Image.LANCZOS)
    left = (resized.width - cw) // 2
    top = (resized.height - ch) // 2
    return resized.crop((left, top, left + cw, top + ch))


def get_blurred_bg(
    src: Path | str,
    canvas: tuple[int, int],
    *,
    cache_dir: Path | None = None,
    blur_radius: float = DEFAULT_BLUR_RADIUS,
    darken: float = DEFAULT_DARKEN,
) -> Path:
    """返回一份"canvas 尺寸的模糊背景图"路径。已缓存则直接命中。

    Args:
        src:          源图路径
        canvas:       (canvas_width, canvas_height)
        cache_dir:    缓存目录（默认 outputs/blur_cache/）
        blur_radius:  高斯模糊半径（默认 30）
        darken:       乘性亮度系数，1.0 不变，<1 变暗；默认 0.75
    """
    src = Path(src).resolve()
    cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    key = _cache_key(src, canvas)
    dst = cache_dir / f"{key}.jpg"
    if dst.exists():
        return dst

    with Image.open(src) as im:
        im = im.convert("RGB")
        covered = _cover_crop(im, canvas)
        blurred = covered.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        if darken != 1.0:
            # 用 Image.eval 逐像素乘系数
            blurred = Image.eval(blurred, lambda px: int(px * darken))
        blurred.save(dst, "JPEG", quality=85, optimize=True)

    return dst
