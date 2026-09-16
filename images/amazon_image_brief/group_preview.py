"""Read-only, asynchronous storefront previews of existing final images.

No generation, product input mutation, automatic cropping, or invented mobile
artwork. Navigation siblings form one module; repeated instances stay separate.
"""
from dataclasses import dataclass
import re
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from .catalogs import CATALOG_BY_CODE
from .image_previews import preview_pool
from .languages import LANGUAGE_NAMES
from .navigation import NAV_CODE


@dataclass(frozen=True)
class PreviewFrame:
    instance_id: str
    order: int
    path: str
    status: str
    version: int


@dataclass(frozen=True)
class PreviewGroup:
    key: str
    code: str
    name: str
    frames: tuple[PreviewFrame, ...]


def preview_groups(instances, recipes, language, channel):
    """Preserve selected order (including unfinished positions), not catalogue order."""
    grouped, order = {}, []
    for index, instance in enumerate(instances, 1):
        spec = CATALOG_BY_CODE.get(instance.module_code)
        if not spec or spec.channel != channel:
            continue
        key = ('nav:' + instance.parent_id if instance.module_code == NAV_CODE and instance.parent_id
               else 'module:' + instance.instance_id)
        if key not in grouped:
            grouped[key] = (spec, [])
            order.append(key)
        record = recipes.get(instance.instance_id, {}).get('direct_results', {}).get(language, {})
        grouped[key][1].append(PreviewFrame(instance.instance_id, index, record.get('final_image') or '',
                                           record.get('status') or '该语言尚未生成', record.get('version', 0)))
    return [PreviewGroup(key, grouped[key][0].code, grouped[key][0].name, tuple(grouped[key][1])) for key in order]


def suggested_size(code, device):
    spec = CATALOG_BY_CODE[code]
    return spec.mobile_size if device == '移动端' else spec.pc_size


def preview_ratio(code, device):
    # Multi-image specifications such as 2×650×350 are slot dimensions, not a
    # 2-pixel canvas. Current output is one composite: retain a full-width stage.
    if code in ('APLUS_DUAL_IMAGE_TEXT', 'APLUS_FOUR_IMAGE_TEXT'):
        return 4 / 3 if device == '移动端' else 1464 / 600
    numbers = re.findall(r'\d+', suggested_size(code, device))
    if len(numbers) >= 2:
        width, height = map(int, numbers[-2:])
        if width > 0 and height > 0:
            return width / height
    return 4 / 3 if device == '移动端' else 1464 / 600


def module_hint(code):
    if code == NAV_CODE:
        return '导航轮播 · 逐帧切换（可左右拖动图片）'
    if 'VIDEO' in code:
        return '视频模块 · 当前仅预览已生成的静态封面，不播放视频'
    if code in ('APLUS_DUAL_IMAGE_TEXT', 'APLUS_FOUR_IMAGE_TEXT'):
        return '多图模块 · 当前结果为一张合成图，不拆分或重复填充图片槽位'
    if 'HOTSPOT' in code:
        return '热点模块 · 静态成图预览，不模拟尚未配置的热点'
    return '按已选模块顺序展示最终成图'


class PreviewCard(ttk.Frame):
    def __init__(self, parent, viewer, group, number, carousel=False):
        super().__init__(parent, padding=8)
        self.viewer, self.group, self.number = viewer, group, number
        self.carousel = carousel
        self.index = 0
        self.photo = None
        self._request_key = None
        self._drag_x = None
        self.heading = ttk.Label(self, style='Section.TLabelframe.Label', wraplength=800)
        self.heading.pack(fill='x', pady=(0, 4))
        self.description = ttk.Label(self, style='Subtitle.TLabel', wraplength=800)
        self.description.pack(fill='x', pady=(0, 6))
        self.image_canvas = tk.Canvas(self, width=300, height=220, background='white',
                                      highlightthickness=1, highlightbackground='#dce2eb')
        self.image_canvas.pack(anchor='center')
        self.image_canvas.bind('<ButtonPress-1>', lambda e: setattr(self, '_drag_x', e.x))
        self.image_canvas.bind('<ButtonRelease-1>', self._swipe)
        self.controls = ttk.Frame(self)
        ttk.Button(self.controls, text='‹ 上一张', command=lambda: self.step(-1), width=12).pack(side='left', padx=3)
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(self.controls, state='readonly', textvariable=self.choice, width=30)
        self.combo.pack(side='left', padx=5)
        self.combo.bind('<<ComboboxSelected>>', lambda _e: self.select(self.combo.current()))
        ttk.Button(self.controls, text='下一张 ›', command=lambda: self.step(1), width=12).pack(side='left', padx=3)
        self.caption = ttk.Label(self, style='Subtitle.TLabel', wraplength=800)
        self.caption.pack(fill='x', pady=(6, 0))
        self.bind('<Destroy>', self._destroyed, add='+')
        self.update_group(group)

    @property
    def current(self):
        return self.group.frames[self.index]

    def _destroyed(self, event):
        if event.widget is self:
            self.viewer.pool.cancel(self)
            self.photo = None

    def update_group(self, group):
        previous = self.current.instance_id if self.group.frames else ''
        self.group = group
        self.index = next((i for i, frame in enumerate(group.frames) if frame.instance_id == previous), 0)
        labels = [f'{i:02d} · 模块序号 {frame.order:02d}' for i, frame in enumerate(group.frames, 1)]
        self.combo.configure(values=labels)
        if len(group.frames) > 1:
            self.controls.pack(anchor='center', before=self.caption, pady=(8, 0))
        else:
            self.controls.pack_forget()
        self.paint_metadata()

    def paint_metadata(self):
        frame = self.current
        if self.carousel:
            instance = next((item for item in self.viewer.app.module_instances if item.instance_id == frame.instance_id), None)
            code = instance.module_code if instance else self.group.code
            name = CATALOG_BY_CODE[code].name
            title = f'主图 {self.index+1:02d} / {len(self.group.frames):02d}  ·  {name}'
        else:
            code = self.group.code
            title = f'A+ 模块 {self.number:02d}  ·  {self.group.name}'
        self.display_code = code
        self.heading.configure(text=title)
        self.description.configure(text=f'{module_hint(code)}\n{self.viewer.device.get()}项目建议尺寸：{suggested_size(code, self.viewer.device.get())}')
        self.combo.current(self.index)
        self.caption.configure(text=self._caption_text())
        self.viewer.schedule_layout()

    def _caption_text(self):
        frame = self.current
        return (f'模块序号 {frame.order:02d} · {self.viewer.language.get()} · {frame.status}'
                + (f' · V{frame.version}（最新保留成图）' if frame.path else ''))

    def select(self, index):
        if not self.group.frames:
            return
        self.index = index % len(self.group.frames)
        self.release()
        self.paint_metadata()

    def step(self, direction):
        self.select(self.index + direction)

    def _swipe(self, event):
        if self._drag_x is not None and abs(event.x-self._drag_x) > 45:
            self.step(-1 if event.x > self._drag_x else 1)
        self._drag_x = None

    def resize(self, available_width, main_height):
        mobile = self.viewer.device.get() == '移动端'
        width = min(390 if mobile else 1000, max(240, available_width-32))
        height = int(width / preview_ratio(self.display_code, self.viewer.device.get()))
        if self.carousel:
            height = min(height, main_height)
            width = height  # All main images use square presentation.
        self.image_canvas.configure(width=width, height=height)
        self.heading.configure(wraplength=available_width-24)
        self.description.configure(wraplength=available_width-24)
        self.caption.configure(wraplength=available_width-24)
        self.size = (width, height)

    def release(self):
        self.viewer.pool.cancel(self)
        self._request_key = None
        self.photo = None
        self.image_canvas.delete('all')

    def load(self):
        frame = self.current
        key = (frame.instance_id, frame.path, frame.version, self.size)
        if self._request_key == key:
            return
        self.release()
        self._request_key = key
        width, height = self.size
        self.image_canvas.create_text(width/2, height/2, text='正在加载成图…' if frame.path else '该语言尚未生成\n完成后将在此自动显示',
                                      fill='#64748b', width=max(180, width-24), justify='center')
        if frame.path:
            self.viewer.pool.request(self, 'final', frame.path, self.size, self._loaded)

    def _loaded(self, data):
        self.image_canvas.delete('all')
        width, height = self.size
        if data.error:
            self.image_canvas.create_text(width/2, height/2, text=data.error+'\n请检查结果文件是否已移动或删除',
                                          fill='#b45309', width=width-24, justify='center')
        else:
            self.photo = ImageTk.PhotoImage(data.image, master=self)
            self.image_canvas.create_image(width/2, height/2, image=self.photo)
            self.caption.configure(text=self._caption_text() + f' · 原图 {data.dimensions}')


class GroupPreview(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.pool = preview_pool(app.root)
        self.title('整组预览 · 主图轮播 / A+ 页面')
        self.geometry(f'1120x{min(820, max(600, self.winfo_screenheight()-100))}')
        self.minsize(800, 600)
        self.cards = []
        self._structure = None
        self._layout_after = self._play_after = None
        self._closed = False
        self.device = tk.StringVar(value='PC端')
        self.channel = tk.StringVar(value='主图')
        self.language = tk.StringVar(value='德语')
        self.autoplay = tk.BooleanVar(value=False)
        self._language_codes = {}
        toolbar = ttk.Frame(self, padding=(14, 10))
        toolbar.pack(fill='x')
        for text, variable in (('主图轮播', '主图'), ('A+ 上下预览', '高级A+')):
            ttk.Radiobutton(toolbar, text=text, value=variable, variable=self.channel, command=self._change_view).pack(side='left', padx=4)
        ttk.Separator(toolbar, orient='vertical').pack(side='left', fill='y', padx=14)
        for device in ('PC端', '移动端'):
            ttk.Radiobutton(toolbar, text=device, value=device, variable=self.device, command=self._change_device).pack(side='left', padx=4)
        ttk.Button(toolbar, text='刷新成图', command=self.force_refresh).pack(side='right')
        options = ttk.Frame(self, padding=(18, 0, 18, 6))
        options.pack(fill='x')
        self.play_button = ttk.Checkbutton(options, text='主图自动轮播（3秒）', variable=self.autoplay, command=self._toggle_play)
        self.play_button.pack(side='left')
        ttk.Label(options, text='预览语言').pack(side='left', padx=(12, 4))
        self.language_combo = ttk.Combobox(options, state='readonly', width=10, textvariable=self.language)
        self.language_combo.pack(side='left', padx=4)
        self.language_combo.bind('<<ComboboxSelected>>', lambda _e: self.refresh())
        self.count = tk.StringVar()
        ttk.Label(options, textvariable=self.count, style='Subtitle.TLabel').pack(side='right')
        ttk.Label(self, text='只预览已生成图片，不调用 AI。移动端为同一成图的窄屏适配，文字不会重排；留白不裁切。\n'
                  'A+ 按所选模块展示；轮播按帧切换。PC / 移动建议尺寸沿用项目规格，最终以上架后台预览为准。',
                  style='Subtitle.TLabel', padding=(18, 4)).pack(fill='x')
        host = ttk.Frame(self)
        host.pack(fill='both', expand=True)
        self.scroll_canvas = tk.Canvas(host, highlightthickness=0, background='#eef2f7', yscrollincrement=28)
        bar = ttk.Scrollbar(host, orient='vertical', command=self.scroll_canvas.yview)
        bar.pack(side='right', fill='y')
        self.scroll_canvas.configure(yscrollcommand=lambda a, b: (bar.set(a, b), self.schedule_layout()))
        self.scroll_canvas.pack(fill='both', expand=True)
        self.body = ttk.Frame(self.scroll_canvas, padding=10)
        self._window = self.scroll_canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _e: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox('all')))
        self.scroll_canvas.bind('<Configure>', self._resized)
        # Existing wheel router can discover this canvas through the parent.
        host.scroll_canvas = self.scroll_canvas
        self.bind('<Left>', lambda _e: self._key_step(-1))
        self.bind('<Right>', lambda _e: self._key_step(1))
        self.bind('<Destroy>', self._destroyed, add='+')
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.refresh()

    def _key_step(self, direction):
        if self.channel.get() == '主图' and self.cards and not isinstance(self.focus_get(), ttk.Combobox):
            self.cards[0].step(direction)

    def _resized(self, event):
        self.scroll_canvas.itemconfigure(self._window, width=event.width)
        self.schedule_layout()

    def schedule_layout(self):
        if not self._closed and self._layout_after is None:
            self._layout_after = self.app.root.after(40, self._layout)

    def _layout(self):
        self._layout_after = None
        if self._closed:
            return
        width = max(280, self.scroll_canvas.winfo_width()-20)
        main_height = max(200, self.scroll_canvas.winfo_height()-170)
        top = self.scroll_canvas.winfo_rooty()
        bottom = top + self.scroll_canvas.winfo_height()
        for card in self.cards:
            card.resize(width, main_height)
            visible = card.winfo_rooty() < bottom+100 and card.winfo_rooty()+card.winfo_height() > top-100
            if visible:
                card.load()
            elif card._request_key is not None:
                card.release()  # Keep only visible Tk images; PIL cache is bounded.

    def _change_device(self):
        for card in self.cards:
            card.paint_metadata()
        self.schedule_layout()

    def _change_view(self):
        self.autoplay.set(False)
        self._toggle_play()
        self.play_button.configure(state='normal' if self.channel.get() == '主图' else 'disabled')
        self.refresh()
        self.scroll_canvas.yview_moveto(0)

    def force_refresh(self):
        for card in self.cards:
            card.release()
        self.refresh()

    def refresh(self):
        if self._closed:
            return
        languages = [code for code, var in self.app.language_vars.items() if var.get()]
        for recipe in self.app.module_recipes.values():
            languages.extend(recipe.get('direct_results', {}))
        languages = list(dict.fromkeys(languages or ['de']))
        self._language_codes = {LANGUAGE_NAMES.get(code, code): code for code in languages}
        self.language_combo.configure(values=tuple(self._language_codes))
        if self.language.get() not in self._language_codes:
            self.language.set('德语' if 'de' in languages else next(iter(self._language_codes)))
        lang = self._language_codes[self.language.get()]
        groups = preview_groups(self.app.module_instances, self.app.module_recipes, lang, self.channel.get())
        frame_count = sum(len(group.frames) for group in groups)
        done = sum(bool(frame.path) for group in groups for frame in group.frames)
        self.count.set(f'{len(groups)} 个模块 · 已有成图 {done}/{frame_count} 张')
        if self.channel.get() == '主图' and groups:
            groups = [PreviewGroup('main-carousel', groups[0].code, '主图', tuple(frame for group in groups for frame in group.frames))]
        structure = (self.channel.get(), tuple((group.key, group.code, tuple(frame.instance_id for frame in group.frames)) for group in groups))
        if structure != self._structure:
            old_selected = {card.group.key: card.current.instance_id for card in self.cards}
            for child in self.body.winfo_children():
                child.destroy()
            self.cards = []
            for number, group in enumerate(groups, 1):
                card = PreviewCard(self.body, self, group, number, carousel=self.channel.get() == '主图')
                card.pack(fill='x', pady=(0, 8))
                card.select(next((i for i, frame in enumerate(group.frames) if frame.instance_id == old_selected.get(group.key)), 0))
                self.cards.append(card)
            if not groups:
                ttk.Label(self.body, text='尚未选择该类型模块，请先在「选择模块」添加。', padding=36).pack()
            self._structure = structure
        else:
            for card, group in zip(self.cards, groups):
                card.update_group(group)
        self.schedule_layout()

    def _toggle_play(self):
        if self._play_after:
            self.app.root.after_cancel(self._play_after)
            self._play_after = None
        if self.autoplay.get() and self.channel.get() == '主图':
            self._play_after = self.app.root.after(3000, self._play)

    def _play(self):
        self._play_after = None
        if self._closed or not self.autoplay.get() or self.channel.get() != '主图':
            return
        if self.cards:
            self.cards[0].step(1)
        self._toggle_play()

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._closed = True
        for timer in (self._layout_after, self._play_after):
            if timer:
                self.app.root.after_cancel(timer)
        self._layout_after = self._play_after = None
