"""照片使用账本：跨次运行记录"哪些图已经被用进视频了"。

存储：<source>/.used_photos.json，格式：
    {
      "version": 1,
      "source": "C:/Users/EDY/Pictures/2026-06/可用",
      "used": [
        {"file": "001.jpg", "used_at": "2026-07-05T15:30:00", "draft": "梗图_可用_20260705_153000"},
        {"file": "007.jpg", "used_at": "2026-07-05T15:30:00", "draft": "梗图_可用_20260705_153000"}
      ]
    }

使用哲学：
  * 用文件名（basename）作为身份标识，不用绝对路径 —— 目录被搬迁不会失效
  * 每次成功生成剪映草稿后追加一条记录
  * `reset()` 清空重来；`--show` 打印当前状态
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


DEFAULT_LEDGER_NAME = ".used_photos.json"


class UsedPhotosLedger:
    """一次运行内使用的读/写门面。"""

    def __init__(self, path: Path, source_dir: Path | None = None):
        self.path = Path(path)
        self.source_dir = str(source_dir) if source_dir else None
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                # 损坏文件时优雅降级为空账本（不覆盖，留给用户）
                return {"version": 1, "used": [], "source": self.source_dir}
        return {"version": 1, "used": [], "source": self.source_dir}

    # ---- 查询 ----

    def used_names(self) -> set[str]:
        return {e.get("file", "") for e in self.data.get("used", []) if e.get("file")}

    def count_used(self) -> int:
        return len(self.data.get("used", []))

    def entries(self) -> list[dict[str, Any]]:
        return list(self.data.get("used", []))

    # ---- 修改 ----

    def mark_used(self, filenames: list[str], draft_name: str = "") -> int:
        """把 filenames（只用 basename）标为已用；已经在账本里的会跳过。"""
        already = self.used_names()
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        added = 0
        for name in filenames:
            basename = Path(name).name
            if basename in already:
                continue
            self.data.setdefault("used", []).append({
                "file": basename,
                "used_at": now,
                "draft": draft_name,
            })
            already.add(basename)
            added += 1
        if self.source_dir:
            self.data["source"] = self.source_dir
        return added

    def reset(self) -> None:
        self.data = {"version": 1, "used": [], "source": self.source_dir}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def default_ledger_path(source_dir: Path) -> Path:
    """账本默认放在 source_dir/.used_photos.json"""
    return Path(source_dir) / DEFAULT_LEDGER_NAME
