"""6种工作流的执行器封装
通过子进程调用原 make_*.py 脚本，使用 -y 自动确认，实时捕获输出到日志
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .task_manager import TaskContext
from .schemas import WorkflowType

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 脚本入口与工作流映射
SCRIPT_MAP = {
    WorkflowType.TOPIC: "make_video.py",
    WorkflowType.MEME: "make_meme_video.py",
    WorkflowType.CAROUSEL: "make_carousel_video.py",
    WorkflowType.CODE_WALK: "make_code_walk.py",
    WorkflowType.DOC_VIDEO: "make_doc_video.py",
    WorkflowType.NARRATION: "make_narration_video.py",
}


def _build_cmd(script_name: str, params: dict[str, Any]) -> list[str]:
    """根据参数构建命令行参数列表"""
    cmd = [sys.executable, str(PROJECT_ROOT / script_name), "-y"]

    # 通用参数
    auto = params.get("auto_confirm", True)
    dry_run = params.get("dry_run", False)
    if dry_run:
        cmd.append("--dry-run")

    if script_name == "make_video.py":
        cmd += ["--topic", params["topic"]]
        if params.get("brief"):
            cmd += ["--brief", params["brief"]]
        if params.get("style"):
            cmd += ["--style", params["style"]]
        if params.get("scenes"):
            cmd += ["--scenes", str(params["scenes"])]
        if params.get("speaker"):
            cmd += ["--speaker", params["speaker"]]
        if params.get("resume_latest"):
            cmd.append("--resume-latest")
        if params.get("skip_llm"):
            cmd.append("--skip-llm")
        if params.get("skip_image"):
            cmd.append("--skip-image")
        if params.get("skip_tts"):
            cmd.append("--skip-tts")
        if params.get("skip_titles"):
            cmd.append("--skip-titles")

    elif script_name == "make_meme_video.py":
        # 处理多图上传
        if params.get("uploaded_images"):
            import shutil
            import tempfile
            tmp_dir = Path(tempfile.mkdtemp(prefix="meme_imgs_", dir=str(PROJECT_ROOT / "uploads")))
            for idx, img_path in enumerate(params["uploaded_images"]):
                src = Path(img_path)
                if src.exists():
                    ext = src.suffix
                    dst = tmp_dir / f"{idx+1:04d}{ext}"
                    shutil.copy2(src, dst)
            cmd += ["--source", str(tmp_dir)]
        elif params.get("source"):
            cmd += ["--source", params["source"]]
        if params.get("bgm"):
            cmd += ["--bgm", params["bgm"]]
        if params.get("fit_to_bgm", True):
            cmd.append("--fit-to-bgm")
        if params.get("range"):
            cmd += ["--range", params["range"]]
        if params.get("sort"):
            cmd += ["--sort", params["sort"]]
        if params.get("count"):
            cmd += ["--count", str(params["count"])]
        # meme脚本默认方屏，使用--canvas参数
        canvas_size = params.get("canvas_w", 1080)
        cmd += ["--canvas", str(canvas_size)]
        if params.get("fit_mode"):
            cmd += ["--fit-mode", params["fit_mode"]]
        if params.get("movement"):
            cmd.append("--movement")
        if params.get("recursive"):
            cmd.append("--recursive")
        if params.get("bgm_volume") is not None:
            cmd += ["--bgm-volume", str(params["bgm_volume"])]
        if params.get("seconds_per_image"):
            cmd += ["--seconds", str(params["seconds_per_image"])]

    elif script_name == "make_carousel_video.py":
        # 处理多图上传：把上传的图片复制到临时目录，作为source
        if params.get("uploaded_images"):
            import shutil
            import tempfile
            tmp_dir = Path(tempfile.mkdtemp(prefix="carousel_imgs_", dir=str(PROJECT_ROOT / "uploads")))
            for idx, img_path in enumerate(params["uploaded_images"]):
                src = Path(img_path)
                if src.exists():
                    ext = src.suffix
                    dst = tmp_dir / f"{idx+1:04d}{ext}"
                    shutil.copy2(src, dst)
            cmd += ["--source", str(tmp_dir)]
        elif params.get("source"):
            cmd += ["--source", params["source"]]
        if params.get("bgm"):
            cmd += ["--bgm", params["bgm"]]
        if params.get("fit_to_bgm", True):
            cmd.append("--fit-to-bgm")
        if params.get("seconds_per_card"):
            cmd += ["--seconds-per-card", str(params["seconds_per_card"])]
        if params.get("duration"):
            cmd += ["--duration", str(params["duration"])]
        if params.get("canvas_w"):
            cmd += ["--canvas-w", str(params["canvas_w"])]
        if params.get("canvas_h"):
            cmd += ["--canvas-h", str(params["canvas_h"])]
        if params.get("cards_visible"):
            cmd += ["--cards-visible", str(params["cards_visible"])]
        if params.get("bg_color"):
            cmd += ["--bg-color", params["bg_color"]]
        if params.get("direction"):
            cmd += ["--direction", params["direction"]]
        if params.get("bg_blur"):
            cmd.append("--bg-blur")
        if params.get("card_radius"):
            cmd += ["--card-radius", str(params["card_radius"])]
        if params.get("card_gap"):
            cmd += ["--card-gap", str(params["card_gap"])]
        if params.get("strip_height"):
            cmd += ["--strip-height", str(params["strip_height"])]
        if params.get("no_text"):
            cmd.append("--no-text")
        if params.get("title_size_px"):
            cmd += ["--title-size-px", str(params["title_size_px"])]
        if params.get("subtitle_size_px"):
            cmd += ["--subtitle-size-px", str(params["subtitle_size_px"])]
        if params.get("bgm_volume") is not None:
            cmd += ["--bgm-volume", str(params["bgm_volume"])]
        if params.get("count"):
            cmd += ["--count", str(params["count"])]
        if params.get("sort"):
            cmd += ["--sort", params["sort"]]
        if params.get("recursive"):
            cmd.append("--recursive")
        # data JSON 需要写到临时文件传递
        if params.get("data"):
            import json
            import tempfile
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8",
                dir=str(PROJECT_ROOT / "uploads"),
            )
            json.dump(params["data"], tmp, ensure_ascii=False, indent=2)
            tmp.close()
            cmd += ["--data", tmp.name]

    elif script_name == "make_code_walk.py":
        cmd += ["--project", params["project"]]
        if params.get("brief"):
            cmd += ["--brief", params["brief"]]
        if params.get("scenes"):
            cmd += ["--scenes", str(params["scenes"])]
        if params.get("dev_port"):
            cmd += ["--dev-port", str(params["dev_port"])]
        if params.get("skip_dev_server"):
            cmd.append("--skip-dev-server")
        if params.get("speaker"):
            cmd += ["--speaker", params["speaker"]]
        if params.get("resume_latest"):
            cmd.append("--resume-latest")
        if params.get("skip_scan"):
            cmd.append("--skip-scan")
        if params.get("skip_llm"):
            cmd.append("--skip-llm")
        if params.get("skip_shots"):
            cmd.append("--skip-shots")
        if params.get("skip_tts"):
            cmd.append("--skip-tts")

    elif script_name == "make_doc_video.py":
        cmd += ["--pdf", params["pdf"]]
        cmd += ["--mp4", params["mp4"]]
        if params.get("brief"):
            cmd += ["--brief", params["brief"]]
        if params.get("scenes"):
            cmd += ["--scenes", str(params["scenes"])]
        if params.get("speaker"):
            cmd += ["--speaker", params["speaker"]]
        if params.get("no_vision"):
            cmd.append("--no-vision")
        if params.get("resume_latest"):
            cmd.append("--resume-latest")
        if params.get("skip_parse"):
            cmd.append("--skip-parse")
        if params.get("skip_vision"):
            cmd.append("--skip-vision")
        if params.get("skip_llm"):
            cmd.append("--skip-llm")
        if params.get("skip_cut"):
            cmd.append("--skip-cut")
        if params.get("skip_tts"):
            cmd.append("--skip-tts")

    elif script_name == "make_narration_video.py":
        cmd += ["--mp4", params["mp4"]]
        if params.get("brief"):
            cmd += ["--brief", params["brief"]]
        if params.get("scenes"):
            cmd += ["--scenes", str(params["scenes"])]
        if params.get("speaker"):
            cmd += ["--speaker", params["speaker"]]
        if params.get("no_vision"):
            cmd.append("--no-vision")
        if params.get("resume_latest"):
            cmd.append("--resume-latest")
        if params.get("skip_parse"):
            cmd.append("--skip-parse")
        if params.get("skip_vision"):
            cmd.append("--skip-vision")
        if params.get("skip_llm"):
            cmd.append("--skip-llm")
        if params.get("skip_cut"):
            cmd.append("--skip-cut")
        if params.get("skip_tts"):
            cmd.append("--skip-tts")

    return cmd


def _run_subprocess(ctx: TaskContext, cmd: list[str]):
    """启动子进程并实时捕获输出到ctx日志"""
    ctx.log(f"执行命令: {' '.join(cmd)}", "debug")
    ctx.set_progress(5, "启动流水线")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    )

    step_markers = [
        ("Step 0", 15, "生成文案/解析素材"),
        ("Step 1", 35, "拆分场景/理解内容"),
        ("Step 2", 55, "生成图片/渲染画面"),
        ("Step 3", 75, "AI配音"),
        ("Step 4", 90, "组装剪映草稿"),
        ("草稿已保存", 98, "保存草稿"),
    ]

    try:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            if ctx.is_cancelled():
                process.terminate()
                ctx.log("任务已被取消", "warning")
                return
            line = line.rstrip()
            if not line:
                continue
            ctx.log(line)

            # 根据输出关键词更新进度
            for marker, prog, step_name in step_markers:
                if marker in line:
                    ctx.set_progress(prog, step_name)
                    break

            # 检测草稿路径
            if "草稿已保存到" in line or "draft saved" in line.lower():
                import re
                m = re.search(r'([A-Za-z]:[\\/][^\s\n]+)', line)
                if m:
                    ctx.draft_path = m.group(1)
            elif "草稿文件夹：" in line:
                ctx.draft_path = line.split("草稿文件夹：")[-1].strip()

        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"子进程退出码: {process.returncode}")

    finally:
        try:
            process.terminate()
        except Exception:
            pass


def run_topic(ctx: TaskContext):
    """主题 → 历史/人物介绍片"""
    ctx.log("🎬 启动：主题AI生成片工作流", "info")
    cmd = _build_cmd("make_video.py", ctx.params)
    _run_subprocess(ctx, cmd)


def run_meme(ctx: TaskContext):
    """本地图片 → 梗图/图集片"""
    ctx.log("🎬 启动：梗图/图集视频工作流", "info")
    cmd = _build_cmd("make_meme_video.py", ctx.params)
    _run_subprocess(ctx, cmd)


def run_carousel(ctx: TaskContext):
    """卡片轮播 + BGM 短视频"""
    ctx.log("🎬 启动：卡片轮播短视频工作流", "info")
    cmd = _build_cmd("make_carousel_video.py", ctx.params)
    _run_subprocess(ctx, cmd)


def run_code_walk(ctx: TaskContext):
    """前端项目 → 代码走读片"""
    ctx.log("🎬 启动：代码走读视频工作流", "info")
    cmd = _build_cmd("make_code_walk.py", ctx.params)
    _run_subprocess(ctx, cmd)


def run_doc_video(ctx: TaskContext):
    """PDF+视频 → 需求讲解片"""
    ctx.log("🎬 启动：需求讲解视频工作流", "info")
    cmd = _build_cmd("make_doc_video.py", ctx.params)
    _run_subprocess(ctx, cmd)


def run_narration(ctx: TaskContext):
    """视频 → 录屏讲解片"""
    ctx.log("🎬 启动：录屏讲解视频工作流", "info")
    cmd = _build_cmd("make_narration_video.py", ctx.params)
    _run_subprocess(ctx, cmd)


def register_all_workflows(task_mgr):
    """向任务管理器注册所有工作流"""
    task_mgr.register_workflow(WorkflowType.TOPIC, run_topic)
    task_mgr.register_workflow(WorkflowType.MEME, run_meme)
    task_mgr.register_workflow(WorkflowType.CAROUSEL, run_carousel)
    task_mgr.register_workflow(WorkflowType.CODE_WALK, run_code_walk)
    task_mgr.register_workflow(WorkflowType.DOC_VIDEO, run_doc_video)
    task_mgr.register_workflow(WorkflowType.NARRATION, run_narration)
