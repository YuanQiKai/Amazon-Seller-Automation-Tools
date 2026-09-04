from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

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
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if skip_text or not german_copy.strip():
        shutil.copy2(source_image, destination)
        return destination

    with Image.open(source_image) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        panel_width = int(width * 0.46)
        draw.rounded_rectangle(
            (int(width * 0.04), int(height * 0.10), panel_width, int(height * 0.90)),
            radius=max(18, width // 55),
            fill=(20, 27, 32, 205),
        )
        lines = [item.strip() for item in german_copy.splitlines() if item.strip()]
        headline = lines[0] if lines else ""
        body = " ".join(lines[1:])
        headline_font = _font(max(28, width // 28), bold=True)
        body_font = _font(max(20, width // 44))
        x = int(width * 0.075)
        y = int(height * 0.19)
        max_chars = max(16, int(panel_width / max(14, width // 55)))
        for line in textwrap.wrap(headline, width=max_chars):
            draw.text((x, y), line, font=headline_font, fill=(255, 255, 255, 255))
            y += int(headline_font.size * 1.22) if hasattr(headline_font, "size") else 40
        y += int(height * 0.035)
        draw.rectangle((x, y, x + int(width * 0.06), y + max(4, width // 260)), fill=(216, 138, 42, 255))
        y += int(height * 0.06)
        for line in textwrap.wrap(body, width=max_chars + 7):
            draw.text((x, y), line, font=body_font, fill=(238, 238, 232, 255))
            y += int(body_font.size * 1.35) if hasattr(body_font, "size") else 30
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
