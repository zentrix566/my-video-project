"""Step 1（代码走读版）：项目元信息 → 8 段讲解稿 + 每段截图规范。

复用 topic_to_story.py 的 LLM 调用范式（requests + Bearer + retry + extract_json_object）。
本模块只做一件事：把 project_meta.json 喂给 LLM，让它输出可执行的 scenes[]。

输出结构（写入 output_dir/generated_scenes.json）：
    {
      "project_title": "crazy-people",
      "subtitle":      "Vue3 密闭空间发疯小人",
      "scenes": [
        {
          "id":        "01_intro",
          "narration": "……适合朗读的 2-4 句中文……",
          "shot_spec": {
            "type": "ui",
            "url_path": "/",
            "wait_ms": 1500,
            "interactions": []
          }
        },
        {
          "id":        "04_world_core",
          "narration": "……",
          "shot_spec": {
            "type": "code",
            "file": "src/game/world.js",
            "focus_lines": [1, 42],
            "language": "javascript",
            "title": "src/game/world.js"
          }
        }
      ]
    }

下游 tts.py / draft_composer.py 只吃 id + narration，shot_spec 是本模块专属扩展字段。
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


SYSTEM_PROMPT_WALK_NARRATOR = """你是一位专业的技术向短视频编剧，擅长给前端开源项目做「代码走读」类讲解视频。

现在给你一个 Node/前端项目的完整元信息（package.json 摘要、README、路由列表、关键文件路径+首 60 行摘录）。
请你为一部 5-6 分钟的中文 B 站/YouTube 视频撰写完整脚本，产出**恰好 8 个场景**。

## 剧作要求

- 8 段合起来是一个完整的故事线，建议节奏：
  1. 开场：一句话点明项目做什么 + 项目名
  2. 定位：这个项目属于什么类型/技术栈/亮点
  3-6. 主体：按「入口 → 核心状态 → 关键子系统 → 视觉/交互层」四段讲代码或对应 UI 表现
  7. 高潮：展示一个最有梗、最有画面感的 UI 时刻
  8. 收尾：一句金句 + 项目地址/试玩提示（可选）
- 每段 narration 使用**中文**，2-4 句，句式短促、口语化、可朗读；避免技术黑话堆砌
- narration 不要出现「大家好」「点赞关注」这类主播套话；也不要出现"接下来我们看""下一段"这类衔接词（视频剪辑自然衔接）
- 允许使用轻幽默、比喻，符合视频调性

## 画面（shot_spec）规则

**大原则：除了讲代码，其它场景一律用动态视频**。首屏必须是动态的（第一眼就要有画面在动，抓观众注意力）。

**类型配额**（8 段总数）：
- 1 段 `cover` 开场（**必须**是 scene 01）：视频 + 大标题叠加。观众第一秒就看到项目在跑，同时标题铺满屏幕。
- 4-5 段 `ui`（等同于 `ui_video`，都是动态视频）：项目跑起来的样子。**任何**能有动画/交互/演化的画面都必须是这种类型。
- 2 段 `code`：**唯一**的静态画面，只用于展示关键代码段。
- 严禁把能动的画面做成静态（除非画面本身 100% 不动，例如纯粹的代码编辑器截图）。

**开场（type: "cover"）字段** —— 8 段的第一段：
- `title`: 大标题（8-14 字）
- `subtitle`: 副标题（≤ 20 字）
- `background_url_path`: 底图/背景视频用哪一页（同 UI 的 url_path）
- `warmup_ms`: 录制开始后先等这么久再执行 interactions（默认 2000，让画面自然演化）
- `tail_ms`: interactions 执行完后继续录这么久（默认 3000）
- `interactions`: 可选。开场一般让画面自然演化，interactions 空数组即可

**动态 UI（type: "ui" 或 "ui_video"，二者等价，都录视频）字段**：
- `url_path`: 页面路径。若 routes 列表非空则从中选一个；若为空则一律填 `"/"`
- `warmup_ms`: 录制开始后先等这么久再执行 interactions（范围 500-6000，默认 2000）
- `tail_ms`: interactions 全部执行完之后继续录这么久（范围 1000-8000，默认 3000）
- `interactions`: 数组，按顺序执行。**执行期间是被录进视频里的**，观众能看到点击瞬间和后续动效：
    * `{"action": "wait",       "ms": 500}`
    * `{"action": "click_text", "text": "混乱 +", "times": 3, "interval_ms": 300}`   ← 通过按钮文字定位
    * `{"action": "click_selector", "selector": ".btn-primary", "times": 1}`         ← 通过 CSS selector 定位
  如果不需要交互，填空数组 `[]` 即可
- 总录制时长 ≈ warmup_ms + interactions 执行时间 + tail_ms，建议 5-8 秒

**代码截图（type: "code"）字段** —— 静态：
- `file`: 相对项目根的文件路径，**必须**从提供的 key_files 里选，路径大小写要一致
- `focus_lines`: `[start, end]`（1-based，闭区间）；`end - start + 1` 必须 ≤ 42（超过字号会太小看不清）
- `language`: prism 语法标识，如 `"javascript"` / `"typescript"` / `"vue"` / `"jsx"` / `"tsx"` / `"json"` / `"html"` / `"css"`
- `title`: 显示在窗口标题栏的文字，一般就是 file 本身

## 讲稿与画面配合

narration 里如果描述了「动态过程」（例如"一步步变红"、"疯子涌向路人"、"点一下就爆炸"），对应场景必然是 `ui`（视频）而不是 `code`。讲静态事实/结构（"这段代码的作用是..."）时用 `code`，看具体源码。

## 输出格式

一个 JSON 对象，仅含两个顶层字段：`project_title`（视频主标题 8-14 字）+ `subtitle`（副标题 ≤ 20 字）+ `scenes`（数组，长度=8）。

每个 scene 必须包含：`id`（形如 "01_intro"、"02_stack"...按序号）+ `narration` + `shot_spec`。
id 用两位数字打头，方便 TTS/图片按顺序命名。

【JSON 严格格式要求 —— 极其重要，违反会导致解析失败】
- 只返回一个 JSON 对象，不要任何解释文字、不要 markdown 代码围栏。
- narration 字段值内部若需要「引用/文件名/术语」，一律用中文引号 「」 或 " "，
  绝对禁止使用英文直双引号 " —— 那会破坏 JSON 结构。
- narration 内部若需换行，使用 \\n 转义序列，不要直接换行。
- 例：正确写法 "src 目录下的「world.js」"、错误写法 "src 目录下的\\"world.js\\""。
"""


def generate_walk_scenes(
    api_key: str,
    project_meta: dict[str, Any],
    *,
    brief: str = "",
    scene_count: int = 8,
    output_dir: Path,
    logger: PipelineLogger,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """调用 LLM，从 project_meta 生成 8 段讲解稿 + 截图规范，落盘 generated_scenes.json。"""
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    # LLM 输入 payload：精简元数据（避免把 project_meta 全丢过去，尤其 excerpt 已够信息量）
    user_payload = {
        "project_name": project_meta.get("raw_name") or project_meta.get("project_name"),
        "framework": project_meta.get("framework"),
        "description": project_meta.get("description", ""),
        "readme_md": project_meta.get("readme_md", ""),
        "routes": project_meta.get("routes", []),
        "key_files": project_meta.get("key_files", []),
        "brief": brief,
        "target_scene_count": scene_count,
    }

    logger.info("step:walk_narrator.start", model=model,
                project=user_payload["project_name"],
                key_files=len(user_payload["key_files"]))

    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        "walk_narrator.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
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
                    {"role": "system", "content": SYSTEM_PROMPT_WALK_NARRATOR},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                "temperature": 0.7,
            },
            timeout=300,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    t0 = time.time()
    response_json = call_llm()
    elapsed = round(time.time() - t0, 2)
    logger.info("step:walk_narrator.done", elapsed_s=elapsed, model=model)

    (output_dir / "responses" / "walk_narrator.json").write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    content = response_json["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)

    scenes = parsed.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("LLM 未返回有效的 scenes 数组。")

    # ---- 校验 & 规范化 ----
    key_file_paths = {f["path"] for f in project_meta.get("key_files", [])}
    normalized: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, start=1):
        narration = str(scene.get("narration") or "").strip()
        if not narration:
            raise SystemExit(f"场景 {index} 缺少 narration。")

        raw_id = str(scene.get("id") or "").strip()
        if not raw_id:
            raw_id = f"{index:02d}_scene"
        # 统一格式：两位数字 + 下划线 + slug
        if not raw_id[:2].isdigit():
            raw_id = f"{index:02d}_{safe_slug(raw_id, fallback='scene')}"
        else:
            raw_id = f"{raw_id[:2]}_{safe_slug(raw_id[3:] or 'scene', fallback='scene')}"

        shot_spec = scene.get("shot_spec") or {}
        stype = str(shot_spec.get("type") or "").strip().lower()
        if stype not in ("ui", "ui_video", "code", "cover"):
            # 兜底：LLM 忘了填就当 UI
            logger.warn("walk_narrator.shot_type_fallback",
                        scene_id=raw_id, got=stype or "empty")
            stype = "ui"

        clean_spec: dict[str, Any] = {"type": stype}

        if stype in ("ui", "cover"):
            url_path = str(shot_spec.get("url_path") or shot_spec.get("background_url_path") or "/").strip()
            if not url_path.startswith("/"):
                url_path = "/" + url_path
            clean_spec["url_path"] = url_path
            wait_ms = int(shot_spec.get("wait_ms") or 1500)
            clean_spec["wait_ms"] = max(300, min(wait_ms, 20000))
            interactions = shot_spec.get("interactions") or []
            if isinstance(interactions, list):
                clean_spec["interactions"] = interactions
            else:
                clean_spec["interactions"] = []

        if stype == "ui_video":
            url_path = str(shot_spec.get("url_path") or "/").strip()
            if not url_path.startswith("/"):
                url_path = "/" + url_path
            clean_spec["url_path"] = url_path
            # warmup + tail 有独立的合理区间
            warmup_ms = int(shot_spec.get("warmup_ms") or 2000)
            tail_ms = int(shot_spec.get("tail_ms") or 3000)
            clean_spec["warmup_ms"] = max(300, min(warmup_ms, 8000))
            clean_spec["tail_ms"] = max(500, min(tail_ms, 10000))
            interactions = shot_spec.get("interactions") or []
            clean_spec["interactions"] = interactions if isinstance(interactions, list) else []

        if stype == "code":
            file_rel = str(shot_spec.get("file") or "").strip().replace("\\", "/")
            if file_rel not in key_file_paths:
                # LLM 挑了个不在清单里的文件；尝试放宽匹配（大小写、去头 ./）
                candidates = [p for p in key_file_paths if p.lower() == file_rel.lower()]
                if candidates:
                    file_rel = candidates[0]
                else:
                    logger.warn("walk_narrator.code_file_not_in_meta",
                                scene_id=raw_id, file=file_rel)
                    # 硬约束失败：退化成 UI 场景，避免后续渲染器崩溃
                    stype = "ui"
                    clean_spec = {"type": "ui", "url_path": "/", "wait_ms": 1500, "interactions": []}
            if stype == "code":
                clean_spec["file"] = file_rel
                lines = shot_spec.get("focus_lines") or [1, 42]
                if isinstance(lines, list) and len(lines) == 2:
                    start, end = int(lines[0]), int(lines[1])
                    start = max(1, start)
                    end = max(start, end)
                    # 强制不超过 42 行
                    if end - start + 1 > 42:
                        end = start + 41
                    clean_spec["focus_lines"] = [start, end]
                else:
                    clean_spec["focus_lines"] = [1, 42]
                clean_spec["language"] = str(shot_spec.get("language") or "").strip() or _guess_language(file_rel)
                clean_spec["title"] = str(shot_spec.get("title") or file_rel).strip()

        if stype == "cover":
            clean_spec["title"] = str(shot_spec.get("title") or parsed.get("project_title") or "").strip()
            clean_spec["subtitle"] = str(shot_spec.get("subtitle") or parsed.get("subtitle") or "").strip()
            # cover 现在也录视频：把 warmup/tail 从 shot_spec 里带过来（若无则用默认）
            warmup_ms = int(shot_spec.get("warmup_ms") or 2000)
            tail_ms = int(shot_spec.get("tail_ms") or 3000)
            clean_spec["warmup_ms"] = max(300, min(warmup_ms, 8000))
            clean_spec["tail_ms"] = max(500, min(tail_ms, 10000))
            interactions = shot_spec.get("interactions") or []
            clean_spec["interactions"] = interactions if isinstance(interactions, list) else []

        normalized.append({
            "id": raw_id,
            "narration": narration,
            "shot_spec": clean_spec,
        })

    result = {
        "project_title": str(parsed.get("project_title") or project_meta.get("raw_name") or "").strip(),
        "subtitle": str(parsed.get("subtitle") or "").strip(),
        "scenes": normalized,
    }

    (output_dir / "generated_scenes.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info("step:walk_narrator.scenes_written",
                count=len(normalized),
                ui=sum(1 for s in normalized if s["shot_spec"]["type"] == "ui"),
                ui_video=sum(1 for s in normalized if s["shot_spec"]["type"] == "ui_video"),
                code=sum(1 for s in normalized if s["shot_spec"]["type"] == "code"),
                cover=sum(1 for s in normalized if s["shot_spec"]["type"] == "cover"))
    return result


_LANG_MAP = {
    ".vue": "markup",   # prism 用 markup 处理 vue SFC
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".html": "markup",
    ".css": "css",
    ".scss": "scss",
    ".md": "markdown",
}


def _guess_language(file_path: str) -> str:
    lower = file_path.lower()
    for ext, lang in _LANG_MAP.items():
        if lower.endswith(ext):
            return lang
    return "text"
