from __future__ import annotations

import base64
import json
import mimetypes
import os
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .api_debug import APIDebugLogger, parse_response_body
from .ai_runtime import AICache, RequestRateLimiter
from .models import GenerationOptions
from .providers import fallback_preset, model_catalog_url
from .retry_policy import TRANSIENT_HTTP, retry_delay
from .image_requests import is_gpt_image, uses_image_edits, validate_image_prompt


class OpenAIError(RuntimeError):
    """Raised when a configured AI provider request cannot be completed."""

    def __init__(self, message, *, http_status=None, retry_after=0, retryable=False, retry_not_before=0):
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after
        self.retryable = retryable
        self.retry_not_before = retry_not_before or (time.time() + retry_after if retry_after else 0)

    @classmethod
    def aggregate(cls, message, last_error):
        return cls(message, **{key: getattr(last_error, key, default) for key, default in
                              [('http_status', None), ('retry_after', 0), ('retryable', False), ('retry_not_before', 0)]})


class OpenAIClient:
    """Multi-provider HTTP client kept under the legacy class name for compatibility."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 300,
        options: GenerationOptions | None = None,
        text_api_key: str = "",
        image_api_key: str = "",
        cache_dir: Path | None = None,
        debug_log_path: Path | None = None,
        debug_enabled: bool | None = None,
        on_api_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.options = options or GenerationOptions()
        self.timeout = timeout
        shared_key = api_key or ""
        from .env_store import provider_key_name
        self.text_api_key = text_api_key or shared_key or os.getenv(provider_key_name('text', self.options.text_provider), '') or os.getenv(self.options.text_api_key_env, "")
        self.image_api_key = image_api_key or shared_key or os.getenv(provider_key_name('image', self.options.image_provider), '') or os.getenv(self.options.image_api_key_env, "")
        self.cache = AICache(cache_dir, self.options.cache_enabled)
        self.api_debug = APIDebugLogger(
            debug_log_path,
            self.options.api_debug_enabled if debug_enabled is None else debug_enabled,
            on_api_event,
        )
        self.api_debug.secrets = [self.text_api_key, self.image_api_key]
        self.debug_context = self.api_debug.context
        rpm = max(1, int(self.options.requests_per_minute))
        self.text_limiter = RequestRateLimiter.shared(f"text:{self.options.text_provider}", rpm)
        self.image_limiter = RequestRateLimiter.shared(f"image:{self.options.image_provider}", rpm)
        self.text_fallbacks: list[OpenAIClient] = []
        self.image_fallbacks: list[OpenAIClient] = []

    @classmethod
    def from_options(
        cls,
        options: GenerationOptions,
        text_api_key: str = "",
        image_api_key: str = "",
        timeout: int = 300,
        cache_dir: Path | None = None,
        debug_log_path: Path | None = None,
        debug_enabled: bool | None = None,
        on_api_event: Callable[[dict], None] | None = None,
        build_fallbacks: bool = True,
    ) -> "OpenAIClient":
        options = cls._resolve_provider_configuration(options)
        client = cls(
            timeout=timeout,
            options=options,
            text_api_key=text_api_key,
            image_api_key=image_api_key,
            cache_dir=cache_dir,
            debug_log_path=debug_log_path,
            debug_enabled=debug_enabled,
            on_api_event=on_api_event,
        )
        if build_fallbacks and options.auto_failover:
            client._configure_fallbacks(cache_dir)
        return client

    @staticmethod
    def _resolve_provider_configuration(options: GenerationOptions) -> GenerationOptions:
        """Overlay connection details from ai_providers.json while preserving model/user runtime choices."""
        resolved = replace(options)
        for kind in ("text", "image"):
            provider_id = getattr(resolved, f"{kind}_provider")
            preset = fallback_preset(kind, provider_id)
            if not preset:
                continue
            setattr(resolved, f"{kind}_protocol", preset.protocol)
            setattr(resolved, f"{kind}_base_url", preset.base_url)
            setattr(resolved, f"{kind}_endpoint", preset.endpoint)
            setattr(resolved, f"{kind}_api_key_env", preset.api_key_env)
            setattr(resolved, f"{kind}_extra_headers", preset.extra_headers)
            if not getattr(resolved, f"{kind}_model"):
                setattr(resolved, f"{kind}_model", preset.default_model)
        return resolved

    def _configure_fallbacks(self, cache_dir: Path | None) -> None:
        for kind in ("text", "image"):
            provider_id = getattr(self.options, f"{kind}_fallback_provider", "")
            if not provider_id or provider_id == getattr(self.options, f"{kind}_provider"):
                continue
            preset = fallback_preset(kind, provider_id)
            if not preset or provider_id == "custom":
                continue
            fallback_options = replace(self.options, auto_failover=False)
            setattr(fallback_options, f"{kind}_provider", provider_id)
            setattr(fallback_options, f"{kind}_protocol", preset.protocol)
            setattr(fallback_options, f"{kind}_base_url", preset.base_url)
            setattr(fallback_options, f"{kind}_endpoint", preset.endpoint)
            setattr(fallback_options, f"{kind}_api_key_env", preset.api_key_env)
            setattr(fallback_options, f"{kind}_extra_headers", preset.extra_headers)
            setattr(
                fallback_options,
                f"{kind}_model",
                getattr(self.options, f"{kind}_fallback_model", "") or preset.default_model,
            )
            fallback = OpenAIClient.from_options(
                fallback_options,
                timeout=self.timeout,
                cache_dir=cache_dir,
                debug_log_path=self.api_debug.path,
                debug_enabled=self.api_debug.enabled,
                on_api_event=self.api_debug.on_event,
                build_fallbacks=False,
            )
            getattr(self, f"{kind}_fallbacks").append(fallback)
            fallback.api_debug.context = self.debug_context

    @property
    def text_available(self) -> bool:
        primary = bool(self.text_api_key.strip() and self.options.text_base_url.strip())
        return primary or any(item.text_available for item in self.text_fallbacks)

    @property
    def image_available(self) -> bool:
        primary = bool(self.image_api_key.strip() and self.options.image_base_url.strip())
        return primary or any(item.image_available for item in self.image_fallbacks)

    @property
    def available(self) -> bool:
        """Backwards-compatible alias used by text-generation callers."""
        return self.text_available

    @staticmethod
    def _url(base_url: str, endpoint: str) -> str:
        if endpoint.lower().startswith(("http://", "https://")):
            return endpoint
        return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    @staticmethod
    def _extra_headers(raw: str) -> dict[str, str]:
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIError("附加请求头不是有效 JSON 对象。") from exc
        if not isinstance(value, dict):
            raise OpenAIError("附加请求头必须是 JSON 对象。")
        return {str(key): str(item) for key, item in value.items()}

    def _headers(self, api_key: str, extra_headers: str, content_type: str = "application/json") -> dict[str, str]:
        if not api_key.strip():
            raise OpenAIError("未检测到当前供应商的 API Key，请在 AI 接入页输入或配置对应环境变量。")
        headers = self._extra_headers(extra_headers)
        headers.setdefault("Authorization", f"Bearer {api_key}")
        headers.setdefault("Content-Type", content_type)
        headers.setdefault("Accept", "application/json")
        headers.setdefault("User-Agent", "AmazonImageBrief/2.7")
        return headers

    def _checkpoint(self):
        control = getattr(self, 'request_control', None)
        if control:
            control()

    def _check_cancelled(self):
        from .task_events import check_cancelled
        check_cancelled(getattr(self, 'request_control', None))

    def _retry_wait(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self._checkpoint()
            time.sleep(min(0.2, max(0, until - time.monotonic())))

    def wait_for_retry(self, seconds, **context):
        if seconds <= 0:
            return
        self.api_debug.log('retry_wait', wait_seconds=round(seconds, 1),
                           note='按供应商退避要求等待；支持暂停/继续，不会在后台立即重发。', **context)
        self._retry_wait(seconds)

    def _post_json(
        self,
        base_url: str,
        endpoint: str,
        payload: dict[str, Any],
        api_key: str,
        extra_headers: str,
        provider_label: str,
        limiter: RequestRateLimiter | None = None,
    ) -> dict[str, Any]:
        url = self._url(base_url, endpoint)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self._headers(api_key, extra_headers)
        result: Any = None
        attempts = max(1, int(self.options.max_retries) + 1)
        for attempt in range(attempts):
            delay = min(2 ** attempt, 8)
            self._checkpoint()
            if limiter:
                limiter.acquire(getattr(self, 'request_control', None))
            self._checkpoint()
            request_id = secrets.token_hex(8)
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            self.api_debug.log(
                "request",
                request_id=request_id,
                provider=provider_label,
                attempt=attempt + 1,
                method="POST",
                url=url,
                request_headers=headers,
                request_json=payload,
            )
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    status = getattr(response, "status", response.getcode())
                    response_headers = dict(response.headers.items())
                self._check_cancelled()
                parsed = parse_response_body(raw)
                self.api_debug.log(
                    "response",
                    request_id=request_id,
                    provider=provider_label,
                    method="POST",
                    url=url,
                    http_status=status,
                    response_headers=response_headers,
                    response_json=parsed,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                )
                try:
                    result = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OpenAIError(f"{provider_label} 返回了无法解析的 JSON；请查看 API 调试日志。") from exc
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                exc.close()
                detail = raw.decode("utf-8", errors="replace")
                delay = retry_delay(exc.code, exc.headers, parse_response_body(raw), attempt)
                self.api_debug.log(
                    "response",
                    request_id=request_id,
                    provider=provider_label,
                    method="POST",
                    url=url,
                    http_status=exc.code,
                    response_headers=dict(exc.headers.items()) if exc.headers else {},
                    response_json=parse_response_body(raw),
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    error="HTTPError",
                )
                if exc.code not in TRANSIENT_HTTP or attempt + 1 >= attempts or delay > 3600:
                    raise OpenAIError(f"{provider_label} 返回 HTTP {exc.code}: {detail[:1000]}",
                                      http_status=exc.code, retry_after=delay if exc.code in TRANSIENT_HTTP else 0,
                                      retryable=exc.code in TRANSIENT_HTTP) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                self.api_debug.log(
                    "error",
                    request_id=request_id,
                    provider=provider_label,
                    method="POST",
                    url=url,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    error_type=type(exc).__name__,
                    error=str(getattr(exc, 'reason', exc)),
                )
                if attempt + 1 >= attempts:
                    raise OpenAIError(f"无法连接或等待 {provider_label} 超时: {getattr(exc, 'reason', exc)}", retryable=True, retry_after=delay) from exc
            self.wait_for_retry(delay, request_id=request_id, provider=provider_label, url=url,
                                next_attempt=attempt+2, max_attempts=attempts)
        if not isinstance(result, dict):
            raise OpenAIError(f"{provider_label} 返回结构不是 JSON 对象。")
        return result

    def _get_json(
        self,
        url: str,
        api_key: str,
        extra_headers: str,
        provider_label: str,
        limiter: RequestRateLimiter,
    ) -> dict[str, Any]:
        headers = self._headers(api_key, extra_headers, "application/json")
        request = urllib.request.Request(url, headers=headers, method="GET")
        limiter.acquire()
        request_id = secrets.token_hex(8)
        self.api_debug.log(
            "request",
            request_id=request_id,
            provider=provider_label,
            attempt=1,
            method="GET",
            url=url,
            request_headers=headers,
            request_json=None,
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 30)) as response:
                raw = response.read()
                status = getattr(response, "status", response.getcode())
                response_headers = dict(response.headers.items())
            parsed = parse_response_body(raw)
            self.api_debug.log(
                "response",
                request_id=request_id,
                provider=provider_label,
                method="GET",
                url=url,
                http_status=status,
                response_headers=response_headers,
                response_json=parsed,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
            result = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = raw.decode("utf-8", errors="replace")
            self.api_debug.log(
                "response",
                request_id=request_id,
                provider=provider_label,
                method="GET",
                url=url,
                http_status=exc.code,
                response_headers=dict(exc.headers.items()) if exc.headers else {},
                response_json=parse_response_body(raw),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error="HTTPError",
            )
            raise OpenAIError(f"{provider_label} 连通测试返回 HTTP {exc.code}: {detail[:600]}") from exc
        except urllib.error.URLError as exc:
            self.api_debug.log(
                "error",
                request_id=request_id,
                provider=provider_label,
                method="GET",
                url=url,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
                error=str(exc.reason),
            )
            raise OpenAIError(f"无法连接 {provider_label}: {exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAIError(f"{provider_label} 模型接口返回了无法解析的 JSON；请查看 API 调试日志。") from exc
        if not isinstance(result, dict):
            raise OpenAIError(f"{provider_label} 模型接口返回结构不是 JSON 对象。")
        return result

    @staticmethod
    def _extract_model_ids(response: dict[str, Any]) -> list[str]:
        candidates: Any = response.get("data", response.get("models", response.get("result", [])))
        if isinstance(candidates, dict):
            candidates = candidates.get("data", candidates.get("models", []))
        values: list[str] = []
        for item in candidates if isinstance(candidates, list) else []:
            value = item.get("id") or item.get("model") or item.get("name") if isinstance(item, dict) else item
            if value:
                values.append(str(value))
        return sorted(set(values), key=str.lower)

    def list_models(self, kind: str) -> list[str]:
        if kind not in {"text", "image"}:
            raise ValueError("kind 必须是 text 或 image")
        provider = getattr(self.options, f"{kind}_provider")
        base_url = getattr(self.options, f"{kind}_base_url")
        url = model_catalog_url(provider, kind, base_url)
        if not url:
            raise OpenAIError(f"供应商 {provider} 没有配置模型列表接口。")
        key = self.text_api_key if kind == "text" else self.image_api_key
        headers = getattr(self.options, f"{kind}_extra_headers")
        limiter = self.text_limiter if kind == "text" else self.image_limiter
        response = self._get_json(url, key, headers, f"{kind}供应商 {provider}", limiter)
        models = self._extract_model_ids(response)
        if not models:
            raise OpenAIError("连接成功，但供应商没有返回可识别的模型 ID。")
        return models

    def test_connection(self, kind: str) -> dict[str, Any]:
        started = time.perf_counter()
        models = self.list_models(kind)
        preview = ""
        tested_model = getattr(self.options, f"{kind}_model") or models[0]
        if kind == "text":
            if self.options.text_protocol == "responses":
                payload = {"model": tested_model, "input": "Reply with exactly: OK", "store": False}
            elif self.options.text_protocol == "chat_completions":
                payload = {
                    "model": tested_model,
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "max_tokens": 8,
                    "stream": False,
                }
            else:
                raise OpenAIError(f"不支持的文本协议：{self.options.text_protocol}")
            response = self._post_json(
                self.options.text_base_url,
                self.options.text_endpoint,
                payload,
                self.text_api_key,
                self.options.text_extra_headers,
                f"文案供应商 {self.options.text_provider}",
                self.text_limiter,
            )
            preview = (
                self._extract_responses_text(response)
                if self.options.text_protocol == "responses"
                else self._extract_chat_text(response)
            )
            if not preview:
                raise OpenAIError("模型列表可访问，但测试对话没有返回文本。")
        return {
            "kind": kind,
            "provider": getattr(self.options, f"{kind}_provider"),
            "tested_model": tested_model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model_count": len(models),
            "models": models,
            "preview": preview[:120],
        }

    @staticmethod
    def _extract_responses_text(response: dict[str, Any]) -> str:
        chunks: list[str] = []
        for output in response.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                    chunks.append(str(content.get("text", "")))
        return "\n".join(chunks).strip()

    @staticmethod
    def _extract_chat_text(response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices or not isinstance(choices[0], dict):
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("text")
            ).strip()
        return ""

    @staticmethod
    def _parse_json_text(text: str) -> Any:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as first_error:
            candidates = []
            for opener, closer in (("[", "]"), ("{", "}")):
                start, end = cleaned.find(opener), cleaned.rfind(closer)
                if start >= 0 and end > start:
                    candidates.append(cleaned[start : end + 1])
            for candidate in candidates:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            raise OpenAIError("文本模型返回内容不是有效 JSON。") from first_error

    def _generate_json_once(self, prompt: str, model: str) -> Any:
        if not (self.text_api_key.strip() and self.options.text_base_url.strip()):
            raise OpenAIError(
                f"文案供应商 {self.options.text_provider} 未配置；请设置 {self.options.text_api_key_env} 或输入临时 Key。"
            )
        protocol = self.options.text_protocol
        if protocol == "responses":
            payload = {"model": model, "input": prompt, "store": False}
        elif protocol == "chat_completions":
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        else:
            raise OpenAIError(f"不支持的文本协议：{protocol}")
        response = self._post_json(
            self.options.text_base_url,
            self.options.text_endpoint,
            payload,
            self.text_api_key,
            self.options.text_extra_headers,
            f"文案供应商 {self.options.text_provider}",
            self.text_limiter,
        )
        text = self._extract_responses_text(response) if protocol == "responses" else self._extract_chat_text(response)
        if not text:
            raise OpenAIError("文本接口未返回可解析内容。")
        return self._parse_json_text(text)

    def generate_json(self, prompt: str, model: str) -> Any:
        self._checkpoint()
        cache_key = self.cache.key(
            {
                "type": "text",
                "provider": self.options.text_provider,
                "protocol": self.options.text_protocol,
                "base_url": self.options.text_base_url,
                "model": model,
                "prompt": prompt,
            }
        )
        cached = self.cache.get_json(cache_key)
        if cached is not None:
            self.api_debug.log('cache_hit', provider=self.options.text_provider, model=model, result_json=cached,
                               note='命中本地缓存，本次没有发送 HTTP 请求')
            return cached
        errors: list[str] = []
        last_error = None
        candidates = [self, *self.text_fallbacks]
        for candidate in candidates:
            self._checkpoint()
            candidate.request_control = getattr(self, 'request_control', None)
            candidate.debug_context.update(self.debug_context)
            candidate_model = model if candidate is self else candidate.options.text_model
            try:
                result = candidate._generate_json_once(prompt, candidate_model)
                self.cache.put_json(cache_key, result)
                return result
            except OpenAIError as exc:
                last_error = exc
                errors.append(f"{candidate.options.text_provider}: {exc}")
        raise OpenAIError.aggregate("文案主供应商及备用供应商均失败：" + " | ".join(errors), last_error)

    def analyze_image_json(self, prompt: str, image_path: Path, model: str | None = None) -> Any:
        return self.analyze_images_json(prompt, [image_path], model)

    def analyze_images_json(self, prompt: str, image_paths: list[Path], model: str | None = None, *, max_image_edge=2048) -> Any:
        """All supplied images become actual content parts, not filename-only hints."""
        self._checkpoint()
        if not image_paths or any(not Path(path).is_file() for path in image_paths):
            raise OpenAIError('待分析图片为空或不存在，请检查上传列表。')
        data_urls = [self._vision_data_url(Path(path), max_image_edge) for path in image_paths]
        errors: list[str] = []
        last_error = None
        for candidate in [self, *self.text_fallbacks]:
            self._checkpoint()
            candidate.request_control = getattr(self, 'request_control', None)
            candidate.debug_context.update(self.debug_context)
            selected_model = model if candidate is self and model else candidate.options.text_model
            protocol = candidate.options.text_protocol
            if not (candidate.text_api_key.strip() and candidate.options.text_base_url.strip()):
                continue
            if protocol == "responses":
                payload = {
                    "model": selected_model,
                    "input": [{"role": "user", "content": [
                        {"type": "input_text", "text": prompt},
                        *[{"type": "input_image", "image_url": url} for url in data_urls],
                    ]}],
                    "store": False,
                }
            elif protocol == "chat_completions":
                payload = {
                    "model": selected_model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        *[{"type": "image_url", "image_url": {"url": url}} for url in data_urls],
                    ]}],
                }
            else:
                errors.append(f"{candidate.options.text_provider}: 不支持视觉协议 {protocol}")
                continue
            cache_key = candidate.cache.key({
                "type": "vision_qa",
                "provider": candidate.options.text_provider,
                "protocol": candidate.options.text_protocol,
                "base_url": candidate.options.text_base_url,
                "model": selected_model,
                "prompt": prompt,
                "images": [candidate.cache.file_digest(Path(path)) for path in image_paths],
                "max_image_edge": max_image_edge,
            })
            cached = candidate.cache.get_json(cache_key)
            if cached is not None:
                candidate.api_debug.log('cache_hit', provider=candidate.options.text_provider, model=selected_model,
                                        result_json=cached, note='图文理解命中缓存，未发出HTTP请求')
                return cached
            try:
                response = candidate._post_json(
                    candidate.options.text_base_url,
                    candidate.options.text_endpoint,
                    payload,
                    candidate.text_api_key,
                    candidate.options.text_extra_headers,
                    f"视觉供应商 {candidate.options.text_provider}",
                    candidate.text_limiter,
                )
                text = candidate._extract_responses_text(response) if protocol == "responses" else candidate._extract_chat_text(response)
                result = candidate._parse_json_text(text)
                candidate.cache.put_json(cache_key, result)
                return result
            except OpenAIError as exc:
                last_error = exc
                errors.append(f"{candidate.options.text_provider}: {exc}")
        action = self.debug_context.get('work_type') or '图文理解 / 视觉检查'
        raise OpenAIError.aggregate(action + "失败：" + " | ".join(errors or ["没有可用的视觉文本供应商"]), last_error)

    @staticmethod
    def _vision_data_url(image_path: Path, max_edge=2048) -> str:
        from PIL import ImageOps
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, 'JPEG', quality=90)
        return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')

    @staticmethod
    def _data_url(image_path: Path) -> str:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _find_image_result(response: dict[str, Any]) -> tuple[str, str]:
        data = response.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            if data[0].get("b64_json"):
                return "base64", str(data[0]["b64_json"])
            if data[0].get("url"):
                return "url", str(data[0]["url"])
        images = response.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict):
            if images[0].get("b64_json"):
                return "base64", str(images[0]["b64_json"])
            if images[0].get("url"):
                return "url", str(images[0]["url"])
        output = response.get("output", {})
        if isinstance(output, dict):
            results = output.get("results")
            if isinstance(results, list) and results and isinstance(results[0], dict):
                for key in ("url", "image_url", "image"):
                    if results[0].get(key):
                        return "url", str(results[0][key])
            choices = output.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                content = message.get("content", []) if isinstance(message, dict) else []
                for item in content if isinstance(content, list) else []:
                    if isinstance(item, dict):
                        for key in ("image", "image_url", "url"):
                            if item.get(key):
                                return "url", str(item[key])
        raise OpenAIError("图片接口未返回可识别的 url 或 b64_json。")

    def _download(self, url: str) -> bytes:
        self._check_cancelled()
        if url.startswith("data:") and ";base64," in url:
            return base64.b64decode(url.split(";base64,", 1)[1])
        headers = {"User-Agent": "AmazonImageBrief/2.7", "Accept": "image/*,application/octet-stream"}
        request = urllib.request.Request(url, headers=headers)
        request_id = secrets.token_hex(8)
        self.api_debug.log(
            "request",
            request_id=request_id,
            provider="图片结果下载",
            attempt=1,
            method="GET",
            url=url,
            request_headers=headers,
            request_json=None,
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = getattr(response, "status", response.getcode())
                response_headers = dict(response.headers.items())
            self.api_debug.log(
                "response",
                request_id=request_id,
                provider="图片结果下载",
                method="GET",
                url=url,
                http_status=status,
                response_headers=response_headers,
                response_json=raw,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
            self._check_cancelled()
            return raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            self.api_debug.log(
                "response",
                request_id=request_id,
                provider="图片结果下载",
                method="GET",
                url=url,
                http_status=exc.code,
                response_headers=dict(exc.headers.items()) if exc.headers else {},
                response_json=parse_response_body(raw),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error="HTTPError",
            )
            raise OpenAIError(f"无法下载图片结果：HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            self.api_debug.log(
                "error",
                request_id=request_id,
                provider="图片结果下载",
                method="GET",
                url=url,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
                error=str(exc.reason),
            )
            raise OpenAIError(f"无法下载图片结果：{exc.reason}") from exc

    @staticmethod
    def _save_as_jpeg(raw: bytes, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(BytesIO(raw)) as image:
                rgb = image.convert("RGB")
                temporary = destination.with_suffix('.tmp')
                rgb.save(temporary, "JPEG", quality=94, optimize=True)
                temporary.replace(destination)
        except Exception as exc:
            raise OpenAIError("供应商返回内容不是有效图片。") from exc
        return destination

    def generate_image(
        self,
        prompt: str,
        destination: Path,
        model: str = "gpt-image-2",
        quality: str = "medium",
        ratio: str = "square",
        reference_images: list[str] | None = None,
        mask_path: str | None = None,
        use_cache: bool = True,
    ) -> Path:
        self._checkpoint()
        refs = [Path(item) for item in (reference_images or []) if Path(item).is_file()]
        mask = Path(mask_path) if mask_path and Path(mask_path).is_file() else None
        cache_key = self.cache.key({
            "type": "image",
            "request_schema": "3.0.2-final",
            "provider": self.options.image_provider,
            "protocol": self.options.image_protocol,
            "base_url": self.options.image_base_url,
            "model": model,
            "prompt": prompt,
            "quality": quality,
            "ratio": ratio,
            "references": [self.cache.file_digest(path) for path in refs],
            "mask": self.cache.file_digest(mask) if mask else "",
        })
        if use_cache and self.cache.copy_image_to(cache_key, destination):
            self.api_debug.log('cache_hit', provider=self.options.image_provider, model=model,
                               result_json={'cached_image': str(destination)},
                               note='图片命中本地缓存，本次没有发送 HTTP 请求')
            return destination
        errors: list[str] = []
        last_error = None
        for candidate in [self, *self.image_fallbacks]:
            self._checkpoint()
            candidate.request_control = getattr(self, 'request_control', None)
            candidate.debug_context.update(self.debug_context)
            selected_model = model if candidate is self else candidate.options.image_model
            try:
                result = candidate._generate_image_once(
                    prompt,
                    destination,
                    selected_model,
                    quality,
                    ratio,
                    refs,
                    mask,
                )
                self._check_cancelled()
                if use_cache:
                    self.cache.put_image(cache_key, result)
                return result
            except OpenAIError as exc:
                last_error = exc
                errors.append(f"{candidate.options.image_provider}: {exc}")
        raise OpenAIError.aggregate("图片主供应商及备用供应商均失败：" + " | ".join(errors), last_error)

    def _generate_image_once(
        self,
        prompt: str,
        destination: Path,
        model: str,
        quality: str,
        ratio: str,
        refs: list[Path],
        mask: Path | None,
    ) -> Path:
        if not (self.image_api_key.strip() and self.options.image_base_url.strip()):
            raise OpenAIError(
                f"图片供应商 {self.options.image_provider} 未配置；请设置 {self.options.image_api_key_env} 或输入临时 Key。"
            )
        protocol = self.options.image_protocol
        try:
            validate_image_prompt(prompt, model)
        except ValueError as exc:
            raise OpenAIError(str(exc)) from exc
        if uses_image_edits(self.options, model, refs):
            if len(refs) > 16 and is_gpt_image(model):
                raise OpenAIError('GPT 图片编辑接口最多接收16张输入图片；请使用产品参考拼图或减少参考图。尚未发送请求。')
            response = self._post_image_edit(prompt, model, quality, ratio, refs[0], mask, refs[1:])
        else:
            payload = self._image_payload(protocol, prompt, model, quality, ratio, refs, mask)
            response = self._post_json(
                self.options.image_base_url,
                self.options.image_endpoint,
                payload,
                self.image_api_key,
                self.options.image_extra_headers,
                f"图片供应商 {self.options.image_provider}",
                self.image_limiter,
            )
        kind, value = self._find_image_result(response)
        raw = base64.b64decode(value) if kind == "base64" else self._download(value)
        self._check_cancelled()
        return self._save_as_jpeg(raw, destination)

    def _image_payload(
        self,
        protocol: str,
        prompt: str,
        model: str,
        quality: str,
        ratio: str,
        refs: list[Path],
        mask: Path | None = None,
    ) -> dict[str, Any]:
        standard_size = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}.get(ratio, "1024x1024")
        if protocol in {"openai_images", "openai_images_url"}:
            payload: dict[str, Any] = {"model": model, "prompt": prompt, "size": standard_size}
            if is_gpt_image(model):
                if refs or mask:
                    raise OpenAIError('GPT 带图请求必须使用 /images/edits，不能发送 image / mask 到 generations。')
                payload.update({"quality": quality, "output_format": "jpeg"})
            elif protocol == "openai_images":
                payload.update({"quality": quality, "output_format": "jpeg"})
            else:
                payload["response_format"] = "url"
            if refs:
                payload["image"] = self._data_url(refs[0])
            if mask:
                payload["mask"] = self._data_url(mask)
            return payload
        if protocol == "siliconflow_images":
            image_size = {"square": "1328x1328", "landscape": "1664x928", "portrait": "928x1664"}.get(ratio, "1328x1328")
            payload = {"model": model, "prompt": prompt, "image_size": image_size, "batch_size": 1}
            if refs:
                payload["image"] = self._data_url(refs[0])
            if mask:
                payload["mask"] = self._data_url(mask)
            return payload
        if protocol == "dashscope_wan":
            size = {"square": "1024*1024", "landscape": "1536*1024", "portrait": "1024*1536"}.get(ratio, "1024*1024")
            content: list[dict[str, str]] = [{"text": prompt}]
            if refs:
                content.insert(0, {"image": self._data_url(refs[0])})
            if mask:
                content.insert(0, {"mask": self._data_url(mask)})
            return {
                "model": model,
                "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": {"size": size, "n": 1, "watermark": False, "prompt_extend": True},
            }
        raise OpenAIError(f"不支持的图片协议：{protocol}")

    def _post_image_edit(
        self,
        prompt: str,
        model: str,
        quality: str,
        ratio: str,
        image_path: Path,
        mask_path: Path | None = None,
        extra_image_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        size = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}.get(ratio, "1024x1024")
        boundary = f"----AmazonBrief{secrets.token_hex(12)}"
        body = bytearray()
        form_fields = {"model": model, "prompt": prompt, "quality": quality, "size": size, "output_format": "jpeg"}

        def add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        for key, value in form_fields.items():
            add_field(key, value)
        def add_file(name: str, path: Path) -> None:
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
                    f"Content-Type: {mime}\r\n\r\n"
                ).encode("utf-8")
            )
            body.extend(path.read_bytes())
            body.extend(b"\r\n")

        all_images = [image_path, *(extra_image_paths or [])]
        for path in all_images:
            add_file('image[]' if len(all_images) > 1 else 'image', path)
        if mask_path:
            add_file("mask", mask_path)
        body.extend(f"--{boundary}--\r\n".encode())
        endpoint = self.options.image_endpoint
        endpoint = endpoint.rsplit('/', 1)[0] + '/edits' if endpoint.rstrip('/').endswith('/generations') else '/images/edits'
        url = self._url(self.options.image_base_url, endpoint)
        provider_label = f'图片供应商 {self.options.image_provider} / 图片编辑'
        headers = self._headers(
            self.image_api_key,
            self.options.image_extra_headers,
            f"multipart/form-data; boundary={boundary}",
        )
        request_body = {
            "content_type": "multipart/form-data",
            "fields": form_fields,
            "files": {
                **({'image[]': [{'path': str(path), 'size_bytes': path.stat().st_size} for path in all_images]} if len(all_images) > 1 else {'image': {'path': str(image_path), 'size_bytes': image_path.stat().st_size}}),
                **({"mask": {"path": str(mask_path), "size_bytes": mask_path.stat().st_size}} if mask_path else {}),
            },
        }
        attempts = max(1, int(self.options.max_retries) + 1)
        for attempt in range(attempts):
            delay = min(2 ** attempt, 8)
            self._checkpoint()
            self.image_limiter.acquire(getattr(self, 'request_control', None))
            self._checkpoint()
            request = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
            request_id = secrets.token_hex(8)
            self.api_debug.log(
                "request",
                request_id=request_id,
                provider=provider_label,
                attempt=attempt + 1,
                method="POST",
                url=url,
                request_headers=headers,
                request_json=request_body,
            )
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    status = getattr(response, "status", response.getcode())
                    response_headers = dict(response.headers.items())
                self.api_debug.log(
                    "response",
                    request_id=request_id,
                    provider=provider_label,
                    method="POST",
                    url=url,
                    http_status=status,
                    response_headers=response_headers,
                    response_json=parse_response_body(raw),
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                )
                try:
                    result = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OpenAIError(f"{provider_label}返回了无法解析的 JSON；请查看 API 调试日志。") from exc
                if not isinstance(result, dict):
                    raise OpenAIError(f"{provider_label}返回结构不是 JSON 对象。")
                return result
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                exc.close()
                detail = raw.decode("utf-8", errors="replace")
                delay = retry_delay(exc.code, exc.headers, parse_response_body(raw), attempt)
                self.api_debug.log(
                    "response",
                    request_id=request_id,
                    provider=provider_label,
                    method="POST",
                    url=url,
                    http_status=exc.code,
                    response_headers=dict(exc.headers.items()) if exc.headers else {},
                    response_json=parse_response_body(raw),
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    error="HTTPError",
                )
                if exc.code not in TRANSIENT_HTTP or attempt + 1 >= attempts or delay > 3600:
                    hint = ('；已使用带图 multipart 编辑格式，请确认供应商开放该模型的 /images/edits 能力。'
                            '未自动去掉产品图重试。' if exc.code in {400, 404, 405, 422} else '')
                    raise OpenAIError(f"{provider_label} 返回 HTTP {exc.code}: {detail[:1000]}{hint}",
                                      http_status=exc.code, retry_after=delay if exc.code in TRANSIENT_HTTP else 0,
                                      retryable=exc.code in TRANSIENT_HTTP) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                self.api_debug.log(
                    "error",
                    request_id=request_id,
                    provider=provider_label,
                    method="POST",
                    url=url,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    error_type=type(exc).__name__,
                    error=str(getattr(exc, 'reason', exc)),
                )
                if attempt + 1 >= attempts:
                    raise OpenAIError(f"无法连接 {provider_label}: {getattr(exc, 'reason', exc)}", retryable=True, retry_after=delay) from exc
            self.wait_for_retry(delay, request_id=request_id, provider=provider_label, url=url,
                                next_attempt=attempt+2, max_attempts=attempts)
        raise OpenAIError(f"{provider_label}接口重试后仍失败。")
