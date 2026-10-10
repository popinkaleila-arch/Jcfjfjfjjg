from __future__ import annotations

from typing import Any


def is_photo_or_video(message: Any) -> bool:
    if getattr(message, "video", None):
        return True
    if getattr(message, "photo", None):
        return True
    return False


def should_skip(message: Any, clean_filter: bool) -> bool:
    if getattr(message, "service", False):
        return True
    if getattr(message, "empty", False):
        return True
    if not clean_filter:
        return False
    if getattr(message, "media_group_id", None) and is_photo_or_video(message):
        return False
    if is_photo_or_video(message):
        return False
    return True


def album_is_clean(messages: list[Any], clean_filter: bool) -> bool:
    if not clean_filter:
        return True
    return all(is_photo_or_video(item) or getattr(item, "service", False) for item in messages)
