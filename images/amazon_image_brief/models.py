from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


LANGUAGES = ("en", "zh", "de", "fr", "it", "es")


@dataclass(slots=True)
class Competitor:
    asin: str = ""
    url: str = ""
    brand: str = ""
    price: str = ""
    selling_points: str = ""
    visual_notes: str = ""
    opportunity: str = ""


@dataclass(slots=True)
class Keyword:
    term: str
    language: str = "en"
    intent: str = "feature"
    priority: int = 3
    notes: str = ""


@dataclass(slots=True)
class ProductVariant:
    name: str = ""
    sku: str = ""
    size: str = ""
    color: str = ""
    weight: str = ""
    capacity: str = ""
    price: str = ""


@dataclass(slots=True)
class ModuleInstance:
    instance_id: str = ""
    module_code: str = ""
    channel: str = ""
    custom_prompt: str = ""


@dataclass(slots=True, frozen=True)
class ModuleSpec:
    code: str
    channel: str
    name: str
    pc_size: str
    mobile_size: str
    purpose: str
    default_selected: bool = False
    supports_text: bool = True
    output_ratio: str = "square"


@dataclass(slots=True)
class ModuleBrief:
    sequence: str
    channel: str
    module_code: str
    module_name: str
    pc_size: str
    mobile_size: str
    design_brief: str
    instance_id: str = ""
    copy: dict[str, str] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    compliance_note: str = ""
    reference_image: str = ""
    ai_effect_image: str = ""
    german_composite_image: str = ""
    image_prompt: str = ""
    custom_prompt: str = ""
    copy_version: int = 1
    review_status: str = "待审核"
    review_notes: str = ""
    self_check_result: str = ""
    image_similarity: float | None = None
    ocr_text: str = ""
    back_translation_result: str = ""
    overflow_result: str = ""
    image_qa_result: str = ""
    rule_preflight_result: str = ""

    def copy_for(self, language: str) -> str:
        return self.copy.get(language, "")


@dataclass(slots=True)
class GenerationOptions:
    optimize_copy_with_ai: bool = True
    generate_ai_images: bool = True
    generate_german_composites: bool = True
    image_quality: str = "medium"
    text_model: str = "gpt-5.6-terra"
    image_model: str = "gpt-image-2"
    text_provider: str = "openai"
    text_protocol: str = "responses"
    text_base_url: str = "https://api.openai.com/v1"
    text_endpoint: str = "/responses"
    text_api_key_env: str = "OPENAI_API_KEY"
    text_extra_headers: str = ""
    image_provider: str = "openai"
    image_protocol: str = "openai_images"
    image_base_url: str = "https://api.openai.com/v1"
    image_endpoint: str = "/images/generations"
    image_api_key_env: str = "OPENAI_API_KEY"
    image_extra_headers: str = ""
    auto_failover: bool = False
    text_fallback_provider: str = ""
    text_fallback_model: str = ""
    image_fallback_provider: str = ""
    image_fallback_model: str = ""
    max_retries: int = 2
    requests_per_minute: int = 10
    cache_enabled: bool = True
    structure_lock: bool = True
    similarity_threshold: float = 35.0
    ocr_check: bool = False
    back_translation_check: bool = False
    overflow_check: bool = True
    image_compliance_check: bool = False
    rule_library_path: str = ""
    rule_update_url: str = ""
    output_root: str = "outputs"


@dataclass(slots=True)
class ProductProject:
    project_name: str = "新建图片需求项目"
    sku: str = ""
    asin: str = ""
    brand: str = ""
    product_name_zh: str = ""
    product_name_en: str = ""
    category: str = ""
    marketplace: str = "Amazon DE"
    target_audience: str = ""
    positioning: str = ""
    model: str = ""
    size: str = ""
    color: str = ""
    material: str = ""
    weight: str = ""
    capacity: str = ""
    price: str = ""
    functions: str = ""
    selling_points: str = ""
    package_contents: str = ""
    maintenance: str = ""
    warranty: str = ""
    certifications: str = ""
    variants: list[ProductVariant] = field(default_factory=list)
    custom_fields: dict[str, str] = field(default_factory=dict)
    brand_tone: str = "高级、清晰、可信"
    visual_style: str = "真实产品摄影、克制信息图、充足留白"
    font_suggestion: str = "Arial / Helvetica / Amazon Ember风格无衬线字体；数字与尺寸优先清晰易读"
    brand_colors: str = ""
    logo_image_path: str = ""
    forbidden_claims: str = "绝对化、无法验证或暗示官方背书的表述"
    product_image_paths: list[str] = field(default_factory=list)
    competitor_image_paths: list[str] = field(default_factory=list)
    competitors: list[Competitor] = field(default_factory=list)
    keywords: list[Keyword] = field(default_factory=list)
    competitor_input_mode: str = "manual"
    keyword_input_mode: str = "manual"
    imported_market_data_path: str = ""
    selected_modules: dict[str, list[str]] = field(default_factory=dict)
    module_instances: list[ModuleInstance] = field(default_factory=list)
    module_order: list[str] = field(default_factory=list)
    module_prompts: dict[str, str] = field(default_factory=dict)
    copy_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    copy_versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    review_statuses: dict[str, str] = field(default_factory=dict)
    review_notes: dict[str, str] = field(default_factory=dict)
    self_check_results: dict[str, str] = field(default_factory=dict)
    preflight_results: dict[str, Any] = field(default_factory=dict)
    options: GenerationOptions = field(default_factory=GenerationOptions)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProductProject":
        values = dict(raw)
        values["competitors"] = [Competitor(**item) for item in raw.get("competitors", [])]
        values["keywords"] = [Keyword(**item) for item in raw.get("keywords", [])]
        values["variants"] = [ProductVariant(**item) for item in raw.get("variants", [])]
        values["module_instances"] = [ModuleInstance(**item) for item in raw.get("module_instances", [])]
        option_fields = GenerationOptions.__dataclass_fields__.keys()
        values["options"] = GenerationOptions(
            **{key: value for key, value in raw.get("options", {}).items() if key in option_fields}
        )
        allowed = cls.__dataclass_fields__.keys()
        project = cls(**{key: value for key, value in values.items() if key in allowed})
        if not project.module_instances:
            project.module_instances = project.normalized_module_instances()
            project.module_order = [item.instance_id for item in project.module_instances]
        return project

    def normalized_module_instances(self) -> list[ModuleInstance]:
        if self.module_instances:
            by_id = {item.instance_id: item for item in self.module_instances if item.instance_id}
            ordered = [by_id[item_id] for item_id in self.module_order if item_id in by_id]
            ordered.extend(item for item in self.module_instances if item not in ordered)
            return ordered

        available = [
            (channel, code)
            for channel in ("主图", "高级A+", "Brand Story", "品牌旗舰店")
            for code in self.selected_modules.get(channel, [])
        ]
        ordered_pairs: list[tuple[str, str]] = []
        for token in self.module_order:
            match = next((pair for pair in available if pair[1] == token), None)
            if match:
                ordered_pairs.append(match)
                available.remove(match)
        ordered_pairs.extend(available)
        counts: dict[str, int] = {}
        result: list[ModuleInstance] = []
        for channel, code in ordered_pairs:
            counts[code] = counts.get(code, 0) + 1
            instance_id = f"{code}__{counts[code]:02d}"
            result.append(
                ModuleInstance(
                    instance_id=instance_id,
                    module_code=code,
                    channel=channel,
                    custom_prompt=self.module_prompts.get(instance_id, self.module_prompts.get(code, "")),
                )
            )
        return result

    def validate(self) -> list[str]:
        errors: list[str] = []
        required = {
            "项目名称": self.project_name,
            "品牌": self.brand,
            "中文产品名": self.product_name_zh,
            "品类": self.category,
            "目标站点": self.marketplace,
            "核心卖点": self.selling_points,
        }
        for label, value in required.items():
            if not value.strip():
                errors.append(f"{label}不能为空")
        instances = self.normalized_module_instances()
        if not instances and not any(self.selected_modules.values()):
            errors.append("至少选择一个图片模块")
        ordered_main = [item.module_code for item in instances if item.channel == "主图"]
        if not ordered_main:
            main_modules = self.selected_modules.get("主图", [])
            ordered_main = list(main_modules)
        if ordered_main and ordered_main[0] != "MAIN_WHITE":
            errors.append("生成主图时，第1张必须选择“产品白底首图”")
        if self.options.generate_ai_images and not self.product_image_paths:
            errors.append("生成AI图片前建议至少上传一张产品白底图；也可关闭AI图片生成后先导出需求表")
        return errors

    def normalized_output_root(self, app_root: Path) -> Path:
        path = Path(self.options.output_root)
        return path if path.is_absolute() else app_root / path
