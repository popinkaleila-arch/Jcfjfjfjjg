from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageNotModified, RPCError

import config
from bot_editor import BotEditor
from bridge import UserbotBridge
from database import Database, Window
from formatting import compose_dest_caption, media_fingerprint
from transfer_filters import album_is_clean, should_skip

CAPTION_LIMIT = 1024
BACKFILL_MAX = 500
SOURCE_INDEX_LIMIT = 3000

log = logging.getLogger("poster")


@dataclass
class BackfillResult:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class Poster:
    def __init__(self, db: Database, bridge: UserbotBridge, editor: BotEditor | None = None) -> None:
        self.db = db
        self.bridge = bridge
        self.editor = editor
        self.backfill_running = False

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
        final_caption = await self._apply_bot_caption(window, dest_id, message)
        await self._remember(window, dest_id, message.id, final_caption or kwargs.get("caption") or "", is_album=False)
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
        final_caption = await self._apply_bot_caption(window, dest_id, album[0])
        await self._remember(window, dest_id, album[0].id, final_caption or caption, is_album=True)
        return True

    async def _apply_bot_caption(self, window: Window, dest_id: int | None, source) -> str:
        html = compose_dest_caption(window.caption, source)
        if not window.bot_edit or not dest_id or not window.dest or not html:
            return html
        if not self.editor:
            log.warning("bot editor не подключён — подпись не проставлена")
            return html
        try:
            await self.editor.edit_caption(window.dest, int(dest_id), html)
            return html
        except Exception as exc:
            log.warning("бот не смог проставить подпись: %s", exc)
            return html

    async def _source_index(self, source: str) -> dict[str, object]:
        index: dict[str, object] = {}
        if not self.bridge.logged_in() or self.bridge.client is None or not source:
            return index
        scanned = 0
        async for message in self.bridge.client.get_chat_history(source):
            key = media_fingerprint(message)
            if key and key not in index:
                index[key] = message
            scanned += 1
            if scanned >= SOURCE_INDEX_LIMIT:
                break
        return index

    async def backfill_existing(
        self,
        window: Window,
        limit: int = 100,
        min_id: int = 0,
        progress=None,
    ) -> BackfillResult:
        if self.backfill_running:
            raise RuntimeError("правка существующих постов уже идёт")
        if not self.editor:
            raise RuntimeError("редактор бота не подключён")
        if not window.dest:
            raise RuntimeError("сначала укажите назначение")
        limit = max(1, min(int(limit), BACKFILL_MAX))
        self.backfill_running = True
        result = BackfillResult()
        try:
            rights = await self.editor.check_rights(window.dest)
            if not rights.ok:
                raise RuntimeError(rights.detail)
            if not self.bridge.logged_in() or self.bridge.client is None:
                raise RuntimeError("чтобы пройти уже залитые посты, войдите в юзербота")
            source_map = await self._source_index(window.source) if window.source else {}
            seen_groups: set[str] = set()
            async for message in self.bridge.client.get_chat_history(window.dest):
                if message.id < min_id:
                    break
                result.scanned += 1
                group = getattr(message, "media_group_id", None)
                if group:
                    if group in seen_groups:
                        result.skipped += 1
                        if result.scanned >= limit:
                            break
                        continue
                    seen_groups.add(group)
                source = source_map.get(media_fingerprint(message) or "")
                html = compose_dest_caption(window.caption, source)
                if not html:
                    result.skipped += 1
                else:
                    try:
                        await self.editor.edit_caption(window.dest, message.id, html)
                        await self._remember(window, message.id, getattr(source, "id", None), html)
                        result.updated += 1
                    except Exception as exc:
                        result.failed += 1
                        if len(result.errors) < 8:
                            result.errors.append(f"{message.id}: {exc}")
                        log.warning("backfill %s: %s", message.id, exc)
                    await asyncio.sleep(0.35)
                if progress and result.scanned % 40 == 0:
                    await progress(result)
                if result.scanned >= limit:
                    break
            return result
        finally:
            self.backfill_running = False

    async def backfill_id_range(
        self,
        window: Window,
        start_id: int,
        count: int,
        progress=None,
    ) -> BackfillResult:
        if self.backfill_running:
            raise RuntimeError("правка существующих постов уже идёт")
        if not self.editor:
            raise RuntimeError("редактор бота не подключён")
        if not window.dest:
            raise RuntimeError("сначала укажите назначение")
        html = compose_dest_caption(window.caption, None)
        if not html:
            raise RuntimeError("задайте ✏️ Описание — без юзербота бот может проставить только шаблон")
        count = max(1, min(int(count), BACKFILL_MAX))
        rights = await self.editor.check_rights(window.dest)
        if not rights.ok:
            raise RuntimeError(rights.detail)
        self.backfill_running = True
        result = BackfillResult()
        try:
            for message_id in range(int(start_id), int(start_id) + count):
                result.scanned += 1
                try:
                    await self.editor.edit_caption(window.dest, message_id, html)
                    await self._remember(window, message_id, None, html)
                    result.updated += 1
                except Exception as exc:
                    text = str(exc).lower()
                    if "не найден" in text or "not found" in text:
                        result.skipped += 1
                    else:
                        result.failed += 1
                        if len(result.errors) < 8:
                            result.errors.append(f"{message_id}: {exc}")
                await asyncio.sleep(0.35)
                if progress and result.scanned % 40 == 0:
                    await progress(result)
            return result
        finally:
            self.backfill_running = False

    async def edit_caption(self, chat: str, message_id: int, caption: str, prefer_bot: bool = True) -> str:
        text = caption or ""
        if len(text) > CAPTION_LIMIT:
            raise RuntimeError(f"подпись длиннее {CAPTION_LIMIT} символов")
        errors: list[str] = []
        if prefer_bot and self.editor:
            try:
                result = await self.editor.edit_caption(chat, message_id, text)
                await self.db.update_post_caption(chat, message_id, text)
                return result
            except Exception as exc:
                errors.append(str(exc))
                log.warning("правка ботом не вышла, пробую юзербота: %s", exc)
        if not self.bridge.logged_in() or self.bridge.client is None:
            raise RuntimeError(errors[0] if errors else "сначала войдите в юзербота или выдайте боту админку")
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
            raise RuntimeError(errors[0] if errors else "пост не найден или его уже нельзя править") from exc
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
                detail = errors[0] if errors else str(text_error)
                raise RuntimeError(f"не удалось править пост: {detail}") from text_error
        await self.db.update_post_caption(chat, message_id, text)
        return "описание обновлено юзерботом"
