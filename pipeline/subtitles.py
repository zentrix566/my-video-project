"""
字幕样式预设库 — 把字号/颜色/描边/背景/阴影/位置打包成命名预设。

使用方式：
    from improvements.subtitle_styles import resolve_subtitle_preset

    preset = resolve_subtitle_preset("cinema", is_landscape=True)
    text_seg = draft.TextSegment(text, time_range, **preset.to_text_segment_kwargs())

每个预设都按横屏/竖屏分别给字号和位置；需要新增样式只要在 PRESETS 里加一项。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pyJianYingDraft as draft
from pyJianYingDraft import (
    ClipSettings, TextBackground, TextBorder, TextShadow, TextStyle,
)


@dataclass
class SubtitlePreset:
    """单条字幕的完整外观配置。

    `to_text_segment_kwargs()` 会输出可直接展开到 `draft.TextSegment(...)` 的关键字参数。
    """

    name: str
    description: str
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    size_landscape: float = 6.0
    size_portrait: float = 13.0
    y_landscape: float = -0.8
    y_portrait: float = -0.3
    align: int = 1  # 0 左 / 1 中 / 2 右
    bold: bool = False
    letter_spacing: int = 0
    max_line_width: float = 0.8
    auto_wrapping: bool = True

    # 外观（可选）
    border: dict | None = None       # {color: (r,g,b), alpha: float, width: float}
    background: dict | None = None   # {color: "#RRGGBB", alpha: float, round_radius: float, ...}
    shadow: dict | None = None       # {color: (r,g,b), alpha: float, distance: float, angle: float, diffuse: float}

    font_name: str = "文轩体"

    def font(self):
        return getattr(draft.FontType, self.font_name, draft.FontType.文轩体)

    def style(self, *, is_landscape: bool) -> TextStyle:
        return TextStyle(
            color=self.color,
            size=self.size_landscape if is_landscape else self.size_portrait,
            align=self.align,
            bold=self.bold,
            letter_spacing=self.letter_spacing,
            auto_wrapping=self.auto_wrapping,
            max_line_width=self.max_line_width,
        )

    def clip_settings(self, *, is_landscape: bool) -> ClipSettings:
        y = self.y_landscape if is_landscape else self.y_portrait
        return ClipSettings(transform_y=y)

    def to_text_segment_kwargs(self, *, is_landscape: bool) -> dict[str, Any]:
        """展开为 draft.TextSegment 构造器需要的关键字参数。"""
        kwargs: dict[str, Any] = {
            "font": self.font(),
            "style": self.style(is_landscape=is_landscape),
            "clip_settings": self.clip_settings(is_landscape=is_landscape),
        }
        if self.border:
            kwargs["border"] = TextBorder(
                color=self.border.get("color", (0.0, 0.0, 0.0)),
                alpha=self.border.get("alpha", 1.0),
                width=self.border.get("width", 40.0),
            )
        if self.background:
            kwargs["background"] = TextBackground(
                color=self.background.get("color", "#000000"),
                style=self.background.get("style", 1),
                alpha=self.background.get("alpha", 1.0),
                round_radius=self.background.get("round_radius", 0.0),
                height=self.background.get("height", 0.14),
                width=self.background.get("width", 0.14),
                horizontal_offset=self.background.get("horizontal_offset", 0.5),
                vertical_offset=self.background.get("vertical_offset", 0.5),
            )
        if self.shadow:
            kwargs["shadow"] = TextShadow(
                color=self.shadow.get("color", (0.0, 0.0, 0.0)),
                alpha=self.shadow.get("alpha", 1.0),
                diffuse=self.shadow.get("diffuse", 15.0),
                distance=self.shadow.get("distance", 5.0),
                angle=self.shadow.get("angle", -45.0),
            )
        return kwargs


# ============================================================
#  预设库
# ============================================================

PRESETS: dict[str, SubtitlePreset] = {
    "default": SubtitlePreset(
        name="default",
        description="默认 — 文轩体，纯白字，无描边",
    ),

    "cinema": SubtitlePreset(
        name="cinema",
        description="电影感 — 白字 + 半透明黑底，底部居中",
        color=(1.0, 1.0, 1.0),
        size_landscape=6.0,
        size_portrait=11.0,
        y_landscape=-0.85,
        y_portrait=-0.35,
        background={
            "color": "#000000",
            "alpha": 0.55,
            "round_radius": 0.06,
            "height": 0.18,
            "width": 0.10,
        },
    ),

    "comic": SubtitlePreset(
        name="comic",
        description="绘本风 — 亮黄字 + 黑色粗描边，顶部偏上",
        color=(1.0, 0.86, 0.20),
        bold=True,
        size_landscape=7.5,
        size_portrait=14.0,
        y_landscape=0.75,
        y_portrait=0.40,
        border={"color": (0.0, 0.0, 0.0), "width": 65.0, "alpha": 1.0},
        shadow={"color": (0.0, 0.0, 0.0), "alpha": 0.6, "distance": 6.0, "diffuse": 10.0, "angle": -45.0},
    ),

    "vlog": SubtitlePreset(
        name="vlog",
        description="Vlog — 大白字 + 柔和阴影，底部居中，无背景",
        color=(1.0, 1.0, 1.0),
        bold=True,
        size_landscape=8.0,
        size_portrait=15.0,
        y_landscape=-0.75,
        y_portrait=-0.30,
        shadow={"color": (0.0, 0.0, 0.0), "alpha": 0.7, "distance": 8.0, "diffuse": 18.0, "angle": -45.0},
    ),

    "classic": SubtitlePreset(
        name="classic",
        description="古风 — 米黄色字 + 浅墨阴影，文轩体，适合诗词",
        color=(0.96, 0.90, 0.74),
        size_landscape=7.0,
        size_portrait=13.5,
        y_landscape=-0.88,
        y_portrait=-0.30,
        letter_spacing=2,
        shadow={"color": (0.18, 0.10, 0.05), "alpha": 0.85, "distance": 5.0, "diffuse": 14.0, "angle": -45.0},
    ),
}


def list_preset_names() -> list[str]:
    return sorted(PRESETS.keys())


def list_presets_with_desc() -> list[tuple[str, str]]:
    return [(p.name, p.description) for p in PRESETS.values()]


def resolve_subtitle_preset(
    spec: str | Mapping[str, Any] | SubtitlePreset | None,
    *,
    fallback: str = "default",
) -> SubtitlePreset:
    """把任意「预设规格」归一化成 SubtitlePreset。

    - 传入 `None` 或空串 → 用 fallback
    - 传入字符串 → 在 PRESETS 里查
    - 传入 dict → 当作覆盖项叠在 fallback 之上（允许只改一两个字段）
    - 传入 SubtitlePreset → 原样返回

    未知名字会回退到 fallback，并保持调用者可继续运行。
    """
    if spec is None or spec == "":
        return PRESETS[fallback]
    if isinstance(spec, SubtitlePreset):
        return spec
    if isinstance(spec, str):
        return PRESETS.get(spec, PRESETS[fallback])
    if isinstance(spec, Mapping):
        base_name = spec.get("base", fallback)
        base = PRESETS.get(base_name, PRESETS[fallback])
        # 只浅复制要改动的字段
        merged = SubtitlePreset(**{**base.__dict__, **{k: v for k, v in spec.items() if k != "base"}})
        return merged
    raise TypeError(f"Unsupported subtitle preset spec: {type(spec).__name__}")
