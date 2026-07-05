"""主题 → 剪映视频（一键流水线）

    python make_video.py --topic "南明李定国"
    python make_video.py --topic "苏轼被贬黄州" --style documentary --scenes 10
    python make_video.py --topic "岳飞抗金" --style shorts --brief "重点写精忠报国"
    python make_video.py --topic "南明李定国" --dry-run          # 只做规划、估算费用
    python make_video.py --topic "南明李定国" --resume-latest --skip-llm --skip-image --skip-tts

流水线阶段：
    Step 0  主题 → 旁白稿      pipeline.topic_to_story    (LLM)
    Step 1  旁白稿 → 场景数组   pipeline.scene_split       (LLM)
    Step 2  场景 → 图片         pipeline.image_gen         (豆包 Seedream)
    Step 3  场景 → 配音         pipeline.tts               (字节 Seed-TTS)
    Step 4  合成剪映草稿        pipeline.draft_composer    (pyJianYingDraft)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pipeline.helpers import (
    PipelineLogger, find_latest_output_dir, load_env, prepare_output_dir,
    required_env, safe_slug,
)
from pipeline.styles import list_style_names, load_style
from pipeline.topic_to_story import generate_story_from_topic
from pipeline.scene_split import split_story_to_scenes
from pipeline.image_gen import generate_images
from pipeline.tts import synthesize_audio
from pipeline.draft_composer import JianyingDraftBuilder, SegmentInfo
from pipeline.camera import CAMERA_PRESETS


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "projects"
DEFAULT_LOG_DIR = ROOT / "outputs" / "logs"
DEFAULT_DRAFT_FOLDER = "D:/Program Files/JianyingPro Drafts"


# ============================================================
#  交互式确认卡点
# ============================================================

def _prompt_confirm(prompt: str, file_path: Path) -> str:
    """询问用户 [Y/n/e]，返回 'y' | 'n' | 'e'。空回车 = y。
    - y: 继续下一步
    - n: 中止流水线
    - e: 让用户手动编辑对应文件后按回车继续
    """
    while True:
        try:
            choice = input(f"{prompt} [Y=继续 / n=中止 / e=编辑后继续]: ").strip().lower()
        except EOFError:
            # 非交互环境（如管道调用），保守中止
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


def _preview_story(story_data: dict[str, str]) -> None:
    story = story_data.get("story", "")
    print("\n" + "─" * 60)
    print(f"  Step 0 生成的旁白稿  ({len(story)} 字)")
    print("─" * 60)
    print(f"  project_name: {story_data.get('project_name', '')}")
    print(f"  title:        {story_data.get('title', '')}")
    if story_data.get("author"):
        print(f"  author:       {story_data['author']}")
    print("─" * 60)
    print(story)
    print("─" * 60)


def _preview_scenes(scenes: list[dict[str, str]]) -> None:
    print("\n" + "─" * 60)
    print(f"  Step 1 场景切分   共 {len(scenes)} 个场景")
    print("─" * 60)
    for i, s in enumerate(scenes, 1):
        print(f"\n  [{i:>2}] {s['id']}")
        print(f"       旁白：{s['narration']}")
        prompt = s["image_prompt"]
        prompt_short = prompt if len(prompt) <= 110 else prompt[:107] + "..."
        print(f"       画面：{prompt_short}")
    print("\n" + "─" * 60)


# ============================================================
#  费用预估
# ============================================================

class CostEstimator:
    """Step 0 主题→稿 + Step 1 场景切分 + 图片 + TTS。"""

    _SEEDREAM_PER_IMAGE_2K = 0.15    # 元/张
    _TTS_PER_CHAR = 0.0001            # 元/字符
    _LLM_PER_KTOKEN = 0.008           # 元/千 token

    def estimate(self, *, topic: str, brief: str, scene_count: int,
                 estimated_story_len: int = 1000) -> str:
        # Step 0: 主题→稿
        step0_in = 400 + len(topic) * 3 + len(brief) * 3
        step0_out = estimated_story_len * 2
        step0_cost = (step0_in + step0_out) / 1000 * self._LLM_PER_KTOKEN

        # Step 1: 场景切分
        step1_in = 600 + estimated_story_len * 3
        step1_out = scene_count * 200
        step1_cost = (step1_in + step1_out) / 1000 * self._LLM_PER_KTOKEN

        # Step 2: 图片
        image_cost = scene_count * self._SEEDREAM_PER_IMAGE_2K

        # Step 3: TTS
        tts_chars = estimated_story_len
        tts_cost = tts_chars * self._TTS_PER_CHAR

        total = step0_cost + step1_cost + image_cost + tts_cost

        lines = [
            "========== 费用预估 (粗略) ==========",
            f"  Step 0  主题→旁白稿    输入 ~{step0_in} tok / 输出 ~{step0_out} tok   →  约 {step0_cost:.4f} 元",
            f"  Step 1  场景切分        输入 ~{step1_in} tok / 输出 ~{step1_out} tok   →  约 {step1_cost:.4f} 元",
            f"  Step 2  图片生成        {scene_count} 张 (Seedream 2K)             →  约 {image_cost:.4f} 元",
            f"  Step 3  TTS 旁白        {tts_chars} 字 (Seed-TTS 2.0)              →  约 {tts_cost:.4f} 元",
            f"  Step 4  剪映合成        本地 pyJianYingDraft                       →  0 元",
            "  ---------------------------",
            f"  合计:  约 {total:.4f} 元 （旁白长度按 {estimated_story_len} 字估算）",
            "  提示: 实际费用以火山引擎/字节账单为准",
            "=========================================",
        ]
        return "\n".join(lines)


# ============================================================
#  Step 4: 剪映草稿
# ============================================================

def compose_jianying_draft(
    project_name: str,
    scenes: list[dict[str, str]],
    image_paths: list[str],
    audio_paths: list[str],
    style: dict[str, Any],
    logger: PipelineLogger,
    *,
    draft_folder_path: str,
    fade_transition: bool = True,
    add_image_movement: bool = True,
) -> str:
    jianying_cfg = style.get("jianying", {}) or {}
    camera_preset = jianying_cfg.get("camera_preset", "mixed")
    camera_effects = CAMERA_PRESETS.get(camera_preset, CAMERA_PRESETS["mixed"])

    # 默认给草稿名追加时间戳
    unique = bool(jianying_cfg.get("unique_draft_name", True))
    draft_name = project_name
    if unique:
        draft_name = f"{draft_name}_{time.strftime('%Y%m%d_%H%M%S')}"

    builder = JianyingDraftBuilder(
        draft_name=draft_name,
        draft_folder_path=draft_folder_path,
        on_progress=lambda msg, pct: logger.info("jianying.progress", message=msg, pct=pct),
        add_image_movement=bool(jianying_cfg.get("add_image_movement", True)) and add_image_movement,
        add_video_movement=bool(jianying_cfg.get("add_video_movement", True)),
        split_subtitles=bool(jianying_cfg.get("split_subtitles", True)),
        fade_transition=bool(jianying_cfg.get("fade_transition", True)) and fade_transition,
        camera_effects=camera_effects,
        canvas_width=style.get("canvas_width"),
        canvas_height=style.get("canvas_height"),
        subtitle_preset=jianying_cfg.get("subtitle_preset"),
        transition_duration=jianying_cfg.get("transition_duration", "0.5s"),
    )

    segments = [
        SegmentInfo(subtitle=s["narration"], audio_path=aud, media_path=img)
        for s, img, aud in zip(scenes, image_paths, audio_paths)
    ]

    report = builder.preflight(segments)
    if not report.passed:
        for err in report.errors:
            logger.error("jianying.preflight.fail", error=err)
        raise SystemExit("剪映草稿预检失败 — 详见上方错误。")
    for w in report.warnings:
        logger.warn("jianying.preflight.warn", warning=w)

    logger.info("step:jianying.start", segments=len(scenes), draft_name=draft_name,
                canvas=f"{report.estimated_canvas}")
    result = builder._build(segments)
    logger.info("step:jianying.done", result=result)
    return f"{result}\n  草稿位置: {draft_folder_path}/{draft_name}"


# ============================================================
#  Pipeline 主流程
# ============================================================

def run(args: argparse.Namespace) -> int:
    load_env(ENV_FILE)
    api_key = required_env("AGENT_API_KEY", ENV_FILE)

    # --audio-only / --images-only 只是 --skip-* 组合的语法糖
    if args.audio_only and args.images_only:
        raise SystemExit("--audio-only 与 --images-only 不能同时使用")
    if args.audio_only:
        args.skip_image = True
        args.skip_jianying = True
    if args.images_only:
        args.skip_tts = True
        args.skip_jianying = True

    style = load_style(args.style)
    scene_count = args.scenes or int(style.get("scene_count", 8))
    draft_folder_path = args.draft_folder or style.get("draft_folder_path") or DEFAULT_DRAFT_FOLDER

    # ---- 费用预估 & --dry-run ----
    print(CostEstimator().estimate(
        topic=args.topic, brief=args.brief or "", scene_count=scene_count,
        estimated_story_len=1000,
    ))
    if args.dry_run:
        print("\n--dry-run 模式，未进行任何 API 调用。若费用可接受，去掉 --dry-run 正式运行。")
        return 0

    # ---- 输出目录 ----
    logger = PipelineLogger(DEFAULT_LOG_DIR)

    if args.output_dir and args.resume_latest:
        raise SystemExit("--output-dir 与 --resume-latest 不能同时使用")

    topic_slug = safe_slug(args.topic, fallback="video")

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for child in ("images", "audio", "responses"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)
    elif args.resume_latest:
        latest = find_latest_output_dir(topic_slug, DEFAULT_OUTPUT_ROOT)
        if latest is None:
            raise SystemExit(
                f"--resume-latest 找不到已有 run 目录：{DEFAULT_OUTPUT_ROOT / topic_slug}"
            )
        output_dir = latest
        for child in ("images", "audio", "responses"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)
        print(f"\n  >> 复用最新 run 目录: {output_dir}")
    else:
        output_dir = prepare_output_dir(topic_slug, DEFAULT_OUTPUT_ROOT)

    print("\n" + "=" * 60)
    print("  通用「主题 → 剪映视频」流水线")
    print("=" * 60)
    print(f"  topic:            {args.topic}")
    print(f"  brief:            {args.brief or '(未指定)'}")
    print(f"  style:            {args.style}   ({style.get('description', '')})")
    print(f"  scenes:           {scene_count}")
    print(f"  workers:          {args.workers}")
    print(f"  output_dir:       {output_dir}")
    print(f"  draft_folder:     {draft_folder_path}")
    print(f"  log:              {logger.log_path}")
    print("=" * 60)

    logger.info("pipeline.start", topic=args.topic, style=args.style,
                scene_count=scene_count, output_dir=str(output_dir))
    t_start = time.time()

    try:
        # ---- Step 0: 主题 → 旁白稿 ----
        story_data_path = output_dir / "generated_story.json"
        if args.skip_llm and story_data_path.exists():
            story_data = json.loads(story_data_path.read_text(encoding="utf-8"))
            logger.info("skip.topic_to_story.reuse", path=str(story_data_path))
        else:
            story_data = generate_story_from_topic(
                api_key,
                args.topic,
                brief=args.brief or "",
                scene_count_hint=scene_count,
                output_dir=output_dir,
                logger=logger,
            )

        project_name = story_data["project_name"]
        story = story_data["story"]
        print(f"\n  [Step 0] 旁白稿 {len(story)} 字，project_name={project_name}")
        print(f"    title:  {story_data.get('title', '')}")

        # ---- 卡点 1：旁白稿确认 ----
        if not args.yes:
            _preview_story(story_data)
            story_json = output_dir / "generated_story.json"
            choice = _prompt_confirm("旁白稿是否符合要求？", story_json)
            if choice == "n":
                logger.info("pipeline.abort", stage="after_story",
                            hint="用户在 Step 0 卡点选择中止")
                print("\n已中止。如需换主题重跑，直接换 --topic 或加 --brief 再跑一次；")
                print(f"如需微调后继续，编辑 {story_json} 后加 --resume-latest --skip-llm 续跑。")
                return 0
            if choice == "e":
                story_data = json.loads(story_json.read_text(encoding="utf-8"))
                project_name = story_data["project_name"]
                story = story_data["story"]
                logger.info("pipeline.edited", stage="after_story", story_len=len(story))
                print(f"  已重新加载 {story_json.name}（{len(story)} 字），继续 Step 1。")

        # ---- Step 1: 场景切分 ----
        scenes_path = output_dir / "generated_scenes.json"
        if args.skip_llm and scenes_path.exists():
            scenes = json.loads(scenes_path.read_text(encoding="utf-8"))["scenes"]
            logger.info("skip.scene_split.reuse", path=str(scenes_path), count=len(scenes))
        else:
            scenes = split_story_to_scenes(
                api_key, project_name, story, scene_count, output_dir, logger,
            )

        # ---- 卡点 2：场景切分确认（图片生成前）----
        if not args.yes and not args.skip_image:
            _preview_scenes(scenes)
            choice = _prompt_confirm("场景切分是否 OK？（下一步开始生成图片，费用最贵）", scenes_path)
            if choice == "n":
                logger.info("pipeline.abort", stage="after_scenes",
                            hint="用户在 Step 1 卡点选择中止（图片生成前）")
                print("\n已中止。旁白与场景已保存：")
                print(f"  - {output_dir / 'generated_story.json'}")
                print(f"  - {scenes_path}")
                print("修改后可 --resume-latest --skip-llm 从图片生成开始续跑。")
                return 0
            if choice == "e":
                scenes = json.loads(scenes_path.read_text(encoding="utf-8"))["scenes"]
                logger.info("pipeline.edited", stage="after_scenes", count=len(scenes))
                print(f"  已重新加载 {scenes_path.name}（{len(scenes)} 场景），继续 Step 2。")

        # ---- Step 2: 图片 ----
        image_cfg = style.get("image", {}) or {}
        if args.skip_image:
            image_paths = []
            fmt = image_cfg.get("output_format", "png")
            for s in scenes:
                p = output_dir / "images" / f"{s['id']}.{fmt}"
                if not p.exists():
                    raise SystemExit(f"--skip-image 但图片不存在: {p}")
                image_paths.append(str(p))
            logger.info("skip.image", count=len(image_paths))
        else:
            image_paths = generate_images(
                api_key, scenes, output_dir, logger,
                base_url=image_cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/plan/v3"),
                model=image_cfg.get("model", "doubao-seedream-5.0-lite"),
                size=image_cfg.get("size", "2K"),
                output_format=image_cfg.get("output_format", "png"),
                response_format=image_cfg.get("response_format", "url"),
                watermark=bool(image_cfg.get("watermark", False)),
                target_aspect_ratio=style.get("target_aspect_ratio")
                                    or image_cfg.get("target_aspect_ratio"),
                trim_black_borders=bool(image_cfg.get("trim_black_borders", True)),
                prompt_suffix=image_cfg.get("prompt_suffix", ""),
                max_workers=args.workers,
                resume=not args.no_resume,
                request_delay=args.request_delay,
            )
        print(f"\n  [Step 2] 图片 {len(image_paths)} 张已就绪")

        # ---- Step 3: TTS ----
        tts_cfg = style.get("tts", {}) or {}
        if args.skip_tts:
            audio_fmt = tts_cfg.get("format", "mp3")
            audio_paths = []
            for s in scenes:
                p = output_dir / "audio" / f"{s['id']}.{audio_fmt}"
                if not p.exists():
                    raise SystemExit(f"--skip-tts 但音频不存在: {p}")
                audio_paths.append(str(p))
            logger.info("skip.tts", count=len(audio_paths))
        else:
            audio_paths = synthesize_audio(
                api_key, scenes, output_dir, logger,
                url=tts_cfg.get("url"),
                resource_id=args.resource_id or tts_cfg.get("resource_id"),
                speaker=args.speaker or tts_cfg.get("speaker"),
                audio_format=tts_cfg.get("format", "mp3"),
                sample_rate=int(tts_cfg.get("sample_rate", 24000)),
            )
        print(f"\n  [Step 3] 配音 {len(audio_paths)} 段已就绪")

        # ---- Step 4: 剪映 ----
        if args.skip_jianying:
            logger.info("skip.jianying")
            draft_result = "(--skip-jianying)"
        else:
            draft_result = compose_jianying_draft(
                project_name=project_name,
                scenes=scenes,
                image_paths=image_paths,
                audio_paths=audio_paths,
                style=style,
                logger=logger,
                draft_folder_path=draft_folder_path,
                fade_transition=not args.no_fade,
                add_image_movement=not args.no_movement,
            )
        print(f"\n  [Step 4] 剪映草稿：\n    {draft_result}")

    except SystemExit:
        logger.error("pipeline.failed")
        print("\n流水线中断 — 请检查上方错误信息与日志文件。")
        return 1
    except Exception as exc:
        logger.error("pipeline.failed", error=str(exc))
        print(f"\n流水线异常: {exc}")
        return 1

    elapsed = round(time.time() - t_start, 1)
    logger.info("pipeline.done", elapsed_s=elapsed)

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
        description="通用「主题 → 剪映视频」流水线：一个主题短语 → 剪映草稿。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--topic", type=str, required=False,
                        help="主题短语，例如 「南明李定国」「苏轼被贬黄州」")
    parser.add_argument("--brief", type=str, default="",
                        help="可选：给 LLM 的重点提示，例如「两蹶名王、磨盘山血战」")
    styles = list_style_names() or ["epic", "documentary", "shorts"]
    parser.add_argument("--style", type=str, default="epic", choices=styles,
                        help=f"风格预设，可选：{'/'.join(styles)}（默认 epic）")
    parser.add_argument("--scenes", type=int, default=None,
                        help="场景数量（覆盖风格预设默认值）")
    parser.add_argument("--workers", type=int, default=2,
                        help="并行生图线程数（默认 2）")
    parser.add_argument("--request-delay", type=float, default=2.0,
                        help="生图请求间延时秒数（默认 2.0）")
    parser.add_argument("--draft-folder", type=str, default=None,
                        help=f"剪映草稿目录（默认 {DEFAULT_DRAFT_FOLDER}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做费用估算，不做任何 API 调用")
    parser.add_argument("--skip-llm", action="store_true",
                        help="跳过 LLM 步骤（用已有 generated_story.json / generated_scenes.json）")
    parser.add_argument("--skip-image", action="store_true", help="跳过图片生成")
    parser.add_argument("--skip-tts", action="store_true", help="跳过 TTS")
    parser.add_argument("--skip-jianying", action="store_true", help="跳过剪映草稿合成")
    parser.add_argument("--audio-only", action="store_true",
                        help="便捷开关：只跑到 Step 3（生成 mp3 配音），跳过图片与剪映草稿")
    parser.add_argument("--images-only", action="store_true",
                        help="便捷开关：只跑到 Step 2（生成图片），跳过 TTS 与剪映草稿")
    parser.add_argument("--speaker", type=str, default=None,
                        help="覆盖 TTS 音色（如 zh_female_vv_uranus_bigtts）；不填则用风格预设里的默认音色")
    parser.add_argument("--resource-id", type=str, default=None,
                        help="覆盖 TTS resource_id（默认 seed-tts-2.0）；换资源包时可用")
    parser.add_argument("--no-resume", action="store_true", help="禁用断点续跑（图片/音频若已存在也重跑）")
    parser.add_argument("--no-fade", action="store_true", help="禁用片段转场")
    parser.add_argument("--no-movement", action="store_true", help="禁用图片运镜")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="指定输出目录（一般用于续跑）")
    parser.add_argument("--resume-latest", action="store_true",
                        help="复用该 topic 最近一次 run 目录（与 --output-dir 互斥）")
    parser.add_argument("--list-styles", action="store_true",
                        help="列出所有可用风格预设后退出")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="关闭 Step 0/1 后的交互确认卡点，直接一路跑到底（脚本化时用）")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_styles:
        names = list_style_names()
        if not names:
            print("(尚未定义任何风格预设，请检查 configs/styles/*.json)")
            return 0
        print("可用风格预设：")
        for name in names:
            try:
                data = load_style(name)
                print(f"  - {name}: {data.get('description', '')}")
            except Exception as exc:
                print(f"  - {name}: (加载失败 {exc})")
        return 0

    if not args.topic:
        parser.error("--topic 是必填参数（或使用 --list-styles 查看可用风格）")

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
