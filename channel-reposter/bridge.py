from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, UserDeactivated, SessionRevoked

import config
from database import AuthCreds, Database

log = logging.getLogger("bridge")


class UserbotBridge:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.client: Client | None = None
        self.lock = asyncio.Lock()
        self.authorized = False
        self.me: Any = None
        self.stop_cycle = asyncio.Event()
        self.cycle = "idle"
        self.current_pair = "—"

    def _build(self, creds: AuthCreds) -> Client:
        assert creds.api_id and creds.api_hash
        return Client(
            name=config.SESSION_NAME,
            api_id=int(creds.api_id),
            api_hash=str(creds.api_hash),
            workdir=str(config.ROOT),
            no_updates=True,
        )

    async def _close_client(self) -> None:
        if self.client is None:
            return
        try:
            if self.client.is_connected:
                await self.client.disconnect()
        except Exception:
            log.exception("disconnect")
        self.client = None
        self.authorized = False
        self.me = None

    async def attach(self) -> None:
        creds = await self.db.auth()
        if not creds.api_id or not creds.api_hash:
            return
        if not config.SESSION_PATH.exists():
            return
        async with self.lock:
            await self._close_client()
            self.client = self._build(creds)
            try:
                await self.client.start()
                self.me = await self.client.get_me()
                self.authorized = True
                log.info("юзербот вошёл как %s", self.me.id)
            except (AuthKeyUnregistered, UserDeactivated, SessionRevoked, ValueError) as exc:
                log.warning("сессия недействительна: %s", exc)
                await self._close_client()
            except Exception:
                log.exception("не удалось поднять юзербота")
                await self._close_client()

    async def disconnect(self) -> None:
        async with self.lock:
            await self._close_client()

    def logged_in(self) -> bool:
        return bool(self.authorized and self.client and self.client.is_connected)

    def status_text(self) -> str:
        if self.logged_in() and self.me:
            name = self.me.first_name or ""
            username = f"@{self.me.username}" if self.me.username else str(self.me.id)
            return f"есть ({name} {username})".strip()
        return "нет входа"
