"""Several live checks against the destination channel. Not collected by pytest.

Run on the server as the service user:

    cd /opt/channel-reposter/app
    .venv/bin/python tests/live_caption_suite.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from bot_editor import BotEditor
from formatting import normalize_caption_html
from logs import caption_event, caption_note, flush_logs, setup_logging

CHAT_FALLBACK = "@sw3atsh0p"
ADMIN_FALLBACK = 7835556726
READ_PAUSE = 0.35
EDIT_PAUSE = 1.6


def _load_window() -> tuple[str, str, list[int]]:
    db_path = config.DATABASE_PATH
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT dest, caption FROM windows ORDER BY id LIMIT 1"
        ).fetchone()
        ids = [int(item[0]) for item in conn.execute("SELECT dest_msg_id FROM posts ORDER BY dest_msg_id")]
    finally:
        conn.close()
    dest = (row[0] if row else "") or CHAT_FALLBACK
    caption = row[1] if row else ""
    return dest, caption or "", ids


def _sample(ids: list[int], newest: list[int]) -> list[int]:
    picks: list[int] = []
    if ids:
        last = len(ids) - 1
        for fraction in (0, 0.02, 0.08, 0.2, 0.35, 0.5, 0.65, 0.8, 0.92, 0.98, 1):
            picks.append(ids[min(last, int(fraction * last))])
        picks.extend(ids[:3])
        picks.extend(ids[-8:])
    picks.extend(newest)
    for fixed in (3, 10, 50, 100, 500, 1000, 2000, 3000, 4000, 5000, 6000):
        picks.append(fixed)
    return sorted({int(item) for item in picks if int(item) > 0})


def _media_kind(message) -> str:
    for name in ("photo", "video", "animation", "document", "audio", "voice", "video_note"):
        if getattr(message, name, None):
            return name
    if getattr(message, "text", None):
        return "text"
    return "other"


async def _call(action, *args, **kwargs):
    for _ in range(6):
        try:
            return await action(*args, **kwargs)
        except TelegramRetryAfter as exc:
            wait = int(getattr(exc, "retry_after", 1) or 1)
            caption_note(f"Telegram просит подождать {wait}с")
            flush_logs()
            await asyncio.sleep(wait + 1)
    return await action(*args, **kwargs)


async def _read(bot: Bot, chat: str, admin: int, message_id: int):
    copied = await _call(bot.forward_message, chat_id=admin, from_chat_id=chat, message_id=message_id)
    try:
        await _call(bot.delete_message, admin, copied.message_id)
    except Exception:
        pass
    return copied


async def _probe_newest(bot: Bot, chat: str, admin: int, start: int) -> tuple[int, list[int]]:
    top = start
    found: list[int] = []
    misses = 0
    cursor = max(1, start)
    while misses < 12 and cursor < start + 40:
        try:
            await _read(bot, chat, admin, cursor)
            top = cursor
            found.append(cursor)
            misses = 0
        except TelegramBadRequest:
            misses += 1
        cursor += 1
        await asyncio.sleep(READ_PAUSE)
    return top, found


async def main() -> None:
    log_path = setup_logging(config.DATABASE_PATH.parent / "logs")
    dest, raw_caption, known = _load_window()
    promo = normalize_caption_html(raw_caption)
    admin = config.ADMIN_IDS[0] if config.ADMIN_IDS else ADMIN_FALLBACK
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    if not promo:
        raise SystemExit("в окне нет описания")

    bot = Bot(config.BOT_TOKEN)
    editor = BotEditor(bot)
    failed: list[str] = []
    empty_media: list[int] = []
    above: list[int] = []
    checked = 0
    with_promo = 0
    caption_note(f"live-тест {dest}, известных id {len(known)}, лог {log_path}")
    try:
        rights = await editor.check_rights(dest)
        print("rights", rights.ok, rights.detail)
        caption_note(f"права: {rights.detail}")
        if not rights.ok:
            raise SystemExit("no rights")

        floor = min(known) if known else 1
        ceil_guess = max(known) if known else 1
        top, fresh = await _probe_newest(bot, dest, admin, ceil_guess)
        print("range", floor, top, "fresh", fresh)
        caption_note(f"потолок канала {top}, новые id {fresh or 'нет'}")

        sample = _sample(known, fresh)
        print("sample", len(sample))
        for message_id in sample:
            try:
                message = await _read(bot, dest, admin, message_id)
            except TelegramBadRequest as exc:
                print("miss", message_id, exc.message)
                caption_event(dest, message_id, "miss", "live: поста нет")
                await asyncio.sleep(READ_PAUSE)
                continue
            kind = _media_kind(message)
            text = message.caption or message.text or ""
            above_flag = bool(getattr(message, "show_caption_above_media", False))
            has_promo = "VPN" in text or promo[:24] in text
            checked += 1
            if has_promo:
                with_promo += 1
            if above_flag:
                above.append(message_id)
            if kind not in {"text", "other"} and not text.strip():
                empty_media.append(message_id)
            print(
                f"id={message_id} {kind} caption={len(text)} above={above_flag} promo={has_promo}"
            )
            caption_event(
                dest,
                message_id,
                "ok" if text.strip() else "skip",
                f"live {kind} len={len(text)} above={int(above_flag)} promo={int(has_promo)}",
            )
            await asyncio.sleep(READ_PAUSE)

        targets = [item for item in (top, 3000, 100, 10) if item]
        restored: list[int] = []
        for message_id in targets:
            marker = f"CAPTEST-{message_id}-{int(time.time()) % 100000}"
            try:
                status = await editor.edit_caption(dest, message_id, marker)
                print("marker", message_id, status)
                caption_event(dest, message_id, "ok", f"live marker {marker}")
                await asyncio.sleep(EDIT_PAUSE)
                message = await _read(bot, dest, admin, message_id)
                got = message.caption or message.text or ""
                above_flag = bool(getattr(message, "show_caption_above_media", False))
                print(" reread", message_id, repr(got[:60]), "above", above_flag)
                if marker not in got:
                    failed.append(f"{message_id} marker missing: {got!r}")
                if above_flag:
                    failed.append(f"{message_id} caption above media")
            except Exception as exc:
                text = str(exc).lower()
                if "нет поля подписи" in text or "no caption" in text or "не найден" in text:
                    print("skip target", message_id, exc)
                    caption_event(dest, message_id, "skip", f"live target {exc}")
                    continue
                failed.append(f"{message_id} edit failed: {exc}")
                caption_event(dest, message_id, "fail", f"live {exc}")
            finally:
                try:
                    status = await editor.edit_caption(dest, message_id, promo)
                    print(" restore", message_id, status)
                    await asyncio.sleep(EDIT_PAUSE)
                    message = await _read(bot, dest, admin, message_id)
                    got = message.caption or message.text or ""
                    if "VPN" not in got and promo[:24] not in got:
                        failed.append(f"{message_id} promo not restored: {got[:80]!r}")
                    else:
                        restored.append(message_id)
                    caption_event(dest, message_id, "ok", "live promo restored")
                except Exception as exc:
                    text = str(exc).lower()
                    if "нет поля подписи" in text or "no caption" in text or "не найден" in text:
                        print("restore skip", message_id, exc)
                    else:
                        failed.append(f"{message_id} restore failed: {exc}")
                        caption_event(dest, message_id, "fail", f"live restore {exc}")

        if empty_media:
            print("empty media", empty_media)
            caption_note(f"пустые медиа в выборке: {empty_media[:20]}")
            for message_id in empty_media[:8]:
                try:
                    status = await editor.edit_caption(dest, message_id, promo, media_only=True)
                    print(" fill", message_id, status)
                    caption_event(dest, message_id, "ok", "live заполнил пустое")
                    await asyncio.sleep(EDIT_PAUSE)
                    message = await _read(bot, dest, admin, message_id)
                    got = message.caption or ""
                    if "VPN" not in got:
                        failed.append(f"{message_id} still empty after fill: {got!r}")
                except Exception as exc:
                    text = str(exc).lower()
                    if "нет поля подписи" in text or "no caption" in text:
                        print(" album tail", message_id)
                        caption_event(dest, message_id, "skip", "live альбом без поля")
                    else:
                        failed.append(f"{message_id} fill failed: {exc}")
                        caption_event(dest, message_id, "fail", f"live fill {exc}")
        if above:
            failed.append(f"caption above media: {above}")

        summary = (
            f"live итог проверено={checked} с промо={with_promo} "
            f"пустых={len(empty_media)} над фото={len(above)} "
            f"восстановлено={restored} ошибок={len(failed)} потолок={top}"
        )
        print(summary)
        caption_note(summary)
        flush_logs()
    finally:
        await bot.session.close()

    if failed:
        print("FAILED")
        for item in failed:
            print(" -", item)
        caption_note("live FAILED: " + " | ".join(failed[:6]))
        flush_logs()
        raise SystemExit(1)
    print("LIVE_OK")


if __name__ == "__main__":
    asyncio.run(main())
