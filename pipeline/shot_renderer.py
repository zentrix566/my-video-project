"""Step 2（代码走读版）：UI 截图 + UI 视频 + 代码高亮图 三通道渲染器。

依赖：Playwright（做浏览器）+ Pygments（做代码高亮）+ imageio-ffmpeg（转 mp4）。

- render_ui:        起浏览器 → goto dev server URL → 执行 interactions → 截图 viewport
- render_ui_video:  同上但录制 webm 视频，然后用 ffmpeg 转 mp4
- render_code:      读源文件 focus_lines → Pygments 生成高亮 HTML → Playwright 截图
- render_cover:     UI 截图 + CSS 叠加大标题

外部合约：
    ShotRenderer(style, logger).render_all(scenes, dev_server_url, project_root, output_dir)
        -> list[str]（每个 scene 的媒体绝对路径 .png/.mp4，与 scenes 顺序一致）
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 playwright 依赖。请运行：\n"
        "  pip install playwright\n"
        "  playwright install chromium"
    ) from exc

try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
    from pygments.lexers.special import TextLexer
    from pygments.util import ClassNotFound
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 pygments 依赖。请运行：\n"
        "  pip install pygments"
    ) from exc

try:
    import imageio_ffmpeg
    _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 imageio-ffmpeg 依赖。请运行：\n"
        "  pip install imageio-ffmpeg"
    ) from exc

from pipeline.helpers import PipelineLogger


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "templates" / "code_shot.html"


# 决定单个 scene 落盘时的扩展名。code 用 png（静态），其它类型都是动态视频（.mp4）。
def shot_extension(shot_spec: dict[str, Any]) -> str:
    stype = (shot_spec or {}).get("type", "ui")
    return ".png" if stype == "code" else ".mp4"


# ============================================================
#  dev server 生命周期
# ============================================================

def _is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@contextmanager
def ensure_dev_server(
    project_root: Path,
    port: int,
    logger: PipelineLogger,
    *,
    timeout_s: int = 40,
    npm_cmd: str | None = None,
    already_running: bool = False,
) -> Iterator[str]:
    """确保 dev server 起来，yield 出 base URL；退出 with 块时清理子进程。

    Args:
        already_running: True 时假设外部已启动（例如用户手动 npm run dev），只做端口探测不启子进程。
    """
    url = f"http://localhost:{port}"
    if _is_port_open("localhost", port):
        logger.info("dev_server.detected_existing", url=url)
        yield url
        return

    if already_running:
        raise SystemExit(f"--skip-dev-server 但 {url} 未监听。请先 npm run dev。")

    # 选择 npm 命令：Windows 上是 npm.cmd（不是 .exe，CreateProcess 找不到裸 "npm"）
    if npm_cmd:
        cmd_bin = npm_cmd
    else:
        cmd_bin = "npm.cmd" if sys.platform == "win32" else "npm"

    logger.info("dev_server.starting", cmd=f"{cmd_bin} run dev", cwd=str(project_root))
    print(f"  正在启动 dev server: {cmd_bin} run dev  (cwd={project_root})")

    def _spawn(bin_name: str) -> subprocess.Popen:
        return subprocess.Popen(
            [bin_name, "run", "dev"],
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # Windows 上要显式加 CREATE_NEW_PROCESS_GROUP，方便优雅终止
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )

    try:
        proc = _spawn(cmd_bin)
    except FileNotFoundError:
        # Windows 兜底：用户可能配了 "npm" 但实际得用 "npm.cmd"
        if sys.platform == "win32" and not cmd_bin.lower().endswith((".cmd", ".bat", ".exe")):
            fallback = cmd_bin + ".cmd"
            logger.warn("dev_server.retry_with_cmd_ext", tried=cmd_bin, using=fallback)
            print(f"  找不到 {cmd_bin}，尝试 {fallback} ...")
            proc = _spawn(fallback)
        else:
            raise

    try:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if _is_port_open("localhost", port):
                logger.info("dev_server.ready", url=url, elapsed_s=round(time.time() - t0, 2))
                print(f"  dev server 已就绪: {url}")
                break
            if proc.poll() is not None:
                # 进程已退出（很可能启动失败）
                out = proc.stdout.read() if proc.stdout else ""
                raise SystemExit(
                    f"dev server 子进程异常退出（exit={proc.returncode}）\n----- stdout -----\n{out}"
                )
            time.sleep(0.5)
        else:
            raise SystemExit(
                f"dev server 启动超时（{timeout_s}s），请手动 npm run dev 后重试并加 --skip-dev-server"
            )

        yield url
    finally:
        if proc.poll() is None:
            logger.info("dev_server.terminating", pid=proc.pid)
            try:
                if sys.platform == "win32":
                    proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


# ============================================================
#  代码高亮 HTML
# ============================================================

_LANG_ALIAS = {
    "js": "javascript",
    "ts": "typescript",
    "vue": "html",       # Pygments 无内置 VueLexer 时降级为 html + template
    "markup": "html",
    "jsx": "jsx",
    "tsx": "tsx",
}


def _get_lexer(language: str, file_hint: str | None = None):
    """按 LLM 给的 language 标识拿 lexer；失败则按文件名兜底；再失败退到 TextLexer。"""
    lang = _LANG_ALIAS.get(language.lower(), language.lower())
    try:
        return get_lexer_by_name(lang)
    except ClassNotFound:
        pass
    if file_hint:
        try:
            return get_lexer_for_filename(file_hint)
        except ClassNotFound:
            pass
    return TextLexer()


def _read_focus_lines(source_path: Path, focus_lines: list[int]) -> tuple[str, int, int]:
    """读取 focus 范围内的源码，返回 (代码文本, 起始行号-1based, 结束行号-1based)。"""
    text = source_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    total = len(lines)
    if not lines:
        return "", 1, 1
    start = max(1, min(focus_lines[0], total))
    end = max(start, min(focus_lines[1], total))
    # 上限 42 行强制截断
    if end - start + 1 > 42:
        end = start + 41
    snippet = "\n".join(lines[start - 1:end])
    return snippet, start, end


_THEME_ALIAS = {
    # 常见 IDE/编辑器主题名 → Pygments 内置样式
    "tomorrow-night": "one-dark",
    "tomorrow": "one-dark",
    "vscode-dark": "one-dark",
    "atom-dark": "one-dark",
    "night-owl": "one-dark",
}


def _resolve_theme(theme: str) -> str:
    """把用户友好的主题名映射到 Pygments 内置样式；找不到就回落到 monokai。"""
    from pygments.styles import get_all_styles
    available = set(get_all_styles())
    key = theme.strip().lower()
    if key in available:
        return key
    if key in _THEME_ALIAS and _THEME_ALIAS[key] in available:
        return _THEME_ALIAS[key]
    return "monokai"


def _render_code_html(
    source_code: str,
    language: str,
    file_hint: str,
    *,
    line_start: int,
    theme: str,
    font_family: str,
    font_size_px: int,
    line_height: float,
    bg_start: str,
    bg_end: str,
    title: str,
    project_label: str,
    line_range_label: str,
) -> str:
    """把源码 + 模板拼成最终 HTML 字符串。"""
    lexer = _get_lexer(language, file_hint=file_hint)
    formatter = HtmlFormatter(
        style=_resolve_theme(theme),
        cssclass="highlight",
        linenos="table",
        linenostart=line_start,
        noclasses=False,
        wrapcode=True,
    )
    highlighted = highlight(source_code, lexer, formatter)
    css_defs = formatter.get_style_defs(".code-area .highlight")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "__PYGMENTS_CSS__": css_defs,
        "__PYGMENTS_HTML__": highlighted,
        "__FONT_FAMILY__": font_family,
        "__FONT_SIZE__": str(font_size_px),
        "__LINE_HEIGHT__": str(line_height),
        "__BG_START__": bg_start,
        "__BG_END__": bg_end,
        "__TITLE__": _html_escape(title),
        "__LINE_RANGE__": _html_escape(line_range_label),
        "__LANGUAGE__": _html_escape(language.upper()),
        "__PROJECT_LABEL__": _html_escape(project_label),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ============================================================
#  ShotRenderer
# ============================================================

class ShotRenderer:
    """UI 截图 + UI 视频 + 代码 三通道渲染。一次 with 一次 Chromium。"""

    def __init__(self, style: dict[str, Any], logger: PipelineLogger):
        self.style = style
        self.logger = logger
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

        ui_cfg = style.get("ui_shot", {}) or {}
        self.viewport = {
            "width": int(ui_cfg.get("viewport_width", 1920)),
            "height": int(ui_cfg.get("viewport_height", 1080)),
        }
        self.device_scale_factor = float(ui_cfg.get("device_scale_factor", 1))
        self.default_ui_wait_ms = int(ui_cfg.get("default_wait_ms", 1500))

        code_cfg = style.get("code_shot", {}) or {}
        self.code_theme = str(code_cfg.get("theme") or "monokai")
        self.code_font = str(code_cfg.get("font_family") or "Consolas, monospace")
        self.code_font_size = int(code_cfg.get("font_size_px") or 22)
        self.code_line_height = float(code_cfg.get("line_height") or 1.55)
        bg = code_cfg.get("background_gradient") or ["#1a1a2e", "#16213e"]
        self.bg_start, self.bg_end = str(bg[0]), str(bg[1])

        video_cfg = style.get("ui_video", {}) or {}
        self.video_default_warmup_ms = int(video_cfg.get("default_warmup_ms", 2000))
        self.video_default_tail_ms = int(video_cfg.get("default_tail_ms", 3000))
        self.video_default_duration_ms = int(video_cfg.get("default_duration_ms", 6000))
        self.video_crf = int(video_cfg.get("crf", 22))
        self.video_preset = str(video_cfg.get("preset", "fast"))

    def __enter__(self) -> "ShotRenderer":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            viewport=self.viewport,
            device_scale_factor=self.device_scale_factor,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -------- 单条渲染 --------

    def render_ui(self, shot_spec: dict[str, Any], dev_server_url: str, output_png: Path) -> None:
        page = self._context.new_page()
        try:
            url = dev_server_url.rstrip("/") + shot_spec.get("url_path", "/")
            self.logger.info("shot.ui.goto", url=url)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # 等 #app 出现，兜底等 body
            try:
                page.wait_for_selector("#app", timeout=8_000)
            except Exception:
                page.wait_for_selector("body", timeout=8_000)

            wait_ms = int(shot_spec.get("wait_ms") or self.default_ui_wait_ms)
            page.wait_for_timeout(wait_ms)

            for step in shot_spec.get("interactions") or []:
                self._run_interaction(page, step)

            page.screenshot(path=str(output_png), full_page=False)
            self.logger.info("shot.ui.done", output=str(output_png))
        finally:
            page.close()

    def render_ui_video(self, shot_spec: dict[str, Any], dev_server_url: str, output_mp4: Path) -> None:
        """录制 UI 视频段：另开一个带 record_video_dir 的 context 单独录，避免污染主 context。"""
        webm_tmp_dir = output_mp4.parent / "_webm_tmp"
        webm_tmp_dir.mkdir(parents=True, exist_ok=True)

        video_ctx = self._browser.new_context(
            viewport=self.viewport,
            device_scale_factor=self.device_scale_factor,
            record_video_dir=str(webm_tmp_dir),
            record_video_size=self.viewport,
        )
        page = video_ctx.new_page()
        video_ref = page.video  # 抓引用，close 前拿到 path()

        try:
            url = dev_server_url.rstrip("/") + shot_spec.get("url_path", "/")
            self.logger.info("shot.ui_video.goto", url=url)

            # 关键：测量"从 context 就绪到页面首帧可见"的耗时，用来在 ffmpeg 里跳过白屏
            t_start = time.perf_counter()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector("#app", timeout=8_000)
            except Exception:
                page.wait_for_selector("body", timeout=8_000)
            # 再等一小段让首帧真正被绘制上屏（Chromium 内部的 compositing 延迟）
            page.wait_for_timeout(200)
            load_ms = int((time.perf_counter() - t_start) * 1000)

            # 时间线：warmup（画面自然演化）→ interactions（可选，观众能看到点击效果）→ tail（继续录）
            # 兼容旧字段：ui 类型历史上只有 wait_ms，作为 warmup_ms 的兜底
            warmup_ms = int(
                shot_spec.get("warmup_ms")
                or shot_spec.get("wait_ms")
                or self.video_default_warmup_ms
            )
            tail_ms = int(shot_spec.get("tail_ms") or self.video_default_tail_ms)
            page.wait_for_timeout(warmup_ms)

            for step in shot_spec.get("interactions") or []:
                self._run_interaction(page, step)

            page.wait_for_timeout(tail_ms)

            self.logger.info("shot.ui_video.recorded",
                             load_ms=load_ms, warmup_ms=warmup_ms, tail_ms=tail_ms)
        finally:
            page.close()
            video_ctx.close()  # 关 context 才会把 webm 写盘

        # 转码 webm → mp4（H.264 + yuv420p，剪映专业版和大多数播放器兼容）
        # 跳过前 load_ms 毫秒，把 Playwright 录到的"about:blank + 加载中"白屏干掉
        webm_path = Path(video_ref.path())
        self._convert_webm_to_mp4(webm_path, output_mp4, skip_ms=load_ms)

        # 清理临时 webm
        try:
            webm_path.unlink(missing_ok=True)
            # 如果 _webm_tmp 空了就删掉
            if webm_tmp_dir.exists() and not any(webm_tmp_dir.iterdir()):
                webm_tmp_dir.rmdir()
        except OSError:
            pass

        self.logger.info("shot.ui_video.done", output=str(output_mp4))

    def _convert_webm_to_mp4(self, webm_path: Path, output_mp4: Path, skip_ms: int = 0) -> None:
        """用 imageio-ffmpeg 打包的 ffmpeg 二进制做 H.264 转码。

        Args:
            skip_ms: 跳过 webm 开头 N 毫秒（用来去掉 Playwright 录制的"页面加载白屏"阶段）
        """
        cmd = [_FFMPEG_EXE, "-y", "-i", str(webm_path)]
        # -ss 放在 -i 之后是"精确 seek"，会解码到目标帧；放在之前是"快速 seek"，可能跳到最近关键帧。
        # 我们的白屏一般是前 500-1500ms 且需要精确剪掉，用精确 seek。
        if skip_ms > 0:
            cmd += ["-ss", f"{skip_ms / 1000:.3f}"]
        cmd += [
            "-c:v", "libx264",
            "-preset", self.video_preset,
            "-crf", str(self.video_crf),
            "-pix_fmt", "yuv420p",       # 剪映/QuickTime 兼容必选
            "-movflags", "+faststart",   # 让视频头能被快速读到，剪映装载更快
            "-an",                       # 视频不带声音，音频轨走 TTS
            str(output_mp4),
        ]
        self.logger.info(
            "shot.video.ffmpeg",
            output=output_mp4.name,
            skip_ms=skip_ms,
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise SystemExit(
                f"ffmpeg 转码失败（{output_mp4.name}）：\n"
                f"stderr:\n{result.stderr[-1500:]}"
            )

    def render_code(self, shot_spec: dict[str, Any], project_root: Path, output_png: Path) -> None:
        file_rel = shot_spec["file"]
        focus = shot_spec.get("focus_lines") or [1, 42]
        source_path = project_root / file_rel
        if not source_path.exists():
            raise SystemExit(f"代码截图源文件不存在: {source_path}")

        source_code, start, end = _read_focus_lines(source_path, focus)
        language = shot_spec.get("language") or "text"
        title = shot_spec.get("title") or file_rel

        html = _render_code_html(
            source_code,
            language=language,
            file_hint=file_rel,
            line_start=start,
            theme=self.code_theme,
            font_family=self.code_font,
            font_size_px=self.code_font_size,
            line_height=self.code_line_height,
            bg_start=self.bg_start,
            bg_end=self.bg_end,
            title=title,
            project_label=project_root.name,
            line_range_label=f"L{start}-L{end}",
        )

        page = self._context.new_page()
        try:
            self.logger.info("shot.code.render", file=file_rel, lines=f"{start}-{end}")
            page.set_content(html, wait_until="domcontentloaded", timeout=15_000)
            # 保底再等一帧让字体加载
            page.wait_for_timeout(300)
            page.screenshot(path=str(output_png), full_page=False)
            self.logger.info("shot.code.done", output=str(output_png))
        finally:
            page.close()

    def render_cover(self, shot_spec: dict[str, Any], dev_server_url: str, output_mp4: Path) -> None:
        """封面：录一段视频，同时在浏览器内叠加大标题（半透明黑色蒙层 + 白色标题+副标题）。

        观众第一眼就能看到「运动中的项目 + 大标题」，比静态封面更抓眼球。
        """
        webm_tmp_dir = output_mp4.parent / "_webm_tmp"
        webm_tmp_dir.mkdir(parents=True, exist_ok=True)

        video_ctx = self._browser.new_context(
            viewport=self.viewport,
            device_scale_factor=self.device_scale_factor,
            record_video_dir=str(webm_tmp_dir),
            record_video_size=self.viewport,
        )
        page = video_ctx.new_page()
        video_ref = page.video

        try:
            url = dev_server_url.rstrip("/") + (
                shot_spec.get("background_url_path")
                or shot_spec.get("url_path")
                or "/"
            )
            self.logger.info("shot.cover.goto", url=url)

            # 测量加载耗时，用来在 ffmpeg 里跳过 about:blank 白屏
            t_start = time.perf_counter()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector("#app", timeout=8_000)
            except Exception:
                page.wait_for_selector("body", timeout=8_000)

            # 立刻注入封面层，观众第一帧就看到标题
            title = _html_escape(shot_spec.get("title", ""))
            subtitle = _html_escape(shot_spec.get("subtitle", ""))
            page.evaluate(
                """([title, subtitle]) => {
                    const el = document.createElement('div');
                    el.style.cssText = `
                      position: fixed; inset: 0; z-index: 999999;
                      display: flex; flex-direction: column;
                      align-items: center; justify-content: center;
                      background: radial-gradient(circle at 50% 40%, rgba(0,0,0,0.28), rgba(0,0,0,0.72));
                      color: #fff; font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
                      text-shadow: 0 6px 24px rgba(0,0,0,0.6);
                      pointer-events: none;
                    `;
                    el.innerHTML = `
                      <div style="font-size: 108px; font-weight: 800; letter-spacing: 8px;
                                  animation: coverIn 900ms cubic-bezier(.16,.84,.44,1) both;">${title}</div>
                      <div style="margin-top: 24px; font-size: 38px; letter-spacing: 4px; opacity: 0.92;
                                  animation: coverIn 900ms cubic-bezier(.16,.84,.44,1) 200ms both;">${subtitle}</div>
                    `;
                    const style = document.createElement('style');
                    style.textContent = `@keyframes coverIn {
                        from { opacity: 0; transform: translateY(20px) scale(0.96); }
                        to   { opacity: 1; transform: translateY(0)   scale(1); }
                    }`;
                    document.head.appendChild(style);
                    document.body.appendChild(el);
                }""",
                [title, subtitle],
            )
            # 让首帧真正被绘制上屏后再定"内容起点"
            page.wait_for_timeout(200)
            load_ms = int((time.perf_counter() - t_start) * 1000)

            # 视频时长：默认 warmup + tail = 5 秒够开场
            warmup_ms = int(
                shot_spec.get("warmup_ms")
                or shot_spec.get("wait_ms")
                or self.video_default_warmup_ms
            )
            tail_ms = int(shot_spec.get("tail_ms") or self.video_default_tail_ms)
            page.wait_for_timeout(warmup_ms)

            for step in shot_spec.get("interactions") or []:
                self._run_interaction(page, step)

            page.wait_for_timeout(tail_ms)

            self.logger.info("shot.cover.recorded",
                             load_ms=load_ms, warmup_ms=warmup_ms, tail_ms=tail_ms)
        finally:
            page.close()
            video_ctx.close()

        webm_path = Path(video_ref.path())
        self._convert_webm_to_mp4(webm_path, output_mp4, skip_ms=load_ms)
        try:
            webm_path.unlink(missing_ok=True)
            if webm_tmp_dir.exists() and not any(webm_tmp_dir.iterdir()):
                webm_tmp_dir.rmdir()
        except OSError:
            pass

        self.logger.info("shot.cover.done", output=str(output_mp4))

    # -------- interaction 原语 --------

    def _run_interaction(self, page: Page, step: dict[str, Any]) -> None:
        action = str(step.get("action") or "").strip().lower()
        try:
            if action == "wait":
                page.wait_for_timeout(int(step.get("ms") or 500))
            elif action == "click_text":
                text = str(step.get("text") or "").strip()
                if not text:
                    return
                times = int(step.get("times") or 1)
                interval_ms = int(step.get("interval_ms") or 200)
                locator = page.get_by_text(text, exact=False).first
                for i in range(times):
                    locator.click(timeout=5_000)
                    if i < times - 1:
                        page.wait_for_timeout(interval_ms)
            elif action == "click_selector":
                selector = str(step.get("selector") or "").strip()
                if not selector:
                    return
                times = int(step.get("times") or 1)
                interval_ms = int(step.get("interval_ms") or 200)
                locator = page.locator(selector).first
                for i in range(times):
                    locator.click(timeout=5_000)
                    if i < times - 1:
                        page.wait_for_timeout(interval_ms)
            elif action == "scroll":
                y = int(step.get("y") or 300)
                page.mouse.wheel(0, y)
            elif action == "keyboard":
                key = str(step.get("key") or "").strip()
                if key:
                    page.keyboard.press(key)
            else:
                self.logger.warn("shot.interaction.unknown", action=action)
        except Exception as exc:
            # 交互失败不 fatal（截图仍然产出），只记录
            self.logger.warn("shot.interaction.failed",
                             action=action, error=str(exc)[:200])


# ============================================================
#  批量渲染入口
# ============================================================

def render_all_shots(
    scenes: list[dict[str, Any]],
    *,
    project_root: Path,
    dev_server_url: str | None,
    output_dir: Path,
    style: dict[str, Any],
    logger: PipelineLogger,
    resume: bool = True,
) -> list[str]:
    """按 scenes 顺序把每一张 shot 渲染成 output_dir/images/<id>.<ext>。

    Args:
        dev_server_url: 若 scenes 含 ui/ui_video/cover 类型必须提供；纯 code 场景可传 None
        resume: 已存在的文件是否复用

    Returns:
        与 scenes 同顺序的媒体绝对路径列表；ui_video 类型产出 .mp4，其余 .png
    """
    output_images_dir = output_dir / "images"
    output_images_dir.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    with ShotRenderer(style, logger) as renderer:
        for idx, scene in enumerate(scenes, start=1):
            scene_id = scene["id"]
            spec = scene.get("shot_spec") or {}
            stype = spec.get("type", "ui")
            ext = shot_extension(spec)
            out = output_images_dir / f"{scene_id}{ext}"

            if resume and out.exists():
                logger.info("shot.skip_existing", scene_id=scene_id, path=str(out))
                paths.append(str(out))
                continue

            logger.info("shot.render.start", scene_id=scene_id, type=stype, index=idx)
            try:
                if stype == "code":
                    renderer.render_code(spec, project_root, out)
                elif stype == "cover":
                    if not dev_server_url:
                        raise SystemExit(f"场景 {scene_id} 需要 dev server（cover 类型），但未启动")
                    renderer.render_cover(spec, dev_server_url, out)
                else:  # ui / ui_video —— 一律动态视频
                    if not dev_server_url:
                        raise SystemExit(f"场景 {scene_id} 需要 dev server（{stype} 类型），但未启动")
                    renderer.render_ui_video(spec, dev_server_url, out)
            except Exception as exc:
                logger.error("shot.render.failed", scene_id=scene_id, error=str(exc)[:200])
                raise

            paths.append(str(out))

    logger.info("step:shots.done", count=len(paths))
    return paths
