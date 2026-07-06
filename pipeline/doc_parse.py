"""Step 0 · make_doc_video.py 专用：PDF 抽文本 + mp4 元信息 + 均匀抽帧。

三件事：
  1. extract_pdf_text  ——  pdfplumber 抽 PDF 全文，去掉空行/纯页码行。
  2. probe_video       ——  ffprobe 读 mp4 时长/分辨率/帧率，用 imageio-ffmpeg 附带的二进制。
  3. extract_frames    ——  ffmpeg 均匀抽 N 张 720p JPEG，作为视觉大模型的输入。

产物：doc_content.json（文本 + 视频元信息），以及 frames/frame_00.jpg ... frame_0N.jpg。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
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
# 这不是最优雅方案，但省去让用户额外装 ffprobe 的负担，同一二进制一把梭。
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", re.IGNORECASE)
_STREAM_VIDEO_RE = re.compile(
    r"Stream.*?Video:.*?,\s*(\d+)x(\d+).*?(\d+(?:\.\d+)?)\s*fps",
    re.IGNORECASE,
)


@dataclass
class VideoMeta:
    path: str
    duration_s: float
    width: int
    height: int
    fps: float


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
        # 分辨率/帧率解析失败退化为默认值，不影响主流水线（切段用不到）
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


def _clean_pdf_line(line: str) -> str:
    """去掉行首尾空白 + 纯页码/纯页眉噪声。"""
    stripped = line.strip()
    if not stripped:
        return ""
    # 纯数字行大概率是页码
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
        # 极短 = 大概率扫描件；不 fail，只提示
        if logger:
            logger.warn(
                "doc_parse.pdf.suspicious_short",
                hint="PDF 抽出的文本极少，可能是扫描件（无文本层）。当前流水线不做 OCR。",
            )

    return result


def extract_frames(
    mp4_path: Path,
    out_dir: Path,
    n: int = 8,
    *,
    duration_s: float | None = None,
    logger: PipelineLogger | None = None,
    height: int = 720,
) -> list[Path]:
    """在视频里均匀抽 n 张 JPEG（等距时间点 + 单帧提取），落在 out_dir 下。

    命名：frame_00.jpg ... frame_{n-1}.jpg
    统一 scale 到 720p 高度（等比），控制单帧 base64 后的体积，避免视觉模型请求过大。
    """
    if not mp4_path.exists():
        raise SystemExit(f"视频文件不存在：{mp4_path}")
    if n <= 0:
        raise SystemExit(f"抽帧数量必须 > 0，收到 n={n}")

    out_dir.mkdir(parents=True, exist_ok=True)

    if duration_s is None:
        duration_s = probe_video(mp4_path, logger=logger).duration_s

    # 均匀分布：把时长分 n+1 份，取每份中点
    # 避免第 0 秒（片头黑屏）和末尾（片尾静止）导致的信息量塌陷
    timestamps = [duration_s * (i + 0.5) / n for i in range(n)]

    frame_paths: list[Path] = []
    for idx, ts in enumerate(timestamps):
        out_path = out_dir / f"frame_{idx:02d}.jpg"
        cmd = [
            _FFMPEG_EXE, "-y",
            "-ss", f"{ts:.3f}",
            "-i", str(mp4_path),
            "-frames:v", "1",
            "-vf", f"scale=-2:{height}",  # 等比缩放到 height 高，宽自动取偶数
            "-q:v", "3",                  # JPEG 质量，2-5 之间，3 已经很清晰
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists():
            raise SystemExit(
                f"ffmpeg 抽帧失败（时间点 {ts:.2f}s → {out_path.name}）：\n"
                f"stderr:\n{(result.stderr or '')[-1000:]}"
            )
        frame_paths.append(out_path)

    if logger:
        logger.info(
            "doc_parse.frames",
            count=len(frame_paths),
            out_dir=str(out_dir),
            timestamps=[round(t, 2) for t in timestamps],
        )
    return frame_paths


def save_doc_content(
    output_path: Path,
    pdf_result: dict[str, Any],
    video_meta: VideoMeta,
    frame_paths: list[Path],
    frame_timestamps: list[float],
) -> None:
    """把 Step 0 全部产物落盘成一份 JSON，便于 --skip-parse 复用。"""
    payload = {
        "pdf": {
            "path": pdf_result["path"],
            "page_count": pdf_result["page_count"],
            "char_count": pdf_result["char_count"],
            "full_text": pdf_result["full_text"],
        },
        "video": asdict(video_meta),
        "frames": [
            {"index": i, "path": str(p), "timestamp_s": round(t, 3)}
            for i, (p, t) in enumerate(zip(frame_paths, frame_timestamps))
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_doc_content(path: Path) -> dict[str, Any]:
    """读取磁盘上已保存的 doc_content.json（--skip-parse 用）。"""
    if not path.exists():
        raise SystemExit(
            f"找不到 {path}；不能 --skip-parse。请先跑一次不带 --skip-parse 的完整命令。"
        )
    return json.loads(path.read_text(encoding="utf-8"))
