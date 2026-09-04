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
from typing import Any

from PIL import Image

from .ai_runtime import AICache, RequestRateLimiter
from .models import GenerationOptions
from .providers import fallback_preset, model_catalog_url


class OpenAIError(RuntimeError):
    """Raised when a configured AI provider request cannot be completed."""


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
    ) -> None:
        self.options = options or GenerationOptions()
        self.timeout = timeout
        shared_key = api_key or ""
        self.text_api_key = text_api_key or shared_key or os.getenv(self.options.text_api_key_env, "")
        self.image_api_key = image_api_key or shared_key or os.getenv(self.options.image_api_key_env, "")
        self.cache = AICache(cache_dir, self.options.cache_enabled)
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
        build_fallbacks: bool = True,
    ) -> "OpenAIClient":
        options = cls._resolve_provider_configuration(options)
        client = cls(
            timeout=timeout,
            options=options,
            text_api_key=text_api_key,
            image_api_key=image_api_key,
            cache_dir=cache_dir,
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
                build_fallbacks=False,
            )
            getattr(self, f"{kind}_fallbacks").append(fallback)

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
        return headers

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
        request = urllib.request.Request(
            self._url(base_url, endpoint),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(api_key, extra_headers),
            method="POST",
        )
        result: Any = None
        attempts = max(1, int(self.options.max_retries) + 1)
        for attempt in range(attempts):
            if limiter:
                limiter.acquire()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {408, 409, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                    raise OpenAIError(f"{provider_label} 返回 HTTP {exc.code}: {detail[:1000]}") from exc
            except urllib.error.URLError as exc:
                if attempt + 1 >= attempts:
                    raise OpenAIError(f"无法连接 {provider_label}: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise OpenAIError(f"{provider_label} 返回了无法解析的 JSON。") from exc
            time.sleep(min(2 ** attempt, 8))
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
        request = urllib.request.Request(
            url,
            headers=self._headers(api_key, extra_headers, "application/json"),
            method="GET",
        )
        limiter.acquire()
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 30)) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAIError(f"{provider_label} 连通测试返回 HTTP {exc.code}: {detail[:600]}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIError(f"无法连接 {provider_label}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise OpenAIError(f"{provider_label} 模型接口返回了无法解析的 JSON。") from exc
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
            return cached
        errors: list[str] = []
        candidates = [self, *self.text_fallbacks]
        for candidate in candidates:
            candidate_model = model if candidate is self else candidate.options.text_model
            try:
                result = candidate._generate_json_once(prompt, candidate_model)
                self.cache.put_json(cache_key, result)
                return result
            except OpenAIError as exc:
                errors.append(f"{candidate.options.text_provider}: {exc}")
        raise OpenAIError("文案主供应商及备用供应商均失败：" + " | ".join(errors))

    def analyze_image_json(self, prompt: str, image_path: Path, model: str | None = None) -> Any:
        """Run OCR/visual QA through a vision-capable text provider."""
        if not image_path.is_file():
            raise OpenAIError(f"待分析图片不存在：{image_path}")
        data_url = self._data_url(image_path)
        errors: list[str] = []
        for candidate in [self, *self.text_fallbacks]:
            selected_model = model if candidate is self and model else candidate.options.text_model
            protocol = candidate.options.text_protocol
            if not (candidate.text_api_key.strip() and candidate.options.text_base_url.strip()):
                continue
            if protocol == "responses":
                payload = {
                    "model": selected_model,
                    "input": [{"role": "user", "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url},
                    ]}],
                    "store": False,
                }
            elif protocol == "chat_completions":
                payload = {
                    "model": selected_model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
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
                "image": candidate.cache.file_digest(image_path),
            })
            cached = candidate.cache.get_json(cache_key)
            if cached is not None:
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
                errors.append(f"{candidate.options.text_provider}: {exc}")
        raise OpenAIError("OCR/视觉检查失败：" + " | ".join(errors or ["没有可用的视觉文本供应商"]))

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
        if url.startswith("data:") and ";base64," in url:
            return base64.b64decode(url.split(";base64,", 1)[1])
        request = urllib.request.Request(url, headers={"User-Agent": "AmazonImageBrief/2.4"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise OpenAIError(f"无法下载图片结果：{exc.reason}") from exc

    @staticmethod
    def _save_as_jpeg(raw: bytes, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(BytesIO(raw)) as image:
                rgb = image.convert("RGB")
                rgb.save(destination, "JPEG", quality=94, optimize=True)
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
        refs = [Path(item) for item in (reference_images or []) if Path(item).is_file()]
        mask = Path(mask_path) if mask_path and Path(mask_path).is_file() else None
        cache_key = self.cache.key({
            "type": "image",
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
            return destination
        errors: list[str] = []
        for candidate in [self, *self.image_fallbacks]:
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
                if use_cache:
                    self.cache.put_image(cache_key, result)
                return result
            except OpenAIError as exc:
                errors.append(f"{candidate.options.image_provider}: {exc}")
        raise OpenAIError("图片主供应商及备用供应商均失败：" + " | ".join(errors))

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
        if protocol == "openai_images" and refs and self.options.image_provider == "openai":
            response = self._post_image_edit(prompt, model, quality, ratio, refs[0], mask)
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
            if protocol == "openai_images":
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
    ) -> dict[str, Any]:
        size = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}.get(ratio, "1024x1024")
        boundary = f"----AmazonBrief{secrets.token_hex(12)}"
        body = bytearray()

        def add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        for key, value in {"model": model, "prompt": prompt, "quality": quality, "size": size, "output_format": "jpeg"}.items():
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

        add_file("image", image_path)
        if mask_path:
            add_file("mask", mask_path)
        body.extend(f"--{boundary}--\r\n".encode())
        endpoint = "/images/edits"
        request = urllib.request.Request(
            self._url(self.options.image_base_url, endpoint),
            data=bytes(body),
            headers=self._headers(
                self.image_api_key,
                self.options.image_extra_headers,
                f"multipart/form-data; boundary={boundary}",
            ),
            method="POST",
        )
        attempts = max(1, int(self.options.max_retries) + 1)
        for attempt in range(attempts):
            self.image_limiter.acquire()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise OpenAIError("OpenAI 图片编辑返回结构不是 JSON 对象。")
                return result
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {408, 409, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                    raise OpenAIError(f"OpenAI 图片编辑返回 HTTP {exc.code}: {detail[:1000]}") from exc
            except urllib.error.URLError as exc:
                if attempt + 1 >= attempts:
                    raise OpenAIError(f"无法连接 OpenAI 图片编辑接口: {exc.reason}") from exc
            time.sleep(min(2 ** attempt, 8))
        raise OpenAIError("OpenAI 图片编辑接口重试后仍失败。")
