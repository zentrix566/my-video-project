"""Step 1：故事 → 场景数组（narration + image_prompt）。

严格复用参考项目 run_story_pipeline.py 的 split_story_to_scenes 实现，
仅调整了导入路径与几个默认参数。
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


SYSTEM_PROMPT_SCENE_SPLIT = """你是故事视觉导演，负责将一段故事拆分为适合制作视频的多个场景。

核心原则：
1. 旁白文本必须直接使用故事原文，按场景切分，不要改写、不要概括、不要缩写
2. 所有场景的旁白拼接起来必须等于完整的故事原文（允许仅调整句首/句尾标点以适配朗读节奏）
3. 每个场景的旁白应该是 2-4 句话，对应故事中一个相对完整的画面
4. 图片提示语要具体、画面感强，适合文生图模型
5. 场景之间要有连贯性，形成一个完整的故事
6. 图片风格由外部风格预设决定（史诗电影 / 纪录片 / 短视频等），使用者会在 image_prompt 之外统一追加后缀
7. 画面绝对干净——不生成任何文字、字幕、水印、logo

【JSON 严格格式要求 —— 极其重要，违反会导致解析失败】
- 只返回一个 JSON 对象，不要任何解释文字、不要 markdown 代码围栏。
- narration / image_prompt 字符串内部若需要「引用/绰号/别名」，
  一律用中文引号 「」 或 " "，绝对禁止使用英文直双引号 " —— 那会破坏 JSON 结构。
- 字符串内部若需要换行，使用 \\n 转义序列，不要直接换行。
- 例：正确 "被称为「新亨利」"，错误 "被称为\\"新亨利\\""。
"""


def split_story_to_scenes(
    api_key: str,
    project_name: str,
    story: str,
    scene_count: int,
    output_dir: Path,
    logger: PipelineLogger,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> list[dict[str, str]]:
    """LLM 把 story 拆成 N 个场景。返回 [{id, narration, image_prompt}, ...]。"""
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    user_payload = {
        "project_name": project_name,
        "scene_count": scene_count,
        "story": story,
        "output_schema": {
            "scenes": [
                {
                    "id": "scene_1",
                    "narration": "直接摘取故事原文中属于该场景的句子，不要改写或概括",
                    "image_prompt": "该场景的画面描述，具体的、画面感强的中文提示语",
                }
            ]
        },
    }

    logger.info("step:scene_split.start", model=model, scene_count=scene_count)

    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        "scene_split.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
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
                    {"role": "system", "content": SYSTEM_PROMPT_SCENE_SPLIT},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "temperature": 0.7,
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
    logger.info("step:scene_split.done", elapsed_s=elapsed, model=model)

    (output_dir / "responses" / "scene_split.json").write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    content = response_json["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)
    scenes = parsed.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("LLM 未返回有效的 scenes 数组。")

    result: list[dict[str, str]] = []
    for index, scene in enumerate(scenes, start=1):
        narration = str(scene.get("narration") or scene.get("text") or "").strip()
        image_prompt = str(scene.get("image_prompt") or scene.get("prompt") or "").strip()
        if not narration:
            raise SystemExit(f"Scene {index} 缺少 narration。")
        if not image_prompt:
            raise SystemExit(f"Scene {index} 缺少 image_prompt。")
        narration_slug = safe_slug(narration[:15], fallback="scene")
        result.append({
            "id": f"{index:02d}_{narration_slug}",
            "narration": narration,
            "image_prompt": image_prompt,
        })

    (output_dir / "generated_scenes.json").write_text(
        json.dumps({"scenes": result}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
