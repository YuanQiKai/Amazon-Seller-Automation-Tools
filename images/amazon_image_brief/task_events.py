"""Fence late worker events after Stop, without touching Tk from worker threads."""
from queue import Queue
from threading import local


class TaskEvents(Queue):
    def __init__(self):
        super().__init__()
        self.context = local()
        self.generation = 0

    def activate(self):
        self.generation += 1
        return self.generation

    def bind_worker(self, generation):
        self.context.generation = generation

    def put(self, item, block=True, timeout=None):
        token = getattr(self.context, 'generation', None)
        if token is not None:
            item = ('task_event', (token, item))
        super().put(item, block, timeout)


def check_cancelled(callback):
    """Unlike a pause checkpoint this never delays a completed response."""
    owner = getattr(callback, '__self__', None)
    check = getattr(owner, 'check_cancelled', None)
    if check:
        check()
