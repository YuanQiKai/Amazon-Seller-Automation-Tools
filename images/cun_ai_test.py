from __future__ import annotations

import json
from pathlib import Path

from main import load_local_env
from amazon_image_brief.ai_client import OpenAIClient
from amazon_image_brief.models import GenerationOptions
from amazon_image_brief.providers import TEXT_PROVIDER_PRESETS


def main() -> None:
    app_root = Path(__file__).resolve().parent
    load_local_env(app_root)
    preset = TEXT_PROVIDER_PRESETS["cunai"]
    options = GenerationOptions(
        text_provider=preset.provider_id,
        text_protocol=preset.protocol,
        text_base_url=preset.base_url,
        text_endpoint=preset.endpoint,
        text_api_key_env=preset.api_key_env,
        text_extra_headers=preset.extra_headers,
        text_model=preset.default_model,
        cache_enabled=False,
        api_debug_enabled=True,
    )
    result = OpenAIClient.from_options(
        options,
        debug_log_path=app_root / "logs" / "ai_api_debug.jsonl",
        debug_enabled=True,
        build_fallbacks=False,
    ).test_connection("text")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
