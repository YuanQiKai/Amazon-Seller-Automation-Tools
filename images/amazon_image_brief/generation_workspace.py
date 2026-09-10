"""Interactive controls and A+ group-level design actions."""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from .generation_control import GenerationControl
from .creative_planner import plan_modules
from .catalogs import CATALOG_BY_CODE


class GenerationWorkspace:
    def _init_generation_controls(self):
        self.generation_controls = {kind: GenerationControl(kind, lambda event: self.events.put(('control_state', event)))
                                    for kind in ('copy', 'image')}
        self.control_widgets = {}
        self.aplus_continuous_var = tk.BooleanVar(value=False)
        self.aplus_direction_var = tk.StringVar()
        self.aplus_theme_var = tk.StringVar(value='品牌浅色')
        self.poster_device_var = tk.StringVar(value='PC整幅')
        self.poster_status_var = tk.StringVar(value='启用A+连贯模式并生成后，这里预览完整海报。')

    def _control_bar(self, parent, kind):
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=5)
        pause = ttk.Button(bar, text='中断处理（暂停）', state='disabled', command=lambda: self.generation_controls[kind].pause())
        resume = ttk.Button(bar, text='继续生成', state='disabled', command=lambda: self.generation_controls[kind].resume())
        pause.pack(side=tk.LEFT)
        resume.pack(side=tk.LEFT, padx=6)
        stop = ttk.Button(bar, text='结束任务（不再继续）', style='Danger.TButton', state='disabled', command=self.stop_generation)
        stop.pack(side=tk.LEFT, padx=(0, 8))
        status = tk.StringVar(value='未运行。暂停不会丢弃当前已发送请求，返回后从断点继续。')
        ttk.Label(bar, textvariable=status, wraplength=850).pack(side=tk.LEFT, padx=5)
        self.control_widgets[kind] = (pause, resume, status, stop)

    def _control_state(self, event):
        pause, resume, label, stop = self.control_widgets[event['kind']]
        state = event['state']
        pause.configure(state='normal' if state == 'running' else 'disabled')
        resume.configure(state='normal' if state in {'paused', 'pause_requested'} else 'disabled')
        stop.configure(state='normal' if state in {'running', 'paused', 'pause_requested'} else 'disabled')
        label.set({'idle': '未运行 / 本轮结束；已完成结果已保留。', 'running': '处理中，可暂停后续步骤。',
                   'pause_requested': '暂停请求已接收：等待当前请求/步骤完成；不会丢弃结果或取消供应商已开始的计费。',
                   'paused': '已暂停：不再发送新请求。点击“继续生成”从断点接着处理。',
                   'stopped': '已结束：保留已完成结果；本轮不能继续，可重新发起生成。'}[state])

    def stop_generation(self):
        if not self.busy:
            return
        if not messagebox.askyesno('结束当前任务', '结束后不再处理本轮后续模块，也不能点击继续恢复。已完成结果保留。\n供应商已收到的请求可能仍会计费。确定结束吗？', parent=self.root):
            return
        for control in self.generation_controls.values():
            control.stop()
        self.events.activate()  # Ignore old worker results/errors, even if a new task starts.
        if self._event_after_id:
            self.root.after_cancel(self._event_after_id)
            self._event_after_id = None
        self.busy = False
        self._export_needs_sync = False
        self._end_live_results(interrupted=True)
        self.creative_workspace.set_editable(True)
        self._v3_editable(True)
        for kind in ('copy', 'image'):
            self._control_state({'kind': kind, 'state': 'stopped'})
        # Existing workers retain cancelled controls; new jobs get fresh controls.
        self.generation_controls = {kind: GenerationControl(kind, lambda event: self.events.put(('control_state', event)))
                                    for kind in ('copy', 'image')}
        self.generate_button.configure(state='normal')
        self.status_var.set('任务已结束；已完成结果已保留。')
        self.progress_var.set(self.status_var.get())
        if hasattr(self, 'profiles'):
            self.profiles.flush(force=True)

    def _start_controls(self, operation):
        self.creative_workspace.set_editable(False)
        self._v3_editable(False)
        kinds = []
        if operation.startswith('v3:') or operation in {'draft', 'regen_all', 'regen_copy', 'regen_aplus_copy', 'export'}:
            kinds.append('copy')
        if operation in {'export', 'regen_image', 'regen_image_review', 'regen_image_batch', 'image_review_save'}:
            kinds.append('image')
        for kind in kinds:
            self.generation_controls[kind].start()

    def _finish_controls(self):
        self.creative_workspace.set_editable(True)
        self._v3_editable(True)
        for control in self.generation_controls.values():
            control.finish()

    def _aplus_settings(self, parent):
        frame = ttk.LabelFrame(parent, text='A+一体式连贯海报（仅高级A+，其他渠道保持独立）', padding=8)
        frame.pack(fill=tk.X, pady=5)
        ttk.Checkbutton(frame, text='将全部A+章节作为一体式连贯海报制作', variable=self.aplus_continuous_var).pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text='全组视觉/修改要求').pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.aplus_theme_var, values=('品牌浅色', '深色奢雅', '纯白极简', '冷灰科技'), width=12, state='readonly').pack(side=tk.LEFT, padx=4)
        ttk.Entry(row, textvariable=self.aplus_direction_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(row, text='应用整体规划', command=self.apply_aplus_plan).pack(side=tk.LEFT)
        ttk.Label(frame, text='统一章节顺序、品牌色、字体、背景与衔接。保留各模块文案；整幅为审核参考，视频/交互等仍需按后台模块制作。', wraplength=1050).pack(anchor='w')

    def _build_poster_preview(self):
        frame = ttk.Frame(self.export_workspace_tabs, padding=10)
        self.export_workspace_tabs.add(frame, text='A+连贯海报（PC / 移动）')
        row = ttk.Frame(frame)
        row.pack(fill=tk.X)
        combo = ttk.Combobox(row, textvariable=self.poster_device_var, values=('PC整幅', '移动端整幅'), state='readonly', width=16)
        combo.pack(side=tk.LEFT)
        combo.bind('<<ComboboxSelected>>', lambda _e: self._show_poster())
        ttk.Button(row, text='按整体要求重生A+文案', command=self.regenerate_aplus_copy).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text='按整体要求重生A+图片', command=self.regenerate_aplus_images).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text='只更新海报排版（不调用AI）', command=self.recompose_aplus).pack(side=tk.LEFT, padx=4)
        ttk.Label(frame, textvariable=self.poster_status_var, wraplength=1050).pack(anchor='w', pady=5)
        self.poster_canvas = tk.Canvas(frame, height=460, background='#e5e7eb', highlightthickness=0)
        self.poster_canvas.scroll_canvas = self.poster_canvas
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.poster_canvas.yview)
        self.poster_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.poster_canvas.pack(fill=tk.BOTH, expand=True)

    def _receive_poster(self, manifest):
        self.last_result['aplus_poster'] = manifest
        self._show_poster()

    def _show_poster(self):
        manifest = self.last_result.get('aplus_poster', {})
        path = Path(manifest.get('pc' if self.poster_device_var.get() == 'PC整幅' else 'mobile', ''))
        self.poster_canvas.delete('all')
        self.poster_photo = None
        if not path.is_file():
            self.poster_status_var.set('尚无海报；请先启用连贯A+模式并生成。')
            return
        with Image.open(path) as source:
            width = min(950, max(500, self.poster_canvas.winfo_width()-25))
            scale = min(1, width/source.width, 16000/source.height)
            preview = source.resize((max(1, int(source.width*scale)), max(1, int(source.height*scale))), Image.Resampling.LANCZOS)
            self.poster_photo = ImageTk.PhotoImage(preview)
        self.poster_canvas.create_image(5, 5, anchor='nw', image=self.poster_photo)
        self.poster_canvas.configure(scrollregion=self.poster_canvas.bbox('all'))
        self.poster_status_var.set(f"{manifest.get('completed', 0)}/{manifest.get('total', 0)}章图片就绪｜完整原图：{path}\n" + ('；'.join(manifest.get('warnings', [])) or '每章图生成完成后更新整幅预览；审核后按各模块规格拆分使用。'))

    def apply_aplus_plan(self):
        if self.busy:
            messagebox.showinfo('任务进行中', '请本轮结束后修改整体规划；暂停期间可继续审核已完成结果。')
            return None
        self.creative_workspace.commit()
        project = self.collect_project()
        self.creative_plans = plan_modules(project)
        project.creative_plans = deepcopy(self.creative_plans)
        for brief in self.current_briefs:
            plan = self.creative_plans.get(brief.instance_id)
            if plan and brief.channel == '高级A+':
                self._save_creative_plan(brief.instance_id, plan)
        self.creative_workspace.set_plans(self.creative_plans, project.normalized_module_instances())
        self.status_var.set('已更新全组规划；既有图片未改变，可选择重生图片或只更新排版。')
        return project

    def _aplus_project(self, require_output=False):
        if self.busy:
            return None
        if not self.aplus_continuous_var.get():
            messagebox.showinfo('请先启用', '请勾选“一体式连贯海报”。')
            return None
        if not any(b.channel == '高级A+' for b in self.current_briefs):
            messagebox.showinfo('缺少A+草稿', '请先选择A+模块并生成文案草稿。')
            return None
        if require_output and not self.last_output_dir:
            messagebox.showinfo('请先生成', '请先生成最终结果图；Excel 可稍后单独导出。')
            return None
        project = self.apply_aplus_plan()
        if project is None:
            return None
        expected = {item.instance_id for item in project.normalized_module_instances()
                    if CATALOG_BY_CODE.get(item.module_code) and CATALOG_BY_CODE[item.module_code].channel == '高级A+'}
        actual = {b.instance_id for b in self.current_briefs if b.channel == '高级A+'}
        if expected != actual:
            messagebox.showinfo('A+模块已变化', '当前A+草稿与已选模块不一致，请先生成/刷新多语言草稿再进行整体操作。')
            return None
        return project

    def _ordered_aplus(self, project):
        order = {item.instance_id: index for index, item in enumerate(project.normalized_module_instances())}
        return sorted([b for b in self.current_briefs if b.channel == '高级A+'], key=lambda b: order[b.instance_id])

    def regenerate_aplus_copy(self):
        project = self._aplus_project()
        if project is None:
            return
        briefs = deepcopy(self._ordered_aplus(project))
        generator = self._copy_generator(project.options)
        self._start_async('regen_aplus_copy', lambda: generator.regenerate_all_copy(project, briefs, project.options.aplus_direction), '按连贯章节整体重生成A+多语言文案...')

    def regenerate_aplus_images(self):
        project = self._aplus_project(require_output=True)
        if project is None:
            return
        ids = [b.instance_id for b in self._ordered_aplus(project)]
        if not messagebox.askyesno('确认A+整体重生', f'将重新请求生成{len(ids)}张A+图片，供应商可能计费。其他渠道不变。继续吗？'):
            return
        service = self._interactive_service(project.options)
        working = deepcopy(self.current_briefs)
        instructions = {iid: project.options.aplus_direction for iid in ids}
        self._start_async('regen_image_batch', lambda: service.regenerate_images(project, working, ids, Path(self.last_output_dir), instructions), '整体重生成A+图片与连贯海报...')

    def recompose_aplus(self):
        project = self._aplus_project(require_output=True)
        if project is None:
            return
        for brief in self.current_briefs:
            if brief.channel == '高级A+':
                brief.image_review_status = '待复核'
                if self.current_image_review_code == brief.instance_id:
                    self.image_review_status_var.set('待复核')
                self._update_live_rows(brief)
        working = deepcopy(self.current_briefs)
        service = self._interactive_service(project.options)
        self._start_async('image_review_save', lambda: service.refresh_output(project, working, Path(self.last_output_dir)), '只更新A+排版、海报和图片包；Excel 请单独导出（不调用AI）...')
