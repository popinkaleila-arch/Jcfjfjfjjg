from __future__ import annotations

import asyncio
import logging
import random
import time
from pyrogram.errors import FloodWait, RPCError

import config
from bridge import UserbotBridge
from database import Database, Window
from transfer_filters import album_is_clean, should_skip

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
            await message.copy(window.dest, **kwargs)
            return True
        except FloodWait as exc:
            wait = int(exc.value)
            log.warning("FloodWait %s сек на copy", wait)
            if wait <= 30:
                await asyncio.sleep(wait + 1)
                await message.copy(window.dest, **kwargs)
                return True
            return False
        except RPCError:
            log.exception("copy failed id=%s", message.id)
            return False

    async def _copy_album(self, window: Window, album: list) -> bool:
        assert window.dest and self.bridge.client
        album = sorted(album, key=lambda item: item.id)
        captions = [""] * len(album) if window.clean_filter else None
        try:
            await self.bridge.client.copy_media_group(
                chat_id=window.dest,
                from_chat_id=window.source,
                message_id=album[0].id,
                captions=captions,
            )
            return True
        except FloodWait as exc:
            wait = int(exc.value)
            log.warning("FloodWait %s сек на album", wait)
            if wait <= 30:
                await asyncio.sleep(wait + 1)
                await self.bridge.client.copy_media_group(
                    chat_id=window.dest,
                    from_chat_id=window.source,
                    message_id=album[0].id,
                    captions=captions,
                )
                return True
            return False
        except RPCError:
            log.exception("album copy failed")
            return False
