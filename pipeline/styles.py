"""风格预设加载器 —— 从 configs/styles/*.json 里读取「一整套外观 + 参数」。

一个风格预设决定了：
  * 画布尺寸 (canvas_width / canvas_height)
  * 图片模型/尺寸 (image.model / image.size / prompt_suffix / target_aspect_ratio)
  * TTS 音色 (tts.speaker / resource_id / format / sample_rate)
  * 剪映装配的运镜/字幕/转场 (jianying.camera_preset / subtitle_preset / …)

CLI 只需 --style 名字即可切换。预设 JSON 里所有字段都是可选的，
未提供的字段回落到 helpers.load_env 里的环境变量默认值。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STYLES_DIR = ROOT / "configs" / "styles"


def list_style_names() -> list[str]:
    if not STYLES_DIR.exists():
        return []
    return sorted(p.stem for p in STYLES_DIR.glob("*.json"))


def load_style(name: str) -> dict[str, Any]:
    """按名字加载风格预设 JSON。找不到时抛 SystemExit 并列出可用值。"""
    path = STYLES_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(list_style_names()) or "(none)"
        raise SystemExit(
            f"风格预设 '{name}' 不存在（{path}）。可选：{available}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("_name", name)
    return data
