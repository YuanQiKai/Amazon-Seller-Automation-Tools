from __future__ import annotations

from pathlib import Path
from tkinter import BOTH, LEFT, RIGHT, X, messagebox
import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk


class MaskEditor(tk.Toplevel):
    """Simple brush-mask editor. Painted areas become transparent edit regions in the saved PNG."""

    def __init__(self, parent: tk.Misc, image_path: Path, destination: Path, on_apply: Callable[[Path], None]) -> None:
        super().__init__(parent)
        self.title("蒙版选区式局部重生")
        self.geometry("900x720")
        self.minsize(760, 600)
        self.image_path = image_path
        self.destination = destination
        self.on_apply = on_apply
        with Image.open(image_path) as source:
            self.source = source.convert("RGB")
        self.scale = min(780 / self.source.width, 540 / self.source.height, 1.0)
        self.preview_size = (max(1, int(self.source.width * self.scale)), max(1, int(self.source.height * self.scale)))
        self.mask_alpha = Image.new("L", self.source.size, 255)
        self.mask_draw = ImageDraw.Draw(self.mask_alpha)
        self.last_point: tuple[int, int] | None = None
        self.brush_var = tk.IntVar(value=60)

        ttk.Label(
            self,
            text="按住鼠标涂抹需要重新生成的区域。红色仅用于预览，保存的 PNG 会把选区写为透明蒙版。",
        ).pack(fill=X, padx=14, pady=(12, 6))
        self.canvas = tk.Canvas(self, width=self.preview_size[0], height=self.preview_size[1], bg="#20242a", highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True, padx=14, pady=6)
        preview = self.source.resize(self.preview_size, Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._paint)
        self.canvas.bind("<ButtonRelease-1>", self._end)

        toolbar = ttk.Frame(self, padding=(14, 6, 14, 14))
        toolbar.pack(fill=X)
        ttk.Label(toolbar, text="画笔大小").pack(side=LEFT)
        ttk.Scale(toolbar, from_=12, to=180, variable=self.brush_var, orient="horizontal", length=220).pack(side=LEFT, padx=8)
        ttk.Button(toolbar, text="清空选区", command=self.clear).pack(side=LEFT, padx=8)
        ttk.Button(toolbar, text="保存蒙版并重生", command=self.apply).pack(side=RIGHT)
        ttk.Button(toolbar, text="取消", command=self.destroy).pack(side=RIGHT, padx=8)

    def _coordinates(self, event) -> tuple[int, int]:
        x = max(0, min(self.source.width - 1, int(event.x / self.scale)))
        y = max(0, min(self.source.height - 1, int(event.y / self.scale)))
        return x, y

    def _start(self, event) -> None:
        self.last_point = self._coordinates(event)
        self._paint(event)

    def _paint(self, event) -> None:
        point = self._coordinates(event)
        start = self.last_point or point
        native_width = max(1, int(self.brush_var.get() / self.scale))
        self.mask_draw.line([start, point], fill=0, width=native_width, joint="curve")
        preview_start = (int(start[0] * self.scale), int(start[1] * self.scale))
        preview_point = (int(point[0] * self.scale), int(point[1] * self.scale))
        self.canvas.create_line(*preview_start, *preview_point, fill="#ff3b30", width=self.brush_var.get(), capstyle=tk.ROUND, smooth=True)
        self.last_point = point

    def _end(self, _event) -> None:
        self.last_point = None

    def clear(self) -> None:
        self.mask_alpha = Image.new("L", self.source.size, 255)
        self.mask_draw = ImageDraw.Draw(self.mask_alpha)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

    def apply(self) -> None:
        if self.mask_alpha.getextrema() == (255, 255):
            messagebox.showinfo("尚未选择区域", "请先涂抹需要重新生成的区域。", parent=self)
            return
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        rgba = Image.new("RGBA", self.source.size, (0, 0, 0, 255))
        rgba.putalpha(self.mask_alpha)
        rgba.save(self.destination, "PNG")
        self.destroy()
        self.on_apply(self.destination)
