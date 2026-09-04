from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


class RequestRateLimiter:
    """Thread-safe rolling-window limiter shared by provider and API key environment."""

    _instances: dict[tuple[str, int], "RequestRateLimiter"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, requests_per_minute: int) -> None:
        self.limit = max(1, int(requests_per_minute))
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    @classmethod
    def shared(cls, identity: str, requests_per_minute: int) -> "RequestRateLimiter":
        key = (identity, max(1, int(requests_per_minute)))
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(key[1])
            return cls._instances[key]

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 60:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                wait_seconds = max(0.05, 60 - (now - self._timestamps[0]))
            time.sleep(min(wait_seconds, 1.0))


class AICache:
    """Content-addressed JSON and JPG cache. API credentials are never part of cache metadata."""

    def __init__(self, root: Path | None, enabled: bool = True) -> None:
        self.root = Path(root) if root else None
        self.enabled = bool(enabled and self.root)
        if self.enabled:
            (self.root / "text").mkdir(parents=True, exist_ok=True)
            (self.root / "images").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def get_json(self, key: str) -> Any | None:
        if not self.enabled or not self.root:
            return None
        path = self.root / "text" / f"{key}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put_json(self, key: str, value: Any) -> None:
        if not self.enabled or not self.root:
            return
        path = self.root / "text" / f"{key}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def copy_image_to(self, key: str, destination: Path) -> bool:
        if not self.enabled or not self.root:
            return False
        source = self.root / "images" / f"{key}.jpg"
        if not source.is_file():
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return True

    def put_image(self, key: str, source: Path) -> None:
        if not self.enabled or not self.root or not source.is_file():
            return
        destination = self.root / "images" / f"{key}.jpg"
        shutil.copy2(source, destination)

    def clear(self) -> int:
        if not self.root or not self.root.exists():
            return 0
        removed = 0
        for folder in (self.root / "text", self.root / "images"):
            if folder.exists():
                for path in folder.iterdir():
                    if path.is_file():
                        path.unlink()
                        removed += 1
        return removed
