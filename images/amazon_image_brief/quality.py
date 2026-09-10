from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageFont, ImageOps, ImageStat

from .ai_client import OpenAIClient, OpenAIError
from .models import LANGUAGES, ModuleBrief, ProductProject


def _dhash(image: Image.Image, size: int = 16) -> int:
    gray = ImageOps.grayscale(image).resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.get_flattened_data() if hasattr(gray, "get_flattened_data") else gray.getdata())
    value = 0
    for row in range(size):
        for column in range(size):
            left = pixels[row * (size + 1) + column]
            right = pixels[row * (size + 1) + column + 1]
            value = (value << 1) | int(left > right)
    return value


def image_similarity(reference: Path, generated: Path) -> float:
    """Approximate structural similarity using perceptual edges and grayscale distribution."""
    if not reference.is_file() or not generated.is_file():
        return 0.0
    with Image.open(reference) as left_image, Image.open(generated) as right_image:
        left = ImageOps.fit(left_image.convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS)
        right = ImageOps.fit(right_image.convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS)
        left_hash, right_hash = _dhash(left), _dhash(right)
        bits = 16 * 16
        hash_score = 1 - ((left_hash ^ right_hash).bit_count() / bits)
        left_gray, right_gray = ImageOps.grayscale(left), ImageOps.grayscale(right)
        left_hist, right_hist = left_gray.histogram(), right_gray.histogram()
        overlap = sum(min(a, b) for a, b in zip(left_hist, right_hist)) / max(1, sum(left_hist))
        edge_left = ImageChops.difference(left_gray, left_gray.filter(ImageFilter.GaussianBlur(2)))
        edge_right = ImageChops.difference(right_gray, right_gray.filter(ImageFilter.GaussianBlur(2)))
        edge_delta = abs(ImageStat.Stat(edge_left).mean[0] - ImageStat.Stat(edge_right).mean[0]) / 255
        score = (hash_score * 0.60 + overlap * 0.30 + max(0.0, 1 - edge_delta) * 0.10) * 100
        return round(max(0.0, min(100.0, score)), 1)


def _parse_size(value: str) -> tuple[int, int]:
    match = re.search(r"(\d{3,5})\s*[x×*]\s*(\d{3,5})", value)
    return (int(match.group(1)), int(match.group(2))) if match else (1464, 600)


def _font(size: int = 38) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_overflow_report(brief: ModuleBrief) -> str:
    if brief.creative_plan.get('pc', {}).get('elements'):
        from .typography import typeset
        risks = []
        for device in ('pc', 'mobile'):
            size = ((1464, 600) if device == 'pc' else (1200, 900)) if brief.creative_plan.get('poster') else _parse_size(brief.pc_size if device == 'pc' else brief.mobile_size)
            for language in brief.languages():
                try:
                    typeset(size, brief.copy_for(language), brief.creative_plan, device, validate_only=True)
                except ValueError as exc:
                    risks.append(f'{language}: {exc}')
        return '；'.join(risks) if risks else 'PC / 移动端逐文本框多语言排版检查通过（实际图片仍需人工核对部位与可读性）'
    width, height = _parse_size(brief.pc_size)
    safe_width = max(240, int(width * (0.45 if width / max(1, height) > 1.4 else 0.78)))
    safe_height = max(120, int(height * 0.42))
    box = brief.creative_plan.get('pc', {}).get('text_box', [])
    if len(box) == 4:
        safe_width = max(1, int(width * box[2] * 0.86))
        safe_height = max(1, int(height * box[3] * 0.86))
    font = _font(max(24, min(46, int(height * 0.07))))
    risks: list[str] = []
    for language in brief.languages():
        text = brief.copy_for(language).strip()
        if not text:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        widths = [font.getbbox(line)[2] - font.getbbox(line)[0] for line in lines]
        line_height = (font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) * 1.35
        estimated_lines = sum(max(1, math.ceil(item / safe_width)) for item in widths)
        if max(widths or [0]) > safe_width * 1.35 or estimated_lines * line_height > safe_height:
            risks.append(f"{language}: 预计{estimated_lines}行，可能超出{safe_width}×{safe_height}px安全区")
    return "；".join(risks) if risks else "未发现明显文字溢出风险"


def local_image_compliance(brief: ModuleBrief, image_path: Path) -> str:
    if not image_path.is_file():
        return "未生成图片，无法检查"
    findings: list[str] = []
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        longest = max(rgb.size)
        if longest < 1000:
            findings.append(f"最长边{longest}px，小于建议的1000px")
        if longest > 10000:
            findings.append(f"最长边{longest}px，超过10000px")
        if brief.module_code == "MAIN_WHITE":
            sample = ImageOps.fit(rgb, (100, 100), method=Image.Resampling.BILINEAR)
            pixels = list(sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata())
            white_ratio = sum(1 for red, green, blue in pixels if red >= 248 and green >= 248 and blue >= 248) / len(pixels)
            if white_ratio < 0.35:
                findings.append(f"白色像素占比约{white_ratio:.0%}，主图背景可能不是纯白")
    return "；".join(findings) if findings else "本地尺寸与主图背景检查通过"


class QualityAnalyzer:
    def __init__(self, ai_client: OpenAIClient) -> None:
        self.ai_client = ai_client

    def back_translate(self, project: ProductProject, briefs: list[ModuleBrief]) -> None:
        if not briefs or not self.ai_client.text_available:
            return
        payload = [
            {"instance_id": item.instance_id, "module_code": item.module_code, "copy": item.copy}
            for item in briefs
        ]
        prompt = f"""
Back-translate each supplied non-zh language into Chinese and compare it with the supplied zh copy.
Return ONLY a JSON array. Each item: instance_id, back_translations (keys matching the supplied non-zh language codes),
semantic_score_0_100, issues_zh (array), recommendation_zh.
Do not rewrite the copy. Product facts: {json.dumps(project.to_dict(), ensure_ascii=False)}
Modules: {json.dumps(payload, ensure_ascii=False)}
""".strip()
        result = self.ai_client.generate_json(prompt, project.options.text_model)
        if not isinstance(result, list):
            raise OpenAIError("多语言回译接口未返回列表。")
        by_id = {str(item.get("instance_id")): item for item in result if isinstance(item, dict)}
        for brief in briefs:
            item = by_id.get(brief.instance_id)
            if not item:
                continue
            translations = item.get("back_translations", {})
            brief.back_translation_result = (
                f"语义分：{item.get('semantic_score_0_100', '-')}；"
                f"问题：{'；'.join(map(str, item.get('issues_zh', []))) or '无'}；"
                f"建议：{item.get('recommendation_zh', '人工复核')}；"
                f"回译：{json.dumps(translations, ensure_ascii=False)}"
            )

    def image_ocr_and_policy(self, project: ProductProject, brief: ModuleBrief, image_path: Path) -> None:
        if not self.ai_client.text_available or not image_path.is_file():
            return
        expected = brief.copy_for(brief.image_language) if image_path == Path(brief.german_composite_image or "") else ""
        prompt = f"""
Inspect this Amazon creative. Return ONLY JSON with keys:
detected_text (array), unexpected_text (array), spelling_issues (array),
policy_risks_zh (array), verdict_zh.
Expected text (language {brief.image_language}): {expected!r}. MAIN_WHITE must contain no visible text, logo overlay,
watermark, badge or promotional graphic. Other images may contain only supplied copy and verified facts.
Module: {brief.module_code}; marketplace: {project.marketplace}; category: {project.category}.
""".strip()
        result = self.ai_client.analyze_image_json(prompt, image_path, project.options.text_model)
        if not isinstance(result, dict):
            raise OpenAIError("OCR/图片合规接口未返回对象。")
        brief.ocr_text = " / ".join(map(str, result.get("detected_text", [])))
        ai_result = (
            f"结论：{result.get('verdict_zh', '人工复核')}；"
            f"意外文字：{' / '.join(map(str, result.get('unexpected_text', []))) or '无'}；"
            f"拼写：{' / '.join(map(str, result.get('spelling_issues', []))) or '无'}；"
            f"风险：{' / '.join(map(str, result.get('policy_risks_zh', []))) or '无'}"
        )
        brief.image_qa_result = f"{brief.image_qa_result}；AI检查：{ai_result}" if brief.image_qa_result else ai_result

    def run_local(self, brief: ModuleBrief, reference: Path | None, generated: Path | None) -> None:
        brief.overflow_result = text_overflow_report(brief)
        if generated and generated.is_file():
            local = local_image_compliance(brief, generated)
            if reference and reference.is_file():
                brief.image_similarity = image_similarity(reference, generated)
                local += f"；与产品参考图结构相似度约{brief.image_similarity:.1f}/100"
            brief.image_qa_result = local
