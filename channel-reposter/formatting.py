from __future__ import annotations

from aiogram.types import Message


def message_to_html(message: Message) -> str:
    if message.html_text:
        return message.html_text
    return message.text or message.caption or ""


def apply_caption_template(template: str, original: str) -> str:
    text = (template or "").strip()
    if not text:
        return original or ""
    if "{text}" in text:
        return text.replace("{text}", original or "")
    if original:
        return f"{text}\n\n{original}"
    return text
