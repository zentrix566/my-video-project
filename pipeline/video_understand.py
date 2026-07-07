"""Step 1 · 视觉理解：把抽出的帧交给视觉大模型，得到逐帧描述。

支持两种调用方式：
- 旧模式（单组调用）：把所有帧一次性送模型，适合帧少（≤8）或模型能处理多图。
- 分组模式（新默认，group_by_scene=True）：按 is_scene_change 标记把帧分组成"同镜头"
  小组，每组最多 max_group_size 张，组内多图一起送 VLM 让它理解上下文连续变化。

两种模式输出 schema 相同，都是：
```
{
  "video_summary": "...",
  "frames": [
    {"frame_index", "timestamp_s", "caption", "key_ui_element", "linked_doc_section"}
  ],
  "groups": [{"start_idx", "end_idx", "summary"}]   # 分组模式下新增
}
```
其中 timestamp_s 由本模块从 doc_content.frames[].timestamp_s 补齐。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pipeline.helpers import (
    PipelineLogger, extract_json_object, vision_chat,
)


# 多帧成组模式的 prompt —— 每组最多 max_group_size 张连续帧
SYSTEM_PROMPT_GROUP = """你是一位资深的视频内容分析师。用户会给你一段上下文（PDF 摘录或用户提供的视频简介），以及从**同一段连续镜头**里按时间顺序抽取的 N 张截图（第 1 张最早，第 N 张最晚）。

## 任务
请分析这一组截图，结合上下文，输出 JSON：
```
{
  "group_summary": "一句话（20-40 字）概括这组画面整体在演示什么",
  "frames": [
    {
      "frame_offset": 0,
      "caption": "简明中文描述这一帧屏幕上在做什么（20-50 字），写清具体按钮名/表单字段名/页面标题等可见元素",
      "key_ui_element": "本帧最关键的一个 UI 元素或动作名（≤15 字，如：点击「同步销项发票」、查看差异表）",
      "linked_doc_section": "这一帧对应上下文里的哪一步/哪一节（≤20 字，无明显对应写「无」）"
    }
  ]
}
```

## 规则
- `frames` 数组长度**必须等于输入图片数 N**，frame_offset 从 0 到 N-1 严格递增。
- caption 只描述屏幕上真实可见的内容，不要臆测未展示的功能。
- 若某帧是黑屏/加载中/过渡帧，caption 如实写「加载中过渡帧」。
- 结合前后帧判断动作走向：比如前一帧是按钮未点击、后一帧是弹窗打开，就可以说"点击按钮打开弹窗"。
- **只返回 JSON 对象，不要 markdown 代码围栏、不要任何解释性文字。**
- caption/key_ui_element 内的引号一律用中文「」或转义 \"，避免破坏 JSON。
"""


# 旧单组模式的 prompt（帧少或回退时用）
SYSTEM_PROMPT_FLAT = """你是一位资深的{role}。用户会给你一段上下文（{context_label}），以及从一段视频里按时间顺序抽取的若干张截图。请**结合上下文**逐帧分析截图，说明每一帧屏幕上正在做什么、涉及上下文里的哪一步。

## 输入
- context: {context_label}
- 图片列表：按时间顺序抽出的 N 张截图（第 0 张最靠前，第 N-1 张最靠后）

## 输出（一个 JSON 对象，仅含以下顶层字段）
```
{{
  "video_summary": "一句话（30-60 字）概括整段视频演示的核心流程",
  "frames": [
    {{
      "frame_index": 0,
      "caption": "简明中文描述这一帧屏幕上在做什么，含具体按钮名/表单字段名/页面标题等可辨认的界面元素（20-50 字）",
      "key_ui_element": "本帧最关键的一个 UI 元素或动作名（≤ 15 字）",
      "linked_doc_section": "本帧对应上下文里的哪一步/哪一节（≤ 20 字，无法对应写「无」）"
    }}
  ]
}}
```

## 规则
- frames 数组长度**必须等于输入图片数量 N**，frame_index 从 0 开始严格递增。
- caption 只描述屏幕上真实可见的内容，不要臆测未展示的功能。
- 若某帧是黑屏/加载中/过渡帧，如实写「加载中过渡帧」。
- **只返回 JSON 对象，不要 markdown 代码围栏、不要任何解释性文字。**
- caption 内的引号一律用中文「」或转义 \"，避免破坏 JSON。
"""


def _build_groups(
    frame_paths: list[Path],
    frame_timestamps: list[float],
    is_scene_change: list[bool] | None,
    max_group_size: int,
) -> list[dict[str, Any]]:
    """按 is_scene_change 把帧分成同镜头组；超过 max_group_size 则再平分。

    返回 groups 列表，每项 {"start_idx", "end_idx"（不含）, "paths", "timestamps"}。
    """
    n = len(frame_paths)
    if not is_scene_change or len(is_scene_change) != n:
        is_scene_change = [True] + [False] * (n - 1)

    # 先按镜头边界切
    raw: list[list[int]] = []
    cur: list[int] = []
    for i, sc in enumerate(is_scene_change):
        if sc and cur:
            raw.append(cur)
            cur = []
        cur.append(i)
    if cur:
        raw.append(cur)

    # 再按 max_group_size 平分过长组
    groups: list[dict[str, Any]] = []
    for idxs in raw:
        if len(idxs) <= max_group_size:
            chunks = [idxs]
        else:
            import math
            chunk_size = max(1, math.ceil(len(idxs) / math.ceil(len(idxs) / max_group_size)))
            chunks = [idxs[i:i + chunk_size] for i in range(0, len(idxs), chunk_size)]
        for ch in chunks:
            groups.append({
                "start_idx": ch[0],
                "end_idx": ch[-1] + 1,
                "paths": [frame_paths[i] for i in ch],
                "timestamps": [frame_timestamps[i] for i in ch],
            })
    return groups


def _call_vlm(
    paths: list[Path],
    prompt: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    logger: PipelineLogger,
    temperature: float = 0.4,
) -> str:
    """调一次 VLM，返回原始文本。"""
    return vision_chat(
        paths, prompt,
        api_key=api_key,
        logger=logger,
        base_url=base_url,
        model=model,
        temperature=temperature,
    )


def caption_frames(
    api_key: str | None,
    frame_paths: list[Path],
    frame_timestamps: list[float],
    pdf_excerpt: str,
    *,
    output_dir: Path,
    logger: PipelineLogger,
    base_url: str | None = None,
    model: str | None = None,
    pdf_excerpt_chars: int = 3000,
    is_scene_change: list[bool] | None = None,
    max_group_size: int = 6,
    context_label: str = "PDF 摘录",
    role_label: str = "软件产品分析师",
) -> dict[str, Any]:
    """调视觉大模型描述所有抽出的帧，落盘 frame_captions.json 后返回 dict。

    api_key / base_url / model 都可为 None：vision_chat 会依次尝试
    VISION_API_KEY / VISION_BASE_URL / VISION_MODEL 环境变量，缺失时再回落到
    AGENT_API_KEY / PROMPT_BASE_URL / 默认模型名。

    is_scene_change: 与 frame_paths 等长的 bool 列表，True 表示这是镜头切换首帧。
                     提供则按镜头分组识别；None/长度不符则回退到单组模式。
    max_group_size:  同组最多几张图，默认 6。
    context_label:   prompt 里怎么称呼上下文（PDF 摘录/视频简介 等）。
    role_label:      prompt 里 AI 的角色。
    """
    if len(frame_paths) != len(frame_timestamps):
        raise SystemExit(
            f"frame_paths 和 frame_timestamps 长度不一致："
            f"{len(frame_paths)} vs {len(frame_timestamps)}"
        )
    if not frame_paths:
        raise SystemExit("frame_paths 为空，无法调用视觉模型。")

    excerpt = (pdf_excerpt or "").strip()
    if len(excerpt) > pdf_excerpt_chars:
        excerpt = excerpt[:pdf_excerpt_chars] + \
            "\n\n[... 上下文后续内容省略，用于视觉分析已足够 ...]"

    (output_dir / "responses").mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    all_raw_texts: list[str] = []
    normalized: list[dict[str, Any]] = []
    groups_meta: list[dict[str, Any]] = []

    # 决定是否分组：帧多或提供了 is_scene_change 就走分组
    use_grouping = bool(
        is_scene_change and len(is_scene_change) == len(frame_paths)
        and any(is_scene_change[1:])  # 至少有一个非首帧的切换标记
    ) or len(frame_paths) > max_group_size

    if use_grouping:
        groups = _build_groups(
            frame_paths, frame_timestamps, is_scene_change, max_group_size,
        )
        logger.info(
            "step:video_understand.grouped",
            frame_count=len(frame_paths),
            group_count=len(groups),
        )

        for gi, g in enumerate(groups):
            # ---- 断点续跑：如果 group 响应已存在且合法，跳过 ----
            cache_path = output_dir / "responses" / f"video_understand_group{gi:02d}.txt"
            cached_raw = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
            cached_valid = False
            if cached_raw.strip():
                try:
                    cached_parsed = extract_json_object(cached_raw)
                    cframes = cached_parsed.get("frames", [])
                    if len(cframes) == len(g["paths"]):
                        cached_valid = True
                        logger.info(
                            "step:video_understand.group_cache",
                            group_index=gi, hint="cached, skip",
                        )
                except Exception:
                    pass  # cache 损坏或格式不对，重跑

            if cached_valid:
                # 复用缓存数据组装 normalized
                cached_parsed = extract_json_object(cached_raw)
                cframes = cached_parsed.get("frames", [])
                group_summary = str(cached_parsed.get("group_summary") or "").strip()
                for k in range(len(g["paths"])):
                    global_i = g["start_idx"] + k
                    item = cframes[k] if k < len(cframes) and isinstance(cframes[k], dict) else {}
                    normalized.append({
                        "frame_index": global_i,
                        "timestamp_s": round(float(frame_timestamps[global_i]), 3),
                        "caption": str(item.get("caption") or "").strip(),
                        "key_ui_element": str(item.get("key_ui_element") or "").strip(),
                        "linked_doc_section": str(item.get("linked_doc_section") or "").strip(),
                    })
                groups_meta.append({
                    "group_index": gi,
                    "start_idx": g["start_idx"],
                    "end_idx": g["end_idx"],
                    "start_s": round(g["timestamps"][0], 3),
                    "end_s": round(g["timestamps"][-1], 3),
                    "summary": group_summary,
                })
                continue  # 跳过 VLM 调用

            prompt = (
                SYSTEM_PROMPT_GROUP
                + f"\n\n---\n\n{context_label}:\n" + excerpt
                + f"\n\n---\n\n本组共 {len(g['paths'])} 张图片，是视频第 "
                + f"{gi + 1}/{len(groups)} 组连续镜头。请严格按上述 JSON 格式回复。"
            )
            logger.info(
                "step:video_understand.group",
                group_index=gi,
                group_size=len(g["paths"]),
                start_s=round(g["timestamps"][0], 2),
                end_s=round(g["timestamps"][-1], 2),
            )
            raw = _call_vlm(
                g["paths"], prompt,
                api_key=api_key, base_url=base_url, model=model,
                logger=logger,
            )
            all_raw_texts.append(f"\n\n===== Group {gi} =====\n{raw}")
            (output_dir / "responses" / f"video_understand_group{gi:02d}.txt").write_text(
                raw, encoding="utf-8",
            )

            parsed = extract_json_object(raw)
            frames_in = parsed.get("frames")
            if not isinstance(frames_in, list) or not frames_in:
                logger.warn(
                    "video_understand.group_parse_fail",
                    group=gi,
                    hint="这一组没拿到有效 frames，生成占位条目",
                )
                frames_in = []
            if len(frames_in) != len(g["paths"]):
                logger.warn(
                    "video_understand.group_frame_mismatch",
                    group=gi,
                    expected=len(g["paths"]),
                    got=len(frames_in),
                    hint="按 min 截断 / 末尾补空",
                )

            group_summary = str(parsed.get("group_summary") or "").strip()
            groups_meta.append({
                "group_index": gi,
                "start_idx": g["start_idx"],
                "end_idx": g["end_idx"],
                "start_s": round(g["timestamps"][0], 3),
                "end_s": round(g["timestamps"][-1], 3),
                "summary": group_summary,
            })

            for k in range(len(g["paths"])):
                global_i = g["start_idx"] + k
                item = frames_in[k] if k < len(frames_in) and isinstance(frames_in[k], dict) else {}
                normalized.append({
                    "frame_index": global_i,
                    "timestamp_s": round(float(frame_timestamps[global_i]), 3),
                    "caption": str(item.get("caption") or "").strip(),
                    "key_ui_element": str(item.get("key_ui_element") or "").strip(),
                    "linked_doc_section": str(item.get("linked_doc_section") or "").strip(),
                })

        video_summary = "；".join(
            g["summary"] for g in groups_meta if g["summary"]
        )[:200] or "（分组识别完成）"

    else:
        # 单组模式：所有帧一起送
        sys_prompt = SYSTEM_PROMPT_FLAT.format(
            role=role_label,
            context_label=context_label,
        )
        prompt = (
            sys_prompt
            + f"\n\n---\n\ncontext ({context_label}):\n" + excerpt
            + f"\n\n---\n\n共 {len(frame_paths)} 张图片，按时间顺序传入。请严格按上述 JSON 格式回复。"
        )
        logger.info(
            "step:video_understand.start",
            frame_count=len(frame_paths),
            mode="flat",
            context_chars=len(excerpt),
        )
        raw = _call_vlm(
            frame_paths, prompt,
            api_key=api_key, base_url=base_url, model=model,
            logger=logger,
        )
        all_raw_texts.append(raw)
        (output_dir / "responses" / "video_understand.txt").write_text(
            raw, encoding="utf-8",
        )

        parsed = extract_json_object(raw)
        frames_in = parsed.get("frames")
        if not isinstance(frames_in, list) or not frames_in:
            raise SystemExit(
                "视觉模型未返回有效的 frames 数组。请检查 responses/video_understand*.txt。"
            )
        if len(frames_in) != len(frame_paths):
            logger.warn(
                "video_understand.frame_count_mismatch",
                expected=len(frame_paths),
                got=len(frames_in),
                hint="将按 min(expected, got) 截断，缺的部分补空",
            )
        video_summary = str(parsed.get("video_summary") or "").strip()

        n = min(len(frames_in), len(frame_paths))
        for i in range(len(frame_paths)):
            if i < n:
                item = frames_in[i] or {}
                cap = str(item.get("caption") or "").strip()
                key = str(item.get("key_ui_element") or "").strip()
                sec = str(item.get("linked_doc_section") or "").strip()
            else:
                cap = key = sec = ""
            normalized.append({
                "frame_index": i,
                "timestamp_s": round(float(frame_timestamps[i]), 3),
                "caption": cap,
                "key_ui_element": key,
                "linked_doc_section": sec,
            })

    elapsed = round(time.time() - t0, 2)
    logger.info("step:video_understand.done", elapsed_s=elapsed, mode="grouped" if use_grouping else "flat")

    payload: dict[str, Any] = {
        "video_summary": video_summary,
        "frames": normalized,
    }
    if groups_meta:
        payload["groups"] = groups_meta

    (output_dir / "frame_captions.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def load_frame_captions(path: Path) -> dict[str, Any]:
    """读取 frame_captions.json（--skip-vision 用）。兼容旧版本无 groups 字段。"""
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}；不能 --skip-vision。请先跑一次不带 --skip-vision 的完整命令。"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("groups", [])
    for f in data.get("frames", []):
        f.setdefault("key_ui_element", "")
        f.setdefault("linked_doc_section", "")
    return data


def write_empty_captions(
    frame_paths: list[Path],
    frame_timestamps: list[float],
    output_path: Path,
    *,
    manual_stub: bool = False,
    logger: PipelineLogger | None = None,
    video_summary_stub: str = "[未使用视觉模型；仅根据上下文与视频时长写讲稿]",
) -> dict[str, Any]:
    """写一份不依赖视觉大模型的 frame_captions.json 兜底文件。

    Args:
        manual_stub: True 时留占位字符串提示用户手填；False 时全部留空，交给下游 narrator
                     直接根据上下文 + 时间戳"盲讲"。
    """
    stub = "[手填这里，30 字内描述这一帧屏幕上在做什么]" if manual_stub else ""
    payload = {
        "video_summary": video_summary_stub if not manual_stub
        else "[手填这里，30-60 字概括整段视频演示的核心流程]",
        "groups": [],
        "frames": [
            {
                "frame_index": i,
                "timestamp_s": round(float(ts), 3),
                "caption": stub,
                "key_ui_element": "",
                "linked_doc_section": "",
            }
            for i, ts in enumerate(frame_timestamps)
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if logger:
        logger.info(
            "video_understand.stub",
            manual=manual_stub,
            frame_count=len(frame_timestamps),
            path=str(output_path),
        )
    return payload
