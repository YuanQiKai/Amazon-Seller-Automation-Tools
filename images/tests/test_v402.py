from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

import test_v40
from amazon_image_brief.asset_gallery import AssetGallery
from amazon_image_brief.image_previews import PreviewData, preview_pool
from amazon_image_brief.direct_images import module_recipe, image_jobs
from amazon_image_brief.models import ProductProject, ModuleInstance


def pump(root, predicate, timeout=5):
    deadline = time.monotonic()+timeout
    while not predicate() and time.monotonic() < deadline:
        root.update()
        time.sleep(.01)
    if not predicate():
        raise AssertionError('Timed out waiting for a background preview')


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.attributes('-alpha', 0)
        self.root.geometry('780x250')
        self.gallery = AssetGallery(self.root, lambda paths: self.gallery.refresh(paths), compact=True, lazy=True)
        self.gallery.pack(fill='both', expand=True)
        self.pool = preview_pool(self.root)
        self.root.update()

    def tearDown(self):
        self.pool.close()
        for worker in self.pool._threads:
            worker.join(5)
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temp.cleanup()

    def create_image(self, name, colour):
        path = self.folder/name
        Image.new('RGB', (1200, 900), colour).save(path)
        return str(path)

    def test_many_images_return_immediately_and_only_visible_rows_load(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        image = Image.new('RGB', (100, 80), 'red')
        def slow_read(path, size):
            calls.append((path, threading.get_ident()))
            started.set()
            release.wait(5)
            return PreviewData(image, image, '1200×900px')
        main = threading.get_ident()
        try:
            with patch.object(self.pool, '_read', side_effect=slow_read):
                begin = time.monotonic()
                self.gallery.refresh([str(self.folder/f'{i}.jpg') for i in range(200)], context_key='many')
                self.assertLess(time.monotonic()-begin, .5)
                pump(self.root, started.is_set)
                heartbeat = []
                self.root.after(10, lambda: heartbeat.append(True))
                pump(self.root, lambda: heartbeat)
                self.assertFalse(release.is_set())
                with self.pool._condition:
                    self.assertLessEqual(len(self.pool._jobs), 8)
                self.assertTrue(all(worker != main for _, worker in calls))
                release.set()
                pump(self.root, lambda: self.gallery.preview_photo is not None)
                self.assertLess(len(calls), 12)
        finally:
            release.set()

    def test_late_old_module_thumbnail_cannot_replace_current_preview(self):
        red, blue = self.create_image('red.jpg', 'red'), self.create_image('blue.jpg', 'blue')
        started, release = threading.Event(), threading.Event()
        read = self.pool._read
        def gated(path, size):
            if path == red:
                started.set()
                release.wait(5)
            return read(path, size)
        try:
            with patch.object(self.pool, '_read', side_effect=gated):
                self.gallery.refresh([red], context_key='old')
                pump(self.root, started.is_set)
                self.gallery.refresh([blue], context_key='new')
                pump(self.root, lambda: self.gallery.preview_photo is not None)
                release.set()
                pump(self.root, lambda: not self.pool._requests)
                self.assertEqual([blue], self.gallery.paths)
                pixel = self.root.tk.call(str(self.gallery.preview_photo), 'get', 10, 10)
                self.assertGreater(pixel[2], 200)
                self.assertLess(pixel[0], 20)
        finally:
            release.set()

    def test_cached_images_reused_and_changed_file_invalidates_cache(self):
        path = self.create_image('cached.bmp', 'red')
        owner, answers = object(), []
        self.pool.request(owner, 'image', path, (100, 80), answers.append)
        pump(self.root, lambda: len(answers) == 1)
        self.pool.request(owner, 'image', path, (100, 80), answers.append)
        pump(self.root, lambda: len(answers) == 2)
        self.assertEqual(1, self.pool.decode_count)
        self.assertEqual(1, self.pool.cache_hits)
        Image.new('RGB', (1000, 800), 'blue').save(path)  # size and mtime change
        self.pool.request(owner, 'image', path, (100, 80), answers.append)
        pump(self.root, lambda: len(answers) == 3)
        self.assertEqual(2, self.pool.decode_count)
        self.assertEqual((0, 0, 255), answers[-1].image.getpixel((0, 0)))

    def test_scrolling_loads_later_rows_and_preserves_check_numbers(self):
        path = self.create_image('photo.jpg', 'green')
        # Distinct paths share bytes, as many product-angle exports often do.
        import shutil
        paths = []
        for i in range(30):
            target = self.folder/f'photo-{i}.jpg'
            shutil.copyfile(path, target)
            paths.append(str(target))
        self.gallery.number_labels = {p: f'P{i+10:02d}' for i, p in enumerate(paths)}
        self.gallery.refresh(paths, context_key='module')
        self.gallery.toggle_checked('20')
        self.gallery.tree.see('29')
        self.root.update()
        pump(self.root, lambda: '29' in self.gallery._ready_rows)
        self.assertEqual('P39', self.gallery.tree.set('29', 'number'))
        self.assertIn(paths[20], self.gallery.checked_paths)
        self.assertLess(self.pool.decode_count, 15)
        self.gallery.remove_selected()
        self.assertNotIn(paths[20], self.gallery.paths)

    def test_missing_images_and_lru_limit_do_not_break_loading(self):
        owner, answers = object(), []
        self.pool.capacity = 2
        self.pool.request(owner, 'missing', str(self.folder/'missing.jpg'), (100, 80), answers.append)
        pump(self.root, lambda: answers)
        self.assertTrue(answers[0].error)
        for index, colour in enumerate(('red', 'blue', 'green')):
            path = self.create_image(f'{index}.jpg', colour)
            self.pool.request(owner, index, path, (100, 80), answers.append)
            pump(self.root, lambda: len(answers) == index+2)
        self.assertEqual(2, len(self.pool._cache))
        self.pool.close()
        self.assertTrue(self.pool.closed)
        self.assertFalse(self.pool._jobs)


class DirectInputTests(unittest.TestCase):
    setUp = test_v40.DirectDesktopTests.setUp
    tearDown = test_v40.DirectDesktopTests.tearDown

    def test_new_modules_do_not_inherit_global_product_photos(self):
        app = self.app
        app.product_images = [str(self.folder/'product.png')]
        project = ProductProject(product_image_paths=list(app.product_images),
                                 module_instances=[ModuleInstance('new', 'MAIN_DETAIL', '主图')])
        self.assertEqual([], module_recipe(project, 'new')['direct_product_images'])
        self.assertEqual([], image_jobs(project)[0][2]['direct_product_images'])
        before = set(app.module_order)
        app.module_lists['主图'].selection_clear(0, 'end')
        app.module_lists['主图'].selection_set(0)
        app.add_module_instance('主图')
        added = (set(app.module_order)-before).pop()
        self.assertEqual([], app.module_recipes[added]['direct_product_images'])
        self.assertEqual(['P01'], [p['id'] for p in app.module_recipes['m1']['direct_product_images']])

    def test_existing_module_inputs_and_explicit_copy_remain_supported(self):
        app = self.app
        app.product_images = [str(self.folder/'reference.png')]
        snapshot = app.profiles.capture()
        app.profiles.restore(snapshot)
        self.assertEqual('product.png', Path(app.module_recipes['m1']['direct_product_images'][0]['asset_path']).name)
        app._append_direct_assets('product', app.product_images)
        self.assertEqual(['P01', 'P02'], [p['id'] for p in app.module_recipes['m1']['direct_product_images']])

    def test_market_page_removed_and_old_profile_cannot_reopen_it(self):
        app = self.app
        expected = ['01  API配置', '02  产品信息', '03  选择模块', '04  创意生图', '05  批量任务与合规']
        self.assertEqual(expected, [app.tabs.tab(tab, 'text') for tab in app.tabs.tabs()])
        app._set_text(app.competitors_text, 'B012345678 | https://example.test/product | brand', editable=True)
        snapshot = app.profiles.capture()
        snapshot['ui']['page_key'] = 'market'
        app.profiles.restore(snapshot)
        self.assertEqual(app.pages['product'], app.tabs.select())
        self.assertEqual(expected, [app.tabs.tab(tab, 'text') for tab in app.tabs.tabs()])
        self.assertIn('B012345678', app.competitors_text.get('1.0', 'end-1c'))

    def test_switching_many_images_updates_prompt_before_decoder_returns(self):
        app = self.app
        pool = preview_pool(self.root)
        pump(self.root, lambda: not pool._requests)
        started, release = threading.Event(), threading.Event()
        calls = []
        image = Image.new('RGB', (80, 60), 'blue')
        def slow(path, size):
            calls.append(threading.get_ident())
            started.set()
            release.wait(5)
            return PreviewData(image, image, '800×600px')
        app.module_recipes['m2']['direct_product_images'] = [{'id': f'P{i+1:02d}', 'asset_path': str(self.folder/f'large-{i}.jpg')} for i in range(200)]
        app.module_recipes['m2']['direct_prompt'] = '第二模块提示词立即出现'
        try:
            with patch.object(pool, '_read', side_effect=slow):
                app.direct_module_tree.selection_set('m2')
                before = time.monotonic()
                app._select_direct_module()
                self.assertLess(time.monotonic()-before, .5)
                self.assertEqual('第二模块提示词立即出现', app.direct_prompt_text.get('1.0', 'end-1c'))
                pump(self.root, started.is_set)
                app.direct_module_tree.selection_set('m1')
                app._select_direct_module()
                self.assertEqual('m1', app.direct_current_id)
                self.assertEqual(1, len(app.direct_galleries['product'].paths))
                self.assertTrue(all(worker != threading.get_ident() for worker in calls))
                release.set()
                pump(self.root, lambda: not pool._requests)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
