"""独立诊断脚本：验证新的 Ark 视觉模型接入点是否可用。

只依赖标准库 + requests，不 import 项目其它模块，避免连带装 imageio-ffmpeg / PIL / pyJianYingDraft 等重型依赖。

用法（PowerShell / bash 通用）：
    # 1) 装最小依赖（如果 venv 里已有 requests 就跳过）
    pip install requests

    # 2) 通过环境变量传 key（**不要写在命令行里**，避免留在 shell history）
    #    PowerShell:
    #        $env:ARK_API_KEY_TEST = "ark-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    #    Bash / Git Bash:
    #        export ARK_API_KEY_TEST="ark-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

    # 3) 跑测试（用接入点 ID 作为 model 字段，最保险）
    python examples/test_vision_model.py `
        --model ep-m-20260706201520-dblv6 `
        --image "outputs/doc_videos/<slug>/<ts>/frames/frame_01.jpg"

    # 或者不指定 --image，会自动到 outputs/**/frames/ 找一张 jpg
    python examples/test_vision_model.py --model ep-m-20260706201520-dblv6

预期成功输出：
    HTTP 200
    模型返回:  这是一张 xxx 的截图，画面里可以看到 ...

常见失败：
    HTTP 401 → key 错 or base_url 错（agent-plan 的 key 走不通标准 v3；反之亦然）
    HTTP 404 model_not_found → model 字段用了没部署的模型名；改用接入点 ID
    HTTP 403 UnsupportedModel → key 所在套餐不支持视觉；换标准 Ark key
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests


DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ROOT = Path(__file__).resolve().parent.parent


def find_sample_image() -> Path | None:
    """自动在 outputs/ 下找一张已经抽好的帧图当测试样本。"""
    candidates = sorted(
        (ROOT / "outputs").rglob("frames/*.jpg"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def encode_image_to_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "jpeg")
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


def call_vision(
    api_key: str,
    base_url: str,
    model: str,
    image_data_url: str,
    prompt: str,
) -> tuple[int, dict | str]:
    resp = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        },
        timeout=120,
    )
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, resp.text[:500]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="快速验证 Ark 视觉大模型接入点可不可用（不动主流水线）。",
    )
    parser.add_argument("--api-key", type=str, default=None,
                        help="Ark API Key（推荐用环境变量 ARK_API_KEY_TEST 传入，避免留 shell 历史）")
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL,
                        help=f"Ark 基础 URL（默认 {DEFAULT_BASE_URL}，agent-plan 需改为 .../api/plan/v3）")
    parser.add_argument("--model", type=str, required=True,
                        help="模型 ID 或接入点 ID，例如 ep-m-20260706201520-dblv6")
    parser.add_argument("--image", type=Path, default=None,
                        help="测试图片路径；不填则自动在 outputs/**/frames/*.jpg 里挑一张最新的")
    parser.add_argument("--prompt", type=str,
                        default="请用一句话（20-40 字）描述这张图上你看到的关键内容。",
                        help="给视觉大模型的提问")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ARK_API_KEY_TEST", "").strip()
    if not api_key:
        print("[错误] 未提供 API Key。请设置环境变量 ARK_API_KEY_TEST 或用 --api-key 传入。")
        return 2

    image_path = args.image or find_sample_image()
    if image_path is None:
        print("[错误] 未指定 --image 且 outputs/**/frames/ 下没有任何 jpg 可用。请先跑一次 make_narration_video.py 或直接给一张图。")
        return 2
    if not image_path.exists():
        print(f"[错误] 图片不存在: {image_path}")
        return 2

    print("=" * 60)
    print("  Ark 视觉模型接入点测试")
    print("=" * 60)
    print(f"  base_url:  {args.base_url}")
    print(f"  model:     {args.model}")
    print(f"  image:     {image_path}")
    print(f"  key:       {api_key[:8]}...{api_key[-4:]}  (长度 {len(api_key)})")
    print("=" * 60)

    image_data_url = encode_image_to_data_url(image_path)
    status, body = call_vision(api_key, args.base_url, args.model, image_data_url, args.prompt)

    print(f"\nHTTP {status}")
    if status != 200:
        # 出错时打印完整 body 便于诊断
        print("[响应体]")
        print(json.dumps(body, ensure_ascii=False, indent=2) if isinstance(body, dict) else body)
        print("\n常见错误对照：")
        print("  401 unauthorized       → key 错，或 base_url 与 key 所属套餐不匹配（plan vs 标准 v3）")
        print("  404 model_not_found    → 模型未部署到该账户；改用接入点 ID（--model ep-m-...）")
        print("  403 UnsupportedModel   → key 所在套餐不支持视觉；换一把标准 Ark key")
        return 1

    if not isinstance(body, dict):
        print(body)
        return 1

    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        print("[警告] 响应结构不含预期字段；完整响应：")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 1

    print("\n模型返回:")
    if isinstance(content, list):
        # 兼容 content 为分段结构的少数情况
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                print(f"  {part.get('text', '')}")
    else:
        print(f"  {content}")

    usage = body.get("usage")
    if usage:
        print(f"\nusage: {usage}")

    print("\n[通过] 视觉大模型可以正常调用。可以接下来配到项目里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
