"""Model-aware rules for the OpenAI-compatible Images transport."""


GPT_IMAGE_PROMPT_LIMIT = 32000


def is_gpt_image(model):
    name = str(model).lower().rsplit('/', 1)[-1]
    return name.startswith('gpt-image-') or name.startswith('chatgpt-image-')


def uses_image_edits(options, model, has_references):
    return bool(has_references and options.image_protocol in {'openai_images', 'openai_images_url'}
                and (is_gpt_image(model) or options.image_provider == 'openai'))


def validate_image_prompt(prompt, model):
    if not str(prompt).strip():
        raise ValueError('图片 Prompt 不能为空，尚未发送请求。')
    if is_gpt_image(model) and len(prompt) > GPT_IMAGE_PROMPT_LIMIT:
        raise ValueError(f'图片 Prompt 共 {len(prompt):,} 字符，超过 {model} 的 32,000 字符限制。'
                         '请缩短本帧 Prompt / 产品资料；已保留原文，未截断确认文案，尚未发送请求。')
