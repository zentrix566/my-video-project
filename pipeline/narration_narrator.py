"""Step 2 · make_narration_video.py 专用：视频元信息 + 逐帧描述 → N 段讲解稿 + 切段时间戳。

本文件现仅做向后兼容的 re-export，核心实现（含 generate_narration_scenes /
SYSTEM_PROMPT_NARRATION / _normalize_scenes / load_generated_scenes）已统一迁入
pipeline/doc_narrator.py，避免两套 LLM 逻辑漂移。
"""

from pipeline.doc_narrator import (  # noqa: F401
    SYSTEM_PROMPT_NARRATION,
    _normalize_scenes,
    generate_narration_scenes,
    load_generated_scenes,
)
