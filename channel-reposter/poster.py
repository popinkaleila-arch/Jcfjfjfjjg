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
from logs import caption_event, caption_note, caption_report, dump_report, flush_logs
from bridge import UserbotBridge
from database import Database, Window
from formatting import compose_dest_caption, media_fingerprint, normalize_caption_html
from transfer_filters import album_is_clean, should_skip

CAPTION_LIMIT = 1024
BACKFILL_MAX = 800
SOURCE_INDEX_LIMIT = 3000
BACKFILL_MISS_STREAK = 80
BACKFILL_DELAY = 1.2
UPWARD_PROBE = 12

log = logging.getLogger("poster")


@dataclass
class BackfillResult:
    scanned: int = 0
    updated: int = 0
    same: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: bool = False
    waiting: int = 0
    current_id: int = 0
    top_hit: int = 0
    errors: list[str] = field(default_factory=list)


class Poster:
    def __init__(self, db: Database, bridge: UserbotBridge, editor: BotEditor | None = None) -> None:
        self.db = db
        self.bridge = bridge
        self.editor = editor
        self.backfill_running = False
        self.stop_backfill = asyncio.Event()
        self.backfill_task: asyncio.Task | None = None
        self.backfill_delay = BACKFILL_DELAY

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

    def caption_edit_active(self) -> bool:
        return self.backfill_running or (self.backfill_task is not None and not self.backfill_task.done())

    def request_stop_backfill(self) -> bool:
        if not self.caption_edit_active():
            return False
        self.stop_backfill.set()
        return True

    async def backfill_via_bot(
        self,
        window: Window,
        start_id: int,
        html: str,
        limit: int = 0,
        progress=None,
    ) -> BackfillResult:
        if self.backfill_running:
            raise RuntimeError("правка существующих постов уже идёт")
        if not self.editor:
            raise RuntimeError("редактор бота не подключён")
        if not window.dest:
            raise RuntimeError("сначала укажите назначение или пришлите ссылку на пост")
        caption = normalize_caption_html((html or "").strip())
        if not caption:
            raise RuntimeError("пришлите новое описание в бота — юзербот для этого не нужен")
        if len(caption) > CAPTION_LIMIT:
            raise RuntimeError(f"подпись длиннее {CAPTION_LIMIT} символов")
        limit = max(0, int(limit))
        start = max(1, int(start_id))
        self.stop_backfill.clear()
        self.backfill_running = True
        result = BackfillResult()
        seen: set[int] = set()
        previous_wait = getattr(self.editor, "on_wait", None)

        async def on_wait(seconds: int) -> None:
            result.waiting = int(seconds)
            if progress:
                await progress(result)

        self.editor.on_wait = on_wait
        try:
            rights = await self.editor.check_rights(window.dest)
            if not rights.ok:
                caption_note(f"нет прав {window.dest}: {rights.detail}")
                flush_logs()
                raise RuntimeError(rights.detail)
            if start <= 1 and hasattr(self.db, "list_dest_message_ids"):
                try:
                    known = await self.db.list_dest_message_ids(window.dest)
                    if known:
                        start = max(int(item) for item in known)
                except Exception:
                    pass
            start = max(1, start)
            if hasattr(self.db, "list_dest_captions"):
                try:
                    for message_id, old in await self.db.list_dest_captions(window.dest):
                        if normalize_caption_html(old or "").strip() == caption:
                            seen.add(int(message_id))
                except Exception:
                    log.warning("не смог прочитать журнал подписей")
            result.same = len(seen)
            log.info(
                "caption backfill %s from %s, уже с этим текстом %s, дальше все медиа до начала канала",
                window.dest,
                start,
                result.same,
            )
            caption_note(
                f"старт {window.dest} с id {start}, уже с этим текстом {result.same}, "
                f"длина подписи {len(caption)}"
            )
            if progress:
                await progress(result)
            if not result.stopped and (limit == 0 or result.updated < limit):
                await self._scan_ids(
                    window,
                    caption,
                    _iter_ids(start, 1),
                    result,
                    limit,
                    progress,
                    seen,
                    use_miss=True,
                    miss_needs_hit=False,
                    streak=UPWARD_PROBE,
                    track_top=True,
                )
            ceiling = result.top_hit or start
            if not result.stopped and (limit == 0 or result.updated < limit) and ceiling > 1:
                await self._scan_ids(
                    window,
                    caption,
                    _iter_ids(ceiling - 1, -1),
                    result,
                    limit,
                    progress,
                    seen,
                    use_miss=False,
                )
            result.waiting = 0
            await self._store_caption_report(window.dest, start, caption, result)
            return result
        finally:
            self.editor.on_wait = previous_wait
            self.backfill_running = False

    async def _scan_ids(
        self,
        window,
        caption,
        ids,
        result: BackfillResult,
        limit: int,
        progress,
        seen: set[int],
        use_miss: bool,
        miss_needs_hit: bool = True,
        streak: int | None = None,
        track_top: bool = False,
    ) -> None:
        miss = 0
        hits = 0
        miss_limit = streak if streak is not None else BACKFILL_MISS_STREAK
        for message_id in ids:
            if self.stop_backfill.is_set():
                result.stopped = True
                return
            if limit and result.updated >= limit:
                return
            if message_id in seen:
                continue
            try:
                kind = await asyncio.wait_for(
                    self._edit_one_existing(window, message_id, caption),
                    timeout=25,
                )
            except asyncio.TimeoutError:
                kind = "таймаут Telegram"
                caption_event(str(window.dest), message_id, "fail", "таймаут Telegram")
                log.warning("правка %s зависла, пропускаю", message_id)
            seen.add(message_id)
            result.scanned += 1
            result.current_id = message_id
            result.waiting = 0
            if kind != "miss" and track_top:
                result.top_hit = message_id
            if kind == "ok":
                result.updated += 1
                miss = 0
                hits += 1
            elif kind == "same":
                result.same += 1
                miss = 0
                hits += 1
            elif kind == "miss":
                result.skipped += 1
                miss += 1
            elif kind == "skip":
                result.skipped += 1
                miss = 0
                hits += 1
            else:
                result.failed += 1
                miss = 0
                hits += 1
                if len(result.errors) < 8:
                    result.errors.append(f"{message_id}: {kind}")
            if use_miss and miss_limit and miss >= miss_limit and (hits or not miss_needs_hit):
                return
            if progress and (kind == "ok" or result.scanned % 15 == 0):
                await progress(result)
            if result.scanned and result.scanned % 100 == 0:
                caption_note(
                    f"ход {window.dest}/{message_id} поставлено={result.updated} "
                    f"уже={result.same} пустых={result.skipped} ошибок={result.failed} "
                    f"просмотрено={result.scanned}"
                )
            delay = 0
            if kind == "ok":
                delay = self.backfill_delay
            elif kind == "miss":
                delay = 0.05 if self.backfill_delay else 0
            elif kind in {"skip", "same"}:
                delay = min(0.25, self.backfill_delay) if self.backfill_delay else 0
            if delay:
                await asyncio.sleep(delay)

    async def _edit_one_existing(self, window: Window, message_id: int, html: str) -> str:
        assert self.editor and window.dest
        try:
            status = await self.editor.edit_caption(window.dest, message_id, html, media_only=True)
            if "тот же" in (status or "").lower():
                caption_event(window.dest, message_id, "same", "текст уже стоит")
                return "same"
            await self._remember(window, message_id, None, html)
            caption_event(window.dest, message_id, "ok", "подпись поставлена")
            log.info("описание поста %s/%s обновлено", window.dest, message_id)
            return "ok"
        except Exception as exc:
            text = str(exc).lower()
            if "не найден" in text or "not found" in text:
                caption_event(window.dest, message_id, "miss", "поста нет")
                return "miss"
            if "тот же" in text or "not modified" in text:
                caption_event(window.dest, message_id, "same", "текст уже стоит")
                return "same"
            if (
                "нет поля подписи" in text
                or "no caption" in text
                or "не даёт править" in text
                or "can't be edited" in text
            ):
                caption_event(window.dest, message_id, "skip", "нет поля подписи")
                return "skip"
            if "прав" in text or "administrator" in text or "admin" in text:
                caption_event(window.dest, message_id, "fail", text)
                raise
            caption_event(window.dest, message_id, "fail", text)
            return str(exc)

    async def _store_caption_report(self, dest: str, start_id: int, caption: str, result: BackfillResult) -> None:
        payload = {
            "dest": dest,
            "start_id": start_id,
            "last_id": result.current_id,
            "updated": result.updated,
            "same": result.same,
            "skipped": result.skipped,
            "failed": result.failed,
            "scanned": result.scanned,
            "stopped": result.stopped,
            "errors": result.errors[:5],
            "caption_len": len(caption),
        }
        caption_note(caption_report(payload).replace("\n", " | "))
        flush_logs()
        if hasattr(self.db, "set_setting"):
            try:
                await self.db.set_setting("caption_last", dump_report(payload))
            except Exception:
                log.warning("не сохранил итог правки описаний")

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


def _iter_ids(start: int, step: int, limit: int = 100000):
    current = start
    scanned = 0
    while current >= 1 and scanned < limit:
        yield current
        current += step
        scanned += 1
