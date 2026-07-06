"""Step 2 · make_narration_video.py 专用：视频元信息 + 逐帧描述 → N 段讲解稿 + 视频切段时间戳。

与 doc_narrator.py 的差别只有 **system prompt**：这里假设没有 PDF 背景，画面来自
屏幕录制（软件操作 / 产品演示 / 教程）；讲稿只依赖帧描述 + 用户 brief。

输入：
    - video_meta: {"duration_s": float, "width": int, "height": int, "fps": float, ...}
    - frame_captions: {"video_summary": str, "frames": [{"timestamp_s":..., "caption":...}]}
    - brief: 可选，用户对视频背景的一句话补充
    - scene_count: 目标切段数

输出（generated_scenes.json，结构与 doc_narrator 完全一致，让下游零改动）：
    {
      "title": "...",
      "subtitle": "...",
      "video_duration_s": float,
      "scenes": [ {"id": "01_intro", "narration": "...", "video_start_s": 0.0, "video_end_s": 6.5}, ... ]
    }
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.helpers import PipelineLogger, extract_json_object, retry
from pipeline.doc_narrator import _normalize_scenes, load_generated_scenes  # noqa: F401


SYSTEM_PROMPT_NARRATION = """你是一位擅长把「屏幕录制视频」变成「有配音的讲解片」的短视频编剧。

你现在收到：
- 一段屏幕录制视频的**基本元信息**（总时长 duration_s、分辨率）
- 从视频中均匀抽出的**逐帧描述**（每帧含时间戳与看到的 UI 元素、操作动作）
- 可能有的一句 brief（用户对视频背景的补充说明）
- 目标场景数 target_scene_count（每段大约 duration_s / target_scene_count 秒）

请为一部**给同事/学习者看的操作讲解视频**撰写完整旁白。视频结构：
    原始 mp4 会按你给的时间点切成 target_scene_count 段，一段配一句 AI 配音，原音已被剥离。

## 【极其重要】禁止脑补规则

抽帧是稀疏的——两帧之间可能相隔十几秒，中间发生的操作你完全看不到。**只允许描述你在帧描述里能查到的具体名字**（页面名、按钮名、控件名、工具名、术语名）。

- ✅ 允许写：帧描述里出现过的具体文字（例："进入'发票使用'模块，看到'发票用途确认'等入口"）
- ❌ 严禁写：帧描述里没提过的具体按钮名（例：帧描述都没提"填写申报表"，你就绝不能说"点击填写申报表按钮"）
- 如果某段时间窗内**没有任何帧**能提供具体信息，用**通用描述**兜底（例："切换到下一个模块"、"等待页面加载完成后继续下一步"、"接下来进入本流程的下一环节"），不要编造具体控件名。
- brief 里提到的产品/工具名可以直接引用（它是用户给你的可信信息）。

## 写作要求

- 恰好产出 target_scene_count 个 scene；scene 数量、id 序号、时间段都必须严格遵守。
- 每段 narration 用**中文**，字数**严格**满足两条约束：
  1. **下限**：`字数 ≥ (video_end_s - video_start_s) × 4.5 × 0.85`（填满 85% 段时长，避免讲完后画面还静静地播很久）
  2. **上限**：`字数 ≤ (video_end_s - video_start_s) × 4.5`（TTS 语速约 5 字/秒，留 10% 余量避免尾巴讲不完）
  举例：
    - 视频段 6 秒 → narration 目标区间 [23, 27] 字
    - 视频段 10 秒 → 目标 [38, 45] 字
    - 视频段 15 秒 → 目标 [57, 67] 字
    - 视频段 21 秒 → 目标 [80, 94] 字
  **长段（>12 秒）必须写长 narration**。如果内容不够填，可以多讲讲这一步的**业务意义 / 目的 / 注意事项**，或者把该操作放在整个流程里的位置说清楚，绝不能敷衍写 30 字。
- narration 目标读者：**第一次看到这段录屏的人**。既要说清楚屏幕上正在做什么操作（在**帧描述能支持的前提下**），也要点出这一步的**目的/意义**。目的/意义可以推理，具体控件名不能推理。
- **严格禁用**「大家好」「点赞关注」「接下来我们看」「下一段」「首先」「其次」「最后」等衔接/主播套话——每段之间靠画面自然衔接。
- 首段（scene 01）一句话点明：这段视频在演示**什么工具 / 什么流程**（用 brief + 首帧描述综合判断）。
- 末段用一句总结或落地建议收尾，不要突然结束。

## video_start_s / video_end_s 规则

- N 段大致均匀覆盖 [0, duration_s]，允许根据逐帧描述做小幅调整（把画面停留久或者操作密集的关键节点划成独立一段）。
- **强烈建议**每段视频时长在 **5-10 秒**之间。避免出现 > 12 秒的长段，否则要么把它切成两段，要么必须给这一段配**充足的 narration 字数**（见上面下限公式）。
- **强烈建议**每一段的时间窗都至少覆盖一张帧的时间戳。若某段横跨的时间窗里没有任何帧，本段 narration 必须走通用兜底话术。
- 相邻段必须**紧接**：第 i 段的 video_end_s = 第 i+1 段的 video_start_s。
- 第 1 段的 video_start_s 必须是 0.0；最后一段的 video_end_s 必须 ≥ duration_s - 0.5 且 ≤ duration_s。
- 每段最短 3 秒，最长不超过 15 秒。

## 输出格式

一个 JSON 对象，含且仅含 `title` / `subtitle` / `scenes` 三个顶层字段。scenes 是数组，长度 = target_scene_count。
每个 scene 含且仅含：`id`（形如 "01_overview"）/ `narration` / `video_start_s` / `video_end_s`。
时间戳是数字（float，单位秒），保留 1 位小数即可。

【JSON 严格格式要求 —— 极其重要】
- 只返回一个 JSON 对象，不要任何解释文字，不要 markdown 代码围栏。
- narration 内部若需要引号（例如引用界面文案），一律用中文「」或 " "，**绝对禁止**使用英文 " —— 会破坏 JSON。
- narration 内部禁止使用反斜杠转义序列（如 \\n \\"），保持纯文本单行。
"""


def generate_narration_scenes(
    api_key: str,
    video_meta: dict[str, Any],
    frame_captions: dict[str, Any],
    *,
    scene_count: int,
    brief: str = "",
    output_dir: Path,
    logger: PipelineLogger,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """调 LLM 生成 N 段讲稿 + 视频切段时间戳，落盘 generated_scenes.json。

    与 doc_narrator.generate_doc_scenes 的差别：
      - 不传 PDF 全文；只传 video 元信息 + 帧描述 + brief。
      - 用面向屏幕录制的 SYSTEM_PROMPT_NARRATION。
    """
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    duration_s = float(video_meta["duration_s"])

    # caption 全空（--no-vision）时给 LLM 一个降级提示，让它只按时间轴均匀切段
    frames_in = frame_captions.get("frames", [])
    has_captions = any((f or {}).get("caption") for f in frames_in)
    if not has_captions and frames_in:
        note = (
            "[视觉分析未启用，仅提供帧时间戳；请结合 brief 与视频总时长写讲稿，"
            "narration 少讲「屏幕上具体看到什么」，多讲这一步的目的与意义]"
        )
    else:
        note = ""

    user_payload = {
        "video_duration_s": round(duration_s, 2),
        "video_resolution": f"{video_meta.get('width', 0)}x{video_meta.get('height', 0)}",
        "video_summary": frame_captions.get("video_summary", ""),
        "frames": frames_in,
        "target_scene_count": scene_count,
        "brief": brief,
        "downgrade_hint": note,
    }

    logger.info(
        "step:narration_narrator.start",
        model=model,
        scene_count=scene_count,
        frame_count=len(frames_in),
        duration_s=round(duration_s, 2),
    )

    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        "narration_narrator.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
    ))
    def call_llm() -> dict:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT_NARRATION},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "temperature": 0.6,
                # 关掉思考模式：写结构化 JSON 讲稿不需要 chain-of-thought，
                # 否则 ark-code-latest 会把整个 4096 max_tokens 塞进 reasoning_content
                # 导致最终 content 为空、下游 JSON 解析报 "Expecting value"。
                "thinking": {"type": "disabled"},
                "max_tokens": 8192,
            },
            timeout=300,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    t0 = time.time()
    response_json = call_llm()
    elapsed = round(time.time() - t0, 2)
    logger.info("step:narration_narrator.done", elapsed_s=elapsed, model=model)

    (output_dir / "responses" / "narration_narrator.json").write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    content = response_json["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)

    scenes = parsed.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("LLM 未返回有效的 scenes 数组。")

    normalized = _normalize_scenes(scenes, duration_s, logger)

    payload = {
        "title": str(parsed.get("title") or "").strip() or "录屏讲解",
        "subtitle": str(parsed.get("subtitle") or "").strip(),
        "video_duration_s": round(duration_s, 3),
        "scenes": normalized,
    }
    (output_dir / "generated_scenes.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload
