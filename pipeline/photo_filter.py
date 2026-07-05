"""按图像尺寸/宽高比筛选照片，分成三档：usable / rejected / animated（gif/mp4）。

判定规则（默认，均可覆盖）：
  usable：非动图、任一边 >= min_dim(400)、宽高比 min_ratio(0.5) ~ max_ratio(2.5)
  rejected：非动图、但不满足 usable 的尺寸/比例约束（附上原因）
  animated：.gif / .mp4 / .mov / .webm 等动画/视频扩展名
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


STATIC_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp",
               ".tif", ".tiff", ".heic", ".heif"}
ANIMATED_EXTS = {".gif", ".mp4", ".mov", ".avi", ".mkv", ".wmv",
                 ".webm", ".m4v", ".3gp"}


@dataclass
class PhotoInfo:
    path: Path
    width: int
    height: int
    aspect: float          # width / height

    @property
    def orientation(self) -> str:
        if 0.85 <= self.aspect <= 1.18:
            return "方"
        return "横" if self.aspect > 1.0 else "竖"


@dataclass
class CurationResult:
    usable:    list[PhotoInfo]
    rejected:  list[tuple[PhotoInfo, str]]   # (info, 原因)
    animated:  list[Path]
    unreadable: list[tuple[Path, str]]       # (path, 错误消息)
    source_dir: Path

    def summary(self) -> str:
        buckets = {"方": 0, "竖": 0, "横": 0}
        for p in self.usable:
            buckets[p.orientation] += 1
        lines = [
            "─" * 60,
            f"  源目录: {self.source_dir}",
            f"  可用静态图:   {len(self.usable)}   (方 {buckets['方']} / 竖 {buckets['竖']} / 横 {buckets['横']})",
            f"  剔除:         {len(self.rejected)}",
            f"  动图/视频:    {len(self.animated)} （不参与本次梗图视频，可另做）",
        ]
        if self.unreadable:
            lines.append(f"  ⚠ 读不出:     {len(self.unreadable)}")
        return "\n".join(lines)

    def rejected_report(self, limit: int = 10) -> str:
        if not self.rejected:
            return "  （无剔除）"
        lines = [f"  剔除清单（前 {min(limit, len(self.rejected))} 个）："]
        for info, reason in self.rejected[:limit]:
            lines.append(f"    {info.width:>5}x{info.height:<5} r={info.aspect:.2f}  {reason}  {info.path.name}")
        if len(self.rejected) > limit:
            lines.append(f"    ... 还有 {len(self.rejected) - limit} 个")
        return "\n".join(lines)


def curate_directory(
    source_dir: Path,
    *,
    min_dim: int = 400,
    min_ratio: float = 0.5,
    max_ratio: float = 2.5,
    recursive: bool = False,
) -> CurationResult:
    """扫描目录里的图像，按 usable / rejected / animated 分档。"""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise SystemExit(f"目录不存在: {source_dir}")

    iterator: Iterable[Path]
    iterator = source_dir.rglob("*") if recursive else source_dir.iterdir()

    usable:    list[PhotoInfo] = []
    rejected:  list[tuple[PhotoInfo, str]] = []
    animated:  list[Path] = []
    unreadable: list[tuple[Path, str]] = []

    for f in iterator:
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in ANIMATED_EXTS:
            animated.append(f)
            continue
        if ext not in STATIC_EXTS:
            continue  # 完全非图片扩展名直接忽略

        try:
            with Image.open(f) as img:
                w, h = img.size
        except Exception as exc:
            unreadable.append((f, str(exc)[:80]))
            continue

        if w <= 0 or h <= 0:
            unreadable.append((f, f"size={w}x{h}"))
            continue

        info = PhotoInfo(path=f, width=w, height=h, aspect=w / h)

        if w < min_dim or h < min_dim:
            rejected.append((info, f"太小(<{min_dim})"))
        elif info.aspect < min_ratio:
            rejected.append((info, f"过窄(r<{min_ratio})"))
        elif info.aspect > max_ratio:
            rejected.append((info, f"过宽(r>{max_ratio})"))
        else:
            usable.append(info)

    return CurationResult(
        usable=usable,
        rejected=rejected,
        animated=animated,
        unreadable=unreadable,
        source_dir=source_dir,
    )
