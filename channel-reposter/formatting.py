from __future__ import annotations

from aiogram.types import Message


def message_to_html(message: Message) -> str:
    if message.html_text:
        return message.html_text
    return message.text or message.caption or ""


def rich_text_html(value: object | None) -> str:
    if value is None:
        return ""
    html = getattr(value, "html", None)
    if isinstance(html, str) and html:
        return html
    markdown = getattr(value, "markdown", None)
    if isinstance(markdown, str) and markdown and not isinstance(value, str):
        return markdown
    return str(value) if value else ""


def pyrogram_to_html(message: object) -> str:
    caption = rich_text_html(getattr(message, "caption", None))
    if caption:
        return caption
    return rich_text_html(getattr(message, "text", None))


def apply_caption_template(template: str, original: str) -> str:
    text = (template or "").strip()
    if not text:
        return original or ""
    if "{text}" in text:
        return text.replace("{text}", original or "")
    if original:
        return f"{text}\n\n{original}"
    return text


def compose_dest_caption(template: str, source_message: object | None) -> str:
    original = pyrogram_to_html(source_message) if source_message is not None else ""
    return apply_caption_template(template, original)


def _unique_id(media: object | None) -> str | None:
    if media is None:
        return None
    if isinstance(media, (list, tuple)) and media:
        media = media[-1]
    uid = getattr(media, "file_unique_id", None)
    return str(uid) if uid else None


def media_fingerprint(message: object) -> str | None:
    photo = _unique_id(getattr(message, "photo", None))
    if photo:
        return f"p:{photo}"
    video = _unique_id(getattr(message, "video", None))
    if video:
        return f"v:{video}"
    animation = _unique_id(getattr(message, "animation", None))
    if animation:
        return f"a:{animation}"
    document = _unique_id(getattr(message, "document", None))
    if document:
        return f"d:{document}"
    return None
