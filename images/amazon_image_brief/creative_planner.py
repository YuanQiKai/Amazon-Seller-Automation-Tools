from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any

from .catalogs import CATALOG_BY_CODE
from .models import ProductProject
from .typography import PRESETS, element_layout, palette, poster_palette


LAYOUTS = {
    'left': {'label': '左文右产品', 'text_box': [0.05, 0.10, 0.38, 0.80], 'product_box': [0.49, 0.08, 0.46, 0.84]},
    'right': {'label': '左产品右文', 'text_box': [0.57, 0.10, 0.38, 0.80], 'product_box': [0.05, 0.08, 0.46, 0.84]},
    'bottom': {'label': '上方场景 / 底部文案', 'text_box': [0.05, 0.64, 0.90, 0.31], 'product_box': [0.08, 0.05, 0.84, 0.53]},
    'none': {'label': '纯白产品图 / 无文案', 'text_box': [], 'product_box': [0.075, 0.075, 0.85, 0.85]},
}
LAYOUTS.update({key: {'label': value[0], 'product_box': value[1], 'text_box': []} for key, value in PRESETS.items()})


def apply_layout(plan, key):
    plan['layout'] = key
    if key in PRESETS:
        for device in ('pc', 'mobile'):
            plan[device] = element_layout(key, plan.get('copy_slots', []), device == 'mobile')
    else:
        plan['pc'] = deepcopy(LAYOUTS.get(key, LAYOUTS['none']))
        plan['mobile'] = deepcopy(LAYOUTS['none' if key == 'none' else 'bottom'])
    return plan


def split_facts(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip(' -•\t') for item in re.split(r'[\n；;]+', value) if item.strip(' -•\t')))


def _role(code: str) -> tuple[str, tuple[str, ...], str]:
    if code == 'MAIN_WHITE':
        return '外观识别', (), '只展示实物完整轮廓和准确颜色'
    if 'COLOR_SIZE' in code or 'TECH' in code or 'COMPARE' in code or 'COMPARISON' in code:
        return '规格选择', ('尺寸', '颜色', '重量', '容量', 'size', 'capacity', 'weight'), '并排产品与尺寸线；可验证规格逐项对照'
    if 'CARE' in code or 'QA' in code:
        return '使用答疑', ('维护', '保修', '清洁', '使用', 'care', 'clean'), '用步骤编号或问题与答案解释使用与维护'
    if 'DETAIL' in code or 'EXPLODED' in code or 'HOTSPOT' in code:
        return '细节证据', ('材质', '轮', '拉杆', '结构', '接口', '纹理', 'material', 'wheel'), '真实局部放大、结构标注；禁止虚构看不见的零件'
    if 'SCENE' in code or 'AUDIENCE' in code or 'LIFESTYLE' in code:
        return '场景收益', ('旅行', '便携', '轻', '人群', '场景', 'travel', 'light'), '人在真实场景中使用产品，突出一个有证据的收益'
    if 'PACKAGE' in code:
        return '交付内容', ('包装', '清单', 'package'), '平铺展示包装中实际包含的物品'
    if 'PROOF' in code:
        return '事实依据', ('认证', '测试', '证据', 'test'), '展示输入中已有证据；资料缺失时留出待确认区'
    if code.startswith('BS_') or 'HERO' in code:
        return '品牌价值', ('设计', '品牌', '定位', 'design'), '品牌与产品主视觉，禁止编造品牌历史与奖项'
    return '功能与购买理由', ('功能', '卖点', '收纳', '便捷', 'function'), '一个主卖点配细节与使用收益，建立清晰阅读顺序'


def plan_modules(project: ProductProject) -> dict[str, dict[str, Any]]:
    facts = split_facts(re.sub(r'[,，](?!\d)', '\n', project.selling_points)) + split_facts(project.functions)
    facts.extend(f'{key}：{value}' for key, value in project.custom_fields.items() if str(value).strip())
    facts.extend(f'{key}：{value}' for key, value in [('材质', project.material), ('维护', project.maintenance), ('包装清单', project.package_contents), ('保修', project.warranty), ('认证依据', project.certifications)] if value.strip())
    facts.extend(
        f'{v.name or v.sku}：' + ' / '.join(f'{label} {getattr(v, key)}' for key, label in [('size', '尺寸'), ('color', '颜色'), ('weight', '重量'), ('capacity', '容量')] if getattr(v, key).strip())
        for v in project.variants if any((v.size, v.color, v.weight, v.capacity))
    )
    facts = list(dict.fromkeys(facts))
    counts: Counter[str] = Counter()
    repeats: Counter[str] = Counter()
    result = {}
    for index, instance in enumerate(project.normalized_module_instances()):
        spec = CATALOG_BY_CODE.get(instance.module_code)
        if not spec:
            continue
        role, cues, shot = _role(spec.code)
        ranked = sorted(enumerate(facts), key=lambda pair: (-sum(cue.lower() in pair[1].lower() for cue in cues) * 3 + counts[pair[1]] * 2, pair[0]))
        evidence = [value for _, value in ranked[:3]]
        focus = evidence[0] if evidence else f'{spec.purpose}（缺少产品依据，待补充）'
        if spec.code == 'MAIN_WHITE':
            focus, evidence = '真实产品外观与颜色；首图不加文字', []
        else:
            counts[focus] += 1
        repeats[spec.code] += 1
        angle = ('展示事实', '实际使用收益', '细节证据', '选择与使用建议')[(repeats[spec.code] - 1) % 4]
        layout_id = ('none' if spec.code == 'MAIN_WHITE' else 'steps' if role == '使用答疑' else
                     'cards' if 'FOUR' in spec.code or 'DUAL' in spec.code else 'specs' if role == '规格选择' else
                     'editorial' if role == '场景收益' else 'hotspots' if role == '细节证据' else 'hero')
        if spec.code == 'MAIN_WHITE':
            slots = []
        elif 'QA' in spec.code:
            slots = ['标题', '问题1', '答案1', '问题2', '答案2']
        elif 'FOUR' in spec.code:
            slots = ['标题', '导语', '卖点卡1', '卖点卡2', '卖点卡3', '卖点卡4']
        elif role == '规格选择':
            slots = ['标题', '选择说明', '规格要点1', '规格要点2', '规格要点3']
        else:
            slots = ['标题', '支持说明', '证据/细节1', '证据/细节2']
        plan = {
            'focus': focus,
            'evidence': evidence,
            'angle': f'{role} / {angle} / 同类模块第{repeats[spec.code]}次',
            'visual': shot,
            'layout': layout_id,
            'pc': deepcopy(LAYOUTS[layout_id]),
            'mobile': deepcopy(LAYOUTS['none' if layout_id == 'none' else 'bottom']),
            'copy_slots': slots,
            'pc_size': spec.pc_size,
            'mobile_size': spec.mobile_size,
            'custom_direction': project.module_recipes.get(instance.instance_id, {}).get('copy_prompt', instance.custom_prompt or project.module_prompts.get(instance.instance_id, '')),
            'palette': palette(project.brand_colors),
            'font_suggestion': project.font_suggestion,
        }
        apply_layout(plan, layout_id)
        # A manually approved plan is preserved when opening/saving a project.
        saved = project.creative_plans.get(instance.instance_id, {})
        if isinstance(saved, dict) and saved.get('manual'):
            plan.update({key: deepcopy(saved[key]) for key in ('focus', 'angle', 'evidence', 'visual', 'layout', 'copy_slots', 'manual', 'manual_fields', 'ai_layout', 'layout_rationale', 'planning_prompt', 'planning_response') if key in saved})
            apply_layout(plan, plan['layout'])
            for device in ('pc', 'mobile'):
                if saved.get(device, {}).get('elements'):
                    plan[device] = deepcopy(saved[device])
        if spec.code == 'MAIN_WHITE':
            plan.update(focus='真实产品外观与颜色；首图不加文字', layout='none', pc=deepcopy(LAYOUTS['none']), mobile=deepcopy(LAYOUTS['none']), copy_slots=[])
        result[instance.instance_id] = plan
    if project.options.aplus_continuous:
        instances = [item for item in project.normalized_module_instances()
                     if item.instance_id in result and CATALOG_BY_CODE[item.module_code].channel == '高级A+']
        story = [{'id': item.instance_id, 'module': CATALOG_BY_CODE[item.module_code].name,
                  'focus': result[item.instance_id]['focus']} for item in instances]
        for index, instance in enumerate(instances):
            plan = result[instance.instance_id]
            if plan['layout'] not in PRESETS and plan['layout'] != 'ai':
                apply_layout(plan, 'hero')
            if plan['layout'] == 'hero' and not plan.get('manual'):
                apply_layout(plan, ('hero', 'editorial', 'specs')[index % 3])
            plan['palette'] = poster_palette(project.brand_colors, project.options.aplus_theme)
            plan['poster'] = {'enabled': True, 'chapter': index+1, 'total': len(instances),
                              'story': story, 'direction': project.options.aplus_direction,
                              'continuity': f'{project.options.aplus_theme}统一底色、品牌色贯穿线、字体与间距；章节按选择顺序衔接，各章推进不同卖点。',
                              'previous_focus': story[index-1]['focus'] if index else '',
                              'next_focus': story[index+1]['focus'] if index+1 < len(story) else '',
                              'anchor_mapping': '产品框内引线锚点随实际素材等比缩放到图像区域，避免指向竖版产品两侧留白；仍须人工核对具体部位。',
                              'delivery': 'PC 1464×600 / 移动1200×900章节设计参考；视频为封面、轮播为关键帧，交互/文本模块需在后台单独配置。'}
    return result


def plan_description(plan: dict[str, Any]) -> str:
    description = (
        f"【主卖点】{plan.get('focus', '')}\n【本图角度】{plan.get('angle', '')}\n"
        f"【事实依据】{'；'.join(plan.get('evidence', [])) or '待补充'}\n"
        f"【视觉证明】{plan.get('visual', '')}\n"
        f"【PC版式】{plan.get('pc', {}).get('label', '')}；文案区[x,y,w,h]={plan.get('pc', {}).get('text_box', [])}\n"
        f"【移动版式】{plan.get('mobile', {}).get('label', '')}；单独重新排版，保持文字可读\n"
        f"【文案层级】{' → '.join(plan.get('copy_slots', [])) or '无文案'}；坐标为画布比例，德语预留20%扩展空间。"
    )
    for device in ('pc', 'mobile'):
        for scene in plan.get(device, {}).get('scenes', []):
            description += f"\n【{device.upper()} 图片场景】{scene.get('label', '')}；区域={scene['box']}"
        for item in plan.get(device, {}).get('elements', []):
            description += (f"\n【{device.upper()} {item['label']}】框[x,y,w,h]={item['box']}；"
                            f"对齐={item['align']}；字号≈画布短边×{item['font_ratio']:.3f}；"
                            f"对应部位锚点={item.get('anchor') or '无'}（需按实物确认）")
    if plan.get('poster'):
        poster = plan['poster']
        description += f"\n【A+连贯海报】第{poster['chapter']}/{poster['total']}章；{poster['continuity']}\n【整体修改方向】{poster['direction']}\n【交付说明】{poster['delivery']}"
    return description


def layout_prompt(plan: dict[str, Any]) -> str:
    instruction = ('\nCONTINUOUS A+ CHAPTER VISUAL: This is one chapter of the supplied complete storyboard. '
                   'Generate ONLY its visual asset, filling your image canvas with the complete product/scene; '
                   'product_box and text elements describe the FINAL poster placement, NOT a box to shrink this source asset into. '
                   'Match the shared palette, lighting and camera language of the complete storyboard. '
                   'A local compositor places this whole visual into product_box without cropping and adds all text later. '
                   'Do not bake in frames, text, labels or sidebars. Show only facts assigned to this chapter. '
                   if plan.get('poster') else
                   '\nKeep the full product inside product_box. Reserve each elements[].box as a low-detail text-safe area; '
                   'position the corresponding feature near its anchor where physically accurate. '
                   'These are separate headline, subtitle and feature-callout placements, not one generic side panel. ')
    payload = {key: value for key, value in plan.items() if key not in ('planning_prompt', 'planning_response')}
    return 'APPROVED VISUAL PLAN (normalized x,y,width,height): ' + json.dumps(payload, ensure_ascii=False) + instruction + 'Render no lettering. Use ONLY supplied evidence; do not invent product features.'


def local_copy(plan: dict[str, Any], language: str) -> str:
    if not plan.get('copy_slots'):
        return ''
    focus = plan.get('focus', '')
    evidence = plan.get('evidence', [])
    if language == 'zh':
        values = [focus, plan.get('angle', '')] + (evidence[1:] or ['待补充该卖点的实物细节与使用依据'])
        return '\n'.join(values)
    # Offline copy is explicitly a draft, never an invented translation of product facts.
    notices = {
        'en': 'Translation pending — product facts require AI or human translation.',
        'de': 'Übersetzung ausstehend — Produktangaben bitte übersetzen und prüfen.',
        'fr': 'Traduction en attente — traduire et vérifier les caractéristiques.',
        'it': 'Traduzione in attesa — tradurre e verificare i dati del prodotto.',
        'es': 'Traducción pendiente — traducir y verificar los datos del producto.',
    }
    return notices.get(language, f'[{language}] Translation pending — AI or human translation required.') + '\n' + focus
