from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner

log = logging.getLogger("bot_editor")

CAPTION_LIMIT = 1024
ADMIN_HINT = (
    "добавьте @{username} админом канала-назначения с правом "
    "«Изменение сообщений» (Edit messages of others)"
)


@dataclass
class BotRights:
    ok: bool
    detail: str


class BotEditor:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._username: str | None = None
        self.on_wait = None

    async def username(self) -> str:
        if not self._username:
            me = await self.bot.get_me()
            self._username = me.username or "bot"
        return self._username

    async def hint(self) -> str:
        return ADMIN_HINT.format(username=await self.username())

    async def check_rights(self, chat: str) -> BotRights:
        try:
            me = await self.bot.get_me()
            member = await self.bot.get_chat_member(chat, me.id)
        except TelegramForbiddenError:
            return BotRights(False, f"бот не состоит в {chat}. {await self.hint()}")
        except TelegramBadRequest as exc:
            return BotRights(False, f"не вижу чат {chat}: {exc.message}. {await self.hint()}")
        if isinstance(member, ChatMemberOwner):
            return BotRights(True, "бот — владелец чата")
        if isinstance(member, ChatMemberAdministrator):
            if member.can_edit_messages:
                return BotRights(True, "есть право менять сообщения")
            return BotRights(
                False,
                "бот админ, но без права «Изменение сообщений». " + await self.hint(),
            )
        return BotRights(False, f"бот в чате, но не админ. {await self.hint()}")

    async def edit_caption(self, chat: str, message_id: int, caption: str) -> str:
        text = caption or ""
        if len(text) > CAPTION_LIMIT:
            raise RuntimeError(f"подпись длиннее {CAPTION_LIMIT} символов")
        parse = ParseMode.HTML if text else None
        try:
            return await self._edit_caption_once(chat, message_id, text, parse)
        except TelegramBadRequest as exc:
            message = (exc.message or "").lower()
            if "can't parse entities" in message or "unsupported start tag" in message:
                return await self._edit_caption_once(chat, message_id, _strip_html(text), None)
            raise

    async def _edit_caption_once(self, chat: str, message_id: int, text: str, parse: ParseMode | None) -> str:
        try:
            await self._with_retry(
                self.bot.edit_message_caption,
                chat_id=chat,
                message_id=message_id,
                caption=text or None,
                parse_mode=parse,
            )
            return "описание обновлено ботом"
        except TelegramBadRequest as exc:
            message = (exc.message or "").lower()
            if "message is not modified" in message:
                return "текст тот же — Telegram ничего не менял"
            if "there is no caption" in message or "message can't be edited" in message:
                return await self._edit_as_text(chat, message_id, text, parse, exc)
            raise RuntimeError(_humanize(exc.message, await self.hint())) from exc
        except TelegramForbiddenError as exc:
            raise RuntimeError(_humanize(str(exc), await self.hint())) from exc

    async def _edit_as_text(
        self,
        chat: str,
        message_id: int,
        text: str,
        parse: ParseMode | None,
        cause: Exception,
    ) -> str:
        if not text:
            raise RuntimeError("у этого поста нечего снимать") from cause
        try:
            await self._with_retry(
                self.bot.edit_message_text,
                text=text,
                chat_id=chat,
                message_id=message_id,
                parse_mode=parse or ParseMode.HTML,
            )
            return "текст обновлён ботом"
        except TelegramBadRequest as text_exc:
            message = (text_exc.message or "").lower()
            if "message is not modified" in message:
                return "текст тот же — Telegram ничего не менял"
            if "can't parse entities" in message or "unsupported start tag" in message:
                await self._with_retry(
                    self.bot.edit_message_text,
                    text=_strip_html(text),
                    chat_id=chat,
                    message_id=message_id,
                    parse_mode=None,
                )
                return "текст обновлён ботом"
            if "there is no text" in message or "message can't be edited" in message or "can't be edited" in message:
                raise RuntimeError("у этого куска альбома нет поля подписи") from text_exc
            raise RuntimeError(_humanize(text_exc.message, await self.hint())) from text_exc

    async def _with_retry(self, fn, **kwargs):
        last: Exception | None = None
        for _attempt in range(8):
            try:
                return await fn(**kwargs)
            except TelegramRetryAfter as exc:
                last = exc
                wait = _retry_after(exc) or 1
            except TelegramBadRequest as exc:
                wait = _retry_after(exc)
                if wait is None:
                    raise
                last = exc
            log.warning("RetryAfter %s сек на правке подписи", wait)
            if self.on_wait:
                try:
                    await self.on_wait(wait)
                except Exception:
                    pass
            await asyncio.sleep(wait + 1)
        raise last or RuntimeError("Telegram просит подождать")


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def caption_key(text: str) -> str:
    plain = html.unescape(_strip_html(text or ""))
    return re.sub(r"\s+", " ", plain).strip()


def _retry_after(exc: Exception) -> int | None:
    wait = getattr(exc, "retry_after", None)
    if wait:
        try:
            return max(1, int(wait))
        except (TypeError, ValueError):
            pass
    message = str(getattr(exc, "message", None) or exc)
    match = re.search(r"retry after (\d+)", message, re.I)
    if match:
        return max(1, int(match.group(1)))
    return None


def _humanize(raw: str | None, hint: str) -> str:
    text = (raw or "ошибка Telegram").strip()
    low = text.lower()
    if "not enough rights" in low or "need administrator" in low or "chat_admin_required" in low:
        return f"боту не хватает прав. {hint}"
    if "message to edit not found" in low:
        return "пост не найден"
    if "message can't be edited" in low:
        return "этот пост Telegram уже не даёт править"
    return f"{text}. {hint}"
