"""Проверка входа в MangaBuff: получается ли авторизоваться на чистом профиле."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root/remanga_autobattle")

_override = os.environ.get("MB_SERVICE_PATH")
if _override:
    sys.path.insert(0, _override)

from config import Config  # noqa: E402
from services.mangabuff_service import MangaBuffService  # noqa: E402


async def main() -> int:
    profile = Path(tempfile.mkdtemp(prefix="mb-login-"))
    config = Config(
        bot_token="0:test",
        telegram_admin_id=0,
        mangabuff_user_data_dir=profile,
        mangabuff_start_url="https://mangabuff.ru/",
        mangabuff_delay_min_sec=1.0,
        mangabuff_delay_max_sec=2.0,
        mangabuff_email=os.environ.get("MANGABUFF_EMAIL", ""),
        mangabuff_password=os.environ.get("MANGABUFF_PASSWORD", ""),
        selector_timeout_ms=15000,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        viewport_width=1280,
        viewport_height=720,
    )

    svc = MangaBuffService(
        config=config,
        user_data_dir=profile,
        email=os.environ.get("MANGABUFF_EMAIL", ""),
        password=os.environ.get("MANGABUFF_PASSWORD", ""),
    )
    print("патч _clear_login_overlays:", hasattr(svc, "_clear_login_overlays"))

    await svc.start(headless=True)
    try:
        ok = await svc._ensure_login_unlocked()
        print("результат входа:", "OK — авторизован" if ok else "FAIL — не авторизован")
        page = svc._page
        if page is not None:
            print("url после входа:", page.url)
        return 0 if ok else 1
    finally:
        await svc.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
