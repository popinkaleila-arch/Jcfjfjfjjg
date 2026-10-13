from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser

from aiogram.types import Message

_KEEP_TAGS = {
    "a": "a",
    "b": "b",
    "strong": "b",
    "code": "code",
    "pre": "pre",
}


class _TelegramHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = _KEEP_TAGS.get(tag.lower())
        if not tag:
            return
        if tag in self.stack:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href:
                return
            self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
            self.stack.append("a")
            return
        self.parts.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = _KEEP_TAGS.get(tag.lower())
        if not tag or tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        data = unicodedata.normalize("NFKC", data or "")
        data = data.replace("\u200a", " ").replace("\u200b", "").replace("\xa0", " ")
        data = re.sub(r"[ \t]+", " ", data)
        if data:
            self.parts.append(html.escape(data, quote=False))


def normalize_caption_html(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    parser = _TelegramHtml()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return html.escape(re.sub(r"<[^>]+>", "", raw), quote=False).strip()
    while parser.stack:
        parser.parts.append(f"</{parser.stack.pop()}>")
    out = "".join(parser.parts)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


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
