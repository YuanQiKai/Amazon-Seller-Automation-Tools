from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
from copy import deepcopy

from .catalogs import CATALOG_BY_CODE
from .creative_planner import LAYOUTS, plan_description, apply_layout
from .typography import PRESETS
from .layout_editor import LayoutEditor
from .navigation import instance_name


def readonly_text(parent, height=10):
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.BOTH, expand=True)
    widget = tk.Text(frame, wrap='word', height=height, font=('Consolas', 10), state='disabled')
    scroll = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
    widget.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    widget.pack(fill=tk.BOTH, expand=True)
    return widget


def show_text(widget, value):
    widget.configure(state='normal')
    widget.delete('1.0', 'end')
    widget.insert('1.0', value)
    widget.configure(state='disabled')


class CreativeWorkspace(ttk.Frame):
    def __init__(self, parent, on_plan_change):
        super().__init__(parent, padding=8)
        canvas = tk.Canvas(self, highlightthickness=0, background='#f6f9ff')
        self.scroll_canvas = canvas
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content = ttk.Frame(canvas, padding=6)
        window = canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        self.on_selection = None
        self.on_plan_change = on_plan_change
        self.plans = {}
        self.results = {}
        self.current_id = ''
        self.loading = False
        self.editable = True
        self.progress_var = tk.StringVar(value='先点击“预览卖点与版式”，检查每图卖点；生成时显示逐模块进度。')
        ttk.Label(self.content, textvariable=self.progress_var, wraplength=1150).pack(anchor='w', pady=5)
        self.progress = ttk.Progressbar(self.content, maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 8))
        panes = ttk.Panedwindow(self.content, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)
        left, right = ttk.Frame(panes), ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        self.prompt_host = ttk.Frame(left)
        self.prompt_host.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        self.tree = ttk.Treeview(left, height=8, columns=('module', 'focus', 'status'), show='headings', selectmode='browse')
        for col, label, width in [('module', '模块', 170), ('focus', '本模块宣传卖点', 310), ('status', '当前进度', 165)]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width)
        self.tree.tag_configure('failed', foreground='#b42318')
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        ttk.Label(right, text='主卖点（可调整，必须有产品事实依据）').pack(anchor='w')
        self.focus_var = tk.StringVar()
        self.focus_entry = ttk.Entry(right, textvariable=self.focus_var)
        self.focus_entry.pack(fill=tk.X, pady=4)
        self.angle_var = tk.StringVar()
        self.evidence_var = tk.StringVar()
        self.extra_entries = []
        for variable, title in ((self.angle_var, '本图角度（可编辑）'), (self.evidence_var, '事实依据（多条用分号分隔；可编辑）')):
            ttk.Label(right, text=title).pack(anchor='w')
            entry = ttk.Entry(right, textvariable=variable)
            entry.pack(fill=tk.X, pady=4)
            entry.bind('<FocusOut>', self.commit)
            entry.bind('<<InputUndo>>', self.commit)
            self.extra_entries.append(entry)
        ttk.Label(right, text='画面需展示的动作 / 细节 / 场景').pack(anchor='w')
        self.visual_var = tk.StringVar()
        self.visual_entry = ttk.Entry(right, textvariable=self.visual_var)
        self.visual_entry.pack(fill=tk.X, pady=4)
        self.layout_var = tk.StringVar()
        self.layout_combo = ttk.Combobox(right, textvariable=self.layout_var, values=[LAYOUTS[key]['label'] for key in PRESETS], state='readonly')
        self.layout_combo.pack(fill=tk.X, pady=4)
        self.element_button = ttk.Button(right, text='精调文本位置 / 字号 / 部位引线（可拖动）', command=self.edit_elements)
        self.element_button.pack(fill=tk.X, pady=3)
        self.focus_entry.bind('<FocusOut>', self.commit)
        self.visual_entry.bind('<FocusOut>', self.commit)
        self.focus_entry.bind('<<InputUndo>>', self.commit)
        self.visual_entry.bind('<<InputUndo>>', self.commit)
        self.layout_combo.bind('<<ComboboxSelected>>', self.commit)
        self.canvas = tk.Canvas(right, width=420, height=235, bg='#f6f7fb', highlightthickness=0)
        self.canvas.pack(fill=tk.X, pady=5)
        self.detail = readonly_text(right, 8)

    def set_plans(self, plans, instances):
        self.current_id = ''
        self.plans = deepcopy(plans)
        self.results = {}
        self.tree.delete(*self.tree.get_children())
        for instance in instances:
            if instance.instance_id not in plans:
                continue
            spec = CATALOG_BY_CODE[instance.module_code]
            self.tree.insert('', 'end', iid=instance.instance_id,
                             values=(instance_name(instance, spec.name), plans[instance.instance_id]['focus'], '首图无文案' if spec.code == 'MAIN_WHITE' else '等待生成'))
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.select()
        self.progress['value'] = 0

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] == self.current_id:
            return
        self.commit()
        self.current_id = selected[0]
        plan = self.plans[self.current_id]
        self.loading = True
        self.focus_var.set(plan['focus'])
        self.visual_var.set(plan.get('visual', ''))
        self.angle_var.set(plan.get('angle', ''))
        self.evidence_var.set('；'.join(plan.get('evidence', [])))
        self.layout_var.set(plan['pc']['label'])
        locked = plan['layout'] == 'none' or not self.editable
        self.focus_entry.configure(state='disabled' if locked else 'normal')
        self.visual_entry.configure(state='disabled' if locked else 'normal')
        self.layout_combo.configure(state='disabled' if locked else 'readonly')
        for widget in self.extra_entries:
            widget.configure(state='disabled' if locked else 'normal')
        for widget in (self.focus_entry, self.visual_entry, *self.extra_entries):
            widget.event_generate('<<ResetUndo>>')
        self.loading = False
        self.draw()
        if self.on_selection:
            self.on_selection(self.current_id)

    def commit(self, _event=None):
        if self.loading or not self.editable or self.current_id not in self.plans:
            return
        plan = self.plans[self.current_id]
        if plan['layout'] == 'none':
            return
        layout = next((key for key, value in LAYOUTS.items() if value['label'] == self.layout_var.get()), plan['layout'])
        changes = {'focus': self.focus_var.get().strip(), 'visual': self.visual_var.get().strip(), 'layout': layout,
                   'angle': self.angle_var.get().strip(), 'evidence': [s.strip() for s in self.evidence_var.get().replace('\n', '；').replace(';', '；').split('；') if s.strip()]}
        if any(plan.get(key) != value for key, value in changes.items()):
            changed_layout = plan.get('layout') != layout
            plan['manual_fields'] = list(dict.fromkeys([*plan.get('manual_fields', []), *(key for key, value in changes.items() if plan.get(key) != value)]))
            plan.update(changes, manual=True)
            if changed_layout:
                apply_layout(plan, layout)
            self.on_plan_change(self.current_id, deepcopy(plan))
            self.tree.set(self.current_id, 'focus', plan['focus'])
            self.draw()

    def edit_elements(self):
        if not self.editable:
            return
        self.commit()
        plan = self.plans.get(self.current_id)
        if not plan or plan.get('layout') == 'none':
            return
        if not plan.get('pc', {}).get('elements'):
            apply_layout(plan, 'hero')
            plan['manual'] = True
        iid = self.current_id
        def save(value):
            value['manual_fields'] = list(dict.fromkeys([*value.get('manual_fields', []), 'layout']))
            self.plans[iid] = value
            self.on_plan_change(iid, deepcopy(value))
            if self.current_id == iid:
                self.layout_var.set(value['pc']['label'])
                self.draw()
        LayoutEditor(self, plan, save)

    def set_editable(self, editable):
        self.editable = editable
        locked = not editable or self.plans.get(self.current_id, {}).get('layout') == 'none'
        self.focus_entry.configure(state='disabled' if locked else 'normal')
        self.visual_entry.configure(state='disabled' if locked else 'normal')
        self.layout_combo.configure(state='disabled' if locked else 'readonly')
        self.element_button.configure(state='disabled' if locked else 'normal')
        for widget in self.extra_entries:
            widget.configure(state='disabled' if locked else 'normal')

    def draw(self):
        plan = self.plans.get(self.current_id, {})
        self.canvas.delete('all')
        for origin, size, label, layout in [(10, (250, 148), 'PC示意', plan.get('pc', {})), (280, (110, 148), '移动端示意', plan.get('mobile', {}))]:
            width, height = size
            self.canvas.create_text(origin, 8, text=label, anchor='nw')
            self.canvas.create_rectangle(origin, 32, origin+width, 32+height, fill='white', outline='#aab4c2')
            for key, color, title in [('product_box', '#dbeafe', '产品/场景'), ('text_box', '#fff0c2', '文案区')]:
                box = layout.get(key, [])
                if len(box) == 4:
                    x, y, w, h = box
                    self.canvas.create_rectangle(origin+x*width, 32+y*height, origin+(x+w)*width, 32+(y+h)*height, fill=color, outline='')
                    self.canvas.create_text(origin+(x+w/2)*width, 32+(y+h/2)*height, text=title, width=max(40, w*width-5))
            for scene in layout.get('scenes', []):
                x, y, w, h = scene['box']
                self.canvas.create_rectangle(origin+x*width, 32+y*height, origin+(x+w)*width, 32+(y+h)*height, fill='#dcefea', outline='#70a897')
                self.canvas.create_text(origin+(x+w/2)*width, 32+(y+h/2)*height, text=scene.get('label', '场景'), width=max(30, w*width))
            for element in layout.get('elements', []):
                x, y, w, h = element['box']
                if element.get('anchor'):
                    ax, ay = element['anchor']
                    self.canvas.create_line(origin+(x+w/2)*width, 32+(y+h/2)*height, origin+ax*width, 32+ay*height, fill='#277A79')
                self.canvas.create_rectangle(origin+x*width, 32+y*height, origin+(x+w)*width, 32+(y+h)*height, fill='#fff0c2', outline='')
                self.canvas.create_text(origin+(x+w/2)*width, 32+(y+h/2)*height, text=str(element['slot']+1), font=('Arial', 8))
        self.canvas.create_text(10, 198, text='编号对应文案行；引线指向产品部位。点击精调可独立修改。', anchor='nw')
        event = self.results.get(self.current_id, {})
        show_text(self.detail, plan_description(plan) + (f"\n【生成状态】{event.get('status', '')}\n【异常】{event.get('error') or '无'}" if event else ''))

    def update_progress(self, event):
        iid = event['instance_id']
        self.results[iid] = {key: value for key, value in event.items() if key != 'brief'}
        incoming = event.get('brief')
        if incoming is not None and incoming.creative_plan:
            self.plans[iid] = deepcopy(incoming.creative_plan)
            if self.tree.exists(iid):
                self.tree.set(iid, 'focus', incoming.creative_plan.get('focus', ''))
            if iid == self.current_id:
                self.loading = True
                self.focus_var.set(incoming.creative_plan.get('focus', ''))
                self.angle_var.set(incoming.creative_plan.get('angle', ''))
                self.evidence_var.set('；'.join(incoming.creative_plan.get('evidence', [])))
                self.visual_var.set(incoming.creative_plan.get('visual', ''))
                self.layout_var.set(incoming.creative_plan.get('pc', {}).get('label', ''))
                self.loading = False
        if iid == self.current_id:
            self.draw()
        if self.tree.exists(iid):
            failed = bool(event.get('error'))
            self.tree.set(iid, 'status', event['status'])
            self.tree.item(iid, tags=('failed',) if failed else ())
            self.tree.see(iid)
        done, total = event.get('done', 0), max(1, event.get('total', 1))
        self.progress['value'] = done / total * 100
        self.progress_var.set(f"{done}/{total} 已处理｜{event['module_name']}｜{event['status']}｜{event.get('focus', '')}" + (f"\n{event['error']}" if event.get('error') else ''))


class APIInspector(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=8)
        self.records = {}
        self.counter = 0
        ttk.Label(self, text='实时显示本会话请求和响应（认证信息已脱敏）。点击一行查看完整 JSON；缓存命中会单独标明。').pack(anchor='w', pady=4)
        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, pady=(0, 5))
        self.follow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text='跟随最新接口记录（取消后可查看历史请求）', variable=self.follow_var).pack(side=tk.LEFT)
        ttk.Button(controls, text='清空本页记录', command=self.clear).pack(side=tk.RIGHT)
        panes = ttk.Panedwindow(self, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)
        top, bottom = ttk.Frame(panes), ttk.Notebook(panes)
        panes.add(top, weight=1)
        panes.add(bottom, weight=3)
        self.tree = ttk.Treeview(top, columns=('module', 'request', 'status'), show='headings', height=5)
        for key, label, width in [('module', '模块 / 请求ID', 260), ('request', '接口', 530), ('status', '响应状态', 180)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width)
        scroll = ttk.Scrollbar(top, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        request_frame, response_frame = ttk.Frame(bottom), ttk.Frame(bottom)
        bottom.add(request_frame, text='Request 请求 JSON')
        bottom.add(response_frame, text='Response 返回 JSON / 错误')
        self.request_text = readonly_text(request_frame)
        self.response_text = readonly_text(response_frame)

    def add_event(self, event):
        self.counter += 1
        key = event.get('request_id') or f'cache-{self.counter}'
        record = self.records.setdefault(key, {})
        phase = event.get('phase', '')
        if phase == 'retry_wait':
            record.setdefault('retry_events', []).append(event)
        else:
            record['request' if phase == 'request' else 'response'] = event
        request = record.get('request', event)
        status = '等待响应' if phase == 'request' else ('缓存命中（未请求）' if phase == 'cache_hit' else str(event.get('http_status') or event.get('error') or phase))
        if phase == 'retry_wait':
            status = f"等待{event.get('wait_seconds', 0):.0f}秒后重试"
        values = (f"{request.get('module_name', request.get('provider', ''))} / {key}", f"{request.get('method', '')} {request.get('url', '')}", status)
        if self.tree.exists(key):
            self.tree.item(key, values=values)
        else:
            self.tree.insert('', 'end', iid=key, values=values)
        # Keep a bounded UI history; persistent JSONL retains older events.
        if len(self.records) > 150:
            first = next(iter(self.records))
            self.records.pop(first)
            self.tree.delete(first)
        if self.follow_var.get() or not self.tree.selection() or self.tree.selection()[0] == key:
            self.tree.selection_set(key)
            self.tree.see(key)
            self.select()

    def clear(self):
        self.records.clear()
        self.tree.delete(*self.tree.get_children())
        show_text(self.request_text, '')
        show_text(self.response_text, '')

    def select(self, _event=None):
        selected = self.tree.selection()
        if selected:
            record = self.records.get(selected[0], {})
            show_text(self.request_text, json.dumps(record.get('request', {'说明': '无HTTP请求 / 缓存事件'}), ensure_ascii=False, indent=2))
            response = dict(record.get('response', {'状态': '等待供应商返回'}))
            if record.get('retry_events'):
                response['retry_events'] = record['retry_events']
            show_text(self.response_text, json.dumps(response, ensure_ascii=False, indent=2))
