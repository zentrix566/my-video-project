"""图片去水印工具
使用 OpenCV inpainting 算法，根据用户标注的矩形区域去除水印。
支持两种算法：
  - TELEA (默认): 速度快，适合小面积水印
  - NS (Navier-Stokes): 边缘衔接更自然，适合大面积或复杂背景
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import cv2
import numpy as np


InpaintMethod = Literal["telea", "ns"]


def remove_watermark(
    image: np.ndarray,
    regions: list[dict],
    method: InpaintMethod = "telea",
    radius: int = 5,
    padding: int = 3,
) -> np.ndarray:
    """去除图片中指定区域的水印

    Args:
        image: OpenCV BGR 格式图片（通过 cv2.imread 读取）
        regions: 水印区域列表，每个区域为 {"x": int, "y": int, "w": int, "h": int}
                 坐标基于原图尺寸
        method: 修复算法，"telea" 或 "ns"
        radius: inpainting 半径（像素），越大参考周围像素越多
        padding: 矩形区域向外扩展的像素，避免边缘残留

    Returns:
        修复后的 BGR 图片
    """
    h, w = image.shape[:2]

    # 创建 mask（单通道，白色=需要修复的区域）
    mask = np.zeros((h, w), dtype=np.uint8)

    for region in regions:
        x = int(region["x"])
        y = int(region["y"])
        rw = int(region["w"])
        rh = int(region["h"])

        # 添加 padding 并确保不越界
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(w, x + rw + padding)
        y2 = min(h, y + rh + padding)

        mask[y1:y2, x1:x2] = 255

    # 选择修复算法
    if method == "ns":
        flags = cv2.INPAINT_NS
    else:
        flags = cv2.INPAINT_TELEA

    # 执行修复
    result = cv2.inpaint(image, mask, radius, flags)
    return result


def remove_watermark_file(
    input_path: str | Path,
    output_path: str | Path,
    regions: list[dict],
    method: InpaintMethod = "telea",
    radius: int = 5,
    padding: int = 3,
) -> Path:
    """从文件读取图片，去水印后保存到文件

    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        regions: 水印区域列表
        method: 修复算法
        radius: inpainting 半径
        padding: 区域扩展像素

    Returns:
        输出文件路径
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    # 用 imdecode 支持中文路径
    img = cv2.imdecode(np.fromfile(str(input_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法读取图片: {input_path}")

    result = remove_watermark(img, regions, method, radius, padding)

    # 用 imencode 支持中文路径
    ext = output_path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        encode_param = [cv2.IMWRITE_JPEG_QUALITY, 95]
    elif ext == ".webp":
        encode_param = [cv2.IMWRITE_WEBP_QUALITY, 95]
    else:
        encode_param = [cv2.IMWRITE_PNG_COMPRESSION, 3]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    success, buf = cv2.imencode(ext, result, encode_param)
    if not success:
        raise RuntimeError("图片编码失败")
    buf.tofile(str(output_path))

    return output_path
