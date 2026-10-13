"""One-off live checks against the destination channel. Not collected by pytest."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from aiogram import Bot

from bot_editor import BotEditor
from formatting import normalize_caption_html

CHAT = os.getenv("LIVE_CHAT", "@sw3atsh0p")
ADMIN = int(os.getenv("LIVE_ADMIN", "7835556726"))
MARKERS = {
    100: "CAPTEST-A-78421",
    3998: "CAPTEST-B-78421",
    5600: "CAPTEST-C-78421",
}


async def read_caption(bot: Bot, mid: int) -> str:
    copied = await bot.forward_message(chat_id=ADMIN, from_chat_id=CHAT, message_id=mid)
    caption = copied.caption or copied.text or ""
    try:
        await bot.delete_message(ADMIN, copied.message_id)
    except Exception:
        pass
    return caption


async def main() -> None:
    token = os.environ["BOT_TOKEN"]
    promo_raw = os.environ.get("LIVE_PROMO", "")
    if not promo_raw:
        import sqlite3

        db_path = os.environ.get("DATABASE_PATH", "/opt/channel-reposter/data/reposter.sqlite3")
        promo_raw = sqlite3.connect(db_path).execute("select caption from windows where id=1").fetchone()[0]
    promo = normalize_caption_html(promo_raw)
    bot = Bot(token)
    editor = BotEditor(bot)
    failed: list[str] = []
    try:
        rights = await editor.check_rights(CHAT)
        print("rights", rights.ok, rights.detail)
        if not rights.ok:
            raise SystemExit("no rights")
        for mid, marker in MARKERS.items():
            print("edit", mid, marker)
            status = await editor.edit_caption(CHAT, mid, marker)
            print(" ", status)
            await asyncio.sleep(3.2)
            got = await read_caption(bot, mid)
            print("  reread", repr(got[:80]))
            if marker not in got:
                failed.append(f"{mid} missing marker {marker!r} got {got!r}")
            print("  restore", mid)
            status = await editor.edit_caption(CHAT, mid, promo)
            print(" ", status)
            await asyncio.sleep(3.2)
            got = await read_caption(bot, mid)
            print("  restored", repr(got[:80]), "above_ok", "VPN" in got or "test" in got.lower())
            if not got.strip():
                failed.append(f"{mid} empty after restore")
        newest = 6645
        print("edit newest", newest)
        status = await editor.edit_caption(CHAT, newest, promo)
        print(" ", status)
        await asyncio.sleep(3.2)
        got = await read_caption(bot, newest)
        print("  newest", repr(got[:100]))
        if "VPN" not in got and "vpn" not in got.lower():
            failed.append(f"{newest} promo not visible: {got!r}")
    finally:
        await bot.session.close()
    if failed:
        print("FAILED")
        for item in failed:
            print(" -", item)
        raise SystemExit(1)
    print("LIVE_OK", len(MARKERS) + 1, "posts")


if __name__ == "__main__":
    asyncio.run(main())
