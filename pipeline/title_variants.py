"""Step 0.5：旁白稿 → B 站 / 短视频标题候选。

主流水线 Step 0 只产出一个 title 作为草稿命名用；这里再打一次 LLM，
基于同一份旁白稿生成 8-10 个不同定位的标题候选（流量向 / 正经历史 /
悬念钩子 / 引经据典 / 系列人设），并给出一个推荐。

产物：
- `outputs/projects/<slug>/<ts>/titles.json`        —— 结构化候选列表
- `outputs/projects/<slug>/<ts>/responses/title_variants.json`  —— 原始 LLM 响应

不改动草稿命名逻辑；title 依旧沿用 Step 0 返回的 primary_title。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.helpers import PipelineLogger, extract_json_object, retry


# 五类定位，稳定输出模板；顺序即输出顺序，便于人工比对
TITLE_CATEGORIES = [
    ("traffic",     "流量向",     "数字/极端词/名场面钩子，追求首页点击"),
    ("historical",  "正经历史",   "信息量高、克制、适合历史区收藏"),
    ("hook",        "悬念钩子",   "反问/悬念/未完成句，激发好奇"),
    ("literary",    "引经据典",   "诗句/文言短句/文学感强"),
    ("series",      "系列人设",   "适合作为账号系列开头，塑造 IP"),
]


SYSTEM_PROMPT_TITLE_VARIANTS = """你是资深 B 站历史区标题策划人，擅长把一段旁白稿改写成能吸量的短视频标题。

给定一段 800-1500 字的旁白稿，请产出 8-10 个不同定位的标题候选，覆盖以下五类：

1. traffic 「流量向」——数字/极端词/名场面钩子，例如「44 岁孤守 10 天」「明朝最后的读书人」
2. historical 「正经历史」——信息量高、克制不夸张，适合历史区收藏
3. hook 「悬念钩子」——反问/悬念/未完成句，激发好奇，例如「城破那一夜，他为什么不逃？」
4. literary 「引经据典」——诗句/文言短句/文学感强
5. series 「系列人设」——适合作为账号系列开头，例如「【明末群像 01】...」

写作要求：
- 每条标题 12-30 中文字符（含标点），最长不超过 40 字。
- 至少覆盖 4 个类别；同一类别最多 3 条。
- 不要用「震惊」「速看」这类过度标题党词；克制中带钩子。
- 允许用「·」「｜」「【】」「，」等符号；不要 emoji；不要 hashtag。
- 每条附一句 15-30 字的 reason 说明选它的理由（受众/定位/情感基调）。
- 最后选一条你最推荐的（recommended_index，从 0 开始），并给出 recommend_reason。

【JSON 严格格式要求 —— 极其重要，违反会导致解析失败】
- 只返回一个 JSON 对象，不要任何解释文字、不要 markdown 代码围栏。
- 字符串值内部若需要「引用/绰号」一律用中文引号 「」 或 " "，
  绝对禁止使用英文直双引号 " —— 那会破坏 JSON 结构。
- 字符串内若需要换行使用 \\n 转义。

返回格式：
{
  "variants": [
    {"category": "traffic|historical|hook|literary|series", "title": "…", "reason": "…"},
    …
  ],
  "recommended_index": 0,
  "recommend_reason": "…"
}
"""


def generate_title_variants(
    api_key: str,
    topic: str,
    story_data: dict[str, str],
    output_dir: Path,
    logger: PipelineLogger,
    *,
    brief: str = "",
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """基于 Step 0 的旁白稿生成 B 站标题候选。

    返回 dict：
        {
          "topic": ...,
          "primary_title": ...,   # Step 0 生成的原始 title，保持不动
          "variants": [{"category","title","reason"}, ...],
          "recommended_index": int,
          "recommend_reason": str,
        }
    """
    base_url = (base_url or os.environ.get(
        "PROMPT_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )).rstrip("/")
    model = model or os.environ.get("PROMPT_MODEL", "ark-code-latest")

    story = story_data.get("story", "").strip()
    if not story:
        raise SystemExit("title_variants: story 为空，无法生成标题候选。")

    primary_title = story_data.get("title", "").strip() or topic

    user_payload: dict[str, Any] = {
        "topic": topic,
        "brief": brief,
        "primary_title": primary_title,
        "story": story,
        "target_variant_count": 10,
    }

    logger.info("step:title_variants.start", model=model, topic=topic,
                primary_title=primary_title)

    @retry(max_attempts=3, base_delay=2.0, on_retry=lambda e, a, d: logger.warn(
        "title_variants.retry", attempt=a, delay=round(d, 1), error=str(e)[:120]
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
                    {"role": "system", "content": SYSTEM_PROMPT_TITLE_VARIANTS},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                # 标题需要多样性，温度调高一些
                "temperature": 0.9,
            },
            timeout=120,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    t0 = time.time()
    response_json = call_llm()
    elapsed = round(time.time() - t0, 2)
    logger.info("step:title_variants.done", elapsed_s=elapsed, model=model)

    (output_dir / "responses" / "title_variants.json").write_text(
        json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    content = response_json["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)

    raw_variants = parsed.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        raise SystemExit("title_variants: LLM 未返回 variants 数组。")

    allowed_categories = {key for key, _, _ in TITLE_CATEGORIES}
    variants: list[dict[str, str]] = []
    for item in raw_variants:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        category = str(item.get("category") or "").strip()
        # 兜底：category 不在白名单里时归到 traffic
        if category not in allowed_categories:
            category = "traffic"
        reason = str(item.get("reason") or "").strip()
        variants.append({"category": category, "title": title, "reason": reason})

    if not variants:
        raise SystemExit("title_variants: 所有候选都缺 title 字段，解析失败。")

    recommended_index = parsed.get("recommended_index", 0)
    try:
        recommended_index = int(recommended_index)
    except (TypeError, ValueError):
        recommended_index = 0
    if not 0 <= recommended_index < len(variants):
        recommended_index = 0

    recommend_reason = str(parsed.get("recommend_reason") or "").strip()

    result: dict[str, Any] = {
        "topic": topic,
        "primary_title": primary_title,
        "variants": variants,
        "recommended_index": recommended_index,
        "recommend_reason": recommend_reason,
    }

    (output_dir / "titles.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def format_title_variants_block(titles_data: dict[str, Any]) -> str:
    """把 titles.json 结构渲染成终端友好的多行文本。"""
    variants = titles_data.get("variants") or []
    if not variants:
        return "(未生成标题候选)"

    # 中文标签映射，便于终端展示
    cat_label = {key: label for key, label, _ in TITLE_CATEGORIES}

    lines: list[str] = []
    lines.append("─" * 60)
    lines.append("  B 站标题候选（挑一条填到 B 站投稿页 title 里）")
    lines.append("─" * 60)

    rec_idx = titles_data.get("recommended_index", 0)
    for i, v in enumerate(variants):
        label = cat_label.get(v.get("category", "traffic"), v.get("category", ""))
        marker = "★" if i == rec_idx else " "
        lines.append(f"  {marker} [{i + 1:>2}] ({label}) {v['title']}")
        reason = v.get("reason", "")
        if reason:
            lines.append(f"        └─ {reason}")

    recommend_reason = titles_data.get("recommend_reason", "")
    if recommend_reason:
        lines.append("")
        lines.append(f"  推荐 (★ 第 {rec_idx + 1} 条): {recommend_reason}")
    lines.append("─" * 60)
    return "\n".join(lines)
