from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_NAMES = ("authorization", "api-key", "apikey", "token", "secret", "cookie", "password")
_BINARY_KEYS = ("b64", "base64", "image", "audio", "file", "mask")


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower().replace("_", "-")
    if lowered in {'max-tokens', 'input-tokens', 'output-tokens', 'prompt-tokens', 'completion-tokens', 'total-tokens', 'cached-tokens', 'reasoning-tokens'}:
        return False
    return any(part in lowered for part in _SENSITIVE_NAMES)


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https"} or not parsed.query:
        return value
    query = [
        (key, "[REDACTED]" if _is_sensitive_name(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _omitted_blob(value: str | bytes, kind: str) -> dict[str, Any]:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return {
        "_omitted": kind,
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def sanitize_for_log(value: Any, key_hint: str = "") -> Any:
    """Return a JSON-safe copy with credentials and large encoded media removed."""
    if _is_sensitive_name(key_hint):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(key): sanitize_for_log(item, str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_log(item, key_hint) for item in value]
    if isinstance(value, bytes):
        return _omitted_blob(value, "binary bytes")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value:
            media_type = value[5:].split(";", 1)[0] or "application/octet-stream"
            metadata = _omitted_blob(value.split(";base64,", 1)[1], "base64 data URL")
            metadata["media_type"] = media_type
            return metadata
        if key_hint.lower() in {'b64_json', 'base64', 'image', 'audio', 'file', 'mask'} and len(value) > 2048:
            return _omitted_blob(value, "encoded media")
        if value.startswith(("http://", "https://")):
            return _redact_url(value)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def parse_response_body(raw: bytes) -> Any:
    """Represent a response as JSON data, raw text, or binary metadata."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _omitted_blob(raw, "binary response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw_text": text}


class APIDebugLogger:
    """Print and persist one redacted JSON object per HTTP request/response event."""

    _lock = threading.Lock()

    def __init__(self, path: Path | None = None, enabled: bool = False, on_event: Callable[[dict], None] | None = None) -> None:
        self.path = path
        self.enabled = enabled
        self.on_event = on_event
        self.context: dict[str, Any] = {}
        self.secrets: list[str] = []

    def log(self, phase: str, **fields: Any) -> None:
        if not self.enabled and not self.on_event:
            return
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "phase": phase,
            **self.context,
            **fields,
        }
        safe_event = sanitize_for_log(event)
        line = json.dumps(safe_event, ensure_ascii=False, separators=(",", ":"))
        for secret in self.secrets:
            if secret:
                line = line.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]')
        safe_event = json.loads(line)
        if self.on_event:
            self.on_event(safe_event)
        if not self.enabled:
            return
        with self._lock:
            try:
                print(line, flush=True)
            except (UnicodeEncodeError, OSError, AttributeError):
                pass
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
