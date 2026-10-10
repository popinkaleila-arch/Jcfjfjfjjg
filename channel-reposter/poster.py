from __future__ import annotations

import asyncio
import logging
import random
import time
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageNotModified, RPCError

import config
from bridge import UserbotBridge
from database import Database, Window
from transfer_filters import album_is_clean, should_skip

CAPTION_LIMIT = 1024

log = logging.getLogger("poster")


class Poster:
    def __init__(self, db: Database, bridge: UserbotBridge) -> None:
        self.db = db
        self.bridge = bridge

    async def run_window(self, window: Window) -> int:
        if self.bridge.stop_cycle.is_set():
            return 0
        if not self.bridge.logged_in() or self.bridge.client is None:
            raise RuntimeError("юзербот не вошёл")
        client = self.bridge.client
        self.bridge.current_pair = f"{window.source} → {window.dest}"
        collected = await self._collect(window)
        posted = 0
        seen_groups: set[str] = set()
        deadline = time.time() + config.WINDOW_TIMEOUT_SECONDS
        for message in collected:
            if self.bridge.stop_cycle.is_set() or time.time() > deadline:
                break
            if posted >= window.posts_per_cycle:
                break
            group = getattr(message, "media_group_id", None)
            if group:
                if group in seen_groups:
                    await self.db.update_window(window.id, last_msg_id=max(window.last_msg_id, message.id))
                    window.last_msg_id = max(window.last_msg_id, message.id)
                    continue
                album = [item for item in collected if getattr(item, "media_group_id", None) == group]
                seen_groups.add(group)
                if not album_is_clean(album, window.clean_filter):
                    last_id = max(item.id for item in album)
                    await self.db.update_window(window.id, last_msg_id=last_id)
                    window.last_msg_id = last_id
                    continue
                ok = await self._copy_album(window, album)
            else:
                if should_skip(message, window.clean_filter):
                    await self.db.update_window(window.id, last_msg_id=message.id)
                    window.last_msg_id = message.id
                    continue
                ok = await self._copy_one(window, message)
            if ok:
                posted += 1
                last_id = message.id if not group else max(item.id for item in album)
                await self.db.update_window(window.id, last_msg_id=last_id)
                window.last_msg_id = last_id
                await self.db.bump_posted(window.id, 1)
                delay = random.uniform(config.POST_DELAY_MIN, config.POST_DELAY_MAX)
                await asyncio.sleep(delay)
        return posted

    async def _remember(
        self,
        window: Window,
        dest_msg_id: int | None,
        source_msg_id: int | None,
        caption: str,
        is_album: bool = False,
    ) -> None:
        if not dest_msg_id or not window.dest:
            return
        await self.db.record_post(
            window_id=window.id,
            dest=window.dest,
            dest_msg_id=int(dest_msg_id),
            source=window.source,
            source_msg_id=source_msg_id,
            is_album=is_album,
            caption=caption or "",
        )

    async def _collect(self, window: Window) -> list:
        client = self.bridge.client
        assert client and window.source
        batch: list = []
        async for message in client.get_chat_history(window.source, limit=200):
            if message.id <= window.last_msg_id:
                break
            batch.append(message)
        batch.sort(key=lambda item: item.id)
        return batch

    async def estimate_queue(self, window: Window) -> int:
        if not self.bridge.logged_in() or not window.source:
            return window.queued
        try:
            batch = await self._collect(window)
            return len(batch)
        except Exception:
            log.exception("queue estimate")
            return window.queued

    async def rewind_to_start(self, window: Window) -> int:
        if not self.bridge.logged_in() or self.bridge.client is None or not window.source:
            raise RuntimeError("сначала войдите в юзербота")
        oldest_id = None
        scanned = 0
        async for message in self.bridge.client.get_chat_history(window.source):
            oldest_id = message.id
            scanned += 1
            if scanned >= 20000:
                break
        if oldest_id is None:
            await self.db.update_window(window.id, last_msg_id=0, queued=0)
            return 0
        await self.db.update_window(window.id, last_msg_id=oldest_id - 1, queued=scanned)
        return scanned

    async def _copy_one(self, window: Window, message) -> bool:
        assert window.dest
        kwargs = {}
        if window.clean_filter:
            kwargs["caption"] = ""
        elif window.caption:
            kwargs["caption"] = window.caption
        try:
            copied = await message.copy(window.dest, **kwargs)
        except FloodWait as exc:
            wait = int(exc.value)
            log.warning("FloodWait %s сек на copy", wait)
            if wait > 30:
                return False
            await asyncio.sleep(wait + 1)
            copied = await message.copy(window.dest, **kwargs)
        except RPCError:
            log.exception("copy failed id=%s", message.id)
            return False
        dest_id = getattr(copied, "id", None) if copied is not None else None
        await self._remember(window, dest_id, message.id, kwargs.get("caption") or "", is_album=False)
        return True

    async def _copy_album(self, window: Window, album: list) -> bool:
        assert window.dest and self.bridge.client
        album = sorted(album, key=lambda item: item.id)
        captions = [""] * len(album) if window.clean_filter else None
        try:
            copied = await self.bridge.client.copy_media_group(
                chat_id=window.dest,
                from_chat_id=window.source,
                message_id=album[0].id,
                captions=captions,
            )
        except FloodWait as exc:
            wait = int(exc.value)
            log.warning("FloodWait %s сек на album", wait)
            if wait > 30:
                return False
            await asyncio.sleep(wait + 1)
            copied = await self.bridge.client.copy_media_group(
                chat_id=window.dest,
                from_chat_id=window.source,
                message_id=album[0].id,
                captions=captions,
            )
        except RPCError:
            log.exception("album copy failed")
            return False
        first = copied[0] if copied else None
        dest_id = getattr(first, "id", None)
        caption = "" if window.clean_filter else (window.caption or "")
        await self._remember(window, dest_id, album[0].id, caption, is_album=True)
        return True

    async def edit_caption(self, chat: str, message_id: int, caption: str) -> str:
        if not self.bridge.logged_in() or self.bridge.client is None:
            raise RuntimeError("сначала войдите в юзербота")
        text = caption or ""
        if len(text) > CAPTION_LIMIT:
            raise RuntimeError(f"подпись длиннее {CAPTION_LIMIT} символов")
        client = self.bridge.client
        parse = ParseMode.HTML if text else ParseMode.DISABLED

        async def _call(fn, *args: object, **kwargs: object):
            try:
                return await fn(*args, **kwargs)
            except FloodWait as exc:
                wait = int(exc.value)
                if wait > 30:
                    raise RuntimeError(f"Telegram просит подождать {wait} сек") from exc
                await asyncio.sleep(wait + 1)
                return await fn(*args, **kwargs)

        try:
            await _call(
                client.edit_message_caption,
                chat,
                message_id,
                text,
                parse_mode=parse,
            )
        except MessageNotModified:
            return "текст тот же — Telegram ничего не менял"
        except MessageIdInvalid as exc:
            raise RuntimeError("пост не найден или его уже нельзя править") from exc
        except RPCError as caption_error:
            try:
                if not text:
                    raise caption_error
                await _call(
                    client.edit_message_text,
                    chat,
                    message_id,
                    text,
                    parse_mode=ParseMode.HTML,
                )
            except MessageNotModified:
                return "текст тот же — Telegram ничего не менял"
            except RPCError as text_error:
                raise RuntimeError(f"не удалось править пост: {text_error}") from text_error
        await self.db.update_post_caption(chat, message_id, text)
        return "описание обновлено"
