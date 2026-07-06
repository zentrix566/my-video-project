"""Step 3 · make_doc_video.py 专用：按 scenes 里的 video_start_s / video_end_s 切原 mp4。

每段输出 mp4：
    - 视频轨：libx264，pix_fmt=yuv420p，movflags=+faststart（剪映 / QuickTime 兼容）
    - 音频轨：**剥掉**（-an）——避免和 AI 讲解语音打架

关于 clip_tail_padding_s：
    - 现役调用方（make_doc_video.py）走 preserve_video_duration=True 合成，不需要尾余量。
    - 参数保留是为了兼容"crop_video=True 合成"这种老玩法：视频段末尾多切一小截，
      给 draft_composer.crop_video 分支留裁剪空间。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

try:
    import imageio_ffmpeg
    _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 imageio-ffmpeg 依赖。请运行：\n"
        "  pip install imageio-ffmpeg"
    ) from exc

from pipeline.helpers import PipelineLogger


def cut_clips(
    mp4_path: Path,
    scenes: list[dict[str, Any]],
    out_dir: Path,
    *,
    logger: PipelineLogger,
    clip_tail_padding_s: float = 0.0,
    resume: bool = True,
    preset: str = "medium",
    crf: int = 20,
) -> list[Path]:
    """按 scenes 时间戳把 mp4 切成 N 段，返回 mp4 路径列表（与 scenes 顺序一致）。

    Args:
        mp4_path:            源视频
        scenes:              每项含 id / video_start_s / video_end_s（来自 doc_narrator）
        out_dir:             输出目录（clips/）
        clip_tail_padding_s: 每段末尾多切一小截（比讲解稍长），供 draft_composer.crop_video 兜底
        resume:              True 时磁盘已有对应 mp4 就跳过（供 --skip-cut / 断点续跑）
    """
    if not mp4_path.exists():
        raise SystemExit(f"源视频不存在：{mp4_path}")
    if not scenes:
        raise SystemExit("scenes 为空，无法切段。")

    out_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []

    for scene in scenes:
        scene_id = str(scene["id"])
        start_s = float(scene["video_start_s"])
        end_s = float(scene["video_end_s"]) + clip_tail_padding_s
        if end_s <= start_s:
            raise SystemExit(
                f"场景 {scene_id} 时间段非法：{start_s:.2f}s → {end_s:.2f}s"
            )

        out_path = out_dir / f"{scene_id}.mp4"
        if resume and out_path.exists() and out_path.stat().st_size > 1024:
            logger.info("video_cut.reuse", scene=scene_id, path=str(out_path))
            clip_paths.append(out_path)
            continue

        cmd = [
            _FFMPEG_EXE, "-y",
            "-ss", f"{start_s:.3f}",     # 输入前 -ss：快速 seek，速度快；对操作演示视频精度够用
            "-i", str(mp4_path),
            "-t", f"{end_s - start_s:.3f}",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",
            str(out_path),
        ]
        logger.info(
            "video_cut.ffmpeg",
            scene=scene_id,
            start_s=round(start_s, 2),
            duration_s=round(end_s - start_s, 2),
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not out_path.exists():
            raise SystemExit(
                f"ffmpeg 切段失败（{scene_id}）：\n"
                f"stderr:\n{(result.stderr or '')[-1500:]}"
            )
        clip_paths.append(out_path)

    logger.info("step:video_cut.done", clip_count=len(clip_paths))
    return clip_paths
