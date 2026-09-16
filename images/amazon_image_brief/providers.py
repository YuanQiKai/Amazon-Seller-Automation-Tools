from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROVIDER_CONFIG_PATH = Path(__file__).parent / "resources" / "ai_providers.json"


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    provider_id: str
    label: str
    protocol: str
    base_url: str
    endpoint: str
    model_endpoint: str
    api_key_env: str
    models: tuple[str, ...]
    extra_headers: str
    note: str
    routes: tuple[tuple[str, str], ...] = ()

    @property
    def default_model(self) -> str:
        return self.models[0] if self.models else ""


def _load_configuration(path: Path = PROVIDER_CONFIG_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"AI 供应商配置文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI 供应商配置文件 JSON 无效：第 {exc.lineno} 行第 {exc.colno} 列") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeError("AI 供应商配置文件 schema_version 必须为 1。")
    for kind in ("text", "image"):
        if not isinstance(data.get(kind), dict) or not data[kind]:
            raise RuntimeError(f"AI 供应商配置缺少 {kind} 定义。")
    # Ship new providers separately: upgrades must not overwrite locally edited
    # endpoints, models, headers or keys in the original configuration.
    additions = path.with_name("ai_provider_additions.json")
    if additions.exists():
        try:
            extra = json.loads(additions.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("AI 新增供应商配置无法读取，请检查 ai_provider_additions.json。") from exc
        if not isinstance(extra, dict) or extra.get("schema_version") != 1:
            raise RuntimeError("AI 新增供应商配置 schema_version 必须为 1。")
        for kind in ("text", "image"):
            if not isinstance(extra.get(kind, {}), dict):
                raise RuntimeError(f"AI 新增供应商配置 {kind} 必须为对象。")
            for provider_id, raw in extra.get(kind, {}).items():
                data[kind].setdefault(provider_id, raw)
    return data


def _build_presets(kind: str, data: dict[str, Any]) -> dict[str, ProviderPreset]:
    result: dict[str, ProviderPreset] = {}
    required = {"label", "protocol", "base_url", "endpoint", "api_key_env", "models"}
    for provider_id, raw in data[kind].items():
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise RuntimeError(f"AI 供应商 {kind}.{provider_id} 缺少必要参数。")
        result[provider_id] = ProviderPreset(
            provider_id=provider_id,
            label=str(raw["label"]),
            protocol=str(raw["protocol"]),
            base_url=str(raw["base_url"]),
            endpoint=str(raw["endpoint"]),
            model_endpoint=str(raw.get("model_endpoint", "/models")),
            api_key_env=str(raw["api_key_env"]),
            models=tuple(str(item) for item in raw.get("models", []) if str(item).strip()),
            extra_headers=json.dumps(raw.get("extra_headers", {}), ensure_ascii=False) if raw.get("extra_headers") else "",
            note=str(raw.get("note", "")),
            routes=tuple((str(label), str(url).rstrip('/')) for label, url in raw.get("routes", {}).items()),
        )
    return result


PROVIDER_CONFIGURATION = _load_configuration()
TEXT_PROVIDER_PRESETS = _build_presets("text", PROVIDER_CONFIGURATION)
IMAGE_PROVIDER_PRESETS = _build_presets("image", PROVIDER_CONFIGURATION)

# Internal adapters. Connection details live in ai_providers.json and are no longer exposed in the UI.
TEXT_PROTOCOLS = ("responses", "chat_completions")
IMAGE_PROTOCOLS = ("openai_images", "openai_images_url", "siliconflow_images", "dashscope_wan")


def provider_base_url(preset: ProviderPreset, kind: str, selections: dict | None = None) -> str:
    """Only accept a configured route; profile data cannot inject a credential destination."""
    route = (selections or {}).get(f"{kind}:{preset.provider_id}", "")
    return dict(preset.routes).get(route, preset.base_url)


def preset_id_from_label(presets: dict[str, ProviderPreset], label: str) -> str:
    return next((key for key, value in presets.items() if value.label == label), "custom")


def model_catalog_url(provider_id: str, kind: str, base_url: str) -> str:
    """Resolve the model-list endpoint from the editable provider JSON."""
    presets = TEXT_PROVIDER_PRESETS if kind == "text" else IMAGE_PROVIDER_PRESETS
    preset = presets.get(provider_id)
    endpoint = preset.model_endpoint if preset else "/models"
    if endpoint.lower().startswith(("http://", "https://")):
        return endpoint
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}" if base_url.strip() else ""


def fallback_preset(kind: str, provider_id: str) -> ProviderPreset | None:
    presets = TEXT_PROVIDER_PRESETS if kind == "text" else IMAGE_PROVIDER_PRESETS
    return presets.get(provider_id)
