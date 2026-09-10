from copy import deepcopy
from io import BytesIO
import base64
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

from PIL import Image
from openpyxl import load_workbook

from amazon_image_brief.ai_client import OpenAIClient, OpenAIError
from amazon_image_brief.aplus_poster import build_poster
from amazon_image_brief.brief_generator import BriefGenerator
from amazon_image_brief.final_images import final_image_path
from amazon_image_brief.gui import AmazonImageBriefApp
from amazon_image_brief.models import GenerationOptions
from amazon_image_brief.product_context import image_context_text
from amazon_image_brief.reference_inputs import reference_inputs
from amazon_image_brief.service import GenerationService
from test_v28 import project_at
from test_v30 import photos
from test_v210 import ImageAI
from test_v211 import poster_project


class ImageResponse:
    status, headers = 200, {'Content-Type': 'application/json'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return self.status
    def read(self):
        buffer = BytesIO()
        Image.new('RGB', (80, 80), 'white').save(buffer, 'PNG')
        return json.dumps({'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}]}).encode()


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.client = OpenAIClient(options=GenerationOptions(image_provider='sudocode', image_protocol='openai_images_url',
                                   image_base_url='https://api.sudocode.chat/v1', image_model='gpt-image-2',
                                   max_retries=0, cache_enabled=False, api_debug_enabled=False, requests_per_minute=100000),
                                   image_api_key='unit-secret', cache_dir=self.root/'cache', on_api_event=self.events.append)
        self.refs = photos(self.root, 2)

    def tearDown(self): self.temp.cleanup()

    def test_sudocode_gpt_image_with_refs_uses_multipart_edits_without_url_format(self):
        with patch('urllib.request.urlopen', return_value=ImageResponse()) as network:
            self.client.generate_image('Exact final German: Gute Reise', self.root/'final.jpg', reference_images=self.refs)
        request = network.call_args.args[0]
        self.assertEqual('https://api.sudocode.chat/v1/images/edits', request.full_url)
        self.assertIn('multipart/form-data', request.get_header('Content-type'))
        self.assertEqual(2, request.data.count(b'name="image[]"'))
        self.assertNotIn(b'response_format', request.data)
        self.assertIn(b'gpt-image-2', request.data)
        self.assertIn(b'Gute Reise', request.data)
        with Image.open(self.root/'final.jpg') as image: self.assertEqual('JPEG', image.format)
        logged = next(e['request_json'] for e in self.events if e['phase'] == 'request')
        self.assertEqual(2, len(logged['files']['image[]']))
        self.assertNotIn('unit-secret', json.dumps(self.events))

    def test_text_only_gpt_images_keeps_generation_route_and_native_base64(self):
        with patch('urllib.request.urlopen', return_value=ImageResponse()) as network:
            self.client.generate_image('final artwork', self.root/'final.jpg')
        request = network.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/images/generations'))
        payload = json.loads(request.data)
        self.assertEqual({'model', 'prompt', 'size', 'quality', 'output_format'}, set(payload))
        self.assertEqual('jpeg', payload['output_format'])

    def test_non_gpt_gateway_keeps_its_image_url_contract(self):
        payload = self.client._image_payload('openai_images_url', 'p', 'seedream-model', 'medium', 'square', [Path(self.refs[0])])
        self.assertEqual('url', payload['response_format'])
        self.assertTrue(payload['image'].startswith('data:image/'))

    def test_oversized_prompt_fails_locally_without_http_or_silent_truncation(self):
        with patch('urllib.request.urlopen') as network:
            with self.assertRaisesRegex(OpenAIError, '32,000'):
                self.client.generate_image('x'*32001, self.root/'bad.jpg', reference_images=self.refs)
        network.assert_not_called()
        self.assertFalse((self.root/'bad.jpg').exists())

    def test_mask_is_sent_with_edit_not_generation(self):
        mask = self.root/'mask.png'
        Image.new('RGBA', (800, 1000), (0, 0, 0, 0)).save(mask)
        with patch('urllib.request.urlopen', return_value=ImageResponse()) as network:
            self.client.generate_image('edit wheel', self.root/'edit.jpg', reference_images=self.refs[:1], mask_path=str(mask))
        self.assertIn(b'name="mask"', network.call_args.args[0].data)
        self.assertIn(b'name="image"', network.call_args.args[0].data)

    def test_edit_400_preserves_status_and_does_not_retry_without_references(self):
        self.client.options.max_retries = 3
        error = HTTPError('https://api.sudocode.chat/v1/images/edits', 400, 'Invalid', {},
                          BytesIO(b'{"error":{"code":"request_parameter_invalid"}}'))
        with patch('urllib.request.urlopen', side_effect=error) as network:
            with self.assertRaises(OpenAIError) as caught:
                self.client.generate_image('final', self.root/'bad.jpg', reference_images=self.refs)
        self.assertEqual(400, caught.exception.http_status)
        self.assertEqual(1, network.call_count)
        self.assertIn('未自动去掉产品图', str(caught.exception))

    def test_edit_504_honors_retry_after_without_real_sleep(self):
        self.client.options.max_retries = 1
        error = HTTPError('https://api.sudocode.chat/v1/images/edits', 504, 'timeout', {'Retry-After': '120'}, BytesIO(b'{}'))
        with patch('urllib.request.urlopen', side_effect=[error, ImageResponse()]), patch.object(self.client, '_retry_wait') as wait:
            self.client.generate_image('final', self.root/'final.jpg', reference_images=self.refs)
        wait.assert_called_once_with(120)

    def test_reference_transport_uses_originals_up_to_limit_and_preserves_all_beyond(self):
        refs, manifest = reference_inputs(self.refs[:1], self.refs[1:], self.client, self.root/'refs')
        self.assertEqual(self.refs, refs)
        self.assertEqual('STYLE ONLY', manifest['sources'][1]['role'])
        many = photos(self.root, 26)
        refs, manifest = reference_inputs(many, [], self.client, self.root/'refs')
        self.assertEqual(1, len(refs))
        self.assertEqual(26, len(manifest['sources']))
        self.assertTrue(Path(refs[0]).is_file())


class FinalPipelineTests(unittest.TestCase):
    def test_large_context_preserves_inputs_exact_copy_and_user_prompt_without_mutating_context(self):
        project = project_at('unused')
        project.ai_context = {'fingerprint': 'hash', 'analysis': [
            {'summary_zh': f'photo{i} silver suitcase', 'confirmed_input_facts': ['repeat'*1000],
             'visual_observations': [{'image_index': i, 'observation': 'wheel '*100}],
             'conflicts': ['20寸与28寸素材冲突，请以录入变体为准']} for i in range(26)]}
        before = deepcopy(project.ai_context)
        brief = BriefGenerator().generate(project)[1]
        brief.copy['de'] = 'Gute Reise\nVier Rollen – exakt'
        project.module_recipes[brief.instance_id] = {'image_prompt': '保持银色，只展示轮子细节', 'render_text': 'typeset'}
        prompt = GenerationService._generation_prompt(project, brief)
        self.assertLessEqual(len(prompt), 32000)
        self.assertIn(brief.copy['de'], prompt)
        self.assertIn('保持银色，只展示轮子细节', prompt)
        self.assertIn('20寸与28寸素材冲突', prompt)
        self.assertIn('FINAL finished image', prompt)
        self.assertNotIn('NO-LETTERING source', prompt)
        self.assertEqual(before, project.ai_context)
        compact = json.loads(image_context_text(project))
        self.assertEqual(project.sku, compact['original_product_facts']['sku'])
        self.assertEqual(26, compact['product_understanding']['source_images'])

    def test_mandatory_inputs_too_long_are_not_silently_cut(self):
        project = project_at('unused')
        brief = BriefGenerator().generate(project)[1]
        project.module_recipes[brief.instance_id] = {'image_prompt': 'HUMAN '*6000}
        with self.assertRaises(OpenAIError): GenerationService._generation_prompt(project, brief)
        self.assertEqual('HUMAN '*6000, project.module_recipes[brief.instance_id]['image_prompt'])

    def test_only_real_final_jpgs_exported_and_failed_module_has_no_placeholder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = project_at(root/'out')
            project.product_image_paths = photos(root, 1)
            project.options.generate_ai_images = True
            project.options.generate_german_composites = True  # Old checkbox cannot create a duplicate/draft.
            events = []
            def next_call(index):
                if index:
                    self.assertTrue(any(e['stage'] == 'ai_ready' for e in events))
            result = GenerationService(root, ImageAI(fail=True, before_call=next_call), on_image_progress=events.append).run(project)
            output = Path(result['output_dir'])
            self.assertEqual(2, len(list((output/'final_images').glob('*.jpg'))))
            for old in ('reference_images', 'ai_reference_images', 'german_composite_images'):
                self.assertFalse((output/old).exists())
            failed = result['briefs'][1]
            self.assertIsNone(final_image_path(failed))
            self.assertIn('504', failed.image_generation_error)
            self.assertTrue(any(e['stage'] == 'failed' and e['instance_id'] == 'detail1' for e in events))
            with zipfile.ZipFile(result['image_zip']) as package:
                self.assertEqual(2, len(package.namelist()))
                self.assertTrue(all(n.startswith('final_images/') and n.endswith('.jpg') for n in package.namelist()))
            workbook = load_workbook(result['excel'])
            self.assertEqual('最终结果图（所选语言）', workbook['主图']['H3'].value)
            self.assertIn('504', workbook['主图']['G5'].value)
            workbook.close()

    def test_uploaded_input_and_legacy_placeholder_are_not_result_previews(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brief = BriefGenerator().generate(project_at(root))[0]
            brief.reference_image = photos(root, 1)[0]
            self.assertIsNone(final_image_path(brief))
            brief.ai_effect_image = brief.reference_image
            brief.image_generation_status = '失败 / 占位图'
            self.assertIsNone(final_image_path(brief))

    def test_unfinished_aplus_does_not_create_placeholder_panels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = poster_project(root)
            briefs = BriefGenerator().generate(project)
            poster = build_poster(project, briefs, root/'out')
            self.assertEqual([], poster['files'])
            self.assertEqual('', poster['pc'])
            self.assertEqual([], list((root/'out').rglob('*.jpg')))


class ImmediatePreviewTests(unittest.TestCase):
    def test_real_worker_queue_displays_first_final_while_quality_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root_path = Path(temp)
            window = tk.Tk()
            window.withdraw()
            app = AmazonImageBriefApp(window, root_path)
            project = project_at(root_path/'out')
            project.product_image_paths = photos(root_path, 1)
            project.options.generate_ai_images = True
            app.populate_project(project)
            entered, release = threading.Event(), threading.Event()
            def qa(*args):
                entered.set()
                if not release.wait(12): raise RuntimeError('test did not release quality worker')
            service = GenerationService(root_path, ImageAI(), on_image_progress=lambda e: app.events.put(('image_progress', e)))
            try:
                with patch('amazon_image_brief.service.QualityAnalyzer.run_local', side_effect=qa), patch('amazon_image_brief.gui.messagebox.showinfo'):
                    app._start_async('export', lambda: service.run(project), 'test')
                    deadline = time.monotonic()+10
                    while not app.image_preview_photo and time.monotonic() < deadline:
                        window.update()
                        time.sleep(.01)
                    self.assertTrue(entered.is_set())
                    self.assertTrue(app.busy)
                    self.assertIsNotNone(app.image_preview_photo)
                    self.assertEqual('white', app.current_image_review_code)
                    self.assertEqual(app.pages['image'], app.tabs.select())
                    self.assertEqual(app.export_workspace_tabs.tabs()[0], app.export_workspace_tabs.select())
                    release.set()
                    deadline = time.monotonic()+15
                    while app.busy and time.monotonic() < deadline:
                        window.update()
                        time.sleep(.01)
                    self.assertFalse(app.busy)
            finally:
                release.set()
                for callback in window.tk.call('after', 'info'): window.after_cancel(callback)
                window.destroy()

    def test_event_poll_yields_after_a_ready_image_before_remaining_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            window = tk.Tk()
            window.withdraw()
            app = AmazonImageBriefApp(window, Path(temp))
            project = project_at(temp)
            app.populate_project(project)
            app._begin_live_results('export')
            brief = BriefGenerator().generate(project)[0]
            brief.ai_effect_image = photos(Path(temp), 1)[0]
            brief.german_composite_image = brief.ai_effect_image
            brief.image_generation_status = '结果图已就绪 / 质检中'
            event = {'brief': brief, 'stage': 'ai_ready', 'output_dir': temp, 'status': brief.image_generation_status}
            app.events.put(('image_progress', event))
            app.events.put(('progress', ('later module', 1, 2)))
            try:
                app._poll_events()
                self.assertIsNotNone(app.image_preview_photo)
                self.assertEqual(1, app.events.qsize())
            finally:
                for callback in window.tk.call('after', 'info'): window.after_cancel(callback)
                window.destroy()


if __name__ == '__main__': unittest.main()
