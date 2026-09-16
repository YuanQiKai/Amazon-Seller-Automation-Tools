from copy import deepcopy
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from amazon_image_brief.ai_client import OpenAIClient
from amazon_image_brief.group_preview import preview_groups, preview_ratio
from amazon_image_brief.models import GenerationOptions, ProductProject, ModuleInstance
from amazon_image_brief.providers import TEXT_PROVIDER_PRESETS, IMAGE_PROVIDER_PRESETS, _load_configuration, provider_base_url
from amazon_image_brief.navigation import NAV_CODE
import test_v40
import test_v302


def pump(root, condition, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return True
        time.sleep(.015)
    return False


def modules():
    return [ModuleInstance('second', 'MAIN_SCENE', '主图'),
            ModuleInstance('first', 'MAIN_DETAIL', '主图'),
            ModuleInstance('dual', 'APLUS_DUAL_IMAGE_TEXT', '高级A+'),
            ModuleInstance('nav2', NAV_CODE, '高级A+', parent_id='nav', frame_index=2, frame_count=2),
            ModuleInstance('nav1', NAV_CODE, '高级A+', parent_id='nav', frame_index=1, frame_count=2),
            ModuleInstance('repeat', NAV_CODE, '高级A+', parent_id='another', frame_index=1, frame_count=1),
            ModuleInstance('dual2', 'APLUS_DUAL_IMAGE_TEXT', '高级A+')]


class PreviewModelTests(unittest.TestCase):
    def test_user_order_placeholders_and_language_isolation(self):
        results = {'second': {'direct_results': {'de': {'final_image': 'only-de.jpg'}}}}
        groups = preview_groups(modules(), results, 'fr', '主图')
        self.assertEqual(['second', 'first'], [g.frames[0].instance_id for g in groups])
        self.assertTrue(all(not g.frames[0].path for g in groups))
        self.assertEqual([1, 2], [g.frames[0].order for g in groups])

    def test_navigation_grouping_repeated_instances_and_order(self):
        groups = preview_groups(modules(), {}, 'de', '高级A+')
        self.assertEqual(4, len(groups))
        self.assertEqual(['nav2', 'nav1'], [f.instance_id for f in groups[1].frames])
        self.assertEqual('repeat', groups[2].frames[0].instance_id)
        self.assertEqual('dual2', groups[3].frames[0].instance_id)

    def test_no_mutation_and_no_draft_reference_images(self):
        recipes = {'first': {'direct_reference_images': [{'asset_path':'reference.jpg'}], 'ai_effect_image':'legacy.jpg'}}
        before = deepcopy(recipes)
        self.assertEqual('', preview_groups(modules(), recipes, 'de', '主图')[1].frames[0].path)
        self.assertEqual(before, recipes)

    def test_mobile_and_multi_slot_ratios_are_sensible(self):
        self.assertEqual(1, preview_ratio('MAIN_DETAIL', 'PC端'))
        self.assertEqual(1, preview_ratio('MAIN_DETAIL', '移动端'))
        for code in ('APLUS_DUAL_IMAGE_TEXT', 'APLUS_FOUR_IMAGE_TEXT', NAV_CODE, 'APLUS_TEXT'):
            self.assertAlmostEqual(1464/600, preview_ratio(code, 'PC端'))
            self.assertAlmostEqual(4/3, preview_ratio(code, '移动端'))


class RouteTests(unittest.TestCase):
    def options(self, route):
        return GenerationOptions(text_provider='9527code', image_provider='9527code',
            text_model='account-text-model', image_model='account-image-model', cache_enabled=False, api_debug_enabled=False,
            provider_routes={'text:9527code': route, 'image:9527code': route})

    def test_both_presets_and_three_routes_no_fabricated_models(self):
        for presets in (TEXT_PROVIDER_PRESETS, IMAGE_PROVIDER_PRESETS):
            preset = presets['9527code']
            self.assertEqual(3, len(preset.routes))
            self.assertEqual((), preset.models)
            self.assertEqual('CODE9527_API_KEY', preset.api_key_env)

    def test_each_route_reaches_models_text_and_image_request(self):
        picture = io.BytesIO()
        Image.new('RGB', (32,32), 'blue').save(picture, format='JPEG')
        with tempfile.TemporaryDirectory() as folder:
            for route, url in TEXT_PROVIDER_PRESETS['9527code'].routes:
                client = OpenAIClient.from_options(self.options(route), text_api_key='unit-key', image_api_key='unit-key')
                with patch.object(client, '_get_json', return_value={'data':[{'id':'model-a'},{'id':'model-b'}]}) as get:
                    for kind in ('text', 'image'):
                        self.assertEqual(['model-a','model-b'], client.list_models(kind))
                        self.assertEqual(url+'/models', get.call_args.args[0])
                with patch.object(client, '_post_json', return_value={'choices':[{'message':{'content':'OK'}}]}) as post, \
                     patch.object(client, 'list_models', return_value=['account-text-model']):
                    client.test_connection('text')
                    self.assertEqual((url, '/chat/completions'), post.call_args.args[:2])
                with patch.object(client, '_post_json', return_value={'data':[{'b64_json':base64.b64encode(picture.getvalue()).decode()}]}) as post:
                    client.generate_image('Use this prompt verbatim', Path(folder)/(route+'.jpg'), model='account-image-model')
                    self.assertEqual((url, '/images/generations'), post.call_args.args[:2])
                    self.assertEqual('Use this prompt verbatim', post.call_args.args[2]['prompt'])

    def test_fallback_routes_and_independent_text_image(self):
        opts = self.options('路线2')
        opts.provider_routes['image:9527code'] = '路线3'
        opts.text_provider = opts.image_provider = 'cunai'
        opts.auto_failover = True
        opts.text_fallback_provider = opts.image_fallback_provider = '9527code'
        opts.text_fallback_model, opts.image_fallback_model = 'fallback-text', 'fallback-image'
        client = OpenAIClient.from_options(opts)
        self.assertEqual('https://api.9527.codes/v1', client.text_fallbacks[0].options.text_base_url)
        self.assertEqual('https://cdn.9527.codes/v1', client.image_fallbacks[0].options.image_base_url)
        self.assertEqual('fallback-image', client.image_fallbacks[0].options.image_model)

    def test_gpt_edit_request_preserves_both_inputs_and_route_in_debug_events(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            refs = [root/'product.png', root/'style.png']
            for path in refs:
                Image.new('RGB', (40,40), 'blue').save(path)
            for route, url in IMAGE_PROVIDER_PRESETS['9527code'].routes:
                events = []
                opts = self.options(route)
                opts.image_model = 'gpt-image-2'
                client = OpenAIClient.from_options(opts, image_api_key='private-unit-key', on_api_event=events.append)
                with patch('urllib.request.urlopen', return_value=test_v302.ImageResponse()) as network:
                    client.generate_image('Use P01 shape, R01 style', root/(route+'.jpg'), reference_images=refs)
                request = network.call_args.args[0]
                self.assertEqual(url+'/images/edits', request.full_url)
                self.assertEqual(2, request.data.count(b'name="image[]"'))
                self.assertIn(b'Use P01 shape, R01 style', request.data)
                self.assertNotIn('private-unit-key', json.dumps(events))
                self.assertIn(url+'/images/edits', json.dumps(events))

    def test_untrusted_profile_route_cannot_redirect_key(self):
        client = OpenAIClient.from_options(self.options('https://invalid.example/steal'))
        self.assertEqual('https://9527.codes/v1', client.options.image_base_url)
        self.assertEqual('https://9527.codes/v1', provider_base_url(IMAGE_PROVIDER_PRESETS['9527code'], 'image', {}))

    def test_configuration_override_is_preserved_and_old_profiles_migrate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = {'schema_version':1, 'text':{'9527code':{'local':'preserved'}}, 'image':{'custom':{'local':'preserved'}}}
            extra = {'schema_version':1, 'text':{'9527code':{'local':'new'}}, 'image':{'9527code':{'new':True}}}
            (root/'ai_providers.json').write_text(json.dumps(base), encoding='utf-8')
            (root/'ai_provider_additions.json').write_text(json.dumps(extra), encoding='utf-8')
            merged = _load_configuration(root/'ai_providers.json')
            self.assertEqual({'local':'preserved'}, merged['text']['9527code'])
            self.assertTrue(merged['image']['9527code']['new'])
            self.assertEqual(base, json.loads((root/'ai_providers.json').read_text(encoding='utf-8')))
        self.assertEqual({}, ProductProject.from_dict({}).options.provider_routes)
        project = ProductProject(options=self.options('路线3'))
        self.assertEqual(project.options.provider_routes, ProductProject.from_dict(project.to_dict()).options.provider_routes)


class PreviewDesktopTests(unittest.TestCase):
    setUp = test_v40.DirectDesktopTests.setUp
    tearDown = test_v40.DirectDesktopTests.tearDown

    def open(self, aplus=False):
        self.app.open_group_preview()
        viewer = self.app.group_preview
        viewer.attributes('-alpha', 0)
        if aplus:
            viewer.channel.set('高级A+')
            viewer._change_view()
        self.assertTrue(pump(self.root, lambda: bool(viewer.cards) and hasattr(viewer.cards[0], 'size')))
        return viewer

    def put_aplus(self):
        project = test_v40.sample(self.folder)
        project.module_instances = modules()
        project.module_order = [i.instance_id for i in project.module_instances]
        for instance in project.module_instances:
            project.module_recipes.setdefault(instance.instance_id, {'direct_results':{}})
        self.app.populate_project(project)

    def test_entry_single_window_preview_does_not_change_editor(self):
        with patch.object(OpenAIClient, 'generate_image', side_effect=AssertionError('No API calls')):
            viewer = self.open()
            card = viewer.cards[0]
            self.assertEqual('m1', card.current.instance_id)
            card.step(1)
            self.assertEqual('m2', card.current.instance_id)
            self.assertEqual('m1', self.app.direct_current_id)
            self.app.open_group_preview()
            self.assertIs(viewer, self.app.group_preview)
            self.assertEqual('m2', card.current.instance_id)
            card.step(1)
            self.assertEqual('m1', card.current.instance_id)

    def test_each_return_updates_existing_card_without_reset_selection(self):
        viewer = self.open()
        card = viewer.cards[0]
        card.step(1)
        path = self.folder/'result.jpg'
        Image.new('RGB', (900,900), 'red').save(path)
        self.app.busy = True
        self.app.follow_image_var.set(False)
        self.app._receive_direct_image(dict(id='m2', language='de', done=1, total=2, output_dir=str(self.folder),
            result=dict(status='已完成', final_image=str(path), version=1)))
        self.assertIs(card, viewer.cards[0])
        self.assertEqual('m2', card.current.instance_id)
        self.assertTrue(pump(self.root, lambda: card.photo is not None))
        self.assertEqual('m1', self.app.direct_current_id)
        self.assertIn('1/2', viewer.count.get())

    def test_aplus_sequence_navigation_and_live_scroll_preservation(self):
        self.put_aplus()
        viewer = self.open(aplus=True)
        self.assertEqual(4, len(viewer.cards))
        nav = viewer.cards[1]
        self.assertEqual('nav2', nav.current.instance_id)
        nav.step(1)
        self.assertEqual('nav1', nav.current.instance_id)
        self.root.update()
        viewer.scroll_canvas.yview_moveto(.45)
        pump(self.root, lambda: viewer.scroll_canvas.yview()[0] > .1)
        before = viewer.scroll_canvas.yview()[0]
        viewer.refresh()
        self.root.update()
        self.assertAlmostEqual(before, viewer.scroll_canvas.yview()[0], places=2)
        self.assertIs(nav, viewer.cards[1])
        self.assertEqual('nav1', nav.current.instance_id)

    def test_mobile_switch_language_missing_results_and_no_cropping(self):
        self.put_aplus()
        viewer = self.open(aplus=True)
        pc_width = viewer.cards[0].size[0]
        viewer.device.set('移动端')
        viewer._change_device()
        self.assertTrue(pump(self.root, lambda: viewer.cards[0].size[0] == 390))
        self.assertGreater(pc_width, 390)
        self.assertAlmostEqual(4/3, viewer.cards[0].size[0]/viewer.cards[0].size[1], delta=.01)
        self.app.language_vars['fr'].set(True)
        self.app._direct_languages_changed()
        viewer.language.set('法语')
        viewer.refresh()
        self.assertTrue(all(not card.current.path for card in viewer.cards))

    def test_closed_view_cancels_timers_images_and_reopens(self):
        viewer = self.open()
        viewer.autoplay.set(True)
        viewer._toggle_play()
        timer = viewer._play_after
        viewer.destroy()
        self.assertTrue(viewer._closed)
        self.assertNotIn(timer, self.root.tk.call('after', 'info'))
        self.app._refresh_group_preview()
        new = self.open()
        self.assertIsNot(viewer, new)

    def test_reorder_empty_channel_and_autoplay(self):
        viewer = self.open()
        card = viewer.cards[0]
        viewer.autoplay.set(True)
        viewer._toggle_play()
        self.root.after_cancel(viewer._play_after)
        viewer._play()
        self.assertEqual('m2', card.current.instance_id)
        self.app.module_instances.reverse()
        self.app.module_order.reverse()
        viewer.refresh()
        self.assertEqual(['m2','m1'], [f.instance_id for f in viewer.cards[0].group.frames])
        self.assertEqual('m2', viewer.cards[0].current.instance_id)
        viewer.channel.set('高级A+')
        viewer._change_view()
        self.assertEqual([], viewer.cards)
        self.assertIsNone(viewer._play_after)
        self.assertFalse(viewer.autoplay.get())

    def test_missing_file_does_not_block_other_images_or_refresh(self):
        self.app.module_recipes['m1']['direct_results']['de'] = {'final_image':str(self.folder/'missing.jpg'), 'version':1}
        self.app.module_recipes['m2']['direct_results']['de'] = {'final_image':str(self.folder/'product.png'), 'version':1}
        viewer = self.open()
        card = viewer.cards[0]
        def errored():
            return any('无法预览' in card.image_canvas.itemcget(item, 'text') for item in card.image_canvas.find_all()
                       if card.image_canvas.type(item) == 'text')
        self.assertTrue(pump(self.root, errored))
        card.step(1)
        self.assertTrue(pump(self.root, lambda: card.photo is not None))
        self.assertEqual('m2', card.current.instance_id)

    def test_route_change_invalidates_old_model_responses(self):
        app = self.app
        app.ai_config_vars['image']['provider'].set(IMAGE_PROVIDER_PRESETS['9527code'].label)
        app.apply_provider_preset('image')
        app.ai_key_vars['image'].set('unit-route-key')
        old_id = app._model_request_ids.get(('image', False), 0)
        app.provider_route_widgets[('image', False)][1].set('路线2')
        app._change_provider_route('image')
        app._model_events.put(('models', (('image', False), old_id, '9527code', ['old-route-model'], '')))
        app._poll_model_events()
        self.assertNotEqual('old-route-model', app.ai_config_vars['image']['model'].get())
        self.assertEqual('https://api.9527.codes/v1', app._build_ai_client(app.collect_project(sync_modules=False).options).options.image_base_url)
        current_model = app.ai_config_vars['image']['model'].get()
        old_result = {'_connection_target': ('9527code', 'https://9527.codes/v1'), 'data': ['wrong-manual-route']}
        app._handle_operation_done('models:image', old_result)
        self.assertEqual(current_model, app.ai_config_vars['image']['model'].get())

    def test_large_aplus_only_requests_visible_images(self):
        project = test_v40.sample(self.folder)
        project.module_instances = [ModuleInstance(f'a{i}', 'APLUS_FULL_IMAGE', '高级A+') for i in range(50)]
        project.module_order = [i.instance_id for i in project.module_instances]
        for instance in project.module_instances:
            project.module_recipes[instance.instance_id] = {'direct_results':{'de': {'final_image':str(self.folder/'product.png'), 'version':1}}}
        self.app.populate_project(project)
        viewer = self.open(aplus=True)
        self.assertTrue(pump(self.root, lambda: viewer.cards[0].photo is not None))
        self.assertLessEqual(sum(card.photo is not None for card in viewer.cards), 5)
        viewer.scroll_canvas.yview_moveto(1)
        self.assertTrue(pump(self.root, lambda: viewer.cards[-1].photo is not None))
        self.assertIsNone(viewer.cards[0].photo)

    def test_preview_controls_fit_small_window_and_caption_does_not_accumulate(self):
        from amazon_image_brief.ui_themes import THEMES
        self.app.module_recipes['m1']['direct_results']['de'] = {'final_image':str(self.folder/'product.png'), 'version':1}
        viewer = self.open()
        viewer.geometry('800x600')
        card = viewer.cards[0]
        for theme in THEMES:
            self.app.theme_var.set(theme)
            self.app._apply_theme()
            viewer.force_refresh()
            self.assertTrue(pump(self.root, lambda: card.photo is not None))
            edge = viewer.winfo_rootx()+viewer.winfo_width()
            for widget in (viewer.language_combo, viewer.play_button, card.combo):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), edge)
            self.assertEqual(1, card.caption.cget('text').count('原图'))

    def test_provider_route_ui_saved_with_profiles_and_key_unchanged(self):
        app = self.app
        for kind in ('text', 'image'):
            app.ai_config_vars[kind]['provider'].set(app._provider_presets(kind)['9527code'].label)
            app.apply_provider_preset(kind)
            app.ai_key_vars[kind].set('unit-route-key')
            app.provider_route_widgets[(kind, False)][1].set('路线3')
            app._change_provider_route(kind)
            self.assertEqual('unit-route-key', app.ai_key_vars[kind].get())
            self.assertEqual('https://cdn.9527.codes/v1', app.ai_config_vars[kind]['base_url'].get())
            getattr(app, f'{kind}_fallback_provider_var').set('9527code')
            app._apply_fallback_provider(kind)
            self.assertEqual('路线3', app.provider_route_widgets[(kind, True)][1].get())
        snapshot = app.profiles.capture()
        self.assertNotIn('unit-route-key', json.dumps(snapshot))
        app.provider_routes.clear()
        app.profiles.restore(snapshot)
        for kind in ('text', 'image'):
            self.assertEqual('路线3', app.provider_route_widgets[(kind, False)][1].get())
            self.assertEqual('https://cdn.9527.codes/v1', app.ai_config_vars[kind]['base_url'].get())


if __name__ == '__main__':
    unittest.main()
