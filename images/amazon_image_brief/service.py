from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .ai_client import OpenAIClient, OpenAIError
from .brief_generator import BriefGenerator
from .catalogs import CATALOG_BY_CODE
from .excel_exporter import ExcelExporter
from .image_composer import copy_reference_image, create_german_composite, create_placeholder
from .models import ModuleBrief, ProductProject
from .quality import QualityAnalyzer
from .rules import RuleLibrary


ProgressCallback = Callable[[str, int, int], None]
ControlCallback = Callable[[], None]


class GenerationService:
    def __init__(self, app_root: Path, ai_client: OpenAIClient | None = None) -> None:
        self.app_root = app_root
        self._explicit_client = ai_client is not None
        self.ai_client = ai_client or OpenAIClient(cache_dir=app_root / ".cache" / "ai")
        self.brief_generator = BriefGenerator(self.ai_client)
        self.excel_exporter = ExcelExporter()

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
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options, cache_dir=self.app_root / ".cache" / "ai")
            self.brief_generator = BriefGenerator(self.ai_client)
        errors = project.validate()
        if errors:
            raise ValueError("\n".join(errors))
        if project.options.generate_ai_images and not getattr(self.ai_client, "image_available", self.ai_client.available):
            raise OpenAIError(
                f"已勾选AI图片生成，但图片供应商 {project.options.image_provider} 未配置有效 API Key。"
            )

        briefs = briefs or self.brief_generator.generate(project)
        rule_report = RuleLibrary.load(project.options.rule_library_path).preflight(project, briefs)
        project.preflight_results = rule_report.to_dict()
        if rule_report.errors:
            messages = "\n".join(f"[{item.rule_id}] {item.message}" for item in rule_report.errors)
            raise ValueError(f"Amazon 规则预检未通过：\n{messages}")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = project.normalized_output_root(self.app_root) / f"{timestamp}-{self._slug(project.project_name)}"
        reference_dir = output_dir / "reference_images"
        ai_dir = output_dir / "ai_reference_images"
        german_dir = output_dir / "german_composite_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        quality = QualityAnalyzer(self.ai_client)

        total = len(briefs)
        for index, brief in enumerate(briefs, 1):
            if control:
                control()
            spec = CATALOG_BY_CODE[brief.module_code]
            base_name = self._base_name(index, brief)
            if project.product_image_paths:
                source = project.product_image_paths[(index - 1) % len(project.product_image_paths)]
                brief.reference_image = copy_reference_image(source, reference_dir / f"{base_name}.jpg")
            if progress:
                progress(f"处理 {brief.sequence} {brief.module_name}", index, total)

            ai_path = ai_dir / f"{base_name}.jpg"
            if project.options.generate_ai_images:
                prompt = self._generation_prompt(project, brief)
                try:
                    self.ai_client.generate_image(
                        prompt=prompt,
                        destination=ai_path,
                        model=project.options.image_model,
                        quality=project.options.image_quality,
                        ratio=spec.output_ratio,
                        reference_images=project.product_image_paths,
                    )
                except OpenAIError as exc:
                    create_placeholder(ai_path, brief.module_name, brief.image_prompt, spec.output_ratio)
                    warnings.append(f"{brief.sequence} {brief.module_name}: AI图片生成失败，已改用占位图。原因：{exc}")
                brief.ai_effect_image = str(ai_path)

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

            if project.options.generate_german_composites and ai_path.is_file():
                german_path = german_dir / f"{base_name}-de.jpg"
                create_german_composite(ai_path, german_path, brief.copy_for("de"), skip_text=brief.module_code == "MAIN_WHITE")
                brief.german_composite_image = str(german_path)

            qa_image = Path(brief.german_composite_image or brief.ai_effect_image)
            if (project.options.ocr_check or project.options.image_compliance_check) and qa_image.is_file():
                try:
                    quality.image_ocr_and_policy(project, brief, qa_image)
                except OpenAIError as exc:
                    warnings.append(f"{brief.sequence} {brief.module_name}: OCR/图片合规检查未完成：{exc}")

        if project.options.back_translation_check:
            try:
                quality.back_translate(project, briefs)
            except OpenAIError as exc:
                warnings.append(f"六语回译检查未完成：{exc}")

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
    ) -> dict[str, Any]:
        if not self._explicit_client:
            self.ai_client = OpenAIClient.from_options(project.options, cache_dir=self.app_root / ".cache" / "ai")
        if not getattr(self.ai_client, "image_available", self.ai_client.available):
            raise OpenAIError("局部图片重生成需要配置当前图片供应商的 API Key。")
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
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_name = f"{self._slug(brief.sequence)}-{self._slug(brief.module_name)}-regen-{stamp}"
        ai_path = output_dir / "ai_reference_images" / f"{base_name}.jpg"
        edit_references = project.product_image_paths
        if mask_path and brief.ai_effect_image and Path(brief.ai_effect_image).is_file():
            edit_references = [brief.ai_effect_image]
        self.ai_client.generate_image(
            prompt=self._generation_prompt(project, brief),
            destination=ai_path,
            model=project.options.image_model,
            quality=project.options.image_quality,
            ratio=spec.output_ratio,
            reference_images=edit_references,
            use_cache=False,
            **({"mask_path": mask_path} if mask_path is not None else {}),
        )
        brief.ai_effect_image = str(ai_path)
        quality = QualityAnalyzer(self.ai_client)
        reference = Path(project.product_image_paths[0]) if project.product_image_paths else None
        quality.run_local(brief, reference, ai_path)
        if not project.options.overflow_check:
            brief.overflow_result = "未启用"
        if project.options.generate_german_composites:
            german_path = output_dir / "german_composite_images" / f"{base_name}-de.jpg"
            create_german_composite(
                ai_path,
                german_path,
                brief.copy_for("de"),
                skip_text=brief.module_code == "MAIN_WHITE",
            )
            brief.german_composite_image = str(german_path)
        warnings: list[str] = []
        qa_image = Path(brief.german_composite_image or brief.ai_effect_image)
        if (project.options.ocr_check or project.options.image_compliance_check) and qa_image.is_file():
            try:
                quality.image_ocr_and_policy(project, brief, qa_image)
            except OpenAIError as exc:
                warnings.append(f"{brief.sequence} {brief.module_name}: OCR/图片合规检查未完成：{exc}")
        report = RuleLibrary.load(project.options.rule_library_path).preflight(project, briefs)
        project.preflight_results = report.to_dict()
        return self.refresh_output(project, briefs, output_dir, warnings)

    def refresh_output(
        self,
        project: ProductProject,
        briefs: list[ModuleBrief],
        output_dir: Path,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        excel_path = output_dir / f"{self._slug(project.project_name)}-图片需求表.xlsx"
        self.excel_exporter.export(project, briefs, excel_path)
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
            }
            for item in briefs
        ]
        (output_dir / "qa_report.json").write_text(
            json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        warnings = warnings or []
        if warnings:
            (output_dir / "generation_warnings.txt").write_text("\n".join(warnings), encoding="utf-8")
        ai_dir = output_dir / "ai_reference_images"
        archive = shutil.make_archive(str(output_dir / "ai_reference_images"), "zip", ai_dir) if ai_dir.exists() else ""
        return {
            "output_dir": str(output_dir),
            "excel": str(excel_path),
            "image_dir": str(ai_dir),
            "german_image_dir": str(output_dir / "german_composite_images"),
            "image_zip": archive,
            "warnings": "\n".join(warnings),
            "preflight": project.preflight_results,
        }

    def _base_name(self, index: int, brief: ModuleBrief) -> str:
        return f"{index:02d}-{self._slug(brief.channel)}-{self._slug(brief.module_name)}"

    @staticmethod
    def _brief_to_dict(brief: ModuleBrief) -> dict[str, object]:
        return {
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
        }

    @staticmethod
    def _generation_prompt(project: ProductProject, brief: ModuleBrief) -> str:
        prompt = brief.image_prompt
        if project.options.structure_lock and project.product_image_paths:
            prompt += (
                "\nPRODUCT STRUCTURE LOCK: Treat uploaded product photos as immutable identity references. "
                "Preserve the exact silhouette, shell geometry, wheel count and placement, handle layout, "
                "zipper seams, logo position, material texture and color. Do not invent, remove or deform parts."
            )
        return prompt
