"""Cooperative controls: pause future work, never discard an in-flight response."""
from __future__ import annotations

from threading import Condition


class GenerationCancelled(RuntimeError):
    pass


class GenerationControl:
    def __init__(self, kind: str, notify=None):
        self.kind = kind
        self.notify = notify
        self._condition = Condition()
        self.state = 'idle'
        self._closed = False

    def _emit(self):
        if self.notify:
            self.notify({'kind': self.kind, 'state': self.state})

    def start(self):
        with self._condition:
            self._closed = False
            self.state = 'running'
            self._condition.notify_all()
            self._emit()

    def pause(self):
        with self._condition:
            if self.state == 'running':
                self.state = 'pause_requested'
                self._emit()

    def resume(self):
        with self._condition:
            if self.state in {'pause_requested', 'paused'}:
                self.state = 'running'
                self._condition.notify_all()
                self._emit()

    def checkpoint(self):
        with self._condition:
            if self.state == 'pause_requested':
                self.state = 'paused'
                self._emit()
            while self.state == 'paused' and not self._closed:
                self._condition.wait(timeout=0.25)
            self.check_cancelled()

    def check_cancelled(self):
        if self._closed or self.state == 'stopped':
            raise GenerationCancelled('任务已停止；保留已完成结果，不再继续本轮任务。')

    def stop(self):
        with self._condition:
            self._closed = True
            self.state = 'stopped'
            self._condition.notify_all()
            self._emit()

    def finish(self):
        with self._condition:
            if self.state != 'stopped':
                self.state = 'idle'
            self._condition.notify_all()
            self._emit()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
