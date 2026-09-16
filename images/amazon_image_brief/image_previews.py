"""Bounded, cancellable preview loading. Workers never call Tk or open full-size UI images."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import os
import queue
import threading
import tkinter as tk

from PIL import Image, ImageOps


@dataclass(frozen=True)
class PreviewData:
    image: object = None
    thumbnail: object = None
    dimensions: str = ''
    error: str = ''


class PreviewPool:
    def __init__(self, root, capacity=128):
        self.root, self.capacity = root, capacity
        self.closed = False
        self._serial = 0
        self._requests = {}
        self._jobs = OrderedDict()
        self._cache = OrderedDict()
        self._condition = threading.Condition()
        self._results = queue.Queue()
        self.decode_count = self.cache_hits = 0
        self._after = root.after(25, self._drain)
        root.bind('<Destroy>', self._on_destroy, add='+')
        self._threads = [threading.Thread(target=self._worker, name=f'image-preview-{i}', daemon=True) for i in range(2)]
        for thread in self._threads:
            thread.start()

    def request(self, owner, key, path, size, callback):
        """Replace pending work by owner/key; only the latest request can paint."""
        if self.closed:
            return
        identity = (id(owner), key)  # Never retain Tk widgets inside worker jobs.
        self._serial += 1
        serial = self._serial
        self._requests[identity] = (serial, callback)
        with self._condition:
            self._jobs[identity] = (serial, path, tuple(size))
            self._jobs.move_to_end(identity)
            self._condition.notify()

    def cancel(self, owner):
        for identity in list(self._requests):
            if identity[0] == id(owner):
                self._requests.pop(identity, None)
        with self._condition:
            for identity in list(self._jobs):
                if identity[0] == id(owner):
                    self._jobs.pop(identity, None)

    def cancel_key(self, owner, key):
        identity = (id(owner), key)
        self._requests.pop(identity, None)
        with self._condition:
            self._jobs.pop(identity, None)

    def _read(self, path, size):
        # Even stat/network-path checks belong off the UI thread.
        try:
            source_path = Path(path)
            stat = source_path.stat()
            key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns, size)
            with self._condition:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    self.cache_hits += 1
                    return cached
            with Image.open(source_path) as source:
                width, height = source.size
                if source.getexif().get(274) in (5, 6, 7, 8):
                    width, height = height, width
                source.draft('RGB', size)  # Decode JPEG at preview resolution when supported.
                picture = ImageOps.exif_transpose(source)
                picture.thumbnail(size, Image.Resampling.LANCZOS)
                picture = picture.convert('RGB')
                thumbnail = picture.copy()
                thumbnail.thumbnail((48, 48), Image.Resampling.LANCZOS)
                result = PreviewData(picture, thumbnail, f'{width}×{height}px')
            with self._condition:
                self.decode_count += 1
                if not self.closed:
                    self._cache[key] = result
                    self._cache.move_to_end(key)
                    while len(self._cache) > self.capacity:
                        self._cache.popitem(last=False)
            return result
        except Exception as exc:
            return PreviewData(error=f'无法预览：{type(exc).__name__}')

    def _worker(self):
        while True:
            with self._condition:
                while not self.closed and not self._jobs:
                    self._condition.wait()
                if self.closed:
                    return
                identity, (serial, path, size) = self._jobs.popitem(last=True)
            result = self._read(path, size)
            if not self.closed:
                self._results.put((identity, serial, result))

    def _drain(self):
        self._after = None
        if self.closed:
            return
        # Limit Tk image allocations in one frame; scrolling/input get a turn.
        for _ in range(8):
            try:
                identity, serial, result = self._results.get_nowait()
            except queue.Empty:
                break
            current = self._requests.get(identity)
            if current is None or current[0] != serial:
                continue
            self._requests.pop(identity, None)
            try:
                current[1](result)
            except tk.TclError:
                pass  # The preview widget was closed between requests.
        self._after = self.root.after(25, self._drain)

    def _on_destroy(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        self._requests.clear()
        with self._condition:
            self._jobs.clear()
            self._cache.clear()
            self._condition.notify_all()


def preview_pool(widget):
    root = widget._root()
    if not hasattr(root, '_image_preview_pool'):
        root._image_preview_pool = PreviewPool(root)
    return root._image_preview_pool
