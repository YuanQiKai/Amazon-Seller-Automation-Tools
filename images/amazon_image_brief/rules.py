from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .models import ModuleBrief, ProductProject


@dataclass(slots=True)
class RuleFinding:
    severity: str
    rule_id: str
    scope: str
    message: str
    suggestion: str
    source_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class PreflightReport:
    library_version: str
    last_reviewed: str
    marketplace_code: str
    category_key: str
    findings: list[RuleFinding]

    @property
    def errors(self) -> list[RuleFinding]:
        return [item for item in self.findings if item.severity == "错误"]

    @property
    def warnings(self) -> list[RuleFinding]:
        return [item for item in self.findings if item.severity == "警告"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "library_version": self.library_version,
            "last_reviewed": self.last_reviewed,
            "marketplace_code": self.marketplace_code,
            "category_key": self.category_key,
            "findings": [item.to_dict() for item in self.findings],
        }


class RuleLibrary:
    def __init__(self, data: dict[str, Any], source_path: Path | None = None) -> None:
        self.data = data
        self.source_path = source_path
        self._validate(data)

    @classmethod
    def load(cls, custom_path: str | Path | None = None) -> "RuleLibrary":
        bundled = Path(__file__).parent / "resources" / "amazon_rules.json"
        path = Path(custom_path) if custom_path and Path(custom_path).is_file() else bundled
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        required = {"schema_version", "library_version", "last_reviewed", "global", "marketplaces", "categories"}
        if not isinstance(data, dict) or not required.issubset(data):
            raise ValueError("规则库缺少必要字段。")
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError("暂不支持该规则库 schema_version。")

    @classmethod
    def import_file(cls, source: Path, destination: Path) -> "RuleLibrary":
        data = json.loads(source.read_text(encoding="utf-8"))
        cls._validate(data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(data, destination)

    @classmethod
    def update_from_url(cls, url: str, destination: Path, timeout: int = 30) -> "RuleLibrary":
        if not url.lower().startswith("https://"):
            raise ValueError("在线规则库仅允许 HTTPS URL。")
        request = urllib.request.Request(url, headers={"User-Agent": "AmazonImageBrief/2.7"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ValueError(f"规则库更新失败：{exc}") from exc
        cls._validate(data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(data, destination)

    @property
    def version_label(self) -> str:
        return f"{self.data['library_version']}（复核日期 {self.data['last_reviewed']}）"

    def marketplace_code(self, marketplace: str) -> str:
        match = re.search(r"Amazon\s+([A-Z]{2})", marketplace)
        return match.group(1) if match else "US"

    def category_key(self, category: str) -> str:
        value = category.lower()
        for key, definition in self.data["categories"].items():
            if key == "general":
                continue
            if any(alias.lower() in value for alias in definition.get("aliases", [])):
                return key
        return "general"

    def preflight(self, project: ProductProject, briefs: list[ModuleBrief], check_outputs: bool = False) -> PreflightReport:
        global_rules = self.data["global"]
        sources = self.data.get("sources", [])
        primary_source = sources[0].get("url", "") if sources else ""
        aplus_source = next((item.get("url", "") for item in sources if "A+" in item.get("title", "")), primary_source)
        findings: list[RuleFinding] = []

        main = [item for item in briefs if item.channel == "主图"]
        if main and main[0].module_code != "MAIN_WHITE":
            findings.append(RuleFinding("错误", "IMG-MAIN-001", "全站点/主图", "主图第1张不是白底首图。", "把白底首图移到主图第1位。", primary_source))
        if main and any(main[0].copy.values()):
            findings.append(RuleFinding("错误", "IMG-MAIN-002", "全站点/主图", "白底首图包含可见文案。", "清空首图文案、徽章、Logo叠层和水印。", primary_source))
        max_images = int(global_rules.get("listing_image_max", 9))
        if len(main) > max_images:
            findings.append(RuleFinding("警告", "IMG-COUNT-001", "全站点/主图", f"规划了{len(main)}张主图，超过规则库记录的{max_images}张。", "确认上传界面可用槽位并精简重复图片。", primary_source))

        for image_name in dict.fromkeys([*project.product_image_paths, *project.competitor_image_paths]):
            path = Path(image_name)
            if not path.is_file():
                findings.append(RuleFinding("警告", "IMG-FILE-001", "全站点/素材", f"产品参考图不存在：{path.name}", "重新选择有效的本机图片。", primary_source))
                continue
            try:
                with Image.open(path) as image:
                    longest = max(image.size)
            except OSError:
                findings.append(RuleFinding("警告", "IMG-FILE-002", "全站点/素材", f"无法读取图片：{path.name}", "转换为有效 JPG/PNG 后重新导入。", primary_source))
                continue
            minimum = int(global_rules.get("image_longest_side_min", 500))
            recommended = int(global_rules.get("image_longest_side_zoom_recommended", 1000))
            maximum = int(global_rules.get("image_longest_side_max", 10000))
            if longest < minimum or longest > maximum:
                findings.append(RuleFinding("警告", "REF-SIZE-001", "输入参考素材", f"{path.name} 最长边为{longest}px；仅作生成参考，不阻止生成。", "低清素材可能丢失结构细节，建议补充高清原图；最终输出另行检查尺寸。", primary_source))
            elif longest < recommended:
                findings.append(RuleFinding("警告", "IMG-ZOOM-001", "全站点/素材", f"{path.name} 最长边为{longest}px，可能无法获得理想缩放效果。", f"建议使用最长边至少{recommended}px的图片。", primary_source))

        if check_outputs:
            for brief in briefs:
                for output in dict.fromkeys([brief.ai_effect_image, brief.german_composite_image]):
                    if not output:
                        continue
                    path = Path(output)
                    try:
                        with Image.open(path) as picture:
                            longest = max(picture.size)
                    except (OSError, ValueError):
                        findings.append(RuleFinding('错误', 'OUT-FILE-001', brief.instance_id, f'输出图片无法读取：{path.name}', '重新生成此模块图片。', primary_source))
                        continue
                    minimum = int(global_rules.get('image_longest_side_min', 500))
                    maximum = int(global_rules.get('image_longest_side_max', 10000))
                    if brief.channel == '主图' and not minimum <= longest <= maximum:
                        findings.append(RuleFinding('错误', 'IMG-SIZE-001', brief.instance_id, f'生成的主图 {path.name} 最长边为{longest}px，不在{minimum}–{maximum}px范围。', '重新生成符合尺寸要求的主图；参考素材不会被作为上架成品。', primary_source))

        all_copy = "\n".join(
            [project.selling_points, project.functions, *[value for brief in briefs for value in brief.copy.values()]]
        ).lower()
        matched = sorted({term for term in global_rules.get("forbidden_claim_terms", []) if term.lower() in all_copy})
        if matched:
            findings.append(RuleFinding("警告", "COPY-CLAIM-001", "全站点/文案", f"发现可能需要证据或禁止的表达：{' / '.join(matched)}", "逐项核验证据并改为客观、有限定条件的表述。", primary_source))

        market_code = self.marketplace_code(project.marketplace)
        market = self.data["marketplaces"].get(market_code, {})
        languages = market.get("languages", [])
        supported = set(project.active_languages())
        missing_languages = [item for item in languages if item not in supported]
        if missing_languages:
            findings.append(RuleFinding("警告", "LOCALE-001", f"站点/{market_code}", f"目标站点主要语言包含 {', '.join(missing_languages)}，当前多语言输出未覆盖。", "增加对应语言文案并由母语人员审核。", primary_source))

        category_key = self.category_key(project.category)
        if category_key == "luggage":
            placeholders = ("请填写", "待确认", "-", "")
            invalid_variants = [item.name or item.sku or "未命名变体" for item in project.variants if item.size.strip() in placeholders or item.weight.strip() in placeholders]
            if invalid_variants:
                findings.append(RuleFinding("警告", "LUG-VARIANT-001", "品类/行李箱", f"以下变体缺少可核验尺寸或重量：{' / '.join(invalid_variants)}", "补充实测尺寸、重量和容量后再使用登机/容量卖点。", primary_source))
            if re.search(r"carry.?on|登机|handgepäck", all_copy, re.I) and invalid_variants:
                findings.append(RuleFinding("警告", "LUG-CARRYON-001", "品类/行李箱", "存在登机箱兼容性表达，但部分变体尺寸未确认。", "按目标航空公司限制复核，避免无条件保证。", primary_source))
            if re.search(r"tsa", all_copy, re.I) and "tsa" not in (project.certifications + project.custom_fields.get("锁具", "")).lower():
                findings.append(RuleFinding("警告", "LUG-LOCK-001", "品类/行李箱", "文案提及 TSA，但认证/锁具字段没有对应依据。", "补充锁具实物与许可依据，或删除 TSA 表述。", primary_source))

        for brief in briefs:
            brief.rule_preflight_result = "通过"
        for finding in findings:
            for brief in briefs:
                if finding.scope == brief.instance_id:
                    brief.rule_preflight_result = f'{finding.severity}：{finding.message}'
            if finding.rule_id.startswith("IMG-MAIN") and main:
                main[0].rule_preflight_result = f"{finding.severity}：{finding.message}"
            elif finding.scope.startswith("全站点/文案") or finding.scope.startswith("品类"):
                for brief in briefs:
                    if brief.rule_preflight_result == "通过":
                        brief.rule_preflight_result = f"{finding.severity}：{finding.message}"

        if not findings:
            findings.append(RuleFinding("信息", "PREFLIGHT-OK", f"站点/{market_code} + 品类/{category_key}", "未发现自动规则命中。", "仍需在 Seller Central 上传页人工终审。", aplus_source))
        return PreflightReport(
            self.data["library_version"],
            self.data["last_reviewed"],
            market_code,
            category_key,
            findings,
        )
