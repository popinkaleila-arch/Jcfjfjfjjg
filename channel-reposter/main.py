from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from aiogram import Bot

import config
from admin_bot import build_dispatcher, start_polling
from bot_editor import BotEditor
from bridge import UserbotBridge
from database import Database
from poster import Poster
from scheduling import due_windows, next_run
from single_instance import SingleInstance
from userbot_auth import UserbotAuth

from logs import setup_logging

_log_dir = config.DATABASE_PATH.parent / "logs"
setup_logging(_log_dir)
log = logging.getLogger("main")
log.info("логи: %s", _log_dir)


async def scheduler_loop(db: Database, bridge: UserbotBridge, poster: Poster) -> None:
    log.info("Планировщик запущен (tick=%ss)", config.SCHEDULER_TICK_SECONDS)
    while True:
        await asyncio.sleep(config.SCHEDULER_TICK_SECONDS)
        if not bridge.logged_in():
            bridge.cycle = "idle"
            continue
        windows = await db.list_windows()
        ready = due_windows(windows)
        if not ready:
            bridge.cycle = "idle"
            continue
        bridge.stop_cycle.clear()
        bridge.cycle = "run"
        started = time.time()
        published = 0
        try:
            for window in ready:
                if bridge.stop_cycle.is_set():
                    break
                if published >= config.PASS_PUBLISH_LIMIT:
                    break
                if time.time() - started > config.PASS_TIMEOUT_SECONDS:
                    break
                try:
                    count = await poster.run_window(window)
                    published += count
                    queued = await poster.estimate_queue(await db.get_window(window.id) or window)
                    await db.update_window(
                        window.id,
                        queued=queued,
                        next_run_at=next_run(window),
                    )
                except Exception:
                    log.exception("окно #%s", window.id)
                    await db.update_window(window.id, next_run_at=next_run(window))
        finally:
            bridge.cycle = "idle"
            bridge.current_pair = "—"


async def heartbeat_loop(db: Database, bridge: UserbotBridge, bot: Bot) -> None:
    me = await bot.get_me()
    username = me.username or "bot"
    while True:
        windows = await db.list_windows()
        current = await db.current_window()
        enabled = sum(1 for win in windows if win.enabled and not win.paused)
        queued = sum(win.queued for win in windows)
        posted = sum(win.posted_total for win in windows)
        autopost = "идёт" if enabled else "пауза"
        nxt = "—"
        due = [win.next_run_at for win in windows if win.enabled and not win.paused and win.next_run_at]
        if due:
            left = max(0, int(min(due) - time.time()))
            nxt = f"{left}с"
        log.info(
            "heartbeat @%s pending=0 | окна=%s/%s автопост=%s юзербот=%s цикл=%s интервал=%s следующий=%s очередь=%s всего=%s текущее=%s → %s",
            username,
            enabled,
            len(windows),
            autopost,
            bridge.status_text(),
            bridge.cycle,
            current.interval_seconds,
            nxt,
            queued,
            posted,
            current.source or "—",
            current.dest or "—",
        )
        await asyncio.sleep(60)


async def resume_caption_backfill(db: Database, poster: Poster) -> None:
    if await db.get_setting("caption_job", "0") != "1":
        return
    win = await db.current_window()
    if not win.dest or not (win.caption or "").strip() or poster.caption_edit_active():
        return
    known = []
    if hasattr(db, "list_dest_message_ids"):
        try:
            known = await db.list_dest_message_ids(win.dest)
        except Exception:
            known = []
    start = max(known) if known else 1

    async def runner() -> None:
        log.info("resume caption backfill %s from %s", win.dest, start)
        try:
            result = await poster.backfill_via_bot(win, start, win.caption)
            log.info(
                "resume caption done updated=%s same=%s scanned=%s",
                result.updated,
                result.same,
                result.scanned,
            )
        except Exception:
            log.exception("resume caption backfill")
        finally:
            try:
                await db.set_setting("caption_job", "0")
            except Exception:
                pass

    poster.backfill_task = asyncio.create_task(runner(), name="caption-backfill")


async def amain() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    lock = SingleInstance(config.LOCK_PATH)
    lock.acquire()
    db = Database(config.DATABASE_PATH)
    await db.connect()
    bridge = UserbotBridge(db)
    auth = UserbotAuth(db, bridge)
    bot = Bot(config.BOT_TOKEN)
    editor = BotEditor(bot)
    poster = Poster(db, bridge, editor)
    dp = build_dispatcher()
    bot.workflow = {  # type: ignore[attr-defined]
        "db": db,
        "bridge": bridge,
        "auth": auth,
        "poster": poster,
        "editor": editor,
    }
    await bridge.attach()
    me = await bot.get_me()
    log.info("Admin panel @%s", me.username)
    log.info("Pyrogram worker loop started")
    log.info("Online https://t.me/%s", me.username)
    await resume_caption_backfill(db, poster)
    stop = asyncio.Event()

    def _stop(*_args: object) -> None:
        stop.set()
        bridge.stop_cycle.set()
        poster.stop_backfill.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(start_polling(bot, dp), name="polling"),
        asyncio.create_task(scheduler_loop(db, bridge, poster), name="scheduler"),
        asyncio.create_task(heartbeat_loop(db, bridge, bot), name="heartbeat"),
    ]
    stopper = asyncio.create_task(stop.wait())
    _done, pending = await asyncio.wait({*tasks, stopper}, return_when=asyncio.FIRST_COMPLETED)
    for task in [*tasks, *pending]:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    try:
        await dp.stop_polling()
    except RuntimeError:
        log.info("polling уже остановлен")
    try:
        await bridge.disconnect()
    except Exception:
        log.exception("юзербот не отключился")
    try:
        await bot.session.close()
    except Exception:
        log.exception("сессия бота не закрылась")
    try:
        await db.close()
    except Exception:
        log.exception("база не закрылась")
    lock.release()
    log.info("остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(0)
