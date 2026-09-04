from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .models import ProductProject


@dataclass(slots=True)
class BatchQueueItem:
    task_id: str
    project_path: str
    project_name: str = ""
    sku: str = ""
    status: str = "等待中"
    progress: str = ""
    result_dir: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


QueueProcessor = Callable[[ProductProject, Callable[[str, int, int], None], Callable[[], None]], dict[str, Any]]
QueueCallback = Callable[[], None]


class PersistentBatchQueue:
    """Single-worker persistent queue with pause/resume checkpoints between module calls."""

    def __init__(self, queue_path: Path) -> None:
        self.queue_path = queue_path
        self.items: list[BatchQueueItem] = []
        self._lock = threading.RLock()
        self._run_event = threading.Event()
        self._run_event.set()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.load()

    def load(self) -> None:
        if not self.queue_path.is_file():
            return
        try:
            raw = json.loads(self.queue_path.read_text(encoding="utf-8"))
            self.items = [BatchQueueItem(**item) for item in raw.get("items", []) if isinstance(item, dict)]
            for item in self.items:
                if item.status in {"运行中", "暂停中"}:
                    item.status = "等待中"
                    item.progress = "上次运行中断，可继续"
        except (OSError, json.JSONDecodeError, TypeError):
            self.items = []

    def save(self) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "items": [asdict(item) for item in self.items]}
        temporary = self.queue_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.queue_path)

    def add_projects(self, paths: list[str]) -> list[BatchQueueItem]:
        added: list[BatchQueueItem] = []
        with self._lock:
            existing = {Path(item.project_path).resolve() for item in self.items if Path(item.project_path).exists()}
            for value in paths:
                path = Path(value)
                if not path.is_file() or path.resolve() in existing:
                    continue
                project = ProductProject.from_dict(json.loads(path.read_text(encoding="utf-8")))
                now = datetime.now().isoformat(timespec="seconds")
                item = BatchQueueItem(
                    task_id=uuid.uuid4().hex[:12],
                    project_path=str(path.resolve()),
                    project_name=project.project_name,
                    sku=project.sku,
                    created_at=now,
                    updated_at=now,
                )
                self.items.append(item)
                added.append(item)
                existing.add(path.resolve())
            self.save()
        return added

    def remove(self, task_ids: list[str]) -> int:
        with self._lock:
            before = len(self.items)
            self.items = [item for item in self.items if item.task_id not in task_ids or item.status == "运行中"]
            self.save()
            return before - len(self.items)

    def retry_failed(self) -> int:
        count = 0
        with self._lock:
            for item in self.items:
                if item.status == "失败":
                    item.status, item.error, item.progress = "等待中", "", "等待重试"
                    count += 1
            self.save()
        return count

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def is_paused(self) -> bool:
        return not self._run_event.is_set()

    def start(self, processor: QueueProcessor, callback: QueueCallback | None = None) -> None:
        if self.is_running:
            self.resume()
            return
        self._stop_event.clear()
        self._run_event.set()
        self._thread = threading.Thread(target=self._worker, args=(processor, callback), daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._run_event.clear()
        with self._lock:
            for item in self.items:
                if item.status == "运行中":
                    item.status = "暂停中"
                    item.progress = "将在当前请求完成后暂停"
            self.save()

    def resume(self) -> None:
        with self._lock:
            for item in self.items:
                if item.status == "暂停中":
                    item.status = "运行中"
            self.save()
        self._run_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._run_event.set()

    def wait_if_paused(self) -> None:
        while not self._run_event.wait(timeout=0.5):
            if self._stop_event.is_set():
                raise RuntimeError("批量任务已停止")
        if self._stop_event.is_set():
            raise RuntimeError("批量任务已停止")

    def _worker(self, processor: QueueProcessor, callback: QueueCallback | None) -> None:
        while not self._stop_event.is_set():
            if not self._run_event.wait(timeout=0.5):
                continue
            with self._lock:
                item = next((entry for entry in self.items if entry.status == "等待中"), None)
                if not item:
                    break
                item.status = "运行中"
                item.progress = "读取项目"
                item.updated_at = datetime.now().isoformat(timespec="seconds")
                self.save()
            if callback:
                callback()
            try:
                project = ProductProject.from_dict(json.loads(Path(item.project_path).read_text(encoding="utf-8")))

                def progress(message: str, done: int, total: int) -> None:
                    with self._lock:
                        item.progress = f"{done}/{total} {message}"
                        item.updated_at = datetime.now().isoformat(timespec="seconds")
                        self.save()
                    if callback:
                        callback()

                result = processor(project, progress, self.wait_if_paused)
                with self._lock:
                    item.status = "已完成"
                    item.progress = "完成"
                    item.result_dir = str(result.get("output_dir", ""))
                    item.error = ""
            except Exception as exc:
                with self._lock:
                    item.status = "失败"
                    item.error = str(exc)
                    item.progress = "失败"
            finally:
                with self._lock:
                    item.updated_at = datetime.now().isoformat(timespec="seconds")
                    self.save()
                if callback:
                    callback()
