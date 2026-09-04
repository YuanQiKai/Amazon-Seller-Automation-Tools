from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .catalogs import CATALOG_BY_CODE
from .models import ModuleBrief, ProductProject


NAVY = "1E2A33"
AMBER = "D88A2A"
STONE = "F4F1EA"
WARM_WHITE = "FCFBF7"
HEADER = "E8D8BF"
BORDER = "C8C2B8"
YELLOW = "FFF2CC"
GREEN = "E2F0D9"
RED = "FCE4D6"


class ExcelExporter:
    HEADERS = [
        "图片序号",
        "模块实例ID",
        "模块/图片类型",
        "PC建议尺寸（px）",
        "移动端建议尺寸（px）",
        "参考图片",
        "AI生成效果图片",
        "AI生成图片（德语主文案）",
        "用户指定Prompt",
        "设计思路和需求说明",
        "英文文案",
        "中文翻译文案",
        "德语",
        "法语",
        "意大利语",
        "西班牙语",
        "关键词映射",
        "文案版本",
        "审核状态",
        "ChatGPT自查结果/修改建议",
        "产品结构相似度",
        "OCR识别文字",
        "六语回译检查",
        "文案溢出检查",
        "图片文字合规检查",
        "Amazon规则预检",
        "审核备注",
        "合规/备注",
    ]

    def export(self, project: ProductProject, briefs: list[ModuleBrief], destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        workbook.remove(workbook.active)
        self._add_project_sheet(workbook, project)
        for channel in ("主图", "高级A+", "Brand Story", "品牌旗舰店"):
            rows = [item for item in briefs if item.channel == channel]
            if rows:
                self._add_module_sheet(workbook, project, channel, rows)
        if project.preflight_results:
            self._add_preflight_sheet(workbook, project)
        if project.copy_versions:
            self._add_version_sheet(workbook, project)
        workbook.save(destination)
        return destination

    def _add_project_sheet(self, workbook: Workbook, project: ProductProject) -> None:
        sheet = workbook.create_sheet("项目参数")
        sheet.sheet_view.showGridLines = False
        sheet.merge_cells("A1:H1")
        sheet["A1"] = "Amazon 图片需求生成项目 V2.4"
        sheet["A1"].font = Font(name="Microsoft YaHei", size=18, bold=True, color="FFFFFF")
        sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
        sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
        sheet.row_dimensions[1].height = 36

        fields = [
            ("项目名称", project.project_name),
            ("SKU", project.sku),
            ("ASIN", project.asin),
            ("品牌", project.brand),
            ("中文产品名", project.product_name_zh),
            ("英文产品名", project.product_name_en),
            ("品类", project.category),
            ("目标站点", project.marketplace),
            ("目标人群", project.target_audience),
            ("核心定位", project.positioning),
            ("型号", project.model),
            ("材质", project.material),
            ("基础价格", project.price),
            ("功能", project.functions),
            ("核心卖点", project.selling_points),
            ("包装清单", project.package_contents),
            ("维护/洗护", project.maintenance),
            ("保修", project.warranty),
            ("认证", project.certifications),
            ("品牌语气", project.brand_tone),
            ("视觉风格", project.visual_style),
            ("字体建议", project.font_suggestion),
            ("品牌颜色", project.brand_colors),
            ("品牌Logo", project.logo_image_path),
            ("禁用表达", project.forbidden_claims),
            ("竞品/关键词导入来源", project.imported_market_data_path),
            ("文案AI供应商", project.options.text_provider),
            ("文案协议/模型", f"{project.options.text_protocol} / {project.options.text_model}"),
            ("文案Base URL", project.options.text_base_url),
            ("图片AI供应商", project.options.image_provider),
            ("图片协议/模型", f"{project.options.image_protocol} / {project.options.image_model}"),
            ("图片Base URL", project.options.image_base_url),
            ("失败自动切换", "开启" if project.options.auto_failover else "关闭"),
            ("文案备用供应商/模型", f"{project.options.text_fallback_provider} / {project.options.text_fallback_model}"),
            ("图片备用供应商/模型", f"{project.options.image_fallback_provider} / {project.options.image_fallback_model}"),
            ("请求限流", f"{project.options.requests_per_minute} 次/分钟"),
            ("结果缓存", "开启" if project.options.cache_enabled else "关闭"),
            ("结构锁定/相似度阈值", f"{'开启' if project.options.structure_lock else '关闭'} / {project.options.similarity_threshold}"),
            ("OCR/回译/溢出/图片合规", " / ".join([
                "开" if project.options.ocr_check else "关",
                "开" if project.options.back_translation_check else "关",
                "开" if project.options.overflow_check else "关",
                "开" if project.options.image_compliance_check else "关",
            ])),
            ("Amazon规则库", project.options.rule_library_path or "内置规则库"),
            ("站点列表来源", "https://sell.amazon.com/global-selling"),
            ("模型列表来源", "https://developers.openai.com/api/docs/models"),
        ]
        sheet.append(["字段", "内容"])
        self._style_section_header(sheet, 2, 2)
        for label, value in fields:
            sheet.append([label, value])
        for label, value in project.custom_fields.items():
            sheet.append([f"自定义参数｜{label}", value])

        start = sheet.max_row + 2
        sheet.cell(start, 1, "产品变体")
        sheet.cell(start, 1).font = Font(name="Microsoft YaHei", size=13, bold=True, color=AMBER)
        sheet.append(["变体名称", "SKU", "尺寸", "颜色", "重量", "容量", "价格"])
        self._style_section_header(sheet, sheet.max_row, 7)
        for variant in project.variants:
            sheet.append([variant.name, variant.sku, variant.size, variant.color, variant.weight, variant.capacity, variant.price])

        start = sheet.max_row + 2
        sheet.cell(start, 1, "竞品信息")
        sheet.cell(start, 1).font = Font(name="Microsoft YaHei", size=13, bold=True, color=AMBER)
        sheet.append(["ASIN", "链接", "品牌", "价格", "卖点", "视觉观察", "机会点"])
        self._style_section_header(sheet, sheet.max_row, 7)
        for competitor in project.competitors:
            sheet.append([competitor.asin, competitor.url, competitor.brand, competitor.price, competitor.selling_points, competitor.visual_notes, competitor.opportunity])

        start = sheet.max_row + 2
        sheet.cell(start, 1, "关键词词库")
        sheet.cell(start, 1).font = Font(name="Microsoft YaHei", size=13, bold=True, color=AMBER)
        sheet.append(["关键词", "语言", "意图", "优先级", "备注"])
        self._style_section_header(sheet, sheet.max_row, 5)
        for keyword in project.keywords:
            sheet.append([keyword.term, keyword.language, keyword.intent, keyword.priority, keyword.notes])

        start = sheet.max_row + 2
        sheet.cell(start, 1, "已确认模块顺序与Prompt")
        sheet.cell(start, 1).font = Font(name="Microsoft YaHei", size=13, bold=True, color=AMBER)
        sheet.append(["顺序", "渠道", "模块", "模块代码", "模块实例ID", "用户指定Prompt"])
        self._style_section_header(sheet, sheet.max_row, 6)
        for index, instance in enumerate(project.normalized_module_instances(), 1):
            spec = CATALOG_BY_CODE.get(instance.module_code)
            if spec:
                sheet.append(
                    [
                        index,
                        spec.channel,
                        spec.name,
                        instance.module_code,
                        instance.instance_id,
                        instance.custom_prompt or project.module_prompts.get(instance.instance_id, ""),
                    ]
                )

        thin = Side(style="thin", color=BORDER)
        for row in sheet.iter_rows():
            for cell in row:
                updated_font = copy(cell.font)
                updated_font.name = "Microsoft YaHei"
                updated_font.sz = updated_font.sz or 10
                cell.font = updated_font
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if cell.value is not None:
                    cell.border = Border(bottom=thin)
        sheet.column_dimensions["A"].width = 24
        sheet.column_dimensions["B"].width = 64
        for column in range(3, 9):
            sheet.column_dimensions[get_column_letter(column)].width = 30
        sheet.freeze_panes = "A3"

    @staticmethod
    def _style_section_header(sheet, row: int, columns: int) -> None:
        for column in range(1, columns + 1):
            cell = sheet.cell(row, column)
            cell.fill = PatternFill("solid", fgColor=HEADER)
            cell.font = Font(name="Microsoft YaHei", bold=True, color=NAVY)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def _add_module_sheet(self, workbook: Workbook, project: ProductProject, channel: str, rows: list[ModuleBrief]) -> None:
        sheet = workbook.create_sheet(channel[:31])
        sheet.sheet_view.showGridLines = False
        end_column = get_column_letter(len(self.HEADERS))
        sheet.merge_cells(f"A1:{end_column}1")
        sheet["A1"] = f"{project.project_name}｜{channel}图片需求"
        sheet["A1"].font = Font(name="Microsoft YaHei", size=17, bold=True, color="FFFFFF")
        sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
        sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
        sheet.row_dimensions[1].height = 34
        sheet.merge_cells(f"A2:{end_column}2")
        sheet["A2"] = (
            "尺寸为建议制作尺寸，上传前以目标站点 Seller Central 实时提示为准。AI图只作构图参考；"
            "产品结构、颜色、文字、翻译、声明与审核状态必须人工终审。"
        )
        sheet["A2"].fill = PatternFill("solid", fgColor=YELLOW)
        sheet["A2"].font = Font(name="Microsoft YaHei", size=10, bold=True, color="7A4A00")
        sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
        sheet.row_dimensions[2].height = 32

        for column, header in enumerate(self.HEADERS, 1):
            cell = sheet.cell(3, column, header)
            cell.fill = PatternFill("solid", fgColor=HEADER)
            cell.font = Font(name="Microsoft YaHei", size=10, bold=True, color=NAVY)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.row_dimensions[3].height = 45

        for row_index, brief in enumerate(rows, 4):
            values = [
                brief.sequence,
                brief.instance_id,
                brief.module_name,
                brief.pc_size,
                brief.mobile_size,
                "",
                "",
                "",
                brief.custom_prompt,
                brief.design_brief,
                brief.copy_for("en"),
                brief.copy_for("zh"),
                brief.copy_for("de"),
                brief.copy_for("fr"),
                brief.copy_for("it"),
                brief.copy_for("es"),
                " / ".join(brief.keywords),
                f"V{brief.copy_version}",
                brief.review_status,
                brief.self_check_result,
                "" if brief.image_similarity is None else f"{brief.image_similarity:.1f}/100",
                brief.ocr_text,
                brief.back_translation_result,
                brief.overflow_result,
                brief.image_qa_result,
                brief.rule_preflight_result,
                brief.review_notes,
                brief.compliance_note,
            ]
            for column, value in enumerate(values, 1):
                cell = sheet.cell(row_index, column, value)
                cell.fill = PatternFill("solid", fgColor=WARM_WHITE if row_index % 2 == 0 else STONE)
                cell.font = Font(name="Arial" if 11 <= column <= 16 else "Microsoft YaHei", size=9, color=NAVY)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            status_fill = {"已通过": GREEN, "需修改": RED}.get(brief.review_status, YELLOW)
            sheet.cell(row_index, 19).fill = PatternFill("solid", fgColor=status_fill)
            sheet.cell(row_index, 1).font = Font(name="Microsoft YaHei", size=11, bold=True, color=AMBER)
            sheet.row_dimensions[row_index].height = 125
            self._place_image(sheet, brief.reference_image, row_index, 6)
            self._place_image(sheet, brief.ai_effect_image, row_index, 7)
            self._place_image(sheet, brief.german_composite_image, row_index, 8)

        thin = Side(style="thin", color=BORDER)
        for row in sheet.iter_rows(min_row=3, max_row=sheet.max_row, min_col=1, max_col=len(self.HEADERS)):
            for cell in row:
                cell.border = Border(bottom=thin, right=thin)
        widths = [15, 30, 25, 18, 22, 25, 25, 25, 38, 46, 30, 30, 30, 30, 30, 30, 25, 12, 14, 42, 18, 32, 48, 38, 42, 38, 32, 34]
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A4"
        sheet.auto_filter.ref = f"A3:{end_column}{sheet.max_row}"

    def _add_preflight_sheet(self, workbook: Workbook, project: ProductProject) -> None:
        sheet = workbook.create_sheet("Amazon规则预检")
        sheet.sheet_view.showGridLines = False
        report = project.preflight_results if isinstance(project.preflight_results, dict) else {}
        sheet.merge_cells("A1:F1")
        sheet["A1"] = (
            f"Amazon 导出前预检｜规则库 {report.get('library_version', '-')}｜"
            f"站点 {report.get('marketplace_code', '-')}｜品类 {report.get('category_key', '-')}"
        )
        sheet["A1"].font = Font(name="Microsoft YaHei", size=15, bold=True, color="FFFFFF")
        sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
        sheet["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.row_dimensions[1].height = 34
        headers = ["级别", "规则ID", "作用范围", "检查结果", "处理建议", "依据"]
        sheet.append(headers)
        self._style_section_header(sheet, 2, len(headers))
        for item in report.get("findings", []):
            sheet.append([
                item.get("severity", ""), item.get("rule_id", ""), item.get("scope", ""),
                item.get("message", ""), item.get("suggestion", ""), item.get("source_url", ""),
            ])
            color = {"错误": RED, "警告": YELLOW, "信息": GREEN}.get(item.get("severity"), WARM_WHITE)
            sheet.cell(sheet.max_row, 1).fill = PatternFill("solid", fgColor=color)
        for row in sheet.iter_rows(min_row=3):
            for cell in row:
                cell.font = Font(name="Microsoft YaHei", size=9, color=NAVY)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.border = Border(bottom=Side(style="thin", color=BORDER))
        for index, width in enumerate([12, 20, 22, 48, 48, 52], 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A3"
        sheet.auto_filter.ref = f"A2:F{max(2, sheet.max_row)}"

    def _add_version_sheet(self, workbook: Workbook, project: ProductProject) -> None:
        sheet = workbook.create_sheet("文案版本记录")
        sheet.sheet_view.showGridLines = False
        headers = ["模块实例ID", "模块代码", "模块名称", "版本", "记录时间", "变更原因", "英语", "中文", "德语", "法语", "意大利语", "西班牙语"]
        sheet.append(headers)
        self._style_section_header(sheet, 1, len(headers))
        for instance in project.normalized_module_instances():
            spec = CATALOG_BY_CODE.get(instance.module_code)
            for version in project.copy_versions.get(instance.instance_id, project.copy_versions.get(instance.module_code, [])):
                copy_text = version.get("copy", {})
                sheet.append([
                    instance.instance_id,
                    instance.module_code,
                    spec.name if spec else instance.module_code,
                    f"V{version.get('version', 1)}",
                    version.get("time", ""),
                    version.get("reason", ""),
                    copy_text.get("en", ""),
                    copy_text.get("zh", ""),
                    copy_text.get("de", ""),
                    copy_text.get("fr", ""),
                    copy_text.get("it", ""),
                    copy_text.get("es", ""),
                ])
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.font = Font(name="Arial", size=9, color=NAVY)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.border = Border(bottom=Side(style="thin", color=BORDER))
        widths = [30, 24, 26, 10, 20, 28, 34, 34, 34, 34, 34, 34]
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:L{max(1, sheet.max_row)}"

    @staticmethod
    def _place_image(sheet, image_path: str, row: int, column: int) -> None:
        path = Path(image_path) if image_path else None
        if not path or not path.is_file():
            return
        image = ExcelImage(str(path))
        image.width = 165
        image.height = 108
        sheet.add_image(image, f"{get_column_letter(column)}{row}")
