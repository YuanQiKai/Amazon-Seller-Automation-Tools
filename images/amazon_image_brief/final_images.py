"""One deliverable per frame; never present an uploaded input as a generated result."""
from pathlib import Path


def final_image_path(brief):
    # Old releases wrote error placeholders into the same fields as real outputs.
    if '占位' in brief.image_generation_status:
        return None
    for value in (brief.german_composite_image, brief.ai_effect_image):
        if value and Path(value).is_file():
            return Path(value)
    return None
