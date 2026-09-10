"""A coordinated A+ artboard, responsive panels and versioned designer assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from .creative_planner import plan_modules
from .typography import poster_panel
from .creative_ai import recipe_for


def sync_plans(project, briefs):
    """Refresh the whole storyboard without replacing individually approved copy."""
    plans = plan_modules(project)
    for brief in briefs:
        plan = plans.get(brief.instance_id)
        if plan:
            brief.creative_plan = plan


def build_poster(project, briefs, output_dir: Path):
    if not project.options.aplus_continuous:
        return {}
    ordered = {item.instance_id: index for index, item in enumerate(project.normalized_module_instances())}
    chapters = sorted([b for b in briefs if b.channel == '高级A+'], key=lambda b: ordered.get(b.instance_id, 9999))
    if not chapters:
        return {}
    # Individual final images already stream to the review page. Do not create
    # draft chapters / pending placeholders merely to fill an unfinished poster.
    completed = sum(bool(b.ai_effect_image and Path(b.ai_effect_image).is_file()
                         and not b.image_generation_error and '占位' not in b.image_generation_status) for b in chapters)
    if completed < len(chapters):
        return {'mode': '最终连贯A+海报', 'version': '', 'pc': '', 'mobile': '', 'files': [],
                'chapters': [], 'completed': completed, 'total': len(chapters),
                'warnings': [f'A+结果图已完成 {completed}/{len(chapters)}；全部成功后再合成最终海报，不生成占位章节。']}
    # JPEG has a 65500px dimension ceiling; cap masters only, keep full-size individual panels.
    fingerprint = hashlib.sha256(json.dumps([
        {'schema': 'v3.0.2-final', 'id': b.instance_id, 'source': b.ai_effect_image,
         'source_mtime': Path(b.ai_effect_image).stat().st_mtime_ns if b.ai_effect_image and Path(b.ai_effect_image).is_file() else 0,
         'error': b.image_generation_error,
         'copy': b.copy_for(b.image_language) if project.options.generate_german_composites else '', 'plan': b.creative_plan,
         'render_text': recipe_for(project, b.instance_id)['render_text']}
        for b in chapters], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    folder = output_dir / 'aplus_poster' / fingerprint
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / 'manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if all(Path(path).is_file() for path in manifest.get('files', [])):
            return manifest
    manifest = {'mode': '连续章节设计参考（不是单张后台上传文件）', 'version': fingerprint,
                'direction': project.options.aplus_direction, 'chapters': [], 'files': [], 'warnings': [],
                'pc': '', 'mobile': '', 'completed': 0, 'total': len(chapters)}
    for index, brief in enumerate(chapters):
        plan = brief.creative_plan
        source_path = Path(brief.ai_effect_image) if brief.ai_effect_image else None
        ready = bool(source_path and source_path.is_file() and not brief.image_generation_error)
        entry = {'id': brief.instance_id, 'module': brief.module_name, 'order': index+1,
                 'status': '就绪' if ready else '未完成', 'focus': plan.get('focus', ''),
                 'pc': '', 'mobile': '', 'note': '章节视觉参考。视频需另制作视频；轮播需补齐帧；多图、热点、问答、比较表需后台单独配置。'}
        if ready:
            manifest['completed'] += 1
        for device, size in [('pc', (1464, 600)), ('mobile', (1200, 900))]:
            native = recipe_for(project, brief.instance_id)['render_text'] == 'native'
            text = brief.copy_for(brief.image_language) if project.options.generate_german_composites and not native else ''
            try:
                if ready:
                    with Image.open(source_path) as source:
                        if native:
                            # Do not stamp a second copy on AI-rendered lettering.
                            from PIL import ImageOps
                            panel = ImageOps.pad(source.convert('RGB'), size, color=plan.get('palette', {}).get('paper', '#F5F3ED'))
                            entry['note'] = 'AI已绘字：原图等比适配，移动端未重排文字；需人工检查拼写和可读性。'
                        else:
                            panel = poster_panel(source, plan, size, device, text)
            except (ValueError, OSError) as exc:
                # Do not mislabel untypeset visuals as approved final artwork.
                entry['status'] = '排版未通过 / 待修改'
                manifest['warnings'].append(f'{brief.sequence} {device}: {exc}')
                continue
            path = folder / f'{index+1:02d}-{device}-final.jpg'
            panel.save(path, 'JPEG', quality=94, optimize=True)
            entry[device] = str(path)
            entry[f'{device}_size'] = f'{size[0]}×{size[1]}'
            manifest['files'].append(str(path))
        manifest['chapters'].append(entry)
    for device, width, height in [('pc', 1464, 600), ('mobile', 1200, 900)]:
        if not all(entry[device] for entry in manifest['chapters']):
            continue
        scale = min(1.0, 60000 / (height * len(chapters)), (60_000_000 / (width*height*len(chapters)))**.5)
        panel_w, panel_h = max(1, round(width*scale)), max(1, int(height*scale))
        canvas = Image.new('RGB', (panel_w, panel_h*len(chapters)), 'white')
        for index, entry in enumerate(manifest['chapters']):
            with Image.open(entry[device]) as panel:
                canvas.paste(panel.resize((panel_w, panel_h), Image.Resampling.LANCZOS), (0, index*panel_h))
        path = folder / f'aplus-{device}-full.jpg'
        canvas.save(path, 'JPEG', quality=94, optimize=True)
        manifest[device] = str(path)
        manifest['files'].append(str(path))
        manifest[f'{device}_size'] = f'{canvas.width}×{canvas.height}'
    manifest['files'].append(str(manifest_path))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
