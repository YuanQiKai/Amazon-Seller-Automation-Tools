from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from PIL import Image

from amazon_image_brief.ai_client import OpenAIClient
from amazon_image_brief.brief_generator import BriefGenerator
from amazon_image_brief.gui import AmazonImageBriefApp, ScrollFrame
from amazon_image_brief.models import GenerationOptions, LANGUAGES, ProductProject
from amazon_image_brief.mousewheel import MouseWheelRouter
from amazon_image_brief.profile_store import ProfileStore
from amazon_image_brief.providers import TEXT_PROVIDER_PRESETS, IMAGE_PROVIDER_PRESETS


class ProviderTests(unittest.TestCase):
    def test_sudocode_models_chat_and_image_use_same_key_and_v1(self):
        with tempfile.TemporaryDirectory() as temp:
            options = GenerationOptions(text_provider='sudocode', image_provider='sudocode',
                                        api_debug_enabled=False, cache_enabled=False)
            client = OpenAIClient.from_options(options, text_api_key='test-only', image_api_key='test-only', cache_dir=Path(temp))
            for presets in (TEXT_PROVIDER_PRESETS, IMAGE_PROVIDER_PRESETS):
                self.assertEqual('SUDOCODE_API_KEY', presets['sudocode'].api_key_env)
                self.assertEqual('https://api.sudocode.chat/v1', presets['sudocode'].base_url)
                self.assertEqual((), presets['sudocode'].models)
            with patch.object(client, '_get_json', return_value={'data': [{'id': 'chat-model'}, {'id': 'image-model'}]}) as request:
                for kind in ('text', 'image'):
                    self.assertEqual(['chat-model', 'image-model'], client.list_models(kind))
                    self.assertEqual('https://api.sudocode.chat/v1/models', request.call_args.args[0])
                    self.assertEqual('test-only', request.call_args.args[1])
            with patch.object(client, '_post_json', return_value={'choices': [{'message': {'content': '{"ok": true}'}}]}) as request:
                self.assertEqual({'ok': True}, client.generate_json('test prompt', 'chat-model'))
                args = request.call_args.args
                self.assertEqual(('https://api.sudocode.chat/v1', '/chat/completions'), args[:2])
                self.assertEqual('chat-model', args[2]['model'])
                self.assertEqual('test-only', args[3])
            buffer = BytesIO()
            Image.new('RGB', (40, 40), 'white').save(buffer, 'PNG')
            response = {'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}]}
            with patch.object(client, '_post_json', return_value=response) as request:
                output = client.generate_image('reference layout', Path(temp)/'out.jpg', 'image-model')
                args = request.call_args.args
                self.assertEqual(('https://api.sudocode.chat/v1', '/images/generations'), args[:2])
                self.assertEqual('test-only', args[3])
                with Image.open(output) as image:
                    self.assertEqual('JPEG', image.format)


class StoreTests(unittest.TestCase):
    def test_assets_are_managed_deduplicated_and_portable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'产品.png'
            Image.new('RGB', (30, 30), 'red').save(source)
            snapshot = {'project': ProductProject(product_image_paths=[str(source)], competitor_image_paths=[str(source)], logo_image_path=str(source)).to_dict(),
                        'briefs': [{'ai_effect_image': str(source), 'image_history': [{'german_composite_image': str(source)}]}]}
            store = ProfileStore(root/'data')
            profile_id = store.save_as(snapshot, '旅行箱')
            store.save(snapshot, profile_id, '旅行箱')
            self.assertEqual(1, len(list(store.assets.rglob('*.png'))))
            saved = json.loads(store.session_path.read_text(encoding='utf-8'))
            self.assertTrue(saved['project']['product_image_paths'][0].startswith('asset://'))
            source.unlink()  # Only removes a test fixture in TemporaryDirectory.
            # Saving another field before restarting must not replace a managed
            # reference with a now-missing original path.
            snapshot['project']['brand'] = 'updated after moving original'
            store.save(snapshot, profile_id, '旅行箱')
            self.assertTrue(store.load(profile_id)['project']['product_image_paths'][0].endswith('产品.png'))
            shutil.copytree(store.directory, root/'moved')
            restored = ProfileStore(root/'moved').load(profile_id)
            self.assertTrue(Path(restored['project']['product_image_paths'][0]).is_file())
            self.assertTrue(Path(restored['briefs'][0]['image_history'][0]['german_composite_image']).is_file())
            self.assertTrue(Path(restored['project']['logo_image_path']).is_relative_to(root/'moved'))

    def test_previous_revision_recovers_corruption_and_missing_paths_warn(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProfileStore(Path(temp)/'data')
            data = {'project': ProductProject(brand='first').to_dict()}
            store.save(data)
            data['project']['brand'] = 'second'
            store.save(data)
            store.session_path.write_text('{broken', encoding='utf-8')
            self.assertEqual('first', store.load()['project']['brand'])
            self.assertTrue(store.warnings)
            data['project']['product_image_paths'] = [str(Path(temp)/'missing.png')]
            store.save(data)
            self.assertTrue(store.warnings)
            self.assertEqual('second', store.load()['project']['brand'])

    def test_invalid_ids_and_asset_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProfileStore(Path(temp))
            with self.assertRaises(ValueError):
                store.load('../outside')
            with self.assertRaises(ValueError):
                store._restore_asset('asset://../../outside.png')


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.start_app()

    def start_app(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = AmazonImageBriefApp(self.root, self.directory)
        self.root.update_idletasks()

    def stop_app(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()

    def tearDown(self):
        self.stop_app()
        self.temp.cleanup()

    def test_profile_relaunch_restores_all_sections_and_never_serializes_keys(self):
        app = self.app
        app.scalar_vars['brand'].set('Profile A')
        app.scalar_vars['brand_colors'].set('#123456')
        app.text_widgets['selling_points'].insert('1.0', '轮组顺滑\n分层收纳\n轻量箱体')
        app.competitors_text.insert('1.0', 'ASIN1 | https://example.test | Brand | 90 | wheels | scene | gap\n')
        app.keywords_text.insert('1.0', 'Koffer | de | feature | 4 | chosen\n')
        app.variant_tree.selection_set('1')
        app._load_variant_editor()
        app.variant_vars['color'].set('Blue')
        app.variant_vars['weight'].set('2.5kg')
        original = self.directory/'reference.png'
        Image.new('RGB', (80, 80), 'blue').save(original)
        app.product_images = [str(original)]
        app.competitor_images = [str(original)]
        app.logo_image_path = str(original)
        app._refresh_image_labels()
        app._load_ai_options(GenerationOptions(text_provider='sudocode', image_provider='sudocode', text_model='custom-chat', image_model='custom-image',
                            text_fallback_provider='sudocode', text_fallback_model='live-chat', image_fallback_provider='sudocode', image_fallback_model='live-image'))
        app.ai_key_vars['text'].set('never-store-real-key')
        app.ai_key_vars['image'].set('never-store-image-key')
        project = app.collect_project()
        project.options.optimize_copy_with_ai = False
        app.current_briefs = BriefGenerator().generate(project)
        app._refresh_review_tree()
        app._refresh_image_review_tree()
        brief = app.current_briefs[1]
        brief.copy = {lang: f'{lang} original' for lang in LANGUAGES}
        brief.ai_effect_image = str(original)
        brief.german_composite_image = str(original)
        app.review_tree.selection_set(brief.instance_id)
        app._on_review_select()
        app._set_text(app.current_copy_text, 'Deutsch edited but not committed', editable=True)
        app.review_notes_var.set('保留四轮')
        app.image_review_tree.selection_set(brief.instance_id)
        app._on_image_review_select()
        app.image_review_status_var.set('不满意-待修改')
        app.image_revision_text.insert('1.0', '背景换成机场')
        app.confirm_tree.selection_set(brief.instance_id)
        app._on_confirm_select()
        app.module_prompt_text.insert('1.0', 'blue scene layout')
        app.global_revision_var.set('更简洁')
        app.max_retries_var.set('-')  # A partially typed field must not prevent saving.
        app.tabs.select(app.pages['image'])
        self.assertTrue(app.profiles.flush(force=True))
        document = app.profiles.store.session_path.read_text(encoding='utf-8')
        self.assertNotIn('never-store-', document)
        self.assertEqual('-', app.max_retries_var.get())
        original.unlink()
        self.stop_app()
        self.start_app()
        restored = self.app
        self.assertEqual('Profile A', restored.scalar_vars['brand'].get())
        self.assertEqual('#123456', restored.scalar_vars['brand_colors'].get())
        self.assertEqual('Blue', restored.variants[1].color)
        self.assertIn('Koffer', restored.keywords_text.get('1.0', 'end'))
        self.assertTrue(Path(restored.product_images[0]).is_file())
        self.assertTrue(Path(restored.competitor_images[0]).is_file())
        self.assertTrue(Path(restored.logo_image_path).is_file())
        self.assertEqual('sudocode', restored._provider_id('image'))
        self.assertEqual('custom-chat', restored.text_model_var.get())
        self.assertEqual('live-image', restored.image_fallback_model_var.get())
        self.assertEqual('blue scene layout', restored.module_prompt_text.get('1.0', 'end-1c'))
        self.assertEqual('Deutsch edited but not committed', restored.current_copy_text.get('1.0', 'end-1c'))
        self.assertEqual('保留四轮', restored.review_notes_var.get())
        self.assertEqual('背景换成机场', restored.image_revision_text.get('1.0', 'end-1c'))
        self.assertEqual('不满意-待修改', restored.image_review_status_var.get())
        self.assertEqual(restored.pages['image'], restored.tabs.select())
        self.assertEqual('-', restored.max_retries_var.get())
        self.assertEqual('更简洁', restored.global_revision_var.get())
        self.assertTrue(Path(restored.current_briefs[1].ai_effect_image).is_file())

    def test_switching_profiles_does_not_leak_editors_or_clear_named_data(self):
        app = self.app
        app.scalar_vars['brand'].set('A')
        app.variant_tree.selection_set('0')
        app._load_variant_editor()
        app.variant_vars['color'].set('Red')
        first_module = app.module_instances[1].instance_id
        app.confirm_tree.selection_set(first_module)
        app._on_confirm_select()
        app.module_prompt_text.insert('1.0', 'A-only prompt')
        with patch('amazon_image_brief.profile_workspace.simpledialog.askstring', return_value='配置 A'):
            app.profiles.name_var.set('配置 A')
            app.profiles.save_as()
        a_id = app.profiles.profile_id
        app.new_project()
        app.scalar_vars['brand'].set('B')
        app.variant_tree.selection_set('0')
        app._load_variant_editor()
        app.variant_vars['color'].set('Green')
        with patch('amazon_image_brief.profile_workspace.simpledialog.askstring', return_value='配置 B'):
            app.profiles.name_var.set('配置 B')
            app.profiles.save_as()
        b_id = app.profiles.profile_id
        a_label = next(key for key, value in app.profiles.choices.items() if value['id'] == a_id)
        app.profiles.selected.set(a_label)
        app.profiles.select_profile()
        self.root.update_idletasks()
        self.assertEqual('A', app.scalar_vars['brand'].get())
        self.assertEqual('Red', app.variants[0].color)
        self.assertEqual('A-only prompt', app.module_prompts[first_module])
        app.variant_vars['color'].set('Black')
        self.assertTrue(app.profiles.flush(force=True))
        self.assertEqual('Green', app.profiles.store.load(b_id)['project']['variants'][0]['color'])
        self.assertEqual('Black', app.profiles.store.load(a_id)['project']['variants'][0]['color'])
        self.assertEqual(2, len(app.profiles.store.list_profiles()))

    def test_module_tab_multiselect_move_and_drag_sync_and_white_stays_first(self):
        app = self.app
        tree = app.module_selected_tree
        order = list(app.module_order)
        selected = order[2:4]
        tree.selection_set(selected)
        app.move_module(-1, tree)
        self.assertEqual([order[0], *selected, order[1], *order[4:]], app.module_order)
        self.assertEqual(tuple(app.module_order), app.confirm_tree.get_children())
        self.assertEqual(tuple(app.module_order), tree.get_children())
        app.move_module(1, tree)
        self.assertEqual(order, app.module_order)
        app.tabs.select(app.pages['modules'])
        self.root.attributes('-alpha', 0)
        self.root.deiconify()
        self.root.update()
        x, y, width, height = tree.bbox(order[2])
        app._on_module_drag_start(SimpleNamespace(widget=tree, y=y+height//2, state=0))
        target_y = tree.bbox(order[5])[1] + height//2
        app._on_module_drag_motion(SimpleNamespace(widget=tree, y=target_y))
        app._on_module_drag_end(None)
        self.assertEqual(order[2], app.module_order[5])
        self.assertEqual(app.confirm_tree.get_children(), tree.get_children())
        tree.selection_set(app.module_order[1])
        app.move_module(-1, tree)
        self.assertEqual('MAIN_WHITE', app.module_instances[0].module_code)

    def test_gallery_batch_delete_and_clear_only_remove_references(self):
        app = self.app
        paths = []
        for index in range(3):
            path = self.directory/f'{index}.png'
            Image.new('RGB', (20, 20)).save(path)
            paths.append(str(path))
        app.product_images = list(paths)
        app.competitor_images = list(paths)
        app._refresh_image_labels()
        app.product_gallery.tree.selection_set(('0', '2'))
        app.product_gallery.toggle_checked('0')
        app.product_gallery.toggle_checked('2')
        app.product_gallery.remove_selected()
        self.assertEqual([paths[1]], app.product_images)
        self.assertEqual(paths, app.competitor_images)
        with patch('amazon_image_brief.asset_gallery.messagebox.askyesno', return_value=False):
            app.product_gallery.clear_all()
        self.assertEqual([paths[1]], app.product_images)
        with patch('amazon_image_brief.asset_gallery.messagebox.askyesno', return_value=True):
            app.competitor_gallery.clear_all()
        self.assertEqual([], app.competitor_images)
        self.assertTrue(all(Path(path).exists() for path in paths))
        app.product_gallery.select_all()
        self.assertEqual(('0',), app.product_gallery.tree.selection())

    def test_mousewheel_nested_text_and_combobox_route_without_changing_value(self):
        app = self.app
        self.root.attributes('-alpha', 0)
        self.root.deiconify()
        self.root.update()
        app.tabs.select(app.pages['product'])
        self.root.update()
        frame = self.root.nametowidget(app.pages['product'])
        self.assertIsInstance(frame, ScrollFrame)
        combo = next(widget for widget in app.input_undo.histories if isinstance(widget, ttk.Combobox) and str(widget.cget('textvariable')) == str(app.marketplace_var))
        value = combo.get()
        event = SimpleNamespace(widget=combo, x_root=-500, y_root=-500, delta=-120, num='??')
        before = frame.scroll_canvas.yview()[0]
        self.assertEqual('break', app.mousewheel.route(event))
        self.assertGreater(frame.scroll_canvas.yview()[0], before)
        self.assertEqual(value, combo.get())
        text = app.text_widgets['selling_points']
        text.insert('1.0', '\n'.join(str(i) for i in range(80)))
        self.root.update_idletasks()
        text.yview_moveto(0)
        before = frame.scroll_canvas.yview()[0]
        event.widget = text
        app.mousewheel.route(event)
        self.assertGreater(text.yview()[0], 0)
        self.assertEqual(before, frame.scroll_canvas.yview()[0])
        text.yview_moveto(1)
        app.mousewheel.route(event)
        self.assertGreater(frame.scroll_canvas.yview()[0], before)
        self.assertEqual(-3, MouseWheelRouter.steps(SimpleNamespace(delta=120, num='??')))
        self.assertEqual(3, MouseWheelRouter.steps(SimpleNamespace(delta=-20, num='??')))

    def test_autosave_does_not_clear_prompt_selection_or_create_copy_versions(self):
        app = self.app
        project = app.collect_project()
        project.options.optimize_copy_with_ai = False
        app.current_briefs = BriefGenerator().generate(project)
        app._refresh_review_tree()
        code = app.current_briefs[1].instance_id
        app.review_tree.selection_set(code)
        app._on_review_select()
        app._set_text(app.current_copy_text, 'Typing unfinished copy', editable=True)
        app.confirm_tree.selection_set(code)
        app._on_confirm_select()
        app.module_prompt_text.insert('1.0', 'unfinished prompt')
        app.profiles.flush(force=True)
        self.assertEqual('unfinished prompt', app.module_prompt_text.get('1.0', 'end-1c'))
        self.assertEqual(code, app.current_prompt_code)
        self.assertEqual({}, app.copy_versions)
        self.assertEqual('Typing unfinished copy', app.current_copy_text.get('1.0', 'end-1c'))

    def test_timer_saves_unsaved_draft_and_busy_switch_is_blocked(self):
        app = self.app
        app.scalar_vars['brand'].set('timer-saved')
        app.profiles.tick()
        self.assertEqual('timer-saved', app.profiles.store.load()['project']['brand'])
        app.busy = True
        with patch('amazon_image_brief.profile_workspace.messagebox.showinfo'):
            app.new_project()
        self.assertEqual('timer-saved', app.scalar_vars['brand'].get())
        app.busy = False


if __name__ == '__main__':
    unittest.main()
