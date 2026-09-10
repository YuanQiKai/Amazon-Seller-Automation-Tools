"""V3.1 UI helpers: named pages, locale selection, branding and provider memory."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

from .languages import LANGUAGE_NAMES, TARGET_LANGUAGES, active_languages, label
from .env_store import read_env_values, upsert_env_value, provider_key_name
from .ai_client import OpenAIClient
from .service import GenerationService
from .brief_generator import BriefGenerator


class WorkspaceV31:
    def _modern_style(self):
        from .ui_themes import ThemeManager, THEMES
        self.theme_var = tk.StringVar(value=next(iter(THEMES)))
        self.theme_manager = ThemeManager(self.root)
        self.theme_manager.apply(self.theme_var.get())

    def _finalize_v31(self):
        old_order = ('product', 'market', 'modules', 'copy', 'api', 'batch', 'image')
        self.pages = dict(zip(old_order, self.tabs.tabs()))
        names = (('api', 'API配置'), ('product', '产品信息'), ('market', '竞品与关键词'),
                 ('modules', '选择模块'), ('copy', '文案生成'), ('image', '图片生成'), ('batch', '批量任务与合规'))
        for index, (key, name) in enumerate(names):
            self.tabs.insert(index, self.pages[key], text=f'{index+1:02d}  {name}')
        self.tabs.select(self.pages['api'])
        self._provider_selection = {}
        self._provider_model_cache = {}
        self._model_events = queue.Queue()
        self._model_request_ids = {}
        self._model_after_id = self.root.after(200, self._poll_model_events)
        for kind, entry in self.ai_key_entries.items():
            entry.bind('<FocusOut>', lambda _e, k=kind: self._remember_provider_key(k), add='+')
        self._add_text_scrollbars()
        self.generate_button.master.pack_configure(before=self.export_workspace_tabs)
        self.control_widgets['image'][0].master.pack_configure(before=self.export_workspace_tabs)
        for parent in (self.review_language_combo.master, self.image_review_actions):
            columns = 2 if parent is self.image_review_actions else 3
            widgets = parent.winfo_children()
            for widget in widgets:
                widget.pack_forget()
            for index, widget in enumerate(widgets):
                widget.grid(row=index//columns, column=index%columns, sticky='w', padx=3, pady=4)
        ttk.Checkbutton(self.copy_scroll_frame.body, text='使用 AI 生成文案（关闭时仅建立离线待翻译需求）', variable=self.optimize_var).pack(fill=tk.X, before=self.copy_scroll_frame.body.winfo_children()[0])

    def _add_text_scrollbars(self):
        # These panes have one text widget each. Keep a scrollbar visible rather
        # than letting long text enlarge the outer notebook beyond the window.
        for name in ('current_copy_text', 'previous_copy_text', 'compare_copy_text', 'self_check_text', 'module_prompt_text'):
            widget = getattr(self, name, None)
            if widget is None or widget.winfo_manager() != 'pack':
                continue
            parent = widget.master
            bar = ttk.Scrollbar(parent, orient='vertical', command=widget.yview)
            widget.configure(yscrollcommand=bar.set)
            bar.pack(side=tk.RIGHT, fill=tk.Y)
            widget.pack_configure(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _build_language_picker(self, parent):
        box = ttk.LabelFrame(parent, text='目标文案语言 · 中文对照自动生成', padding=10)
        box.pack(fill=tk.X, pady=(0, 10))
        self.language_vars = {code: tk.BooleanVar(value=code == 'de') for code in TARGET_LANGUAGES}
        self.language_summary_var = tk.StringVar()
        self.image_language_var = tk.StringVar(value=label('de'))
        common = ('de', 'en', 'fr', 'it', 'es', 'pt', 'nl', 'pl', 'sv')
        common_row = ttk.Frame(box)
        common_row.pack(fill=tk.X)
        for code in common:
            ttk.Checkbutton(common_row, text=LANGUAGE_NAMES[code], variable=self.language_vars[code], command=self._languages_changed).pack(side=tk.LEFT, padx=4)
        self.more_languages = ttk.Frame(box)
        for index, code in enumerate(code for code in TARGET_LANGUAGES if code not in common):
            ttk.Checkbutton(self.more_languages, text=LANGUAGE_NAMES[code], variable=self.language_vars[code], command=self._languages_changed).grid(row=index//8, column=index%8, sticky='w', padx=7, pady=3)
        self.more_language_button = ttk.Button(common_row, text='更多欧洲语言 ▾', command=self._toggle_languages)
        self.more_language_button.pack(side=tk.RIGHT)
        ttk.Label(box, textvariable=self.language_summary_var, style='Subtitle.TLabel', wraplength=1050).pack(anchor='w', pady=(5, 0))
        self._languages_changed()

    def _toggle_languages(self):
        if self.more_languages.winfo_manager():
            self.more_languages.pack_forget()
            self.more_language_button.configure(text='更多欧洲语言 ▾')
        else:
            self.more_languages.pack(fill=tk.X, pady=5)
            self.more_language_button.configure(text='收起语言 ▴')

    def _languages_changed(self):
        selected = [code for code, var in self.language_vars.items() if var.get()]
        if not selected:
            self.language_vars['de'].set(True)
            selected = ['de']
        self.language_summary_var.set('将生成：' + '、'.join(LANGUAGE_NAMES[code] for code in selected) + ' ＋ 中文逐行对照。选择越多，单模块请求量与费用越高。')
        choices = tuple(label(code) for code in active_languages(selected))
        if self.image_language_var.get() not in choices:
            self.image_language_var.set(label(selected[0]))
        if hasattr(self, 'image_language_combo'):
            self.image_language_combo.configure(values=choices)
        if hasattr(self, 'review_language_combo'):
            # Existing historical languages remain readable; do not discard them.
            codes = list(dict.fromkeys([*active_languages(selected), *(code for b in self.current_briefs for code in b.copy if code in LANGUAGE_NAMES)]))
            self.review_language_combo.configure(values=[label(code) for code in codes])
            if self.current_review_language not in codes:
                self.review_language_var.set(label(selected[0]))
                self._on_language_change()

    def _build_brand_preview(self, brand):
        ttk.Label(brand, text='品牌 Slogan').grid(row=5, column=0, sticky='w', pady=6)
        self.scalar_vars['brand_slogan'] = tk.StringVar()
        ttk.Entry(brand, textvariable=self.scalar_vars['brand_slogan']).grid(row=5, column=1, sticky='ew', pady=6)
        visual = ttk.Frame(brand)
        visual.grid(row=6, column=0, columnspan=2, sticky='ew', pady=8)
        self.logo_preview = ttk.Label(visual, text='Logo 预览', anchor='center', width=25)
        self.logo_preview.pack(side=tk.LEFT, padx=(0, 15))
        self.brand_swatches = ttk.Frame(visual)
        self.brand_swatches.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.scalar_vars['brand_colors'].trace_add('write', lambda *_: self._refresh_brand_colors())

    def _refresh_brand_colors(self):
        for widget in self.brand_swatches.winfo_children():
            widget.destroy()
        colors = list(dict.fromkeys(re.findall(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b', self.scalar_vars['brand_colors'].get())))
        for index, color in enumerate(colors[:16]):
            box = ttk.Frame(self.brand_swatches)
            box.grid(row=index//8, column=index%8, padx=5, pady=4)
            canvas = tk.Canvas(box, width=58, height=35, background=color, highlightthickness=1, highlightbackground='#bccbdf')
            canvas.pack()
            ttk.Label(box, text=color.upper(), style='Subtitle.TLabel').pack()
        if not colors:
            ttk.Label(self.brand_swatches, text='导入 Logo 或填写 #RRGGBB 色值后在这里预览色块。', style='Subtitle.TLabel').pack(anchor='w')

    def _refresh_brand_preview(self):
        self._brand_photo = None
        path = Path(self.logo_image_path) if self.logo_image_path else None
        try:
            if path and path.is_file():
                with Image.open(path) as source:
                    preview = source.convert('RGBA')
                    preview.thumbnail((200, 100), Image.Resampling.LANCZOS)
                    self._brand_photo = ImageTk.PhotoImage(preview)
                self.logo_preview.configure(image=self._brand_photo, text='')
            else:
                self.logo_preview.configure(image='', text='未上传 Logo' if not path else 'Logo 文件缺失，请重新上传')
        except (OSError, ValueError):
            self.logo_preview.configure(image='', text='Logo 无法预览，请重新上传')
        self._refresh_brand_colors()

    def _build_merged_confirm(self):
        self.confirm_tree = self.module_selected_tree
        self.confirm_tree.configure(columns=('order', 'channel', 'module', 'instance', 'pc', 'mobile', 'prompt'),
                                    displaycolumns=('order', 'module', 'pc', 'mobile'), height=10)
        for key, title, width in (('order','顺序',45), ('module','模块',165), ('pc','PC尺寸',110), ('mobile','移动尺寸',115), ('prompt','Prompt',155)):
            self.confirm_tree.heading(key, text=title)
            self.confirm_tree.column(key, width=width, minwidth=45)
        self.confirm_tree.bind('<<TreeviewSelect>>', self._on_confirm_select)
        parent = self.confirm_tree.master
        self.confirm_count_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.confirm_count_var, style='Subtitle.TLabel').pack(anchor='w', pady=4)
        ttk.Label(parent, text='模块 Prompt 已合并到「文案生成 → 卖点与排版审核」。', style='Subtitle.TLabel').pack(anchor='w')
        box = ttk.Frame(parent)  # Unmapped legacy editor retained only for old profile migration.
        self.module_prompt_text = tk.Text(box, height=4, wrap='word', undo=True)
        self.module_prompt_text.pack(fill=tk.X)
        self.module_prompt_text.bind('<FocusOut>', lambda _e: self._save_module_prompt())

    @staticmethod
    def _provider_key_name(kind, provider):
        return provider_key_name(kind, provider)

    def _stored_provider_key(self, kind, provider):
        preset = self._provider_presets(kind).get(provider)
        scoped = self._provider_key_name(kind, provider)
        values = read_env_values(self.app_root / '.env')
        legacy = preset.api_key_env if preset else ''
        return values.get(scoped) or os.getenv(scoped, '') or values.get(legacy) or os.getenv(legacy, '')

    def _remember_provider_key(self, kind, quiet=True):
        provider = getattr(self, '_provider_selection', {}).get(kind)
        if not provider:
            return True
        key = self.ai_key_vars[kind].get().strip()
        if not key:  # Empty entry never silently erases an existing saved key.
            return True
        try:
            name = self._provider_key_name(kind, provider)
            current = read_env_values(self.app_root / '.env').get(name)
            if key != current:
                upsert_env_value(self.app_root / '.env', name, key)
            if not quiet:
                self.status_var.set(f'{provider} 的{ "文案" if kind == "text" else "图片"} Key 已独立保存。')
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror('供应商 Key 保存失败', str(exc), parent=self.root)
            return False

    def _fetch_provider_models(self, kind, provider, fallback=False):
        preset = self._provider_presets(kind).get(provider)
        if not preset:
            return
        slot = (kind, fallback)
        self._model_request_ids[slot] = self._model_request_ids.get(slot, 0) + 1
        request_id = self._model_request_ids[slot]
        key = self._stored_provider_key(kind, provider) if fallback else self.ai_key_vars[kind].get().strip()
        if not key:
            self.status_var.set(f'{provider} 尚未配置 Key：请在上方选择该供应商并保存 Key；暂用预设模型。')
            return
        options = replace(self.collect_project(commit_review=False, sync_modules=False, draft=True).options, auto_failover=False)
        setattr(options, f'{kind}_provider', provider)
        client = OpenAIClient.from_options(options, **{f'{kind}_api_key': key}, timeout=30,
            debug_log_path=self.app_root / 'logs' / 'ai_api_debug.jsonl',
            on_api_event=lambda event: self._model_events.put(('api', event)))
        self.status_var.set(f'正在获取 {provider} 的{ "备用" if fallback else "主用"}模型列表…')
        def worker():
            try:
                self._model_events.put(('models', (slot, request_id, provider, client.list_models(kind), '')))
            except Exception as exc:
                self._model_events.put(('models', (slot, request_id, provider, [], str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_model_events(self):
        for _ in range(8):
            try:
                event, payload = self._model_events.get_nowait()
            except queue.Empty:
                break
            if event == 'api':
                self._receive_api_event(payload)
                continue
            slot, request_id, provider, models, error = payload
            kind, fallback = slot
            selected = getattr(self, f'{kind}_fallback_provider_var').get() if fallback else self._provider_id(kind)
            if selected != provider or request_id != self._model_request_ids.get(slot):
                continue  # Slow provider A cannot replace provider B's dropdown.
            if error:
                self.status_var.set(f'{provider} 模型列表获取失败，保留预设/已选模型；详情见接口日志：{error[:180]}')
                continue
            self._provider_model_cache[(kind, provider)] = tuple(models)
            combo = getattr(self, f'{kind}_fallback_model_combo') if fallback else self.ai_model_combos[kind]
            variable = getattr(self, f'{kind}_fallback_model_var') if fallback else self.ai_config_vars[kind]['model']
            combo.configure(values=models)
            if models and variable.get() not in models:
                variable.set(models[0])
            self.status_var.set(f'{provider} 模型列表已更新：{len(models)} 个')
        self._model_after_id = self.root.after(200, self._poll_model_events)

    def export_excel_only(self):
        if self.busy:
            messagebox.showinfo('任务进行中', '请暂停后继续至完成，或结束任务后导出已有结果。', parent=self.root)
            return
        project = self.collect_project(sync_modules=False)
        working = deepcopy(self.current_briefs)
        if not working:
            offline = deepcopy(project)
            offline.options.optimize_copy_with_ai = False
            working = BriefGenerator()._local_briefs(offline)
        if not working:
            messagebox.showinfo('尚无模块', '请先选择要导出的模块。', parent=self.root)
            return
        output = (Path(self.last_output_dir) if self.last_output_dir else
                  project.normalized_output_root(self.app_root) / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-需求表'))
        service = GenerationService(self.app_root, export_excel_on_images=False)
        self._start_async('excel_export', lambda: service.refresh_output(project, working, output, export_excel=True),
                          '正在导出当前文案、审核记录及已有图片到 Excel（不会调用 AI）…')
