from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from amazon_image_brief.ai_client import OpenAIClient, OpenAIError
from amazon_image_brief.api_debug import APIDebugLogger
from amazon_image_brief.brief_generator import BriefGenerator
from amazon_image_brief.gui import AmazonImageBriefApp
from amazon_image_brief.models import GenerationOptions
from amazon_image_brief.service import GenerationService
from test_v28 import PlannedAI, project_at


class ImageAI(PlannedAI):
    image_available = True

    def __init__(self, fail=False, before_call=None):
        super().__init__()
        self.image_calls = []
        self.fail = fail
        self.before_call = before_call

    def generate_image(self, destination, **kwargs):
        if self.before_call:
            self.before_call(len(self.image_calls))
        self.image_calls.append(dict(self.debug_context))
        if self.fail and len(self.image_calls) == 2:
            raise OpenAIError('模拟图片 HTTP 504')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (1200, 1200), 'white').save(destination, 'JPEG')
        return destination


class StreamServiceTests(unittest.TestCase):
    def test_copy_events_are_immutable_and_partial_regeneration_contains_new_copy(self):
        project = project_at('unused')
        project.options.optimize_copy_with_ai = True
        events = []
        generator = BriefGenerator(PlannedAI(), events.append)
        result = generator.generate(project)
        start = next(event for event in events if event['instance_id'] == 'detail1' and not event['finished'])
        finish = next(event for event in events if event['instance_id'] == 'detail1' and event['finished'])
        self.assertEqual('正在生成', start['brief'].generation_status)
        self.assertIn('detail1 de', finish['brief'].copy['de'])
        self.assertIsNot(finish['brief'], result[1])
        result[1].copy['de'] = 'later mutation'
        self.assertNotEqual('later mutation', finish['brief'].copy['de'])
        events.clear()
        payload = generator.regenerate_copy(project, result[1])
        self.assertEqual(payload['copy'], events[-1]['brief'].copy)
        self.assertTrue(events[-1]['finished'])

    def test_each_image_published_before_next_call_and_checkpoint_keeps_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = project_at(root/'out')
            project.options.generate_ai_images = True
            project.options.generate_german_composites = True
            source = root/'source.jpg'
            Image.new('RGB', (800, 800), 'white').save(source)
            project.product_image_paths = [str(source)]
            events = []
            def before_call(index):
                if index:
                    self.assertEqual(index, len([event for event in events if event['stage'] in ('complete', 'failed')]))
                    self.assertTrue(Path(events[-1]['output_dir'], 'partial_briefs.json').is_file())
            client = ImageAI(fail=True, before_call=before_call)
            service = GenerationService(root, client, on_image_progress=events.append)
            result = service.run(project)
            self.assertEqual(3, len(client.image_calls))
            self.assertEqual(['white', 'detail1', 'detail2'], [call['instance_id'] for call in client.image_calls])
            self.assertEqual({}, client.debug_context)
            first_ready = next(event for event in events if event['stage'] == 'ai_ready')
            self.assertTrue(Path(first_ready['brief'].ai_effect_image).is_file())
            self.assertEqual(first_ready['brief'].ai_effect_image, first_ready['brief'].german_composite_image)
            first_complete = next(event for event in events if event['stage'] == 'complete')
            self.assertTrue(Path(first_complete['brief'].german_composite_image).is_file())
            failed = next(event for event in events if event['instance_id'] == 'detail1' and event['stage'] == 'failed')
            self.assertIn('无结果图', failed['status'])
            self.assertEqual('', failed['brief'].ai_effect_image)
            self.assertIn('504', failed['error'])
            checkpoint = json.loads((Path(result['output_dir'])/'partial_briefs.json').read_text(encoding='utf-8'))
            self.assertEqual(3, len(checkpoint))
            self.assertIn('504', checkpoint[1]['image_generation_error'])
            self.assertTrue(Path(result['excel']).is_file())

    def test_failed_regeneration_publishes_error_without_replacing_old_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = project_at(root/'out')
            original = root/'product.jpg'
            Image.new('RGB', (800, 800), 'white').save(original)
            project.product_image_paths = [str(original)]
            briefs = BriefGenerator().generate(project)
            briefs[1].ai_effect_image = str(original)
            events = []
            client = ImageAI()
            service = GenerationService(root, client, on_image_progress=events.append)
            with patch.object(client, 'generate_image', side_effect=OpenAIError('HTTP 504')):
                with self.assertRaises(OpenAIError):
                    service.regenerate_image(project, briefs, 'detail1', root/'out')
            self.assertEqual('failed', events[-1]['stage'])
            self.assertEqual(str(original), events[-1]['brief'].ai_effect_image)
            self.assertEqual({}, client.debug_context)

    def test_cached_image_emits_explicit_no_http_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = []
            client = OpenAIClient(image_api_key='test-only', cache_dir=root/'cache',
                                  options=GenerationOptions(api_debug_enabled=False), on_api_event=events.append)
            def generate(_prompt, destination, *_args):
                Image.new('RGB', (50, 50), 'white').save(destination, 'JPEG')
                return destination
            with patch.object(client, '_generate_image_once', side_effect=generate) as request:
                client.generate_image('same prompt', root/'one.jpg')
                client.generate_image('same prompt', root/'two.jpg')
                self.assertEqual(1, request.call_count)
            self.assertEqual('cache_hit', events[-1]['phase'])
            self.assertIn('没有发送 HTTP', events[-1]['note'])


class LiveDesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = AmazonImageBriefApp(self.root, self.directory)
        self.project = project_at(self.directory/'out')
        self.app.populate_project(self.project)
        self.root.update_idletasks()

    def tearDown(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temp.cleanup()

    def pump_until(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertTrue(predicate(), 'UI event did not arrive before timeout')

    def test_checkbox_is_independent_of_preview_and_survives_append(self):
        app = self.app
        paths = []
        for index in range(3):
            path = self.directory/f'{index}.jpg'
            Image.new('RGB', (80, 80), 'white').save(path)
            paths.append(str(path))
        app.product_images = paths[:2]
        app.competitor_images = list(paths)
        app._refresh_image_labels()
        gallery = app.product_gallery
        gallery.tree.selection_set('0')
        gallery.preview()
        self.assertEqual(set(), gallery.checked_paths)
        with patch('amazon_image_brief.asset_gallery.messagebox.showinfo') as message:
            gallery.remove_selected()
            message.assert_called_once()
        self.assertEqual(paths[:2], app.product_images)
        with patch.object(gallery.tree, 'identify_row', return_value='1'), patch.object(gallery.tree, 'identify_column', return_value='#1'):
            self.assertEqual('break', gallery.on_checkbox_click(SimpleNamespace(x=90, y=60)))
        self.assertEqual('☑', gallery.tree.set('1', 'checked'))
        app.product_images.append(paths[2])
        app._refresh_image_labels()
        self.assertEqual({paths[1]}, gallery.checked_paths)
        gallery.tree.selection_set('0')  # Preview another row; only checked #1 is deleted.
        gallery.remove_selected()
        self.assertEqual([paths[0], paths[2]], app.product_images)
        self.assertEqual(paths, app.competitor_images)
        self.assertTrue(all(Path(path).is_file() for path in paths))
        gallery.toggle_all()
        self.assertEqual(2, len(gallery.checked_paths))
        gallery.toggle_all()
        self.assertFalse(gallery.checked_paths)

    def test_export_inspector_receives_redacted_requests_responses_and_history(self):
        app = self.app
        events = APIDebugLogger(on_event=app._receive_api_event)
        events.secrets = ['fake-secret']
        events.log('request', request_id='one', module_name='细节', request_headers={'Authorization': 'Bearer fake-secret'}, request_json={'prompt': 'macro layout'})
        events.log('response', request_id='one', http_status=504, response_json={'error': 'fake-secret timeout'})
        self.assertEqual(3, len(app.export_workspace_tabs.tabs()))
        inspector = app.export_api_inspector
        self.assertIn('macro layout', inspector.request_text.get('1.0', 'end'))
        self.assertIn('504', inspector.response_text.get('1.0', 'end'))
        self.assertNotIn('fake-secret', json.dumps(inspector.records))
        self.assertEqual(app.api_inspector.records, inspector.records)
        inspector.follow_var.set(False)
        events.log('request', request_id='two', request_json={'prompt': 'second'})
        self.assertEqual(('one',), inspector.tree.selection())
        inspector.clear()
        self.assertFalse(inspector.tree.get_children())
        self.assertEqual(2, len(app.api_inspector.records))

    def test_copy_preview_updates_while_next_module_is_still_running(self):
        app = self.app
        release = threading.Event()
        second_started = threading.Event()
        class GatedClient(PlannedAI):
            def generate_json(inner, prompt, model):
                if json.loads(prompt.split('\nModule: ')[-1])['instance_id'] == 'detail2':
                    second_started.set()
                    if not release.wait(15):
                        raise OpenAIError('test timeout')
                return super().generate_json(prompt, model)
        project = deepcopy(self.project)
        project.options.optimize_copy_with_ai = True
        app._prepare_copy_progress(project)
        generator = BriefGenerator(GatedClient(), lambda event: app.events.put(('copy_progress', event)))
        app._start_async('draft', lambda: generator.generate(project), 'offline progressive test')
        try:
            self.pump_until(lambda: second_started.is_set() and 'detail1' in app._live_copy_done)
            self.assertTrue(app.busy)
            self.assertIn('detail1 de', app.current_copy_text.get('1.0', 'end'))
            self.assertEqual('detail1', app.current_review_code)
            self.assertNotIn('detail2', app._live_copy_done)
            app.follow_copy_var.set(False)
            app._set_text(app.current_copy_text, 'Reviewed German while next request runs', editable=True)
            release.set()
            self.pump_until(lambda: not app.busy)
            self.assertEqual('detail1', app.current_review_code)
            self.assertEqual('Reviewed German while next request runs', app.current_copy_text.get('1.0', 'end-1c'))
            self.assertEqual(3, len(app.review_tree.get_children()))
            self.assertEqual(2, len(app.copy_versions['detail1']))  # Generated + human edit, no duplicate final batch version.
            self.assertEqual(1, len(app.copy_versions['detail2']))
        finally:
            release.set()
            self.pump_until(lambda: not app.busy)

    def test_image_preview_is_ready_before_packaging_and_keeps_review_edits(self):
        app = self.app
        project = deepcopy(self.project)
        project.options.generate_ai_images = True
        project.options.generate_german_composites = True
        source = self.directory/'source.jpg'
        Image.new('RGB', (800, 800), 'white').save(source)
        project.product_image_paths = [str(source)]
        app.populate_project(project)
        app._begin_live_results('export')
        observed = []
        worker_briefs = BriefGenerator().generate(project)
        def on_image(event):
            app._receive_image_progress(event)
            if event['instance_id'] == 'white' and event['stage'] == 'ai_ready':
                self.assertIsNotNone(app.image_preview_photo)
                self.assertEqual(Path(event['brief'].ai_effect_image).name, app.image_preview_path_var.get())
                observed.append('preview before packaging')
            if event['instance_id'] == 'white' and event['stage'] == 'complete':
                app.follow_image_var.set(False)
                app.set_image_review_status('满意')
                app.image_revision_text.insert('1.0', '保留当前构图')
        service = GenerationService(self.directory, ImageAI(), on_image_progress=on_image)
        result = service.run(project, briefs=worker_briefs)
        self.assertEqual(['preview before packaging'], observed)
        self.assertIsNot(app.current_briefs[0], worker_briefs[0])
        with patch('amazon_image_brief.gui.messagebox.showinfo'):
            app._handle_operation_done('export', result)
        self.assertEqual('white', app.current_image_review_code)
        self.assertEqual('满意', app.current_briefs[0].image_review_status)
        self.assertEqual('保留当前构图', app.current_briefs[0].image_revision_notes)
        self.assertTrue(app._export_needs_sync)
        self.assertEqual(3, len(app.image_review_tree.get_children()))
        # The follow-up packaging uses isolated UI snapshots and converges without another generation request.
        reconciled = service.refresh_output(app.collect_project(sync_modules=False), deepcopy(app.current_briefs), Path(result['output_dir']))
        app._handle_operation_done('image_review_save', reconciled)
        self.assertFalse(app._export_needs_sync)

    def test_error_keeps_finished_module_and_saves_recovery(self):
        app = self.app
        app._begin_live_results('draft')
        completed = BriefGenerator().generate(self.project)[1]
        app._receive_copy_progress({'instance_id': 'detail1', 'module_name': completed.module_name, 'focus': 'wheel detail',
                                    'status': completed.generation_status, 'error': '', 'done': 1, 'total': 3,
                                    'finished': True, 'brief': completed})
        app.busy = True
        app.events.put(('operation_error', ('simulated later failure', 'trace')))
        with patch('amazon_image_brief.gui.messagebox.showerror'):
            app._poll_events()
        self.assertFalse(app.busy)
        self.assertTrue(app.review_tree.exists('detail1'))
        self.assertEqual('detail1', app.profiles.store.load()['briefs'][0]['instance_id'])

    def test_final_event_automatically_repackages_review_edits_without_ai(self):
        app = self.app
        app.current_briefs = BriefGenerator().generate(self.project)
        app._refresh_review_tree()
        app._refresh_image_review_tree()
        worker_briefs = deepcopy(app.current_briefs)
        app.image_review_status_var.set('满意')
        app.image_revision_text.insert('1.0', 'live review note')
        output = self.directory/'out'
        output.mkdir()
        calls = []
        class PackagingOnly:
            def refresh_output(inner, project, briefs, directory):
                calls.append(deepcopy(briefs))
                return {'briefs': briefs, 'output_dir': str(directory)}
        app._begin_live_results('export')
        app.busy = True
        app.events.put(('operation_done', ('export', {'briefs': worker_briefs, 'output_dir': str(output)})))
        with patch.object(app, '_interactive_service', return_value=PackagingOnly()), patch('amazon_image_brief.gui.messagebox.showinfo') as popup:
            app._poll_events()
            self.pump_until(lambda: not app.busy)
            popup.assert_not_called()
        self.assertEqual(1, len(calls))
        self.assertEqual('满意', calls[0][0].image_review_status)
        self.assertEqual('live review note', calls[0][0].image_revision_notes)
        self.assertFalse(app._export_needs_sync)

    def test_busy_autosave_recovers_finished_parts_without_auto_retry(self):
        app = self.app
        app.current_briefs = BriefGenerator().generate(self.project)
        app.current_briefs[1].generation_status = '正在生成'
        app.current_briefs[1].image_generation_status = 'AI图就绪 / 正在排版质检'
        app.busy = True
        app.profiles.tick()
        saved = app.profiles.store.load()
        self.assertTrue(saved['ui']['was_generating'])
        app.busy = False
        app.profiles.restore(saved)
        self.assertEqual('首图无文案', app.current_briefs[0].generation_status)
        self.assertIn('上次未完成', app.current_briefs[1].generation_status)
        self.assertIn('已有图片保留', app.current_briefs[1].image_generation_status)
        self.assertFalse(app.busy)


if __name__ == '__main__':
    unittest.main()
