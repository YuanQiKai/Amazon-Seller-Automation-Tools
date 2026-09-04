from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .ai_client import OpenAIClient, OpenAIError
from .catalogs import CATALOG_BY_CODE
from .models import LANGUAGES, ModuleBrief, ProductProject


LOCAL_COPY = {
    "主图": {
        "en": "{product}\nThoughtful details for reliable everyday use.",
        "zh": "{product}\n用心打磨每个细节，日常使用更安心。",
        "de": "{product}\nDurchdachte Details für zuverlässige Nutzung im Alltag.",
        "fr": "{product}\nDes détails soignés pour une utilisation fiable au quotidien.",
        "it": "{product}\nDettagli curati per un uso quotidiano affidabile.",
        "es": "{product}\nDetalles cuidados para un uso diario fiable.",
    },
    "高级A+": {
        "en": "{product}\nDiscover thoughtful design, function and comfort.",
        "zh": "{product}\n探索兼顾设计、功能与舒适度的产品体验。",
        "de": "{product}\nDurchdachtes Design, Funktion und Komfort entdecken.",
        "fr": "{product}\nDécouvrez un design soigné, fonctionnel et confortable.",
        "it": "{product}\nScopri design curato, funzionalità e comfort.",
        "es": "{product}\nDescubre diseño cuidado, funcionalidad y comodidad.",
    },
    "Brand Story": {
        "en": "{product}\nMade with purpose. Designed for real life.",
        "zh": "{product}\n因明确目标而打造，为真实生活而设计。",
        "de": "{product}\nMit Anspruch gefertigt. Für das echte Leben entwickelt.",
        "fr": "{product}\nConçu avec exigence. Pensé pour la vraie vie.",
        "it": "{product}\nCreato con uno scopo. Progettato per la vita reale.",
        "es": "{product}\nCreado con propósito. Diseñado para la vida real.",
    },
    "品牌旗舰店": {
        "en": "{product}\nExplore products designed around your needs.",
        "zh": "{product}\n探索围绕真实需求打造的产品系列。",
        "de": "{product}\nProdukte entdecken, die sich an Ihren Bedürfnissen orientieren.",
        "fr": "{product}\nDécouvrez des produits conçus autour de vos besoins.",
        "it": "{product}\nScopri prodotti progettati intorno alle tue esigenze.",
        "es": "{product}\nDescubre productos diseñados en torno a tus necesidades.",
    },
}


class BriefGenerator:
    def __init__(self, ai_client: OpenAIClient | None = None) -> None:
        self._explicit_client = ai_client is not None
        self.ai_client = ai_client or OpenAIClient()

    def generate(self, project: ProductProject) -> list[ModuleBrief]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options)
        briefs = self._local_briefs(project)
        if project.options.optimize_copy_with_ai and self.ai_client.available:
            try:
                self._enhance_with_ai(project, briefs)
            except (OpenAIError, ValueError, KeyError, TypeError):
                pass
        self._apply_review_state(project, briefs)
        return briefs

    def selected_codes(self, project: ProductProject) -> list[str]:
        return [item.module_code for item in project.normalized_module_instances() if item.module_code in CATALOG_BY_CODE]

    def selected_instance_ids(self, project: ProductProject) -> list[str]:
        return [item.instance_id for item in project.normalized_module_instances() if item.module_code in CATALOG_BY_CODE]

    def _keyword_map(self, project: ProductProject, count: int) -> list[list[str]]:
        keywords = sorted(
            [item for item in project.keywords if item.term.strip()],
            key=lambda item: (-item.priority, item.term.lower()),
        )
        if not keywords:
            defaults = [
                project.category,
                project.material,
                project.size,
                *[item.strip() for item in project.selling_points.replace("；", ",").split(",")],
            ]
            terms = [item for item in defaults if item]
        else:
            terms = [item.term for item in keywords]
        if not terms:
            terms = [project.product_name_en or project.product_name_zh]
        return [
            [terms[(index + offset) % len(terms)] for offset in range(min(3, len(terms)))]
            for index in range(count)
        ]

    @staticmethod
    def variant_summary(project: ProductProject) -> str:
        if not project.variants:
            return "未建立独立变体；使用通用尺寸/颜色/重量/容量字段。"
        return "；".join(
            f"{item.name or item.sku or '变体'}：尺寸{item.size or '-'}，颜色{item.color or '-'}，"
            f"重量{item.weight or '-'}，容量{item.capacity or '-'}，价格{item.price or '-'}"
            for item in project.variants
        )

    def _local_briefs(self, project: ProductProject) -> list[ModuleBrief]:
        selected_instances = [item for item in project.normalized_module_instances() if item.module_code in CATALOG_BY_CODE]
        keyword_map = self._keyword_map(project, len(selected_instances))
        channel_counts: dict[str, int] = defaultdict(int)
        result: list[ModuleBrief] = []
        variants = self.variant_summary(project)
        for index, instance in enumerate(selected_instances):
            code = instance.module_code
            spec = CATALOG_BY_CODE[code]
            channel_counts[spec.channel] += 1
            sequence = f"{spec.channel}-{channel_counts[spec.channel]:02d}"
            product_name = project.product_name_en or project.product_name_zh or "Product"
            channel_copy = LOCAL_COPY[spec.channel]
            copy = {language: channel_copy[language].format(product=product_name) for language in LANGUAGES}
            if code == "MAIN_WHITE":
                copy = {language: "" for language in LANGUAGES}
            custom_prompt = (
                instance.custom_prompt
                or project.module_prompts.get(instance.instance_id, "")
                or project.module_prompts.get(code, "")
            ).strip()
            design = (
                f"【目标】{spec.purpose}。\n"
                f"【构图】围绕“{project.positioning or project.selling_points}”建立单一视觉焦点；"
                "产品外观、颜色、材质和结构必须与上传的实物图一致。\n"
                f"【变体】{variants}\n"
                f"【排版】PC {spec.pc_size}；移动端 {spec.mobile_size}；字体建议：{project.font_suggestion}；"
                "正文采用短句，德语版本需预留约20%的文字扩展空间。\n"
                f"【关键词】自然使用：{' / '.join(keyword_map[index])}，不得隐藏文字或堆砌无关词。"
            )
            compliance = "图片与文字须真实、可验证；不得使用竞品商标、虚构认证、评分、促销角标或绝对化宣称。"
            if custom_prompt:
                design += f"\n【用户指定构图/风格】{custom_prompt}"
            if code == "MAIN_WHITE":
                design = (
                    "【首图合规】纯白背景（RGB 255/255/255），只展示售卖产品；产品主体建议占画面约85%，"
                    "完整呈现轮廓并保留自然接触阴影。首图不放文字、图标、场景、配件或促销信息。"
                )
                if custom_prompt:
                    design += f"\n【可执行补充】{custom_prompt}。如与白底首图规则冲突，以白底首图规则为准。"
                compliance = "Amazon首图不添加任何可见文案；产品颜色与结构以最终实物白底图为准。"
            result.append(
                ModuleBrief(
                    sequence=sequence,
                    channel=spec.channel,
                    module_code=spec.code,
                    module_name=spec.name,
                    pc_size=spec.pc_size,
                    mobile_size=spec.mobile_size,
                    design_brief=design,
                    instance_id=instance.instance_id,
                    copy=copy,
                    keywords=keyword_map[index],
                    compliance_note=compliance,
                    image_prompt=self._image_prompt(project, spec, keyword_map[index], custom_prompt),
                    custom_prompt=custom_prompt,
                )
            )
        return result

    def _image_prompt(self, project: ProductProject, spec, keywords: list[str], custom_prompt: str) -> str:
        main_image_rule = (
            "Pure white RGB 255 background, product only, centered, full silhouette, no text, no props. "
            "This rule overrides any conflicting user direction."
            if spec.code == "MAIN_WHITE"
            else "Leave a clean copy-safe area; do not render any text because copy will be typeset later."
        )
        prompt = (
            "Use case: Amazon ecommerce creative reference. "
            f"Create a {spec.output_ratio} composition for module '{spec.name}'. "
            f"Product: {project.product_name_en or project.product_name_zh}; category: {project.category}; "
            f"model: {project.model}; material: {project.material}; variants: {self.variant_summary(project)}; "
            f"custom product attributes: {json.dumps(project.custom_fields, ensure_ascii=False)}; "
            f"functions: {project.functions}; selling points: {project.selling_points}. "
            f"Module objective: {spec.purpose}. Audience: {project.target_audience}. Marketplace: {project.marketplace}. "
            f"Visual style: {project.visual_style}; brand tone: {project.brand_tone}; colors: {project.brand_colors}. "
            f"Naturally communicate these concepts: {', '.join(keywords)}. {main_image_rule} "
            "Preserve the exact product design from the reference image. No third-party logos, no watermark, "
            "no unverifiable badges, no distorted anatomy, no invented accessories."
        )
        if custom_prompt:
            prompt += f" User-specific composition and style direction: {custom_prompt}."
        return prompt

    def _apply_review_state(self, project: ProductProject, briefs: list[ModuleBrief]) -> None:
        for brief in briefs:
            key = brief.instance_id or brief.module_code
            override = project.copy_overrides.get(key, project.copy_overrides.get(brief.module_code, {}))
            if all(language in override for language in LANGUAGES):
                brief.copy = {language: str(override[language]) for language in LANGUAGES}
            versions = project.copy_versions.get(key, project.copy_versions.get(brief.module_code, []))
            if versions:
                brief.copy_version = max(int(item.get("version", 1)) for item in versions)
            brief.review_status = project.review_statuses.get(key, project.review_statuses.get(brief.module_code, "待审核"))
            brief.review_notes = project.review_notes.get(key, project.review_notes.get(brief.module_code, ""))
            brief.self_check_result = project.self_check_results.get(key, project.self_check_results.get(brief.module_code, ""))
            if brief.module_code == "MAIN_WHITE":
                brief.copy = {language: "" for language in LANGUAGES}

    def _project_context(self, project: ProductProject) -> str:
        return json.dumps(
            {"product": project.to_dict(), "variant_summary": self.variant_summary(project)},
            ensure_ascii=False,
        )

    def _enhance_with_ai(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        revision_instruction: str = "",
    ) -> None:
        payload = [
            {
                "instance_id": item.instance_id,
                "module_code": item.module_code,
                "channel": item.channel,
                "module_name": item.module_name,
                "pc_size": item.pc_size,
                "mobile_size": item.mobile_size,
                "keywords": item.keywords,
                "custom_prompt": item.custom_prompt,
                "current_design_brief": item.design_brief,
            }
            for item in briefs
        ]
        prompt = f"""
You are an Amazon ecommerce creative strategist for international marketplaces.
Return ONLY a valid JSON array, one object per requested module in the same order.
Each object must contain: instance_id, module_code, design_brief_zh, compliance_note_zh, image_prompt_en,
and copy with exactly these keys: en, zh, de, fr, it, es.

Rules:
- The six language versions must carry the same substantiated meaning. German is the primary image-copy language.
- Use one concise headline and at most one supporting line per visual.
- Integrate supplied keywords only when relevant and natural. Never hide text or stuff keywords.
- MAIN_WHITE has empty copy in every language and remains a pure-white product-only image.
- Never invent certification, test result, dimension, guarantee, award, ranking or competitor claim.
- Design briefs state hierarchy, composition, product angle, scene, lighting, typography and PC/mobile safe areas.
- Respect each custom_prompt unless it conflicts with marketplace compliance or verified product facts.
- Image prompts request no rendered text because copy is typeset separately.
- Global revision instruction: {revision_instruction or 'Improve clarity, specificity and natural marketplace language.'}

Project: {self._project_context(project)}
Modules: {json.dumps(payload, ensure_ascii=False)}
""".strip()
        enhanced = self.ai_client.generate_json(prompt, project.options.text_model)
        if not isinstance(enhanced, list):
            raise ValueError("AI brief response must be a list")
        by_instance = {item.get("instance_id"): item for item in enhanced if isinstance(item, dict)}
        by_code = {item.get("module_code"): item for item in enhanced if isinstance(item, dict)}
        for brief in briefs:
            self._apply_ai_item(brief, by_instance.get(brief.instance_id) or by_code.get(brief.module_code))

    @staticmethod
    def _apply_ai_item(brief: ModuleBrief, item: Any) -> None:
        if not isinstance(item, dict):
            return
        copy = item.get("copy", {})
        if isinstance(copy, dict) and all(language in copy for language in LANGUAGES):
            brief.copy = {language: str(copy[language]).strip() for language in LANGUAGES}
        brief.design_brief = str(item.get("design_brief_zh") or brief.design_brief).strip()
        brief.compliance_note = str(item.get("compliance_note_zh") or brief.compliance_note).strip()
        brief.image_prompt = str(item.get("image_prompt_en") or brief.image_prompt).strip()
        if brief.module_code == "MAIN_WHITE":
            brief.copy = {language: "" for language in LANGUAGES}

    def regenerate_copy(
        self,
        project: ProductProject,
        brief: ModuleBrief,
        instruction: str = "",
    ) -> dict[str, Any]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options)
        if not self.ai_client.available:
            raise OpenAIError(
                f"局部文案重生成需要配置文案供应商 {project.options.text_provider} 的 API Key"
                f"（环境变量 {project.options.text_api_key_env} 或 AI 接入页临时 Key）。"
            )
        prompt = f"""
Rewrite the copy for one Amazon visual module. Return ONLY a valid JSON object with:
copy (exact keys en, zh, de, fr, it, es), design_brief_zh, compliance_note_zh, image_prompt_en.
Keep all six languages semantically equivalent. German is primary. Use a short headline and at most one support line.
Do not invent facts, numbers, certification, guarantees, rankings or competitor claims. Do not keyword-stuff.
MAIN_WHITE must stay empty in all languages. Image prompt must request no rendered text.
User revision instruction: {instruction or 'Improve clarity, specificity and natural marketplace language.'}
Project: {self._project_context(project)}
Module: {json.dumps(self._brief_payload(brief), ensure_ascii=False)}
""".strip()
        result = self.ai_client.generate_json(prompt, project.options.text_model)
        if not isinstance(result, dict):
            raise OpenAIError("局部文案接口未返回有效对象。")
        copy = result.get("copy", {})
        if not isinstance(copy, dict) or not all(language in copy for language in LANGUAGES):
            raise OpenAIError("局部文案缺少六语字段。")
        return result

    def regenerate_all_copy(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        instruction: str = "",
    ) -> list[ModuleBrief]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options)
        if not self.ai_client.available:
            raise OpenAIError(
                f"全局文案重生成需要配置文案供应商 {project.options.text_provider} 的 API Key"
                f"（环境变量 {project.options.text_api_key_env} 或 AI 接入页临时 Key）。"
            )
        self._enhance_with_ai(project, briefs, instruction)
        return briefs

    def self_check(self, project: ProductProject, briefs: list[ModuleBrief]) -> dict[str, str]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options)
        if not self.ai_client.available:
            raise OpenAIError(
                f"AI 文案自查需要配置文案供应商 {project.options.text_provider} 的 API Key"
                f"（环境变量 {project.options.text_api_key_env} 或 AI 接入页临时 Key）。"
            )
        prompt = f"""
Audit the following Amazon image copy. Return ONLY a valid JSON array. Each item must contain:
instance_id, module_code, verdict (通过/建议修改/高风险), issues (array of concise Chinese strings),
suggestions (array of concise Chinese strings).

Check unsupported facts or claims, prohibited absolutes, six-language semantic mismatch, unnatural German,
grammar, ambiguity, excessive length, keyword stuffing, competitor/trademark risk, and consistency with product inputs.
Do not assume missing evidence exists. MAIN_WHITE should contain no copy.
Project: {self._project_context(project)}
Modules: {json.dumps([self._brief_payload(item) for item in briefs], ensure_ascii=False)}
""".strip()
        result = self.ai_client.generate_json(prompt, project.options.text_model)
        if not isinstance(result, list):
            raise OpenAIError("文案自查接口未返回有效列表。")
        checks: dict[str, str] = {}
        for item in result:
            if not isinstance(item, dict) or not (item.get("instance_id") or item.get("module_code")):
                continue
            issues = "；".join(str(value) for value in item.get("issues", []) if value)
            suggestions = "；".join(str(value) for value in item.get("suggestions", []) if value)
            key = str(item.get("instance_id") or item["module_code"])
            if not item.get("instance_id"):
                matches = [brief for brief in briefs if brief.module_code == item.get("module_code")]
                if len(matches) == 1:
                    key = matches[0].instance_id or matches[0].module_code
            checks[key] = (
                f"结论：{item.get('verdict', '建议复核')}\n"
                f"问题：{issues or '未发现明显问题'}\n"
                f"建议：{suggestions or '保持人工终审'}"
            )
        return checks

    @staticmethod
    def _brief_payload(brief: ModuleBrief) -> dict[str, Any]:
        return {
            "instance_id": brief.instance_id,
            "module_code": brief.module_code,
            "channel": brief.channel,
            "module_name": brief.module_name,
            "keywords": brief.keywords,
            "custom_prompt": brief.custom_prompt,
            "design_brief": brief.design_brief,
            "copy": brief.copy,
            "compliance_note": brief.compliance_note,
        }
