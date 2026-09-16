"""Preserve all reference roles even for single-image-compatible gateways."""
from pathlib import Path
import hashlib
import json
import math
from PIL import Image, ImageDraw, ImageOps
from .image_requests import uses_image_edits


def reference_inputs(product_paths, style_paths, client, folder, identifiers=None):
    products = list(dict.fromkeys(product_paths))
    styles = list(dict.fromkeys(style_paths))
    sources = [('PRODUCT IDENTITY', p) for p in products] + [('STYLE ONLY', p) for p in styles]
    if not sources:
        return [], {'strategy': 'text-only', 'sources': []}
    manifest = []
    for role, path in sources:
        source = Path(path)
        if not source.is_file():
            raise ValueError(f'参考图不存在：{source}')
        manifest.append({'role': role, 'name': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    if identifiers is not None:
        if len(identifiers) != len(manifest) or len(set(identifiers)) != len(identifiers):
            raise ValueError('图片编号必须与实际输入数量一一对应且不可重复。')
        for index, (item, identifier) in enumerate(zip(manifest, identifiers), 1):
            item.update(reference_id=identifier, input_index=index)
    options = getattr(client, 'options', None)
    provider = getattr(options, 'image_provider', getattr(client, 'image_provider', ''))
    if options and uses_image_edits(options, options.image_model, sources) and len(sources) <= 16 and not getattr(client, 'image_fallbacks', []):
        return [p for _, p in sources], {'strategy': 'multipart image[] (all references in listed order)', 'sources': manifest}
    # A contact sheet works with the established single-image adapters and their
    # fallback providers. Never silently retain just sources[0].
    if len(sources) == 1:
        return [sources[0][1]], {'strategy': 'single-reference', 'sources': manifest}
    key = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:20]
    path = Path(folder) / f'references-{key}.jpg'
    if not path.is_file():
        columns = min(4, math.ceil(math.sqrt(len(sources))))
        rows = math.ceil(len(sources)/columns)
        cell = min(1000, max(240, 8000//max(columns, rows)))
        canvas = Image.new('RGB', (columns*cell, rows*cell), '#E8EDF2')
        draw = ImageDraw.Draw(canvas)
        for index, (role, source) in enumerate(sources):
            x, y = index % columns*cell, index//columns*cell
            draw.text((x+12, y+8), f'{manifest[index].get("reference_id", index+1)}: {role}', fill='black')
            with Image.open(source) as original:
                tile = ImageOps.exif_transpose(original).convert('RGB')
                tile.thumbnail((cell-20, cell-45), Image.Resampling.LANCZOS)
                canvas.paste(tile, (x+(cell-tile.width)//2, y+35+(cell-45-tile.height)//2))
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(path, 'JPEG', quality=94)
    return [str(path)], {'strategy': 'labeled-contact-sheet (all references; single-image gateway compatible)',
                         'sources': manifest, 'transport_image': str(path),
                         'note': 'Cells are identity/style references, not the requested output layout. Never reproduce sheet labels.'}
