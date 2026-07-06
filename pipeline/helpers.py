"""
流水线辅助工具 — 独立使用，不改动原项目任何文件。

功能：
1. retry  — 带指数退避的重试装饰器，适用于 API 调用
2. parallel_image_gen — 图片并行生成（原版是串行的）
3. CostEstimator — 运行前预估本次流水线的费用
4. PipelineLogger — 结构化日志，方便排查问题

使用示例：

    from improvements.pipeline_helpers import retry, ParallelImageGenerator

    @retry(max_attempts=3)
    def call_api():
        ...

    gen = ParallelImageGenerator(client, model, output_dir)
    paths = gen.generate(prompts)
"""

from __future__ import annotations

import json
import os
import re
import time
import logging
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import urllib.request

from PIL import Image, ImageChops


# ============================================================
#  通用工具（两条流水线共享）
# ============================================================

_SUBTITLE_SEPARATORS = re.compile(r'([，。！,!?；：:、\n])')


def smart_split(text: str) -> list[str]:
    """按中文/英文标点把一段 narration 拆成子句列表。

    保留结尾标点，跳过空段。若没有任何标点匹配，返回单元素列表 [原文]。
    tts.py 和 draft_composer.py 共用此函数，保证"切成几句 → TTS 几句 → 字幕几句"三路一致。
    """
    parts = _SUBTITLE_SEPARATORS.split(text)
    sentences: list[str] = []
    buf = ""
    for part in parts:
        if _SUBTITLE_SEPARATORS.fullmatch(part):
            buf += part
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
        else:
            buf += part
    if buf.strip():
        sentences.append(buf.strip())
    if not sentences:
        sentences = [text]
    return sentences


def strip_punctuation(text: str) -> str:
    """去掉子句尾部的中文/英文标点，返回干净字幕文本。"""
    return _SUBTITLE_SEPARATORS.sub('', text).strip()


def load_env(path: Path) -> None:
    """把 .env 文件里的键值对 setdefault 进 os.environ（不覆盖已存在的值）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        clean_key = key.strip().lstrip("\ufeff")
        os.environ.setdefault(clean_key, value.strip().strip('"').strip("'"))


def required_env(name: str, env_file_path: Path | None = None) -> str:
    """读取必填环境变量；缺失或仍是占位符时 SystemExit。"""
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("your_"):
        hint = f" Please set it in {env_file_path}." if env_file_path else ""
        raise SystemExit(f"Missing {name}.{hint}")
    return value


def safe_slug(value: str, fallback: str = "project") -> str:
    """把任意文本转成文件名安全的 slug，中文/字母数字/下划线/连字符保留，其余替换为下划线。"""
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value.strip())
    slug = slug.strip("_")
    return slug or fallback


def resolve_optional_path(value: str | None, root: Path) -> str | None:
    """把配置里相对路径解析为绝对路径（相对 root）；空值返回 None。"""
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return str(path)


def _repair_bare_quotes_in_string_values(text: str) -> str:
    """LLM 常见错误：在 JSON 字符串值内部使用了未转义的英文双引号。

    典型例子：  "story": "他被称为"新亨利"..."
    这里 story 字段值本身用 " 包裹，内部又出现 " —— JSON 直接坏掉。

    修复策略（按行）：识别形如 `  "key": "value...",` 或 `  "key": "value..."` 的行，
    定位 value 首尾引号，把 value 内部裸露的英文双引号统一替换成中文右双引号 " 。
    这样处理不动键、不动分隔符，只治字符串值内部的坏引号。
    """
    # 键行：以缩进 + "key" 开头，冒号后跟双引号开始的 value；行尾可有 , 或 }
    key_open = re.compile(r'^(?P<indent>\s*)"(?P<key>[A-Za-z_][\w]*)"\s*:\s*"')

    out_lines: list[str] = []
    for line in text.splitlines():
        m = key_open.match(line)
        if not m:
            out_lines.append(line)
            continue
        # value 起始位置（第一个 " 之后一位）
        val_start = m.end()
        # value 结束位置：从行末往前找最后一个 "，允许后面跟 , 或 } 或空白
        # 例如：  "key": "some "bare" value",
        #                          ^ value 结束 "
        tail = line.rstrip()
        # 逆向找结尾引号：允许行末是 ", 或 " 或 "}
        m_end = re.search(r'"(\s*[,}]?\s*)$', tail)
        if not m_end:
            out_lines.append(line)
            continue
        val_end = m_end.start()
        if val_end <= val_start:
            out_lines.append(line)
            continue
        inner = tail[val_start:val_end]
        # 只把裸英文双引号替换（保留已转义的 \"）
        # 先临时挡一下 \" 再还原
        SENTINEL = "\x00ESC_QUOTE\x00"
        CJK_RQUOTE = "\u201D"  # 中文右双引号 ” 显式转义，避免源码里被工具误改
        fixed_inner = inner.replace('\\"', SENTINEL).replace('"', CJK_RQUOTE).replace(SENTINEL, '\\"')
        out_lines.append(tail[:val_start] + fixed_inner + tail[val_end:])
    return "\n".join(out_lines)


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 返回中提取 JSON 对象。

    容错顺序：
      1. 直接 json.loads
      2. 去掉 ```json ... ``` markdown 围栏后再试
      3. 正则匹配首个 {...}
      4. 修复 JSON 字符串值内部的裸英文双引号后重试
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()

    # 尝试 1：直接解析
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 尝试 2：截取首个大括号包裹的对象
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    candidate = match.group(0) if match else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 尝试 3：修复裸英文引号后再试
    repaired = _repair_bare_quotes_in_string_values(candidate)
    return json.loads(repaired)


def prepare_output_dir(project_name: str, output_root: Path) -> Path:
    """创建 outputs/projects/<slug>/<时间戳>/ 目录及其 images/audio/responses 子目录。"""
    project_dir = output_root / safe_slug(project_name) / time.strftime("%Y%m%d_%H%M%S")
    for child in ("images", "audio", "responses"):
        (project_dir / child).mkdir(parents=True, exist_ok=True)
    return project_dir


def find_latest_output_dir(project_name: str, output_root: Path) -> Path | None:
    """在 outputs/projects/<slug>/ 下寻找最近一次运行目录（按目录名字典序，时间戳格式天然有序）。"""
    project_root = output_root / safe_slug(project_name)
    if not project_root.exists():
        return None
    candidates = [p for p in project_root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


# ============================================================
#  重试
# ============================================================

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    on_retry: callable | None = None,
):
    """指数退避重试装饰器

    delay = min(base_delay * backoff ^ (attempt - 1), max_delay)

    Args:
        max_attempts: 最大尝试次数（含首次）
        base_delay: 首次重试等待秒数
        backoff: 退避倍数
        max_delay: 最大等待秒数
        on_retry: 重试回调，签名为 (exception, attempt, delay) -> None
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (backoff ** (attempt - 1)), max_delay)
                    if on_retry:
                        on_retry(exc, attempt, delay)
                    time.sleep(delay)
            raise last_exc

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (backoff ** (attempt - 1)), max_delay)
                    if on_retry:
                        on_retry(exc, attempt, delay)
                    time.sleep(delay)
            raise last_exc

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    return decorator


# ============================================================
#  并行图片生成
# ============================================================

@dataclass
class ImageTask:
    id: str
    prompt: str


def _parse_aspect_ratio(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace("x", ":")
    if ":" in cleaned:
        left, right = cleaned.split(":", 1)
        try:
            width = float(left)
            height = float(right)
        except ValueError:
            return None
        return width / height if width > 0 and height > 0 else None
    try:
        ratio = float(cleaned)
    except ValueError:
        return None
    return ratio if ratio > 0 else None


def _crop_black_borders(image: Image.Image, threshold: int = 12) -> Image.Image:
    """Remove letterbox/pillarbox bars that the image model may paint in."""
    rgb = image.convert("RGB")
    background = Image.new("RGB", rgb.size, (0, 0, 0))
    diff = ImageChops.difference(rgb, background).convert("L")
    mask = diff.point(lambda p: 255 if p > threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    width, height = image.size
    if left <= 2 and top <= 2 and right >= width - 2 and bottom >= height - 2:
        return image
    return image.crop(bbox)


def _center_crop_to_aspect(image: Image.Image, aspect_ratio: float) -> Image.Image:
    width, height = image.size
    current = width / height
    if abs(current - aspect_ratio) < 0.01:
        return image
    if current > aspect_ratio:
        new_width = max(1, int(height * aspect_ratio))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = max(1, int(width / aspect_ratio))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


class ParallelImageGenerator:
    """并行图片生成器

    原版 run_poem_pipeline.py 里图片是逐张串行生成的，
    这里用 ThreadPoolExecutor 并发调用 API，大幅缩短等待时间。

    注意：不要设置过大的 max_workers，可能触发 API 限流。
    """

    def __init__(
        self,
        client: Any,
        model: str,
        output_dir: Path,
        *,
        size: str = "2K",
        output_format: str = "png",
        response_format: str = "url",
        watermark: bool = False,
        target_aspect_ratio: str | None = None,
        trim_black_borders: bool = True,
        black_border_threshold: int = 12,
        prompt_suffix: str = "",
        max_workers: int = 1,
        resume: bool = True,
        request_delay: float = 2.0,
        on_progress: callable | None = None,
    ):
        self.client = client
        self.model = model
        self.output_dir = output_dir
        self.size = size
        self.output_format = output_format
        self.response_format = response_format
        self.watermark = watermark
        self.target_aspect_ratio = _parse_aspect_ratio(target_aspect_ratio)
        self.trim_black_borders = trim_black_borders
        self.black_border_threshold = black_border_threshold
        self.prompt_suffix = prompt_suffix.strip()
        self.max_workers = max_workers
        self.resume = resume
        self.request_delay = request_delay
        self.on_progress = on_progress

    @staticmethod
    def _download(url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=180) as resp:
            return resp.read()

    def _final_prompt(self, prompt: str) -> str:
        if not self.prompt_suffix:
            return prompt
        return f"{prompt.rstrip()}{self.prompt_suffix}"

    def _postprocess(self, image_path: Path) -> None:
        if not self.trim_black_borders and not self.target_aspect_ratio:
            return
        with Image.open(image_path) as img:
            original_size = img.size
            processed = img.copy()
            if self.trim_black_borders:
                processed = _crop_black_borders(processed, self.black_border_threshold)
            if self.target_aspect_ratio:
                processed = _center_crop_to_aspect(processed, self.target_aspect_ratio)
            if processed.size != original_size:
                processed.save(image_path)

    def _generate_one(self, task: ImageTask) -> tuple[str, str]:
        """生成一张图片，返回 (task_id, local_path)

        带 429 限流重试（指数退避），避免因瞬时限流导致全盘失败。
        """
        image_path = self.output_dir / f"{task.id}.{self.output_format}"

        # 断点续跑：已存在的图片直接跳过
        if self.resume and image_path.exists():
            self._postprocess(image_path)
            return task.id, str(image_path)

        # 逐 request 间加延时，降低 API 限流风险
        if self.request_delay > 0:
            time.sleep(self.request_delay)

        last_exc = None
        for attempt in range(1, 4):  # 最多 3 次尝试（含首次）
            try:
                response = self.client.images.generate(
                    model=self.model,
                    prompt=self._final_prompt(task.prompt),
                    size=self.size,
                    output_format=self.output_format,
                    response_format=self.response_format,
                    watermark=self.watermark,
                )
                first = next((d for d in (response.data or []) if d.url), None)
                if not first:
                    raise RuntimeError(f"Image response for {task.id} had no URL")

                image_path.write_bytes(self._download(first.url))
                self._postprocess(image_path)
                return task.id, str(image_path)
            except Exception as exc:
                last_exc = exc
                if attempt < 3:
                    delay = min(2.0 * (2.0 ** (attempt - 1)), 30.0)
                    time.sleep(delay)
        raise RuntimeError(
            f"Image generation failed for {task.id} after 3 attempts: {last_exc}"
        )

    def generate(self, prompts: list[dict[str, str]]) -> dict[str, str]:
        """并行生成所有图片（支持断点续跑）

        Returns:
            {task_id: local_path, ...}

        Raises:
            RuntimeError: 有任务失败时，汇总所有失败信息
        """
        tasks = [ImageTask(id=p["id"], prompt=p["image_prompt"]) for p in prompts]
        results: dict[str, str] = {}
        completed = 0
        total = len(tasks)
        errors: list[str] = []

        # 断点续跑：先检查已完成的部分
        if self.resume:
            for t in tasks:
                img_path = self.output_dir / f"{t.id}.{self.output_format}"
                if img_path.exists():
                    results[t.id] = str(img_path)
            # 移除已存在的任务
            tasks = [t for t in tasks if t.id not in results]
            completed = len(results)
            for tid in results:
                if self.on_progress:
                    self.on_progress(tid, completed, total)

        if not tasks:
            return results

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(tasks))) as executor:
            future_map = {executor.submit(self._generate_one, t): t for t in tasks}
            for future in as_completed(future_map):
                task = future_map[future]
                try:
                    tid, path = future.result()
                    results[tid] = path
                    completed += 1
                    if self.on_progress:
                        self.on_progress(tid, completed, total)
                except Exception as exc:
                    errors.append(f"  - {task.id}: {exc}")

        if errors:
            raise RuntimeError(
                f"Image generation failed for {len(errors)} task(s):\n"
                + "\n".join(errors)
            )

        return results


# ============================================================
#  费用预估
# ============================================================

@dataclass
class CostEstimate:
    """费用预估结果"""
    image_count: int
    tts_chars: int
    image_estimated_tokens: int
    tts_estimated_tokens: int
    estimated_total: str
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            "========== 费用预估 ==========",
            f"  图片生成: {self.image_count} 张",
            f"  TTS 朗读: {self.tts_chars} 字",
            f"  预估 token 消耗: {self.estimated_total}",
        ]
        for note in self.notes:
            lines.append(f"  提示: {note}")
        lines.append("===============================")
        return "\n".join(lines)


class CostEstimator:
    """运行前预估流水线费用

    注：精确价格以火山引擎官方定价为准，这里只是粗估。
    """

    # 参考定价（以火山引擎公开价格为基准，单位：元/百万 token）
    _SEEDREAM_ESTIMATE_PER_IMAGE_2K = 0.15   # seedream-5.0-lite, 2K，约 0.15 元/张
    _TTS_ESTIMATE_PER_CHAR = 0.0001          # seed-tts-2.0，约 0.0001 元/字

    def __init__(self, image_price: float | None = None, tts_price: float | None = None):
        self.image_price = image_price or self._SEEDREAM_ESTIMATE_PER_IMAGE_2K
        self.tts_price = tts_price or self._TTS_ESTIMATE_PER_CHAR

    def estimate(self, lines: list[str]) -> CostEstimate:
        image_count = len(lines)
        tts_chars = sum(len(line) for line in lines)
        image_cost = image_count * self.image_price
        tts_cost = tts_chars * self.tts_price
        total = image_cost + tts_cost

        est = CostEstimate(
            image_count=image_count,
            tts_chars=tts_chars,
            image_estimated_tokens=image_count * 500,
            tts_estimated_tokens=tts_chars * 3,
            estimated_total=f"约 ¥{total:.4f}（{image_count} 张图 ¥{image_cost:.4f} + {tts_chars} 字 ¥{tts_cost:.4f}）",
        )
        if image_count > 10:
            est.notes.append("图片超过 10 张，建议分批生成避免 API 限流")
        return est


# ============================================================
#  结构化日志
# ============================================================

class PipelineLogger:
    """流水线专用日志器

    同时输出到控制台和 JSONL 文件，便于事后分析。
    """

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._log_path = log_dir / f"pipeline_{timestamp}.jsonl"
        self._console = logging.getLogger("pipeline")
        self._console.setLevel(logging.INFO)
        if not self._console.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            self._console.addHandler(handler)

    def log(self, level: str, event: str, **extra):
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": level,
            "event": event,
            **extra,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        getattr(self._console, level.lower(), self._console.info)(f"{event} {json.dumps(extra, ensure_ascii=False)}")

    def info(self, event: str, **extra):
        self.log("INFO", event, **extra)

    def warn(self, event: str, **extra):
        self.log("WARN", event, **extra)

    def error(self, event: str, **extra):
        self.log("ERROR", event, **extra)

    def step(self, name: str, **extra):
        """记录流水线步骤"""
        self.log("INFO", f"step:{name}", **extra)

    @property
    def log_path(self) -> Path:
        return self._log_path


# ============================================================
#  视觉大模型调用（make_doc_video.py 用）
# ============================================================

def vision_chat(
    images: list[Path],
    prompt: str,
    *,
    api_key: str | None = None,
    logger: "PipelineLogger | None" = None,
    base_url: str | None = None,
    model: str | None = None,
    temperature: float = 0.4,
    timeout: int = 300,
) -> str:
    """把若干张本地图片 + 一段文字 prompt 发给豆包视觉模型，返回文本回复。

    走 OpenAI 兼容多模态协议：content 是一个 list，包含 1 个 text part + N 个 image_url part，
    图片用 base64 data URL 直接内联，不依赖任何外部图床。

    优先级：显式传参 > VISION_API_KEY/VISION_BASE_URL/VISION_MODEL 环境变量 >
    回落到 AGENT_API_KEY / PROMPT_BASE_URL / "doubao-seed-1.6-flash"。
    这样可以让文本 LLM 走 agent-plan key，视觉单独走标准 Ark key，互不影响。
    """
    import base64
    import requests

    resolved_api_key = (
        api_key
        or os.environ.get("VISION_API_KEY")
        or os.environ.get("AGENT_API_KEY")
    )
    if not resolved_api_key:
        raise RuntimeError(
            "vision_chat 缺少 API Key：请设置 VISION_API_KEY 或 AGENT_API_KEY 环境变量。"
        )

    resolved_base_url = (
        base_url
        or os.environ.get("VISION_BASE_URL")
        or os.environ.get("PROMPT_BASE_URL")
        or "https://ark.cn-beijing.volces.com/api/plan/v3"
    ).rstrip("/")
    resolved_model = model or os.environ.get("VISION_MODEL", "doubao-seed-1.6-flash")

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for p in images:
        path = Path(p)
        suffix = path.suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else (
            "image/png" if suffix == ".png" else "image/jpeg"
        )
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })

    def _on_retry(exc, attempt, delay):
        if logger:
            logger.warn(
                "vision_chat.retry",
                attempt=attempt,
                delay=round(delay, 1),
                error=str(exc)[:180],
            )

    @retry(max_attempts=3, base_delay=2.0, on_retry=_on_retry)
    def _call() -> dict[str, Any]:
        resp = requests.post(
            f"{resolved_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {resolved_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": resolved_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": temperature,
            },
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"vision_chat HTTP {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    if logger:
        logger.info(
            "vision_chat.request",
            model=resolved_model,
            base_url=resolved_base_url,
            image_count=len(images),
            prompt_chars=len(prompt),
        )
    data = _call()
    return data["choices"][0]["message"]["content"]
