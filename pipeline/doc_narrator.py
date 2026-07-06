"""Step 2 · make_doc_video.py 专用：PDF + 逐帧描述 → N 段讲解稿 + 视频切段时间戳。

参考 pipeline/walk_narrator.py 的调用范式（requests + Bearer + retry + extract_json_object）。
输入：doc_content.json（含 PDF 全文 + 视频总时长）+ frame_captions.json（含视频摘要 + 逐帧描述）+ 可选 brief
输出：generated_scenes.json，结构：
    {
      "title": "视频主标题（8-14 字）",
      "subtitle": "副标题 ≤ 20 字",
      "scenes": [
        {
          "id": "01_intro",
          "narration": "适合朗读的一句话（25-35 字）",
          "video_start_s": 0.0,
          "video_end_s":   6.5
        },
        ...
      ]
    }

下游 tts.py 只吃 id + narration；video_cut.py 用 video_start_s / video_end_s 切段。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.helpers import (
    PipelineLogger, extract_json_object, retry, safe_slug,
)


SYSTEM_PROMPT_DOC_NARRATOR = """你是一位专业的企业内训短视频编剧，擅长把「需求文档 + 操作演示视频」组合起来讲清楚一个业务流程。

你现在收到：
- 一份 PDF 需求文档的**完整正文**（业务定义、字段规则、流程约束）
- 从操作演示视频里均匀抽出的**逐帧描述**（含每一帧的时间戳、看到的 UI 元素、与文档对应关系）
- 视频总时长 duration_s
- 目标场景数 target_scene_count（每段大约对应 duration_s / target_scene_count 秒的视频片段）

请为一部**给同事/客户看的需求讲解视频**撰写完整旁白。视频结构：
    原始 mp4 会按你给的时间点切成 target_scene_count 段，一段对应一句旁白，配上 AI 配音。

## 写作要求

- 恰好产出 target_scene_count 个 scene；scene 数量、id 序号、时间段都必须严格遵守。
- 每段 narration 用**中文**，字数**严格**满足两条约束：
  1. **下限**：`字数 ≥ (video_end_s - video_start_s) × 4.5 × 0.85`（填满 85% 段时长，避免讲完后画面还静静地播很久）
  2. **上限**：`字数 ≤ (video_end_s - video_start_s) × 4.5`（TTS 语速约 5 字/秒，留 10% 余量避免尾巴讲不完）
  举例：视频段 6 秒 → 目标 [23, 27] 字；10 秒 → [38, 45] 字；15 秒 → [57, 67] 字。
  **长段（>12 秒）必须写长 narration**，可以多讲这一步涉及的**PDF 术语 / 业务字段 / 流程约束**，绝不能敷衍写 30 字。
- narration 目标读者：**没读过 PDF 的同事/客户**。既要说清楚屏幕上在做什么操作，又要点出这个操作背后的业务意义（比如「同步销项发票是为了让开票系统和申报表口径一致，避免申报后出现差异」）。
- **严格禁用**「大家好」「点赞关注」「接下来我们看」「下一段」「首先」「其次」「最后」这类衔接/主播套话——每段之间靠视频自然衔接。
- 允许直接引用 PDF 里的**关键术语/字段名/流程名**（如「销项发票核对」「未开票收入」「留抵税额」等），但不要照抄大段 PDF 原文。
- 首段（scene 01）用一句话点明视频要讲什么业务场景 + 涉及哪个系统模块。
- 末段用一句总结/落地建议收尾，不要突然结束。

## video_start_s / video_end_s 规则

- 8 段应大致均匀覆盖 [0, duration_s]，允许根据逐帧描述做小幅调整（比如把画面停留久的重要帧划分为独立一段）。
- 相邻段之间必须**紧接**：第 i 段的 video_end_s = 第 i+1 段的 video_start_s。
- 第 1 段的 video_start_s 必须是 0.0；最后一段的 video_end_s 必须 ≥ duration_s - 0.5 且 ≤ duration_s。
- 每段最短 3 秒，最长不超过 15 秒。

## 输出格式

一个 JSON 对象，含且仅含 `title` / `subtitle` / `scenes` 三个顶层字段。scenes 是数组，长度 = target_scene_count。
每个 scene 含且仅含：`id`（形如 "01_overview"）/ `narration` / `video_start_s` / `video_end_s`。
时间戳是数字（float，单位秒），保留 1 位小数即可。

【JSON 严格格式要求 —— 极其重要】
- 只返回一个 JSON 对象，不要任何解释文字，不要 markdown 代码围栏。
- narration 内部若需要引号（例如引用 PDF 术语/字段），一律用中文「」或 " "，**绝对禁止**使用英文 " —— 会破坏 JSON。
- narration 内部禁止使用反斜杠转义序列（如 \\n \\"），保持纯文本单行。
"""


def generate_doc_scenes(
    api_key: str,
    doc_content: dict[str, Any],
    frame_captions: dict[str, Any],
    *,
    scene_count: int,
    brief: str = "",
    output_dir: Path,
    logger: PipelineLogger,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """调 LLM 生成 N 段讲稿 + 视频切段时间戳，落盘 generated_scenes.json。"""
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    duration_s = float(doc_content["video"]["duration_s"])

    # 若 caption 全为空（--no-vision 模式），给 LLM 明确降级信号，让它按 PDF + 时间戳盲讲
    frames_in = frame_captions.get("frames", [])
    has_captions = any((f or {}).get("caption") for f in frames_in)
    if not has_captions and frames_in:
        note = (
            "[视觉分析未启用，仅提供帧时间戳；请依据 PDF 完整正文写讲稿，"
            "在时间轴上均匀分布，narration 少提「屏幕上看到」而多讲业务含义]"
        )
    else:
        note = ""

    user_payload = {
        "pdf_full_text": doc_content["pdf"]["full_text"],
        "pdf_page_count": doc_content["pdf"]["page_count"],
        "video_duration_s": round(duration_s, 2),
        "video_summary": frame_captions.get("video_summary", ""),
        "frames": frames_in,
        "target_scene_count": scene_count,
        "brief": brief,
        "downgrade_hint": note,
    }

    logger.info(
        "step:doc_narrator.start",
        model=model,
        scene_count=scene_count,
        pdf_chars=doc_content["pdf"]["char_count"],
        frame_count=len(user_payload["frames"]),
        duration_s=round(duration_s, 2),
    )

    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        "doc_narrator.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
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
                    {"role": "system", "content": SYSTEM_PROMPT_DOC_NARRATOR},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "temperature": 0.6,
                # 关掉思考模式 + 抬高 max_tokens：写结构化 JSON 讲稿不需要 chain-of-thought，
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
    logger.info("step:doc_narrator.done", elapsed_s=elapsed, model=model)

    (output_dir / "responses" / "doc_narrator.json").write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    content = response_json["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)

    scenes = parsed.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("LLM 未返回有效的 scenes 数组。")

    normalized = _normalize_scenes(scenes, duration_s, logger)

    payload = {
        "title": str(parsed.get("title") or "").strip() or "需求文档讲解",
        "subtitle": str(parsed.get("subtitle") or "").strip(),
        "video_duration_s": round(duration_s, 3),
        "scenes": normalized,
    }
    (output_dir / "generated_scenes.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _normalize_scenes(
    scenes: list[dict[str, Any]],
    duration_s: float,
    logger: PipelineLogger,
) -> list[dict[str, Any]]:
    """校验 + 规范化 LLM 输出的 scenes（id 规范、时间戳修补、相邻紧接）。"""
    result: list[dict[str, Any]] = []
    prev_end = 0.0
    for index, scene in enumerate(scenes, start=1):
        narration = str(scene.get("narration") or "").strip()
        if not narration:
            raise SystemExit(f"场景 {index} 缺少 narration。")

        raw_id = str(scene.get("id") or "").strip()
        if not raw_id or not raw_id[:2].isdigit():
            raw_id = f"{index:02d}_scene"
        else:
            tail = raw_id[3:] if len(raw_id) > 3 else "scene"
            raw_id = f"{raw_id[:2]}_{safe_slug(tail, fallback='scene')}"

        # 时间戳兜底：LLM 偶尔会打乱顺序或漏字段
        try:
            v_start = float(scene.get("video_start_s"))
        except (TypeError, ValueError):
            v_start = prev_end
        try:
            v_end = float(scene.get("video_end_s"))
        except (TypeError, ValueError):
            v_end = min(prev_end + duration_s / len(scenes), duration_s)

        # 首段强制 0；每段 start 与上一段 end 对齐
        if index == 1:
            v_start = 0.0
        else:
            v_start = round(prev_end, 3)

        # 末段强制铺满
        if index == len(scenes):
            v_end = round(duration_s, 3)

        # 单段最短 1s 兜底
        if v_end <= v_start + 0.5:
            v_end = min(v_start + max(duration_s / len(scenes), 1.0), duration_s)

        v_start = round(max(0.0, v_start), 3)
        v_end = round(min(duration_s, v_end), 3)
        if v_end <= v_start:
            logger.warn(
                "doc_narrator.time_range_fallback",
                scene_id=raw_id, v_start=v_start, v_end=v_end,
            )
            v_end = min(duration_s, v_start + 1.0)

        result.append({
            "id": raw_id,
            "narration": narration,
            "video_start_s": v_start,
            "video_end_s": v_end,
        })
        prev_end = v_end

    return result


def load_generated_scenes(path: Path) -> dict[str, Any]:
    """读取 generated_scenes.json（--skip-llm 用）。"""
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}；不能 --skip-llm。请先跑一次不带 --skip-llm 的完整命令。"
        )
    return json.loads(path.read_text(encoding="utf-8"))
