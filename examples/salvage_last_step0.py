"""救援脚本：把上一次 Step 0 已花钱但 JSON 解析失败的响应转成 generated_story.json。

用法（示例）：
    python examples/salvage_last_step0.py "outputs/projects/姆巴佩/20260705_132114"

之后就可以：
    python make_video.py --topic "姆巴佩" --style shorts \
        --output-dir outputs/projects/姆巴佩/20260705_132114 --skip-llm
    # 或用 --resume-latest：
    python make_video.py --topic "姆巴佩" --style shorts --resume-latest --skip-llm
从 Step 1 场景切分开始接着跑，就不必再花 Step 0 的 LLM 钱。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本能从 examples/ 目录以外的地方运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.helpers import extract_json_object, safe_slug


def salvage(run_dir: Path) -> None:
    resp_path = run_dir / "responses" / "topic_to_story.json"
    if not resp_path.exists():
        raise SystemExit(f"找不到原始响应文件：{resp_path}")

    raw = json.loads(resp_path.read_text(encoding="utf-8"))
    content = raw["choices"][0]["message"]["content"]

    parsed = extract_json_object(content)   # 走 helpers 里的完整容错链

    project_name = str(parsed.get("project_name") or "").strip()
    story = str(parsed.get("story") or "").strip()
    if not story:
        raise SystemExit("响应里 story 字段为空，无法救回")

    if not project_name:
        project_name = safe_slug(run_dir.parent.name, fallback="video")

    result = {
        "project_name": project_name,
        "title":        str(parsed.get("title") or project_name).strip(),
        "author":       str(parsed.get("author") or "").strip(),
        "story":        story,
    }

    out_path = run_dir / "generated_story.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"✓ 救回成功 → {out_path}")
    print(f"  project_name: {result['project_name']}")
    print(f"  title:        {result['title']}")
    print(f"  story 长度:   {len(result['story'])} 字")
    print()
    print("现在可以从 Step 1 续跑，不再花 Step 0 的钱：")
    topic_hint = run_dir.parent.name
    print(f'    python make_video.py --topic "{topic_hint}" --resume-latest --skip-llm')


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python examples/salvage_last_step0.py <output_dir>")
        print('示例: python examples/salvage_last_step0.py "outputs/projects/姆巴佩/20260705_132114"')
        return 2
    run_dir = Path(sys.argv[1]).resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"目录不存在：{run_dir}")
    salvage(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
