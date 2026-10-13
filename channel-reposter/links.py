from __future__ import annotations

import re
from dataclasses import dataclass

USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{4,64}$")
TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/(?P<cid>\d+)|(?P<user>[A-Za-z0-9_]+))/(?P<mid>\d+)",
    re.I,
)
TME_CHAT_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/(?P<cid>\d+)|(?P<user>[A-Za-z0-9_]+))/?$",
    re.I,
)


@dataclass(frozen=True)
class ChatRef:
    raw: str
    display: str


@dataclass(frozen=True)
class StartPoint:
    chat: str | None
    message_id: int


def is_saved_messages(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in {"me", "избранное", "избранные", "saved", "saved messages"}


def normalize_chat(text: str) -> str:
    value = (text or "").strip()
    if not value:
        raise ValueError("пустой канал")
    if is_saved_messages(value):
        return "me"

    link = TME_RE.search(value) or TME_CHAT_RE.search(value)
    if link:
        if link.group("cid"):
            return f"-100{link.group('cid')}"
        user = link.group("user")
        if user and user.lower() not in {"c", "joinchat", "addstickers", "share"}:
            return f"@{user}"

    if value.startswith("-100") and value[4:].isdigit():
        return value
    if value.startswith("-") and value[1:].isdigit():
        return value
    if value.isdigit():
        return f"-100{value}"
    if USERNAME_RE.match(value):
        return value if value.startswith("@") else f"@{value}"
    raise ValueError(f"не понял канал: {value}")


def parse_start_link(text: str) -> StartPoint:
    value = (text or "").strip()
    match = TME_RE.search(value)
    if not match:
        raise ValueError("нужна ссылка вида t.me/channel/123 или t.me/c/35839961/123")
    if match.group("cid"):
        chat = f"-100{match.group('cid')}"
    else:
        chat = f"@{match.group('user')}"
    return StartPoint(chat=chat, message_id=int(match.group("mid")))


def parse_channel_or_post(text: str) -> StartPoint:
    value = (text or "").strip()
    match = TME_RE.search(value)
    if match:
        return parse_start_link(value)
    chat = normalize_chat(value)
    return StartPoint(chat=chat, message_id=0)


def parse_duration(text: str) -> int:
    value = (text or "").strip().lower().replace(" ", "")
    if not value:
        raise ValueError("пустой интервал")
    if value.isdigit():
        seconds = int(value)
        if seconds < 5:
            raise ValueError("интервал меньше 5 секунд")
        return seconds
    match = re.fullmatch(r"(\d+)(с|s|сек|м|m|мин|ч|h|час|часа|часов|д|d|дн|день|дня|дней)", value)
    if not match:
        raise ValueError("пример: 30м, 2ч, 1д или число секунд")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"с", "s", "сек"}:
        seconds = amount
    elif unit in {"м", "m", "мин"}:
        seconds = amount * 60
    elif unit in {"ч", "h", "час", "часа", "часов"}:
        seconds = amount * 3600
    else:
        seconds = amount * 86400
    if seconds < 5:
        raise ValueError("интервал меньше 5 секунд")
    return seconds


def format_duration(seconds: int) -> str:
    if seconds % 86400 == 0 and seconds >= 86400:
        days = seconds // 86400
        return f"{days} д"
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600} ч"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60} мин"
    return f"{seconds} с"


def format_chat(chat: str | None) -> str:
    if not chat:
        return "—"
    if chat == "me":
        return "⭐ Избранное"
    return chat


def format_post_link(chat: str, message_id: int) -> str:
    if chat == "me":
        return f"Saved Messages / {message_id}"
    if chat.startswith("-100") and chat[4:].isdigit():
        return f"https://t.me/c/{chat[4:]}/{message_id}"
    if chat.startswith("@"):
        return f"https://t.me/{chat[1:]}/{message_id}"
    return f"{chat}/{message_id}"


def forwarded_point(message: object) -> StartPoint | None:
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None)
    mid = getattr(origin, "message_id", None)
    if chat is not None and mid:
        username = getattr(chat, "username", None)
        chat_id = getattr(chat, "id", None)
        if username:
            return StartPoint(f"@{username}", int(mid))
        if chat_id is not None:
            return StartPoint(str(chat_id), int(mid))
    chat = getattr(message, "forward_from_chat", None)
    mid = getattr(message, "forward_from_message_id", None)
    if chat is not None and mid:
        username = getattr(chat, "username", None)
        chat_id = getattr(chat, "id", None)
        if username:
            return StartPoint(f"@{username}", int(mid))
        if chat_id is not None:
            return StartPoint(str(chat_id), int(mid))
    return None


def preview_caption(text: str, limit: int = 60) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return "без подписи"
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
