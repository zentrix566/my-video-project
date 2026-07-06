"""Step 1 · make_doc_video.py 专用：把抽出的帧交给视觉大模型，得到逐帧描述。

一次视觉 LLM 调用传 N 张帧 + PDF 摘要 + 结构化 prompt，输出 JSON：
    [{frame_index, timestamp_s, caption, key_ui_element}, ...]

其中 timestamp_s 由本模块从 doc_content.json 的 frames[].timestamp_s 补齐（LLM 不看时间戳）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pipeline.helpers import (
    PipelineLogger, extract_json_object, vision_chat,
)


SYSTEM_PROMPT_VIDEO_UNDERSTAND = """你是一位资深的软件产品分析师。用户会给你一段 PDF 需求文档的摘录，以及从一段操作演示视频里均匀抽取的若干张截图。请**结合需求文档的上下文**逐帧分析截图，说明每一帧屏幕上正在做什么、涉及需求文档里的哪一步。

## 输入
- pdf_excerpt: PDF 前若干字符（业务上下文）
- 图片列表：从视频里按时间顺序均匀抽出的 N 张截图（第 0 张最靠前，第 N-1 张最靠后）

## 输出（一个 JSON 对象，仅含以下顶层字段）
```
{
  "video_summary": "一句话（30-60 字）概括整段视频演示的核心业务流程",
  "frames": [
    {
      "frame_index": 0,
      "caption": "简明中文描述这一帧屏幕上在做什么，含具体按钮名/表单字段名/页面标题等可辨认的界面元素（20-50 字）",
      "key_ui_element": "本帧最关键的一个 UI 元素或动作名（≤ 15 字，如：点击「同步销项发票」、查看差异表、进入申报表填写页）",
      "linked_doc_section": "本帧对应 PDF 需求文档里的哪一步/哪一节（≤ 20 字，若无法对应就写「无明显对应」）"
    }
  ]
}
```

## 规则
- **frames 数组长度必须等于输入图片数量**，frame_index 从 0 开始严格递增。
- caption 只描述屏幕上真实可见的内容，不要臆测未展示的功能。
- 若某帧是黑屏/加载中/过渡帧，如实写「加载中过渡帧」并给一个合理的 key_ui_element。
- **只返回 JSON 对象，不要 markdown 代码围栏、不要任何解释性文字。**
- caption / key_ui_element / linked_doc_section 内部若需要引号，一律用中文「」或 " "，绝不使用英文 " —— 会破坏 JSON。
"""


def caption_frames(
    api_key: str,
    frame_paths: list[Path],
    frame_timestamps: list[float],
    pdf_excerpt: str,
    *,
    output_dir: Path,
    logger: PipelineLogger,
    base_url: str | None = None,
    model: str | None = None,
    pdf_excerpt_chars: int = 3000,
) -> dict[str, Any]:
    """调视觉大模型描述所有抽出的帧，落盘 frame_captions.json 后返回 dict。"""
    if len(frame_paths) != len(frame_timestamps):
        raise SystemExit(
            f"frame_paths 和 frame_timestamps 长度不一致："
            f"{len(frame_paths)} vs {len(frame_timestamps)}"
        )
    if not frame_paths:
        raise SystemExit("frame_paths 为空，无法调用视觉模型。")

    excerpt = (pdf_excerpt or "").strip()
    if len(excerpt) > pdf_excerpt_chars:
        excerpt = excerpt[:pdf_excerpt_chars] + "\n\n[... PDF 后续内容省略，用于视觉分析已足够 ...]"

    prompt = SYSTEM_PROMPT_VIDEO_UNDERSTAND + "\n\n---\n\npdf_excerpt:\n" + excerpt + \
        f"\n\n---\n\n共 {len(frame_paths)} 张图片，按时间顺序传入。请严格按上述 JSON 格式回复。"

    logger.info(
        "step:video_understand.start",
        frame_count=len(frame_paths),
        pdf_excerpt_chars=len(excerpt),
    )

    t0 = time.time()
    content = vision_chat(
        frame_paths,
        prompt,
        api_key=api_key,
        logger=logger,
        base_url=base_url,
        model=model,
        temperature=0.4,
    )
    elapsed = round(time.time() - t0, 2)
    logger.info("step:video_understand.done", elapsed_s=elapsed)

    (output_dir / "responses" / "video_understand.txt").write_text(
        content, encoding="utf-8"
    )

    parsed = extract_json_object(content)
    frames = parsed.get("frames")
    if not isinstance(frames, list) or not frames:
        raise SystemExit(
            "视觉模型未返回有效的 frames 数组。请检查 responses/video_understand.txt。"
        )
    if len(frames) != len(frame_paths):
        logger.warn(
            "video_understand.frame_count_mismatch",
            expected=len(frame_paths),
            got=len(frames),
            hint="将按 min(expected, got) 截断",
        )

    # 补齐 timestamp_s，保证下游 doc_narrator 拿到准确的视频时间戳
    normalized: list[dict[str, Any]] = []
    n = min(len(frames), len(frame_paths))
    for i in range(n):
        item = frames[i] or {}
        normalized.append({
            "frame_index": i,
            "timestamp_s": round(float(frame_timestamps[i]), 3),
            "caption": str(item.get("caption") or "").strip(),
            "key_ui_element": str(item.get("key_ui_element") or "").strip(),
            "linked_doc_section": str(item.get("linked_doc_section") or "").strip(),
        })

    payload = {
        "video_summary": str(parsed.get("video_summary") or "").strip(),
        "frames": normalized,
    }

    (output_dir / "frame_captions.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def load_frame_captions(path: Path) -> dict[str, Any]:
    """读取 frame_captions.json（--skip-vision 用）。"""
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}；不能 --skip-vision。请先跑一次不带 --skip-vision 的完整命令。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_empty_captions(
    frame_paths: list[Path],
    frame_timestamps: list[float],
    output_path: Path,
    *,
    manual_stub: bool = False,
    logger: PipelineLogger | None = None,
) -> dict[str, Any]:
    """写一份不依赖视觉大模型的 frame_captions.json 兜底文件。

    Args:
        manual_stub: True 时留占位字符串提示用户手填；False 时全部留空，交给下游 doc_narrator
                     直接根据 PDF + 时间戳"盲讲"。
    """
    stub = "[手填这里，30 字内描述这一帧屏幕上在做什么]" if manual_stub else ""
    payload = {
        "video_summary": "[未使用视觉模型；仅根据 PDF 与视频时长写讲稿]" if not manual_stub
        else "[手填这里，30-60 字概括整段视频演示的核心业务流程]",
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
