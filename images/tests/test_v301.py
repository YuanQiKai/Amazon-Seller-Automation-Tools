from copy import deepcopy
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from io import BytesIO
import base64
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image

from amazon_image_brief.ai_client import OpenAIClient, OpenAIError
from amazon_image_brief.creative_workspace import APIInspector
from amazon_image_brief.generation_control import GenerationCancelled
from amazon_image_brief.models import GenerationOptions, ProductProject
from amazon_image_brief.product_context import ProductContext
from amazon_image_brief.retry_policy import retry_delay
from test_v28 import project_at
from test_v30 import ContextAI, photos


class Response:
    status, headers = 200, {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return 200
    def read(self): return b'{"ok":true}'


def error(status=504, header=None, body=None):
    return HTTPError('https://gateway.test/v1/chat/completions', status, 'Test Error', header or {},
                     BytesIO(json.dumps(body or {'retry_after': 120, 'error_name': 'origin_gateway_timeout'}).encode()))


class RetryTests(unittest.TestCase):
    def client(self, retries=1, events=None):
        return OpenAIClient(options=GenerationOptions(max_retries=retries, cache_enabled=False, api_debug_enabled=False),
                            text_api_key='unit-secret', on_api_event=events.append if events is not None else None)

    def post(self, client):
        return client._post_json('https://gateway.test/v1', '/chat/completions', {'model': 'unit-model'}, 'unit-secret', '', 'unit supplier')

    def test_retry_after_header_and_json_minimum_are_honored(self):
        self.assertEqual(120, retry_delay(504, {'Retry-After': '120'}, {}, 0))
        self.assertEqual(120, retry_delay(504, {}, {'retry_after': 120}, 1))
        self.assertEqual(150, retry_delay(503, {'retry-after': '120'}, {'error': {'retry_after': 150}}, 0))
        self.assertEqual(30, retry_delay(504, {'Retry-After': 'nan'}, {'retry_after': -1}, 0))

    def test_retry_after_http_date(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(180, retry_delay(503, {'Retry-After': format_datetime(now+timedelta(seconds=180))}, {}, 0, now))

    def test_504_waits_120_seconds_before_retry_without_real_sleep(self):
        events, client = [], self.client()
        client.api_debug.on_event = events.append
        with patch('urllib.request.urlopen', side_effect=[error(header={'Retry-After': '120'}), Response()]) as network, patch.object(client, '_retry_wait') as wait:
            self.assertEqual({'ok': True}, self.post(client))
        self.assertEqual(2, network.call_count)
        wait.assert_called_once_with(120)
        self.assertEqual(504, next(e for e in events if e.get('http_status') == 504)['http_status'])
        self.assertEqual(120, next(e for e in events if e['phase'] == 'retry_wait')['wait_seconds'])
        self.assertNotIn('unit-secret', json.dumps(events))

    def test_524_is_retryable_and_timeout_is_caught(self):
        for failure in (error(524), TimeoutError('read timeout')):
            client = self.client()
            with patch('urllib.request.urlopen', side_effect=[failure, Response()]), patch.object(client, '_retry_wait') as wait:
                self.assertTrue(self.post(client)['ok'])
                self.assertEqual(1, wait.call_count)

    def test_auth_error_does_not_retry(self):
        client = self.client(3)
        with patch('urllib.request.urlopen', side_effect=error(401)) as network, patch.object(client, '_retry_wait') as wait:
            with self.assertRaises(OpenAIError) as caught:
                self.post(client)
        self.assertEqual(1, network.call_count)
        self.assertFalse(caught.exception.retryable)
        wait.assert_not_called()

    def test_final_timeout_preserves_retry_metadata_and_budget(self):
        client = self.client(0)
        with patch('urllib.request.urlopen', side_effect=error()), patch.object(client, '_retry_wait') as wait:
            with self.assertRaises(OpenAIError) as caught:
                self.post(client)
        self.assertEqual(504, caught.exception.http_status)
        self.assertEqual(120, caught.exception.retry_after)
        self.assertGreater(caught.exception.retry_not_before, time.time()+110)
        wait.assert_not_called()

    def test_extreme_retry_after_stops_without_early_retry(self):
        client = self.client(4)
        with patch('urllib.request.urlopen', side_effect=error(header={'Retry-After': '7200'})) as network, patch.object(client, '_retry_wait') as wait:
            with self.assertRaises(OpenAIError) as caught:
                self.post(client)
        self.assertEqual(7200, caught.exception.retry_after)
        self.assertEqual(1, network.call_count)
        wait.assert_not_called()

    def test_retry_wait_remains_cancellable(self):
        client = self.client()
        def cancel(): raise GenerationCancelled('closed')
        client.request_control = cancel
        with self.assertRaises(GenerationCancelled):
            client.wait_for_retry(120)

    def test_vision_aggregate_keeps_status_and_context_label(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.client()
            client.debug_context['work_type'] = '产品上下文分析'
            with patch.object(client, '_post_json', side_effect=OpenAIError('gateway timeout', http_status=504, retryable=True, retry_after=120)):
                with self.assertRaises(OpenAIError) as caught:
                    client.analyze_images_json('inspect', [Path(p) for p in photos(Path(temp), 1)])
            self.assertIn('产品上下文分析', str(caught.exception))
            self.assertNotIn('OCR', str(caught.exception))
            self.assertEqual(504, caught.exception.http_status)
            self.assertEqual(120, caught.exception.retry_after)


class RecoveryTests(unittest.TestCase):
    def test_resume_skips_completed_images_and_preserves_cooldown_across_save(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = project_at(temp)
            project.options.context_before_generation = True
            project.product_image_paths = photos(root, 3)
            client = ContextAI()
            original = client.analyze_images_json
            def analyze(prompt, paths, model, **kwargs):
                if str(paths[0]) == project.product_image_paths[1]:
                    raise OpenAIError('504', http_status=504, retryable=True, retry_after=120)
                return original(prompt, paths, model, **kwargs)
            client.analyze_images_json = analyze
            with self.assertRaises(OpenAIError):
                ProductContext(client).prepare(project)
            self.assertEqual('failed', project.ai_context['status'])
            self.assertEqual(1, project.ai_context['completed_batches'])
            restored = ProductProject.from_dict(project.to_dict())
            next_client = ContextAI()
            waits = []
            next_client.wait_for_retry = lambda seconds, **kwargs: waits.append(seconds)
            ProductContext(next_client).prepare(restored)
            self.assertEqual(project.product_image_paths[1:], [p for c in next_client.calls for p in c[2]])
            self.assertEqual(1, len(waits))
            self.assertGreater(waits[0], 110)
            self.assertEqual(3, len(restored.ai_context['analysis']))
            self.assertEqual('complete', restored.ai_context['status'])

    def test_changed_product_invalidates_partial_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            project = project_at(temp)
            project.options.context_before_generation = True
            project.product_image_paths = photos(Path(temp), 2)
            ProductContext(ContextAI()).prepare(project)
            project.ai_context['status'] = 'failed'
            project.price = 'changed price'
            client = ContextAI()
            ProductContext(client).prepare(project)
            self.assertEqual(2, len(client.calls))

    def test_small_analysis_copy_does_not_modify_original_photo(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'large.png'
            Image.new('RGB', (3000, 2000), 'red').save(path)
            original = path.read_bytes()
            url = OpenAIClient._vision_data_url(path, 1280)
            with Image.open(BytesIO(base64.b64decode(url.split(',', 1)[1]))) as image:
                self.assertEqual(1280, max(image.size))
            self.assertEqual(original, path.read_bytes())

    def test_inspector_preserves_response_when_retry_notice_arrives(self):
        root = tk.Tk()
        root.withdraw()
        try:
            view = APIInspector(root)
            view.add_event({'phase': 'request', 'request_id': 'x', 'request_json': {'model': 'unit'}})
            view.add_event({'phase': 'response', 'request_id': 'x', 'http_status': 504, 'response_json': {'retry_after': 120}})
            view.add_event({'phase': 'retry_wait', 'request_id': 'x', 'wait_seconds': 120})
            self.assertEqual(504, view.records['x']['response']['http_status'])
            shown = json.loads(view.response_text.get('1.0', 'end'))
            self.assertEqual(120, shown['response_json']['retry_after'])
            self.assertEqual(120, shown['retry_events'][0]['wait_seconds'])
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
