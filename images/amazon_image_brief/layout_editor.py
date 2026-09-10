"""Normalized element editor. Work on a copy until the user explicitly applies."""
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .typography import validate_element


class LayoutEditor(tk.Toplevel):
    def __init__(self, parent, plan, on_apply):
        super().__init__(parent)
        self.title('文本贴图精排 — 标题 / 副标题 / 功能标注')
        self.geometry('1040x740')
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.plan = deepcopy(plan)
        self.on_apply = on_apply
        self.current = None
        self.device = 'pc'
        self.drag_origin = None
        self.device_var = tk.StringVar(value='pc')
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill=tk.X)
        combo = ttk.Combobox(bar, values=('pc', 'mobile'), textvariable=self.device_var, state='readonly', width=12)
        combo.pack(side=tk.LEFT)
        combo.bind('<<ComboboxSelected>>', self.switch_device)
        ttk.Label(bar, text='每个文本框可拖动；坐标为画布百分比。部位锚点须按实物校正，并非自动识别。').pack(side=tk.LEFT, padx=10)
        ttk.Button(bar, text='应用并保存版式', command=self.apply).pack(side=tk.RIGHT)
        custom = ttk.Frame(self, padding=(10, 0))
        custom.pack(fill=tk.X)
        ttk.Button(custom, text='＋ 文案区块', command=self.add_text).pack(side=tk.LEFT)
        ttk.Button(custom, text='＋ 图片 / 场景', command=self.add_scene).pack(side=tk.LEFT, padx=6)
        ttk.Button(custom, text='重命名 / 场景说明', command=self.rename_region).pack(side=tk.LEFT)
        ttk.Button(custom, text='删除选中区块', command=self.remove_region).pack(side=tk.LEFT, padx=6)
        ttk.Label(custom, text='新增/删除同步PC与移动端；位置可分别调整。文案区块改变后请重生成文案。').pack(side=tk.LEFT)
        row = ttk.Frame(self, padding=10)
        row.pack(fill=tk.X)
        self.slot_var = tk.StringVar()
        self.slot_combo = ttk.Combobox(row, textvariable=self.slot_var, state='readonly', width=25)
        self.slot_combo.pack(side=tk.LEFT)
        self.slot_combo.bind('<<ComboboxSelected>>', self.select_slot)
        self.vars = {}
        for key, label in [('x', 'X%'), ('y', 'Y%'), ('w', '宽%'), ('h', '高%'), ('font', '字号%')]:
            ttk.Label(row, text=label).pack(side=tk.LEFT, padx=(8, 1))
            self.vars[key] = tk.StringVar()
            entry = ttk.Entry(row, textvariable=self.vars[key], width=6)
            entry.pack(side=tk.LEFT)
            entry.bind('<FocusOut>', self.update_element)
        self.align = tk.StringVar(value='left')
        align = ttk.Combobox(row, textvariable=self.align, values=('left', 'center', 'right'), state='readonly', width=8)
        align.pack(side=tk.LEFT, padx=6)
        align.bind('<<ComboboxSelected>>', self.update_element)
        row2 = ttk.Frame(self, padding=(10, 0, 10, 8))
        row2.pack(fill=tk.X)
        self.anchor_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text='功能说明引线 → 产品部位', variable=self.anchor_enabled, command=self.update_element).pack(side=tk.LEFT)
        for key, label in [('ax', '部位X%'), ('ay', '部位Y%')]:
            ttk.Label(row2, text=label).pack(side=tk.LEFT, padx=(8, 1))
            self.vars[key] = tk.StringVar(value='50')
            entry = ttk.Entry(row2, textvariable=self.vars[key], width=7)
            entry.pack(side=tk.LEFT)
            entry.bind('<FocusOut>', self.update_element)
        self.error_var = tk.StringVar()
        ttk.Label(row2, textvariable=self.error_var, foreground='#b42318').pack(side=tk.LEFT, padx=10)
        self.canvas = tk.Canvas(self, bg='#E5E7EB', height=500, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.canvas.bind('<Configure>', lambda _e: self.draw())
        self.canvas.bind('<Button-1>', self.drag_start)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', lambda _e: self.load_fields())
        self.reload()

    def elements(self):
        layout = self.plan.get(self.device, {})
        return layout.get('elements', []) + layout.get('scenes', [])

    def mark_custom(self):
        self.plan.update(layout='custom', manual=True)
        for device in ('pc', 'mobile'):
            self.plan[device]['label'] = '自定义 · 文案区块与图片场景' + (' · 移动端重排' if device == 'mobile' else '')

    def add_text(self, label=None):
        if not self.update_element():
            return
        label = label or simpledialog.askstring('新增文案区块', '该行的作用，例如：轮组静音说明 / 副标题', parent=self)
        if not label or not label.strip():
            return
        slot = len(self.plan['copy_slots'])
        self.plan['copy_slots'].append(label.strip())
        for device in ('pc', 'mobile'):
            self.plan[device]['elements'].append(dict(slot=slot, label=label.strip(), box=[.06, .80, .40, .13], align='left', font_ratio=.03, bold=False, anchor=None, background='auto'))
        self.mark_custom()
        self.reload()

    def add_scene(self, label=None):
        if not self.update_element():
            return
        label = label or simpledialog.askstring('新增图片场景', '场景内容 / 镜头要求（不得虚构产品功能）', parent=self)
        if not label or not label.strip():
            return
        for device in ('pc', 'mobile'):
            self.plan[device].setdefault('scenes', []).append(dict(label=label.strip(), box=[.55, .32, .38, .35], align='left', kind='scene'))
        self.mark_custom()
        self.reload()

    def rename_region(self):
        if self.current is None or not self.update_element():
            return
        item = self.elements()[self.current]
        value = simpledialog.askstring('区块说明', '文案用途 / 场景内容', initialvalue=item['label'], parent=self)
        if not value or not value.strip():
            return
        if item.get('kind') == 'scene':
            index = self.current - len(self.plan[self.device]['elements'])
            for device in ('pc', 'mobile'):
                self.plan[device]['scenes'][index]['label'] = value.strip()
        else:
            slot = item['slot']
            self.plan['copy_slots'][slot] = value.strip()
            for device in ('pc', 'mobile'):
                self.plan[device]['elements'][slot]['label'] = value.strip()
        self.mark_custom()
        self.reload()

    def remove_region(self):
        if self.current is None or not self.update_element():
            return
        item = self.elements()[self.current]
        if item.get('kind') == 'scene':
            index = self.current - len(self.plan[self.device]['elements'])
            for device in ('pc', 'mobile'):
                self.plan[device]['scenes'].pop(index)
        else:
            if len(self.plan['copy_slots']) <= 2:
                self.error_var.set('至少保留标题与支持说明两个文案区块。')
                return
            slot = item['slot']
            self.plan['copy_slots'].pop(slot)
            for device in ('pc', 'mobile'):
                self.plan[device]['elements'].pop(slot)
                for index, element in enumerate(self.plan[device]['elements']):
                    element['slot'] = index
        self.mark_custom()
        self.reload()

    def reload(self):
        values = [f"{'场景' if item.get('kind') == 'scene' else item['slot']+1}. {item['label']}" for item in self.elements()]
        self.slot_combo.configure(values=values)
        self.current = 0 if values else None
        self.slot_var.set(values[0] if values else '')
        self.load_fields()
        self.draw()

    def switch_device(self, _event=None):
        if not self.update_element():
            self.device_var.set(self.device)
            return
        self.device = self.device_var.get()
        self.reload()

    def select_slot(self, _event=None):
        index = self.slot_combo.current()
        if not self.update_element():
            self.slot_combo.current(self.current or 0)
            return
        self.current = index
        self.load_fields()
        self.draw()

    def load_fields(self):
        if self.current is None:
            return
        item = self.elements()[self.current]
        for key, value in zip(('x', 'y', 'w', 'h'), item['box']):
            self.vars[key].set(f'{value*100:.2f}')
        self.vars['font'].set(f"{item.get('font_ratio', .033)*100:.2f}")
        self.align.set(item['align'])
        self.anchor_enabled.set(bool(item.get('anchor')))
        for key, value in zip(('ax', 'ay'), item.get('anchor') or [.5, .5]):
            self.vars[key].set(f'{value*100:.2f}')
        self.slot_combo.current(self.current)

    def update_element(self, _event=None):
        if self.current is None:
            return True
        item = deepcopy(self.elements()[self.current])
        try:
            item['box'] = [float(self.vars[key].get())/100 for key in ('x', 'y', 'w', 'h')]
            item['font_ratio'] = float(self.vars['font'].get())/100
            item['align'] = self.align.get()
            item['anchor'] = [float(self.vars[key].get())/100 for key in ('ax', 'ay')] if self.anchor_enabled.get() else None
            validate_element(item)
        except (ValueError, TypeError) as exc:
            self.error_var.set(str(exc))
            return False
        current = self.elements()[self.current]
        current.clear()
        current.update(item)
        self.error_var.set('')
        self.draw()
        return True

    def draw(self):
        self.canvas.delete('all')
        cw, ch = max(500, self.canvas.winfo_width()), max(300, self.canvas.winfo_height())
        ratio = 1464/600 if self.device == 'pc' else 1200/900
        width = min(cw-40, (ch-40)*ratio)
        height = width/ratio
        self.bounds = ((cw-width)/2, (ch-height)/2, width, height)
        ox, oy, width, height = self.bounds
        self.canvas.create_rectangle(ox, oy, ox+width, oy+height, fill=self.plan.get('palette', {}).get('paper', '#F5F3ED'), outline='')
        x, y, w, h = self.plan[self.device]['product_box']
        self.canvas.create_rectangle(ox+x*width, oy+y*height, ox+(x+w)*width, oy+(y+h)*height, fill='#CBDDE5', outline='')
        self.canvas.create_text(ox+(x+w/2)*width, oy+(y+h/2)*height, text='产品 / 场景区域', fill='#526570')
        for index, item in enumerate(self.elements()):
            x, y, w, h = item['box']
            tag = f'element-{index}'
            if item.get('anchor'):
                ax, ay = item['anchor']
                self.canvas.create_line(ox+(x+w/2)*width, oy+(y+h/2)*height, ox+ax*width, oy+ay*height, fill='#277A79', arrow='last')
            self.canvas.create_rectangle(ox+x*width, oy+y*height, ox+(x+w)*width, oy+(y+h)*height,
                                         fill='#DCEFEA' if item.get('kind') == 'scene' else '#FFF1CC', outline='#D88A2A' if index == self.current else '#CEC6B6', width=2, tags=(tag,))
            self.canvas.create_text(ox+(x+w/2)*width, oy+(y+h/2)*height, text=f"{'场景' if item.get('kind') == 'scene' else item['slot']+1} {item['label']}",
                                    width=max(30, w*width-10), tags=(tag,))

    def drag_start(self, event):
        if not self.update_element():
            return
        for obj in reversed(self.canvas.find_overlapping(event.x, event.y, event.x, event.y)):
            for tag in self.canvas.gettags(obj):
                if tag.startswith('element-'):
                    self.current = int(tag.split('-')[1])
                    self.drag_origin = (event.x, event.y, list(self.elements()[self.current]['box']))
                    self.load_fields()
                    self.draw()
                    return
        self.drag_origin = None

    def drag(self, event):
        if self.drag_origin is None or self.current is None:
            return
        ex, ey, box = self.drag_origin
        _, _, width, height = self.bounds
        x, y, w, h = box
        self.elements()[self.current]['box'] = [min(1-w, max(0, x+(event.x-ex)/width)), min(1-h, max(0, y+(event.y-ey)/height)), w, h]
        self.load_fields()
        self.draw()

    def apply(self):
        if not self.update_element():
            messagebox.showerror('版式未保存', self.error_var.get(), parent=self)
            return
        self.plan['manual'] = True
        self.on_apply(deepcopy(self.plan))
        self.destroy()
