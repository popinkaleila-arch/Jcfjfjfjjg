"""Проверка, что сбой start() не оставляет за собой node-драйвер Playwright.

Запуск браузера ломается принудительно: PLAYWRIGHT_BROWSERS_PATH уводится
в пустой каталог, как это и было на сервере, когда браузеры не установлены.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BROKEN_BROWSERS = tempfile.mkdtemp(prefix="pw-empty-")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROKEN_BROWSERS

sys.path.insert(0, "/root/remanga_autobattle")

# Позволяет прогнать тот же тест против до-патчевой копии сервиса.
_override = os.environ.get("MB_SERVICE_PATH")
if _override:
    sys.path.insert(0, _override)

from config import Config  # noqa: E402
from services.mangabuff_service import MangaBuffService  # noqa: E402


def own_driver_pids() -> list[str]:
    out = subprocess.run(
        ["pgrep", "-P", str(os.getpid()), "-f", "playwright"],
        capture_output=True,
        text=True,
    ).stdout.split()
    return out


async def main() -> int:
    profile = Path(tempfile.mkdtemp(prefix="mb-profile-"))
    config = Config(
        bot_token="0:test",
        telegram_admin_id=0,
        mangabuff_user_data_dir=profile,
        mangabuff_start_url="https://mangabuff.ru/",
        mangabuff_delay_min_sec=1.0,
        mangabuff_delay_max_sec=2.0,
        mangabuff_email="",
        mangabuff_password="",
        selector_timeout_ms=5000,
        user_agent="test-agent",
        viewport_width=1280,
        viewport_height=720,
    )
    svc = MangaBuffService(config=config, user_data_dir=profile)

    before = own_driver_pids()
    print(f"драйверов до вызова: {len(before)}")

    try:
        await svc.start(headless=True)
    except Exception as exc:
        print(f"start() упал ожидаемо: {type(exc).__name__}: {str(exc)[:90]}")
    else:
        print("ОШИБКА ТЕСТА: start() не упал, браузер нашёлся")
        await svc.stop()
        return 1

    await asyncio.sleep(2)
    after = own_driver_pids()
    print(f"драйверов после сбоя: {len(after)}")
    print(f"svc._playwright is None: {svc._playwright is None}")
    print(f"svc._started: {svc._started}")

    ok = not after and svc._playwright is None and svc._started is False
    print("РЕЗУЛЬТАТ:", "OK — утечки нет" if ok else "FAIL — драйвер остался висеть")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
