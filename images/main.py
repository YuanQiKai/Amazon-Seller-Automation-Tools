from __future__ import annotations

import os
from pathlib import Path


def load_local_env(app_root: Path) -> None:
    """Load simple KEY=VALUE pairs without requiring python-dotenv."""
    env_path = app_root / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    load_local_env(root)
    from amazon_image_brief.gui import launch_app

    launch_app(root)
