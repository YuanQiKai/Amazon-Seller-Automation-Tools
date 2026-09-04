from __future__ import annotations

from .models import ModuleSpec


MAIN_IMAGE_MODULES = [
    ModuleSpec("MAIN_WHITE", "主图", "产品白底首图", "2000×2000", "1200×1200（同图移动安全区）", "纯白背景、完整产品轮廓、首图合规", True, False),
    ModuleSpec("MAIN_COLOR_SIZE", "主图", "颜色与尺寸图", "2000×2000", "1200×1200", "颜色变体、尺寸、重量、容量和比例", True),
    ModuleSpec("MAIN_DETAIL", "主图", "细节特写图", "2000×2000", "1200×1200", "材质、纹理、接口、做工与关键部件", True),
    ModuleSpec("MAIN_FUNCTION", "主图", "功能展示图", "2000×2000", "1200×1200", "功能动作、效果路径及可验证参数", True),
    ModuleSpec("MAIN_AUDIENCE", "主图", "使用人群场景图", "2000×2000", "1200×1200", "目标人群与产品互动", True),
    ModuleSpec("MAIN_SCENE", "主图", "使用场景图", "2000×2000", "1200×1200", "真实使用环境、痛点与收益", True),
    ModuleSpec("MAIN_EXPLODED", "主图", "产品结构分解图", "2000×2000", "1200×1200", "结构层级、部件关系和工作原理", True),
    ModuleSpec("MAIN_SELLING", "主图", "核心卖点展现图", "2000×2000", "1200×1200", "集中呈现最强差异化购买理由", True),
    ModuleSpec("MAIN_CARE", "主图", "使用/维护/洗护/保修图", "2000×2000", "1200×1200", "步骤、保养、清洁、适用限制或保修", True),
    ModuleSpec("MAIN_COMPARISON", "主图", "规格或系列对比图", "2000×2000", "1200×1200", "品牌内部型号或规格差异", False),
    ModuleSpec("MAIN_PACKAGE", "主图", "包装清单图", "2000×2000", "1200×1200", "包装内含物和不包含物", False),
    ModuleSpec("MAIN_PROOF", "主图", "测试与信任证明图", "2000×2000", "1200×1200", "可验证测试、认证或参数证据", False),
]


PREMIUM_APLUS_MODULES = [
    ModuleSpec("APLUS_VIDEO_CAROUSEL", "高级A+", "优质视频图像轮播", "1464×600", "1200×900", "视频与图像组合讲述核心体验", True, True, "landscape"),
    ModuleSpec("APLUS_DUAL_IMAGE_TEXT", "高级A+", "包含文本的高级双图片", "2×650×350（建议）", "2×600×450（建议）", "双场景或双卖点并列", True, True, "landscape"),
    ModuleSpec("APLUS_BACKGROUND_TEXT", "高级A+", "包含文本的高级背景图片", "1464×600", "1200×900", "沉浸式品牌/产品英雄图", True, True, "landscape"),
    ModuleSpec("APLUS_VIDEO_TEXT", "高级A+", "包含文本的高级视频", "1464×600封面", "1200×900封面", "视频配标题和说明", True, True, "landscape"),
    ModuleSpec("APLUS_SINGLE_TEXT", "高级A+", "带文本的单张高级图片", "1464×600", "1200×900", "单一核心功能深讲", True, True, "landscape"),
    ModuleSpec("APLUS_SIMPLE_CAROUSEL", "高级A+", "高级简单的图像轮播", "1464×600/帧", "1200×900/帧", "多角度、步骤或场景轮播", True, True, "landscape"),
    ModuleSpec("APLUS_FULL_VIDEO", "高级A+", "高级全视频", "1464×600封面", "1200×900封面", "完整产品故事视频", True, True, "landscape"),
    ModuleSpec("APLUS_FOUR_IMAGE_TEXT", "高级A+", "高级四图片和文本", "4×300×225（建议）", "4×600×450（建议）", "四项卖点或四种使用场景", False, True, "landscape"),
    ModuleSpec("APLUS_FULL_IMAGE", "高级A+", "高级完整图片", "1464×600", "1200×900", "完整视觉叙事或品牌横幅", False, False, "landscape"),
    ModuleSpec("APLUS_NAV_CAROUSEL", "高级A+", "高级导航轮播", "1464×600/帧", "1200×900/帧", "可导航的产品系列或功能故事", False, True, "landscape"),
    ModuleSpec("APLUS_TECH_SPECS", "高级A+", "高级技术规格", "1464×600（画布）", "1200×900", "结构化参数和技术规格", False, True, "landscape"),
    ModuleSpec("APLUS_TEXT", "高级A+", "高级文本", "文本模块", "文本模块", "长文说明、品牌理念或使用指南", False, True, "landscape"),
    ModuleSpec("APLUS_COMPARE_1", "高级A+", "高级比较表1", "1464×600（画布）", "1200×900", "品牌内部产品基础对比", False, True, "landscape"),
    ModuleSpec("APLUS_COMPARE_2", "高级A+", "高级比较表2", "1464×600（画布）", "1200×900", "品牌内部产品功能对比", False, True, "landscape"),
    ModuleSpec("APLUS_COMPARE_3", "高级A+", "高级比较表3", "1464×600（画布）", "1200×900", "品牌内部产品场景对比", False, True, "landscape"),
    ModuleSpec("APLUS_HOTSPOT_1", "高级A+", "高级热点1", "1464×600", "1200×900", "单产品结构热点讲解", False, True, "landscape"),
    ModuleSpec("APLUS_HOTSPOT_2", "高级A+", "高级热点2", "1464×600", "1200×900", "场景化多热点讲解", False, True, "landscape"),
    ModuleSpec("APLUS_CAROUSEL_RULE", "高级A+", "高级轮播规则", "1464×600/帧", "1200×900/帧", "统一轮播构图、文字安全区与节奏", False, True, "landscape"),
    ModuleSpec("APLUS_QA", "高级A+", "高级问答", "文本/辅助图1464×600", "文本/辅助图1200×900", "消除购买顾虑的问答", False, True, "landscape"),
]


BRAND_STORY_MODULES = [
    ModuleSpec("BS_BACKGROUND", "Brand Story", "品牌故事背景", "1464×625", "463×625", "品牌起源、使命与情绪主视觉", True, True, "landscape"),
    ModuleSpec("BS_BRAND_FOCUS", "Brand Story", "品牌聚焦卡", "362×453", "362×453", "品牌Logo、价值主张与简短介绍", True, True, "portrait"),
    ModuleSpec("BS_PRODUCT_CARD", "Brand Story", "产品系列卡", "362×453", "362×453", "关联产品线或场景入口", True, True, "portrait"),
    ModuleSpec("BS_MISSION", "Brand Story", "品牌使命与承诺卡", "362×453", "362×453", "品牌价值、材料或服务承诺", False, True, "portrait"),
    ModuleSpec("BS_STORE_LINK", "Brand Story", "旗舰店引导卡", "362×453", "362×453", "引导访问品牌旗舰店", False, True, "portrait"),
]


BRAND_STORE_MODULES = [
    ModuleSpec("STORE_HERO", "品牌旗舰店", "首页英雄横幅", "3000×600", "1500×600", "品牌主张与核心产品入口", True, True, "landscape"),
    ModuleSpec("STORE_CATEGORY", "品牌旗舰店", "品类导航卡", "1500×1500", "1200×1200", "品类、系列或使用场景导航", True, True),
    ModuleSpec("STORE_FEATURE", "品牌旗舰店", "核心卖点横幅", "3000×1200", "1500×1200", "品牌级差异化卖点", True, True, "landscape"),
    ModuleSpec("STORE_LIFESTYLE", "品牌旗舰店", "生活方式场景", "3000×1500", "1500×1500", "品牌进入真实生活的方式", True, True, "landscape"),
    ModuleSpec("STORE_COLLECTION", "品牌旗舰店", "产品系列陈列", "3000×1200", "1500×1200", "多SKU或系列关系", False, True, "landscape"),
    ModuleSpec("STORE_VIDEO", "品牌旗舰店", "品牌视频封面", "3000×1500", "1500×1500", "品牌/产品视频入口", False, True, "landscape"),
    ModuleSpec("STORE_PROMO", "品牌旗舰店", "活动主题页横幅", "3000×1200", "1500×1200", "季节、活动或新品主题", False, True, "landscape"),
]


CATALOGS = {
    "主图": MAIN_IMAGE_MODULES,
    "高级A+": PREMIUM_APLUS_MODULES,
    "Brand Story": BRAND_STORY_MODULES,
    "品牌旗舰店": BRAND_STORE_MODULES,
}


CATALOG_BY_CODE = {spec.code: spec for specs in CATALOGS.values() for spec in specs}


def default_module_selection() -> dict[str, list[str]]:
    return {
        channel: [spec.code for spec in specs if spec.default_selected]
        for channel, specs in CATALOGS.items()
    }
