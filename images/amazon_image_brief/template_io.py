from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import ProductProject
from .rules import RuleLibrary


def export_batch_project_template(project: ProductProject, destination: Path) -> Path:
    """Export one valid queue-ready project with ignored human-readable template metadata."""
    payload = project.to_dict()
    payload["_template_help"] = {
        "用途": "复制本文件为每个 SKU 单独文件，修改产品、变体、图片路径、模块和 AI 选项后，通过‘添加项目JSON（可多选）’加入队列。",
        "必填字段": ["project_name", "brand", "product_name_zh", "category", "marketplace", "selling_points"],
        "图片路径": "product_image_paths 必须是运行批量任务这台电脑上可访问的绝对路径。",
        "API密钥": "模板不保存密钥；队列统一读取项目 .env 或系统环境变量。",
        "安全提示": "首次使用建议关闭 generate_ai_images 或只保留少量模块试跑，确认模型、费用和输出后再批量执行。",
    }
    ProductProject.from_dict(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def export_rule_library_template(destination: Path, library: RuleLibrary | None = None) -> Path:
    """Export the current validated rule library as an editable schema reference."""
    source = library or RuleLibrary.load()
    payload: dict[str, Any] = deepcopy(source.data)
    RuleLibrary(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
