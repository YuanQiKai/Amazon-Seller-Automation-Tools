from __future__ import annotations

import os
import re
from pathlib import Path


ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def provider_key_name(kind: str, provider: str) -> str:
    return 'AMAZON_BRIEF_' + kind.upper() + '_' + re.sub(r'[^A-Z0-9_]', '_', provider.upper()) + '_API_KEY'


def read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if ENV_NAME_PATTERN.fullmatch(name):
            values[name] = value.strip().strip('"').strip("'")
    return values


def upsert_env_value(path: Path, name: str, value: str) -> Path:
    if not ENV_NAME_PATTERN.fullmatch(name):
        raise ValueError("API Key 环境变量名无效。")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("API Key 不能为空。")
    if "\n" in cleaned or "\r" in cleaned:
        raise ValueError("API Key 不能包含换行。")

    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    replacement = f"{name}={cleaned}"
    replaced = False
    updated: list[str] = []
    for line in lines:
        if pattern.match(line) and not replaced:
            updated.append(replacement)
            replaced = True
        elif not pattern.match(line):
            updated.append(line)
    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(replacement)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f"{path.name}.tmp"
    temporary.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)
    os.environ[name] = cleaned
    return path
