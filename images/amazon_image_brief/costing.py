from __future__ import annotations

from dataclasses import dataclass

from .models import ProductProject


@dataclass(slots=True)
class CostEstimate:
    currency: str
    text_cost: float | None
    image_cost: float | None
    input_tokens: int
    output_tokens: int
    image_count: int
    note: str

    @property
    def total(self) -> float | None:
        if self.currency == "MIXED":
            return None
        if self.text_cost is None and self.image_cost is None:
            return None
        return (self.text_cost or 0.0) + (self.image_cost or 0.0)

    def summary(self) -> str:
        money = "费率未配置" if self.total is None else f"约 {self.currency} {self.total:.4f}"
        return (
            f"{money}｜文案输入约 {self.input_tokens:,} tokens，输出约 {self.output_tokens:,} tokens，"
            f"图片 {self.image_count} 张。{self.note}"
        )


# 用于下单前预算，不代表账单。价格来源与复核日期见 README/配置指南。
TEXT_RATES = {
    ("openai", "gpt-5.6-terra"): ("USD", 1.0, 6.0),
    ("openai", "gpt-5.6-sol"): ("USD", 2.0, 10.0),
    ("openai", "gpt-5.6-luna"): ("USD", 0.1, 0.6),
    ("openai", "gpt-6-astra"): ("USD", 5.0, 25.0),
    ("aliyun", "qwen-plus"): ("CNY", 0.8, 2.0),
    ("volcengine", "doubao-seed-2-0-lite-260215"): ("CNY", 0.6, 3.6),
}

IMAGE_RATES = {
    ("volcengine", "doubao-seedream-4-5-251128"): ("CNY", 0.25),
    ("volcengine", "doubao-seedream-4-0-250828"): ("CNY", 0.20),
    # OpenAI 图片按图像 token 计费；这里使用可编辑代码中的保守规划值，并明确标记为估算。
    ("openai", "gpt-image-2", "low"): ("USD", 0.03),
    ("openai", "gpt-image-2", "medium"): ("USD", 0.08),
    ("openai", "gpt-image-2", "high"): ("USD", 0.20),
}


def estimate_project_cost(project: ProductProject, module_count: int | None = None) -> CostEstimate:
    count = module_count if module_count is not None else len(project.normalized_module_instances())
    text_modules = max(0, count - sum(item.module_code == 'MAIN_WHITE' for item in project.normalized_module_instances()))
    input_tokens = text_modules * (2600 + len(project.selling_points + project.functions) + count * 90) if project.options.optimize_copy_with_ai else 0
    locale_count = len(project.active_languages())
    # Includes a Chinese back-translation of each selected target language.
    output_tokens = text_modules * (350 + locale_count * 180 + (locale_count - 1) * 90) if project.options.optimize_copy_with_ai else 0
    if project.options.optimize_copy_with_ai and project.options.ai_plan_before_copy:
        input_tokens += text_modules * (3200 + len(project.selling_points + project.functions))
        output_tokens += text_modules * 1200
    if project.options.back_translation_check:
        input_tokens += count * 900
        output_tokens += count * 500
    if (project.options.ocr_check or project.options.image_compliance_check) and project.options.generate_ai_images:
        input_tokens += count * 1400
        output_tokens += count * 400
    image_count = count if project.options.generate_ai_images else 0

    text_rate = TEXT_RATES.get((project.options.text_provider, project.options.text_model))
    image_rate = IMAGE_RATES.get(
        (project.options.image_provider, project.options.image_model, project.options.image_quality),
        IMAGE_RATES.get((project.options.image_provider, project.options.image_model)),
    )
    currencies = {rate[0] for rate in (text_rate, image_rate) if rate}
    currency = next(iter(currencies)) if len(currencies) == 1 else "MIXED" if currencies else ""
    text_cost = None
    if text_rate:
        text_cost = input_tokens / 1_000_000 * text_rate[1] + output_tokens / 1_000_000 * text_rate[2]
    image_cost = image_count * image_rate[1] if image_rate else None
    note_parts = ["按逐模块多语言文案及版式规划估算；校验不合格时每模块最多额外修正1次，网络重试另计；缓存命中会降低调用量。"]
    note_parts.append('已包含开启时的逐模块自动卖点排版规划；产品上下文/参考图视觉token、单独点击提示词优化/AI重建及重复附带长上下文的额外token尚未计入金额，实际费用以供应商账单为准。')
    if len(currencies) > 1:
        note_parts.append("文案与图片币种不同，未做汇率换算。")
    if not text_rate and project.options.optimize_copy_with_ai:
        note_parts.append("当前文案模型没有内置费率。")
    if not image_rate and project.options.generate_ai_images:
        note_parts.append("当前图片模型没有内置费率。")
    return CostEstimate(currency, text_cost, image_cost, input_tokens, output_tokens, image_count, "".join(note_parts))
