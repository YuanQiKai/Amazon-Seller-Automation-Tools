"""V3 context, per-frame creative recipes and bilingual review workspace."""
from copy import deepcopy
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .asset_gallery import AssetGallery, append_image_paths
from .catalogs import CATALOG_BY_CODE
from .creative_ai import CreativeAI
from .creative_workspace import readonly_text, show_text
from .product_context import ProductContext
from .navigation import NAV_CODE, resize_navigation, instance_name
from .presets import LANGUAGE_LABELS
from .languages import SUPPORTED_LANGUAGES as LANGUAGES, LANGUAGE_NAMES
LANGUAGE_LABELS.update(LANGUAGE_NAMES)


class V30Workspace:
    def _init_v30(self):
        self.module_recipes, self.ai_context = {}, {}
        self.recipe_panels = {}
        self.context_enabled_var = tk.BooleanVar(value=True)
        self.context_status_var = tk.StringVar(value='尚未分析；AI生成前自动读取产品资料与全部产品实拍。')
        self.nav_count_var = tk.StringVar(value='4')
        self.compare_language_var = tk.StringVar(value='zh — 中文')

    def _build_v30(self):
        context = ttk.Frame(self.copy_workspace_tabs, padding=10)
        self.context_tab = context
        self.copy_workspace_tabs.add(context, text='分析上下文')
        row = ttk.Frame(context)
        row.pack(fill=tk.X)
        ttk.Checkbutton(row, text='生成前分析产品图文（默认开启）', variable=self.context_enabled_var).pack(side=tk.LEFT)
        ttk.Button(row, text='分析 / 继续上下文', command=self.analyze_product_context).pack(side=tk.LEFT, padx=5)
        ttk.Button(row, text='强制重新分析全部', command=lambda: self.analyze_product_context(force=True)).pack(side=tk.LEFT, padx=5)
        ttk.Button(row, text='查看详细接口 JSON', command=lambda: self.copy_workspace_tabs.select(self.api_inspector)).pack(side=tk.LEFT)
        ttk.Label(context, text='需选择支持看图的文案模型；全部产品图分批上传。资料改变后自动重新分析。未变化则复用已保存结果，但每次生成仍显式附带上下文。\n关闭仅发送原始产品资料，不进行看图理解。API Key 与图片 Base64 原文不会显示；JSON保留图片数量、摘要和其余参数。', wraplength=1080).pack(fill=tk.X, pady=5)
        ttk.Label(context, text='产品分析补充要求（不是逐图生成提示词）').pack(anchor='w')
        self.context_prompt_text = tk.Text(context, height=3, wrap='word', undo=True)
        self.context_prompt_text.pack(fill=tk.X)
        ttk.Label(context, textvariable=self.context_status_var).pack(anchor='w', pady=5)
        self.context_detail_text = readonly_text(context, 15)
        self._build_recipe_panel(self.copy_workspace_tabs, 'copy')
        self._build_recipe_panel(self.export_workspace_tabs, 'image')
        row = ttk.Frame(self.creative_workspace.content)
        row.pack(fill=tk.X, before=self.creative_workspace.progress)
        ttk.Button(row, text='AI重建卖点与排版：当前', command=lambda: self.optimize_recipes('plan')).pack(side=tk.LEFT)
        ttk.Button(row, text='AI重建卖点与排版：全部', command=lambda: self.optimize_recipes('plan', True)).pack(side=tk.LEFT, padx=5)
        ttk.Label(row, text='重新理解上下文与模块Prompt；重建卖点/角度/依据，覆盖手动字段（自定义区块保留）。', wraplength=400).pack(side=tk.LEFT)

    def _build_recipe_panel(self, notebook, kind):
        if kind == 'image':
            frame = ttk.LabelFrame(self.image_review_body, text='当前图片 Prompt · 风格参考 · AI 优化', padding=10)
            frame.pack(fill=tk.X, pady=12)
        else:
            frame = ttk.LabelFrame(self.creative_workspace.prompt_host, text='当前模块 Prompt · 文案生成指令', padding=8)
            frame.pack(fill=tk.X)
        top = ttk.Frame(frame)
        top.pack(fill=tk.X)
        variable = tk.StringVar()
        combo = ttk.Combobox(top, textvariable=variable, state='readonly', width=64)
        combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(top, text='跟随当前预览模块', command=lambda: self.select_recipe_current(kind)).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text='刷新模块列表', command=self._refresh_recipe_lists).pack(side=tk.LEFT)
        label = '文案' if kind == 'copy' else '图片'
        ttk.Label(frame, text=f'此字段控制本帧{label}与卖点规划；切换模块或自动保存时即保存。导航轮播的每帧可独立填写。', wraplength=430 if kind == 'copy' else 950).pack(anchor='w', pady=5)
        editor = tk.Text(frame, height=5, wrap='word', undo=True)
        editor.pack(fill=tk.X)
        controls = ttk.Frame(frame)
        controls.pack(fill=tk.X, pady=5)
        ttk.Button(controls, text='AI优化当前Prompt', command=lambda: self.optimize_recipes(kind)).pack(side=tk.LEFT)
        ttk.Button(controls, text='AI批量优化全部Prompt', command=lambda: self.optimize_recipes(kind, True)).pack(side=tk.LEFT, padx=5)
        ttk.Button(controls, text='查看产品理解', command=self.show_product_context).pack(side=tk.LEFT)
        ttk.Button(controls, text='局部生成 / 重新生成文案' if kind == 'copy' else '生成/重生本帧', command=lambda: self.generate_planned_copy() if kind == 'copy' else self.generate_recipe_frame(kind)).pack(side=tk.LEFT, padx=5)
        state = {'frame': frame, 'combo': combo, 'var': variable, 'editor': editor, 'id': '', 'choices': {}}
        self.recipe_panels[kind] = state
        combo.bind('<<ComboboxSelected>>', lambda _e: self._recipe_selection_changed(kind))
        editor.bind('<FocusOut>', lambda _e: self._commit_recipe(kind))
        if kind == 'image':
            mode = tk.StringVar(value='AI直接绘制所选语言（需人工检查拼写）')
            ttk.Label(controls, text='仅生成最终成图：使用所选语言的确认文案，需人工检查拼写').pack(side=tk.LEFT, padx=(12, 0))
            state['mode'] = mode
            ttk.Button(frame, text='追加上传本帧风格 / 布局参考图（可多选）', command=self.upload_style_references).pack(anchor='w')
            gallery = AssetGallery(frame, self._replace_style_references)
            gallery.pack(fill=tk.X, pady=4)
            state['gallery'] = gallery
            ttk.Label(frame, text='产品实拍仍从“产品信息”读取。这里的图只参考布局/风格，不会把竞品功能当作你的产品事实。蒙版重生保留原底图，风格通过分析文字传入。', wraplength=1080).pack(anchor='w')
        state['detail'] = readonly_text(frame, 4)
        if kind == 'copy':
            widgets = controls.winfo_children()
            for widget in widgets:
                widget.pack_forget()
            for index, widget in enumerate(widgets):
                widget.grid(row=index//2, column=index%2, sticky='ew', padx=2, pady=3)
            combo.configure(width=28)
            top.winfo_children()[1].pack_forget()
            top.winfo_children()[2].pack_forget()

    def _nav_controls(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=5)
        ttk.Label(row, text='高级导航轮播帧数（默认4）').pack(side=tk.LEFT)
        ttk.Spinbox(row, textvariable=self.nav_count_var, from_=1, to=20, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text='应用到所选轮播组', command=self.resize_selected_navigation).pack(side=tk.LEFT)

    def resize_selected_navigation(self):
        if self.busy:
            return
        selected = self.module_selected_tree.selection() or self.confirm_tree.selection()
        item = next((i for i in self.module_instances if selected and i.instance_id == selected[0]), None)
        if not item or item.module_code != NAV_CODE:
            messagebox.showinfo('请选择轮播', '先在已选列表选择高级导航轮播中的任意一帧。')
            return
        try:
            count = int(self.nav_count_var.get())
            if count < item.frame_count and not messagebox.askyesno('减少轮播帧', '将移除本组末尾多余帧（原始图片文件不会删除），继续吗？'):
                return
            self._commit_v30()
            self.module_instances = resize_navigation(self.module_instances, item.parent_id, count)
            self.module_order = [i.instance_id for i in self.module_instances]
            keep = set(self.module_order)
            self.current_briefs = [b for b in self.current_briefs if b.instance_id in keep]
            self._sync_module_selection()
            self._refresh_review_tree()
            self._refresh_recipe_lists()
        except ValueError as exc:
            messagebox.showerror('帧数无效', str(exc))

    def _refresh_recipe_lists(self):
        for kind, panel in self.recipe_panels.items():
            self._commit_recipe(kind)
            choices = {f'{n:02d} | {instance_name(i, CATALOG_BY_CODE[i.module_code].name)} | {i.instance_id}': i.instance_id
                       for n, i in enumerate(self.module_instances, 1) if i.module_code in CATALOG_BY_CODE}
            panel['choices'] = choices
            panel['combo'].configure(values=tuple(choices))
            selected = next((label for label, iid in choices.items() if iid == panel['id']), next(iter(choices), ''))
            panel['var'].set(selected)
            self._select_recipe(kind, commit=False)

    def _recipe(self, iid):
        item = next((i for i in self.module_instances if i.instance_id == iid), None)
        legacy = self.module_prompts.get(iid, '') or (item.custom_prompt if item else '')
        return {'copy_prompt': legacy, 'image_prompt': legacy, 'style_reference_paths': [],
                **deepcopy(self.module_recipes.get(iid, {})), 'render_text': 'native'}

    def _select_recipe(self, kind, commit=True):
        if commit:
            self._commit_recipe(kind)
        panel = self.recipe_panels[kind]
        panel['id'] = panel['choices'].get(panel['var'].get(), '')
        recipe = self._recipe(panel['id'])
        self._set_text(panel['editor'], recipe[kind+'_prompt'], editable=not self.busy)
        if kind == 'image':
            panel['mode'].set('AI直接绘制所选语言（需人工检查拼写）')
            panel['gallery'].refresh(recipe['style_reference_paths'])
        brief = next((b for b in self.current_briefs if b.instance_id == panel['id']), None)
        effective = getattr(brief, 'effective_'+kind+'_prompt', '') if brief else ''
        show_text(panel['detail'], ('本次实际生成 Prompt（未生成时为空）：\n'+effective+'\n\n提示词优化历史：\n'+
                                  json.dumps(recipe.get('prompt_history', []), ensure_ascii=False, indent=2)))
        if kind == 'image':
            self._refresh_image_comparison()

    def _commit_recipe(self, kind):
        panel = self.recipe_panels.get(kind)
        if not panel or not panel['id'] or self.busy:
            return
        recipe = self._recipe(panel['id'])
        recipe[kind+'_prompt'] = panel['editor'].get('1.0', 'end-1c').strip()
        if kind == 'image':
            recipe['render_text'] = 'native'
        self.module_recipes[panel['id']] = recipe

    def _commit_v30(self):
        for kind in self.recipe_panels:
            self._commit_recipe(kind)

    def _load_v30(self, project):
        for panel in self.recipe_panels.values():
            panel['id'] = ''
        self.module_recipes = deepcopy(project.module_recipes)
        self.ai_context = deepcopy(project.ai_context)
        self.context_enabled_var.set(project.options.context_before_generation)
        self._set_text(self.context_prompt_text, project.context_prompt, editable=True)
        self._receive_context({'status': '已载入产品理解记录' if self.ai_context else '尚未分析产品', 'context': self.ai_context})

    def select_recipe_current(self, kind):
        panel = self.recipe_panels[kind]
        iid = self.creative_workspace.current_id if kind == 'copy' else self.current_image_review_code
        self._refresh_recipe_lists()
        label = next((label for label, value in panel['choices'].items() if value == iid), '')
        if label:
            panel['var'].set(label)
            self._select_recipe(kind)

    def upload_style_references(self):
        if self.busy:
            return
        panel = self.recipe_panels['image']
        if not panel['id']:
            return
        paths = filedialog.askopenfilenames(title='选择本帧布局/风格参考图', filetypes=[('图片', '*.png *.jpg *.jpeg *.webp *.bmp'), ('所有文件', '*.*')])
        if paths:
            self._replace_style_references(append_image_paths(self._recipe(panel['id'])['style_reference_paths'], list(paths)))

    def generate_recipe_frame(self, kind):
        if self.busy:
            return
        self._commit_v30()
        iid = self.recipe_panels[kind]['id']
        if not iid:
            return
        if not any(b.instance_id == iid for b in self.current_briefs):
            messagebox.showinfo('请先生成草稿', '先生成/刷新多语言草稿，让本帧获得卖点、版式和文案，再单独生成。')
            return
        tree = self.review_tree if kind == 'copy' else self.image_review_tree
        tree.selection_set(iid)
        if kind == 'copy':
            self._on_review_select()
            self.regenerate_selected_copy()
        else:
            self._on_image_review_select()
            self.regenerate_review_image()

    def _show_recipe_requests(self):
        for kind, panel in self.recipe_panels.items():
            brief = next((b for b in self.current_briefs if b.instance_id == panel['id']), None)
            if brief:
                show_text(panel['detail'], '本帧实际生成 Prompt：\n'+getattr(brief, 'effective_'+kind+'_prompt', '')+
                          '\n\n提示词优化历史：\n'+json.dumps(self._recipe(panel['id']).get('prompt_history', []), ensure_ascii=False, indent=2))

    def _replace_style_references(self, paths):
        if self.busy:
            return
        self._commit_recipe('image')
        panel = self.recipe_panels['image']
        if panel['id']:
            recipe = self._recipe(panel['id'])
            recipe['style_reference_paths'] = list(paths)
            recipe.pop('style_analysis', None)
            self.module_recipes[panel['id']] = recipe
            panel['gallery'].refresh(paths)

    def show_product_context(self):
        self.tabs.select(self.pages['copy'])
        self.copy_workspace_tabs.select(self.context_tab)

    def analyze_product_context(self, force=False):
        if self.busy:
            return
        project = deepcopy(self.collect_project())
        project.options.context_before_generation = True  # Explicit manual analysis also works when auto-analysis is off.
        if force and not messagebox.askyesno('重新分析全部', '将重新请求全部产品图，可能重复计费；如只需处理失败部分，请选“分析 / 继续上下文”。继续吗？'):
            return
        client = self._build_ai_client(project.options)
        worker = ProductContext(client, lambda e: self.events.put(('product_context', e)), self.generation_controls['copy'].checkpoint)
        self._start_async('v3:context', lambda: worker.prepare(project, force=force), '正在逐张分析产品图；已完成部分将保留...')

    def optimize_recipes(self, action, all_modules=False):
        if self.busy:
            return
        self.creative_workspace.commit()
        project = deepcopy(self.collect_project())
        if all_modules:
            ids = [i.instance_id for i in project.normalized_module_instances()]
        else:
            iid = self.creative_workspace.current_id if action in ('layout', 'plan') else self.recipe_panels[action]['id']
            ids = [iid] if iid else []
        if not ids:
            messagebox.showinfo('请选择模块', '先选择一个模块；版式页可先点击“预览卖点与版式”。')
            return
        worker = CreativeAI(self._build_ai_client(project.options),
                            on_context=lambda e: self.events.put(('product_context', e)),
                            on_item=lambda e: self.events.put(('recipe_result', e)),
                            control=self.generation_controls['copy'].checkpoint)
        self._start_async('v3:'+action, lambda: worker.run(project, ids, action), '正在结合产品上下文优化；每完成一帧即更新...')

    def _receive_context(self, event):
        self.ai_context = deepcopy(event.get('context', self.ai_context))
        self.context_status_var.set(event['status'])
        show_text(self.context_detail_text, json.dumps(self.ai_context, ensure_ascii=False, indent=2))
        self.status_var.set(event['status'])

    def _receive_recipe(self, event):
        iid = event['id']
        if iid not in self.module_order:
            return
        self.module_recipes[iid] = deepcopy(event['recipe'])
        if event['action'] in ('layout', 'plan'):
            previous_results = deepcopy(self.creative_workspace.results)
            self.creative_plans = {**deepcopy(self.creative_workspace.plans), **self.creative_plans}
            self._save_creative_plan(iid, event['plan'])
            self.creative_workspace.set_plans(self.creative_plans, self.module_instances)
            for result in previous_results.values():
                self.creative_workspace.update_progress(result)
            self.creative_workspace.tree.selection_set(iid)
            self.creative_workspace.select()
            self.creative_workspace.update_progress({'instance_id': iid, 'module_name': iid, 'focus': event['plan'].get('focus', ''),
                                                      'status': 'AI规划完成 / 待审核', 'done': event.get('done', 1), 'total': event.get('total', 1)})
        for kind, panel in self.recipe_panels.items():
            if panel['id'] == iid:
                self._select_recipe(kind, commit=False)
        if 'done' in event:
            self.status_var.set(f"{event['action']}：已完成 {event['done']}/{event['total']} 帧，优化结果已写回原字段")

    def _v3_editable(self, enabled):
        for panel in self.recipe_panels.values():
            panel['editor'].configure(state='normal' if enabled else 'disabled')
        self.context_prompt_text.configure(state='normal' if enabled else 'disabled')

    def _build_language_compare(self, parent):
        tabs = ttk.Notebook(parent)
        tabs.pack(fill=tk.BOTH, expand=True)
        language, history = ttk.Frame(tabs, padding=4), ttk.Frame(tabs, padding=4)
        tabs.add(language, text='不同语言对比')
        tabs.add(history, text='历史版本对比')
        combo = ttk.Combobox(language, textvariable=self.compare_language_var,
                             values=[f'{code} — {LANGUAGE_LABELS[code]}' for code in LANGUAGES], state='readonly')
        combo.pack(fill=tk.X, pady=(0, 5))
        combo.bind('<<ComboboxSelected>>', lambda _e: self._refresh_language_compare())
        self.translation_compare_text = readonly_text(language, 12)
        return history

    def _refresh_language_compare(self):
        brief = self._brief_by_code(self.current_review_code)
        target = self.compare_language_var.get().split(' ')[0]
        text = brief.copy_for(target) if brief else ''
        if brief and target == 'zh' and self.current_review_language != 'zh':
            text = brief.chinese_translations.get(self.current_review_language) or ('【通用中文文案；此版本暂无逐语回译】\n' + brief.copy_for('zh'))
        show_text(self.translation_compare_text, text)

    def _recipe_selection_changed(self, kind):
        self._select_recipe(kind)
        if kind == 'copy':
            iid = self.recipe_panels[kind]['id']
            if self.creative_workspace.tree.exists(iid):
                self.creative_workspace.tree.selection_set(iid)
                self.creative_workspace.select()
        if kind == 'image':
            iid = self.recipe_panels[kind]['id']
            if self.image_review_tree.exists(iid):
                self.image_review_tree.selection_set(iid)
                self._on_image_review_select()
