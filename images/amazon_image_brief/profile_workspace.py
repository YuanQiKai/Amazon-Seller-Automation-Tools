"""Named profiles plus automatic recovery for the Tk workbench."""
from __future__ import annotations

from dataclasses import asdict, fields
import json
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .models import ModuleBrief, ProductProject
from .languages import SUPPORTED_LANGUAGES as LANGUAGES
from .profile_store import ProfileStore


RAW_VARIABLES = ('global_revision_var', 'max_retries_var', 'requests_per_minute_var',
                 'similarity_threshold_var', 'review_status_var', 'review_notes_var',
                 'review_language_var', 'preview_asset_var', 'image_review_status_var',
                 'follow_copy_var', 'follow_image_var', 'poster_device_var', 'compare_language_var', 'nav_count_var', 'theme_var')
RAW_TEXTS = ('competitors_text', 'keywords_text', 'custom_fields_text',
             'current_copy_text', 'image_revision_text', 'module_prompt_text')


class ProfileWorkspace:
    def __init__(self, app):
        self.app = app
        self.store = ProfileStore(app.app_root / 'user_data')
        self.profile_id = ''
        self.name = ''
        self.loading = False
        self.enabled = True
        self.last_snapshot = ''
        self.timer = None
        self.choices = {}
        self.selected = tk.StringVar()
        self.name_var = tk.StringVar()
        self.state = tk.StringVar(value='每 2 秒自动保存；命名配置可下拉切换')
        bar = app.profile_bar
        ttk.Label(bar, text='已保存配置').pack(side=tk.LEFT, padx=(0, 8))
        self.combo = ttk.Combobox(bar, textvariable=self.selected, state='readonly', width=24)
        self.combo.pack(side=tk.LEFT)
        self.combo.bind('<<ComboboxSelected>>', self.select_profile)
        ttk.Label(bar, text='配置名称').pack(side=tk.LEFT, padx=(12, 5))
        self.name_entry = ttk.Entry(bar, textvariable=self.name_var, width=23)
        self.name_entry.pack(side=tk.LEFT)
        self.name_entry.bind('<Return>', lambda _e: self.save_current())
        ttk.Button(bar, text='保存 / 重命名', command=self.save_current).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text='另存为新配置', command=self.save_as).pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.state, style='Subtitle.TLabel').pack(side=tk.LEFT, padx=12)
        self.refresh()
        try:
            snapshot = self.store.load()
            if snapshot:
                self.restore(snapshot)
                self.state.set('已恢复上次内容；后续修改自动保存' if not snapshot.get('ui', {}).get('was_generating')
                               else '已恢复已完成部分；未完成模块请手动重试')
            if self.store.warnings:
                self.state.set(self.store.warnings[0])
        except (ValueError, OSError, TypeError, KeyError) as exc:
            self.enabled = False  # Never overwrite an unreadable recovery file.
            self.state.set('上次配置读取失败，自动保存已暂停')
            messagebox.showwarning('配置恢复失败', f'{exc}\n原文件保留在 user_data 中。可另存为新配置继续。', parent=app.root)
        self.last_snapshot = self._fingerprint(self.capture())
        self.schedule()
        app.root.protocol('WM_DELETE_WINDOW', self.close)

    def refresh(self):
        self.choices = {f"{item['name']}  [{item['id'][:6]}]": item for item in self.store.list_profiles()}
        self.combo.configure(values=tuple(self.choices))
        label = next((label for label, item in self.choices.items() if item['id'] == self.profile_id), '')
        self.selected.set(label or '未命名草稿（自动恢复）')
        self.name_var.set(self.name)

    @staticmethod
    def _fingerprint(snapshot):
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    def capture(self, commit=False):
        app = self.app
        project = app.collect_project(commit_review=commit, sync_modules=False, draft=True).to_dict()
        # Connection headers are loaded from provider configuration, not profiles.
        # API-key entry variables are deliberately absent from every snapshot.
        project['options']['text_extra_headers'] = '{}'
        project['options']['image_extra_headers'] = '{}'
        ui = {
            'variables': {name: getattr(app, name).get() for name in RAW_VARIABLES},
            'texts': {name: getattr(app, name).get('1.0', 'end-1c') for name in RAW_TEXTS},
            'product_texts': {name: widget.get('1.0', 'end-1c') for name, widget in app.text_widgets.items()},
            'scalar_values': {name: value.get() for name, value in app.scalar_vars.items()},
            'variant_index': app.variant_current_index,
            'variant_editor': {key: value.get() for key, value in app.variant_vars.items()},
            'review_code': app.current_review_code,
            'review_language': app.current_review_language,
            'image_code': app.current_image_review_code,
            'prompt_code': app.current_prompt_code,
            'selected_modules': list(app.module_selected_tree.selection()),
            'creative_id': app.creative_workspace.current_id,
            'creative_editor': {key: getattr(app.creative_workspace, key).get() for key in ('focus_var', 'visual_var', 'layout_var', 'angle_var', 'evidence_var')},
            'creative_results': app.creative_workspace.results,
            'creative_progress': app.creative_workspace.progress_var.get(),
            'creative_percent': float(app.creative_workspace.progress['value']),
            'tab': app.tabs.index(app.tabs.select()),
            'page_key': next((key for key, value in app.pages.items() if value == app.tabs.select()), 'api'),
            'ui_version': 32,
            'direct_module_id': getattr(app, 'direct_current_id', ''),
            'direct_language': getattr(app, 'direct_current_language', 'de'),
            'copy_page_key': next((key for key, value in app.copy_pages.items() if str(value) == app.copy_workspace_tabs.select()), 'context'),
            'copy_tab': app.copy_workspace_tabs.index(app.copy_workspace_tabs.select()),
            'export_tab': app.export_workspace_tabs.index(app.export_workspace_tabs.select()),
            'was_generating': app.busy,
            'model_lists': {kind: list(combo.cget('values')) for kind, combo in app.ai_model_combos.items()},
        }
        return {'project': project, 'briefs': [asdict(brief) for brief in app.current_briefs], 'ui': ui,
                'output': {'directory': app.last_output_dir,
                           'result': {key: value for key, value in app.last_result.items() if key != 'briefs'}}}

    def restore(self, snapshot):
        app = self.app
        project = ProductProject.from_dict(snapshot['project'])
        allowed = {field.name for field in fields(ModuleBrief)}
        briefs = [ModuleBrief(**{key: value for key, value in item.items() if key in allowed}) for item in snapshot.get('briefs', [])]
        ui = snapshot.get('ui', {})
        self.loading = True
        try:
            app.populate_project(project)
            app.current_briefs = briefs
            if ui.get('was_generating'):
                for brief in briefs:
                    if brief.generation_status in ('正在生成', '文案校验修正中'):
                        brief.generation_status = '上次未完成 / 请手动重试'
                    if brief.image_generation_status.startswith('正在') or brief.image_generation_status == 'AI图就绪 / 正在排版质检':
                        brief.image_generation_status = '上次未完成 / 已有图片保留'
            language = ui.get('review_language', 'de')
            app.current_review_language = language if language in LANGUAGES else 'de'
            app._refresh_review_tree()
            app._refresh_image_review_tree(ui.get('image_code', ''))
            for tree, code, callback in (
                (app.review_tree, ui.get('review_code'), app._on_review_select),
                (app.confirm_tree, ui.get('prompt_code'), app._on_confirm_select),
                (app.creative_workspace.tree, ui.get('creative_id'), app.creative_workspace.select),
            ):
                if code and tree.exists(code):
                    tree.selection_set(code)
                    callback()
            for code in ui.get('selected_modules', []):
                if app.module_selected_tree.exists(code):
                    app.module_selected_tree.selection_add(code)
            index = ui.get('variant_index')
            if isinstance(index, int) and 0 <= index < len(app.variants):
                app.variant_tree.selection_set(str(index))
                app._load_variant_editor()
            app.variant_editor_loading = True
            try:
                for key, value in ui.get('variant_editor', {}).items():
                    if key in app.variant_vars:
                        app.variant_vars[key].set(value)
            finally:
                app.variant_editor_loading = False
            for name, value in ui.get('variables', {}).items():
                if name in RAW_VARIABLES:
                    getattr(app, name).set(value)
            app._refresh_language_compare()
            for name, value in ui.get('scalar_values', {}).items():
                if name in app.scalar_vars:
                    app.scalar_vars[name].set(value)
            for name, value in ui.get('texts', {}).items():
                if name in RAW_TEXTS:
                    widget = getattr(app, name)
                    editable = str(widget.cget('state')) != 'disabled'
                    app._set_text(widget, value, editable=editable)
            for name, value in ui.get('product_texts', {}).items():
                if name in app.text_widgets:
                    app._set_text(app.text_widgets[name], value, editable=True)
            for name, value in ui.get('creative_editor', {}).items():
                if name in ('focus_var', 'visual_var', 'layout_var', 'angle_var', 'evidence_var'):
                    getattr(app.creative_workspace, name).set(value)
            for event in ui.get('creative_results', {}).values():
                app.creative_workspace.update_progress(event)
            if ui.get('creative_progress'):
                app.creative_workspace.progress_var.set(ui['creative_progress'])
            if ui.get('was_generating'):
                app.creative_workspace.progress_var.set('上次任务未正常结束：已完成结果已恢复，未自动发起重试。')
            app.creative_workspace.progress['value'] = ui.get('creative_percent', 0)
            for kind, values in ui.get('model_lists', {}).items():
                if kind in app.ai_model_combos:
                    app.ai_model_combos[kind].configure(values=values)
            page_key = ui.get('page_key')
            if page_key not in app.pages:
                legacy = ('product', 'market', 'modules', 'modules', 'copy', 'api', 'batch', 'image')
                index = ui.get('tab', 5)
                page_key = legacy[index] if isinstance(index, int) and 0 <= index < len(legacy) else 'api'
            app.tabs.select(app.pages[page_key])
            for notebook, key in ((app.copy_workspace_tabs, 'copy_tab'), (app.export_workspace_tabs, 'export_tab')):
                index = ui.get(key, 0)
                if key == 'copy_tab':
                    name = ui.get('copy_page_key')
                    if not name and ui.get('ui_version', 0) < 32:
                        legacy = ('review', 'plan', 'api', 'context', 'plan')
                        name = legacy[index] if isinstance(index, int) and 0 <= index < len(legacy) else 'context'
                    if name in app.copy_pages:
                        notebook.select(app.copy_pages[name])
                        continue
                if key == 'export_tab' and index == 3 and ui.get('ui_version', 0) < 31:
                    index = 0  # The old per-image Prompt tab now lives in image review.
                if isinstance(index, int) and 0 <= index < len(notebook.tabs()):
                    notebook.select(index)
            output = snapshot.get('output', {})
            app.last_output_dir = output.get('directory', '')
            app.last_result = output.get('result', {})
            if app.last_output_dir and Path(app.last_output_dir).is_dir():
                app.open_output_button.configure(state='normal')
            app._display_image_preview()
            app._show_poster()
            self.profile_id = snapshot.get('profile_id', '')
            self.name = snapshot.get('name', '')
            self.refresh()
            app._refresh_brand_preview()
            app._apply_theme()
            app._planning_selected(app.creative_workspace.current_id)
            app._languages_changed()
            if hasattr(app, '_restore_direct_ui'):
                app._restore_direct_ui(ui)
            if hasattr(app, 'input_undo'):
                app.input_undo.reset()
        finally:
            self.loading = False

    def flush(self, force=False, commit=False):
        if self.loading or not self.enabled:
            return False
        try:
            snapshot = self.capture(commit=commit)
            fingerprint = self._fingerprint(snapshot)
            if force or fingerprint != self.last_snapshot:
                timestamp = self.store.save(snapshot, self.profile_id, self.name)
                self.last_snapshot = fingerprint
                self.state.set(f'已自动保存 {timestamp[11:19]}' if not self.store.warnings else self.store.warnings[0])
            return True
        except (ValueError, OSError, TypeError, tk.TclError) as exc:
            self.state.set(f'保存失败：{exc}')
            return False

    def schedule(self):
        self.timer = self.app.root.after(2000, self.tick)

    def tick(self):
        # Workers own separate briefs; only immutable snapshots reach Tk. It is
        # now safe to save already received modules while later requests run.
        self.flush()
        self.schedule()

    def can_switch(self):
        if self.app.busy:
            messagebox.showinfo('正在生成', '请等当前任务完成后再切换配置，避免结果写入其他项目。', parent=self.app.root)
            return False
        return True

    def save_current(self):
        if not self.can_switch():
            return
        if not self.profile_id:
            self.save_as()
        else:
            name = self.name_var.get().strip()
            if not name:
                messagebox.showinfo('请输入配置名称', '请在配置名称输入框中填写名称。', parent=self.app.root)
                self.name_entry.focus_set()
                return
            previous = self.name
            self.name = name
            if not self.flush(force=True, commit=True):
                self.name = previous
                messagebox.showerror('保存失败', self.state.get(), parent=self.app.root)
            self.refresh()

    def save_as(self):
        if not self.can_switch():
            return
        name = self.name_var.get().strip()
        if not name or not name.strip():
            self.state.set('请先填写配置名称，再点击保存或另存为。')
            self.name_entry.focus_set()
            return
        try:
            snapshot = self.capture(commit=True)
            self.profile_id = self.store.save_as(snapshot, name)
            self.name = name.strip()
            self.enabled = True
            self.last_snapshot = self._fingerprint(snapshot)
            self.refresh()
            self.state.set('已保存配置；后续修改会自动保存到此配置')
        except (ValueError, OSError) as exc:
            messagebox.showerror('保存失败', str(exc), parent=self.app.root)

    def select_profile(self, _event=None):
        item = self.choices.get(self.selected.get())
        if not item or item['id'] == self.profile_id:
            return
        if not self.can_switch() or not self.flush(force=True):
            self.refresh()
            return
        try:
            if not self.profile_id:
                self.store.save_as(self.capture(), '未命名草稿-' + self.app.scalar_vars['project_name'].get())
            snapshot = self.store.load(item['id'])
            self.restore(snapshot)
            self.flush(force=True)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            self.refresh()
            messagebox.showerror('配置加载失败', str(exc), parent=self.app.root)

    def prepare_new(self):
        if not self.can_switch() or not self.flush(force=True):
            return False
        # An unnamed draft also remains selectable after New / Import / Switch.
        if not self.profile_id:
            self.profile_id = self.store.save_as(self.capture(), '未命名草稿-' + self.app.scalar_vars['project_name'].get())
        self.profile_id = ''
        self.name = ''
        self.last_snapshot = ''
        self.refresh()
        return True

    def close(self):
        app = self.app
        for kind in ('text', 'image'):
            if not app._remember_provider_key(kind):
                return
        if app.busy or app.batch_queue.is_running:
            if not messagebox.askyesno('任务仍在进行', '关闭会中断当前任务，尚未返回的结果可能丢失。确定保存已有内容并退出吗？', parent=app.root):
                return
        if not self.flush(force=True, commit=True):
            messagebox.showerror('未退出：保存失败', self.state.get() + '\n请先另存为配置或导出项目。', parent=app.root)
            return
        app.batch_queue.stop()
        if self.timer:
            app.root.after_cancel(self.timer)
        app.root.destroy()
