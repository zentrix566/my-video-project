"""Step 2：豆包 Seedream 并行出图。薄封装 ParallelImageGenerator。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from volcenginesdkarkruntime import Ark

from pipeline.helpers import ParallelImageGenerator, PipelineLogger


def generate_images(
    api_key: str,
    scenes: list[dict[str, str]],
    output_dir: Path,
    logger: PipelineLogger,
    *,
    base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3",
    model: str = "doubao-seedream-5.0-lite",
    size: str = "2K",
    output_format: str = "png",
    response_format: str = "url",
    watermark: bool = False,
    target_aspect_ratio: str | None = "16:9",
    trim_black_borders: bool = True,
    prompt_suffix: str = "",
    max_workers: int = 1,
    resume: bool = True,
    request_delay: float = 2.0,
) -> list[str]:
    """并行生成每个场景对应的图片，返回按 scenes 顺序排列的本地路径列表。"""
    client = Ark(base_url=base_url, api_key=api_key)
    prompts = [{"id": s["id"], "image_prompt": s["image_prompt"]} for s in scenes]

    gen = ParallelImageGenerator(
        client=client,
        model=model,
        output_dir=output_dir / "images",
        size=size,
        output_format=output_format,
        response_format=response_format,
        watermark=watermark,
        target_aspect_ratio=target_aspect_ratio,
        trim_black_borders=trim_black_borders,
        prompt_suffix=prompt_suffix,
        max_workers=max_workers,
        resume=resume,
        request_delay=request_delay,
        on_progress=lambda tid, done, total: logger.info(
            "image.progress", task_id=tid, done=done, total=total
        ),
    )

    logger.info("step:image_generation.start", count=len(prompts), workers=max_workers,
                model=model, size=size)
    t0 = time.time()
    result_map = gen.generate(prompts)
    elapsed = round(time.time() - t0, 2)
    image_paths = [result_map[p["id"]] for p in prompts]
    logger.info("step:image_generation.done", elapsed_s=elapsed, count=len(image_paths))
    return image_paths
