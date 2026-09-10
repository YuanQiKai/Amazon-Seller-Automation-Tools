"""Unified planning workspace, theme chooser and non-destructive image comparison."""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageOps

from .ui_themes import THEMES
from .brief_generator import BriefGenerator


class WorkspaceV32:
    def _finalize_v32(self):
        self.ai_plan_var = tk.BooleanVar(value=True)
        row = ttk.Frame(self.root, padding=(18, 0, 18, 8))
        row.pack(fill=tk.X, before=self.tabs)
        ttk.Label(row, text='外观主题', style='Subtitle.TLabel').pack(side=tk.LEFT, padx=(0, 8))
        combo = ttk.Combobox(row, textvariable=self.theme_var, values=tuple(THEMES), state='readonly', width=26)
        combo.pack(side=tk.LEFT)
        combo.bind('<<ComboboxSelected>>', lambda _e: self._apply_theme())
        ttk.Label(row, text='选择 → ✓ 已启用  ·  主题自动保存  ·  激活菜单仅高亮，不改变页面尺寸', style='Subtitle.TLabel').pack(side=tk.LEFT, padx=12)
        self.copy_pages = {'context': self.context_tab, 'plan': self.creative_workspace, 'review': self.copy_review_tab, 'api': self.api_inspector}
        for index, (key, frame) in enumerate(self.copy_pages.items()):
            name = {'context': '分析上下文', 'plan': '卖点与排版审核', 'review': '多语言文案与版本审核', 'api': '接口请求与返回 JSON'}[key]
            self.copy_workspace_tabs.insert(index, frame, text=f'{index+1}  {name}')
        self.copy_workspace_tabs.select(self.context_tab)
        self.creative_workspace.on_selection = self._planning_selected
        ttk.Checkbutton(self.creative_workspace.content, text='生成文案前 AI 重建卖点与排版（保留手动改过的字段与自定义区块；取消后沿用审核版式）', variable=self.ai_plan_var).pack(fill=tk.X, before=self.creative_workspace.progress)
        self._build_image_comparison()
        self._apply_theme()

    def _apply_theme(self):
        if self.theme_var.get() not in THEMES:
            self.theme_var.set(next(iter(THEMES)))
        self.theme_manager.apply(self.theme_var.get())

    def _planning_selected(self, iid):
        panel = self.recipe_panels.get('copy')
        if not panel or not iid or panel['id'] == iid:
            return
        self._commit_recipe('copy')
        label = next((key for key, value in panel['choices'].items() if value == iid), '')
        if label:
            panel['var'].set(label)
            self._select_recipe('copy', commit=False)

    def generate_planned_copy(self):
        if self.busy:
            return
        self.creative_workspace.commit()
        self._commit_v30()
        iid = self.creative_workspace.current_id or self.recipe_panels['copy']['id']
        if not iid:
            messagebox.showinfo('请选择模块', '先在卖点与排版审核中选择一个模块。')
            return
        if not self._brief_by_code(iid):
            # Local generation is also usable before a first global generation.
            project = self.collect_project()
            brief = next((b for b in BriefGenerator()._local_briefs(project) if b.instance_id == iid), None)
            if not brief:
                return
            self.current_briefs.append(brief)
            self.current_briefs.sort(key=lambda b: self.module_order.index(b.instance_id))
            self._refresh_review_tree()
        self.review_tree.selection_set(iid)
        self._on_review_select()
        self.regenerate_selected_copy()

    def _build_image_comparison(self):
        panel = self.recipe_panels['image']
        box = ttk.LabelFrame(panel['frame'], text='版本对比 · 重新生成前 / 当前结果（点击图片放大）', padding=10)
        box.pack(fill=tk.X, pady=8, before=panel['detail'].master)
        self.image_compare_var = tk.StringVar()
        self.image_compare_combo = ttk.Combobox(box, textvariable=self.image_compare_var, state='readonly', width=65)
        self.image_compare_combo.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        self.image_compare_combo.bind('<<ComboboxSelected>>', lambda _e: self._refresh_image_comparison())
        self._image_compare_key = None
        self._image_compare_choices = {}
        self._image_compare_photos = {}
        self._image_compare_paths = {}
        self.image_compare_labels = {}
        self.image_compare_notes = {}
        for column, (key, label) in enumerate((('before', '重新生成前'), ('after', '当前结果'))):
            frame = ttk.Frame(box)
            frame.grid(row=1, column=column, sticky='nsew', padx=5)
            box.columnconfigure(column, weight=1, uniform='comparison')
            ttk.Label(frame, text=label, style='Title.TLabel').pack(anchor='w')
            holder = ttk.Frame(frame, height=280, width=390)
            holder.pack(fill=tk.X)
            holder.pack_propagate(False)
            preview = ttk.Label(holder, anchor='center')
            preview.pack(fill=tk.BOTH, expand=True)
            preview.bind('<Button-1>', lambda _e, side=key: self._enlarge_comparison(side))
            self.image_compare_labels[key] = preview
            note = tk.StringVar()
            ttk.Label(frame, textvariable=note, wraplength=420, style='Subtitle.TLabel').pack(fill=tk.X)
            self.image_compare_notes[key] = note

    def _refresh_image_comparison(self):
        if not hasattr(self, 'image_compare_combo'):
            return
        panel = self.recipe_panels.get('image', {})
        brief = self._brief_by_code(panel.get('id', '')) or (self._selected_image_review_brief() if not panel.get('id') else None)
        identity = (brief.instance_id, brief.image_version) if brief else None
        history = brief.image_history if brief else []
        choices = {f"V{item.get('version', index+1)} · {item.get('archived_at', '')} · {item.get('review_status', '待审核')} [{index+1}]": item for index, item in enumerate(history)}
        self._image_compare_choices = choices
        self.image_compare_combo.configure(values=list(choices))
        if identity != self._image_compare_key or self.image_compare_var.get() not in choices:
            self.image_compare_var.set(next(reversed(choices), '') if choices else '')
        self._image_compare_key = identity
        before = choices.get(self.image_compare_var.get(), {})
        candidates = [p for p in (before.get('german_composite_image'), before.get('ai_effect_image')) if p]
        previous_path = next((p for p in candidates if Path(p).is_file()), candidates[0] if candidates else None)
        self._comparison_image('before', previous_path,
                               '尚无历史版本；首次重新生成后自动展示原图。' if not before else f"V{before.get('version', '')} ｜修改意见：{before.get('revision_notes') or '无'}")
        path = self._preview_path_for(brief) if brief else None
        self._comparison_image('after', path, f'V{brief.image_version} ｜{brief.image_generation_status}' if brief else '等待当前模块的最终结果图。')

    def _comparison_image(self, side, path, note):
        self._image_compare_paths[side] = str(path or '')
        self.image_compare_notes[side].set(note)
        widget = self.image_compare_labels[side]
        if not path:
            widget.configure(image='', text='暂无图片')
            self._image_compare_photos.pop(side, None)
            return
        try:
            with Image.open(path) as source:
                thumb = ImageOps.exif_transpose(source).convert('RGB')
                thumb.thumbnail((390, 275), Image.Resampling.LANCZOS)
                self._image_compare_photos[side] = ImageTk.PhotoImage(thumb)
            widget.configure(image=self._image_compare_photos[side], text='')
        except (OSError, ValueError):
            widget.configure(image='', text='原图文件缺失 / 无法读取；不会覆盖当前结果。')
            self._image_compare_photos.pop(side, None)

    def _enlarge_comparison(self, side):
        path = self._image_compare_paths.get(side)
        if not path or not Path(path).is_file():
            return
        window = tk.Toplevel(self.root)
        window.title(f"{'原版本' if side == 'before' else '当前版本'} · {Path(path).name}")
        with Image.open(path) as source:
            picture = ImageOps.exif_transpose(source).convert('RGB')
            picture.thumbnail((1000, 760), Image.Resampling.LANCZOS)
            window.photo = ImageTk.PhotoImage(picture)
        ttk.Label(window, image=window.photo).pack(padx=12, pady=12)
