"""
故事流水线专用运镜效果 — 命名直观、针对图片优化。

与 CameraEffect（通用六种）的区别：
1. 命名从「观众视角」出发，一看就知道画面怎么动
2. 针对绘本/故事图片优化：缩放幅度更大、平移范围更宽
3. 新增对角线运镜，增加视觉丰富度
4. 支持 base_scale 参数：图片刚好铺满画布时的缩放比，所有运镜在此基数上叠加

运镜效果一览：
  ┌─────────────────────┬──────────────────────────────────┐
  │ 名称                 │ 画面效果                         │
  ├─────────────────────┼──────────────────────────────────┤
  │ zoom_in_full        │ 全图 → 逐步缩进到中心细节        │
  │ zoom_out_reveal     │ 中心细节 → 逐步展开到全图        │
  │ sweep_left_to_right │ 从左向右拉镜头                   │
  │ sweep_right_to_left │ 从右向左拉镜头                   │
  │ sweep_top_to_bottom │ 从上向下拉镜头                   │
  │ sweep_bottom_to_top │ 从下向上拉镜头                   │
  │ diagonal_tl_to_br   │ 从左上角拉到右下角               │
  │ diagonal_br_to_tl   │ 从右下角拉到左上角               │
  └─────────────────────┴──────────────────────────────────┘

base_scale 说明：
  base_scale = 图片刚好铺满画布时的 uniform_scale 值。
  计算方式：max(画布宽/图片宽, 画布高/图片高)
  所有运镜的缩放值 = base_scale × 倍数，保证任意时刻图片都覆盖画布。
"""

from __future__ import annotations

from pyJianYingDraft import KeyframeProperty

# 平移类运镜的恒定缩放倍数（相对 base_scale）
# 1.7 倍的好处：即使平移偏移 ±0.3，图片边缘仍超出画布
_PAN_MULT = 1.7

# 缩放类运镜的起始/结束倍数（相对 base_scale）
_ZOOM_START_MULT = 1.6
_ZOOM_END_MULT = 2.0

# 平移最大偏移量（归一化坐标，0.3 ≈ 画面 30% 的位移）
_PAN_OFFSET = 0.30


# ---- 工具函数 ----

def _cover(seg, duration: int, base_scale: float) -> None:
    """确保图片全程铺满画布：在 duration 内保持 scale ≥ base_scale。

    子类在调用 _cover 后，再叠加自己的位移/缩放关键帧即可。
    此处只做一件最基础的事：在 t=0 处写入 base_scale，
    防止 Builder 里的初始 1.0 关键帧导致瞬间露黑边。
    """
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, base_scale)


# ---- 缩放类 ----

def zoom_in_full(seg, duration: int, base_scale: float = 1.0) -> None:
    """全图逐步缩进 → 放大到中心细节

    观众先看到完整画面，然后镜头慢慢推进，聚焦到画面中心。
    适合：开场、展示全景后聚焦关键细节。
    """
    start = base_scale * _ZOOM_START_MULT
    end = base_scale * _ZOOM_END_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, start)
    seg.add_keyframe(KeyframeProperty.uniform_scale, time_offset=duration, value=end)
    for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
        seg.add_keyframe(prop, 0, 0)
        seg.add_keyframe(prop, duration, 0)


def zoom_out_reveal(seg, duration: int, base_scale: float = 1.0) -> None:
    """中心细节逐步展开 → 揭示全图

    观众先看到画面中心特写，然后镜头慢慢拉远，揭示完整场景。
    适合：悬念揭晓、从局部到全景的揭示。
    """
    start = base_scale * _ZOOM_END_MULT
    end = base_scale * _ZOOM_START_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, start)
    seg.add_keyframe(KeyframeProperty.uniform_scale, time_offset=duration, value=end)
    for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
        seg.add_keyframe(prop, 0, 0)
        seg.add_keyframe(prop, duration, 0)


# ---- 水平平移类 ----

def sweep_left_to_right(seg, duration: int, base_scale: float = 1.0) -> None:
    """从左向右拉镜头

    画面从左侧开始，镜头慢慢向右移动，展示完整宽度。
    适合：阅读顺序从左到右的场景、横向风景展示。
    """
    scale = base_scale * _PAN_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
    seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
    seg.add_keyframe(KeyframeProperty.position_x, 0, -_PAN_OFFSET)
    seg.add_keyframe(KeyframeProperty.position_x, duration, _PAN_OFFSET)
    seg.add_keyframe(KeyframeProperty.position_y, 0, 0)
    seg.add_keyframe(KeyframeProperty.position_y, duration, 0)


def sweep_right_to_left(seg, duration: int, base_scale: float = 1.0) -> None:
    """从右向左拉镜头

    画面从右侧开始，镜头慢慢向左移动。
    适合：回溯、回顾、与上一个左→右镜头形成呼应。
    """
    scale = base_scale * _PAN_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
    seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
    seg.add_keyframe(KeyframeProperty.position_x, 0, _PAN_OFFSET)
    seg.add_keyframe(KeyframeProperty.position_x, duration, -_PAN_OFFSET)
    seg.add_keyframe(KeyframeProperty.position_y, 0, 0)
    seg.add_keyframe(KeyframeProperty.position_y, duration, 0)


# ---- 垂直平移类 ----

def sweep_top_to_bottom(seg, duration: int, base_scale: float = 1.0) -> None:
    """从上向下拉镜头

    画面从顶部开始，镜头慢慢向下移动，展示完整高度。
    适合：展示高耸建筑、树木、从天空到地面。
    """
    scale = base_scale * _PAN_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
    seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
    seg.add_keyframe(KeyframeProperty.position_x, 0, 0)
    seg.add_keyframe(KeyframeProperty.position_x, duration, 0)
    seg.add_keyframe(KeyframeProperty.position_y, 0, -_PAN_OFFSET)
    seg.add_keyframe(KeyframeProperty.position_y, duration, _PAN_OFFSET)


def sweep_bottom_to_top(seg, duration: int, base_scale: float = 1.0) -> None:
    """从下向上拉镜头

    画面从底部开始，镜头慢慢向上移动。
    适合：仰望、从地面到天空、揭示高处细节。
    """
    scale = base_scale * _PAN_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
    seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
    seg.add_keyframe(KeyframeProperty.position_x, 0, 0)
    seg.add_keyframe(KeyframeProperty.position_x, duration, 0)
    seg.add_keyframe(KeyframeProperty.position_y, 0, _PAN_OFFSET)
    seg.add_keyframe(KeyframeProperty.position_y, duration, -_PAN_OFFSET)


# ---- 对角线运镜类 ----

def diagonal_tl_to_br(seg, duration: int, base_scale: float = 1.0) -> None:
    """从左上角拉到右下角

    画面从左上开始，镜头沿对角线向右下移动。
    适合：展现画面纵深、故事空间感。
    """
    scale = base_scale * _PAN_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
    seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
    seg.add_keyframe(KeyframeProperty.position_x, 0, -_PAN_OFFSET * 0.8)
    seg.add_keyframe(KeyframeProperty.position_x, duration, _PAN_OFFSET * 0.8)
    seg.add_keyframe(KeyframeProperty.position_y, 0, -_PAN_OFFSET * 0.6)
    seg.add_keyframe(KeyframeProperty.position_y, duration, _PAN_OFFSET * 0.6)


def diagonal_br_to_tl(seg, duration: int, base_scale: float = 1.0) -> None:
    """从右下角拉到左上角

    画面从右下开始，镜头沿对角线向左上移动。
    适合：与上一个对角线镜头形成呼应。
    """
    scale = base_scale * _PAN_MULT
    seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
    seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
    seg.add_keyframe(KeyframeProperty.position_x, 0, _PAN_OFFSET * 0.8)
    seg.add_keyframe(KeyframeProperty.position_x, duration, -_PAN_OFFSET * 0.8)
    seg.add_keyframe(KeyframeProperty.position_y, 0, _PAN_OFFSET * 0.6)
    seg.add_keyframe(KeyframeProperty.position_y, duration, -_PAN_OFFSET * 0.6)


# ---- 效果集 ----

STORY_CAMERA_ALL = [
    zoom_in_full,
    zoom_out_reveal,
    sweep_left_to_right,
    sweep_right_to_left,
    sweep_top_to_bottom,
    sweep_bottom_to_top,
    diagonal_tl_to_br,
    diagonal_br_to_tl,
]

STORY_CAMERA_PAN_ONLY = [
    sweep_left_to_right,
    sweep_right_to_left,
    sweep_top_to_bottom,
    sweep_bottom_to_top,
]

STORY_CAMERA_ZOOM_ONLY = [
    zoom_in_full,
    zoom_out_reveal,
]

STORY_CAMERA_MIXED = [
    zoom_in_full,
    zoom_out_reveal,
    sweep_left_to_right,
    sweep_right_to_left,
]

# 名称 → 效果列表 映射，方便从配置文件读取
CAMERA_PRESETS: dict[str, list] = {
    "all": STORY_CAMERA_ALL,
    "pan": STORY_CAMERA_PAN_ONLY,
    "zoom": STORY_CAMERA_ZOOM_ONLY,
    "mixed": STORY_CAMERA_MIXED,
}
