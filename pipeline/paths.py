"""集中管理跨机器 / 跨用户不同的输出路径。

优先级：环境变量（.env 或系统环境）> 内置默认值。
换机器只需编辑项目根 .env 里的 JIANYING_DRAFT_FOLDER / OUTPUTS_ROOT，
无需改任何 .py。

用法示例:
    from pipeline.paths import (
        JIANYING_DRAFT_FOLDER,
        LOG_DIR,
        PROJECTS_DIR,
    )
"""
from __future__ import annotations

import os
from pathlib import Path

from pipeline.helpers import load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 导入本模块时自动加载项目根 .env，让下方 os.environ.get 立即可用。
# load_env 用 setdefault，系统环境变量优先级更高，重复调用无副作用。
_ENV_FILE = PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_env(_ENV_FILE)


def _env_path_or(key: str, default: Path) -> Path:
    """从环境变量读路径；空 / 未设置则用 default。"""
    raw = os.environ.get(key, "").strip()
    return Path(raw).expanduser() if raw else default


# —— 跨机器不同 ——
# 剪映草稿根目录（Windows 默认 D:/software/JianyingPro Drafts）
JIANYING_DRAFT_FOLDER: str = os.environ.get(
    "JIANYING_DRAFT_FOLDER", "D:/software/JianyingPro Drafts"
).strip() or "D:/software/JianyingPro Drafts"

# 项目产物根目录：默认 <项目根>/outputs；想搬外置盘就在 .env 里改
OUTPUTS_ROOT: Path = _env_path_or("OUTPUTS_ROOT", PROJECT_ROOT / "outputs")

# —— 派生路径（跟仓库语义相关，不建议单独改；改 OUTPUTS_ROOT 就够）——
LOG_DIR:        Path = OUTPUTS_ROOT / "logs"
BLUR_CACHE_DIR: Path = OUTPUTS_ROOT / "blur_cache"
PROJECTS_DIR:   Path = OUTPUTS_ROOT / "projects"          # make_video.py
NARRATION_DIR:  Path = OUTPUTS_ROOT / "narration_videos"  # make_narration_video.py
DOC_VIDEO_DIR:  Path = OUTPUTS_ROOT / "doc_videos"        # make_doc_video.py
CODE_WALK_DIR:  Path = OUTPUTS_ROOT / "code_walks"        # make_code_walk.py
CAROUSEL_DIR:   Path = OUTPUTS_ROOT / "carousels"         # make_carousel_video.py
