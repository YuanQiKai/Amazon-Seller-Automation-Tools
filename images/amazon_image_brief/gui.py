from __future__ import annotations

import json
import os
import queue
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .ai_client import OpenAIClient
from .batch_queue import PersistentBatchQueue
from .brief_generator import BriefGenerator
from .catalogs import CATALOGS, CATALOG_BY_CODE, default_module_selection
from .data_io import MarketWorkbookManager
from .costing import estimate_project_cost
from .mask_editor import MaskEditor
from .models import Competitor, GenerationOptions, Keyword, LANGUAGES, ModuleBrief, ModuleInstance, ProductProject, ProductVariant
from .palette import extract_logo_palette, palette_text
from .presets import (
    AMAZON_MARKETPLACES,
    BRAND_COLOR_PRESETS,
    BRAND_TONE_PRESETS,
    FONT_PRESETS,
    LANGUAGE_LABELS,
    REVIEW_STATUSES,
    VISUAL_STYLE_PRESETS,
)
from .providers import (
    IMAGE_PROVIDER_PRESETS,
    PROVIDER_CONFIG_PATH,
    TEXT_PROVIDER_PRESETS,
    preset_id_from_label,
)
from .rules import RuleLibrary
from .service import GenerationService


APP_TITLE = "Amazon 图片需求生成器 V2.4"


SCALAR_FIELDS = [
    ("project_name", "项目名称 *", "行李箱主图与A+需求"),
    ("sku", "基础SKU", "CASE-001"),
    ("asin", "ASIN", ""),
    ("brand", "品牌 *", "YourBrand"),
    ("product_name_zh", "中文产品名 *", "旅行行李箱"),
    ("product_name_en", "英文产品名", "Travel Suitcase"),
    ("category", "品类 *", "Luggage / Suitcases"),
    ("target_audience", "目标人群", "商务旅行者、家庭与城市短途旅行者"),
    ("positioning", "产品定位", "耐用、轻便、现代的中高端旅行箱"),
    ("model", "基础型号", ""),
    ("material", "材质", ""),
    ("price", "基础价格", ""),
    ("package_contents", "包装清单", "行李箱、说明书、保修卡"),
    ("warranty", "保修", ""),
    ("certifications", "认证/测试依据", ""),
]


TEXT_FIELDS = [
    ("functions", "产品功能", "一行一个功能，尽量填写可验证参数"),
    ("selling_points", "核心卖点 *", "一行一个卖点；说明用户收益及证据"),
    ("maintenance", "使用/维护/洗护", "清洁方法、使用限制、维护步骤"),
    ("forbidden_claims", "禁用词/不可宣称内容", "绝对化、无法验证或暗示官方背书的表述"),
]


VARIANT_FIELDS = [
    ("name", "变体名称"),
    ("sku", "SKU"),
    ("size", "尺寸"),
    ("color", "颜色"),
    ("weight", "重量"),
    ("capacity", "容量"),
    ("price", "价格"),
]


class ScrollFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0, background="#f6f7fb")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas, padding=16)
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        self.body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)


class AmazonImageBriefApp:
    def __init__(self, root: tk.Tk, app_root: Path) -> None:
        self.root = root
        self.app_root = app_root
        self.root.title(APP_TITLE)
        self.root.geometry("1380x900")
        self.root.minsize(1120, 720)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.batch_events: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.current_briefs: list[ModuleBrief] = []
        self.copy_versions: dict[str, list[dict[str, Any]]] = {}
        self.copy_overrides: dict[str, dict[str, str]] = {}
        self.review_statuses: dict[str, str] = {}
        self.review_notes: dict[str, str] = {}
        self.self_check_results: dict[str, str] = {}
        self.preflight_results: dict[str, Any] = {}
        self.last_output_dir = ""
        self.last_result: dict[str, Any] = {}
        self.scalar_vars: dict[str, tk.StringVar] = {}
        self.text_widgets: dict[str, tk.Text] = {}
        self.module_lists: dict[str, tk.Listbox] = {}
        self.module_count_vars: dict[str, tk.StringVar] = {}
        self.product_images: list[str] = []
        self.competitor_images: list[str] = []
        self.variants: list[ProductVariant] = []
        self.module_instances: list[ModuleInstance] = []
        self.module_order: list[str] = []
        self.module_prompts: dict[str, str] = {}
        self.current_prompt_code = ""
        self.drag_module_code = ""
        self.current_review_code = ""
        self.current_review_language = "de"
        self.logo_image_path = ""
        self.imported_market_data_path = ""
        self.market_manager = MarketWorkbookManager()
        self.batch_queue = PersistentBatchQueue(self.app_root / "task_queue.json")

        self._configure_style()
        self._build_header()
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=BOTH, expand=True, padx=14, pady=(0, 10))
        self._build_product_tab()
        self._build_market_tab()
        self._build_modules_tab()
        self._build_confirm_tab()
        self._build_review_tab()
        self._build_ai_tab()
        self._build_batch_tab()
        self._build_export_tab()
        self._build_footer()
        self.new_project()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"), foreground="#12233f")
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 9), foreground="#5d6675")
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"), foreground="#12233f")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=27, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, padding=(18, 12, 18, 8))
        header.pack(fill=X)
        title_area = ttk.Frame(header)
        title_area.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(title_area, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_area,
            text="双栏模块规划、供应商容错、批量队列、蒙版重生、六语质检与 Amazon 预检的一体化工作台。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Button(header, text="新建", command=self.new_project).pack(side=LEFT, padx=4)
        ttk.Button(header, text="打开项目", command=self.load_project).pack(side=LEFT, padx=4)
        ttk.Button(header, text="保存项目", command=self.save_project).pack(side=LEFT, padx=4)

    def _build_product_tab(self) -> None:
        frame = ScrollFrame(self.tabs)
        self.tabs.add(frame, text="1  产品与变体")
        body = frame.body

        base = ttk.LabelFrame(body, text="通用输入参数", style="Section.TLabelframe", padding=12)
        base.pack(fill=X, pady=(0, 12))
        for index, (key, label, _default) in enumerate(SCALAR_FIELDS):
            row, column = divmod(index, 2)
            cell = ttk.Frame(base)
            cell.grid(row=row, column=column, sticky="ew", padx=8, pady=5)
            ttk.Label(cell, text=label, width=18).pack(side=LEFT)
            var = tk.StringVar()
            self.scalar_vars[key] = var
            ttk.Entry(cell, textvariable=var).pack(side=LEFT, fill=X, expand=True)
        marketplace_row = (len(SCALAR_FIELDS) + 1) // 2
        marketplace_cell = ttk.Frame(base)
        marketplace_cell.grid(row=marketplace_row, column=0, sticky="ew", padx=8, pady=5)
        ttk.Label(marketplace_cell, text="目标站点 *", width=18).pack(side=LEFT)
        self.marketplace_var = tk.StringVar()
        ttk.Combobox(
            marketplace_cell,
            textvariable=self.marketplace_var,
            values=AMAZON_MARKETPLACES,
            state="readonly",
        ).pack(side=LEFT, fill=X, expand=True)
        base.columnconfigure(0, weight=1)
        base.columnconfigure(1, weight=1)

        variants = ttk.LabelFrame(body, text="尺寸/颜色/重量/容量变体", style="Section.TLabelframe", padding=12)
        variants.pack(fill=X, pady=(0, 12))
        self.variant_vars: dict[str, tk.StringVar] = {}
        self.variant_current_index: int | None = None
        self.variant_editor_loading = False
        editor = ttk.Frame(variants)
        editor.pack(fill=X, pady=(0, 8))
        for column, (key, label) in enumerate(VARIANT_FIELDS):
            group = ttk.Frame(editor)
            group.grid(row=0, column=column, sticky="ew", padx=3)
            ttk.Label(group, text=label).pack(anchor="w")
            var = tk.StringVar()
            self.variant_vars[key] = var
            entry = ttk.Entry(group, textvariable=var, width=14)
            entry.pack(fill=X)
            entry.bind("<FocusOut>", self._autosave_variant)
            entry.bind("<Return>", self._autosave_variant)
            editor.columnconfigure(column, weight=1)
        buttons = ttk.Frame(variants)
        buttons.pack(fill=X, pady=(0, 8))
        ttk.Button(buttons, text="新增变体", command=self.add_variant).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="复制选中", command=self.duplicate_variant).pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="删除选中", command=self.remove_variant).pack(side=LEFT, padx=6)
        ttk.Label(buttons, text="选中后直接编辑；按 Enter、切换输入框或切换变体时自动保存。", style="Subtitle.TLabel").pack(side=LEFT, padx=12)
        self.variant_tree = ttk.Treeview(variants, columns=[key for key, _label in VARIANT_FIELDS], show="headings", height=5)
        for key, label in VARIANT_FIELDS:
            self.variant_tree.heading(key, text=label)
            self.variant_tree.column(key, width=150 if key in {"name", "size", "color"} else 120, anchor="w")
        self.variant_tree.pack(fill=X)
        self.variant_tree.bind("<<TreeviewSelect>>", self._load_variant_editor)

        details = ttk.LabelFrame(body, text="功能、卖点与特定参数", style="Section.TLabelframe", padding=12)
        details.pack(fill=X, pady=(0, 12))
        for row, (key, label, hint) in enumerate(TEXT_FIELDS):
            ttk.Label(details, text=label).grid(row=row * 2, column=0, sticky="w", pady=(7, 2))
            ttk.Label(details, text=hint, style="Subtitle.TLabel").grid(row=row * 2, column=1, sticky="e")
            text = tk.Text(details, height=3, wrap="word", font=("Microsoft YaHei UI", 9))
            text.grid(row=row * 2 + 1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
            self.text_widgets[key] = text
        custom_row = len(TEXT_FIELDS) * 2
        ttk.Label(details, text="自定义产品参数").grid(row=custom_row, column=0, sticky="w", pady=(7, 2))
        ttk.Label(details, text="格式：参数名 | 参数值", style="Subtitle.TLabel").grid(row=custom_row, column=1, sticky="e")
        self.custom_fields_text = tk.Text(details, height=4, wrap="word", font=("Microsoft YaHei UI", 9))
        self.custom_fields_text.grid(row=custom_row + 1, column=0, columnspan=2, sticky="ew")
        details.columnconfigure(0, weight=1)
        details.columnconfigure(1, weight=1)

        brand = ttk.LabelFrame(body, text="品牌视觉预设与Logo配色", style="Section.TLabelframe", padding=12)
        brand.pack(fill=X, pady=(0, 12))
        preset_fields = [
            ("brand_tone", "品牌语气", BRAND_TONE_PRESETS),
            ("visual_style", "视觉风格", VISUAL_STYLE_PRESETS),
            ("font_suggestion", "字体建议", FONT_PRESETS),
            ("brand_colors", "品牌色", BRAND_COLOR_PRESETS),
        ]
        for row, (key, label, values) in enumerate(preset_fields):
            ttk.Label(brand, text=label, width=16).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            self.scalar_vars[key] = var
            ttk.Combobox(brand, textvariable=var, values=values, state="normal").grid(row=row, column=1, sticky="ew", pady=4)
        self.logo_var = tk.StringVar(value="尚未导入Logo")
        ttk.Button(brand, text="导入品牌Logo并识别配色", command=self.pick_logo).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(brand, textvariable=self.logo_var, style="Subtitle.TLabel").grid(row=4, column=1, sticky="w", pady=(8, 0))
        brand.columnconfigure(1, weight=1)

        assets = ttk.LabelFrame(body, text="产品与竞品图片", style="Section.TLabelframe", padding=12)
        assets.pack(fill=X)
        self.product_image_var = tk.StringVar(value="尚未选择")
        self.competitor_image_var = tk.StringVar(value="尚未选择")
        ttk.Button(assets, text="选择产品白底图/多角度图", command=self.pick_product_images).grid(row=0, column=0, sticky="w")
        ttk.Label(assets, textvariable=self.product_image_var, style="Subtitle.TLabel").grid(row=0, column=1, sticky="w", padx=12)
        ttk.Button(assets, text="选择竞品参考图", command=self.pick_competitor_images).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(assets, textvariable=self.competitor_image_var, style="Subtitle.TLabel").grid(row=1, column=1, sticky="w", padx=12, pady=(8, 0))

    def _build_market_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(frame, text="2  竞品与关键词")
        source = ttk.Frame(frame)
        source.pack(fill=X, pady=(0, 10))
        self.market_input_mode_var = tk.StringVar(value="manual")
        ttk.Label(source, text="数据方式：").pack(side=LEFT)
        ttk.Radiobutton(source, text="手动输入", value="manual", variable=self.market_input_mode_var).pack(side=LEFT, padx=4)
        ttk.Radiobutton(source, text="Excel导入", value="excel", variable=self.market_input_mode_var).pack(side=LEFT, padx=4)
        ttk.Button(source, text="导入Excel", command=self.import_market_excel).pack(side=LEFT, padx=(16, 4))
        ttk.Button(source, text="导出Excel模板", command=self.export_market_template).pack(side=LEFT, padx=4)
        self.import_source_var = tk.StringVar(value="未导入文件")
        ttk.Label(source, textvariable=self.import_source_var, style="Subtitle.TLabel").pack(side=LEFT, padx=12)

        competitor_box = ttk.LabelFrame(frame, text="竞品信息（每行一个竞品）", style="Section.TLabelframe", padding=12)
        competitor_box.pack(fill=BOTH, expand=True, pady=(0, 12))
        ttk.Label(competitor_box, text="格式：ASIN | 链接 | 品牌 | 价格 | 卖点 | 视觉观察 | 可突破机会点", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 6))
        self.competitors_text = tk.Text(competitor_box, height=9, wrap="none", font=("Consolas", 9))
        self.competitors_text.pack(fill=BOTH, expand=True)

        keyword_box = ttk.LabelFrame(frame, text="关键词词库（每行一个关键词）", style="Section.TLabelframe", padding=12)
        keyword_box.pack(fill=BOTH, expand=True)
        ttk.Label(
            keyword_box,
            text="格式：关键词 | 语言 | 搜索意图 | 优先级1-5 | 备注。关键词只自然用于可见文案和画面含义。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        self.keywords_text = tk.Text(keyword_box, height=9, wrap="none", font=("Consolas", 9))
        self.keywords_text.pack(fill=BOTH, expand=True)

    def _build_modules_tab(self) -> None:
        outer = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(outer, text="3  模块选择")
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=X, pady=(0, 8))
        ttk.Label(toolbar, text="左侧模块库可重复添加；右侧即时显示已选模块。默认主图9张、高级A+ 7个模块。", style="Subtitle.TLabel").pack(side=LEFT)
        ttk.Button(toolbar, text="查看已添加模块并排序", style="Primary.TButton", command=self.confirm_modules).pack(side=RIGHT)
        panes = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        panes.pack(fill=BOTH, expand=True)
        available = ttk.LabelFrame(panes, text="可添加模块", style="Section.TLabelframe", padding=8)
        selected = ttk.LabelFrame(panes, text="已选择模块", style="Section.TLabelframe", padding=8)
        panes.add(available, weight=1)
        panes.add(selected, weight=1)
        library_tabs = ttk.Notebook(available)
        library_tabs.pack(fill=BOTH, expand=True)
        for channel, specs in CATALOGS.items():
            group = ttk.Frame(library_tabs, padding=8)
            library_tabs.add(group, text=channel)
            count_var = tk.StringVar()
            self.module_count_vars[channel] = count_var
            ttk.Label(group, textvariable=count_var, style="Subtitle.TLabel").pack(anchor="w", pady=(0, 5))
            picker = ttk.Frame(group)
            picker.pack(fill=BOTH, expand=True)
            listbox = tk.Listbox(picker, selectmode=tk.BROWSE, exportselection=False, font=("Microsoft YaHei UI", 9), height=9)
            scrollbar = ttk.Scrollbar(picker, orient="vertical", command=listbox.yview)
            listbox.configure(yscrollcommand=scrollbar.set)
            listbox.pack(side=LEFT, fill=BOTH, expand=True)
            scrollbar.pack(side=RIGHT, fill=Y)
            for spec in specs:
                listbox.insert(END, f"{spec.name}  | PC {spec.pc_size} | 移动 {spec.mobile_size}")
            listbox.bind("<Double-Button-1>", lambda _event, name=channel: self.add_module_instance(name))
            self.module_lists[channel] = listbox
            ttk.Button(
                group,
                text="添加所选模块（可重复）",
                command=lambda name=channel: self.add_module_instance(name),
            ).pack(fill=X, pady=(7, 0))

        selected_toolbar = ttk.Frame(selected)
        selected_toolbar.pack(fill=X, pady=(0, 6))
        ttk.Button(selected_toolbar, text="复制所选", command=lambda: self.duplicate_module_instance(self.module_selected_tree)).pack(side=LEFT)
        ttk.Button(selected_toolbar, text="批量删除", command=lambda: self.remove_module_instances(self.module_selected_tree)).pack(side=LEFT, padx=6)
        ttk.Label(selected_toolbar, text="可按 Ctrl/Shift 多选；排序请进入下一页。", style="Subtitle.TLabel").pack(side=LEFT, padx=8)
        self.module_selected_tree = ttk.Treeview(
            selected,
            columns=("order", "channel", "module", "instance"),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in [
            ("order", "顺序", 55), ("channel", "渠道", 95), ("module", "模块", 220), ("instance", "实例ID", 150)
        ]:
            self.module_selected_tree.heading(key, text=label)
            self.module_selected_tree.column(key, width=width, anchor="w")
        self.module_selected_tree.pack(fill=BOTH, expand=True)

    def _build_confirm_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(frame, text="4  确认、排序与Prompt")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=X, pady=(0, 8))
        ttk.Button(toolbar, text="刷新列表", command=self._sync_module_selection).pack(side=LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="复制实例", command=lambda: self.duplicate_module_instance(self.confirm_tree)).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="批量删除已选模块", command=lambda: self.remove_module_instances(self.confirm_tree)).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="上移", command=lambda: self.move_module(-1)).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="下移", command=lambda: self.move_module(1)).pack(side=LEFT, padx=4)
        ttk.Label(toolbar, text="可直接拖拽表格行调整生成顺序；白底首图始终是主图中的第1张。", style="Subtitle.TLabel").pack(side=LEFT, padx=12)
        self.confirm_count_var = tk.StringVar(value="尚未确认")
        ttk.Label(toolbar, textvariable=self.confirm_count_var).pack(side=RIGHT)

        self.confirm_tree = ttk.Treeview(frame, columns=("order", "channel", "module", "instance", "pc", "mobile", "prompt"), show="headings", height=13, selectmode="extended")
        headings = [("order", "顺序", 65), ("channel", "渠道", 110), ("module", "模块", 210), ("instance", "实例ID", 155), ("pc", "PC尺寸", 150), ("mobile", "移动端尺寸", 165), ("prompt", "自定义Prompt摘要", 410)]
        for key, label, width in headings:
            self.confirm_tree.heading(key, text=label)
            self.confirm_tree.column(key, width=width, anchor="w")
        self.confirm_tree.pack(fill=BOTH, expand=True)
        self.confirm_tree.bind("<<TreeviewSelect>>", self._on_confirm_select)
        self.confirm_tree.bind("<ButtonPress-1>", self._on_module_drag_start)
        self.confirm_tree.bind("<B1-Motion>", self._on_module_drag_motion)
        self.confirm_tree.bind("<ButtonRelease-1>", self._on_module_drag_end)

        prompt_box = ttk.LabelFrame(frame, text="选中模块的图片生成Prompt（可留空）", style="Section.TLabelframe", padding=10)
        prompt_box.pack(fill=X, pady=(10, 0))
        ttk.Label(prompt_box, text="填写特定风格、镜头、布局、人物或场景要求。若与白底首图规则冲突，首图规则优先。", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 5))
        self.module_prompt_text = tk.Text(prompt_box, height=4, wrap="word", font=("Microsoft YaHei UI", 9))
        self.module_prompt_text.pack(fill=X)
        self.module_prompt_text.bind("<FocusOut>", lambda _event: self._save_module_prompt())

    def _build_review_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(frame, text="5  六语文案审核")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=X, pady=(0, 8))
        ttk.Button(toolbar, text="生成/刷新六语草稿", style="Primary.TButton", command=self.generate_draft).pack(side=LEFT, padx=(0, 5))
        ttk.Button(toolbar, text="全局重新生成文案", command=self.regenerate_all_copy).pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="ChatGPT自查全部", command=lambda: self.check_copy(all_modules=True)).pack(side=LEFT, padx=5)
        ttk.Label(toolbar, text="统一修改要求：").pack(side=LEFT, padx=(16, 4))
        self.global_revision_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.global_revision_var).pack(side=LEFT, fill=X, expand=True)

        panes = ttk.Panedwindow(frame, orient=tk.HORIZONTAL)
        panes.pack(fill=BOTH, expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=1)
        panes.add(right, weight=3)

        self.review_tree = ttk.Treeview(left, columns=("channel", "module", "version", "status"), show="headings")
        for key, label, width in [("channel", "渠道", 100), ("module", "模块", 180), ("version", "版本", 65), ("status", "审核状态", 80)]:
            self.review_tree.heading(key, text=label)
            self.review_tree.column(key, width=width, anchor="w")
        self.review_tree.pack(fill=BOTH, expand=True)
        self.review_tree.bind("<<TreeviewSelect>>", self._on_review_select)

        meta = ttk.Frame(right)
        meta.pack(fill=X, pady=(0, 8))
        ttk.Label(meta, text="审核状态").grid(row=0, column=0, sticky="w")
        self.review_status_var = tk.StringVar(value="待审核")
        ttk.Combobox(meta, textvariable=self.review_status_var, values=REVIEW_STATUSES, state="readonly", width=12).grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Label(meta, text="审核备注").grid(row=0, column=1, sticky="w")
        self.review_notes_var = tk.StringVar()
        ttk.Entry(meta, textvariable=self.review_notes_var).grid(row=1, column=1, sticky="ew")
        meta.columnconfigure(1, weight=1)

        language_row = ttk.Frame(right)
        language_row.pack(fill=X, pady=(0, 5))
        ttk.Label(language_row, text="当前语言：").pack(side=LEFT)
        self.review_language_var = tk.StringVar(value="de — 德语")
        language_combo = ttk.Combobox(
            language_row,
            textvariable=self.review_language_var,
            values=[f"{code} — {LANGUAGE_LABELS[code]}" for code in LANGUAGES],
            state="readonly",
            width=20,
        )
        language_combo.pack(side=LEFT)
        language_combo.bind("<<ComboboxSelected>>", self._on_language_change)
        ttk.Button(language_row, text="保存当前语言", command=self.save_current_copy).pack(side=LEFT, padx=8)
        ttk.Button(language_row, text="局部重生成文案", command=self.regenerate_selected_copy).pack(side=LEFT, padx=4)
        ttk.Button(language_row, text="自查当前模块", command=lambda: self.check_copy(all_modules=False)).pack(side=LEFT, padx=4)
        ttk.Button(language_row, text="局部重生成图片", command=self.regenerate_selected_image).pack(side=LEFT, padx=4)
        ttk.Button(language_row, text="蒙版选区式重生", command=self.open_mask_editor).pack(side=LEFT, padx=4)

        compare = ttk.Frame(right)
        compare.pack(fill=BOTH, expand=True)
        current_group = ttk.LabelFrame(compare, text="当前文案（可编辑）", style="Section.TLabelframe", padding=8)
        current_group.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        history_group = ttk.LabelFrame(compare, text="历史版本对比", style="Section.TLabelframe", padding=8)
        history_group.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.current_copy_text = tk.Text(current_group, wrap="word", height=12, font=("Arial", 10))
        self.current_copy_text.pack(fill=BOTH, expand=True)
        self.compare_version_var = tk.StringVar()
        self.compare_version_combo = ttk.Combobox(history_group, textvariable=self.compare_version_var, state="readonly")
        self.compare_version_combo.pack(fill=X, pady=(0, 5))
        self.compare_version_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_compare_version())
        self.previous_copy_text = tk.Text(history_group, wrap="word", height=12, font=("Arial", 10), state="disabled")
        self.previous_copy_text.pack(fill=BOTH, expand=True)
        compare.rowconfigure(0, weight=1)
        compare.columnconfigure(0, weight=1)
        compare.columnconfigure(1, weight=1)

        check_group = ttk.LabelFrame(right, text="ChatGPT自查结果与修改建议", style="Section.TLabelframe", padding=8)
        check_group.pack(fill=X, pady=(8, 0))
        self.self_check_text = tk.Text(check_group, height=5, wrap="word", font=("Microsoft YaHei UI", 9), state="disabled")
        self.self_check_text.pack(fill=X)

    def _build_ai_tab(self) -> None:
        frame = ScrollFrame(self.tabs)
        self.tabs.add(frame, text="6  AI接入设置")
        body = frame.body
        notice = ttk.LabelFrame(body, text="接入说明", style="Section.TLabelframe", padding=12)
        notice.pack(fill=X, pady=(0, 12))
        ttk.Label(
            notice,
            text=(
                "只需选择供应商和模型。连接地址、协议、接口路径及 Key 环境变量由 "
                "resources/ai_providers.json 维护；真实 API Key 放在项目 .env 文件中。"
            ),
            style="Subtitle.TLabel",
            wraplength=1180,
        ).pack(anchor="w")

        self.ai_config_vars: dict[str, dict[str, tk.StringVar]] = {}
        self.ai_model_combos: dict[str, ttk.Combobox] = {}
        self.auto_failover_var = tk.BooleanVar(value=False)
        self.text_fallback_provider_var = tk.StringVar()
        self.text_fallback_model_var = tk.StringVar()
        self.image_fallback_provider_var = tk.StringVar()
        self.image_fallback_model_var = tk.StringVar()
        self.max_retries_var = tk.StringVar(value="2")
        self.requests_per_minute_var = tk.StringVar(value="10")
        self.cache_enabled_var = tk.BooleanVar(value=True)
        self.structure_lock_var = tk.BooleanVar(value=True)
        self.similarity_threshold_var = tk.StringVar(value="35")
        self.ocr_check_var = tk.BooleanVar(value=False)
        self.back_translation_check_var = tk.BooleanVar(value=False)
        self.overflow_check_var = tk.BooleanVar(value=True)
        self.image_compliance_check_var = tk.BooleanVar(value=False)
        self._build_provider_panel(body, "text", "文案与翻译模型", TEXT_PROVIDER_PRESETS)
        self._build_provider_panel(body, "image", "图片生成模型", IMAGE_PROVIDER_PRESETS)

        resilience = ttk.LabelFrame(body, text="失败切换、限流与质量控制", style="Section.TLabelframe", padding=12)
        resilience.pack(fill=X, pady=(0, 12))
        ttk.Checkbutton(resilience, text="主供应商失败后自动切换", variable=self.auto_failover_var).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(resilience, text="文案备用供应商").grid(row=0, column=1, sticky="w")
        text_fallback_provider = ttk.Combobox(resilience, textvariable=self.text_fallback_provider_var, values=tuple(TEXT_PROVIDER_PRESETS), state="readonly", width=18)
        text_fallback_provider.grid(row=0, column=2, sticky="w", padx=4)
        text_fallback_provider.bind("<<ComboboxSelected>>", lambda _event: self._apply_fallback_provider("text"))
        self.text_fallback_model_combo = ttk.Combobox(resilience, textvariable=self.text_fallback_model_var, state="readonly", width=28)
        self.text_fallback_model_combo.grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Label(resilience, text="图片备用供应商").grid(row=1, column=1, sticky="w")
        image_fallback_provider = ttk.Combobox(resilience, textvariable=self.image_fallback_provider_var, values=tuple(IMAGE_PROVIDER_PRESETS), state="readonly", width=18)
        image_fallback_provider.grid(row=1, column=2, sticky="w", padx=4)
        image_fallback_provider.bind("<<ComboboxSelected>>", lambda _event: self._apply_fallback_provider("image"))
        self.image_fallback_model_combo = ttk.Combobox(resilience, textvariable=self.image_fallback_model_var, state="readonly", width=28)
        self.image_fallback_model_combo.grid(row=1, column=3, sticky="ew", padx=4)
        ttk.Label(resilience, text="重试次数").grid(row=0, column=4, sticky="e", padx=(10, 2))
        ttk.Entry(resilience, textvariable=self.max_retries_var, width=7).grid(row=0, column=5, sticky="w")
        ttk.Label(resilience, text="每分钟请求上限").grid(row=1, column=4, sticky="e", padx=(10, 2))
        ttk.Entry(resilience, textvariable=self.requests_per_minute_var, width=7).grid(row=1, column=5, sticky="w")
        ttk.Checkbutton(resilience, text="启用结果缓存", variable=self.cache_enabled_var).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(resilience, text="产品结构锁定", variable=self.structure_lock_var).grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(resilience, text="最低相似度(0–100)").grid(row=2, column=1, sticky="w")
        ttk.Entry(resilience, textvariable=self.similarity_threshold_var, width=8).grid(row=2, column=2, sticky="w", padx=4)
        quality_checks = ttk.Frame(resilience)
        quality_checks.grid(row=3, column=0, columnspan=6, sticky="w", pady=4)
        ttk.Checkbutton(quality_checks, text="OCR与图片文字检查（会调用文案模型）", variable=self.ocr_check_var).pack(side=LEFT, padx=4)
        ttk.Checkbutton(quality_checks, text="六语回译检查（会调用文案模型）", variable=self.back_translation_check_var).pack(side=LEFT, padx=4)
        ttk.Checkbutton(quality_checks, text="文案溢出检查", variable=self.overflow_check_var).pack(side=LEFT, padx=4)
        ttk.Checkbutton(quality_checks, text="图片合规AI检查", variable=self.image_compliance_check_var).pack(side=LEFT, padx=4)
        resilience.columnconfigure(3, weight=1)

        actions = ttk.Frame(body)
        actions.pack(fill=X)
        ttk.Button(actions, text="检查当前配置", command=self.check_ai_configuration).pack(side=LEFT)
        ttk.Button(actions, text="费用预估", command=self.show_cost_estimate).pack(side=LEFT, padx=6)
        ttk.Button(actions, text="打开供应商参数文件", command=self.open_provider_config).pack(side=LEFT, padx=6)
        ttk.Label(
            actions,
            text="“获取全部模型”只请求模型列表；文案“测试连接”会额外发送一条极短对话并产生少量 token，图片测试不生图。",
            style="Subtitle.TLabel",
        ).pack(side=LEFT, padx=12)

    def _build_provider_panel(
        self,
        parent: tk.Misc,
        kind: str,
        title: str,
        presets,
    ) -> None:
        box = ttk.LabelFrame(parent, text=title, style="Section.TLabelframe", padding=12)
        box.pack(fill=X, pady=(0, 12))
        variables = {
            "provider": tk.StringVar(),
            "protocol": tk.StringVar(),
            "base_url": tk.StringVar(),
            "endpoint": tk.StringVar(),
            "model": tk.StringVar(),
            "key_env": tk.StringVar(),
            "extra_headers": tk.StringVar(),
            "note": tk.StringVar(),
        }
        self.ai_config_vars[kind] = variables

        ttk.Label(box, text="供应商").grid(row=0, column=0, sticky="w")
        provider_combo = ttk.Combobox(
            box,
            textvariable=variables["provider"],
            values=[item.label for item in presets.values()],
            state="readonly",
            width=34,
        )
        provider_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(2, 8))
        provider_combo.bind("<<ComboboxSelected>>", lambda _event, value=kind: self.apply_provider_preset(value))

        ttk.Label(box, text="模型").grid(row=0, column=1, sticky="w")
        model_combo = ttk.Combobox(box, textvariable=variables["model"], state="readonly", width=52)
        model_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(2, 8))
        self.ai_model_combos[kind] = model_combo

        ttk.Label(box, textvariable=variables["note"], style="Subtitle.TLabel", wraplength=1040).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 6)
        )
        button_row = ttk.Frame(box)
        button_row.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Button(button_row, text="获取全部模型", command=lambda value=kind: self.fetch_ai_models(value)).pack(side=LEFT)
        ttk.Button(button_row, text="测试连接", command=lambda value=kind: self.test_ai_connection(value)).pack(side=LEFT, padx=6)
        for column in range(2):
            box.columnconfigure(column, weight=1)

    @staticmethod
    def _provider_presets(kind: str):
        return TEXT_PROVIDER_PRESETS if kind == "text" else IMAGE_PROVIDER_PRESETS

    def _provider_id(self, kind: str) -> str:
        presets = self._provider_presets(kind)
        return preset_id_from_label(presets, self.ai_config_vars[kind]["provider"].get())

    def apply_provider_preset(self, kind: str) -> None:
        variables = self.ai_config_vars[kind]
        presets = self._provider_presets(kind)
        provider_id = preset_id_from_label(presets, variables["provider"].get())
        preset = presets[provider_id]
        variables["provider"].set(preset.label)
        variables["protocol"].set(preset.protocol)
        variables["base_url"].set(preset.base_url)
        variables["endpoint"].set(preset.endpoint)
        variables["key_env"].set(preset.api_key_env)
        variables["model"].set(preset.default_model)
        variables["extra_headers"].set(preset.extra_headers)
        variables["note"].set(self._provider_note(preset))
        self.ai_model_combos[kind].configure(values=preset.models)
        self._refresh_selected_provider_labels()

    @staticmethod
    def _provider_note(preset) -> str:
        key_state = "已配置" if os.getenv(preset.api_key_env, "").strip() else "未配置"
        return f"{preset.note}｜Key：{preset.api_key_env}（{key_state}）"

    def _apply_fallback_provider(self, kind: str, preserve_model: bool = False) -> None:
        presets = self._provider_presets(kind)
        provider_var = self.text_fallback_provider_var if kind == "text" else self.image_fallback_provider_var
        model_var = self.text_fallback_model_var if kind == "text" else self.image_fallback_model_var
        combo = self.text_fallback_model_combo if kind == "text" else self.image_fallback_model_combo
        preset = presets.get(provider_var.get())
        combo.configure(values=preset.models if preset else ())
        if not preserve_model or model_var.get() not in (preset.models if preset else ()):
            model_var.set(preset.default_model if preset else "")

    def _refresh_selected_provider_labels(self) -> None:
        if not hasattr(self, "selected_text_provider_var"):
            return
        text = self.ai_config_vars["text"]
        image = self.ai_config_vars["image"]
        self.selected_text_provider_var.set(f"{text['provider'].get()} / {text['model'].get()}")
        self.selected_image_provider_var.set(f"{image['provider'].get()} / {image['model'].get()}")

    def _load_ai_options(self, options: GenerationOptions) -> None:
        for kind, presets in (("text", TEXT_PROVIDER_PRESETS), ("image", IMAGE_PROVIDER_PRESETS)):
            provider_id = getattr(options, f"{kind}_provider", "openai")
            preset = presets.get(provider_id, presets["custom"])
            variables = self.ai_config_vars[kind]
            variables["provider"].set(preset.label)
            variables["protocol"].set(preset.protocol)
            variables["base_url"].set(preset.base_url)
            variables["endpoint"].set(preset.endpoint)
            variables["key_env"].set(preset.api_key_env)
            variables["model"].set(getattr(options, f"{kind}_model", preset.default_model))
            variables["extra_headers"].set(preset.extra_headers)
            variables["note"].set(self._provider_note(preset))
            available_models = tuple(dict.fromkeys((*preset.models, variables["model"].get())))
            self.ai_model_combos[kind].configure(values=available_models)
        self.auto_failover_var.set(options.auto_failover)
        self.text_fallback_provider_var.set(options.text_fallback_provider)
        self.text_fallback_model_var.set(options.text_fallback_model)
        self.image_fallback_provider_var.set(options.image_fallback_provider)
        self.image_fallback_model_var.set(options.image_fallback_model)
        self._apply_fallback_provider("text", preserve_model=True)
        self._apply_fallback_provider("image", preserve_model=True)
        self.max_retries_var.set(str(options.max_retries))
        self.requests_per_minute_var.set(str(options.requests_per_minute))
        self.cache_enabled_var.set(options.cache_enabled)
        self.structure_lock_var.set(options.structure_lock)
        self.similarity_threshold_var.set(str(options.similarity_threshold))
        self.ocr_check_var.set(options.ocr_check)
        self.back_translation_check_var.set(options.back_translation_check)
        self.overflow_check_var.set(options.overflow_check)
        self.image_compliance_check_var.set(options.image_compliance_check)
        self._refresh_selected_provider_labels()

    def _build_ai_client(self, options: GenerationOptions) -> OpenAIClient:
        return OpenAIClient.from_options(
            options,
            cache_dir=self.app_root / ".cache" / "ai",
        )

    def test_ai_connection(self, kind: str) -> None:
        try:
            project = self.collect_project(commit_review=False)
            client = self._build_ai_client(project.options)
        except Exception as exc:
            messagebox.showerror("配置有误", str(exc))
            return
        label = "文案" if kind == "text" else "图片"
        self._start_async(
            f"connection:{kind}",
            lambda: client.test_connection(kind),
            f"正在真实测试{label}供应商连接...",
        )

    def fetch_ai_models(self, kind: str) -> None:
        try:
            project = self.collect_project(commit_review=False)
            client = self._build_ai_client(project.options)
        except Exception as exc:
            messagebox.showerror("配置有误", str(exc))
            return
        label = "文案" if kind == "text" else "图片"
        self._start_async(
            f"models:{kind}",
            lambda: client.list_models(kind),
            f"正在获取{label}供应商模型列表...",
        )

    def show_cost_estimate(self) -> None:
        try:
            estimate = estimate_project_cost(self.collect_project(commit_review=False))
        except Exception as exc:
            messagebox.showerror("无法估算", str(exc))
            return
        messagebox.showinfo("本次生成费用预估", estimate.summary())

    def check_ai_configuration(self) -> None:
        messages: list[str] = []
        for kind, label in (("text", "文案"), ("image", "图片")):
            variables = self.ai_config_vars[kind]
            base_url = variables["base_url"].get().strip()
            endpoint = variables["endpoint"].get().strip()
            model = variables["model"].get().strip()
            env_name = variables["key_env"].get().strip()
            key_present = bool(env_name and os.getenv(env_name, "").strip())
            problems = []
            if not base_url.startswith(("https://", "http://")):
                problems.append("Base URL无效")
            if not endpoint:
                problems.append("接口路径为空")
            if not model:
                problems.append("模型为空")
            if not key_present:
                problems.append(f"未找到{env_name or 'API Key'}")
            try:
                OpenAIClient._extra_headers(variables["extra_headers"].get())
            except Exception:
                problems.append("附加请求头JSON无效")
            messages.append(f"{label}：{'；'.join(problems) if problems else '配置字段完整'}")
        messagebox.showinfo("AI配置检查（未发送请求）", "\n".join(messages))

    def open_provider_config(self) -> None:
        if PROVIDER_CONFIG_PATH.is_file():
            os.startfile(str(PROVIDER_CONFIG_PATH))  # type: ignore[attr-defined]

    def _build_batch_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(frame, text="7  批量任务与合规")
        panes = ttk.Panedwindow(frame, orient=tk.HORIZONTAL)
        panes.pack(fill=BOTH, expand=True)
        queue_box = ttk.LabelFrame(panes, text="异步批量 SKU 队列", style="Section.TLabelframe", padding=10)
        rules_box = ttk.LabelFrame(panes, text="Amazon 规则库与导出预检", style="Section.TLabelframe", padding=10)
        panes.add(queue_box, weight=3)
        panes.add(rules_box, weight=2)

        queue_actions = ttk.Frame(queue_box)
        queue_actions.pack(fill=X, pady=(0, 7))
        ttk.Button(queue_actions, text="添加项目JSON", command=self.add_batch_projects).pack(side=LEFT)
        ttk.Button(queue_actions, text="批量删除", command=self.remove_batch_projects).pack(side=LEFT, padx=5)
        ttk.Button(queue_actions, text="开始", style="Primary.TButton", command=self.start_batch_queue).pack(side=LEFT, padx=(12, 4))
        ttk.Button(queue_actions, text="暂停", command=self.pause_batch_queue).pack(side=LEFT, padx=4)
        ttk.Button(queue_actions, text="继续", command=self.resume_batch_queue).pack(side=LEFT, padx=4)
        ttk.Button(queue_actions, text="重试失败项", command=self.retry_batch_queue).pack(side=LEFT, padx=4)
        self.batch_tree = ttk.Treeview(
            queue_box,
            columns=("project", "sku", "status", "progress", "result"),
            show="headings",
            selectmode="extended",
        )
        for key, label, width in [
            ("project", "项目", 180), ("sku", "SKU", 95), ("status", "状态", 75),
            ("progress", "进度", 210), ("result", "输出目录", 260),
        ]:
            self.batch_tree.heading(key, text=label)
            self.batch_tree.column(key, width=width, anchor="w")
        self.batch_tree.pack(fill=BOTH, expand=True)
        self.batch_tree.bind("<Double-Button-1>", lambda _event: self.open_batch_result())
        ttk.Label(
            queue_box,
            text="队列持久化保存；暂停在当前网络请求完成后的模块检查点生效。API Key 不写入项目，批处理统一从 .env/环境变量读取。",
            style="Subtitle.TLabel",
            wraplength=720,
        ).pack(anchor="w", pady=(7, 0))

        try:
            rule_label = RuleLibrary.load().version_label
        except Exception as exc:
            rule_label = f"规则库读取失败：{exc}"
        self.rule_version_var = tk.StringVar(value=rule_label)
        ttk.Label(rules_box, textvariable=self.rule_version_var, style="Subtitle.TLabel", wraplength=480).pack(anchor="w")
        self.rule_library_path_var = tk.StringVar()
        self.rule_update_url_var = tk.StringVar()
        path_row = ttk.Frame(rules_box)
        path_row.pack(fill=X, pady=(7, 4))
        ttk.Entry(path_row, textvariable=self.rule_library_path_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(path_row, text="导入规则JSON", command=self.import_rule_library).pack(side=LEFT, padx=(5, 0))
        url_row = ttk.Frame(rules_box)
        url_row.pack(fill=X, pady=4)
        ttk.Entry(url_row, textvariable=self.rule_update_url_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(url_row, text="HTTPS更新", command=self.update_rule_library).pack(side=LEFT, padx=(5, 0))
        ttk.Button(rules_box, text="立即运行导出预检", style="Primary.TButton", command=self.run_rule_preflight).pack(anchor="w", pady=(5, 7))
        self.preflight_tree = ttk.Treeview(
            rules_box,
            columns=("severity", "rule", "scope", "message"),
            show="headings",
        )
        for key, label, width in [
            ("severity", "级别", 55), ("rule", "规则ID", 105), ("scope", "范围", 110), ("message", "结果/建议", 330),
        ]:
            self.preflight_tree.heading(key, text=label)
            self.preflight_tree.column(key, width=width, anchor="w")
        self.preflight_tree.pack(fill=BOTH, expand=True)
        self._refresh_batch_tree()
        self.root.after(500, self._poll_batch_events)

    def _build_export_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(frame, text="8  生成与导出")
        options = ttk.LabelFrame(frame, text="生成选项", style="Section.TLabelframe", padding=14)
        options.pack(fill=X)
        self.optimize_var = tk.BooleanVar(value=True)
        self.generate_images_var = tk.BooleanVar(value=bool(os.getenv("OPENAI_API_KEY")))
        self.german_composite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="用AI优化六语文案与设计需求", variable=self.optimize_var).grid(row=0, column=0, sticky="w", padx=5, pady=4)
        ttk.Checkbutton(options, text="生成AI参考图片", variable=self.generate_images_var).grid(row=0, column=1, sticky="w", padx=5, pady=4)
        ttk.Checkbutton(options, text="生成德语主文案效果图", variable=self.german_composite_var).grid(row=0, column=2, sticky="w", padx=5, pady=4)
        self.quality_var = tk.StringVar(value="medium")
        self.text_model_var = self.ai_config_vars["text"]["model"]
        self.image_model_var = self.ai_config_vars["image"]["model"]
        self.selected_text_provider_var = tk.StringVar()
        self.selected_image_provider_var = tk.StringVar()
        self.text_model_var.trace_add("write", lambda *_args: self._refresh_selected_provider_labels())
        self.image_model_var.trace_add("write", lambda *_args: self._refresh_selected_provider_labels())
        ttk.Label(options, text="文案供应商/模型").grid(row=1, column=0, sticky="w", padx=5, pady=(10, 2))
        ttk.Label(options, textvariable=self.selected_text_provider_var).grid(row=2, column=0, sticky="w", padx=5)
        ttk.Label(options, text="图片供应商/模型").grid(row=1, column=1, sticky="w", padx=5, pady=(10, 2))
        ttk.Label(options, textvariable=self.selected_image_provider_var).grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(options, text="图片质量").grid(row=1, column=2, sticky="w", padx=5, pady=(10, 2))
        ttk.Combobox(options, textvariable=self.quality_var, values=("low", "medium", "high"), state="readonly", width=22).grid(row=2, column=2, sticky="w", padx=5)

        output = ttk.LabelFrame(frame, text="输出位置", style="Section.TLabelframe", padding=14)
        output.pack(fill=X, pady=14)
        self.output_root_var = tk.StringVar(value=str(self.app_root / "outputs"))
        ttk.Entry(output, textvariable=self.output_root_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(output, text="选择文件夹", command=self.pick_output_root).pack(side=RIGHT, padx=(8, 0))
        ttk.Label(
            frame,
            text="最终Excel包含产品变体、模块Prompt、六语文案、版本、审核状态和自查建议。局部图片重生成后会自动刷新Excel与图片ZIP。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 14))
        self.generate_button = ttk.Button(frame, text="生成Excel与参考图", style="Primary.TButton", command=self.start_generation)
        self.generate_button.pack(anchor="w")
        self.progress_var = tk.StringVar(value="等待生成")
        ttk.Label(frame, textvariable=self.progress_var).pack(anchor="w", pady=(14, 4))
        self.progressbar = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.progressbar.pack(fill=X)
        self.open_output_button = ttk.Button(frame, text="打开输出文件夹", state="disabled", command=self.open_output)
        self.open_output_button.pack(anchor="w", pady=(12, 0))

    def _build_footer(self) -> None:
        self.status_var = tk.StringVar(value="就绪")
        footer = ttk.Frame(self.root, padding=(18, 0, 18, 9))
        footer.pack(fill=X)
        ttk.Label(footer, textvariable=self.status_var, style="Subtitle.TLabel").pack(side=LEFT)
        ttk.Label(footer, text="尺寸为策划建议值，正式上传前请在Seller Central实时复核。", style="Subtitle.TLabel").pack(side=RIGHT)

    def add_batch_projects(self) -> None:
        paths = filedialog.askopenfilenames(title="添加批量项目", filetypes=[("项目JSON", "*.json"), ("所有文件", "*.*")])
        if not paths:
            return
        try:
            added = self.batch_queue.add_projects(list(paths))
            self._refresh_batch_tree()
            self.status_var.set(f"已加入 {len(added)} 个批量项目")
        except Exception as exc:
            messagebox.showerror("添加失败", str(exc))

    def remove_batch_projects(self) -> None:
        selected = list(self.batch_tree.selection())
        if not selected:
            return
        if not messagebox.askyesno("批量删除", f"确定从队列移除选中的 {len(selected)} 个任务吗？输出文件不会被删除。"):
            return
        removed = self.batch_queue.remove(selected)
        self._refresh_batch_tree()
        self.status_var.set(f"已从队列移除 {removed} 个任务")

    def start_batch_queue(self) -> None:
        def processor(project: ProductProject, progress, checkpoint):
            client = OpenAIClient.from_options(project.options, cache_dir=self.app_root / ".cache" / "ai")
            return GenerationService(self.app_root, client).run(project, progress, control=checkpoint)

        self.batch_queue.start(processor, lambda: self.batch_events.put("refresh"))
        self._refresh_batch_tree()
        self.status_var.set("批量任务队列已启动")

    def pause_batch_queue(self) -> None:
        self.batch_queue.pause()
        self._refresh_batch_tree()
        self.status_var.set("批量队列将在当前请求完成后暂停")

    def resume_batch_queue(self) -> None:
        if self.batch_queue.is_running:
            self.batch_queue.resume()
        else:
            self.start_batch_queue()
        self._refresh_batch_tree()

    def retry_batch_queue(self) -> None:
        count = self.batch_queue.retry_failed()
        self._refresh_batch_tree()
        self.status_var.set(f"已将 {count} 个失败任务恢复为等待中")

    def _refresh_batch_tree(self) -> None:
        if not hasattr(self, "batch_tree"):
            return
        self.batch_tree.delete(*self.batch_tree.get_children())
        for item in self.batch_queue.items:
            self.batch_tree.insert(
                "", END, iid=item.task_id,
                values=(item.project_name, item.sku, item.status, item.progress or item.error, item.result_dir),
            )

    def _poll_batch_events(self) -> None:
        changed = False
        while True:
            try:
                self.batch_events.get_nowait()
                changed = True
            except queue.Empty:
                break
        if changed:
            self._refresh_batch_tree()
        try:
            self.root.after(500, self._poll_batch_events)
        except tk.TclError:
            pass

    def open_batch_result(self) -> None:
        selected = self.batch_tree.selection()
        item = next((entry for entry in self.batch_queue.items if selected and entry.task_id == selected[0]), None)
        if item and item.result_dir and Path(item.result_dir).is_dir():
            os.startfile(item.result_dir)  # type: ignore[attr-defined]

    def _rule_library(self) -> RuleLibrary:
        return RuleLibrary.load(self.rule_library_path_var.get().strip())

    def import_rule_library(self) -> None:
        source = filedialog.askopenfilename(title="导入 Amazon 规则库", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not source:
            return
        try:
            destination = self.app_root / "rules" / "amazon_rules.override.json"
            library = RuleLibrary.import_file(Path(source), destination)
            self.rule_library_path_var.set(str(destination))
            self.rule_version_var.set(library.version_label)
            self.status_var.set("Amazon 规则库已导入")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    def update_rule_library(self) -> None:
        url = self.rule_update_url_var.get().strip()
        if not url:
            messagebox.showinfo("请输入地址", "请输入 HTTPS 规则库 JSON 地址。")
            return
        destination = self.app_root / "rules" / "amazon_rules.override.json"
        self._start_async(
            "rule_update",
            lambda: RuleLibrary.update_from_url(url, destination),
            "正在更新 Amazon 规则库...",
        )

    def run_rule_preflight(self) -> None:
        try:
            project = self.collect_project(commit_review=False)
            expected = BriefGenerator().selected_instance_ids(project)
            briefs = self.current_briefs if [self._brief_key(item) for item in self.current_briefs] == expected else []
            if not briefs:
                project.options.optimize_copy_with_ai = False
                project.options.generate_ai_images = False
                briefs = BriefGenerator().generate(project)
            report = self._rule_library().preflight(project, briefs)
            self.preflight_results = report.to_dict()
            self._display_preflight(report.to_dict())
            self.status_var.set(f"预检完成：{len(report.errors)} 个错误，{len(report.warnings)} 个警告")
        except Exception as exc:
            messagebox.showerror("预检失败", str(exc))

    def _display_preflight(self, report: dict[str, Any]) -> None:
        self.preflight_tree.delete(*self.preflight_tree.get_children())
        self.rule_version_var.set(
            f"{report.get('library_version', '-')}（复核日期 {report.get('last_reviewed', '-')}）｜"
            f"站点 {report.get('marketplace_code', '-')}｜品类 {report.get('category_key', '-')}"
        )
        for index, item in enumerate(report.get("findings", [])):
            message = f"{item.get('message', '')}｜建议：{item.get('suggestion', '')}"
            self.preflight_tree.insert(
                "", END, iid=f"finding-{index}",
                values=(item.get("severity", ""), item.get("rule_id", ""), item.get("scope", ""), message),
            )

    def new_project(self) -> None:
        selected = default_module_selection()
        instances = [
            self._make_instance(code, channel)
            for channel in ("主图", "高级A+", "Brand Story", "品牌旗舰店")
            for code in selected[channel]
        ]
        order = [item.instance_id for item in instances]
        project = ProductProject(
            project_name="行李箱主图与A+需求",
            sku="CASE-001",
            brand="YourBrand",
            product_name_zh="旅行行李箱",
            product_name_en="Travel Suitcase",
            category="Luggage / Suitcases",
            marketplace="Amazon DE — 德国 (amazon.de)",
            target_audience="商务旅行者、家庭与城市短途旅行者",
            positioning="耐用、轻便、现代的中高端旅行箱",
            package_contents="行李箱、说明书、保修卡",
            variants=[
                ProductVariant(name="20寸", sku="CASE-20"),
                ProductVariant(name="22寸", sku="CASE-22"),
                ProductVariant(name="24寸", sku="CASE-24"),
            ],
            selected_modules=selected,
            module_instances=instances,
            module_order=order,
            options=GenerationOptions(
                optimize_copy_with_ai=True,
                generate_ai_images=bool(os.getenv("OPENAI_API_KEY")),
                generate_german_composites=True,
                text_model="gpt-5.6-terra",
                image_model="gpt-image-2",
                output_root=str(self.app_root / "outputs"),
            ),
        )
        self.populate_project(project)

    def add_variant(self) -> None:
        values = {key: var.get().strip() for key, var in self.variant_vars.items()}
        if not values["name"] and not values["size"]:
            messagebox.showerror("无法新增", "请至少填写变体名称或尺寸。")
            return
        self.variants.append(ProductVariant(**values))
        self._refresh_variant_tree()
        self._clear_variant_editor()

    def update_variant(self) -> None:
        """Backward-compatible explicit save entry point; the UI now auto-saves."""
        if self.variant_current_index is None:
            messagebox.showinfo("请选择变体", "请先选择需要更新的变体。")
            return
        self._autosave_variant()
        self.status_var.set("所选变体已保存")

    @staticmethod
    def _duplicate_variant_value(source: ProductVariant) -> ProductVariant:
        values = {key: getattr(source, key) for key, _label in VARIANT_FIELDS}
        values["name"] = f"{source.name}（副本）" if source.name else "副本"
        values["sku"] = f"{source.sku}-COPY" if source.sku else ""
        return ProductVariant(**values)

    def duplicate_variant(self) -> None:
        self._autosave_variant()
        selection = self.variant_tree.selection()
        if not selection:
            messagebox.showinfo("请选择变体", "请先选择需要复制的变体。")
            return
        source_index = int(selection[0])
        duplicated = self._duplicate_variant_value(self.variants[source_index])
        self.variants.append(duplicated)
        copied_index = len(self.variants) - 1
        self._refresh_variant_tree(select_index=copied_index)
        self._load_variant_editor()
        self.status_var.set(f"已复制变体：{duplicated.name}")

    def remove_variant(self) -> None:
        selection = self.variant_tree.selection()
        if not selection:
            return
        self.variants.pop(int(selection[0]))
        self.variant_current_index = None
        self._refresh_variant_tree()
        self._clear_variant_editor()

    def _refresh_variant_tree(self, select_index: int | None = None) -> None:
        self.variant_tree.delete(*self.variant_tree.get_children())
        for index, item in enumerate(self.variants):
            self.variant_tree.insert("", END, iid=str(index), values=[getattr(item, key) for key, _label in VARIANT_FIELDS])
        if select_index is not None and 0 <= select_index < len(self.variants):
            self.variant_tree.selection_set(str(select_index))

    def _load_variant_editor(self, _event=None) -> None:
        selection = self.variant_tree.selection()
        if not selection:
            return
        selected_index = int(selection[0])
        if self.variant_current_index is not None and self.variant_current_index != selected_index:
            self._autosave_variant(index=self.variant_current_index)
        self.variant_current_index = selected_index
        item = self.variants[selected_index]
        self.variant_editor_loading = True
        try:
            for key, _label in VARIANT_FIELDS:
                self.variant_vars[key].set(getattr(item, key))
        finally:
            self.variant_editor_loading = False

    def _autosave_variant(self, _event=None, index: int | None = None) -> None:
        if self.variant_editor_loading:
            return
        target_index = self.variant_current_index if index is None else index
        if target_index is None or not 0 <= target_index < len(self.variants):
            return
        item = ProductVariant(**{key: var.get().strip() for key, var in self.variant_vars.items()})
        self.variants[target_index] = item
        iid = str(target_index)
        if self.variant_tree.exists(iid):
            self.variant_tree.item(iid, values=[getattr(item, key) for key, _label in VARIANT_FIELDS])

    def _clear_variant_editor(self) -> None:
        self.variant_current_index = None
        self.variant_editor_loading = True
        try:
            for var in self.variant_vars.values():
                var.set("")
        finally:
            self.variant_editor_loading = False

    def pick_logo(self) -> None:
        path = filedialog.askopenfilename(title="选择品牌Logo", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            colors = extract_logo_palette(path)
            if not colors:
                raise ValueError("未识别到可用颜色。")
            self.logo_image_path = path
            self.logo_var.set(f"{Path(path).name}｜识别：{palette_text(colors)}")
            self.scalar_vars["brand_colors"].set(palette_text(colors))
        except Exception as exc:
            messagebox.showerror("配色识别失败", str(exc))

    def _refresh_image_labels(self) -> None:
        self.product_image_var.set(f"已选择 {len(self.product_images)} 张" if self.product_images else "尚未选择")
        self.competitor_image_var.set(f"已选择 {len(self.competitor_images)} 张" if self.competitor_images else "尚未选择")

    def pick_product_images(self) -> None:
        paths = filedialog.askopenfilenames(title="选择产品图片", filetypes=[("图片", "*.jpg *.jpeg *.png *.webp"), ("所有文件", "*.*")])
        if paths:
            self.product_images = list(paths)
            self._refresh_image_labels()

    def pick_competitor_images(self) -> None:
        paths = filedialog.askopenfilenames(title="选择竞品图片", filetypes=[("图片", "*.jpg *.jpeg *.png *.webp"), ("所有文件", "*.*")])
        if paths:
            self.competitor_images = list(paths)
            self._refresh_image_labels()

    def pick_output_root(self) -> None:
        path = filedialog.askdirectory(title="选择输出文件夹", initialdir=self.output_root_var.get())
        if path:
            self.output_root_var.set(path)

    def import_market_excel(self) -> None:
        path = filedialog.askopenfilename(title="导入竞品与关键词Excel", filetypes=[("Excel", "*.xlsx *.xlsm"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            competitors, keywords = self.market_manager.import_data(Path(path))
            self._populate_competitors_text(competitors)
            self._populate_keywords_text(keywords)
            self.imported_market_data_path = path
            self.import_source_var.set(f"{Path(path).name}｜竞品{len(competitors)}条，关键词{len(keywords)}条")
            self.market_input_mode_var.set("excel")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    def export_market_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出竞品与关键词模板",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile="竞品关键词导入模板.xlsx",
        )
        if path:
            try:
                self.market_manager.export_template(Path(path))
                messagebox.showinfo("模板已导出", f"模板已保存：\n{path}")
            except Exception as exc:
                messagebox.showerror("导出失败", str(exc))

    def _update_module_count(self, channel: str) -> None:
        count = sum(1 for item in self.module_instances if item.channel == channel)
        suffix = "（默认9张）" if channel == "主图" else "（默认7个）" if channel == "高级A+" else ""
        self.module_count_vars[channel].set(f"已添加：{count}{suffix}")

    @staticmethod
    def _make_instance(code: str, channel: str, prompt: str = "") -> ModuleInstance:
        return ModuleInstance(
            instance_id=f"{code}__{uuid.uuid4().hex[:10]}",
            module_code=code,
            channel=channel,
            custom_prompt=prompt,
        )

    def add_module_instance(self, channel: str) -> None:
        selection = self.module_lists[channel].curselection()
        if not selection:
            messagebox.showinfo("请选择模块", "请在模块库中先选择一个模块；双击也可以重复添加。")
            return
        spec = CATALOGS[channel][selection[0]]
        instance = self._make_instance(spec.code, channel)
        self.module_instances.append(instance)
        self.module_order.append(instance.instance_id)
        self._update_module_count(channel)
        self._sync_module_selection()
        self.confirm_tree.selection_set(instance.instance_id)
        self.module_selected_tree.selection_set(instance.instance_id)
        self.module_selected_tree.see(instance.instance_id)
        self.confirm_count_var.set(f"已确认 {len(self.module_instances)} 个模块")
        self.status_var.set(f"已添加：{spec.name}（可继续重复添加）")

    def duplicate_module_instance(self, tree: ttk.Treeview | None = None) -> None:
        selection = tuple(tree.selection()) if tree else self._selected_module_ids()
        if not selection:
            messagebox.showinfo("请选择实例", "请先选择要复制的模块实例。")
            return
        source = next((item for item in self.module_instances if item.instance_id == selection[0]), None)
        if not source:
            return
        clone = self._make_instance(source.module_code, source.channel, source.custom_prompt)
        index = self.module_order.index(source.instance_id) + 1
        self.module_instances.insert(index, clone)
        self.module_order.insert(index, clone.instance_id)
        self._sync_module_selection()
        self.confirm_tree.selection_set(clone.instance_id)
        self._update_module_count(source.channel)

    def remove_module_instance(self) -> None:
        self.remove_module_instances()

    def _selected_module_ids(self) -> tuple[str, ...]:
        if hasattr(self, "confirm_tree") and self.confirm_tree.selection():
            return tuple(self.confirm_tree.selection())
        if hasattr(self, "module_selected_tree"):
            return tuple(self.module_selected_tree.selection())
        return ()

    def remove_module_instances(self, tree: ttk.Treeview | None = None) -> None:
        selection = tuple(tree.selection()) if tree else self._selected_module_ids()
        if not selection:
            messagebox.showinfo("请选择实例", "请先选择一个或多个已添加模块。")
            return
        if len(selection) > 1 and not messagebox.askyesno("批量删除", f"确定删除选中的 {len(selection)} 个模块吗？"):
            return
        selected = set(selection)
        affected = {item.channel for item in self.module_instances if item.instance_id in selected}
        self.module_instances = [item for item in self.module_instances if item.instance_id not in selected]
        self.module_order = [item for item in self.module_order if item not in selected]
        for instance_id in selected:
            self.module_prompts.pop(instance_id, None)
            self.copy_overrides.pop(instance_id, None)
            self.copy_versions.pop(instance_id, None)
            self.review_statuses.pop(instance_id, None)
            self.review_notes.pop(instance_id, None)
            self.self_check_results.pop(instance_id, None)
        self.current_briefs = [item for item in self.current_briefs if item.instance_id not in selected]
        self._sync_module_selection()
        self._refresh_review_tree()
        for channel in affected:
            self._update_module_count(channel)
        self.status_var.set(f"已删除 {len(selected)} 个模块")

    def confirm_modules(self) -> None:
        self._sync_module_selection()
        self.tabs.select(self.confirm_tab)

    @property
    def confirm_tab(self):
        return self.tabs.tabs()[3]

    def _sync_module_selection(self) -> None:
        self._save_module_prompt()
        by_id = {item.instance_id: item for item in self.module_instances}
        self.module_order = [instance_id for instance_id in self.module_order if instance_id in by_id]
        self.module_order.extend(item.instance_id for item in self.module_instances if item.instance_id not in self.module_order)
        self.module_instances = [by_id[instance_id] for instance_id in self.module_order]
        self._normalize_main_white()
        self.confirm_tree.delete(*self.confirm_tree.get_children())
        if hasattr(self, "module_selected_tree"):
            self.module_selected_tree.delete(*self.module_selected_tree.get_children())
        for index, instance in enumerate(self.module_instances, 1):
            spec = CATALOG_BY_CODE[instance.module_code]
            prompt = instance.custom_prompt or self.module_prompts.get(instance.instance_id, "")
            self.confirm_tree.insert(
                "",
                END,
                iid=instance.instance_id,
                values=(index, spec.channel, spec.name, instance.instance_id, spec.pc_size, spec.mobile_size, prompt[:90]),
            )
            if hasattr(self, "module_selected_tree"):
                self.module_selected_tree.insert(
                    "", END, iid=instance.instance_id,
                    values=(index, spec.channel, spec.name, instance.instance_id),
                )
        self.confirm_count_var.set(f"已确认 {len(self.module_order)} 个模块")
        self.current_prompt_code = ""
        self.module_prompt_text.delete("1.0", END)

    def _normalize_main_white(self) -> None:
        main_instances = [item for item in self.module_instances if item.channel == "主图"]
        white = next((item for item in main_instances if item.module_code == "MAIN_WHITE"), None)
        if not white or not main_instances or main_instances[0].instance_id == white.instance_id:
            return
        first_main_index = self.module_instances.index(main_instances[0])
        self.module_instances.remove(white)
        self.module_instances.insert(first_main_index, white)
        self.module_order = [item.instance_id for item in self.module_instances]

    def _on_confirm_select(self, _event=None) -> None:
        selection = self.confirm_tree.selection()
        new_code = selection[0] if selection else ""
        if new_code == self.current_prompt_code:
            return
        self._save_module_prompt()
        self.current_prompt_code = new_code
        self.module_prompt_text.delete("1.0", END)
        if new_code:
            self.module_prompt_text.insert("1.0", self.module_prompts.get(new_code, ""))

    def _save_module_prompt(self) -> None:
        if not self.current_prompt_code:
            return
        text = self.module_prompt_text.get("1.0", END).strip()
        self.module_prompts[self.current_prompt_code] = text
        instance = next((item for item in self.module_instances if item.instance_id == self.current_prompt_code), None)
        if instance:
            instance.custom_prompt = text
        if self.confirm_tree.exists(self.current_prompt_code):
            self.confirm_tree.set(self.current_prompt_code, "prompt", text[:90])

    def move_module(self, direction: int) -> None:
        selection = self.confirm_tree.selection()
        if not selection:
            return
        code = selection[0]
        index = self.module_order.index(code)
        target = index + direction
        if target < 0 or target >= len(self.module_order):
            return
        self.module_order[index], self.module_order[target] = self.module_order[target], self.module_order[index]
        by_id = {item.instance_id: item for item in self.module_instances}
        self.module_instances = [by_id[item_id] for item_id in self.module_order]
        self._normalize_main_white()
        self._sync_module_selection()
        self.confirm_tree.selection_set(code)
        self.confirm_tree.see(code)

    def _on_module_drag_start(self, event) -> None:
        self.drag_module_code = self.confirm_tree.identify_row(event.y)

    def _on_module_drag_motion(self, event) -> None:
        target = self.confirm_tree.identify_row(event.y)
        if self.drag_module_code and target and target != self.drag_module_code:
            index = self.confirm_tree.index(target)
            self.confirm_tree.move(self.drag_module_code, "", index)

    def _on_module_drag_end(self, _event) -> None:
        if not self.drag_module_code:
            return
        self._save_module_prompt()
        self.module_order = list(self.confirm_tree.get_children())
        by_id = {item.instance_id: item for item in self.module_instances}
        self.module_instances = [by_id[item_id] for item_id in self.module_order]
        moved = self.drag_module_code
        self.drag_module_code = ""
        self._normalize_main_white()
        self._sync_module_selection()
        if self.confirm_tree.exists(moved):
            self.confirm_tree.selection_set(moved)

    @staticmethod
    def _split_row(line: str, expected: int) -> list[str]:
        values = [part.strip() for part in line.split("|")]
        return (values + [""] * expected)[:expected]

    def _parse_competitors(self) -> list[Competitor]:
        items: list[Competitor] = []
        for line in self.competitors_text.get("1.0", END).splitlines():
            if line.strip():
                items.append(Competitor(*self._split_row(line, 7)))
        return items

    def _parse_keywords(self) -> list[Keyword]:
        items: list[Keyword] = []
        for line in self.keywords_text.get("1.0", END).splitlines():
            if not line.strip():
                continue
            term, language, intent, priority, notes = self._split_row(line, 5)
            try:
                priority_value = max(1, min(5, int(priority or 3)))
            except ValueError:
                priority_value = 3
            if term:
                items.append(Keyword(term, language or "en", intent or "feature", priority_value, notes))
        return items

    def _parse_custom_fields(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in self.custom_fields_text.get("1.0", END).splitlines():
            if line.strip():
                name, value = self._split_row(line, 2)
                if name:
                    result[name] = value
        return result

    def _variant_summary_field(self, field: str) -> str:
        values = [getattr(item, field).strip() for item in self.variants if getattr(item, field).strip()]
        return " / ".join(dict.fromkeys(values))

    def collect_project(self, commit_review: bool = True) -> ProductProject:
        self._autosave_variant()
        if commit_review:
            self._commit_review_edits()
        self._save_module_prompt()
        self._sync_module_selection()
        values = {key: var.get().strip() for key, var in self.scalar_vars.items()}
        values.update({key: widget.get("1.0", END).strip() for key, widget in self.text_widgets.items()})
        selected_modules = {
            channel: [item.module_code for item in self.module_instances if item.channel == channel]
            for channel in ("主图", "高级A+", "Brand Story", "品牌旗舰店")
        }
        if self.current_briefs:
            self.copy_overrides.update({self._brief_key(item): dict(item.copy) for item in self.current_briefs})
            self.review_statuses.update({self._brief_key(item): item.review_status for item in self.current_briefs})
            self.review_notes.update({self._brief_key(item): item.review_notes for item in self.current_briefs})
            self.self_check_results.update({self._brief_key(item): item.self_check_result for item in self.current_briefs})
        options = GenerationOptions(
            optimize_copy_with_ai=self.optimize_var.get(),
            generate_ai_images=self.generate_images_var.get(),
            generate_german_composites=self.german_composite_var.get(),
            image_quality=self.quality_var.get(),
            text_model=self.text_model_var.get(),
            image_model=self.image_model_var.get(),
            text_provider=self._provider_id("text"),
            text_protocol=self.ai_config_vars["text"]["protocol"].get(),
            text_base_url=self.ai_config_vars["text"]["base_url"].get().strip(),
            text_endpoint=self.ai_config_vars["text"]["endpoint"].get().strip(),
            text_api_key_env=self.ai_config_vars["text"]["key_env"].get().strip(),
            text_extra_headers=self.ai_config_vars["text"]["extra_headers"].get().strip(),
            image_provider=self._provider_id("image"),
            image_protocol=self.ai_config_vars["image"]["protocol"].get(),
            image_base_url=self.ai_config_vars["image"]["base_url"].get().strip(),
            image_endpoint=self.ai_config_vars["image"]["endpoint"].get().strip(),
            image_api_key_env=self.ai_config_vars["image"]["key_env"].get().strip(),
            image_extra_headers=self.ai_config_vars["image"]["extra_headers"].get().strip(),
            auto_failover=self.auto_failover_var.get(),
            text_fallback_provider=self.text_fallback_provider_var.get().strip(),
            text_fallback_model=self.text_fallback_model_var.get().strip(),
            image_fallback_provider=self.image_fallback_provider_var.get().strip(),
            image_fallback_model=self.image_fallback_model_var.get().strip(),
            max_retries=max(0, min(8, int(self.max_retries_var.get() or 2))),
            requests_per_minute=max(1, min(600, int(self.requests_per_minute_var.get() or 10))),
            cache_enabled=self.cache_enabled_var.get(),
            structure_lock=self.structure_lock_var.get(),
            similarity_threshold=max(0.0, min(100.0, float(self.similarity_threshold_var.get() or 35))),
            ocr_check=self.ocr_check_var.get(),
            back_translation_check=self.back_translation_check_var.get(),
            overflow_check=self.overflow_check_var.get(),
            image_compliance_check=self.image_compliance_check_var.get(),
            rule_library_path=self.rule_library_path_var.get().strip(),
            rule_update_url=self.rule_update_url_var.get().strip(),
            output_root=self.output_root_var.get().strip() or "outputs",
        )
        return ProductProject(
            **values,
            marketplace=self.marketplace_var.get(),
            size=self._variant_summary_field("size"),
            color=self._variant_summary_field("color"),
            weight=self._variant_summary_field("weight"),
            capacity=self._variant_summary_field("capacity"),
            variants=list(self.variants),
            product_image_paths=list(self.product_images),
            competitor_image_paths=list(self.competitor_images),
            logo_image_path=self.logo_image_path,
            competitors=self._parse_competitors(),
            keywords=self._parse_keywords(),
            custom_fields=self._parse_custom_fields(),
            competitor_input_mode=self.market_input_mode_var.get(),
            keyword_input_mode=self.market_input_mode_var.get(),
            imported_market_data_path=self.imported_market_data_path,
            selected_modules=selected_modules,
            module_instances=list(self.module_instances),
            module_order=list(self.module_order),
            module_prompts=dict(self.module_prompts),
            copy_overrides=dict(self.copy_overrides),
            copy_versions=dict(self.copy_versions),
            review_statuses=dict(self.review_statuses),
            review_notes=dict(self.review_notes),
            self_check_results=dict(self.self_check_results),
            preflight_results=dict(self.preflight_results),
            options=options,
        )

    def populate_project(self, project: ProductProject) -> None:
        defaults = {key: default for key, _label, default in SCALAR_FIELDS}
        for key in self.scalar_vars:
            self.scalar_vars[key].set(getattr(project, key, defaults.get(key, "")))
        self.marketplace_var.set(project.marketplace if project.marketplace in AMAZON_MARKETPLACES else "Amazon DE — 德国 (amazon.de)")
        for key, widget in self.text_widgets.items():
            widget.delete("1.0", END)
            widget.insert("1.0", getattr(project, key, ""))
        self.custom_fields_text.delete("1.0", END)
        for name, value in project.custom_fields.items():
            self.custom_fields_text.insert(END, f"{name} | {value}\n")
        self.variants = list(project.variants)
        if not self.variants and any((project.size, project.color, project.weight, project.capacity)):
            self.variants = [ProductVariant(name=project.model or "默认变体", sku=project.sku, size=project.size, color=project.color, weight=project.weight, capacity=project.capacity, price=project.price)]
        self._refresh_variant_tree()
        self.product_images = list(project.product_image_paths)
        self.competitor_images = list(project.competitor_image_paths)
        self.logo_image_path = project.logo_image_path
        self.logo_var.set(Path(project.logo_image_path).name if project.logo_image_path else "尚未导入Logo")
        self._refresh_image_labels()
        self._populate_competitors_text(project.competitors)
        self._populate_keywords_text(project.keywords)
        self.market_input_mode_var.set(project.competitor_input_mode or "manual")
        self.imported_market_data_path = project.imported_market_data_path
        self.import_source_var.set(Path(project.imported_market_data_path).name if project.imported_market_data_path else "未导入文件")
        self.module_instances = project.normalized_module_instances()
        self.module_order = [item.instance_id for item in self.module_instances]
        for channel, listbox in self.module_lists.items():
            listbox.selection_clear(0, END)
            if listbox.size():
                listbox.selection_set(0)
            self._update_module_count(channel)
        self.module_prompts = dict(project.module_prompts)
        for item in self.module_instances:
            if item.custom_prompt:
                self.module_prompts[item.instance_id] = item.custom_prompt
        self._sync_module_selection()
        self.copy_versions = dict(project.copy_versions)
        self.copy_overrides = dict(project.copy_overrides)
        self.review_statuses = dict(project.review_statuses)
        self.review_notes = dict(project.review_notes)
        self.self_check_results = dict(project.self_check_results)
        self.preflight_results = dict(project.preflight_results) if isinstance(project.preflight_results, dict) else {}
        self.current_briefs = []
        self._refresh_review_tree()
        self.optimize_var.set(project.options.optimize_copy_with_ai)
        self.generate_images_var.set(project.options.generate_ai_images)
        self.german_composite_var.set(project.options.generate_german_composites)
        self.quality_var.set(project.options.image_quality if project.options.image_quality in {"low", "medium", "high"} else "medium")
        self._load_ai_options(project.options)
        self.rule_library_path_var.set(project.options.rule_library_path)
        self.rule_update_url_var.set(project.options.rule_update_url)
        if self.preflight_results:
            self._display_preflight(self.preflight_results)
        else:
            self.preflight_tree.delete(*self.preflight_tree.get_children())
        self.output_root_var.set(project.options.output_root)
        self.last_output_dir = ""
        self.last_result = {}
        self.open_output_button.configure(state="disabled")
        self.status_var.set("项目已载入")

    def _populate_competitors_text(self, items: list[Competitor]) -> None:
        self.competitors_text.delete("1.0", END)
        for item in items:
            self.competitors_text.insert(END, " | ".join([item.asin, item.url, item.brand, item.price, item.selling_points, item.visual_notes, item.opportunity]) + "\n")

    def _populate_keywords_text(self, items: list[Keyword]) -> None:
        self.keywords_text.delete("1.0", END)
        for item in items:
            self.keywords_text.insert(END, " | ".join([item.term, item.language, item.intent, str(item.priority), item.notes]) + "\n")

    def save_project(self) -> None:
        try:
            project = self.collect_project()
        except Exception as exc:
            messagebox.showerror("无法保存", str(exc))
            return
        path = filedialog.asksaveasfilename(title="保存项目", defaultextension=".json", filetypes=[("项目文件", "*.json")], initialfile=f"{project.project_name}.json")
        if path:
            Path(path).write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo("已保存", f"项目已保存到：\n{path}")

    def load_project(self) -> None:
        path = filedialog.askopenfilename(title="打开项目", filetypes=[("项目文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.populate_project(ProductProject.from_dict(json.loads(Path(path).read_text(encoding="utf-8"))))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def generate_draft(self) -> None:
        try:
            project = self.collect_project()
            errors = [error for error in project.validate() if "AI图片" not in error]
            if errors:
                raise ValueError("\n".join(errors))
        except Exception as exc:
            messagebox.showerror("请补充信息", str(exc))
            return
        ai_client = self._build_ai_client(project.options)
        self._start_async("draft", lambda: BriefGenerator(ai_client).generate(project), "正在生成六语文案草稿...")

    def _refresh_review_tree(self) -> None:
        if not hasattr(self, "review_tree"):
            return
        self.review_tree.delete(*self.review_tree.get_children())
        for brief in self.current_briefs:
            key = self._brief_key(brief)
            self.review_tree.insert("", END, iid=key, values=(brief.channel, brief.module_name, f"V{brief.copy_version}", brief.review_status))
        if self.current_briefs:
            first = self._brief_key(self.current_briefs[0])
            self.review_tree.selection_set(first)
            self.current_review_code = ""
            self._on_review_select()
        else:
            self.current_review_code = ""
            self._set_text(self.current_copy_text, "", editable=True)
            self._set_text(self.previous_copy_text, "")
            self._set_text(self.self_check_text, "")

    @staticmethod
    def _brief_key(brief: ModuleBrief) -> str:
        return brief.instance_id or brief.module_code

    def _brief_by_code(self, code: str) -> ModuleBrief | None:
        return next((item for item in self.current_briefs if self._brief_key(item) == code), None)

    def _on_review_select(self, _event=None) -> None:
        selection = self.review_tree.selection()
        new_code = selection[0] if selection else ""
        if new_code == self.current_review_code:
            return
        self._commit_review_edits()
        self.current_review_code = new_code
        self._load_review_editor()

    def _load_review_editor(self) -> None:
        brief = self._brief_by_code(self.current_review_code)
        if not brief:
            return
        self.review_status_var.set(brief.review_status)
        self.review_notes_var.set(brief.review_notes)
        text = brief.copy_for(self.current_review_language)
        self._set_text(self.current_copy_text, text, editable=brief.module_code != "MAIN_WHITE")
        self._set_text(self.self_check_text, brief.self_check_result)
        history = self.copy_versions.get(self._brief_key(brief), self.copy_versions.get(brief.module_code, []))
        values = [f"V{item.get('version', 1)}｜{item.get('reason', '')}" for item in reversed(history)]
        self.compare_version_combo.configure(values=values)
        self.compare_version_var.set(values[1] if len(values) > 1 else values[0] if values else "")
        self._load_compare_version()

    def _on_language_change(self, _event=None) -> None:
        self._commit_review_edits()
        self.current_review_language = self.review_language_var.get().split(" ", 1)[0]
        self._load_review_editor()

    @staticmethod
    def _set_text(widget: tk.Text, value: str, editable: bool = False) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="normal" if editable else "disabled")

    def _load_compare_version(self) -> None:
        brief = self._brief_by_code(self.current_review_code)
        if not brief or not self.compare_version_var.get():
            self._set_text(self.previous_copy_text, "")
            return
        token = self.compare_version_var.get().split("｜", 1)[0].lstrip("V")
        history = self.copy_versions.get(self._brief_key(brief), self.copy_versions.get(brief.module_code, []))
        version = next((item for item in history if str(item.get("version")) == token), None)
        text = str((version or {}).get("copy", {}).get(self.current_review_language, ""))
        self._set_text(self.previous_copy_text, text)

    def _commit_review_edits(self) -> None:
        brief = self._brief_by_code(self.current_review_code)
        if not brief:
            return
        if brief.module_code != "MAIN_WHITE":
            value = self.current_copy_text.get("1.0", END).strip()
            if value != brief.copy_for(self.current_review_language):
                brief.copy[self.current_review_language] = value
                self._record_version(brief, f"手动修改{LANGUAGE_LABELS[self.current_review_language]}")
        brief.review_status = self.review_status_var.get() or "待审核"
        brief.review_notes = self.review_notes_var.get().strip()
        key = self._brief_key(brief)
        self.copy_overrides[key] = dict(brief.copy)
        self.review_statuses[key] = brief.review_status
        self.review_notes[key] = brief.review_notes
        if self.review_tree.exists(key):
            self.review_tree.set(key, "version", f"V{brief.copy_version}")
            self.review_tree.set(key, "status", brief.review_status)

    def _record_version(self, brief: ModuleBrief, reason: str) -> None:
        key = self._brief_key(brief)
        history = self.copy_versions.setdefault(key, [])
        version_number = max([int(item.get("version", 0)) for item in history] + [0]) + 1
        brief.copy_version = version_number
        history.append(
            {
                "version": version_number,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "copy": dict(brief.copy),
            }
        )
        self.copy_overrides[key] = dict(brief.copy)

    def save_current_copy(self) -> None:
        self._commit_review_edits()
        self._load_review_editor()
        self.status_var.set("当前文案已保存为新版本")

    def regenerate_selected_copy(self) -> None:
        self._commit_review_edits()
        brief = self._brief_by_code(self.current_review_code)
        if not brief:
            messagebox.showinfo("请选择模块", "请先选择要重生成文案的模块。")
            return
        project = self.collect_project(commit_review=False)
        instruction = self.global_revision_var.get().strip()
        generator = BriefGenerator(self._build_ai_client(project.options))
        self._start_async(
            "regen_copy",
            lambda: (self._brief_key(brief), generator.regenerate_copy(project, brief, instruction)),
            f"正在重生成 {brief.module_name} 文案...",
        )

    def regenerate_all_copy(self) -> None:
        self._commit_review_edits()
        if not self.current_briefs:
            messagebox.showinfo("请先生成草稿", "先生成六语文案草稿，再进行全局重生成。")
            return
        project = self.collect_project(commit_review=False)
        instruction = self.global_revision_var.get().strip()
        generator = BriefGenerator(self._build_ai_client(project.options))
        self._start_async(
            "regen_all",
            lambda: generator.regenerate_all_copy(project, self.current_briefs, instruction),
            "正在全局重生成六语文案...",
        )

    def check_copy(self, all_modules: bool) -> None:
        self._commit_review_edits()
        if not self.current_briefs:
            messagebox.showinfo("请先生成草稿", "先生成六语文案草稿，再运行自查。")
            return
        briefs = self.current_briefs if all_modules else [self._brief_by_code(self.current_review_code)]
        briefs = [item for item in briefs if item]
        if not briefs:
            return
        project = self.collect_project(commit_review=False)
        codes = [self._brief_key(item) for item in briefs]
        generator = BriefGenerator(self._build_ai_client(project.options))
        self._start_async(
            "self_check",
            lambda: (codes, generator.self_check(project, briefs)),
            "正在自查文案、翻译和声明风险...",
        )

    def regenerate_selected_image(self, mask_path: Path | None = None) -> None:
        self._commit_review_edits()
        brief = self._brief_by_code(self.current_review_code)
        if not brief:
            messagebox.showinfo("请选择模块", "请先选择要局部重生成图片的模块。")
            return
        if not self.last_output_dir:
            messagebox.showinfo("请先生成完整输出", "局部图片重生成会刷新现有Excel，请先在第8步生成一次完整输出。")
            return
        project = self.collect_project(commit_review=False)
        service = GenerationService(self.app_root, self._build_ai_client(project.options))
        self._start_async(
            "regen_image",
            lambda: service.regenerate_image(
                project,
                self.current_briefs,
                self._brief_key(brief),
                Path(self.last_output_dir),
                mask_path=mask_path,
            ),
            f"正在局部重生成 {brief.module_name} 图片...",
        )

    def open_mask_editor(self) -> None:
        self._commit_review_edits()
        brief = self._brief_by_code(self.current_review_code)
        if not brief:
            messagebox.showinfo("请选择模块", "请先选择要局部重生的模块。")
            return
        source = Path(brief.ai_effect_image) if brief.ai_effect_image else None
        if not source or not source.is_file():
            source = Path(self.product_images[0]) if self.product_images else None
        if not source or not source.is_file():
            messagebox.showinfo("缺少图片", "请先生成该模块图片，或上传一张产品参考图。")
            return
        destination = self.app_root / ".cache" / "masks" / f"{self._brief_key(brief)}-{datetime.now():%Y%m%d-%H%M%S}.png"
        MaskEditor(self.root, source, destination, lambda path: self.regenerate_selected_image(path))

    def start_generation(self) -> None:
        self._commit_review_edits()
        try:
            project = self.collect_project(commit_review=False)
            errors = project.validate()
            if errors:
                raise ValueError("\n".join(errors))
        except Exception as exc:
            messagebox.showerror("请补充信息", str(exc))
            return
        expected = BriefGenerator().selected_instance_ids(project)
        briefs = self.current_briefs if [self._brief_key(item) for item in self.current_briefs] == expected else None
        service = GenerationService(self.app_root, self._build_ai_client(project.options))

        def worker() -> dict[str, Any]:
            def progress(message: str, done: int, total: int) -> None:
                self.events.put(("progress", (message, done, total)))

            return service.run(project, progress, briefs)

        self.tabs.select(self.tabs.tabs()[7])
        self.progressbar["value"] = 0
        self._start_async("export", worker, "正在生成Excel与参考图片...")

    def _start_async(self, operation: str, function: Callable[[], object], message: str) -> None:
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前任务完成。")
            return
        self.busy = True
        self.status_var.set(message)
        self.progress_var.set(message)
        self.generate_button.configure(state="disabled")

        def run() -> None:
            try:
                self.events.put(("operation_done", (operation, function())))
            except Exception as exc:
                self.events.put(("operation_error", (str(exc), traceback.format_exc())))

        threading.Thread(target=run, daemon=True).start()
        self.root.after(150, self._poll_events)

    def _poll_events(self) -> None:
        keep_polling = self.busy
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                message, done, total = payload  # type: ignore[misc]
                self.progress_var.set(str(message))
                self.progressbar["value"] = (int(done) / max(1, int(total))) * 100
            elif event == "operation_done":
                operation, result = payload  # type: ignore[misc]
                self.busy = False
                keep_polling = False
                self.generate_button.configure(state="normal")
                self._handle_operation_done(str(operation), result)
            elif event == "operation_error":
                message, details = payload  # type: ignore[misc]
                self.busy = False
                keep_polling = False
                self.generate_button.configure(state="normal")
                self.status_var.set("操作失败")
                self.progress_var.set("操作失败")
                messagebox.showerror("操作失败", f"{message}\n\n详细记录：\n{details[-1800:]}")
        if keep_polling:
            self.root.after(180, self._poll_events)

    def _handle_operation_done(self, operation: str, result: object) -> None:
        if operation.startswith("connection:"):
            data = dict(result)  # type: ignore[arg-type]
            kind = operation.split(":", 1)[1]
            models = list(data.get("models", []))
            self.ai_model_combos[kind].configure(values=models)
            self.status_var.set(f"{data.get('provider')} 连接成功")
            messagebox.showinfo(
                "真实连接测试成功",
                f"供应商：{data.get('provider')}\n测试模型：{data.get('tested_model')}\n"
                f"延迟：{data.get('latency_ms')} ms\n可识别模型：{data.get('model_count')} 个\n"
                f"测试回复：{data.get('preview') or '图片连接无需生成测试图'}",
            )
        elif operation.startswith("models:"):
            kind = operation.split(":", 1)[1]
            models = list(result)  # type: ignore[arg-type]
            self.ai_model_combos[kind].configure(values=models)
            if models and self.ai_config_vars[kind]["model"].get() not in models:
                self.ai_config_vars[kind]["model"].set(models[0])
            self.status_var.set(f"已获取 {len(models)} 个模型")
            messagebox.showinfo("模型列表已更新", f"共获取 {len(models)} 个模型，可在下拉框中选择。")
        elif operation == "rule_update":
            library = result
            destination = self.app_root / "rules" / "amazon_rules.override.json"
            self.rule_library_path_var.set(str(destination))
            self.rule_version_var.set(library.version_label)  # type: ignore[attr-defined]
            self.status_var.set("Amazon 规则库在线更新完成")
        elif operation == "draft":
            self.current_briefs = list(result)  # type: ignore[arg-type]
            for brief in self.current_briefs:
                key = self._brief_key(brief)
                if not self.copy_versions.get(key):
                    self._record_version(brief, "生成初稿")
                else:
                    brief.copy_version = max(int(item.get("version", 1)) for item in self.copy_versions[key])
            self._refresh_review_tree()
            self.tabs.select(self.tabs.tabs()[4])
            self.status_var.set(f"已生成 {len(self.current_briefs)} 个模块的六语草稿")
        elif operation == "regen_copy":
            code, data = result  # type: ignore[misc]
            brief = self._brief_by_code(str(code))
            if brief:
                BriefGenerator._apply_ai_item(brief, data)
                self._record_version(brief, "局部AI重生成")
                self._load_review_editor()
                self.review_tree.set(code, "version", f"V{brief.copy_version}")
            self.status_var.set("局部文案重生成完成")
        elif operation == "regen_all":
            self.current_briefs = list(result)  # type: ignore[arg-type]
            for brief in self.current_briefs:
                self._record_version(brief, "全局AI重生成")
            self._refresh_review_tree()
            self.status_var.set("全局文案重生成完成")
        elif operation == "self_check":
            codes, checks = result  # type: ignore[misc]
            selected_code = self.current_review_code
            for code in codes:
                brief = self._brief_by_code(code)
                if not brief:
                    continue
                brief.self_check_result = checks.get(code, "未返回检查结果")
                self.self_check_results[code] = brief.self_check_result
                if "高风险" in brief.self_check_result or "建议修改" in brief.self_check_result:
                    brief.review_status = "需修改"
            self._refresh_review_tree()
            if selected_code and self.review_tree.exists(selected_code):
                self.review_tree.selection_set(selected_code)
                self.current_review_code = ""
                self._on_review_select()
            self.status_var.set("ChatGPT文案自查完成")
        elif operation in {"export", "regen_image"}:
            self.last_result = dict(result)  # type: ignore[arg-type]
            preflight = self.last_result.get("preflight")
            if isinstance(preflight, dict):
                self.preflight_results = preflight
                self._display_preflight(preflight)
            self.last_output_dir = str(self.last_result.get("output_dir", ""))
            self.open_output_button.configure(state="normal" if self.last_output_dir else "disabled")
            self.progressbar["value"] = 100
            self.progress_var.set("生成完成" if operation == "export" else "局部图片与Excel已更新")
            self.status_var.set(self.progress_var.get())
            message = "Excel与图片资源已生成" if operation == "export" else "局部图片已重生成，Excel与ZIP已刷新"
            messagebox.showinfo(message, self.last_output_dir)

    def open_output(self) -> None:
        if self.last_output_dir and Path(self.last_output_dir).is_dir():
            os.startfile(self.last_output_dir)  # type: ignore[attr-defined]


def launch_app(app_root: Path) -> None:
    root = tk.Tk()
    AmazonImageBriefApp(root, app_root)
    root.mainloop()
