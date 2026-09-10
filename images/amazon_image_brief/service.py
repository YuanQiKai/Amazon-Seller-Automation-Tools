from __future__ import annotations

import json
import re
import shutil
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .ai_client import OpenAIClient, OpenAIError
from .brief_generator import BriefGenerator
from .catalogs import CATALOG_BY_CODE
from .excel_exporter import ExcelExporter
from .final_images import final_image_path
from .models import ModuleBrief, ProductProject
from .quality import QualityAnalyzer
from .rules import RuleLibrary
from .aplus_poster import build_poster, sync_plans
from .product_context import ProductContext, image_context_text
from .image_requests import GPT_IMAGE_PROMPT_LIMIT, validate_image_prompt
from .creative_ai import recipe_for, style_context
from .reference_inputs import reference_inputs
from .task_events import check_cancelled


ProgressCallback = Callable[[str, int, int], None]
ControlCallback = Callable[[], None]


class GenerationService:
    def __init__(self, app_root: Path, ai_client: OpenAIClient | None = None, on_copy_progress=None, on_image_progress=None,
                 copy_control=None, image_control=None, on_poster_progress=None, on_context=None, on_recipe=None,
                 export_excel_on_images=True) -> None:
        self.app_root = app_root
        self._explicit_client = ai_client is not None
        self.ai_client = ai_client or OpenAIClient(cache_dir=app_root / ".cache" / "ai")
        self.on_copy_progress = on_copy_progress
        self.on_image_progress = on_image_progress
        self._partial_results: dict[str, dict] = {}
        self.copy_control = copy_control
        self.image_control = image_control
        self.on_poster_progress = on_poster_progress
        self.on_context, self.on_recipe = on_context, on_recipe
        self.brief_generator = BriefGenerator(self.ai_client, on_progress=on_copy_progress, control=copy_control, on_context=on_context)
        self.excel_exporter = ExcelExporter()
        self.export_excel_on_images = export_excel_on_images

    @contextmanager
    def _api_context(self, brief: ModuleBrief, action: str):
        self._image_checkpoint()
        context = getattr(self.ai_client, 'debug_context', None)
        previous = dict(context) if context is not None else {}
        if context is not None:
            context.update(instance_id=brief.instance_id, module_name=brief.module_name, work_type=action)
        try:
            yield
        finally:
            if context is not None:
                context.clear()
                context.update(previous)

    def _image_checkpoint(self):
        if self.image_control:
            self.image_control()
        self.ai_client.request_control = self.image_control

    def _poster(self, project, briefs, output_dir):
        result = build_poster(project, briefs, output_dir)
        if result and self.on_poster_progress:
            self.on_poster_progress(result)
        return result

    def _references(self, project, brief, briefs):
        style_context(project, brief.instance_id, self.ai_client, self.image_control)
        recipe = recipe_for(project, brief.instance_id)
        brief.style_reference_paths = list(recipe['style_reference_paths'])
        if self.on_recipe:
            self.on_recipe({'id': brief.instance_id, 'recipe': recipe, 'action': 'style'})
        paths, manifest = reference_inputs(project.product_image_paths, brief.style_reference_paths,
                                          self.ai_client, self.app_root / '.cache' / 'reference_inputs')
        self._reference_manifest = manifest
        return paths

    def _reference_debug(self):
        if hasattr(self.ai_client, 'debug_context'):
            self.ai_client.debug_context['reference_inputs'] = deepcopy(getattr(self, '_reference_manifest', {}))

    def _publish_image(self, brief: ModuleBrief, output_dir: Path, stage: str, done: int = 0, total: int = 1):
        from .task_events import check_cancelled
        check_cancelled(self.image_control)
        output_key = str(output_dir.resolve())
        if getattr(self, '_partial_output_key', None) != output_key:
            self._partial_results.clear()
            self._partial_output_key = output_key
        snapshot = deepcopy(brief)
        if stage != 'running':
            self._partial_results[brief.instance_id or brief.module_code] = self._brief_to_dict(snapshot)
            output_dir.mkdir(parents=True, exist_ok=True)
            checkpoint = output_dir / 'partial_briefs.json'
            temporary = checkpoint.with_suffix('.tmp')
            temporary.write_text(json.dumps(list(self._partial_results.values()), ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(checkpoint)
        if self.on_image_progress:
            self.on_image_progress({'instance_id': snapshot.instance_id, 'module_name': snapshot.module_name,
                                   'stage': stage, 'status': snapshot.image_generation_status,
                                   'error': snapshot.image_generation_error, 'done': done, 'total': total,
                                   'output_dir': str(output_dir), 'brief': snapshot})

    @staticmethod
    def _slug(text: str) -> str:
        safe = re.sub(r"[\\/:*?\"<>|\s]+", "-", text.strip())
        return safe.strip("-")[:50] or "amazon-image-brief"

    def run(
        self,
        project: ProductProject,
        progress: ProgressCallback | None = None,
        briefs: list[ModuleBrief] | None = None,
        control: ControlCallback | None = None,
    ) -> dict[str, Any]:
        if self.on_copy_progress or self.on_image_progress:
            project = deepcopy(project)
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options, cache_dir=self.app_root / ".cache" / "ai")
            self.brief_generator = BriefGenerator(self.ai_client, on_progress=self.on_copy_progress, control=self.copy_control or control, on_context=self.on_context)
        errors = project.validate()
        if errors:
            raise ValueError("\n".join(errors))
        if project.options.generate_ai_images and not getattr(self.ai_client, "image_available", self.ai_client.available):
            raise OpenAIError(
                f"已勾选AI图片生成，但图片供应商 {project.options.image_provider} 未配置有效 API Key。"
            )

        if project.options.generate_ai_images or (project.options.optimize_copy_with_ai and self.ai_client.available):
            ProductContext(self.ai_client, self.on_context, self.image_control or self.copy_control or control).prepare(project)
        if briefs is None and project.copy_overrides:
            briefs = self.brief_generator._local_briefs(project)
            self.brief_generator._apply_review_state(project, briefs)
        if not briefs:
            briefs = self.brief_generator.generate(project)
            # generate() works on a snapshot; carry its AI plans into the
            # service project before poster/image synchronization can run.
            project.creative_plans.update({b.instance_id: deepcopy(b.creative_plan) for b in briefs})
        sync_plans(project, briefs)
        rule_report = RuleLibrary.load(project.options.rule_library_path).preflight(project, briefs)
        project.preflight_results = rule_report.to_dict()
        if rule_report.errors:
            messages = "\n".join(f"[{item.rule_id}] {item.message}" for item in rule_report.errors)
            raise ValueError(f"Amazon 规则预检未通过：\n{messages}")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        output_dir = project.normalized_output_root(self.app_root) / f"{timestamp}-{self._slug(project.project_name)}"
        ai_dir = output_dir / "final_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = [f'{b.sequence}: {b.generation_error}' for b in briefs if b.generation_error]
        quality = QualityAnalyzer(self.ai_client)

        total = len(briefs)
        for index, brief in enumerate(briefs, 1):
            self._image_checkpoint()
            if control:
                control()
            spec = CATALOG_BY_CODE[brief.module_code]
            base_name = self._base_name(index, brief)
            if project.product_image_paths:
                source = project.product_image_paths[(index - 1) % len(project.product_image_paths)]
                brief.reference_image = source  # Uploaded input only; no per-module draft/copy output.
            if progress:
                progress(f"处理 {brief.sequence} {brief.module_name}", index, total)

            if '占位' in brief.image_generation_status:
                brief.ai_effect_image = brief.german_composite_image = ''
            previous_image = {'version': brief.image_version, 'ai_effect_image': brief.ai_effect_image,
                              'german_composite_image': brief.german_composite_image, 'review_status': brief.image_review_status,
                              'revision_notes': brief.image_revision_notes, 'archived_at': datetime.now().isoformat(timespec='seconds')}
            brief.image_generation_status = '正在生成'
            brief.image_generation_error = ''
            self._publish_image(brief, output_dir, 'running', index - 1, total)

            ai_path = ai_dir / f"{base_name}.jpg"
            if project.options.generate_ai_images:
                try:
                    with self._api_context(brief, '图片生成'):
                        references = self._references(project, brief, briefs)
                        self._reference_debug()
                        prompt = self._generation_prompt(project, brief)
                        brief.effective_image_prompt = prompt
                        brief.context_fingerprint = project.ai_context.get('fingerprint', '')
                        self.ai_client.generate_image(
                            prompt=prompt,
                            destination=ai_path,
                            model=project.options.image_model,
                            quality=project.options.image_quality,
                            ratio=spec.output_ratio,
                            reference_images=references,
                        )
                except OpenAIError as exc:
                    brief.image_generation_error = str(exc)
                    brief.image_review_status = '不满意-待修改'
                    brief.image_generation_status = '生成失败 / 保留旧结果' if final_image_path(brief) else '生成失败 / 无结果图'
                    warnings.append(f"{brief.sequence} {brief.module_name}: AI图片生成失败，未生成占位图。原因：{exc}")
                    self._publish_image(brief, output_dir, 'failed', index, total)
                    continue
                check_cancelled(self.image_control)
                if previous_image['ai_effect_image'] or previous_image['german_composite_image']:
                    brief.image_history.append(previous_image)
                    brief.image_version += 1
                brief.ai_effect_image = str(ai_path)
                brief.image_language = project.render_language()
                brief.german_composite_image = str(ai_path)  # Legacy field aliases the one final deliverable.
                brief.image_review_status = '待审核'
                brief.image_generation_status = '结果图已就绪 / 质检中'
                self._publish_image(brief, output_dir, 'ai_ready', index - 1, total)

            reference = Path(project.product_image_paths[(index - 1) % len(project.product_image_paths)]) if project.product_image_paths else None
            quality.run_local(brief, reference, ai_path if ai_path.is_file() else None)
            if not project.options.overflow_check:
                brief.overflow_result = "未启用"
            if (
                project.options.structure_lock
                and brief.image_similarity is not None
                and brief.image_similarity < project.options.similarity_threshold
            ):
                warnings.append(
                    f"{brief.sequence} {brief.module_name}: 产品结构相似度 {brief.image_similarity:.1f}/100，"
                    f"低于阈值 {project.options.similarity_threshold:.1f}，请人工复核或使用蒙版局部重生。"
                )

            qa_image = Path(brief.german_composite_image or brief.ai_effect_image)
            if (project.options.ocr_check or project.options.image_compliance_check) and qa_image.is_file():
                try:
                    with self._api_context(brief, 'OCR / 图片合规检查'):
                        quality.image_ocr_and_policy(project, brief, qa_image)
                except OpenAIError as exc:
                    warnings.append(f"{brief.sequence} {brief.module_name}: OCR/图片合规检查未完成：{exc}")

            brief.image_generation_status = '已完成 / 待审核' if project.options.generate_ai_images else '未启用图片生成'
            self._publish_image(brief, output_dir, 'complete', index, total)
            if brief.channel == '高级A+':
                self._poster(project, briefs, output_dir)

        if project.options.back_translation_check:
            self._image_checkpoint()
            try:
                quality.back_translate(project, briefs)
            except OpenAIError as exc:
                warnings.append(f"多语言回译检查未完成：{exc}")

        self._image_checkpoint()
        result = self.refresh_output(project, briefs, output_dir, warnings)
        if progress:
            progress("生成完成", total, total)
        return result

    def regenerate_image(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        module_instance_id: str,
        output_dir: Path,
        mask_path: Path | None = None,
        revision_instruction: str = "",
    ) -> dict[str, Any]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options, cache_dir=self.app_root / ".cache" / "ai")
        self._image_checkpoint()
        sync_plans(project, briefs)
        if not getattr(self.ai_client, "image_available", self.ai_client.available):
            raise OpenAIError("局部图片重生成需要配置当前图片供应商的 API Key。")
        ProductContext(self.ai_client, self.on_context, self.image_control).prepare(project)
        if not project.product_image_paths:
            raise ValueError("局部图片重生成前需要上传产品参考图。")
        brief = next(
            (
                item
                for item in briefs
                if item.instance_id == module_instance_id
                or item.module_code == module_instance_id
            ),
            None,
        )
        if not brief:
            raise ValueError("没有找到要重生成的模块。")
        spec = CATALOG_BY_CODE[brief.module_code]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        base_name = f"{self._slug(brief.sequence)}-{self._slug(brief.module_name)}-regen-{stamp}"
        ai_path = output_dir / "final_images" / f"{base_name}.jpg"
        edit_references = self._references(project, brief, briefs)
        if mask_path and final_image_path(brief):
            edit_references = [str(final_image_path(brief))]
            self._reference_manifest['strategy'] = 'mask-aligned existing image; style supplied as analyzed text'
        previous = {
            "version": brief.image_version,
            "ai_effect_image": brief.ai_effect_image,
            "german_composite_image": brief.german_composite_image,
            "review_status": brief.image_review_status,
            "revision_notes": brief.image_revision_notes,
            "archived_at": datetime.now().isoformat(timespec="seconds"),
        }
        prompt = self._generation_prompt(project, brief)
        if revision_instruction.strip():
            prompt += (
                "\nREVISION REQUEST FROM HUMAN REVIEWER: "
                + revision_instruction.strip()
                + " Preserve every product attribute and approved element that is not explicitly requested to change."
            )
        if '占位' in brief.image_generation_status:
            brief.ai_effect_image = brief.german_composite_image = ''
        brief.image_generation_status = '正在局部重生成' if mask_path else '正在重新生成'
        brief.effective_image_prompt = prompt
        brief.context_fingerprint = project.ai_context.get('fingerprint', '')
        brief.image_generation_error = ''
        self._publish_image(brief, output_dir, 'running')
        try:
            with self._api_context(brief, '蒙版局部重生' if mask_path else '图片重新生成'):
                self._reference_debug()
                self.ai_client.generate_image(
                    prompt=prompt,
                    destination=ai_path,
                    model=project.options.image_model,
                    quality=project.options.image_quality,
                    ratio=spec.output_ratio,
                    reference_images=edit_references,
                    use_cache=False,
                    **({"mask_path": mask_path} if mask_path is not None else {}),
                )
        except (OpenAIError, ValueError) as exc:
            brief.image_generation_status = '失败 / 保留旧图'
            brief.image_generation_error = str(exc)
            self._publish_image(brief, output_dir, 'failed', 1)
            raise
        check_cancelled(self.image_control)
        if previous["ai_effect_image"] or previous["german_composite_image"]:
            brief.image_history.append(previous)
        brief.image_version = max(1, brief.image_version) + 1
        brief.image_review_status = "待复核"
        brief.image_revision_notes = revision_instruction.strip()
        brief.ai_effect_image = str(ai_path)
        brief.image_language = project.render_language()
        brief.german_composite_image = str(ai_path)
        brief.image_generation_status = '结果图已就绪 / 质检中'
        self._publish_image(brief, output_dir, 'ai_ready')
        quality = QualityAnalyzer(self.ai_client)
        reference = Path(project.product_image_paths[0]) if project.product_image_paths else None
        quality.run_local(brief, reference, ai_path)
        if not project.options.overflow_check:
            brief.overflow_result = "未启用"
        warnings: list[str] = []
        qa_image = Path(brief.german_composite_image or brief.ai_effect_image)
        if (project.options.ocr_check or project.options.image_compliance_check) and qa_image.is_file():
            try:
                with self._api_context(brief, 'OCR / 图片合规检查'):
                    quality.image_ocr_and_policy(project, brief, qa_image)
            except OpenAIError as exc:
                warnings.append(f"{brief.sequence} {brief.module_name}: OCR/图片合规检查未完成：{exc}")
        report = RuleLibrary.load(project.options.rule_library_path).preflight(project, briefs)
        project.preflight_results = report.to_dict()
        brief.image_generation_status = '已完成 / 待复核'
        self._publish_image(brief, output_dir, 'complete', 1)
        return self.refresh_output(project, briefs, output_dir, warnings)

    def regenerate_images(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        module_instance_ids: list[str],
        output_dir: Path,
        revision_instructions: dict[str, str],
    ) -> dict[str, Any]:
        """Sequentially regenerate reviewed images as one user-facing batch operation."""
        result: dict[str, Any] = {}
        failures: list[str] = []
        for instance_id in module_instance_ids:
            try:
                result = self.regenerate_image(
                    project,
                    briefs,
                    instance_id,
                    output_dir,
                    revision_instruction=revision_instructions.get(instance_id, ""),
                )
            except (OpenAIError, ValueError) as exc:
                failures.append(f"{instance_id}: {exc}")
        if not result:
            raise OpenAIError("批量图片重生成全部失败：" + " | ".join(failures))
        result["regeneration_failures"] = failures
        return result

    def refresh_output(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        output_dir: Path,
        warnings: list[str] | None = None,
        export_excel: bool | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._image_checkpoint()
        sync_plans(project, briefs)
        poster = self._poster(project, briefs, output_dir)
        poster_rows = {entry['id']: entry for entry in poster.get('chapters', [])}
        for brief in briefs:
            entry = poster_rows.get(brief.instance_id)
            if entry and project.options.generate_german_composites:
                brief.german_composite_image = entry['pc'] if entry['status'] == '就绪' else ''
        for brief in briefs:
            key = brief.instance_id or brief.module_code
            project.creative_plans[key] = brief.creative_plan
            project.image_reviews[key] = {
                "version": brief.image_version,
                "status": brief.image_review_status,
                "notes": brief.image_revision_notes,
                "history": list(brief.image_history),
            }
        report = RuleLibrary.load(project.options.rule_library_path).preflight(project, briefs, check_outputs=True)
        project.preflight_results = report.to_dict()
        # The brief workbook remains exportable so designers can see and repair output errors.
        warnings = list(warnings or []) + [f'输出预检：{item.message}' for item in report.errors] + poster.get('warnings', [])
        excel_path = output_dir / f"{self._slug(project.project_name)}-图片需求表.xlsx"
        write_excel = self.export_excel_on_images if export_excel is None else export_excel
        if write_excel:
            self.excel_exporter.export(project, briefs, excel_path, poster=poster)
        (output_dir / "project.json").write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "briefs.json").write_text(
            json.dumps([self._brief_to_dict(item) for item in briefs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "preflight_report.json").write_text(
            json.dumps(project.preflight_results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        qa_report = [
            {
                "instance_id": item.instance_id,
                "similarity": item.image_similarity,
                "ocr_text": item.ocr_text,
                "back_translation": item.back_translation_result,
                "overflow": item.overflow_result,
                "image_compliance": item.image_qa_result,
                "rule_preflight": item.rule_preflight_result,
                "image_version": item.image_version,
                "image_review_status": item.image_review_status,
                "image_revision_notes": item.image_revision_notes,
            }
            for item in briefs
        ]
        (output_dir / "qa_report.json").write_text(
            json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        warnings = warnings or []
        if warnings:
            (output_dir / "generation_warnings.txt").write_text("\n".join(warnings), encoding="utf-8")
        ai_dir = output_dir / "final_images"
        archive = str(output_dir / 'final_images.zip')
        files = {path for b in briefs if (path := final_image_path(b))}
        files.update(Path(path) for path in poster.get('files', []) if Path(path).is_file() and Path(path).suffix.lower() == '.jpg')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
            for path in sorted(files):
                name = str(path.relative_to(output_dir)) if path.is_relative_to(output_dir) else f'external/{path.name}'
                package.write(path, name)
        return {
            "output_dir": str(output_dir),
            "excel": str(excel_path) if write_excel else '',
            "image_dir": str(ai_dir),
            "german_image_dir": str(ai_dir),
            "image_zip": archive,
            "warnings": "\n".join(warnings),
            "preflight": project.preflight_results,
            "briefs": briefs,
            "aplus_poster": poster,
        }

    def _base_name(self, index: int, brief: ModuleBrief) -> str:
        return f"{index:02d}-{self._slug(brief.channel)}-{self._slug(brief.module_name)}"

    @staticmethod
    def _brief_to_dict(brief: ModuleBrief) -> dict[str, object]:
        return {**asdict(brief),
            "instance_id": brief.instance_id,
            "sequence": brief.sequence,
            "channel": brief.channel,
            "module_code": brief.module_code,
            "module_name": brief.module_name,
            "pc_size": brief.pc_size,
            "mobile_size": brief.mobile_size,
            "design_brief": brief.design_brief,
            "copy": brief.copy,
            "keywords": brief.keywords,
            "compliance_note": brief.compliance_note,
            "reference_image": brief.reference_image,
            "ai_effect_image": brief.ai_effect_image,
            "german_composite_image": brief.german_composite_image,
            "image_prompt": brief.image_prompt,
            "custom_prompt": brief.custom_prompt,
            "copy_version": brief.copy_version,
            "review_status": brief.review_status,
            "review_notes": brief.review_notes,
            "self_check_result": brief.self_check_result,
            "image_similarity": brief.image_similarity,
            "ocr_text": brief.ocr_text,
            "back_translation_result": brief.back_translation_result,
            "overflow_result": brief.overflow_result,
            "image_qa_result": brief.image_qa_result,
            "rule_preflight_result": brief.rule_preflight_result,
            "image_version": brief.image_version,
            "image_review_status": brief.image_review_status,
            "image_revision_notes": brief.image_revision_notes,
            "image_history": brief.image_history,
            "creative_plan": brief.creative_plan,
            "generation_status": brief.generation_status,
            "generation_error": brief.generation_error,
            "image_generation_status": brief.image_generation_status,
            "image_generation_error": brief.image_generation_error,
        }

    @staticmethod
    def _generation_prompt(project: ProductProject, brief: ModuleBrief) -> str:
        from .languages import LANGUAGE_NAMES
        recipe = recipe_for(project, brief.instance_id)
        language = project.render_language()
        language_name = 'German' if language == 'de' else f'{LANGUAGE_NAMES[language]} ({language})'
        native = brief.module_code != 'MAIN_WHITE'
        if native and not brief.copy_for(language).strip():
            raise OpenAIError(f'{brief.module_name} 缺少 {LANGUAGE_NAMES[language]} 文案，请先在文案生成页生成或填写该语言，再生成图片。')
        context_marker = '{__PRODUCT_CONTEXT__}'
        prompt = ('Create one Amazon ecommerce image for ' + brief.module_name + '.\n'
                  'Priority: verified product facts and identity > white-main-image safety > approved exact copy > user image prompt > suggested layout. '
                  'Never invent features or replace the user direction with a generic slogan.\n'
                  'PRODUCT CONTEXT (explicit, no assumed server memory): ' + context_marker + '\n'
                  'USER IMAGE PROMPT: ' + recipe['image_prompt'] + '\n'
                  f'APPROVED {language_name.upper()} COPY (exact lines, do not translate, paraphrase, or add text):\n' + brief.copy_for(language) + '\nEND APPROVED COPY\n'
                  'Module keywords (use only when already in approved copy): ' + json.dumps(brief.keywords, ensure_ascii=False) + '\n'
                  'Style reference analysis (layout/style only, never product facts): ' + json.dumps(recipe.get('style_analysis', {}).get('analyses', []), ensure_ascii=False) + '\n'
                  'Product inputs define identity; style inputs define visual style only. If supplied as a labeled contact sheet, PRODUCT IDENTITY / STYLE ONLY are input roles, not output labels. Never reproduce the sheet, labels, rival brands or products.\n')
        if native:
            layout = {key: value for key, value in brief.creative_plan.items() if key not in ('planning_prompt', 'planning_response')}
            prompt += f'Deliver the FINAL finished image, not a draft, wireframe or blank background. Render the EXACT approved {language_name} lines as integral artwork, mapped to slots in this layout JSON: ' + json.dumps(layout, ensure_ascii=False)
        else:
            prompt += 'Deliver the FINAL white-background product photograph, not a draft. No lettering.\n'
        if brief.module_code == 'MAIN_WHITE':
            prompt += '\nOVERRIDE: Pure white RGB 255 background, actual product only, no text, badges, decorations, props or layout-reference backgrounds.'
        if project.options.structure_lock and project.product_image_paths:
            prompt += (
                "\nPRODUCT STRUCTURE LOCK: Treat uploaded product photos as immutable identity references. "
                "Preserve the exact silhouette, shell geometry, wheel count and placement, handle layout, "
                "zipper seams, logo position, material texture and color. Do not invent, remove or deform parts."
            )
        if brief.creative_plan.get('poster'):
            prompt += '\nCONTINUITY: The original product photo defines immutable identity. Follow the complete chapter story, common visual direction and palette. Chapter continuity comes from this shared art direction and a unified local poster grid; do not invent a different product for later chapters.'
        # Human instructions, exact copy and layout are mandatory and never sliced.
        remaining = GPT_IMAGE_PROMPT_LIMIT - len(prompt) + len(context_marker)
        context = image_context_text(project, budget=remaining)
        prompt = prompt.replace(context_marker, context, 1)
        try:
            validate_image_prompt(prompt, project.options.image_model)
        except ValueError as exc:
            raise OpenAIError(str(exc)) from exc
        return prompt
