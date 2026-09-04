from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image, ImageOps


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((left[index] - right[index]) ** 2 for index in range(3)) ** 0.5


def extract_logo_palette(image_path: str, max_colors: int = 5) -> list[str]:
    """Extract distinct, usable colors while ignoring transparency and near-white backgrounds."""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Logo文件不存在：{image_path}")
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((500, 500), Image.Resampling.LANCZOS)
        source_pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
        pixels: list[tuple[int, int, int]] = []
        for red, green, blue, alpha in source_pixels:
            if alpha < 96:
                continue
            maximum, minimum = max(red, green, blue), min(red, green, blue)
            if minimum > 245 or maximum < 12:
                continue
            pixels.append((red, green, blue))
        if not pixels:
            source_pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
            pixels = [(red, green, blue) for red, green, blue, alpha in source_pixels if alpha >= 96]
        if not pixels:
            return []
        sample = Image.new("RGB", (len(pixels), 1))
        sample.putdata(pixels)
        quantized = sample.quantize(colors=min(12, max(3, max_colors * 2)), method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        ranked = sorted(quantized.getcolors() or [], reverse=True)

    candidates: list[tuple[int, int, int]] = []
    for _count, index in ranked:
        offset = index * 3
        color = tuple(palette[offset : offset + 3])
        if len(color) != 3:
            continue
        red, green, blue = color
        _hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if value > 0.97 and saturation < 0.08:
            continue
        if all(_distance(color, existing) >= 42 for existing in candidates):
            candidates.append(color)
        if len(candidates) >= max_colors:
            break
    if not candidates and ranked:
        _count, index = ranked[0]
        offset = index * 3
        candidates.append(tuple(palette[offset : offset + 3]))
    return [f"#{red:02X}{green:02X}{blue:02X}" for red, green, blue in candidates]


def palette_text(colors: list[str]) -> str:
    return " / ".join(colors)
