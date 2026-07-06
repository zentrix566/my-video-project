"""代码走读视频（一键流水线）—— 前端项目 → 剪映草稿。

    python make_code_walk.py --project "E:/github/crazy-people"
    python make_code_walk.py --project "E:/github/crazy-people" --scenes 8 --yes
    python make_code_walk.py --project "E:/github/crazy-people" --dry-run
    python make_code_walk.py --project "E:/github/crazy-people" --resume-latest --skip-llm --skip-shots --skip-tts

流水线阶段：
    Step 0  项目扫描        pipeline.project_scan      （纯本地）
    Step 1  讲解稿 + 分镜    pipeline.walk_narrator     （LLM）
    Step 2  截图渲染        pipeline.shot_renderer     （Playwright + Pygments）
    Step 3  TTS 配音        pipeline.tts               （字节 Seed-TTS，复用）
    Step 4  剪映草稿        pipeline.draft_composer    （pyJianYingDraft，复用）
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
from pipeline.project_scan import scan_project
from pipeline.walk_narrator import generate_walk_scenes
from pipeline.shot_renderer import ensure_dev_server, render_all_shots, shot_extension
from pipeline.tts import synthesize_audio

# 复用 make_video.py 里已有的剪映合成函数（不改老代码）
from make_video import compose_jianying_draft


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "code_walks"
DEFAULT_LOG_DIR = ROOT / "outputs" / "logs"
DEFAULT_DRAFT_FOLDER = "D:/Program Files/JianyingPro Drafts"


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


def _preview_meta(meta: dict[str, Any]) -> None:
    print("\n" + "─" * 60)
    print(f"  Step 0 项目扫描")
    print("─" * 60)
    print(f"  项目名:      {meta.get('raw_name')}   (slug={meta.get('project_name')})")
    print(f"  框架:        {meta.get('framework')}    dev: {meta.get('dev_command')}  port={meta.get('dev_port')}")
    if meta.get("description"):
        print(f"  描述:        {meta['description']}")
    routes = meta.get("routes") or []
    if routes:
        print(f"  路由:        {len(routes)} 条 - {', '.join(r['path'] for r in routes[:6])}")
    else:
        print(f"  路由:        (无 router，UI 截图恒用 '/')")
    print(f"  关键文件:    {len(meta.get('key_files') or [])} 个")
    for f in meta.get("key_files") or []:
        print(f"    - [{f['role']:>10}] {f['path']}   ({f['loc']} 行)")
    print("─" * 60)


def _preview_scenes(scenes_data: dict[str, Any]) -> None:
    scenes = scenes_data["scenes"]
    print("\n" + "─" * 60)
    print(f"  Step 1 讲解稿 + 分镜   共 {len(scenes)} 段")
    print(f"  视频主标题: {scenes_data.get('project_title', '')}")
    if scenes_data.get("subtitle"):
        print(f"  副标题:     {scenes_data['subtitle']}")
    print("─" * 60)
    for i, s in enumerate(scenes, 1):
        spec = s.get("shot_spec") or {}
        stype = spec.get("type", "?")
        if stype == "code":
            shot_desc = f"code {spec.get('file','')} L{spec.get('focus_lines',[1,1])[0]}-{spec.get('focus_lines',[1,1])[1]}"
        elif stype == "cover":
            shot_desc = f"cover 「{spec.get('title','')}」"
        elif stype == "ui_video":
            warmup = spec.get('warmup_ms', 0)
            tail = spec.get('tail_ms', 0)
            n_int = len(spec.get('interactions') or [])
            shot_desc = f"video {spec.get('url_path','/')}  warmup={warmup}ms tail={tail}ms  interactions={n_int}"
        else:
            shot_desc = f"ui   {spec.get('url_path','/')}  wait={spec.get('wait_ms',0)}ms  interactions={len(spec.get('interactions') or [])}"
        print(f"\n  [{i:>2}] {s['id']}     [{stype:>8}] {shot_desc}")
        print(f"       {s['narration']}")
    print("\n" + "─" * 60)


# ============================================================
#  费用预估
# ============================================================

def estimate_cost(scene_count: int, meta_readme_len: int = 4000) -> str:
    """代码走读版预估：Scan 0 元 + LLM + Shots 0 元 + TTS。"""
    _LLM_PER_KTOKEN = 0.008
    _TTS_PER_CHAR = 0.0001

    # Step 1 LLM：输入 = system + payload(readme + key_files excerpts) ≈ 3-6K tok；输出 ≈ 8 段 * 250 ≈ 2K tok
    step1_in = 2500 + meta_readme_len * 3
    step1_out = scene_count * 300
    step1_cost = (step1_in + step1_out) / 1000 * _LLM_PER_KTOKEN

    # Step 3 TTS：每段旁白约 60-100 字，8 段约 600-800 字
    tts_chars = scene_count * 80
    tts_cost = tts_chars * _TTS_PER_CHAR

    total = step1_cost + tts_cost

    return "\n".join([
        "========== 费用预估 (粗略) ==========",
        f"  Step 0  项目扫描      本地扫描，无 API                       →  0 元",
        f"  Step 1  讲解稿+分镜   输入 ~{step1_in} tok / 输出 ~{step1_out} tok   →  约 {step1_cost:.4f} 元",
        f"  Step 2  Playwright   本地渲染，无 API                       →  0 元",
        f"  Step 3  TTS 旁白      约 {tts_chars} 字 (Seed-TTS 2.0)             →  约 {tts_cost:.4f} 元",
        f"  Step 4  剪映合成      本地 pyJianYingDraft                    →  0 元",
        "  ---------------------------",
        f"  合计:  约 {total:.4f} 元",
        "  提示: 相比图片生成流水线（0.10~2 元）便宜一个数量级",
        "=========================================",
    ])


# ============================================================
#  主流程
# ============================================================

def run(args: argparse.Namespace) -> int:
    load_env(ENV_FILE)

    project_root = Path(args.project).resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise SystemExit(f"--project 路径不存在或不是目录: {project_root}")

    style = load_style(args.style)
    scene_count = args.scenes or int(style.get("scene_count", 8))
    draft_folder_path = args.draft_folder or style.get("draft_folder_path") or DEFAULT_DRAFT_FOLDER

    # ---- 费用预估 & dry-run ----
    print(estimate_cost(scene_count))
    if args.dry_run:
        print("\n--dry-run 模式，仅估算未做任何 API 调用。")
        return 0

    api_key = required_env("AGENT_API_KEY", ENV_FILE)

    # ---- 输出目录 ----
    logger = PipelineLogger(DEFAULT_LOG_DIR)
    if args.output_dir and args.resume_latest:
        raise SystemExit("--output-dir 与 --resume-latest 不能同时使用")

    project_slug = safe_slug(project_root.name, fallback="frontend")
    if args.output_dir:
        output_dir = Path(args.output_dir)
        for child in ("images", "audio", "responses"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)
    elif args.resume_latest:
        latest = find_latest_output_dir(project_slug, DEFAULT_OUTPUT_ROOT)
        if latest is None:
            raise SystemExit(f"--resume-latest 找不到已有 run 目录：{DEFAULT_OUTPUT_ROOT / project_slug}")
        output_dir = latest
        for child in ("images", "audio", "responses"):
            (output_dir / child).mkdir(parents=True, exist_ok=True)
        print(f"\n  >> 复用最新 run 目录: {output_dir}")
    else:
        output_dir = prepare_output_dir(project_slug, DEFAULT_OUTPUT_ROOT)

    print("\n" + "=" * 60)
    print("  「前端项目 → 剪映视频」代码走读流水线")
    print("=" * 60)
    print(f"  project:        {project_root}")
    print(f"  brief:          {args.brief or '(未指定)'}")
    print(f"  style:          {args.style}   ({style.get('description', '')})")
    print(f"  scenes:         {scene_count}")
    print(f"  output_dir:     {output_dir}")
    print(f"  draft_folder:   {draft_folder_path}")
    print(f"  log:            {logger.log_path}")
    print("=" * 60)

    logger.info("code_walk.start",
                project=str(project_root), style=args.style, scene_count=scene_count,
                output_dir=str(output_dir))
    t_start = time.time()

    try:
        # ---- Step 0: 项目扫描 ----
        meta_path = output_dir / "project_meta.json"
        if args.skip_scan and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            logger.info("skip.project_scan.reuse", path=str(meta_path))
        else:
            meta = scan_project(project_root, output_dir, logger)

        print(f"\n  [Step 0] 项目扫描完成 → {meta_path.name}")

        # ---- 卡点 1：项目扫描概览 ----
        if not args.yes:
            _preview_meta(meta)
            choice = _prompt_confirm("扫描结果是否符合预期？", meta_path)
            if choice == "n":
                logger.info("pipeline.abort", stage="after_scan")
                print("\n已中止。可手动编辑 project_meta.json 后 --resume-latest --skip-scan 续跑。")
                return 0
            if choice == "e":
                meta = json.loads(meta_path.read_text(encoding="utf-8"))

        # ---- Step 1: LLM 生成讲解稿 + 分镜 ----
        scenes_path = output_dir / "generated_scenes.json"
        if args.skip_llm and scenes_path.exists():
            scenes_data = json.loads(scenes_path.read_text(encoding="utf-8"))
            logger.info("skip.walk_narrator.reuse", path=str(scenes_path),
                        count=len(scenes_data.get("scenes", [])))
        else:
            scenes_data = generate_walk_scenes(
                api_key, meta,
                brief=args.brief or "",
                scene_count=scene_count,
                output_dir=output_dir,
                logger=logger,
            )

        scenes: list[dict[str, Any]] = scenes_data["scenes"]
        print(f"\n  [Step 1] 讲解稿+分镜 {len(scenes)} 段已就绪 → {scenes_path.name}")

        # ---- 卡点 2：分镜确认（截图前）----
        if not args.yes and not args.skip_shots:
            _preview_scenes(scenes_data)
            choice = _prompt_confirm(
                "讲解稿 + 分镜是否 OK？（下一步启动 dev server + Playwright 截图）",
                scenes_path,
            )
            if choice == "n":
                logger.info("pipeline.abort", stage="after_scenes")
                print("\n已中止。修改后可 --resume-latest --skip-llm 从截图开始续跑。")
                return 0
            if choice == "e":
                scenes_data = json.loads(scenes_path.read_text(encoding="utf-8"))
                scenes = scenes_data["scenes"]
                print(f"  已重新加载 {scenes_path.name}（{len(scenes)} 场景），继续 Step 2。")

        # ---- Step 2: 截图（UI 静态 + UI 视频 + 代码高亮） ----
        need_dev_server = any(
            (s.get("shot_spec") or {}).get("type") in ("ui", "ui_video", "cover")
            for s in scenes
        )

        if args.skip_shots:
            image_paths: list[str] = []
            for s in scenes:
                ext = shot_extension(s.get("shot_spec") or {})
                p = output_dir / "images" / f"{s['id']}{ext}"
                if not p.exists():
                    raise SystemExit(f"--skip-shots 但媒体文件不存在: {p}")
                image_paths.append(str(p))
            logger.info("skip.shots", count=len(image_paths))
        else:
            dev_cfg = style.get("dev_server", {}) or {}
            port = args.dev_port or int(dev_cfg.get("default_port") or meta.get("dev_port") or 5173)
            timeout_s = int(dev_cfg.get("startup_timeout_s") or 40)
            npm_cmd = dev_cfg.get("npm_command")

            if need_dev_server:
                with ensure_dev_server(
                    project_root, port, logger,
                    timeout_s=timeout_s,
                    npm_cmd=npm_cmd,
                    already_running=args.skip_dev_server,
                ) as dev_url:
                    image_paths = render_all_shots(
                        scenes,
                        project_root=project_root,
                        dev_server_url=dev_url,
                        output_dir=output_dir,
                        style=style,
                        logger=logger,
                        resume=not args.no_resume,
                    )
            else:
                image_paths = render_all_shots(
                    scenes,
                    project_root=project_root,
                    dev_server_url=None,
                    output_dir=output_dir,
                    style=style,
                    logger=logger,
                    resume=not args.no_resume,
                )

        print(f"\n  [Step 2] 截图 {len(image_paths)} 张已就绪 → {output_dir / 'images'}")

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
            # 用 scenes_data 里的 project_title 做草稿名，比 slug 好看
            draft_project_name = scenes_data.get("project_title") or meta.get("raw_name") or project_slug
            draft_result = compose_jianying_draft(
                project_name=draft_project_name,
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
        logger.error("code_walk.failed")
        print("\n流水线中断 — 请检查上方错误信息与日志文件。")
        return 1
    except Exception as exc:
        logger.error("code_walk.failed", error=str(exc))
        print(f"\n流水线异常: {exc}")
        return 1

    elapsed = round(time.time() - t_start, 1)
    logger.info("code_walk.done", elapsed_s=elapsed)

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
        description="前端项目 → 代码走读视频剪映草稿：扫源码 → LLM 讲稿 → Playwright 截图 → 剪映合成。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", type=str, required=False,
                        help="目标前端项目根目录（含 package.json 的路径）")
    parser.add_argument("--brief", type=str, default="",
                        help="可选：给 LLM 的重点提示，例如「侧重讲讲发疯 AI 的实现」")
    styles = list_style_names() or ["codewalk"]
    parser.add_argument("--style", type=str, default="codewalk", choices=styles,
                        help=f"风格预设（默认 codewalk）")
    parser.add_argument("--scenes", type=int, default=None,
                        help="场景数量（覆盖风格预设，默认 8）")
    parser.add_argument("--draft-folder", type=str, default=None,
                        help=f"剪映草稿目录（默认 {DEFAULT_DRAFT_FOLDER}）")
    parser.add_argument("--dev-port", type=int, default=None,
                        help="dev server 端口（默认从 vite.config 抠或 5173）")
    parser.add_argument("--skip-dev-server", action="store_true",
                        help="假设 dev server 已在跑（外部手动 npm run dev），不启子进程")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做费用估算，不调 API")
    parser.add_argument("--skip-scan", action="store_true",
                        help="跳过 Step 0 扫描（复用已有 project_meta.json）")
    parser.add_argument("--skip-llm", action="store_true",
                        help="跳过 Step 1 LLM（复用已有 generated_scenes.json）")
    parser.add_argument("--skip-shots", action="store_true",
                        help="跳过 Step 2 截图（复用已有 images/*.png）")
    parser.add_argument("--skip-tts", action="store_true",
                        help="跳过 Step 3 TTS（复用已有 audio/*.mp3）")
    parser.add_argument("--skip-jianying", action="store_true",
                        help="跳过 Step 4 剪映草稿合成")
    parser.add_argument("--speaker", type=str, default=None,
                        help="覆盖 TTS 音色")
    parser.add_argument("--resource-id", type=str, default=None,
                        help="覆盖 TTS resource_id")
    parser.add_argument("--no-resume", action="store_true",
                        help="禁用断点续跑（图片/音频若已存在也重跑）")
    parser.add_argument("--no-fade", action="store_true",
                        help="禁用片段转场")
    parser.add_argument("--no-movement", action="store_true",
                        help="禁用图片运镜（代码走读默认已关，此开关兼容用）")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="指定输出目录（一般用于续跑）")
    parser.add_argument("--resume-latest", action="store_true",
                        help="复用该项目最近一次 run 目录（与 --output-dir 互斥）")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="关闭交互卡点，直接一路跑到底")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.project:
        parser.error("--project 是必填参数（指向目标前端项目根目录）")

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
