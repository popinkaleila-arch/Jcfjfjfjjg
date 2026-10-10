from __future__ import annotations

import logging
import time

from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)

import config
from bridge import UserbotBridge
from database import Database

log = logging.getLogger("userbot_auth")


class AuthError(RuntimeError):
    pass


def _phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit() or ch == "+")
    if digits.startswith("8") and len(digits) == 11:
        digits = "+7" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    if len(digits) < 10:
        raise AuthError("не похоже на номер телефона")
    return digits


class UserbotAuth:
    def __init__(self, db: Database, bridge: UserbotBridge) -> None:
        self.db = db
        self.bridge = bridge
        self._temp: Client | None = None

    async def _temp_client(self) -> Client:
        creds = await self.db.auth()
        if not creds.api_id or not creds.api_hash:
            raise AuthError("сначала отправьте api_id и api_hash")
        if self._temp is None:
            self._temp = Client(
                name=config.SESSION_NAME,
                api_id=int(creds.api_id),
                api_hash=str(creds.api_hash),
                workdir=str(config.ROOT),
                no_updates=True,
            )
        if not self._temp.is_connected:
            await self._temp.connect()
        return self._temp

    async def save_api(self, api_id: str, api_hash: str) -> None:
        try:
            parsed = int(str(api_id).strip())
        except ValueError as exc:
            raise AuthError("api_id должен быть числом") from exc
        hashed = str(api_hash).strip()
        if len(hashed) < 16:
            raise AuthError("api_hash слишком короткий")
        creds = await self.db.auth()
        creds.api_id = parsed
        creds.api_hash = hashed
        await self.db.save_auth(creds)

    async def send_code(self, phone: str) -> str:
        creds = await self.db.auth()
        if creds.flood_until > time.time():
            left = int(creds.flood_until - time.time())
            raise AuthError(self._flood_text(left))
        await self.bridge.disconnect()
        phone = _phone(phone)
        client = await self._temp_client()
        try:
            sent = await client.send_code(phone)
        except FloodWait as exc:
            creds.flood_until = time.time() + int(exc.value)
            creds.phone = phone
            await self.db.save_auth(creds)
            raise AuthError(self._flood_text(int(exc.value))) from exc
        except PhoneNumberInvalid as exc:
            raise AuthError("Telegram не принял номер") from exc
        creds.phone = phone
        creds.phone_code_hash = sent.phone_code_hash
        await self.db.save_auth(creds)
        via = getattr(sent.type, "name", str(sent.type))
        return f"код отправлен ({via}). пришлите цифры из Telegram"

    async def sign_in(self, code: str) -> str:
        creds = await self.db.auth()
        if not creds.phone or not creds.phone_code_hash:
            raise AuthError("сначала запросите код")
        client = await self._temp_client()
        digits = "".join(ch for ch in code if ch.isdigit())
        if len(digits) < 4:
            raise AuthError("код слишком короткий")
        try:
            await client.sign_in(creds.phone, creds.phone_code_hash, digits)
        except SessionPasswordNeeded:
            return "нужен пароль 2FA — отправьте облачный пароль"
        except PhoneCodeInvalid as exc:
            raise AuthError("неверный код") from exc
        except PhoneCodeExpired as exc:
            raise AuthError("код устарел, запросите новый") from exc
        except FloodWait as exc:
            creds.flood_until = time.time() + int(exc.value)
            await self.db.save_auth(creds)
            raise AuthError(self._flood_text(int(exc.value))) from exc
        await self._finish(client)
        return "юзербот вошёл"

    async def check_password(self, password: str) -> str:
        client = await self._temp_client()
        try:
            await client.check_password(password.strip())
        except FloodWait as exc:
            creds = await self.db.auth()
            creds.flood_until = time.time() + int(exc.value)
            await self.db.save_auth(creds)
            raise AuthError(self._flood_text(int(exc.value))) from exc
        except Exception as exc:
            raise AuthError(f"пароль не принят: {exc}") from exc
        await self._finish(client)
        return "юзербот вошёл (2FA)"

    async def _finish(self, client: Client) -> None:
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            log.exception("temp disconnect")
        self._temp = None
        await self.bridge.attach()
        if not self.bridge.logged_in():
            raise AuthError("сессия записалась, но войти не удалось — проверьте api_id/api_hash")

    @staticmethod
    def _flood_text(seconds: int) -> str:
        hours, rem = divmod(max(0, seconds), 3600)
        minutes = rem // 60
        if hours:
            pretty = f"{hours} ч {minutes} мин"
        else:
            pretty = f"{minutes} мин" if minutes else f"{seconds} сек"
        return (
            f"Telegram просит подождать {pretty} ({seconds} сек) перед новым кодом. "
            "Не запрашивайте код повторно — ожидание вырастет."
        )
