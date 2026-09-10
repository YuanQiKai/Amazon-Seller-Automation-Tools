from __future__ import annotations

import shutil
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def copy_reference_image(source: str, destination: Path) -> str:
    path = Path(source)
    if not path.is_file():
        return ""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        image.save(destination, "JPEG", quality=90, optimize=True)
    return str(destination)


def create_german_composite(
    source_image: Path,
    destination: Path,
    german_copy: str,
    skip_text: bool = False,
    creative_plan: dict[str, Any] | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if skip_text or not german_copy.strip():
        shutil.copy2(source_image, destination)
        return destination

    with Image.open(source_image) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if (creative_plan or {}).get('pc', {}).get('elements'):
            from .typography import typeset, poster_panel
            if creative_plan.get('poster'):
                composed = poster_panel(image, creative_plan, (1464, 600), text=german_copy)
            else:
                composed = typeset(image, german_copy, creative_plan)
            composed.save(destination, 'JPEG', quality=94, optimize=True)
            return destination
        width, height = image.size
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        box = (creative_plan or {}).get('pc', {}).get('text_box') or [0.05, 0.10, 0.38, 0.80]
        bx, by, bw, bh = box
        x0, y0, x1, y1 = int(bx*width), int(by*height), int((bx+bw)*width), int((by+bh)*height)
        padding = max(6, int(min(x1-x0, y1-y0)*0.07))
        text_width, text_height = x1-x0-padding*2, y1-y0-padding*2
        lines = [item.strip() for item in german_copy.splitlines() if item.strip()]

        def wrap_pixels(text, font):
            output, current = [], ''
            # Character wrapping also handles long German words without clipping.
            for char in text:
                if current and draw.textlength(current + char, font=font) > text_width:
                    if ' ' in current:
                        prefix, tail = current.rsplit(' ', 1)
                        output.append(prefix)
                        current = tail + char
                    else:
                        output.append(current)
                        current = char
                else:
                    current += char
            if current:
                output.append(current)
            return output

        rendered = []
        for size in range(max(16, min(width//30, height//18)), 7, -1):
            rendered = []
            for index, line in enumerate(lines):
                font = _font(int(size*1.35) if index == 0 else size, bold=index == 0)
                wrapped = wrap_pixels(line, font)
                line_height = max(10, int(getattr(font, 'size', size)*1.35))
                rendered.append((wrapped, font, line_height, max(4, size//3)))
            if sum(len(wrapped)*line_height+gap for wrapped, _, line_height, gap in rendered) <= text_height:
                break
        else:
            raise ValueError('德语文案超出版式预留区域，请缩短文案或调整版式后重试。')
        draw.rounded_rectangle((x0, y0, x1, y1), radius=max(6, width//70), fill=(20, 27, 32, 218))
        y = y0 + padding
        for index, (wrapped, font, line_height, gap) in enumerate(rendered):
            for line in wrapped:
                draw.text((x0+padding, y), line, font=font, fill=(255, 255, 255, 255) if index == 0 else (238, 238, 232, 255))
                y += line_height
            y += gap
        composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        composed.save(destination, "JPEG", quality=90, optimize=True)
    return destination


def create_placeholder(destination: Path, title: str, prompt: str, ratio: str = "square") -> Path:
    size = {"square": (1024, 1024), "landscape": (1536, 1024), "portrait": (1024, 1536)}.get(
        ratio, (1024, 1024)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, "#EDE9E1")
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rounded_rectangle(
        (int(width * 0.08), int(height * 0.10), int(width * 0.92), int(height * 0.90)),
        radius=32,
        fill="#F9F7F2",
        outline="#C9C2B6",
        width=3,
    )
    draw.text((int(width * 0.13), int(height * 0.20)), title, font=_font(width // 24, True), fill="#1E2A33")
    y = int(height * 0.33)
    for line in textwrap.wrap(prompt, width=70 if ratio == "landscape" else 46)[:12]:
        draw.text((int(width * 0.13), y), line, font=_font(max(18, width // 55)), fill="#56616A")
        y += max(28, width // 40)
    draw.text(
        (int(width * 0.13), int(height * 0.82)),
        "AI IMAGE PENDING / 参考构图占位",
        font=_font(max(20, width // 44), True),
        fill="#D88A2A",
    )
    image.save(destination, "JPEG", quality=88, optimize=True)
    return destination
