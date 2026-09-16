"""The simplified, launched UI. Legacy widgets are unmapped for profile migration only."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageOps, ImageTk

from .gui import AmazonImageBriefApp, ScrollFrame
from .models import ProductProject
from .catalogs import CATALOG_BY_CODE
from .languages import TARGET_LANGUAGES, LANGUAGE_NAMES
from .navigation import instance_name
from .asset_gallery import AssetGallery
from .image_previews import preview_pool
from .group_preview import GroupPreview
from .creative_workspace import APIInspector, readonly_text, show_text
from .direct_images import module_recipe, append_assets, image_jobs, validate_recipe, DirectImageService, export_direct


class DirectImageApp(AmazonImageBriefApp):
    def __init__(self, root, app_root):
        self._direct_ready = False
        self._direct_loading = False
        self._pending_direct_ui = {}
        self.direct_current_id = ''
        self.direct_current_language = 'de'
        super().__init__(root, app_root)
        self._build_direct_workspace()
        self._direct_ready = True
        self._load_direct_modules()
        self._restore_direct_ui(self._pending_direct_ui)
        self._update_direct_labels(self.root)
        self._apply_theme()

    def _update_direct_labels(self, parent):
        for widget in parent.winfo_children():
            if isinstance(widget, ttk.Label):
                text = str(widget.cget('text'))
                if '卖点与排版审核' in text and 'Prompt' in text:
                    widget.configure(text='模块排序在此页操作；到「创意生图」编辑各模块 Prompt，并分别上传本体图与参考图。', wraplength=510)
                elif text.startswith('双栏模块规划、供应商容错'):
                    widget.configure(text='逐模块 Prompt · 独立本体与参考图 · 多语言最终图片 · 可恢复生成任务')
            self._update_direct_labels(widget)

    def _build_direct_workspace(self):
        old_page = self.tabs.select()
        old_copy, old_image = self.pages['copy'], self.pages['image']
        old_market = self.pages['market']
        self.tabs.forget(old_copy)
        self.tabs.forget(old_image)
        self.tabs.forget(old_market)
        # Keep legacy fields for lossless profile migration, but never display
        # the retired market page again when restoring an older saved session.
        self.pages['market'] = self.pages['product']
        self.copy_workspace_tabs.pack_forget()
        self.export_workspace_tabs.pack_forget()
        # Keep navigation outside scrolling content: selecting a module must not
        # move its list (or the Prompt editor) out of the viewport.
        page = ttk.Frame(self.tabs, padding=(10, 6))
        self.direct_page = page
        self.tabs.insert(3, page, text='04  创意生图')
        self.pages['copy'] = self.pages['image'] = str(page)
        self.tabs.tab(self.pages['modules'], text='03  选择模块')
        self.tabs.tab(self.pages['batch'], text='05  批量任务与合规')
        if old_page in (old_copy, old_image):
            self.tabs.select(page)
        elif old_page == old_market:
            self.tabs.select(self.pages['product'])
        body = page
        summary = ttk.Frame(body)
        summary.pack(fill=tk.X)
        self.direct_count_var = tk.StringVar()
        ttk.Label(summary, textvariable=self.direct_count_var, style='Subtitle.TLabel').pack(side=tk.LEFT)
        ttk.Button(summary, text='全部语言 / 输出设置', command=self._show_direct_settings).pack(side=tk.RIGHT)
        common = ('de', 'en', 'fr', 'it', 'es', 'pt')
        common_row = summary
        self.direct_language_checks = []
        for code in common:
            checkbox = ttk.Checkbutton(common_row, text=LANGUAGE_NAMES[code], variable=self.language_vars[code], command=self._direct_languages_changed)
            checkbox.pack(side=tk.LEFT, padx=4)
            self.direct_language_checks.append(checkbox)
        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=2)
        self.direct_input_buttons = []
        for index, (label, fn) in enumerate((('生成当前模块', lambda: self.generate_direct('module')),
                                           ('生成全部模块', lambda: self.generate_direct('all')),
                                           ('补齐未完成图片', lambda: self.generate_direct('missing')),
                                           ('导出 Excel / 图片包', self.export_direct_results))):
            button = ttk.Button(actions, text=label, command=fn, style='Primary.TButton' if index == 0 else 'TButton')
            button.grid(row=0, column=index, padx=3, pady=4, sticky='ew')
            self.direct_input_buttons.append(button)
        control_host = ttk.Frame(actions)
        control_host.grid(row=0, column=4, padx=(10, 0), sticky='w')
        self._control_bar(control_host, 'image')
        pause, resume, status, stop = self.control_widgets['image']
        pause.configure(text='暂停')
        resume.configure(text='继续')
        stop.configure(text='停止任务')
        status.set('未运行；切换模块自动保存 Prompt。')
        for widget in pause.master.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.pack_forget()
        progress_row = ttk.Frame(body)
        progress_row.pack(fill=tk.X)
        ttk.Checkbutton(progress_row, text='跟随最新结果（手动切换即取消）', variable=self.follow_image_var).pack(side=tk.RIGHT)
        ttk.Label(progress_row, textvariable=self.progress_var, width=36, style='Subtitle.TLabel').pack(side=tk.LEFT)
        ttk.Label(progress_row, textvariable=status, width=45, style='Subtitle.TLabel').pack(side=tk.LEFT, padx=5)
        ttk.Style(self.root).configure('Direct.Horizontal.TProgressbar', thickness=6)
        self.direct_progress = ttk.Progressbar(body, maximum=100, style='Direct.Horizontal.TProgressbar')
        self.direct_progress.pack(fill=tk.X)
        self.direct_split = ttk.Panedwindow(body, orient=tk.HORIZONTAL)
        self.direct_split.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        modules = ttk.LabelFrame(self.direct_split, text='模块列表 · 单击 / ↑↓ 切换', padding=4, width=330)
        self.direct_split.add(modules, weight=0)
        self.direct_asset_count_var = tk.StringVar()
        ttk.Label(modules, textvariable=self.direct_asset_count_var, style='Subtitle.TLabel').pack(side=tk.BOTTOM, anchor='w')
        listing = ttk.Frame(modules)
        listing.pack(fill=tk.BOTH, expand=True)
        self.direct_module_tree = ttk.Treeview(listing, columns=('order', 'module', 'products', 'refs', 'done'),
                                               displaycolumns=('order', 'module', 'done'), show='headings', height=18, selectmode='browse')
        for key, label, width in (('order','序号',46), ('module','模块',220), ('products','本体图',60), ('refs','参考图',60), ('done','完成',55)):
            self.direct_module_tree.heading(key, text=label)
            self.direct_module_tree.column(key, width=width, minwidth=width if key != 'module' else 110, stretch=key == 'module')
        bar = ttk.Scrollbar(listing, orient='vertical', command=self.direct_module_tree.yview)
        self.direct_module_tree.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.direct_module_tree.pack(fill=tk.BOTH, expand=True)
        self.direct_module_tree.bind('<<TreeviewSelect>>', self._select_direct_module)
        self.direct_module_tree.bind('<ButtonPress-1>', self._manual_direct_navigation)
        self.direct_module_tree.bind('<KeyPress>', self._manual_direct_navigation)
        self.direct_tabs = ttk.Notebook(self.direct_split, width=680)
        self.direct_split.add(self.direct_tabs, weight=1)
        self.direct_split.bind('<Configure>', self._resize_direct_split)
        self.direct_split.bind('<ButtonRelease-1>', self._resize_direct_split)
        inputs, results = ttk.Frame(self.direct_tabs, padding=8), ScrollFrame(self.direct_tabs)
        self.direct_inputs_tab, self.direct_results_tab = inputs, results
        self.direct_tabs.add(inputs, text='01  本模块 Prompt 与图片')
        self.direct_tabs.add(results, text='02  多语言结果与版本对比')
        self.direct_api_inspector = APIInspector(self.direct_tabs)
        self.direct_api_inspector.request_text.configure(width=1)
        self.direct_api_inspector.response_text.configure(width=1)
        for key, width in (('module', 150), ('request', 300), ('status', 110)):
            self.direct_api_inspector.tree.column(key, width=width, minwidth=55)
        self.direct_tabs.add(self.direct_api_inspector, text='03  请求 / 返回 JSON')
        editor = inputs
        self.direct_module_title = tk.StringVar()
        title_row = ttk.Frame(editor)
        title_row.pack(fill=tk.X)
        ttk.Style(self.root).configure('Preview.Primary.TButton', padding=(8, 0), font=('Microsoft YaHei UI', 9, 'bold'))
        self.group_preview_button = ttk.Button(title_row, text='整组预览 · PC / 移动端',
                                               command=self.open_group_preview, style='Preview.Primary.TButton')
        self.group_preview_button.pack(side=tk.RIGHT, padx=(6, 0))
        self.direct_title_label = ttk.Label(title_row, textvariable=self.direct_module_title, style='Section.TLabelframe.Label', wraplength=420)
        self.direct_title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        prompt_box = ttk.LabelFrame(editor, text='Prompt · P01 本体 / R01 参考 · 自动保存', padding=6)
        prompt_box.pack(fill=tk.X)
        self.direct_prompt_text = tk.Text(prompt_box, height=5, width=1, wrap='word', undo=True)
        sb = ttk.Scrollbar(prompt_box, orient='vertical', command=self.direct_prompt_text.yview)
        self.direct_prompt_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.direct_prompt_text.pack(fill=tk.X)
        self.direct_prompt_text.bind('<FocusOut>', lambda _e: self._commit_direct())
        self.direct_assets_frame = ScrollFrame(editor)
        self.direct_assets_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        # The gallery area scrolls independently; Prompt remains pinned above it.
        inputs.scroll_canvas = self.direct_assets_frame.scroll_canvas
        self.direct_galleries = {}
        for kind, title in (('product', '产品本体图 P · 真实外观依据（必传）'), ('reference', '参考图 R · 排版 / 色调 / 文案参考（可选）')):
            group = ttk.LabelFrame(self.direct_assets_frame.body, text=title, padding=6)
            group.pack(fill=tk.X, pady=8)
            buttons = ttk.Frame(group)
            buttons.pack(fill=tk.X)
            button = ttk.Button(buttons, text='追加上传多张图片（不覆盖）', command=lambda k=kind: self.upload_direct_assets(k))
            button.pack(anchor='w')
            self.direct_input_buttons.append(button)
            if kind == 'product':
                button = ttk.Button(buttons, text='复制产品信息中的图片到本模块', command=lambda: self._append_direct_assets('product', self.product_images))
                button.pack(anchor='w', pady=(4, 0))
                self.direct_input_buttons.append(button)
            gallery = AssetGallery(group, lambda paths, k=kind: self._remove_direct_assets(k, paths), compact=True, lazy=True)
            gallery.pack(fill=tk.X, pady=5)
            self.direct_galleries[kind] = gallery
        result_body = results.body
        ttk.Button(result_body, text='整组预览 · 主图轮播 / A+ 上下预览', command=self.open_group_preview).pack(anchor='e', pady=(0, 6))
        result_list = ttk.Frame(result_body)
        result_list.pack(fill=tk.X)
        self.direct_result_tree = ttk.Treeview(result_list, columns=('language', 'status', 'version', 'review'), show='headings', height=3, selectmode='browse')
        for key, title in (('language','语言'), ('status','生成状态'), ('version','版本'), ('review','审核')):
            self.direct_result_tree.heading(key, text=title)
            self.direct_result_tree.column(key, width=140, minwidth=60)
        result_bar = ttk.Scrollbar(result_list, orient='vertical', command=self.direct_result_tree.yview)
        result_bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.direct_result_tree.configure(yscrollcommand=result_bar.set)
        self.direct_result_tree.pack(fill=tk.X)
        self.direct_result_tree.bind('<<TreeviewSelect>>', self._select_direct_result)
        self.direct_result_tree.bind('<ButtonPress-1>', self._manual_direct_navigation)
        self.direct_result_tree.bind('<KeyPress>', self._manual_direct_navigation)
        self.direct_version_var = tk.StringVar()
        self.direct_version_combo = ttk.Combobox(result_body, textvariable=self.direct_version_var, state='readonly')
        self.direct_version_combo.pack(fill=tk.X, pady=6)
        self.direct_version_combo.bind('<<ComboboxSelected>>', lambda _e: self._show_direct_images())
        previews = ttk.Frame(result_body)
        previews.pack(fill=tk.X)
        self.direct_preview_labels, self.direct_photos, self.direct_preview_paths = {}, {}, {}
        self._direct_preview_keys = {}
        self._direct_preview_pool = preview_pool(self.root)
        for index, (side, label) in enumerate((('before','历史版本'), ('after','当前语言结果'))):
            frame = ttk.LabelFrame(previews, text=label+' · 点击图片放大', padding=6)
            frame.grid(row=0, column=index, sticky='nsew', padx=4)
            previews.columnconfigure(index, weight=1, uniform='image-preview', minsize=0)
            frame.pack_propagate(False)
            frame.configure(width=240, height=248)
            holder = ttk.Frame(frame, width=230, height=220)
            holder.pack(fill=tk.BOTH, expand=True)
            holder.pack_propagate(False)
            display = ttk.Label(holder, anchor='center', text='尚无图片')
            display.pack(fill=tk.BOTH, expand=True)
            display.bind('<Button-1>', lambda _e, key=side: self._open_direct_preview(key))
            self.direct_preview_labels[side] = display
        self.direct_review_var = tk.StringVar(value='待审核')
        self.direct_review_combo = ttk.Combobox(result_body, textvariable=self.direct_review_var, values=('待审核', '满意', '不满意-待修改'), state='readonly', width=20)
        self.direct_review_combo.pack(anchor='w', pady=6)
        self.direct_review_combo.bind('<<ComboboxSelected>>', lambda _e: self._commit_direct())
        ttk.Label(result_body, text='当前语言图片的修改意见（与本模块 Prompt、本体图和参考图一起提交）').pack(anchor='w')
        self.direct_revision_text = tk.Text(result_body, height=3, width=1, wrap='word', undo=True)
        self.direct_revision_text.pack(fill=tk.X)
        self.direct_revision_text.bind('<FocusOut>', lambda _e: self._commit_direct())
        button = ttk.Button(result_body, text='按修改意见重新生成当前语言图片', style='Primary.TButton', command=lambda: self.generate_direct('current_language'))
        button.pack(anchor='w', pady=6)
        self.direct_input_buttons.append(button)
        self.direct_result_detail = readonly_text(result_body, 6)
        self.direct_result_detail.configure(width=1)
        self.direct_tabs.bind('<Configure>', self._resize_direct_editor)

    def _show_direct_settings(self):
        window = getattr(self, 'direct_settings_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
            return
        window = self.direct_settings_window = tk.Toplevel(self.root)
        window.title('成图语言与输出设置')
        window.geometry('820x490')
        window.minsize(680, 350)
        window.transient(self.root)
        view = ScrollFrame(window)
        view.pack(fill=tk.BOTH, expand=True)
        body = view.body
        ttk.Label(body, text='每个模块 × 每种勾选语言 = 一张最终图片', style='Section.TLabelframe.Label').pack(anchor='w')
        choices = ttk.Frame(body)
        choices.pack(fill=tk.X, pady=8)
        self.direct_language_checks = [w for w in self.direct_language_checks if w.winfo_exists()]
        for index, code in enumerate(TARGET_LANGUAGES):
            checkbox = ttk.Checkbutton(choices, text=LANGUAGE_NAMES[code], variable=self.language_vars[code],
                                       command=self._direct_languages_changed, state='disabled' if self.busy else 'normal')
            checkbox.grid(row=index//6, column=index%6, sticky='w', padx=6, pady=3)
            self.direct_language_checks.append(checkbox)
        ttk.Label(body, textvariable=self.direct_count_var, wraplength=730).pack(anchor='w', pady=6)
        ttk.Label(body, text='白底首图按语言分别生成，但不添加文字。所有生成按钮均按勾选语言执行。', wraplength=730).pack(anchor='w')
        ttk.Label(body, text='Prompt 示例：以 P01 为产品外观，参考 R01 的构图、R02 的配色。编号在当前模块内有效，删除后不重排；本体图优先于参考图，成图仍需人工验图。', wraplength=730, style='Subtitle.TLabel').pack(anchor='w', pady=6)
        output = ttk.LabelFrame(body, text='输出设置', padding=8)
        output.pack(fill=tk.X)
        ttk.Label(output, text='输出目录').pack(anchor='w')
        row = ttk.Frame(output)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=self.output_root_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text='选择目录', command=self.pick_output_root).pack(side=tk.LEFT, padx=5)
        row = ttk.Frame(output)
        row.pack(fill=tk.X, pady=6)
        ttk.Label(row, text='图片质量').pack(side=tk.LEFT, padx=(0, 8))
        ttk.Combobox(row, textvariable=self.quality_var, values=('low', 'medium', 'high'), state='readonly', width=12).pack(side=tk.LEFT)
        ttk.Button(body, text='完成 · 返回模块编辑', command=window.destroy, style='Primary.TButton').pack(anchor='e', pady=10)
        self.input_undo.install(window)
        self._apply_theme()

    def _resize_direct_split(self, _event=None):
        if not self.direct_split.winfo_exists() or len(self.direct_split.panes()) < 2:
            return
        width = self.direct_split.winfo_width()
        if width < 2:
            return
        current = self.direct_split.sashpos(0)
        wanted = current if getattr(self, '_direct_split_sized', False) else 330
        wanted = max(270, min(wanted, max(270, width-620)))
        if current != wanted:
            self.direct_split.sashpos(0, wanted)
        self._direct_split_sized = True

    def _resize_direct_editor(self, _event=None):
        if not self.direct_title_label.winfo_exists():
            return
        width = max(200, self.direct_tabs.winfo_width()-38)
        self.direct_title_label.configure(wraplength=max(160, width-self.group_preview_button.winfo_reqwidth()-12))
        for frame in (self.direct_inputs_tab, self.direct_results_tab.body, self.direct_api_inspector):
            for widget in frame.winfo_children():
                if isinstance(widget, ttk.Label):
                    widget.configure(wraplength=width)
        if self._direct_ready:
            self._show_direct_images()

    def _manual_direct_navigation(self, event):
        tree = event.widget
        if event.type == tk.EventType.ButtonPress:
            if not tree.identify_row(event.y):
                return
        elif event.keysym not in ('Up', 'Down', 'Home', 'End', 'Prior', 'Next'):
            return
        # This binding precedes the native selection binding; do not consume it.
        # Automatic status events can update data but must not undo a user's click.
        if self.busy:
            self.follow_image_var.set(False)

    def _recipe_project(self):
        return ProductProject(product_image_paths=list(self.product_images), module_recipes=self.module_recipes, module_prompts=self.module_prompts)

    def _load_direct_modules(self):
        if not self._direct_ready:
            return
        self._commit_direct()
        project = self._recipe_project()
        for instance in self.module_instances:
            iid = instance.instance_id
            recipe = module_recipe(project, iid)
            legacy = self._brief_by_code(iid)
            if legacy and legacy.ai_effect_image and not recipe['direct_results'] and not recipe.get('direct_migrated'):
                language = legacy.image_language or 'de'
                recipe['direct_results'][language] = {'final_image': legacy.german_composite_image or legacy.ai_effect_image,
                                                     'status': '已完成', 'version': legacy.image_version,
                                                     'review_status': legacy.image_review_status, 'revision_notes': legacy.image_revision_notes,
                                                     'history': [{'final_image': h.get('german_composite_image') or h.get('ai_effect_image'),
                                                                  'version': h.get('version', 1)} for h in legacy.image_history]}
            recipe['direct_migrated'] = True
            self.module_recipes[iid] = recipe
        self._refresh_direct_rows()
        ids = self.direct_module_tree.get_children()
        iid = self.direct_current_id if self.direct_current_id in ids else (ids[0] if ids else '')
        self.direct_current_id = ''
        if iid:
            self.direct_module_tree.selection_set(iid)
            self._select_direct_module()
        else:
            self.direct_module_title.set('尚未选择模块')
            self._set_text(self.direct_prompt_text, '', editable=True)
            for gallery in self.direct_galleries.values():
                gallery.refresh([])
            self._refresh_direct_results()
        self._direct_languages_changed()

    def _refresh_direct_rows(self):
        tree = self.direct_module_tree
        valid = {instance.instance_id for instance in self.module_instances}
        removed = [iid for iid in tree.get_children() if iid not in valid]
        if removed:
            tree.delete(*removed)
        languages = [code for code, var in self.language_vars.items() if var.get()]
        for index, instance in enumerate(self.module_instances, 1):
            recipe = self.module_recipes.get(instance.instance_id, {})
            done = sum(recipe.get('direct_results', {}).get(lang, {}).get('status') == '已完成' for lang in languages)
            values = (index, instance_name(instance, CATALOG_BY_CODE[instance.module_code].name),
                      len(recipe.get('direct_product_images', [])), len(recipe.get('direct_reference_images', [])), f'{done}/{len(languages)}')
            if tree.exists(instance.instance_id):
                tree.item(instance.instance_id, values=values)
                if tree.index(instance.instance_id) != index-1:
                    tree.move(instance.instance_id, '', index-1)
            else:
                tree.insert('', index-1, iid=instance.instance_id, values=values)
        recipe = self.module_recipes.get(self.direct_current_id, {})
        self.direct_asset_count_var.set(f"当前模块：本体 {len(recipe.get('direct_product_images', []))} 张 · 参考 {len(recipe.get('direct_reference_images', []))} 张")

    def _direct_languages_changed(self):
        if not self._direct_ready:
            return
        self._commit_direct()
        count = sum(var.get() for var in self.language_vars.values())
        self.direct_count_var.set(f'{len(self.module_instances)} 个模块 × {count} 种语言 = {len(self.module_instances)*count} 张图')
        self._refresh_direct_rows()
        self._refresh_direct_results()
        self._refresh_group_preview()

    def open_group_preview(self):
        self._commit_direct()
        viewer = getattr(self, 'group_preview', None)
        if viewer is None or not viewer.winfo_exists():
            self.group_preview = GroupPreview(self)
        else:
            viewer.refresh()
            viewer.deiconify()
            viewer.lift()

    def _refresh_group_preview(self):
        viewer = getattr(self, 'group_preview', None)
        if viewer is not None and viewer.winfo_exists():
            viewer.refresh()

    def _commit_direct(self):
        if not self._direct_ready or self._direct_loading or self.busy or not self.direct_current_id:
            return
        recipe = self.module_recipes.get(self.direct_current_id)
        if not recipe:
            return
        recipe['direct_prompt'] = self.direct_prompt_text.get('1.0', 'end-1c').strip()
        record = recipe.get('direct_results', {}).get(self.direct_current_language)
        if record is not None:
            record['revision_notes'] = self.direct_revision_text.get('1.0', 'end-1c').strip()
            record['review_status'] = self.direct_review_var.get()

    def _select_direct_module(self, _event=None):
        selection = self.direct_module_tree.selection()
        if self._direct_loading or not selection or selection[0] == self.direct_current_id or selection[0] not in self.module_recipes:
            return
        self._commit_direct()
        self.direct_current_id = selection[0]
        self.direct_module_tree.focus(self.direct_current_id)
        self.direct_module_tree.see(self.direct_current_id)
        recipe = self.module_recipes[self.direct_current_id]
        instance = next(i for i in self.module_instances if i.instance_id == self.direct_current_id)
        self.direct_module_title.set(instance_name(instance, CATALOG_BY_CODE[instance.module_code].name) + ' · ' + self.direct_current_id)
        was_loading, self._direct_loading = self._direct_loading, True
        try:
            self._set_text(self.direct_prompt_text, recipe['direct_prompt'], editable=not self.busy)
            for kind, gallery in self.direct_galleries.items():
                records = recipe['direct_'+kind+'_images']
                gallery.number_labels = {r['asset_path']: r['id'] for r in records}
                gallery.refresh([r['asset_path'] for r in records], context_key=self.direct_current_id)
            self._refresh_direct_results()
        finally:
            self._direct_loading = was_loading
        self.direct_asset_count_var.set(f"当前模块：本体 {len(recipe['direct_product_images'])} 张 · 参考 {len(recipe['direct_reference_images'])} 张")
        self.direct_inputs_tab.scroll_canvas.yview_moveto(0)
        self.direct_results_tab.scroll_canvas.yview_moveto(0)

    def upload_direct_assets(self, kind):
        if self.busy or not self.direct_current_id:
            return
        paths = filedialog.askopenfilenames(title='追加产品本体图' if kind == 'product' else '追加布局/色调/文案参考图', filetypes=[('图片', '*.png *.jpg *.jpeg *.webp *.bmp'), ('所有文件', '*.*')])
        if paths:
            self._append_direct_assets(kind, paths)

    def _append_direct_assets(self, kind, paths):
        if self.busy or not self.direct_current_id:
            return
        self._commit_direct()
        self.module_recipes[self.direct_current_id] = append_assets(self.module_recipes[self.direct_current_id], kind, paths)
        self._reload_direct_selection()

    def _remove_direct_assets(self, kind, paths):
        if self.busy or not self.direct_current_id:
            return
        self._commit_direct()
        recipe = self.module_recipes[self.direct_current_id]
        recipe['direct_'+kind+'_images'] = [r for r in recipe['direct_'+kind+'_images'] if r['asset_path'] in paths]
        self._reload_direct_selection()

    def _reload_direct_selection(self):
        iid = self.direct_current_id
        self._refresh_direct_rows()
        self.direct_current_id = ''
        self.direct_module_tree.selection_set(iid)
        self._select_direct_module()

    def _refresh_direct_results(self):
        recipe = self.module_recipes.get(self.direct_current_id, {})
        records = recipe.get('direct_results', {})
        languages = list(dict.fromkeys([*(c for c, v in self.language_vars.items() if v.get()), *records]))
        tree = self.direct_result_tree
        removed = [iid for iid in tree.get_children() if iid not in languages]
        if removed:
            tree.delete(*removed)
        for language in languages:
            record = records.get(language, {})
            values = (LANGUAGE_NAMES.get(language, language), record.get('status', '未生成'),
                      record.get('version', '—'), record.get('review_status', '—'))
            if tree.exists(language):
                tree.item(language, values=values)
            else:
                tree.insert('', 'end', iid=language, values=values)
        if languages:
            language = self.direct_current_language if self.direct_current_language in languages else languages[0]
            self.direct_current_language = language
            if tree.selection() != (language,):
                tree.selection_set(language)
            self._load_direct_result()
        else:
            self.direct_current_language = ''
            self._load_direct_result()

    def _load_direct_result(self):
        record = self.module_recipes.get(self.direct_current_id, {}).get('direct_results', {}).get(self.direct_current_language, {})
        was_loading, self._direct_loading = self._direct_loading, True
        try:
            self.direct_review_var.set(record.get('review_status', '待审核'))
            self._set_text(self.direct_revision_text, record.get('revision_notes', ''), editable=not self.busy)
            self._show_direct_images()
        finally:
            self._direct_loading = was_loading

    def _select_direct_result(self, _event=None):
        selection = self.direct_result_tree.selection()
        if self._direct_loading or not selection or selection[0] == self.direct_current_language:
            return
        self._commit_direct()
        self.direct_current_language = selection[0]
        self.direct_version_var.set('')
        self._load_direct_result()

    def _show_direct_images(self):
        record = self.module_recipes.get(self.direct_current_id, {}).get('direct_results', {}).get(self.direct_current_language, {})
        history = {f"V{r.get('version', index+1)} · {r.get('archived_at', '')} [{index+1}]": r for index, r in enumerate(record.get('history', []))}
        self.direct_version_combo.configure(values=list(history))
        if self.direct_version_var.get() not in history:
            self.direct_version_var.set(next(reversed(history), '') if history else '')
        before = history.get(self.direct_version_var.get(), {})
        for side, item in (('before', before), ('after', record)):
            path = item.get('final_image', '')
            self.direct_preview_paths[side] = path
            width = min(455, max(120, self.direct_tabs.winfo_width()//2-46))
            key = (path, width)
            if self._direct_preview_keys.get(side) == key:
                continue
            self._direct_preview_keys[side] = key
            display = self.direct_preview_labels[side]
            self._direct_preview_pool.cancel(display)
            self.direct_photos.pop(side, None)
            display.configure(image='', text='正在加载预览…' if path else '尚无图片')
            if path:
                self._direct_preview_pool.request(display, side, path, (width, 218),
                                                  lambda data, s=side: self._direct_image_loaded(s, data))
        show_text(self.direct_result_detail, f"状态：{record.get('status', '未生成')}\n错误：{record.get('error') or '无'}\n\n实际生图 Prompt：\n{record.get('effective_prompt', '')}")

    def _direct_image_loaded(self, side, data):
        if data.error:
            self.direct_photos.pop(side, None)
            self.direct_preview_labels[side].configure(image='', text='图片文件缺失或无法读取')
            return
        self.direct_photos[side] = ImageTk.PhotoImage(data.image, master=self.root)
        self.direct_preview_labels[side].configure(image=self.direct_photos[side], text='')

    def _open_direct_preview(self, side):
        path = self.direct_preview_paths.get(side)
        if not path or not Path(path).is_file():
            return
        window = tk.Toplevel(self.root)
        window.title(Path(path).name)
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((1100, 800), Image.Resampling.LANCZOS)
            window.photo = ImageTk.PhotoImage(image)
        ttk.Label(window, image=window.photo).pack(padx=10, pady=10)

    def collect_project(self, *args, **kwargs):
        self._commit_direct()
        project = super().collect_project(*args, **kwargs)
        if self._direct_ready:
            project.options.optimize_copy_with_ai = False
            project.options.ai_plan_before_copy = False
            project.options.context_before_generation = False
            project.options.aplus_continuous = False
        return project

    def populate_project(self, project):
        if self._direct_ready:
            self.direct_current_id = ''
        super().populate_project(project)
        if self._direct_ready:
            self._load_direct_modules()

    def _sync_module_selection(self):
        super()._sync_module_selection()
        if self._direct_ready:
            self._load_direct_modules()

    def duplicate_module_instance(self, tree=None):
        before = set(self.module_order)
        self._commit_direct()
        super().duplicate_module_instance(tree)
        if self._direct_ready:
            for iid in set(self.module_order) - before:
                self.module_recipes[iid]['direct_results'] = {}
            self._refresh_direct_rows()

    def _restore_direct_ui(self, ui):
        self._pending_direct_ui = ui
        if not self._direct_ready:
            return
        self._load_direct_modules()
        for recipe in self.module_recipes.values():
            for record in recipe.get('direct_results', {}).values():
                if record.get('status') == '正在生成':
                    record['status'] = '上次未完成 / 可补齐生成'
        iid = ui.get('direct_module_id') or ui.get('image_code') or ui.get('review_code', '')
        if iid and self.direct_module_tree.exists(iid):
            self.direct_module_tree.selection_set(iid)
            self._select_direct_module()
        language = ui.get('direct_language') or ui.get('review_language', 'de')
        if self.direct_result_tree.exists(language):
            self.direct_result_tree.selection_set(language)
            self._select_direct_result()

    def _direct_editable(self, enabled):
        if not self._direct_ready:
            return
        for widget in [*self.direct_input_buttons, *self.direct_language_checks]:
            if widget.winfo_exists():
                widget.configure(state='normal' if enabled else 'disabled')
        for widget in (self.direct_prompt_text, self.direct_revision_text):
            widget.configure(state='normal' if enabled else 'disabled')
        self.direct_review_combo.configure(state='readonly' if enabled else 'disabled')

    def _start_controls(self, operation):
        super()._start_controls(operation)
        if operation == 'direct_images':
            self.generation_controls['image'].start()
            self._direct_editable(False)

    def _finish_controls(self):
        super()._finish_controls()
        self._direct_editable(True)

    def _end_live_results(self, interrupted=False):
        if self._direct_ready and self._live_operation == 'direct_images':
            if interrupted:
                for recipe in self.module_recipes.values():
                    for record in recipe.get('direct_results', {}).values():
                        if record.get('status') == '正在生成':
                            record['status'] = '已停止 / 保留已有结果'
            self._direct_editable(True)
            self._refresh_direct_rows()
            self._refresh_direct_results()
        super()._end_live_results(interrupted)

    def generate_direct(self, scope):
        if self.busy:
            return
        self._commit_direct()
        try:
            project = self.collect_project(sync_modules=False)
            project.options.generate_ai_images = True
            ids = [self.direct_current_id] if scope in ('module', 'current_language') else None
            languages = [self.direct_current_language] if scope == 'current_language' else None
            jobs = image_jobs(project, ids, languages, scope == 'missing')
            for instance, _lang, recipe in jobs:
                validate_recipe(recipe, CATALOG_BY_CODE[instance.module_code].name)
            if not jobs:
                messagebox.showinfo('没有待生成图片', '所选范围没有未完成图片，或尚未添加模块。')
                return
            revisions = {(i.instance_id, lang): r['direct_results'].get(lang, {}).get('revision_notes', '') for i, lang, r in jobs}
            client = self._build_ai_client(project.options)
            if not client.image_available:
                raise ValueError('请先配置图片供应商的 API Key 和图片模型。')
            if not messagebox.askyesno('确认图片生成', f'本次将生成 {len(jobs)} 张图片，使用图片模型 {project.options.image_model}。\n每个模块按已选语言分别请求，供应商可能计费。继续吗？', parent=self.root):
                return
        except (ValueError, OSError) as exc:
            messagebox.showerror('请检查模块输入', str(exc))
            return
        self._direct_revealed = False
        worker = DirectImageService(self.app_root, client, lambda e: self.events.put(('direct_image_result', e)), self.generation_controls['image'].checkpoint)
        self._start_async('direct_images', lambda: worker.run(project, ids, languages, scope == 'missing', revisions), f'准备生成 {len(jobs)} 张多语言图片...')

    def _receive_direct_image(self, event):
        iid, language = event['id'], event['language']
        if iid not in self.module_order:
            return
        self.module_recipes[iid].setdefault('direct_results', {})[language] = deepcopy(event['result'])
        self.last_output_dir = event['output_dir']
        self.direct_progress['value'] = event['done']/max(1, event['total'])*100
        self.progress_var.set(f"{event['done']}/{event['total']}｜{iid} · {LANGUAGE_NAMES[language]}｜{event['result']['status']}")
        self.status_var.set(self.progress_var.get())
        self._refresh_direct_rows()
        reveal = event['result']['status'] == '已完成' and self.follow_image_var.get()
        if reveal:
            self._direct_revealed = True
            self.direct_module_tree.selection_set(iid)
            self.direct_module_tree.see(iid)
            self._select_direct_module()
            self.direct_current_language = language
            self.tabs.select(self.direct_page)
            self.direct_tabs.select(self.direct_results_tab)
            self.direct_results_tab.scroll_canvas.yview_moveto(0)
        if self.direct_current_id == iid:
            self._refresh_direct_results()
        self._refresh_group_preview()
        self.root.update_idletasks()  # Paint the returned image before managed-asset autosave.
        if reveal:
            canvas = self.direct_results_tab.scroll_canvas
            bounds = canvas.bbox('all')
            if bounds and bounds[3] > 0:
                target = canvas.canvasy(0) + self.direct_preview_labels['after'].winfo_rooty() - canvas.winfo_rooty() - 10
                canvas.yview_moveto(max(0, target / bounds[3]))
                self.root.update_idletasks()
        if hasattr(self, 'profiles'):
            self.profiles.flush(force=True)

    def _receive_api_event(self, payload):
        super()._receive_api_event(payload)
        if self._direct_ready:
            self.direct_api_inspector.add_event(payload)

    def _handle_operation_done(self, operation, result):
        if operation == 'direct_images':
            self.status_var.set(f"本轮完成：成功 {result['completed']} 张，失败 {result['failed']} 张。结果已逐图保存。")
            self.progress_var.set(self.status_var.get())
            return
        if operation == 'direct_export':
            self.last_output_dir = result['output_dir']
            self.status_var.set('Excel 和最终图片包已导出；没有调用 AI。')
            messagebox.showinfo('导出完成', f"Excel：{result['excel_path']}\n图片包：{result['archive_path']}")
            return
        super()._handle_operation_done(operation, result)

    def export_direct_results(self):
        if self.busy:
            return
        project = self.collect_project(sync_modules=False)
        destination = project.normalized_output_root(self.app_root)/('export-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        self._start_async('direct_export', lambda: export_direct(project, destination), '导出当前已选语言的最终图片与模块需求，不调用 AI...')

    def start_batch_queue(self):
        from .ai_client import OpenAIClient
        def processor(project, progress, checkpoint):
            client = OpenAIClient.from_options(project.options, cache_dir=self.app_root/'.cache'/'ai',
                                               debug_log_path=self.app_root/'logs'/'ai_api_debug.jsonl',
                                               debug_enabled=project.options.api_debug_enabled)
            def receive(event):
                progress(f"{event['id']} {event['language']} {event['result']['status']}", event['done'], event['total'])
            result = DirectImageService(self.app_root, client, receive, checkpoint).run(project)
            if result['failed']:
                raise ValueError(f"{result['failed']} 张图片失败；已完成图片保留在 {result['output_dir']}")
            return result
        self.batch_queue.start(processor, lambda: self.batch_events.put('refresh'))
        self._refresh_batch_tree()
        self.status_var.set('批量队列使用模块 Prompt 与两类参考图片直接生图，不调用旧文案/排版流程。')

    def show_cost_estimate(self):
        if not self._direct_ready:
            return super().show_cost_estimate()
        from .costing import estimate_project_cost
        try:
            project = self.collect_project(sync_modules=False)
            project.options.generate_ai_images = True
            count = len(image_jobs(project))
            estimate = estimate_project_cost(project, module_count=count)
            amount = '当前模型未配置费率' if estimate.image_cost is None else f'约 {estimate.currency} {estimate.image_cost:.4f}'
            messagebox.showinfo('多语言图片费用预估', f'{len(self.module_instances)} 个模块 × {len(project.copy_languages)} 种语言 = {count} 张图。\n{amount}。不包含单独文案/排版调用。\n这里只是预算参考，图像输入token、重试和实际价格以供应商账单为准。')
        except ValueError as exc:
            messagebox.showinfo('请先选择语言', str(exc))
