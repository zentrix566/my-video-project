"""Step 2 · make_doc_video.py 专用：PDF + 逐帧描述 → N 段讲解稿 + 视频切段时间戳 + 每段内逐句时间窗。

输入：doc_content.json（含 PDF 全文 + 视频总时长 + 可选镜头切换点）
      + frame_captions.json（含视频摘要 + 逐帧描述 + 可选镜头分组）+ 可选 brief
输出：generated_scenes.json，结构：
    {
      "title": "视频主标题（8-14 字）",
      "subtitle": "副标题 ≤ 20 字",
      "video_duration_s": float,
      "scenes": [
        {
          "id": "01_intro",
          "narration": "一段适合朗读的总旁白（15-25 字/段，随段长自适应）",
          "video_start_s": 0.0,
          "video_end_s":   4.2,
          "sentences": [
            {"text": "一句完整话", "start_s": 0.0, "end_s": 2.0},
            {"text": "另一句完整话", "start_s": 2.0, "end_s": 4.2}
          ]
        },
        ...
      ]
    }

sentences 为 v2 字段。若 LLM 未返回（旧调用/降级），下游会按标点 smart_split + 比例分配兜底。
下游 tts.py 吃 id + narration/sentences；video_cut.py 用 video_start_s / video_end_s 切段。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.helpers import (
    PipelineLogger, extract_json_object, retry, safe_slug, smart_split,
)


SYSTEM_PROMPT_DOC_NARRATOR = """你是一位专业的企业内训短视频编剧，擅长把「需求文档 + 操作演示视频」组合起来讲清楚一个业务流程。

你现在收到：
- 一份 PDF 需求文档的**完整正文**（业务定义、字段规则、流程约束）
- 从操作演示视频里抽出的**逐帧描述**（含每一帧的时间戳、看到的 UI 元素、与文档对应关系）
- 视频总时长 duration_s
- 目标场景数 target_scene_count（每段对应 duration_s / target_scene_count 秒的视频片段）
- **镜头切换点 scene_changes**（单位：秒，数组）：这是 ffmpeg 自动检测到的画面切变位置，**切段必须以镜头为硬边界**，禁止让一个 scene 的时间窗跨越镜头切换点。

请为一部**给同事/客户看的需求讲解视频**撰写完整旁白。视频结构：
    原始 mp4 会按你给的时间点切成 target_scene_count 段，一段对应一组旁白，配上 AI 配音。

## 写作要求

- 恰好产出 target_scene_count 个 scene；scene 数量、id 序号、时间段都必须严格遵守。
- 每段 narration 用**中文**，字数满足：
  - 下限：`字数 ≥ 段时长 × 4.5 × 0.8`（填满 80% 段时长）
  - 上限：`字数 ≤ 段时长 × 5.0`（按 TTS 约 5 字/秒，留余量避免讲不完）
  - 举例：4 秒 → [14, 20] 字；6 秒 → [22, 30] 字；10 秒 → [36, 50] 字。
- narration 目标读者：**没读过 PDF 的同事/客户**。既要说清楚屏幕上在做什么操作，又要点出这个操作背后的业务意义。
- **严格禁用**「大家好」「点赞关注」「接下来我们看」「下一段」「首先」「其次」「最后」等主播套话——每段之间靠画面自然衔接。
- 允许直接引用 PDF 里的关键术语/字段名/流程名，但不要照抄大段 PDF 原文。
- 首段点明视频要讲什么业务场景 + 涉及哪个系统模块；末段用一句总结/落地建议收尾。
- 每段 narration 必须是**完整的一句话或连续两三句短话**（便于拆 sentences）。

## video_start_s / video_end_s 规则

- 场景时间点必须落在 `scene_changes` 相邻两个镜头切换点之间，或者落在镜头切换点本身上：
  * 若提供了 scene_changes（非空数组），每个 scene 的 start/end 必须是 0 / duration_s / 镜头切换点 中的一个值（允许误差 ±0.2s），这样切段恰好在镜头切变处，画面过渡自然。
  * 若 scene_changes 为空数组，则按均匀分布 + 帧内容微调。
- 相邻段必须**紧接**：第 i 段的 video_end_s = 第 i+1 段的 video_start_s。
- 第 1 段的 video_start_s 必须是 0.0；最后一段的 video_end_s 必须 = duration_s。
- 每段最短 2.5 秒，最长 10 秒。段太短可以合并相邻同镜头内容，段太长按镜头再拆。

## sentences 字段规则（极其重要，决定字幕与语音同步）

每个 scene 内，把 narration 拆成 1-3 句短句，并给每句分配 [start_s, end_s]：
- sentences 数组内的时间必须严格连续递增，最后一句 end_s = video_end_s，第一句 start_s = video_start_s。
- 按句子字数比例分配时长：中文字数多的句多占时间，短的句少占时间（TTS 语速约 5 字/秒）。
- 一句话控制在 6-20 字之间；一句话超过 20 字就再拆成两句。
- 每句的 text 必须是 narration 的真实子串（不要改写、不要增删字），按 narration 里出现的顺序。
- 允许一句话刚好占满整个 scene（即 sentences 只有一条，start_s=video_start_s, end_s=video_end_s）。

## 输出格式

一个 JSON 对象，含且仅含 `title` / `subtitle` / `scenes` 三个顶层字段。scenes 是数组，长度 = target_scene_count。
每个 scene 含且仅含：`id`（形如 "01_overview"）/ `narration` / `video_start_s` / `video_end_s` / `sentences`。
其中 sentences 是数组，每项含 `text` / `start_s` / `end_s` 三个字段。
时间戳是数字（float，单位秒），保留 1 位小数即可。

【JSON 严格格式要求】
- 只返回一个 JSON 对象，不要任何解释文字，不要 markdown 代码围栏。
- narration/sentences[].text 内部若需要引号一律用中文「」，**绝对禁止**使用英文 "。
- 文本保持纯文本单行，禁止反斜杠转义序列。
"""


SYSTEM_PROMPT_NARRATION = """你是一位擅长把「屏幕录制视频」变成「有配音的讲解片」的短视频编剧。

你现在收到：
- 一段屏幕录制视频的**基本元信息**（总时长 duration_s、分辨率）
- 从视频中抽出的**逐帧描述**（每帧含时间戳与看到的 UI 元素、操作动作）
- 可能有的一句 brief（用户对视频背景的补充说明）
- 目标场景数 target_scene_count
- **镜头切换点 scene_changes**（单位：秒，数组）：这是 ffmpeg 自动检测到的画面切变位置，**切段必须以镜头为硬边界**，禁止让一个 scene 的时间窗跨越镜头切换点。

请为一部**给同事/学习者看的操作讲解视频**撰写完整旁白。原始 mp4 会按你给的时间点切成 target_scene_count 段，一段配一组 AI 配音。

## 【极其重要】禁止脑补规则

抽帧是稀疏的——两帧之间相隔数秒，中间发生的操作你看不到。**只允许描述帧描述里能查到的具体名字**。
- ✅ 可以写：帧描述里出现过的页面名、按钮名、控件名、术语名
- ❌ 严禁写：帧描述没提过的具体控件名/按钮名/菜单名
- 时间窗内帧描述信息不足时，用通用兜底描述（例："切换到下一个模块"、"等待页面加载完成后继续"），不编造
- brief 里提到的产品/工具名可以直接引用

## 写作要求

- 恰好产出 target_scene_count 个 scene。
- 每段 narration 用**中文**，字数满足：
  - 下限：`字数 ≥ 段时长 × 4.5 × 0.8`
  - 上限：`字数 ≤ 段时长 × 5.0`
  - 4 秒 → [14,20] 字；6 秒 → [22,30] 字；10 秒 → [36,50] 字。
- narration 目标读者是第一次看到这段录屏的人：既说清当前操作（在帧描述支持范围内），也点明这一步的目的/意义。
- **严格禁用**「大家好」「接下来我们看」「下一段」「首先」「其次」「最后」等主播套话。
- 首段点明视频在演示什么工具/流程；末段用一句总结收尾。
- 每段 narration 必须是完整的一句话或连续两三句短话。

## video_start_s / video_end_s 规则

- 场景时间点必须落在 0 / duration_s / scene_changes 相邻两个切换点之间（误差 ±0.2s）；若 scene_changes 为空则均匀分布。
- 相邻段紧接；第 1 段 start=0.0，最后一段 end=duration_s。
- 每段最短 2.5 秒，最长 10 秒。

## sentences 字段规则（极其重要，决定字幕与语音同步）

每个 scene 内把 narration 拆成 1-3 句短句，并给每句分配 [start_s, end_s]：
- sentences 时间严格连续递增，首句 start=video_start_s，末句 end=video_end_s。
- 按句子字数比例分配时长（TTS 约 5 字/秒）。
- 每句 6-20 字，超过 20 字再拆。
- 每句 text 必须是 narration 的真实子串，按出现顺序排列。

## 输出格式

一个 JSON 对象，含且仅含 `title` / `subtitle` / `scenes`。scenes 长度 = target_scene_count。
每个 scene 含且仅含：`id` / `narration` / `video_start_s` / `video_end_s` / `sentences`。
sentences 每项含 `text` / `start_s` / `end_s`。时间戳为 float（秒），保留 1 位小数。

【JSON 严格格式】
- 只返回一个 JSON 对象，不要解释文字、不要 markdown 围栏。
- narration 内引号一律用中文「」；禁止英文 "。
- 文本保持单行，禁止反斜杠转义。
"""


def _call_llm(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    logger: PipelineLogger,
    log_response_path: Path,
    step_name: str,
) -> dict[str, Any]:
    """统一的 LLM 调用 + retry + 落盘 + JSON 提取。"""
    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        f"{step_name}.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
    ))
    def _do() -> dict:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "temperature": 0.6,
                "thinking": {"type": "disabled"},
                "max_tokens": 8192,
            },
            timeout=300,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    t0 = time.time()
    response_json = _do()
    elapsed = round(time.time() - t0, 2)
    logger.info(f"step:{step_name}.done", elapsed_s=elapsed, model=model)

    (log_response_path.parent).mkdir(parents=True, exist_ok=True)
    log_response_path.write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return response_json


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
    scene_changes: list[float] | None = None,
) -> dict[str, Any]:
    """调 LLM 生成 N 段讲稿 + 切段时间戳 + 段内逐句时间窗，落盘 generated_scenes.json。"""
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    duration_s = float(doc_content["video"]["duration_s"])
    scene_changes = [float(x) for x in (scene_changes or doc_content.get("scene_changes") or [])]

    frames_in = frame_captions.get("frames", [])
    has_captions = any((f or {}).get("caption") for f in frames_in)
    note = ""
    if not has_captions and frames_in:
        note = (
            "[视觉分析未启用，仅提供帧时间戳；请依据 PDF 正文写讲稿，"
            "时间轴结合镜头切换点分配，narration 少提「屏幕上看到」而多讲业务含义]"
        )

    user_payload = {
        "pdf_full_text": doc_content["pdf"]["full_text"],
        "pdf_page_count": doc_content["pdf"]["page_count"],
        "video_duration_s": round(duration_s, 2),
        "video_summary": frame_captions.get("video_summary", ""),
        "frames": frames_in,
        "scene_changes": [round(x, 2) for x in scene_changes],
        "target_scene_count": scene_count,
        "brief": brief,
        "downgrade_hint": note,
    }

    logger.info(
        "step:doc_narrator.start",
        model=model,
        scene_count=scene_count,
        pdf_chars=doc_content["pdf"]["char_count"],
        frame_count=len(frames_in),
        scene_changes=len(scene_changes),
        duration_s=round(duration_s, 2),
    )

    response_json = _call_llm(
        api_key, base_url, model,
        SYSTEM_PROMPT_DOC_NARRATOR, user_payload,
        logger,
        output_dir / "responses" / "doc_narrator.json",
        "doc_narrator",
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
    scene_changes: list[float] | None = None,
) -> dict[str, Any]:
    """调 LLM 生成 N 段讲稿 + 时间戳（纯录屏讲解模式，无 PDF）。"""
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    duration_s = float(video_meta["duration_s"])
    scene_changes = [float(x) for x in (scene_changes or [])]

    frames_in = frame_captions.get("frames", [])
    has_captions = any((f or {}).get("caption") for f in frames_in)
    note = ""
    if not has_captions and frames_in:
        note = (
            "[视觉分析未启用，仅提供帧时间戳；请结合 brief 与时长写讲稿，"
            "narration 少讲「屏幕上具体看到什么」，多讲这一步的目的与意义]"
        )

    user_payload = {
        "video_duration_s": round(duration_s, 2),
        "video_resolution": f"{video_meta.get('width', 0)}x{video_meta.get('height', 0)}",
        "video_summary": frame_captions.get("video_summary", ""),
        "frames": frames_in,
        "scene_changes": [round(x, 2) for x in scene_changes],
        "target_scene_count": scene_count,
        "brief": brief,
        "downgrade_hint": note,
    }

    logger.info(
        "step:narration_narrator.start",
        model=model,
        scene_count=scene_count,
        frame_count=len(frames_in),
        scene_changes=len(scene_changes),
        duration_s=round(duration_s, 2),
    )

    response_json = _call_llm(
        api_key, base_url, model,
        SYSTEM_PROMPT_NARRATION, user_payload,
        logger,
        output_dir / "responses" / "narration_narrator.json",
        "narration_narrator",
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


def _split_fallback(narration: str, start_s: float, end_s: float) -> list[dict[str, Any]]:
    """LLM 没返回 sentences 时的兜底：smart_split 按标点拆句 + 按字数比例分配时长。"""
    sentences = [s.strip() for s in smart_split(narration) if s.strip()]
    if not sentences:
        sentences = [narration.strip()] if narration.strip() else [""]
    total_len = max(sum(len(s) for s in sentences), 1)
    dur = end_s - start_s
    t = start_s
    out: list[dict[str, Any]] = []
    for i, s in enumerate(sentences):
        ratio = len(s) / total_len
        if i == len(sentences) - 1:
            s_end = end_s
        else:
            s_end = round(t + dur * ratio, 3)
        out.append({
            "text": s,
            "start_s": round(t, 3),
            "end_s": round(s_end, 3),
        })
        t = s_end
    return out


def _normalize_scenes(
    scenes: list[dict[str, Any]],
    duration_s: float,
    logger: PipelineLogger,
) -> list[dict[str, Any]]:
    """校验 + 规范化 LLM 输出（id 规范、时间戳修补、相邻紧接、sentences 对齐）。"""
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

        try:
            v_start = float(scene.get("video_start_s"))
        except (TypeError, ValueError):
            v_start = prev_end
        try:
            v_end = float(scene.get("video_end_s"))
        except (TypeError, ValueError):
            v_end = min(prev_end + duration_s / len(scenes), duration_s)

        if index == 1:
            v_start = 0.0
        else:
            v_start = round(prev_end, 3)

        if index == len(scenes):
            v_end = round(duration_s, 3)

        if v_end <= v_start + 0.5:
            v_end = min(v_start + max(duration_s / len(scenes), 1.0), duration_s)

        v_start = round(max(0.0, v_start), 3)
        v_end = round(min(duration_s, v_end), 3)
        if v_end <= v_start:
            logger.warn("doc_narrator.time_range_fallback", scene_id=raw_id, v_start=v_start, v_end=v_end)
            v_end = min(duration_s, v_start + 1.0)

        # ---- sentences 规范化 ----
        raw_sentences = scene.get("sentences")
        sentences_out: list[dict[str, Any]]
        if isinstance(raw_sentences, list) and raw_sentences:
            # 解析 LLM 给的 sentences，做时间轴归一化
            items: list[tuple[str, float, float]] = []
            for item in raw_sentences:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                try:
                    ss = float(item.get("start_s"))
                    ee = float(item.get("end_s"))
                except (TypeError, ValueError):
                    ss = ee = -1.0
                items.append((text, ss, ee))
            if not items:
                sentences_out = _split_fallback(narration, v_start, v_end)
            else:
                # 时间可能不严谨：按 LLM 给的比例把句子压缩/拉伸到 [v_start, v_end]
                try:
                    rel_pos = []
                    total_declared = items[-1][2] - items[0][1]
                    if total_declared <= 0:
                        raise ValueError
                    offset = items[0][1]
                    for text, s, e in items:
                        # 相对比例
                        rs = max(0.0, (s - offset) / total_declared) if total_declared > 0 else 0
                        re = max(0.0, (e - offset) / total_declared) if total_declared > 0 else 1
                        rel_pos.append((text, rs, re))
                    dur = v_end - v_start
                    out: list[dict[str, Any]] = []
                    cur = v_start
                    for i, (text, rs, re) in enumerate(rel_pos):
                        if i == len(rel_pos) - 1:
                            s_end = v_end
                        else:
                            s_end = round(v_start + dur * re, 3)
                        s_start = round(cur, 3)
                        out.append({
                            "text": text,
                            "start_s": s_start,
                            "end_s": s_end,
                        })
                        cur = s_end
                    sentences_out = out
                except Exception:
                    sentences_out = _split_fallback(narration, v_start, v_end)
        else:
            # LLM 没给 sentences：兜底按标点拆
            sentences_out = _split_fallback(narration, v_start, v_end)

        result.append({
            "id": raw_id,
            "narration": narration,
            "video_start_s": v_start,
            "video_end_s": v_end,
            "sentences": sentences_out,
        })
        prev_end = v_end

    return result


def load_generated_scenes(path: Path) -> dict[str, Any]:
    """读取 generated_scenes.json（--skip-llm 用）。兼容旧版本无 sentences 字段。"""
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}；不能 --skip-llm。请先跑一次不带 --skip-llm 的完整命令。"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    # 旧 scenes 没有 sentences：现场补一份兜底 split
    for s in data.get("scenes", []):
        if "sentences" not in s or not isinstance(s.get("sentences"), list):
            s["sentences"] = _split_fallback(
                str(s.get("narration") or ""),
                float(s.get("video_start_s", 0.0)),
                float(s.get("video_end_s", 0.0)),
            )
    return data
