"""Разведка DOM страницы входа MangaBuff: где лежат поля и кнопка «Войти»."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root/remanga_autobattle")

from playwright.async_api import async_playwright  # noqa: E402

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

PROBE_JS = """() => {
  const info = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const modal = el.closest('.modal');
    return {
      tag: el.tagName.toLowerCase(),
      cls: el.className || '',
      id: el.id || '',
      visible: !!(r.width && r.height) && cs.visibility !== 'hidden' && cs.display !== 'none',
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      inModal: modal ? (modal.id || modal.className) : null,
    };
  };
  const list = (sel) => [...document.querySelectorAll(sel)].map(info);
  const openModals = [...document.querySelectorAll('.modal')]
    .filter((m) => m.classList.contains('modal--open'))
    .map((m) => ({ id: m.id, cls: m.className }));
  return {
    url: location.href,
    openModals,
    emails: list('input[name=email]'),
    passwords: list('input[name=password]'),
    loginButtons: list('button.login-button'),
    submitButtons: list('#modal-auth button, form button'),
  };
}"""


async def main() -> int:
    profile = Path(tempfile.mkdtemp(prefix="mb-probe-"))
    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            viewport={"width": 1280, "height": 720},
            user_agent=UA,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://mangabuff.ru/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        print("=== как есть ===")
        print(json.dumps(await page.evaluate(PROBE_JS), ensure_ascii=False, indent=2))

        await page.evaluate(
            """() => {
              for (const el of document.querySelectorAll('#modal-tenor, .modal--fixed-bottom')) {
                el.classList.remove('modal--open');
                el.style.display = 'none';
              }
            }"""
        )
        await page.wait_for_timeout(500)
        print("=== после скрытия прилипших баннеров ===")
        print(json.dumps(await page.evaluate(PROBE_JS), ensure_ascii=False, indent=2))
        await ctx.close()
    finally:
        await pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
