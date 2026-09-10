"""Editable, normalized art-direction coordinates and individual text fitting."""
from __future__ import annotations

import math
import re
from copy import deepcopy

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps


PRESETS = {
    'hero': ('主视觉 · 居中标题与产品环绕标注', [0.27, 0.32, 0.46, 0.60]),
    'hotspots': ('细节证据 · 部件引线与特写标注', [0.28, 0.29, 0.44, 0.65]),
    'editorial': ('场景叙事 · 杂志标题与浮动说明', [0.43, 0.08, 0.53, 0.85]),
    'specs': ('参数对照 · 顶部标题与底部规格卡', [0.12, 0.28, 0.76, 0.39]),
    'cards': ('多卖点 · 双层标题与卡片矩阵', [0.10, 0.27, 0.80, 0.35]),
    'steps': ('使用答疑 · 编号步骤与配套说明', [0.61, 0.30, 0.34, 0.60]),
    'custom': ('自定义 · 文案区块与图片场景', [0.28, 0.30, 0.44, 0.50]),
}


def palette(value):
    colors = re.findall(r'#[0-9a-fA-F]{6}\b', str(value))
    return {'ink': '#172B36', 'paper': '#F5F3ED', 'accent': colors[0] if colors else '#277A79'}


def poster_palette(value, theme):
    colors = palette(value)
    if theme == '深色奢雅':
        colors.update(ink='#F8F4E8', paper='#18232B')
    elif theme == '纯白极简':
        colors.update(ink='#18232B', paper='#FFFFFF')
    elif theme == '冷灰科技':
        colors.update(ink='#153145', paper='#EDF2F6')
    return colors


def element_layout(key, slots, mobile=False):
    label, product = PRESETS.get(key, PRESETS['hero'])
    elements = []
    count = max(1, len(slots) - 2)
    if mobile:
        product = [0.18, 0.29, 0.64, 0.39]
    for index, slot in enumerate(slots):
        anchor = None
        align = 'center' if index < 2 else 'left'
        size = 0.066 if index == 0 else 0.037 if index == 1 else 0.033
        box = [0.08, 0.05, 0.84, 0.12] if index == 0 else [0.14, 0.185, 0.72, 0.09]
        if index >= 2:
            n = index - 2
            if mobile:
                cols = 2 if count > 1 else 1
                rows = math.ceil(count / cols)
                box = [0.05 + n % cols * 0.47, 0.73 + n // cols * 0.23 / rows, 0.43 if cols == 2 else 0.90, 0.21 / rows]
                anchor = [0.35 + (n % 2) * 0.30, 0.52 + (n // 2) * 0.06] if key == 'hotspots' else None
            elif key == 'hero':
                rows = math.ceil(count / 2)
                box = [0.035 if n % 2 == 0 else 0.76, 0.39 + (n // 2) * 0.5 / rows, 0.205, min(0.23, 0.44 / rows)]
                align = 'right' if n % 2 == 0 else 'left'
            elif key == 'hotspots':
                box = [0.66, 0.31 + n * 0.62 / count, 0.29, 0.54 / count]
                anchor = [0.27 + (n % 2) * 0.18, 0.39 + n * 0.4 / count]
                product = [0.08, 0.30, 0.49, 0.62]
            elif key == 'specs':
                cell = 0.90 / count
                box = [0.05 + n * cell, 0.72, cell - 0.025, 0.23]
                align = 'center'
            elif key == 'cards':
                rows = math.ceil(count / 2)
                box = [0.44 + n % 2 * 0.28, 0.34 + n // 2 * 0.59 / rows, 0.24, 0.50 / rows]
                product = [0.04, 0.32, 0.34, 0.60]
            elif key == 'custom':
                box = [0.07, 0.78 + n * 0.19 / count, 0.86, 0.17 / count]
            elif key == 'editorial':
                box = [0.05, 0.52 + n * 0.4 / count, 0.32, 0.34 / count]
            else:
                # Question/answer rows are allowed different typographic emphasis.
                box = [0.06, 0.33 + n * 0.60 / count, 0.49, 0.53 / count]
        if key == 'editorial' and not mobile and index < 2:
            box = [0.05, 0.09 if index == 0 else 0.32, 0.33, 0.21 if index == 0 else 0.15]
            align = 'left'
        elements.append({'slot': index, 'label': slot, 'box': box, 'align': align,
                         'font_ratio': size, 'bold': index == 0 or '问题' in slot,
                         'anchor': anchor, 'background': 'auto'})
    if mobile and key == 'cards':
        product = [0.06, 0.29, 0.88, 0.25]
        for item in elements[2:]:
            n = item['slot'] - 2
            rows = math.ceil(count / 2)
            item['box'] = [0.06 + n % 2 * 0.47, 0.60 + n // 2 * 0.35 / rows, 0.41, 0.30 / rows]
    if mobile and key == 'specs':
        product = [0.14, 0.29, 0.72, 0.29]
        for item in elements[2:]:
            item['box'] = [0.07, 0.63 + (item['slot']-2) * 0.32 / count, 0.86, 0.28 / count]
    if mobile and key == 'editorial':
        product = [0.05, 0.36, 0.90, 0.37]
        for item in elements[:2]:
            item['align'] = 'left'
            item['box'] = [0.06, 0.04 if item['slot'] == 0 else 0.22, 0.88, 0.15 if item['slot'] == 0 else 0.10]
        for item in elements[2:]:
            item['box'] = [0.06, 0.77 + (item['slot']-2) * 0.21 / count, 0.88, 0.18 / count]
    if mobile and key == 'steps':
        product = [0.62, 0.33, 0.32, 0.57]
        for item in elements[2:]:
            item['box'] = [0.06, 0.33 + (item['slot']-2) * 0.61 / count, 0.49, 0.53 / count]
    if mobile and key == 'custom':
        product = [0.12, 0.32, 0.76, 0.30]
        for item in elements[2:]:
            item['box'] = [0.06, 0.69 + (item['slot']-2) * 0.28 / count, 0.88, 0.24 / count]
    return {'label': label + (' · 移动端重排' if mobile else ''), 'product_box': list(product), 'text_box': [], 'elements': elements, 'scenes': []}


def validate_element(element):
    box = element.get('box', [])
    if len(box) != 4 or any(not isinstance(n, (int, float)) or not math.isfinite(n) for n in box):
        raise ValueError('文本框坐标需要四个有限数值。')
    x, y, w, h = box
    if min(x, y) < 0 or min(w, h) < 0.015 or x + w > 1.001 or y + h > 1.001:
        raise ValueError('文本框必须在画布内，宽高至少为画布的1.5%。')
    if element.get('align') not in {'left', 'center', 'right'}:
        raise ValueError('对齐方式必须为 left / center / right。')
    ratio = element.get('font_ratio', 0.033)
    if not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or not 0.015 <= ratio <= 0.15:
        raise ValueError('字号比例需要在1.5%–15%之间。')
    anchor = element.get('anchor')
    if anchor is not None and (len(anchor) != 2 or any(not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= 1 for n in anchor)):
        raise ValueError('部位锚点坐标需要在0–100%之间。')


def _font(size, bold, suggestion=''):
    from pathlib import Path
    serif = any(word in suggestion.lower() for word in ('serif', 'georgia', '衬线')) and 'sans' not in suggestion.lower() and '无衬线' not in suggestion
    candidates = [f'C:/Windows/Fonts/{"georgiab" if bold else "georgia"}.ttf'] if serif else []
    candidates += [f'C:/Windows/Fonts/{"arialbd" if bold else "arial"}.ttf', f'C:/Windows/Fonts/{"msyhbd" if bold else "msyh"}.ttc']
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow 10.0 fallback on systems without the named fonts.
        return ImageFont.load_default()


def _wrap(draw, text, font, width):
    lines, current = [], ''
    for char in text:
        if current and draw.textlength(current + char, font=font) > width:
            if ' ' in current:
                part, current = current.rsplit(' ', 1)
                lines.append(part)
            else:
                lines.append(current)
                current = ''
        current += char
    if current:
        lines.append(current)
    # Recheck long words, including tails carried over from a previous wrap.
    if any(draw.textlength(line, font=font) > width + 1 for line in lines):
        lines, current = [], ''
        for char in text:
            if current and draw.textlength(current + char, font=font) > width:
                lines.append(current)
                current = ''
            current += char
        if current:
            lines.append(current)
    return lines


def typeset(image, text, plan, device='pc', validate_only=False):
    """No truncation: overflow is a recoverable error, not invisible text loss."""
    if validate_only:
        width, height = image
    else:
        image = image.convert('RGB')
        width, height = image.size
    colors = plan.get('palette') or palette('')
    elements = plan.get(device, {}).get('elements', [])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > len(elements):
        raise ValueError(f'文案有{len(lines)}行，但版式仅有{len(elements)}个文本元素；请调整版式或文案。')
    overlay = Image.new('RGBA', (1, 1) if validate_only else image.size)
    draw = ImageDraw.Draw(overlay)
    for element in elements:
        validate_element(element)
        index = element['slot']
        if index >= len(lines):
            continue
        x, y, w, h = element['box']
        x0, y0, bw, bh = int(x*width), int(y*height), int(w*width), int(h*height)
        pad = max(3, round(min(bw, bh)*0.06))
        nominal = max(14, round(min(width, height) * element.get('font_ratio', .033)))
        minimum = max(10, round(min(width, height)*.022))
        minimum = min(nominal, minimum)
        for size in range(nominal, minimum-1, -1):
            font = _font(size, element.get('bold', index == 0), plan.get('font_suggestion', ''))
            wrapped = _wrap(draw, lines[index], font, bw-pad*2)
            line_height = int(size*1.24) + 1
            if len(wrapped)*line_height <= bh-pad*2 and all(draw.textlength(line, font=font) <= bw-pad*2 for line in wrapped):
                break
        else:
            raise ValueError(f'{device}「{element.get("label", index)}」文案溢出；请缩短该行或扩大文本框，未裁掉任何文案。')
        if validate_only:
            continue
        anchor = element.get('anchor')
        if anchor:
            ax, ay = int(anchor[0]*width), int(anchor[1]*height)
            edge_x = x0+bw if ax > x0+bw/2 else x0
            edge_y = y0+bh//2
            draw.line([(edge_x, edge_y), ((edge_x+ax)//2, edge_y), (ax, ay)], fill=colors['accent'], width=max(1, width//550))
            radius = max(2, width//300)
            draw.ellipse((ax-radius, ay-radius, ax+radius, ay+radius), fill=colors['accent'])
        ink = colors['ink']
        if element.get('background', 'auto') != 'none':
            # Individual translucent labels, never one full dark copy panel.
            draw.rounded_rectangle((x0, y0, x0+bw, y0+bh), radius=max(3, width//180), fill=(*ImageColor.getrgb(colors['paper']), 225))
        cy = y0+pad
        for line in wrapped:
            length = draw.textlength(line, font=font)
            align = element.get('align', 'left')
            cx = x0+(bw-length)/2 if align == 'center' else x0+bw-pad-length if align == 'right' else x0+pad
            draw.text((cx, cy), line, font=font, fill=ink, anchor='lt')
            cy += line_height
    return None if validate_only else Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')


def poster_panel(source, plan, size, device='pc', text=''):
    """Shared paper, grid and uninterrupted side rails connect every chapter."""
    colors = plan.get('palette') or palette('')
    canvas = Image.new('RGB', size, colors['paper'])
    w, h = size
    draw = ImageDraw.Draw(canvas)
    rail = max(2, round(w*.004))
    draw.rectangle((0, 0, rail, h), fill=colors['accent'])
    x, y, bw, bh = plan[device]['product_box']
    bounds = (max(1, round(bw*w)), max(1, round(bh*h)))
    visual = ImageOps.contain(source.convert('RGB'), bounds, Image.Resampling.LANCZOS)
    # Contain, never stretch or crop away product parts. Feather only the outer edge.
    from PIL import ImageFilter
    mask = Image.new('L', visual.size, 0)
    md = ImageDraw.Draw(mask)
    inset = max(2, round(min(visual.size)*.018))
    md.rectangle((inset, inset, visual.width-inset-1, visual.height-inset-1), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(inset))
    vx, vy = round(x*w)+(bounds[0]-visual.width)//2, round(y*h)+(bounds[1]-visual.height)//2
    canvas.paste(visual, (vx, vy), mask)
    rendered_plan = deepcopy(plan)
    for element in rendered_plan[device].get('elements', []):
        anchor = element.get('anchor')
        if anchor and x <= anchor[0] <= x+bw and y <= anchor[1] <= y+bh:
            # Follow the contained source rather than pointing into the extra
            # paper margin around narrow products such as upright suitcases.
            element['anchor'] = [(vx + (anchor[0]-x)/bw*visual.width)/w,
                                 (vy + (anchor[1]-y)/bh*visual.height)/h]
    return typeset(canvas, text, rendered_plan, device) if text.strip() else canvas
