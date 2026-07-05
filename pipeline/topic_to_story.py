"""Step 0：主题 → 结构化旁白稿。

本项目在参考项目基础上新增的一层。参考项目要求用户先写好一整段 story，
这里把「story」也交给 LLM 生成，用户只要提供一个主题短语（例如「南明李定国」）
和可选的侧重点（brief），就能开始整条流水线。

输出：
    {
      "project_name": "南明_李定国",   # 文件夹/草稿名 slug
      "title":        "南明·李定国",   # 用于标题卡（可选）
      "author":       "史事简录",       # 副标题（可选）
      "story":        "…… 800-1500 字连贯旁白，句式适合朗读 ……"
    }
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


SYSTEM_PROMPT_TOPIC_TO_STORY = """你是一位专业的短视频脚本撰稿人，擅长历史人物、历史事件、文化典故的旁白稿。

给定一个「主题」以及可选的「侧重点」，请为一部 2-4 分钟的中文视频撰写完整的旁白稿。

写作要求：
1. 长度控制在 800-1500 中文字符之间（含标点）。
2. 按照时间线或事件链组织内容，脉络清晰、事迹连贯；突出高光时刻与情感张力。
3. 语言正式而不晦涩，句式短促有力，节奏适合朗读；避免长难句、避免网络流行语。
4. 首句作为引子（点出人物/背景），末句点题或余韵回响。
5. 全程使用旁白第三人称；不出现「大家好」「今天我们讲」这类口语套话。
6. 段落之间使用一个换行符分隔即可，不要输出小标题、不要 Markdown。

同时给出：
- project_name：中文短语，简洁 4-8 字，可用于文件夹/草稿名（例如「南明李定国」「苏轼黄州」）
- title：视频标题（8-14 字，可含分隔号「·」）
- author：一句话副标题或署名（可选，可为空字符串）

【JSON 严格格式要求 —— 极其重要，违反会导致解析失败】
- 只返回一个 JSON 对象，不要任何解释文字、不要 markdown 代码围栏。
- 字符串值内部若需要「引用/绰号/别名」，一律用中文引号 「」 或 " "，
  绝对禁止使用英文直双引号 " —— 那会破坏 JSON 结构。
- 字符串值内部若需要换行，使用 \\n 转义序列，不要直接换行。
- 例：正确写法 "被称为「新亨利」"、错误写法 "被称为\\"新亨利\\""。

返回格式：
{
  "project_name": "…",
  "title":        "…",
  "author":       "…",
  "story":        "…"
}
"""


def generate_story_from_topic(
    api_key: str,
    topic: str,
    *,
    brief: str = "",
    scene_count_hint: int = 8,
    output_dir: Path,
    logger: PipelineLogger,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """调用 Ark LLM，把主题 → 结构化旁白稿并落盘。"""
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    user_payload: dict[str, Any] = {
        "topic": topic,
        "brief": brief,
        "target_scene_count": scene_count_hint,
        "target_length_range": [800, 1500],
    }

    logger.info("step:topic_to_story.start", model=model, topic=topic,
                brief=brief[:120] if brief else "")

    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        "topic_to_story.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
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
                    {"role": "system", "content": SYSTEM_PROMPT_TOPIC_TO_STORY},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "temperature": 0.8,
                # 注：Ark ark-code-latest 不支持 response_format=json_object，
                # 因此依赖 prompt 硬约束 + pipeline.helpers._repair_bare_quotes_in_string_values 兜底。
            },
            timeout=240,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    t0 = time.time()
    response_json = call_llm()
    elapsed = round(time.time() - t0, 2)
    logger.info("step:topic_to_story.done", elapsed_s=elapsed, model=model)

    (output_dir / "responses" / "topic_to_story.json").write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    content = response_json["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)

    project_name = str(parsed.get("project_name") or "").strip()
    title = str(parsed.get("title") or "").strip()
    author = str(parsed.get("author") or "").strip()
    story = str(parsed.get("story") or "").strip()

    if not story:
        raise SystemExit("LLM 没有返回 story 字段。")
    if not project_name:
        # 从 topic 兜底生成 slug
        project_name = safe_slug(topic, fallback="video")
    if not title:
        title = topic

    result = {
        "project_name": project_name,
        "title": title,
        "author": author,
        "story": story,
    }

    (output_dir / "generated_story.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
