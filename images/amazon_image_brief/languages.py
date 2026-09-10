"""Supported copy locales. Chinese is always produced as the review translation."""
LANGUAGE_NAMES = {
    'de': '德语', 'en': '英语', 'fr': '法语', 'it': '意大利语', 'es': '西班牙语',
    'pt': '葡萄牙语', 'nl': '荷兰语', 'pl': '波兰语', 'sv': '瑞典语', 'da': '丹麦语',
    'nb': '挪威语', 'fi': '芬兰语', 'is': '冰岛语', 'cs': '捷克语', 'sk': '斯洛伐克语',
    'hu': '匈牙利语', 'ro': '罗马尼亚语', 'bg': '保加利亚语', 'el': '希腊语',
    'hr': '克罗地亚语', 'sl': '斯洛文尼亚语', 'et': '爱沙尼亚语', 'lv': '拉脱维亚语',
    'lt': '立陶宛语', 'uk': '乌克兰语', 'ru': '俄语', 'tr': '土耳其语', 'sr': '塞尔维亚语',
    'bs': '波斯尼亚语', 'sq': '阿尔巴尼亚语', 'mk': '马其顿语', 'mt': '马耳他语',
    'ga': '爱尔兰语', 'cy': '威尔士语', 'ca': '加泰罗尼亚语', 'zh': '中文',
}
TARGET_LANGUAGES = tuple(code for code in LANGUAGE_NAMES if code != 'zh')
SUPPORTED_LANGUAGES = tuple(LANGUAGE_NAMES)


def active_languages(selected):
    targets = [code for code in dict.fromkeys(selected or []) if code in TARGET_LANGUAGES]
    return tuple([*(targets or ['de']), 'zh'])


def label(code):
    return f'{code} — {LANGUAGE_NAMES.get(code, code)}'
