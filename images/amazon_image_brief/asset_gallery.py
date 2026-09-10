from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageOps, ImageTk


def append_image_paths(existing: list[str], incoming: list[str]) -> list[str]:
    """Append unique absolute paths while keeping the user's original order."""
    result = list(existing)
    seen = {os.path.normcase(os.path.abspath(path)) for path in existing}
    for path in incoming:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen:
            result.append(os.path.abspath(path))
            seen.add(normalized)
    return result


class AssetGallery(ttk.Frame):
    def __init__(self, parent, remove_callback) -> None:
        super().__init__(parent)
        self.paths: list[str] = []
        self.checked_paths: set[str] = set()
        self.thumbnails: list[ImageTk.PhotoImage] = []
        self.preview_photo = None
        self.remove_callback = remove_callback
        listing = ttk.Frame(self)
        listing.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(listing, columns=('checked', 'name', 'size', 'path'), height=4, selectmode='extended')
        self.tree.heading('#0', text='缩略图')
        self.tree.column('#0', width=62, stretch=False)
        self.tree.heading('checked', text='☐ 全选', command=self.toggle_all)
        self.tree.column('checked', width=72, stretch=False, anchor='center')
        self.tree.heading('name', text='文件名（点击行预览，勾选框用于删除）')
        self.tree.column('name', width=220)
        self.tree.heading('size', text='尺寸')
        self.tree.column('size', width=110, stretch=False)
        self.tree.heading('path', text='文件路径')
        self.tree.column('path', width=280)
        style = ttk.Style(self)
        style.configure('Assets.Treeview', rowheight=56)
        self.tree.configure(style='Assets.Treeview')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.preview)
        self.tree.bind('<Button-1>', self.on_checkbox_click)
        self.tree.bind('<space>', self.on_checkbox_key)
        self.tree.bind('<Double-1>', self.on_double_click)
        right = ttk.Frame(self, padding=(10, 0, 0, 0))
        right.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_label = ttk.Label(right, text='选择图片预览', anchor='center', width=28)
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        ttk.Button(right, text='放大预览', command=self.open_large_preview).pack(fill=tk.X)
        actions = ttk.Frame(right)
        actions.pack(fill=tk.X, pady=3)
        ttk.Button(actions, text='全选', command=self.select_all).pack(side=tk.LEFT)
        ttk.Button(actions, text='取消勾选', command=self.uncheck_all).pack(side=tk.LEFT)
        ttk.Button(right, text='删除勾选图片', command=self.remove_selected).pack(fill=tk.X)
        ttk.Button(right, text='全部清空', command=self.clear_all).pack(fill=tk.X)
        self.count_var = tk.StringVar(value='共 0 张｜已勾选 0 张')
        ttk.Label(right, textvariable=self.count_var).pack(pady=3)

    def refresh(self, paths: list[str]) -> None:
        selected_paths = {self.paths[int(i)] for i in self.tree.selection() if int(i) < len(self.paths)}
        self.paths = list(paths)
        self.checked_paths.intersection_update(paths)
        self.tree.delete(*self.tree.get_children())
        self.thumbnails.clear()
        self.preview_photo = None
        self.preview_label.configure(image='', text='选择图片预览')
        for index, name in enumerate(paths):
            photo = ''
            dimensions = '文件不可用'
            try:
                with Image.open(name) as source:
                    thumb = ImageOps.exif_transpose(source).convert('RGB')
                    dimensions = f'{thumb.width}×{thumb.height}px'
                    thumb.thumbnail((48, 48))
                    photo = ImageTk.PhotoImage(thumb, master=self)
                    self.thumbnails.append(photo)
            except (OSError, ValueError):
                pass
            self.tree.insert('', 'end', iid=str(index), image=photo,
                             values=('☑' if name in self.checked_paths else '☐', Path(name).name, dimensions, name))
            if name in selected_paths:
                self.tree.selection_add(str(index))
        if paths and not self.tree.selection():
            self.tree.selection_set('0')
        self.preview()

    def preview(self, _event=None) -> None:
        selection = self.tree.selection()
        self.update_checks()
        if not selection:
            return
        try:
            with Image.open(self.paths[int(selection[0])]) as source:
                picture = ImageOps.exif_transpose(source).convert('RGB')
                picture.thumbnail((230, 165), Image.Resampling.LANCZOS)
                self.preview_photo = ImageTk.PhotoImage(picture, master=self)
            self.preview_label.configure(image=self.preview_photo, text='')
        except (OSError, ValueError, IndexError):
            self.preview_label.configure(image='', text='图片不存在或无法读取')

    def remove_selected(self) -> None:
        if not self.checked_paths:
            messagebox.showinfo('请先勾选图片', '点击列表中的 ☐ 勾选要删除的图片；仅点击行预览不会选中删除。', parent=self)
            return
        self.remove_callback([path for path in self.paths if path not in self.checked_paths])

    def update_checks(self) -> None:
        for iid in self.tree.get_children():
            self.tree.set(iid, 'checked', '☑' if self.paths[int(iid)] in self.checked_paths else '☐')
        all_checked = bool(self.paths) and len(self.checked_paths) == len(self.paths)
        self.tree.heading('checked', text='☑ 全选' if all_checked else '☐ 全选')
        self.count_var.set(f'共 {len(self.paths)} 张｜已勾选 {len(self.checked_paths)} 张')

    def toggle_checked(self, iid: str) -> None:
        path = self.paths[int(iid)]
        if path in self.checked_paths:
            self.checked_paths.remove(path)
        else:
            self.checked_paths.add(path)
        self.update_checks()

    def on_checkbox_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self.tree.identify_column(event.x) == '#1':
            self.toggle_checked(iid)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.focus_set()
            return 'break'

    def on_checkbox_key(self, _event=None):
        for iid in self.tree.selection():
            self.toggle_checked(iid)
        return 'break'

    def on_double_click(self, event):
        if self.tree.identify_column(event.x) != '#1':
            self.open_large_preview(event)
        return 'break'

    def uncheck_all(self) -> None:
        self.checked_paths.clear()
        self.update_checks()

    def toggle_all(self) -> None:
        self.uncheck_all() if self.paths and len(self.checked_paths) == len(self.paths) else self.select_all()

    def select_all(self) -> None:
        self.checked_paths = set(self.paths)
        self.tree.selection_set(self.tree.get_children())
        self.preview()

    def clear_all(self) -> None:
        if self.paths and messagebox.askyesno(
            '清空当前图片列表', f'清空本列表的 {len(self.paths)} 张图片吗？\n仅移除当前配置的引用，不删除原始文件或其他配置的图片。', parent=self):
            self.remove_callback([])

    def open_large_preview(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        name = self.paths[int(selection[0])]
        try:
            with Image.open(name) as source:
                picture = ImageOps.exif_transpose(source).convert('RGB')
                original_size = picture.size
                picture.thumbnail((1000, 700), Image.Resampling.LANCZOS)
        except OSError:
            return
        window = tk.Toplevel(self)
        window.title(f'{Path(name).name} — {original_size[0]}×{original_size[1]}px')
        photo = ImageTk.PhotoImage(picture, master=window)
        label = ttk.Label(window, image=photo)
        label.image = photo
        label.pack(padx=10, pady=10)
        ttk.Label(window, text=name, wraplength=950).pack(padx=10, pady=5)
