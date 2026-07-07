"""Step 0 · make_doc_video.py / make_narration_video.py 专用：
PDF 抽文本 + mp4 元信息 + 抽帧（支持镜头切换检测 + 在切换点加密抽帧）。

核心函数：
  1. extract_pdf_text     ——  pdfplumber 抽 PDF 全文，去掉空行/纯页码行。
  2. probe_video          ——  ffmpeg 读 mp4 时长/分辨率/帧率（复用 imageio-ffmpeg 二进制）。
  3. detect_scene_changes ——  ffmpeg `select=gt(scene,thr)` 检测镜头切换点时间戳。
  4. extract_frames       ——  按间隔均匀抽帧 + 镜头切换点额外抽帧，统一缩到 720p JPEG。

产物：doc_content.json（文本 + 视频元信息 + 每帧元数据），以及 frames/frame_00.jpg ...
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
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

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 pdfplumber 依赖。请运行：\n"
        "  pip install pdfplumber"
    ) from exc

# 抑制 pdfminer.six 从 PDF 字体描述符解析 FontBBox 时的噪音日志。
# 对我们的用例（只抽纯文本）没影响，但会刷屏干扰用户看进度。
import logging as _stdlogging
for _noisy in ("pdfminer", "pdfminer.pdfinterp", "pdfminer.pdffont",
               "pdfminer.pdfpage", "pdfminer.converter", "pdfminer.cmapdb"):
    _stdlogging.getLogger(_noisy).setLevel(_stdlogging.ERROR)

from pipeline.helpers import PipelineLogger


# imageio-ffmpeg 只装了 ffmpeg 不含 ffprobe，因此视频元信息用 `ffmpeg -i` 的 stderr 解析。
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", re.IGNORECASE)
_STREAM_VIDEO_RE = re.compile(
    r"Stream.*?Video:.*?,\s*(\d+)x(\d+).*?(\d+(?:\.\d+)?)\s*fps",
    re.IGNORECASE,
)
# ffmpeg showinfo 滤镜输出行，解析 pts_time（秒）
# 例：...pts_time:4.203125...
_SCENE_PTS_RE = re.compile(r"pts_time:(\d+\.\d+)")
_SCENE_SCORE_RE = re.compile(r"scene:score=(\d+\.\d+)")


@dataclass
class VideoMeta:
    path: str
    duration_s: float
    width: int
    height: int
    fps: float


@dataclass
class FrameExtractResult:
    """extract_frames 的返回值。

    paths / timestamps / is_scene_change 三个列表等长，下标一一对应。
    timestamps 已排序（单调递增）。scene_changes 是检测到的镜头切换点时间戳列表。
    """
    paths: list[Path] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    is_scene_change: list[bool] = field(default_factory=list)
    scene_changes: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.paths)


def _probe_via_ffmpeg_stderr(mp4_path: Path) -> VideoMeta:
    """跑一次 `ffmpeg -i <mp4>`（不指定输出）从 stderr 解析视频元信息。"""
    result = subprocess.run(
        [_FFMPEG_EXE, "-i", str(mp4_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # ffmpeg 只输入不输出会返 1，这是预期，信息在 stderr
    stderr = result.stderr or ""

    m = _DURATION_RE.search(stderr)
    if not m:
        raise SystemExit(
            f"无法从 ffmpeg 输出解析视频时长：{mp4_path.name}\n"
            f"stderr 片段：\n{stderr[-800:]}"
        )
    h, mm, s = m.groups()
    duration_s = int(h) * 3600 + int(mm) * 60 + float(s)

    m2 = _STREAM_VIDEO_RE.search(stderr)
    if not m2:
        width, height, fps = 1920, 1080, 30.0
    else:
        width, height = int(m2.group(1)), int(m2.group(2))
        fps = float(m2.group(3))

    return VideoMeta(
        path=str(mp4_path),
        duration_s=round(duration_s, 3),
        width=width,
        height=height,
        fps=fps,
    )


def probe_video(mp4_path: Path, logger: PipelineLogger | None = None) -> VideoMeta:
    """读取 mp4 元信息（时长、分辨率、帧率）。"""
    if not mp4_path.exists():
        raise SystemExit(f"视频文件不存在：{mp4_path}")
    meta = _probe_via_ffmpeg_stderr(mp4_path)
    if logger:
        logger.info(
            "doc_parse.probe_video",
            path=str(mp4_path),
            duration_s=meta.duration_s,
            resolution=f"{meta.width}x{meta.height}",
            fps=meta.fps,
        )
    return meta


def detect_scene_changes(
    mp4_path: Path,
    threshold: float = 0.4,
    logger: PipelineLogger | None = None,
) -> list[float]:
    """用 ffmpeg `select='gt(scene,thr)',showinfo` 检测镜头切换点。

    返回时间戳列表（秒），单调递增。threshold 越小越敏感（0.2 很敏感、0.5 迟钝）。
    返回的时间戳不包括 0.0 和 duration_s（那两个不算真正的"切换"）。
    """
    if not mp4_path.exists():
        raise SystemExit(f"视频文件不存在：{mp4_path}")
    if not (0.0 < threshold < 1.0):
        raise SystemExit(f"scene threshold 必须在 (0,1) 之间，收到 {threshold}")

    # -f null -  不写文件，只跑滤镜；showinfo 把 PTS 打到 stderr
    cmd = [
        _FFMPEG_EXE, "-i", str(mp4_path),
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # ffmpeg -f null 正常也会返 0；失败则根据 stderr 判断
    stderr = result.stderr or ""

    # showinfo 输出里每一行代表一个被 select 选中的帧；从中抠 pts_time
    # 注意：第一个切换点通常是开场帧（pts≈0），用 >0.1s 过滤掉
    changes: list[float] = []
    for line in stderr.splitlines():
        if "showinfo" not in line or "pts_time" not in line:
            continue
        m = _SCENE_PTS_RE.search(line)
        if not m:
            continue
        ts = float(m.group(1))
        if ts < 0.1:
            continue
        changes.append(round(ts, 3))

    # 去重 + 排序（ffmpeg 输出按时间顺序，但保险起见）
    changes = sorted(set(changes))

    if logger:
        logger.info(
            "doc_parse.scene_changes",
            threshold=threshold,
            count=len(changes),
            timestamps=changes[:30],
        )
    return changes


def _clean_pdf_line(line: str) -> str:
    """去掉行首尾空白 + 纯页码/纯页眉噪声。"""
    stripped = line.strip()
    if not stripped:
        return ""
    if re.fullmatch(r"[\d\-·/\s]+", stripped) and len(stripped) <= 10:
        return ""
    return stripped


def extract_pdf_text(
    pdf_path: Path,
    logger: PipelineLogger | None = None,
    *,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """抽 PDF 全文本；返回 {full_text, page_count, char_count, pages: [str, ...]}。

    对扫描版 PDF（无文本层）会得到大量空页，char_count 会很小，调用方可据此报错并提示用户装 OCR。
    """
    if not pdf_path.exists():
        raise SystemExit(f"PDF 文件不存在：{pdf_path}")

    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        limit = min(max_pages, total_pages) if max_pages else total_pages
        for i in range(limit):
            page = pdf.pages[i]
            raw = page.extract_text() or ""
            cleaned = "\n".join(
                _clean_pdf_line(line) for line in raw.splitlines() if _clean_pdf_line(line)
            )
            pages.append(cleaned)

    full_text = "\n\n".join(p for p in pages if p).strip()
    result = {
        "path": str(pdf_path),
        "page_count": len(pages),
        "char_count": len(full_text),
        "pages": pages,
        "full_text": full_text,
    }

    if logger:
        logger.info(
            "doc_parse.pdf",
            path=str(pdf_path),
            page_count=result["page_count"],
            char_count=result["char_count"],
        )
    if result["char_count"] < 50:
        if logger:
            logger.warn(
                "doc_parse.pdf.suspicious_short",
                hint="PDF 抽出的文本极少，可能是扫描件（无文本层）。当前流水线不做 OCR。",
            )

    return result


def _run_ffmpeg_one_frame(
    mp4_path: Path, ts: float, out_path: Path, height: int,
) -> None:
    """单个时间点抽一帧，失败抛 SystemExit。"""
    cmd = [
        _FFMPEG_EXE, "-y",
        "-ss", f"{ts:.3f}",
        "-i", str(mp4_path),
        "-frames:v", "1",
        "-vf", f"scale=-2:{height}",
        "-q:v", "3",
        str(out_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not out_path.exists():
        raise SystemExit(
            f"ffmpeg 抽帧失败（时间点 {ts:.2f}s → {out_path.name}）：\n"
            f"stderr:\n{(result.stderr or '')[-1000:]}"
        )


def extract_frames(
    mp4_path: Path,
    out_dir: Path,
    n: int = 8,
    *,
    duration_s: float | None = None,
    logger: PipelineLogger | None = None,
    height: int = 720,
    frame_interval_s: float | None = None,
    scene_changes: list[float] | None = None,
    scene_extra_pad_s: float = 0.1,
) -> FrameExtractResult:
    """按等间隔均匀抽帧 + 在镜头切换点额外加抽帧，返回 FrameExtractResult。

    参数：
      n                   旧参数：等间隔抽 n 张；若指定了 frame_interval_s 则按秒间隔计算 n。
      frame_interval_s    每隔多少秒均匀抽一帧（默认 None，由 n 决定）。
      scene_changes       detect_scene_changes() 的结果；提供后会在每个切换点 + scene_extra_pad_s
                          的位置额外抽一帧（标记 is_scene_change=True）。提供 None / [] 则
                          只有均匀帧。
      scene_extra_pad_s   切换点后偏移多少秒抽帧（避免拿到切换前一帧的残像），默认 0.1s。
      height              输出 JPEG 高度（等比缩放），默认 720。

    返回的 FrameExtractResult 里 timestamps 已排序去重（如果均匀帧和切换帧撞在一起会去重）。
    """
    if not mp4_path.exists():
        raise SystemExit(f"视频文件不存在：{mp4_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    if duration_s is None:
        duration_s = probe_video(mp4_path, logger=logger).duration_s

    # ---- 1) 计算均匀帧时间点 ----
    if frame_interval_s and frame_interval_s > 0:
        # 按秒间隔：每 frame_interval_s 取一个中点式的位置，至少 1 张
        import math
        n_computed = max(1, math.ceil(duration_s / frame_interval_s))
        uniform_ts = [duration_s * (i + 0.5) / n_computed for i in range(n_computed)]
    else:
        if n <= 0:
            raise SystemExit(f"抽帧数量必须 > 0，收到 n={n}")
        uniform_ts = [duration_s * (i + 0.5) / n for i in range(n)]

    # ---- 2) 合并镜头切换点（+小偏移） ----
    extra_ts: list[float] = []
    if scene_changes:
        for sc in scene_changes:
            t = sc + scene_extra_pad_s
            if 0.05 < t < duration_s - 0.05:
                extra_ts.append(round(t, 3))

    # ---- 3) 合并、去重、排序 ----
    # 去重容差 0.2s（两帧相差 <0.2s 视为撞点，只留一张并优先标记为 scene_change）
    all_ts: list[tuple[float, bool]] = []  # (ts, is_scene)
    # 先放均匀帧，再放切换帧；切换帧会覆盖同一位置的 is_scene 标记
    for t in uniform_ts:
        all_ts.append((round(t, 3), False))
    for t in extra_ts:
        all_ts.append((round(t, 3), True))

    # 按时间排序，相同时间点优先保留 is_scene=True 版本
    all_ts.sort(key=lambda x: (x[0], 0 if x[1] else 1))
    merged: list[tuple[float, bool]] = []
    for ts, is_sc in all_ts:
        if merged and abs(ts - merged[-1][0]) < 0.2:
            # 撞点：合并，is_scene 取 OR
            prev_ts, prev_sc = merged[-1]
            merged[-1] = (prev_ts, prev_sc or is_sc)
        else:
            merged.append((ts, is_sc))

    # ---- 4) 真正抽帧 ----
    result = FrameExtractResult()
    result.scene_changes = list(scene_changes) if scene_changes else []

    for idx, (ts, is_sc) in enumerate(merged):
        out_path = out_dir / f"frame_{idx:02d}.jpg"
        _run_ffmpeg_one_frame(mp4_path, ts, out_path, height)
        result.paths.append(out_path)
        result.timestamps.append(ts)
        result.is_scene_change.append(is_sc)

    if logger:
        logger.info(
            "doc_parse.frames",
            count=len(result.paths),
            uniform=len(uniform_ts),
            extra_scene=len(extra_ts),
            scene_changes=len(result.scene_changes),
            out_dir=str(out_dir),
        )
    return result


def save_doc_content(
    output_path: Path,
    pdf_result: dict[str, Any] | None,
    video_meta: VideoMeta,
    frames: FrameExtractResult,
) -> None:
    """把 Step 0 全部产物落盘成一份 JSON，便于 --skip-parse 复用。

    pdf_result 为 None 时写入空 stub，供 make_narration_video.py 这类纯视频入口复用。
    """
    if pdf_result is None:
        pdf_section = {"path": "", "page_count": 0, "char_count": 0, "full_text": ""}
    else:
        pdf_section = {
            "path": pdf_result["path"],
            "page_count": pdf_result["page_count"],
            "char_count": pdf_result["char_count"],
            "full_text": pdf_result["full_text"],
        }
    payload = {
        "pdf": pdf_section,
        "video": asdict(video_meta),
        "scene_changes": frames.scene_changes,
        "frames": [
            {
                "index": i,
                "path": str(p),
                "timestamp_s": round(t, 3),
                "is_scene_change": bool(sc),
            }
            for i, (p, t, sc) in enumerate(
                zip(frames.paths, frames.timestamps, frames.is_scene_change)
            )
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_doc_content(path: Path) -> dict[str, Any]:
    """读取磁盘上已保存的 doc_content.json（--skip-parse 用）。兼容旧版本无 scene_changes 字段。"""
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}；不能 --skip-parse。请先跑一次不带 --skip-parse 的完整命令。"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("scene_changes", [])
    for f in data.get("frames", []):
        f.setdefault("is_scene_change", False)
    return data
