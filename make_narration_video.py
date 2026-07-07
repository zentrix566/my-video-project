"""本地 mp4 → 剪映草稿（一键流水线）—— 视频讲解模式（无 PDF）。

给一段屏幕录制/操作演示视频，自动补齐 AI 配音 + 字幕，原视频音频会被剥离。
和 make_doc_video.py 的区别：不需要 PDF 业务背景，讲稿完全靠视频内容 +（可选） --brief。

用法：
    python make_narration_video.py --mp4 "path/to/screen.mp4"
    python make_narration_video.py --mp4 ... --brief "讲讲这个工具怎么装插件" --scenes 6
    python make_narration_video.py --mp4 ... --no-vision -y
    python make_narration_video.py --mp4 ... --resume-latest --skip-parse --skip-vision --skip-llm

流水线阶段（六步）：
    Step 0  视频解析          pipeline.doc_parse         (ffmpeg probe + 均匀抽帧)
    Step 1  逐帧视觉理解      pipeline.video_understand  (豆包视觉大模型；--no-vision 可兜底)
    Step 2  讲稿 + 切段时间戳 pipeline.narration_narrator(文本 LLM，无 PDF 背景)
    Step 3  ffmpeg 切段       pipeline.video_cut         (本地 ffmpeg，-an 剥原音)
    Step 4  TTS 配音          pipeline.tts               (字节 Seed-TTS，复用)
    Step 5  剪映草稿          pipeline.draft_composer    (pyJianYingDraft，复用)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from pipeline.helpers import (
    PipelineLogger, find_latest_output_dir, load_env, prepare_output_dir,
    required_env, safe_slug,
)
from pipeline.styles import list_style_names, load_style
from pipeline.doc_parse import (
    probe_video, extract_frames, detect_scene_changes,
    save_doc_content, load_doc_content,
)
from pipeline.video_understand import (
    caption_frames, load_frame_captions, write_empty_captions,
)
from pipeline.narration_narrator import (
    generate_narration_scenes, load_generated_scenes,
)
from pipeline.video_cut import cut_clips
from pipeline.tts import synthesize_audio, synthesize_audio_per_sentence
from pipeline.draft_composer import JianyingDraftBuilder, SegmentInfo, SentenceInfo
from pipeline.camera import CAMERA_PRESETS


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
# 路径默认值集中在 pipeline.paths；换机器只改 .env 里的 JIANYING_DRAFT_FOLDER / OUTPUTS_ROOT
from pipeline.paths import (
    JIANYING_DRAFT_FOLDER as DEFAULT_DRAFT_FOLDER,
    LOG_DIR as DEFAULT_LOG_DIR,
    NARRATION_DIR as DEFAULT_OUTPUT_ROOT,
)


# ============================================================
#  交互式确认卡点
# ============================================================

def _prompt_confirm(prompt: str, file_path: Path) -> str:
    while True:
        try:
            choice = input(f"{prompt} [Y=继续 / n=中止 / e=编辑后继续]: ").strip().lower()
        except EOFError:
            print("(非交互环境，默认中止；如需自动化请加 --yes)")
            return "n"
        if choice in ("", "y", "yes"):
            return "y"
        if choice in ("n", "no"):
            return "n"
        if choice in ("e", "edit"):
            print(f"\n  请用编辑器打开并修改：\n    {file_path}")
            print("  修改保存后按回车继续（也可以现在按 Ctrl+C 完全中止）...")
            try:
                input()
            except EOFError:
                return "n"
            return "e"
        print("  请输入 y / n / e（回车默认 y）")


def _preview_video_content(doc_content: dict[str, Any]) -> None:
    """Step 0 后的预览。narration 模式下 pdf 字段是空 stub，不打印。"""
    video = doc_content["video"]
    frames = doc_content["frames"]
    print("\n" + "─" * 60)
    print("  Step 0 视频解析（无 PDF）")
    print("─" * 60)
    print(f"  MP4:         {video['path']}")
    print(f"    时长:      {video['duration_s']}s   分辨率: {video['width']}x{video['height']}   fps: {video['fps']}")
    print(f"  抽帧:        {len(frames)} 张 → 时间点 {[round(f['timestamp_s'], 1) for f in frames]}")
    print("─" * 60)


def _preview_scenes(scenes_data: dict[str, Any]) -> None:
    scenes = scenes_data["scenes"]
    duration_s = scenes_data.get("video_duration_s", 0)
    print("\n" + "─" * 60)
    print(f"  Step 2 讲稿 + 视频切段规范     共 {len(scenes)} 段  |  视频总长 {duration_s}s")
    print(f"  视频主标题: {scenes_data.get('title', '')}")
    if scenes_data.get("subtitle"):
        print(f"  副标题:     {scenes_data['subtitle']}")
    print("─" * 60)
    for i, s in enumerate(scenes, 1):
        start = s["video_start_s"]
        end = s["video_end_s"]
        seg_len = round(end - start, 1)
        n_chars = len(s["narration"])
        print(
            f"\n  [{i:>2}] {s['id']}  "
            f"[{start:>5.1f}s → {end:>5.1f}s = {seg_len}s]  narration {n_chars}字"
        )
        print(f"       {s['narration']}")
    print("\n" + "─" * 60)


# ============================================================
#  费用预估（无 PDF）
# ============================================================

def estimate_cost(scene_count: int, frame_count: int) -> str:
    """纯视频流水线预估：视频解析本地 0 元 + 视觉 LLM + 文本 LLM + TTS。"""
    _LLM_TEXT_PER_KTOKEN = 0.008        # ark-code-latest
    _LLM_VISION_PER_KTOKEN = 0.015      # 视觉模型单价（随模型浮动，供量级参考）
    _TTS_PER_CHAR = 0.0001              # seed-tts-2.0
    _IMAGE_TOKENS_EACH = 1500           # 720p 单帧粗估

    # Step 1 视觉：输入 = system prompt + N 张图，输出 ≈ 每帧 80 tok
    step1_in = 1500 + frame_count * _IMAGE_TOKENS_EACH
    step1_out = frame_count * 80 + 200
    step1_cost = (step1_in + step1_out) / 1000 * _LLM_VISION_PER_KTOKEN

    # Step 2 文本讲稿：输入 = 帧描述 + brief，输出 ≈ N * 200 tok
    step2_in = frame_count * 100 + 1500
    step2_out = scene_count * 200
    step2_cost = (step2_in + step2_out) / 1000 * _LLM_TEXT_PER_KTOKEN

    # TTS：每段旁白 ~30 字
    tts_chars = scene_count * 30
    tts_cost = tts_chars * _TTS_PER_CHAR

    total = step1_cost + step2_cost + tts_cost

    return "\n".join([
        "========== 费用预估 (粗略) ==========",
        "  Step 0  视频解析        本地 ffmpeg                              →  0 元",
        f"  Step 1  视觉大模型      输入 ~{step1_in} tok + {frame_count} 帧图      →  约 {step1_cost:.4f} 元",
        f"  Step 2  文本讲稿 LLM    输入 ~{step2_in} tok / 输出 ~{step2_out} tok    →  约 {step2_cost:.4f} 元",
        "  Step 3  ffmpeg 切段     本地                                    →  0 元",
        f"  Step 4  TTS 旁白        约 {tts_chars} 字 (Seed-TTS 2.0)           →  约 {tts_cost:.4f} 元",
        "  Step 5  剪映合成        本地 pyJianYingDraft                    →  0 元",
        "  ---------------------------",
        f"  合计:  约 {total:.4f} 元",
        "  提示: 用 --no-vision 兜底可省掉 Step 1 视觉大模型费用",
        "=========================================",
    ])


# ============================================================
#  Step 5: 剪映合成 —— 保留原 mp4 时长，视频段无运镜
# ============================================================

def _compose_jianying_draft_for_narration(
    project_name: str,
    scenes: list[dict[str, Any]],
    clip_paths: list[str],
    scene_audio_groups: list[dict],
    style: dict[str, Any],
    logger: PipelineLogger,
    *,
    draft_folder_path: str,
    fade_transition: bool = True,
    tts_overshoot: str = "speed_audio",
) -> str:
    """录屏讲解模式专用合成。

    与 doc_video 完全相同的关键点：
        1. preserve_video_duration=True —— 视频段保留原时长（默认 speed_audio 策略：TTS 超长时
           音频加速卡进视频段，视频全程原速；选 slow_video 回退旧行为）。
        2. add_video_movement=False    —— 录屏本身有内容变化，不叠加运镜。
        3. 逐句 TTS —— 每段 narration 按 LLM 给的 sentences 逐句合成（或按标点兜底），
           用真实音频时长驱动字幕，消除字符比例估算的漂移。
    """
    jianying_cfg = style.get("jianying", {}) or {}

    unique = bool(jianying_cfg.get("unique_draft_name", True))
    draft_name = project_name
    if unique:
        draft_name = f"{draft_name}_{time.strftime('%Y%m%d_%H%M%S')}"

    builder = JianyingDraftBuilder(
        draft_name=draft_name,
        draft_folder_path=draft_folder_path,
        on_progress=lambda msg, pct: logger.info("jianying.progress", message=msg, pct=pct),
        add_image_movement=False,
        add_video_movement=False,
        split_subtitles=True,   # 无 sentences 时的兜底路径仍走按字符比例切
        fade_transition=bool(jianying_cfg.get("fade_transition", True)) and fade_transition,
        camera_effects=CAMERA_PRESETS.get("pan"),   # 兜底占位，不会被用到（movement 全关）
        canvas_width=style.get("canvas_width"),
        canvas_height=style.get("canvas_height"),
        subtitle_preset=jianying_cfg.get("subtitle_preset"),
        transition_duration=jianying_cfg.get("transition_duration", "0.4s"),
        preserve_video_duration=True,
        tts_overshoot=tts_overshoot,
    )

    segments: list[SegmentInfo] = []
    for s, clip, group in zip(scenes, clip_paths, scene_audio_groups):
        sentence_infos = [
            SentenceInfo(text=item["text"], audio_path=item["audio_path"])
            for item in group.get("sentences", [])
        ]
        # audio_path 传第一句作为兼容占位；draft_composer 优先用 sentences 列表
        first_audio = sentence_infos[0].audio_path if sentence_infos else ""
        segments.append(SegmentInfo(
            subtitle=s["narration"],
            audio_path=first_audio,
            media_path=clip,
            sentences=sentence_infos or None,
        ))

    report = builder.preflight(segments)
    if not report.passed:
        for err in report.errors:
            logger.error("jianying.preflight.fail", error=err)
        raise SystemExit("剪映草稿预检失败 — 详见上方错误。")
    for w in report.warnings:
        logger.warn("jianying.preflight.warn", warning=w)

    logger.info(
        "step:jianying.start",
        segments=len(scenes),
        draft_name=draft_name,
        canvas=f"{report.estimated_canvas}",
    )
    result = builder._build(segments)
    logger.info("step:jianying.done", result=result)
    return f"{result}\n  草稿位置: {draft_folder_path}/{draft_name}"


# ============================================================
#  主流程
# ============================================================

def run(args: argparse.Namespace) -> int:
    load_env(ENV_FILE)

    mp4_path = Path(args.mp4).resolve()
    if not mp4_path.exists() or mp4_path.suffix.lower() not in (".mp4", ".mov", ".m4v"):
        raise SystemExit(f"--mp4 路径不存在或不是 mp4/mov: {mp4_path}")

    style = load_style(args.style)
    draft_folder_path = args.draft_folder or style.get("draft_folder_path") or DEFAULT_DRAFT_FOLDER

    logger = PipelineLogger(DEFAULT_LOG_DIR)
    if args.output_dir and args.resume_latest:
        raise SystemExit("--output-dir 与 --resume-latest 不能同时使用")

    project_slug = safe_slug(mp4_path.stem, fallback="narration")
    if args.output_dir:
        output_dir = Path(args.output_dir)
        for child in ("audio", "responses", "frames", "clips"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)
    elif args.resume_latest:
        latest = find_latest_output_dir(project_slug, DEFAULT_OUTPUT_ROOT)
        if latest is None:
            raise SystemExit(
                f"--resume-latest 找不到已有 run 目录：{DEFAULT_OUTPUT_ROOT / project_slug}"
            )
        output_dir = latest
        for child in ("audio", "responses", "frames", "clips"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)
        print(f"\n  >> 复用最新 run 目录: {output_dir}")
    else:
        output_dir = prepare_output_dir(project_slug, DEFAULT_OUTPUT_ROOT)
        for child in ("frames", "clips"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)

    # ---- 场景数：优先命令行；否则用视频时长 / 6，夹到 [4, 10] ----
    doc_content_path = output_dir / "doc_content.json"
    duration_probe_s: float | None = None
    if args.skip_parse and doc_content_path.exists():
        cached = json.loads(doc_content_path.read_text(encoding="utf-8"))
        duration_probe_s = float(cached["video"]["duration_s"])

    if args.scenes:
        scene_count = int(args.scenes)
    else:
        if duration_probe_s is None:
            duration_probe_s = probe_video(mp4_path).duration_s
        # 每段目标 ~5 秒（配合镜头检测切段）
        scene_count = max(4, min(20, round(duration_probe_s / 5)))

    # ---- 视觉模型解析 ----
    if args.vision_model == "pro":
        vision_model = os.environ.get("VISION_MODEL_PRO") or os.environ.get("VISION_MODEL")
        if not vision_model:
            print("  [!] --vision-model pro 但 VISION_MODEL_PRO 未配置，回退到 VISION_MODEL")
            vision_model = os.environ.get("VISION_MODEL")
    else:
        vision_model = os.environ.get("VISION_MODEL")

    # 抽帧数：优先 --frames；否则按 frame_interval 自动算
    if args.frames is not None:
        frame_count = int(args.frames)
    else:
        if duration_probe_s is None:
            duration_probe_s = probe_video(mp4_path).duration_s
        import math
        frame_count = max(6, math.ceil(duration_probe_s / args.frame_interval))

    print(estimate_cost(scene_count, frame_count))
    if args.dry_run:
        if duration_probe_s is None:
            duration_probe_s = probe_video(mp4_path).duration_s
        print(f"\n  (视频时长 {duration_probe_s} s，目标 {scene_count} 段，抽 {frame_count} 帧)")
        if args.frames is None:
            print("  抽帧数按 duration / 7 自动算；想少花钱可加 --frames 8，想更精细可加 --frames 20")
        print("\n--dry-run 模式，仅估算未做任何 API 调用。")
        return 0

    api_key = required_env("AGENT_API_KEY", ENV_FILE)

    print("\n" + "=" * 60)
    print("  「MP4 → 剪映视频」录屏讲解流水线（无 PDF）")
    print("=" * 60)
    print(f"  mp4:            {mp4_path}")
    print(f"  brief:          {args.brief or '(无)'}")
    print(f"  style:          {args.style}   ({style.get('description', '')})")
    print(f"  scenes:         {scene_count}    (frames={frame_count})")
    print(f"  output_dir:     {output_dir}")
    print(f"  draft_folder:   {draft_folder_path}")
    print(f"  log:            {logger.log_path}")
    print("=" * 60)

    logger.info(
        "narration_video.start",
        mp4=str(mp4_path), style=args.style,
        scene_count=scene_count, frame_count=frame_count,
        output_dir=str(output_dir),
    )
    t_start = time.time()

    try:
        # ---- Step 0: 视频抽帧（无 PDF）----
        frames_dir = output_dir / "frames"
        if args.skip_parse and doc_content_path.exists():
            doc_content = load_doc_content(doc_content_path)
            logger.info("skip.doc_parse.reuse", path=str(doc_content_path))
            if "scene_changes" not in doc_content:
                doc_content["scene_changes"] = []
            for f in doc_content.get("frames", []):
                f.setdefault("is_scene_change", False)
        else:
            video_meta = probe_video(mp4_path, logger=logger)

            # 镜头切换检测
            if args.no_scene_detect:
                scene_changes: list[float] = []
                print("  [Step 0] 已关闭镜头切换检测")
            else:
                scene_changes = detect_scene_changes(
                    mp4_path, threshold=args.scene_threshold, logger=logger,
                )
                print(f"  [Step 0] 检测到 {len(scene_changes)} 个镜头切换点")

            frame_result = extract_frames(
                mp4_path, frames_dir,
                n=frame_count,
                duration_s=video_meta.duration_s, logger=logger,
                frame_interval_s=None if args.frames else args.frame_interval,
                scene_changes=scene_changes if scene_changes else None,
            )
            frame_paths = frame_result.paths
            frame_timestamps = frame_result.timestamps
            save_doc_content(
                doc_content_path, None, video_meta, frame_result,
            )
            doc_content = load_doc_content(doc_content_path)

        print(f"\n  [Step 0] 视频解析完成 → {doc_content_path.name}")

        # ---- 卡点 1：视频/帧概览 ----
        if not args.yes:
            _preview_video_content(doc_content)
            choice = _prompt_confirm(
                "视频抽帧结果是否 OK？（下一步调视觉大模型）",
                doc_content_path,
            )
            if choice == "n":
                logger.info("pipeline.abort", stage="after_parse")
                print("\n已中止。可 --resume-latest --skip-parse 续跑。")
                return 0
            if choice == "e":
                doc_content = load_doc_content(doc_content_path)

        # ---- Step 1: 视觉大模型逐帧描述（可用 --no-vision / --manual-captions 兜底） ----
        captions_path = output_dir / "frame_captions.json"
        if args.skip_vision and captions_path.exists():
            frame_captions = load_frame_captions(captions_path)
            logger.info("skip.video_understand.reuse", path=str(captions_path))
        elif args.no_vision or args.manual_captions:
            frame_paths = [Path(f["path"]) for f in doc_content["frames"]]
            frame_timestamps = [float(f["timestamp_s"]) for f in doc_content["frames"]]
            frame_captions = write_empty_captions(
                frame_paths, frame_timestamps, captions_path,
                manual_stub=bool(args.manual_captions),
                logger=logger,
            )
            if args.manual_captions:
                print("\n  [Step 1] 已生成 frame_captions.json 骨架（含帧时间戳，caption 待填）。")
                print(f"    请打开：{captions_path}")
                print(f"    对照抽帧图：{frames_dir}")
                print("    每帧 caption 填 20-50 字描述屏幕上在做什么，改完保存后运行：")
                print("      python make_narration_video.py --mp4 ... "
                      "--resume-latest --skip-parse --skip-vision -y")
                logger.info("pipeline.pause", stage="manual_captions_stub", path=str(captions_path))
                return 0
            print("\n  [Step 1] 已跳过视觉大模型 (--no-vision)，将用空 caption 交给 LLM 写讲稿。")
        else:
            frame_paths = [Path(f["path"]) for f in doc_content["frames"]]
            frame_timestamps = [float(f["timestamp_s"]) for f in doc_content["frames"]]
            is_scene_arr = [bool(f.get("is_scene_change")) for f in doc_content["frames"]]
            frame_captions = caption_frames(
                None,
                frame_paths, frame_timestamps,
                pdf_excerpt=(args.brief or ""),
                output_dir=output_dir,
                logger=logger,
                model=vision_model,
                is_scene_change=is_scene_arr,
                context_label="视频简介(brief)",
                role_label="操作演示视频讲解编剧",
            )
        print(
            f"\n  [Step 1] 视觉理解完成 → {captions_path.name}"
            f"    ({len(frame_captions.get('frames', []))} 帧描述)"
        )
        if frame_captions.get("video_summary"):
            print(f"    视频总结: {frame_captions['video_summary']}")

        # ---- Step 2: 讲稿 + 切段时间戳（无 PDF 版本） ----
        scenes_path = output_dir / "generated_scenes.json"
        if args.skip_llm and scenes_path.exists():
            scenes_data = load_generated_scenes(scenes_path)
            logger.info(
                "skip.narration_narrator.reuse",
                path=str(scenes_path),
                count=len(scenes_data.get("scenes", [])),
            )
        else:
            scenes_data = generate_narration_scenes(
                api_key,
                doc_content["video"], frame_captions,
                scene_count=scene_count,
                brief=args.brief or "",
                output_dir=output_dir,
                logger=logger,
                scene_changes=doc_content.get("scene_changes") or [],
            )
        scenes: list[dict[str, Any]] = scenes_data["scenes"]
        print(f"\n  [Step 2] 讲稿 + 切段 {len(scenes)} 段已就绪 → {scenes_path.name}")

        # ---- 卡点 2：讲稿 + 切段确认 ----
        if not args.yes and not args.skip_cut:
            _preview_scenes(scenes_data)
            choice = _prompt_confirm(
                "讲稿 + 视频切段方案是否 OK？（下一步切 mp4）",
                scenes_path,
            )
            if choice == "n":
                logger.info("pipeline.abort", stage="after_scenes")
                print("\n已中止。修改后可 --resume-latest --skip-llm 从切段开始续跑。")
                return 0
            if choice == "e":
                scenes_data = load_generated_scenes(scenes_path)
                scenes = scenes_data["scenes"]
                print(f"  已重新加载 {scenes_path.name}（{len(scenes)} 场景），继续 Step 3。")

        # ---- Step 3: ffmpeg 切段 ----
        clips_dir = output_dir / "clips"
        if args.skip_cut:
            clip_paths: list[str] = []
            for s in scenes:
                p = clips_dir / f"{s['id']}.mp4"
                if not p.exists():
                    raise SystemExit(f"--skip-cut 但视频片段不存在: {p}")
                clip_paths.append(str(p))
            logger.info("skip.video_cut", count=len(clip_paths))
        else:
            clip_paths = [
                str(p)
                for p in cut_clips(
                    mp4_path, scenes, clips_dir,
                    logger=logger, resume=not args.no_resume,
                    clip_tail_padding_s=0.0,
                )
            ]
        print(f"\n  [Step 3] 视频切段 {len(clip_paths)} 段 → {clips_dir}")

        # ---- Step 4: TTS 配音（逐句合成，让下游字幕严格对齐真实发音时长） ----
        tts_cfg = style.get("tts", {}) or {}
        audio_fmt = tts_cfg.get("format", "mp3")
        if args.skip_tts:
            # 复用磁盘上的 audio/<scene_id>/NN.<fmt>，同时兼容旧目录（audio/<scene_id>.<fmt>）
            scene_audio_groups: list[dict] = []
            for s in scenes:
                scene_dir = output_dir / "audio" / s["id"]
                if scene_dir.exists():
                    sub_paths = sorted(scene_dir.glob(f"*.{audio_fmt}"))
                    if not sub_paths:
                        raise SystemExit(f"--skip-tts 但 {scene_dir} 下没有音频")
                    from pipeline.helpers import smart_split as _split, strip_punctuation as _strip
                    raw_sentences = [_strip(x) or x for x in _split(s["narration"])]
                    if len(sub_paths) != len(raw_sentences):
                        logger.warn(
                            "skip_tts.sentence_count_mismatch",
                            scene_id=s["id"], mp3=len(sub_paths), sentences=len(raw_sentences),
                        )
                    sentence_records = [
                        {"text": raw_sentences[i] if i < len(raw_sentences) else "",
                         "audio_path": str(p)}
                        for i, p in enumerate(sub_paths)
                    ]
                    scene_audio_groups.append({
                        "id": s["id"], "narration": s["narration"],
                        "sentences": sentence_records,
                    })
                else:
                    # 兼容旧结构 audio/<scene_id>.mp3
                    legacy = output_dir / "audio" / f"{s['id']}.{audio_fmt}"
                    if not legacy.exists():
                        raise SystemExit(f"--skip-tts 但音频不存在: {scene_dir} 或 {legacy}")
                    scene_audio_groups.append({
                        "id": s["id"], "narration": s["narration"],
                        "sentences": [{"text": s["narration"], "audio_path": str(legacy)}],
                    })
            logger.info("skip.tts", count=sum(len(g["sentences"]) for g in scene_audio_groups))
        else:
            scene_audio_groups = synthesize_audio_per_sentence(
                api_key, scenes, output_dir, logger,
                url=tts_cfg.get("url"),
                resource_id=args.resource_id or tts_cfg.get("resource_id"),
                speaker=args.speaker or tts_cfg.get("speaker"),
                audio_format=audio_fmt,
                sample_rate=int(tts_cfg.get("sample_rate", 24000)),
            )
        n_sentences = sum(len(g["sentences"]) for g in scene_audio_groups)
        print(f"\n  [Step 4] TTS 配音 {len(scene_audio_groups)} 段 · 共 {n_sentences} 句子句音频")

        # ---- Step 5: 剪映合成 ----
        if args.skip_jianying:
            logger.info("skip.jianying")
            draft_result = "(--skip-jianying)"
        else:
            draft_project_name = scenes_data.get("title") or project_slug
            draft_result = _compose_jianying_draft_for_narration(
                project_name=draft_project_name,
                scenes=scenes,
                clip_paths=clip_paths,
                scene_audio_groups=scene_audio_groups,
                style=style,
                logger=logger,
                draft_folder_path=draft_folder_path,
                fade_transition=not args.no_fade,
                tts_overshoot=args.tts_overshoot,
            )
        print(f"\n  [Step 5] 剪映草稿：\n    {draft_result}")

    except SystemExit:
        logger.error("narration_video.failed")
        print("\n流水线中断 — 请检查上方错误信息与日志文件。")
        return 1
    except Exception as exc:
        logger.error("narration_video.failed", error=str(exc))
        print(f"\n流水线异常: {exc}")
        return 1

    elapsed = round(time.time() - t_start, 1)
    logger.info("narration_video.done", elapsed_s=elapsed)

    print("\n" + "=" * 60)
    print("  流水线完成")
    print("=" * 60)
    print(f"  总耗时:  {elapsed:.1f}s")
    print(f"  输出:    {output_dir}")
    print(f"  日志:    {logger.log_path}")
    print("=" * 60)
    return 0


# ============================================================
#  CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="本地 mp4 → 录屏讲解视频剪映草稿：抽帧 → 视觉理解 → LLM 讲稿 → 切段 → 配音 → 剪映合成（无需 PDF）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mp4", type=str, required=False,
                        help="要讲解的本地视频（mp4 / mov / m4v）")
    parser.add_argument("--brief", type=str, default="",
                        help="可选：给 LLM 的背景提示，例如「讲讲这个 VSCode 插件怎么装」")

    styles = list_style_names() or ["documentary"]
    default_style = "documentary" if "documentary" in styles else styles[0]
    parser.add_argument("--style", type=str, default=default_style, choices=styles,
                        help=f"风格预设（默认 {default_style}）")
    parser.add_argument("--scenes", type=int, default=None,
                        help="场景/切段数量（默认按视频时长 / 6 自动算，夹到 4-10）")
    parser.add_argument("--frames", type=int, default=None,
                        help="送去视觉大模型的抽帧数量；不填时按视频时长 / 7 自动算（8-24 之间）。帧越多越贵越准")
    parser.add_argument("--draft-folder", type=str, default=None,
                        help=f"剪映草稿目录（默认 {DEFAULT_DRAFT_FOLDER}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做费用估算，不调 API")

    parser.add_argument("--skip-parse", action="store_true",
                        help="跳过 Step 0（复用 doc_content.json + frames/）")
    parser.add_argument("--skip-vision", action="store_true",
                        help="跳过 Step 1 视觉理解（复用 frame_captions.json）")
    parser.add_argument("--no-vision", action="store_true",
                        help="不调用视觉大模型，直接写空 caption 让 narrator 盲讲（省钱兜底）")
    parser.add_argument("--manual-captions", action="store_true",
                        help="生成 frame_captions.json 骨架供你手填 caption 后 --skip-vision 续跑")
    parser.add_argument("--skip-llm", action="store_true",
                        help="跳过 Step 2 讲稿生成（复用 generated_scenes.json）")
    parser.add_argument("--skip-cut", action="store_true",
                        help="跳过 Step 3 切段（复用 clips/*.mp4）")
    parser.add_argument("--skip-tts", action="store_true",
                        help="跳过 Step 4 配音（复用 audio/*.mp3）")
    parser.add_argument("--skip-jianying", action="store_true",
                        help="跳过 Step 5 剪映草稿合成")

    parser.add_argument("--speaker", type=str, default=None, help="覆盖 TTS 音色")
    parser.add_argument("--resource-id", type=str, default=None, help="覆盖 TTS resource_id")
    parser.add_argument("--no-resume", action="store_true",
                        help="禁用切段/配音的断点续跑（已存在文件也重跑）")
    parser.add_argument("--no-fade", action="store_true", help="禁用片段转场")
    parser.add_argument("--vision-model", type=str, default="auto",
                        choices=["auto", "pro"],
                        help="视觉模型选择：auto=用 VISION_MODEL（默认），pro=用 VISION_MODEL_PRO")
    parser.add_argument("--frame-interval", type=float, default=2.0,
                        help="均匀抽帧间隔秒数（默认 2.0；--frames 指定时优先）")
    parser.add_argument("--scene-threshold", type=float, default=0.4,
                        help="镜头切换检测阈值 0-1，越小越敏感（默认 0.4）")
    parser.add_argument("--no-scene-detect", action="store_true",
                        help="关闭镜头切换检测")
    parser.add_argument("--tts-overshoot", type=str, default="speed_audio",
                        choices=["speed_audio", "slow_video"],
                        help="TTS 超长时处理：speed_audio=加速音频保视频原速（默认），slow_video=旧行为慢放视频")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="指定输出目录（一般用于续跑）")
    parser.add_argument("--resume-latest", action="store_true",
                        help="复用该视频最近一次 run 目录（与 --output-dir 互斥）")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="关闭交互卡点，直接一路跑到底")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.mp4:
        parser.error("--mp4 是必填参数")

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
