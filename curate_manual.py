"""手动挑图工具：一张张浏览目录里的图片，不合适的一键移到指定子目录。

    # 默认：在 --source 下建 "不太合适" 子目录接收被踢出的图
    python curate_manual.py --source "C:/Users/EDY/Pictures/2026-06/可用"

    # 自定义"不合适"目录名（相对 source）或绝对路径
    python curate_manual.py --source "..." --reject-dir "先放一放"
    python curate_manual.py --source "..." --reject-dir "D:/回收站/挑剩下的"

快捷键：
    → / SPACE / D    下一张
    ← / A            上一张
    X / DELETE        标记为"不合适" → 立刻移走 → 跳到下一张
    Z / Ctrl+Z        撤销上一次移动
    Q / ESC           退出
    F                 全屏切换
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:
    print("需要 Python 内置的 tkinter；你的 Python 安装可能没带 GUI 支持。")
    sys.exit(1)

from PIL import Image, ImageTk


STATIC_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp",
               ".tif", ".tiff", ".heic", ".heif"}

BG_DARK = "#1e1e1e"
FG_TEXT = "#eeeeee"
FG_DIM = "#888888"
ACCENT_REJECT = "#c8402c"


class PhotoCurator(tk.Tk):
    def __init__(self, source: Path, reject_dir: Path):
        super().__init__()
        self.source = source
        self.reject_dir = reject_dir
        self.reject_dir.mkdir(parents=True, exist_ok=True)

        self.photos = self._load_photos()
        self.index = 0
        # (moved_dst_path, original_src_path) 栈，最新在末尾
        self.moved_stack: list[tuple[Path, Path]] = []

        self.title(f"手动挑图 · {source.name}")
        self.geometry("1080x900")
        self.configure(bg=BG_DARK)
        self.minsize(600, 500)

        self._build_ui()
        self._bind_keys()

        # 防止 <Configure> 频繁触发导致重绘卡顿
        self._resize_after_id: str | None = None

        # 等窗口 mapped 完拿到真实尺寸后再首次显示
        self.after(80, self._show_current)

        if not self.photos:
            self.after(200, self._empty_dir_warning)

    # ------------------------------------------------------------------
    #  UI build
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # 顶部状态条
        self.status_var = tk.StringVar()
        tk.Label(self, textvariable=self.status_var, fg=FG_TEXT, bg=BG_DARK,
                 font=("Microsoft YaHei", 13, "bold"), pady=6
                 ).pack(fill=tk.X)

        self.filename_var = tk.StringVar()
        tk.Label(self, textvariable=self.filename_var, fg=FG_DIM, bg=BG_DARK,
                 font=("Microsoft YaHei", 10)
                 ).pack(fill=tk.X, pady=(0, 4))

        # 图片区
        self.image_frame = tk.Frame(self, bg="#000")
        self.image_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.image_label = tk.Label(self.image_frame, bg="#000")
        self.image_label.pack(fill=tk.BOTH, expand=True)
        self.image_frame.bind("<Configure>", self._on_resize)

        # 按钮条
        btn_bar = tk.Frame(self, bg=BG_DARK, pady=8)
        btn_bar.pack(fill=tk.X)

        tk.Button(btn_bar, text="← 上一张 (A)", command=self.prev_photo,
                  font=("Microsoft YaHei", 11), padx=18, pady=6
                  ).pack(side=tk.LEFT, padx=(20, 6))

        tk.Button(btn_bar, text="↶ 撤销 (Z)", command=self.undo,
                  font=("Microsoft YaHei", 11), padx=18, pady=6
                  ).pack(side=tk.LEFT, padx=6)

        tk.Button(btn_bar, text="✗   标为不合适   [X]", command=self.reject_current,
                  bg=ACCENT_REJECT, fg="white", activebackground="#a12e1c",
                  activeforeground="white",
                  font=("Microsoft YaHei", 12, "bold"),
                  padx=28, pady=8
                  ).pack(side=tk.LEFT, expand=True, padx=20)

        tk.Button(btn_bar, text="下一张 → (D)", command=self.next_photo,
                  font=("Microsoft YaHei", 11), padx=18, pady=6
                  ).pack(side=tk.RIGHT, padx=(6, 20))

        # 底部提示
        hint = ("快捷键:  ← / A 上一张    → / D / 空格 下一张    "
                "X / Delete 移到不合适    Z 撤销    Q / Esc 退出    F 全屏")
        tk.Label(self, text=hint, fg=FG_DIM, bg=BG_DARK,
                 font=("Microsoft YaHei", 9), pady=4
                 ).pack(fill=tk.X)

    def _bind_keys(self) -> None:
        for k in ("<Right>", "<space>", "<d>", "<D>"):
            self.bind(k, lambda e: self.next_photo())
        for k in ("<Left>", "<a>", "<A>"):
            self.bind(k, lambda e: self.prev_photo())
        for k in ("<x>", "<X>", "<Delete>"):
            self.bind(k, lambda e: self.reject_current())
        for k in ("<z>", "<Z>", "<Control-z>"):
            self.bind(k, lambda e: self.undo())
        for k in ("<q>", "<Q>", "<Escape>"):
            self.bind(k, lambda e: self.destroy())
        self.bind("<f>", self._toggle_fullscreen)
        self.bind("<F>", self._toggle_fullscreen)
        self._is_fullscreen = False

    def _toggle_fullscreen(self, _e=None) -> None:
        self._is_fullscreen = not self._is_fullscreen
        self.attributes("-fullscreen", self._is_fullscreen)

    def _on_resize(self, _event) -> None:
        # 防抖：只在停止调整 200ms 后重绘
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(200, self._show_current)

    # ------------------------------------------------------------------
    #  Photo listing
    # ------------------------------------------------------------------
    def _load_photos(self) -> list[Path]:
        return sorted(
            [f for f in self.source.iterdir()
             if f.is_file() and f.suffix.lower() in STATIC_EXTS],
            key=lambda p: p.name.lower(),
        )

    def _empty_dir_warning(self) -> None:
        messagebox.showinfo(
            "空目录",
            f"{self.source} 里没有可浏览的静态图。\n\n"
            "小提示：如果该目录已被 curate_photos.py 拆成了子目录，"
            "请把 --source 指到子目录里，例如 .../2026-06/可用/"
        )
        self.destroy()

    # ------------------------------------------------------------------
    #  Rendering
    # ------------------------------------------------------------------
    def _show_current(self) -> None:
        if not self.photos:
            self.status_var.set("(全部处理完了)")
            self.filename_var.set("")
            self.image_label.config(image="")
            self.image_label.image = None
            return

        # 夹住 index
        self.index = max(0, min(self.index, len(self.photos) - 1))
        photo = self.photos[self.index]

        # 用当前 frame 的真实尺寸计算缩略
        fw = max(200, self.image_frame.winfo_width())
        fh = max(200, self.image_frame.winfo_height())

        try:
            with Image.open(photo) as im:
                orig_w, orig_h = im.size
                thumb = im.copy()
                thumb.thumbnail((fw - 8, fh - 8), Image.LANCZOS)
                tkimg = ImageTk.PhotoImage(thumb)
            self.image_label.config(image=tkimg, text="")
            self.image_label.image = tkimg  # 防止被 GC
        except Exception as exc:
            self.image_label.config(image="",
                                    text=f"图片加载失败：{exc}",
                                    fg="white")
            orig_w = orig_h = 0

        remain = len(self.photos)
        moved = len(self.moved_stack)
        self.status_var.set(
            f"{self.source.name}   ·   第 {self.index + 1} / {remain} 张"
            f"   ·   已移走 {moved} 张"
        )
        self.filename_var.set(f"{photo.name}    {orig_w}×{orig_h}")

    # ------------------------------------------------------------------
    #  Actions
    # ------------------------------------------------------------------
    def next_photo(self) -> None:
        if not self.photos:
            return
        if self.index < len(self.photos) - 1:
            self.index += 1
            self._show_current()

    def prev_photo(self) -> None:
        if not self.photos:
            return
        if self.index > 0:
            self.index -= 1
            self._show_current()

    def reject_current(self) -> None:
        if not self.photos:
            return
        cur = self.photos[self.index]
        dst = self.reject_dir / cur.name
        n = 1
        while dst.exists():
            dst = self.reject_dir / f"{cur.stem}_{n}{cur.suffix}"
            n += 1
        try:
            shutil.move(str(cur), str(dst))
        except Exception as exc:
            messagebox.showerror("移动失败", f"{cur.name}\n\n{exc}")
            return
        self.moved_stack.append((dst, cur))
        del self.photos[self.index]
        # index 保持指向"下一张"（即原来位置），若已到末尾则回退一格
        if self.index >= len(self.photos):
            self.index = len(self.photos) - 1
        self._show_current()

    def undo(self) -> None:
        if not self.moved_stack:
            return
        dst, orig = self.moved_stack.pop()
        if not dst.exists():
            messagebox.showwarning("撤销失败",
                                   f"目标文件已不在预期位置：\n{dst}")
            return
        try:
            shutil.move(str(dst), str(orig))
        except Exception as exc:
            messagebox.showerror("撤销失败", str(exc))
            self.moved_stack.append((dst, orig))
            return
        # 恢复到列表，并跳到它
        self.photos.append(orig)
        self.photos.sort(key=lambda p: p.name.lower())
        try:
            self.index = self.photos.index(orig)
        except ValueError:
            pass
        self._show_current()


# ---------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description="手动挑图：一张一张看，不合适的一键移到指定子目录",
    )
    p.add_argument("--source", type=str, required=True,
                   help="要浏览的目录（如 C:/Users/EDY/Pictures/2026-06/可用）")
    p.add_argument("--reject-dir", type=str, default="不太合适",
                   help='"不合适"图片的去处；相对路径解析为 --source 子目录，'
                        '也可传绝对路径（默认 "不太合适"）')
    args = p.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"--source 不是目录: {source}")

    reject_dir = Path(args.reject_dir).expanduser()
    if not reject_dir.is_absolute():
        reject_dir = source / reject_dir
    reject_dir = reject_dir.resolve()

    # 一个基本护栏：reject_dir 不能等于 source（那样会自己吃自己）
    if reject_dir == source:
        raise SystemExit("--reject-dir 不能就是 --source 本身")

    app = PhotoCurator(source, reject_dir)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
