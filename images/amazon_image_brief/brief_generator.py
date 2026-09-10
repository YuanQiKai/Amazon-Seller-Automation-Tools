from __future__ import annotations

import json
import re
from copy import deepcopy
from collections import defaultdict
from typing import Any, Callable

from .ai_client import OpenAIClient, OpenAIError
from .catalogs import CATALOG_BY_CODE
from .creative_planner import plan_modules, plan_description, layout_prompt, local_copy
from .models import LANGUAGES, ModuleBrief, ProductProject
from .product_context import ProductContext, context_text
from .creative_ai import recipe_for, style_context
from .creative_planning_ai import rebuild_plan
from .navigation import instance_name


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
    def __init__(self, ai_client: OpenAIClient | None = None, on_progress: Callable[[dict[str, Any]], None] | None = None, control=None, on_context=None) -> None:
        self._explicit_client = ai_client is not None
        self.ai_client = ai_client or OpenAIClient()
        self.on_progress = on_progress
        self.control = control
        self.on_context = on_context

    def _checkpoint(self):
        if self.control:
            self.control()
        self.ai_client.request_control = self.control

    def _report(self, brief: ModuleBrief, index: int, total: int) -> None:
        from .task_events import check_cancelled
        check_cancelled(self.control)
        if self.on_progress:
            self.on_progress({'instance_id': brief.instance_id, 'module_name': brief.module_name,
                              'focus': brief.creative_plan.get('focus', ''), 'status': brief.generation_status,
                              'error': brief.generation_error, 'done': index, 'total': total,
                              'finished': brief.generation_status not in {'正在生成', '文案校验修正中', '正在规划卖点与排版', '规划完成 / 正在生成文案'},
                              'brief': deepcopy(brief)})

    def generate(self, project: ProductProject) -> list[ModuleBrief]:
        project = deepcopy(project)
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options)
        briefs = self._local_briefs(project)
        self._apply_review_state(project, briefs)
        if project.options.optimize_copy_with_ai and self.ai_client.available:
            self._enhance_with_ai(project, briefs)
        else:
            for index, brief in enumerate(briefs, 1):
                self._checkpoint()
                brief.generation_status = '首图无文案' if brief.module_code == 'MAIN_WHITE' else ('已载入已保存文案' if brief.instance_id in project.copy_overrides else '本地规划 / 待翻译')
                if project.options.optimize_copy_with_ai and not self.ai_client.available:
                    brief.generation_error = '未配置文案模型 Key，未调用 AI；请补充接入设置后重试。'
                self._report(brief, index, len(briefs))
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
        plans = plan_modules(project)
        for index, instance in enumerate(selected_instances):
            code = instance.module_code
            spec = CATALOG_BY_CODE[code]
            channel_counts[spec.channel] += 1
            sequence = f"{spec.channel}-{channel_counts[spec.channel]:02d}"
            plan = plans[instance.instance_id]
            copy = {language: local_copy(plan, language) for language in project.active_languages()}
            if code == "MAIN_WHITE":
                copy = {language: "" for language in project.active_languages()}
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
                    module_name=instance_name(instance, spec.name),
                    pc_size=spec.pc_size,
                    mobile_size=spec.mobile_size,
                    design_brief=design,
                    instance_id=instance.instance_id,
                    parent_id=instance.parent_id,
                    frame_index=instance.frame_index,
                    frame_count=instance.frame_count,
                    style_reference_paths=recipe_for(project, instance.instance_id)['style_reference_paths'],
                    copy=copy,
                    requested_languages=list(project.active_languages()),
                    image_language=project.render_language(),
                    keywords=keyword_map[index],
                    compliance_note=compliance,
                    image_prompt=self._image_prompt(project, spec, keyword_map[index], custom_prompt),
                    custom_prompt=custom_prompt,
                    creative_plan=plan,
                )
            )
            result[-1].design_brief += '\n' + plan_description(plan)
            result[-1].image_prompt += '\n' + layout_prompt(plan)
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
            if override:
                brief.copy.update({language: str(value) for language, value in override.items()})
            versions = project.copy_versions.get(key, project.copy_versions.get(brief.module_code, []))
            if versions:
                brief.copy_version = max(int(item.get("version", 1)) for item in versions)
                brief.chinese_translations = dict(versions[-1].get('chinese_translations', {}))
            brief.review_status = project.review_statuses.get(key, project.review_statuses.get(brief.module_code, "待审核"))
            brief.review_notes = project.review_notes.get(key, project.review_notes.get(brief.module_code, ""))
            brief.self_check_result = project.self_check_results.get(key, project.self_check_results.get(brief.module_code, ""))
            image_review = project.image_reviews.get(key, project.image_reviews.get(brief.module_code, {}))
            if isinstance(image_review, dict):
                brief.image_version = max(1, int(image_review.get("version", 1) or 1))
                brief.image_review_status = str(image_review.get("status", "待审核") or "待审核")
                brief.image_revision_notes = str(image_review.get("notes", ""))
                history = image_review.get("history", [])
                brief.image_history = list(history) if isinstance(history, list) else []
            if brief.module_code == "MAIN_WHITE":
                brief.copy = {language: "" for language in project.active_languages()}

    def _project_context(self, project: ProductProject) -> str:
        fields = ('brand', 'product_name_zh', 'product_name_en', 'category', 'marketplace', 'target_audience',
                  'positioning', 'model', 'material', 'size', 'color', 'weight', 'capacity', 'functions',
                  'selling_points', 'package_contents', 'maintenance', 'warranty', 'certifications',
                  'brand_tone', 'visual_style', 'font_suggestion', 'brand_colors', 'forbidden_claims', 'custom_fields')
        return json.dumps({'product': {key: getattr(project, key) for key in fields},
                           'variants': self.variant_summary(project),
                           'competitors': project.to_dict()['competitors'],
                           'keywords': project.to_dict()['keywords']}, ensure_ascii=False)

    def _enhance_with_ai(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        revision_instruction: str = "",
    ) -> None:
        ProductContext(self.ai_client, self.on_context, self.control).prepare(project)
        # One request per instance makes progress, retry and repeated module isolation observable.
        accepted: list[ModuleBrief] = []
        for index, brief in enumerate(briefs, 1):
            self._checkpoint()
            brief.requested_languages = list(project.active_languages())
            if brief.module_code == 'MAIN_WHITE':
                brief.copy = {language: '' for language in project.active_languages()}
                brief.generation_status = '首图无文案'
                self._report(brief, index, len(briefs))
                continue
            brief.generation_error = ''
            brief.generation_status = '正在生成'
            self._report(brief, index - 1, len(briefs))
            prior = [{'instance_id': item.instance_id, 'focus': item.creative_plan.get('focus'),
                      'headlines': {lang: item.copy_for(lang).splitlines()[0] for lang in project.active_languages() if item.copy_for(lang)}} for item in accepted]
            context = getattr(self.ai_client, 'debug_context', None)
            if context is not None:
                context.update(instance_id=brief.instance_id, module_name=brief.module_name, focus=brief.creative_plan.get('focus', ''))
            try:
                style_context(project, brief.instance_id, self.ai_client, self.control)
                self._plan_before_copy(project, brief, revision_instruction, prior, index-1, len(briefs))
                issue = ''
                for attempt in range(2):
                    self._checkpoint()
                    prompt = self._copy_prompt(project, brief, revision_instruction, prior, issue)
                    brief.effective_copy_prompt = prompt
                    brief.context_fingerprint = project.ai_context.get('fingerprint', '')
                    result = self.ai_client.generate_json(prompt, project.options.text_model)
                    try:
                        self._validate_copy_result(brief, result, accepted)
                        break
                    except (OpenAIError, ValueError, TypeError) as exc:
                        issue = str(exc)
                        if attempt == 1:
                            raise
                        brief.generation_status = '文案校验修正中'
                        self._report(brief, index - 1, len(briefs))
                self._apply_ai_item(brief, result)
                brief.generation_status = 'AI完成 / 待审核'
                accepted.append(brief)
            except (OpenAIError, ValueError, TypeError, KeyError) as exc:
                brief.generation_status = '生成失败 / 保留原稿'
                brief.generation_error = str(exc)
            finally:
                if context is not None:
                    context.clear()
            self._report(brief, index, len(briefs))

    def _plan_before_copy(self, project, brief, instruction='', prior=None, done=0, total=1):
        base = plan_modules(project).get(brief.instance_id, brief.creative_plan)
        if not project.options.ai_plan_before_copy:
            brief.creative_plan = deepcopy(base)
            return
        brief.generation_status = '正在规划卖点与排版'
        self._report(brief, done, total)
        plan = rebuild_plan(self.ai_client, project, brief.instance_id, base, instruction=instruction,
                            prior=prior, control=self.control)
        brief.creative_plan = plan
        project.creative_plans[brief.instance_id] = deepcopy(plan)
        brief.generation_status = '规划完成 / 正在生成文案'
        self._report(brief, done, total)

    def _copy_prompt(self, project, brief, instruction='', prior=None, issue='') -> str:
        from .languages import LANGUAGE_NAMES
        schema = json.dumps({language: '...' for language in project.active_languages()}, ensure_ascii=False)
        translations = json.dumps({language: '逐行中文对照' for language in project.active_languages() if language != 'zh'}, ensure_ascii=False)
        return f'''Rewrite the copy for one Amazon visual module. Return ONLY one JSON object:
{{"instance_id":"{brief.instance_id}", "copy":{schema}, "chinese_translations":{translations},
"design_brief_zh":"...", "compliance_note_zh":"...", "image_prompt_en":"..."}}.
Requested languages only: {json.dumps({code: LANGUAGE_NAMES[code] for code in project.active_languages()}, ensure_ascii=False)}.
Always generate zh for Chinese review. chinese_translations must translate EACH target language back into Chinese,
one line per original line, preserving that language's exact meaning and line order. Do not merely copy a generic summary.
First follow the supplied creative_plan: main selling point, source evidence, specific visual proof,
product position, reserved copy region and PC/mobile reading order. The plan drives BOTH copy and imagery.
Map each copy line to the matching elements[].slot: headline above, smaller supporting line,
then specific product-feature callouts near their assigned part anchors. Respect each box's size and reading order.
For a poster plan, follow the ENTIRE ordered chapter story and global direction: each chapter advances the story,
uses the same voice and terminology, and transitions from the previous focus to the next without repeating a slogan.
Use concise embedded labels; longer explanations belong in the design brief or native A+ text fields.
Write exactly one nonempty line per copy_slots entry, in that order, in every requested language.
Headline: 3–7 words; supporting explanation: 6–14 words; detail/callout: 3–8 words.
For Chinese use equivalent concise lengths. All translations convey the same verified facts.
Use specific product evidence, benefits and instructions, not generic slogans. Do not repeat other module headlines.
If the same feature reappears, develop a distinct angle and visual proof. Q&A uses real questions and answers;
specification modules use real variant data; four-image modules need four individual callouts.
Do not invent absent specifications, guarantees, certifications, tests or brand history. If evidence is missing,
state the gap in the Chinese design brief and use neutral descriptive copy, never placeholder claims in image copy.
Keep copy consistent with the uploaded product's supplied facts and custom direction; no keyword stuffing.
design_brief_zh must explain the main selling point, hierarchy, exact PC/mobile copy/product regions, product angle,
lighting, typography and how each line maps to the composition. image_prompt_en follows the same layout and requests NO rendered text.
Revision: {instruction or 'Build a specific, differentiated, evidence-based visual story.'}
User's per-image COPY PROMPT (honor this direction unless it conflicts with verified facts or white-main-image rules): {recipe_for(project, brief.instance_id)['copy_prompt']}
Product understanding and verified input facts (explicitly attached; do not assume server-side memory): {context_text(project)}
Style reference analysis (layout only, never facts about our product): {json.dumps(recipe_for(project, brief.instance_id).get('style_analysis', {}), ensure_ascii=False)}
Validation issue to correct: {issue or 'None'}
Project: {self._project_context(project)}
Previously accepted headlines to avoid: {json.dumps(prior or [], ensure_ascii=False)}
Module: {json.dumps(self._brief_payload(brief), ensure_ascii=False)}'''

    @staticmethod
    def _validate_copy_result(brief: ModuleBrief, result: Any, accepted: list[ModuleBrief]) -> None:
        if not isinstance(result, dict) or result.get('instance_id', brief.instance_id) != brief.instance_id:
            raise OpenAIError('返回的模块实例 ID 不匹配或不是 JSON 对象。')
        copy = result.get('copy', {})
        expected = len(brief.creative_plan.get('copy_slots', [])) or 4
        for language in brief.languages():
            value = copy.get(language) if isinstance(copy, dict) else None
            if not isinstance(value, str):
                raise OpenAIError(f'缺少 {language} 的完整文案。')
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            if len(lines) != expected:
                raise OpenAIError(f'{language} 应按版式返回 {expected} 行，实际 {len(lines)} 行；不能只返回一句话。')
            normalized = lambda text: re.sub(r'\W+', '', text.casefold())
            headline = normalized(lines[0])
            if any(item.copy_for(language) and normalized(item.copy_for(language).splitlines()[0]) == headline for item in accepted):
                raise OpenAIError(f'{language} 标题与其他模块重复，请按本模块卖点重写。')
        if brief.requested_languages:
            translations = result.get('chinese_translations', {})
            for language in brief.languages():
                if language == 'zh':
                    continue
                value = translations.get(language) if isinstance(translations, dict) else None
                if not isinstance(value, str) or len([line for line in value.splitlines() if line.strip()]) != expected:
                    raise OpenAIError(f'缺少 {language} 的逐行中文对照，请与原文行数对齐。')
        for field in ('design_brief_zh', 'image_prompt_en'):
            if not isinstance(result.get(field), str) or not result[field].strip():
                raise OpenAIError(f'缺少 {field}；文案必须同时交付版式说明和图片 Prompt。')

    @staticmethod
    def _apply_ai_item(brief: ModuleBrief, item: Any) -> None:
        if not isinstance(item, dict):
            return
        copy = item.get("copy", {})
        if isinstance(copy, dict) and all(language in copy for language in brief.languages()):
            brief.copy.update({language: str(copy[language]).strip() for language in brief.languages()})
            brief.chinese_translations.update({code: str(value) for code, value in item.get('chinese_translations', {}).items()})
            brief.review_status = '待审核'
        brief.design_brief = str(item.get("design_brief_zh") or brief.design_brief).strip()
        brief.compliance_note = str(item.get("compliance_note_zh") or brief.compliance_note).strip()
        brief.image_prompt = str(item.get("image_prompt_en") or brief.image_prompt).strip()
        if brief.creative_plan:
            brief.design_brief = brief.design_brief.split('\n【主卖点】', 1)[0] + '\n' + plan_description(brief.creative_plan)
            brief.image_prompt = brief.image_prompt.split('\nAPPROVED VISUAL PLAN', 1)[0] + '\n' + layout_prompt(brief.creative_plan)
        if brief.module_code == "MAIN_WHITE":
            brief.copy = {language: "" for language in brief.languages()}

    def regenerate_copy(
        self,
        project: ProductProject,
        brief: ModuleBrief,
        instruction: str = "",
    ) -> dict[str, Any]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options)
        self._checkpoint()
        brief.requested_languages = list(project.active_languages())
        if not self.ai_client.available:
            raise OpenAIError(
                f"局部文案重生成需要配置文案供应商 {project.options.text_provider} 的 API Key"
                f"（环境变量 {project.options.text_api_key_env} 或 AI 接入页临时 Key）。"
            )
        if brief.module_code == 'MAIN_WHITE':
            return {'copy': {language: '' for language in project.active_languages()}}
        ProductContext(self.ai_client, self.on_context, self.control).prepare(project)
        style_context(project, brief.instance_id, self.ai_client, self.control)
        if not brief.creative_plan:
            brief.creative_plan = plan_modules(project).get(brief.instance_id, {})
        brief.generation_status = '正在生成'
        self._report(brief, 0, 1)
        brief.context_fingerprint = project.ai_context.get('fingerprint', '')
        context = getattr(self.ai_client, 'debug_context', None)
        if context is not None:
            context.update(instance_id=brief.instance_id, module_name=brief.module_name, focus=brief.creative_plan.get('focus', ''))
        try:
            self._plan_before_copy(project, brief, instruction)
            prompt = self._copy_prompt(project, brief, instruction)
            brief.effective_copy_prompt = prompt
            result = self.ai_client.generate_json(prompt, project.options.text_model)
            self._validate_copy_result(brief, result, [])
        except (OpenAIError, ValueError, TypeError) as exc:
            brief.generation_status = '生成失败 / 保留原稿'
            brief.generation_error = str(exc)
            self._report(brief, 1, 1)
            raise
        finally:
            if context is not None:
                context.clear()
        brief.generation_status = 'AI完成 / 待审核'
        brief.generation_error = ''
        self._apply_ai_item(brief, result)
        self._report(brief, 1, 1)
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
            "requested_languages": list(brief.languages()),
            "compliance_note": brief.compliance_note,
            "creative_plan": {key: value for key, value in brief.creative_plan.items() if key not in ('planning_prompt', 'planning_response')},
        }
