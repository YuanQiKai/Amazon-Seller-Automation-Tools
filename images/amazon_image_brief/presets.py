from __future__ import annotations


# Active third-party seller stores listed by Amazon Global Selling and its 2025 market guide.
AMAZON_MARKETPLACES = [
    "Amazon US — 美国 (amazon.com)",
    "Amazon CA — 加拿大 (amazon.ca)",
    "Amazon MX — 墨西哥 (amazon.com.mx)",
    "Amazon BR — 巴西 (amazon.com.br)",
    "Amazon UK — 英国 (amazon.co.uk)",
    "Amazon DE — 德国 (amazon.de)",
    "Amazon FR — 法国 (amazon.fr)",
    "Amazon IT — 意大利 (amazon.it)",
    "Amazon ES — 西班牙 (amazon.es)",
    "Amazon NL — 荷兰 (amazon.nl)",
    "Amazon SE — 瑞典 (amazon.se)",
    "Amazon PL — 波兰 (amazon.pl)",
    "Amazon BE — 比利时 (amazon.com.be)",
    "Amazon IE — 爱尔兰 (amazon.ie)",
    "Amazon TR — 土耳其 (amazon.com.tr)",
    "Amazon JP — 日本 (amazon.co.jp)",
    "Amazon IN — 印度 (amazon.in)",
    "Amazon AU — 澳大利亚 (amazon.com.au)",
    "Amazon SG — 新加坡 (amazon.sg)",
    "Amazon AE — 阿联酋 (amazon.ae)",
    "Amazon SA — 沙特阿拉伯 (amazon.sa)",
    "Amazon EG — 埃及 (amazon.eg)",
    "Amazon ZA — 南非 (amazon.co.za)",
]


BRAND_TONE_PRESETS = [
    "高级、清晰、可信",
    "专业、理性、参数导向",
    "现代、简洁、科技感",
    "温暖、亲和、家庭导向",
    "活力、年轻、户外探索",
    "克制、天然、可持续",
]


VISUAL_STYLE_PRESETS = [
    "真实产品摄影、克制信息图、充足留白",
    "欧洲现代旅行摄影、低饱和中性色、自然光",
    "高端棚拍、深色渐变背景、金属质感",
    "北欧极简、浅灰米白、柔和自然光",
    "科技蓝信息图、结构线稿、精确参数",
    "户外纪实、动态人物、自然环境",
    "家庭生活方式、温暖光线、真实互动",
]


FONT_PRESETS = [
    "Arial / Helvetica / Amazon Ember风格无衬线字体；数字与尺寸清晰易读",
    "Aptos / Arial；标题粗体、正文常规，适合多语种",
    "Montserrat / Arial；现代几何感，德语预留20%扩展空间",
    "Roboto / Arial；科技感、参数信息清晰",
    "Noto Sans / Arial；兼顾中英文与欧洲语言字形",
    "Playfair Display标题 + Arial正文；仅用于高端品牌氛围",
]


BRAND_COLOR_PRESETS = [
    "深海军蓝 #12233F / 香槟金 #D88A2A / 暖白 #F4F1EA",
    "科技蓝 #1769AA / 冰蓝 #DCEFFC / 深灰 #27313A",
    "高端黑 #171717 / 金色 #C8A45D / 象牙白 #F7F3EA",
    "自然绿 #496B52 / 沙色 #D8C6A5 / 米白 #F5F1E8",
    "活力橙 #E46B2E / 深蓝 #18324A / 浅灰 #EDF0F2",
    "极简灰 #2F3438 / 中灰 #9CA3A8 / 白色 #FFFFFF",
    "从品牌Logo自动识别",
]


TEXT_MODELS = [
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.6-luna",
    "gpt-6-astra",
]


IMAGE_MODELS = [
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
]


LANGUAGE_LABELS = {
    "en": "英语",
    "zh": "中文",
    "de": "德语",
    "fr": "法语",
    "it": "意大利语",
    "es": "西班牙语",
}


REVIEW_STATUSES = ["待审核", "审核中", "需修改", "已通过"]
