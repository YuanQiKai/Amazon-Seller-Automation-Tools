from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageOps, ImageTk
from .image_previews import preview_pool


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
    def __init__(self, parent, remove_callback, compact=False, lazy=False) -> None:
        super().__init__(parent)
        self.paths: list[str] = []
        self.number_labels: dict[str, str] = {}
        self.checked_paths: set[str] = set()
        self.thumbnails: list[ImageTk.PhotoImage] = []
        self.preview_photo = None
        self.remove_callback = remove_callback
        self.compact = compact
        self.lazy = lazy
        self._context_key = None
        self._ready_rows = set()
        self._pending_rows = set()
        self._row_photos = {}
        self._preview_key = None
        self._visible_after = None
        self._pool = preview_pool(self) if lazy else None
        listing = ttk.Frame(self)
        listing.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(listing, columns=('checked', 'number', 'name', 'size', 'path'), height=4, selectmode='extended')
        self.tree.heading('#0', text='缩略图')
        self.tree.column('#0', width=62, stretch=False)
        self.tree.heading('checked', text='☐ 全选', command=self.toggle_all)
        self.tree.column('checked', width=72, stretch=False, anchor='center')
        self.tree.heading('number', text='编号')
        self.tree.column('number', width=62, stretch=False, anchor='center')
        self.tree.heading('name', text='文件名（点击行预览，勾选框用于删除）')
        self.tree.column('name', width=220)
        self.tree.heading('size', text='尺寸')
        self.tree.column('size', width=110, stretch=False)
        self.tree.heading('path', text='文件路径')
        self.tree.column('path', width=280)
        if compact:
            self.tree.configure(displaycolumns=('checked', 'number', 'name'), height=3)
            self.tree.heading('checked', text='☐')
            self.tree.heading('name', text='文件名 · 点击预览')
            for key, width in (('#0', 52), ('checked', 42), ('number', 48), ('name', 120)):
                self.tree.column(key, width=width, minwidth=40, stretch=key == 'name')
        style = ttk.Style(self)
        style.configure('Assets.Treeview', rowheight=56)
        self.tree.configure(style='Assets.Treeview')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=lambda start, end: (scroll.set(start, end), self._schedule_visible()))
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.preview)
        self.tree.bind('<Button-1>', self.on_checkbox_click)
        self.tree.bind('<space>', self.on_checkbox_key)
        self.tree.bind('<Double-1>', self.on_double_click)
        self.tree.bind('<Configure>', lambda _e: self._schedule_visible())
        self.tree.bind('<Map>', lambda _e: self._schedule_visible())
        self.bind('<Destroy>', self._gallery_destroyed, add='+')
        right = ttk.Frame(self, padding=(10, 0, 0, 0))
        right.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_label = ttk.Label(right, text='选择图片预览', anchor='center', width=20 if compact else 28)
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

    def refresh(self, paths: list[str], context_key=None) -> None:
        selected_paths = {self.paths[int(i)] for i in self.tree.selection() if int(i) < len(self.paths)}
        if context_key != self._context_key:
            selected_paths.clear()
            self.checked_paths.clear()
        self._context_key = context_key
        if self.lazy:
            self._pool.cancel(self)
            self._pool.cancel(self.preview_label)
            self._ready_rows.clear()
            self._pending_rows.clear()
            self._row_photos.clear()
            self._preview_key = None
        self.paths = list(paths)
        self.checked_paths.intersection_update(paths)
        self.tree.delete(*self.tree.get_children())
        self.thumbnails.clear()
        self.preview_photo = None
        self.preview_label.configure(image='', text='选择图片预览')
        kept_selection = []
        for index, name in enumerate(paths):
            photo = ''
            dimensions = '等待预览' if self.lazy else '文件不可用'
            if not self.lazy:
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
                             values=('☑' if name in self.checked_paths else '☐', self.number_labels.get(name, f'{index+1:02d}'), Path(name).name, dimensions, name))
            if name in selected_paths:
                kept_selection.append(str(index))
        if paths:
            self.tree.selection_set(kept_selection or ['0'])
        self.preview()
        self._schedule_visible()

    def _gallery_destroyed(self, event):
        if event.widget is not self:
            return
        if self._pool:
            self._pool.cancel(self)
            self._pool.cancel(self.preview_label)
        if self._visible_after:
            self._root().after_cancel(self._visible_after)
            self._visible_after = None

    def _schedule_visible(self):
        if self.lazy and self._visible_after is None:
            self._visible_after = self._root().after_idle(self._load_visible)

    def _load_visible(self):
        self._visible_after = None
        if not self.tree.winfo_ismapped() or not self.paths:
            return
        # Query rows intersecting the actual viewport, not every uploaded image.
        visible = {self.tree.identify_row(y) for y in range(0, self.tree.winfo_height(), 20)}
        keep = visible | set(self.tree.selection()[:1])
        for iid in self._pending_rows - keep:
            self._pool.cancel_key(self, iid)
        self._pending_rows.intersection_update(keep)
        for iid in list(self._row_photos):
            if len(self._row_photos) <= 96:
                break
            if iid not in keep:
                self.tree.item(iid, image='')
                self._row_photos.pop(iid)
                self._ready_rows.discard(iid)
        for iid in sorted(visible - {''}, key=int, reverse=True):
            self._request_row(iid)

    def _request_row(self, iid):
        if iid in self._ready_rows or iid in self._pending_rows or not self.tree.exists(iid):
            return
        self._pending_rows.add(iid)
        path = self.paths[int(iid)]
        self._pool.request(self, iid, path, (230, 165), lambda data: self._row_loaded(iid, path, data))

    def _row_loaded(self, iid, path, data):
        self._pending_rows.discard(iid)
        if not self.tree.exists(iid) or self.paths[int(iid)] != path:
            return
        self._ready_rows.add(iid)
        if data.error:
            self.tree.set(iid, 'size', '文件不可用')
        else:
            self._row_photos[iid] = ImageTk.PhotoImage(data.thumbnail, master=self)
            self.tree.item(iid, image=self._row_photos[iid])
            self.tree.set(iid, 'size', data.dimensions)
        if self.tree.selection() and self.tree.selection()[0] == iid:
            self._paint_preview(data)

    def _paint_preview(self, data):
        if data.error:
            self.preview_photo = None
            self.preview_label.configure(image='', text='图片不存在或无法读取')
            return
        picture = data.image.copy()
        picture.thumbnail((160, 115) if self.compact else (230, 165), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(picture, master=self)
        self.preview_label.configure(image=self.preview_photo, text='')

    def preview(self, _event=None) -> None:
        selection = self.tree.selection()
        self.update_checks()
        if not selection:
            return
        if self.lazy:
            iid = selection[0]
            if self._preview_key == iid:
                return
            self._pool.cancel(self.preview_label)
            self._preview_key = iid
            self.preview_photo = None
            self.preview_label.configure(image='', text='正在加载预览…')
            if iid not in self._ready_rows:
                self._request_row(iid)
            else:
                self._pool.request(self.preview_label, 'preview', self.paths[int(iid)], (230, 165), self._paint_preview)
            return
        try:
            with Image.open(self.paths[int(selection[0])]) as source:
                picture = ImageOps.exif_transpose(source).convert('RGB')
                picture.thumbnail((160, 115) if self.compact else (230, 165), Image.Resampling.LANCZOS)
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
        self.tree.heading('checked', text=('☑' if all_checked else '☐') + ('' if self.compact else ' 全选'))
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
