"""Асинхронный клиент платёжного шлюза Platega (app.platega.io).

Схема запросов и ответов снята с живого API мерчанта «Paskod VPN»
(merchantId 81da406f-...), документации у Platega публичной нет.

Проверено на реальном аккаунте:
  * авторизация — заголовки ``X-MerchantId`` и ``X-Secret``;
  * создание счёта — ``POST /transaction/process``;
  * статус счёта — ``GET /transaction/{id}``;
  * доступен единственный метод оплаты — СБП по QR (код 2, ``SBPQR``);
  * комиссия 8 % добавляется сверху: счёт на 10 ₽ выставляется как 10.80 ₽.

Пример:

    client = PlategaClient(merchant_id=..., secret=...)
    payment = await client.create_payment(
        amount=299,
        description="Paskod VPN — 30 дней",
        payload="tg:123456789",
        return_url="https://t.me/PaskodVPN_bot",
    )
    # payment.redirect_url → отдать пользователю кнопкой «Оплатить»
    ...
    status = await client.get_status(payment.transaction_id)
    if status.is_paid:
        ...
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from typing import Any, Optional

import httpx

API_BASE = "https://app.platega.io"

# Единственный включённый у мерчанта метод. Остальные коды шлюз отклоняет
# с «Wrong input parameters», пока их не активируют в личном кабинете.
class PaymentMethod(IntEnum):
    SBP_QR = 2


class TransactionStatus(str):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


_FINAL_STATUSES = frozenset(
    {TransactionStatus.CONFIRMED, TransactionStatus.CANCELED, TransactionStatus.EXPIRED}
)


class PlategaError(RuntimeError):
    """Шлюз вернул ошибку или неожиданный ответ."""

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class PlategaAuthError(PlategaError):
    """Неверная пара merchantId / secret."""


@dataclass(frozen=True)
class CreatedPayment:
    transaction_id: str
    redirect_url: str
    status: str
    payment_method: str
    amount_with_fee: Optional[Decimal]
    currency: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class PaymentStatus:
    transaction_id: str
    status: str
    amount: Optional[Decimal]
    currency: str
    commission: Optional[Decimal]
    payload: Optional[str]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def is_paid(self) -> bool:
        return self.status == TransactionStatus.CONFIRMED

    @property
    def is_final(self) -> bool:
        return self.status in _FINAL_STATUSES


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


class PlategaClient:
    def __init__(
        self,
        merchant_id: str,
        secret: str,
        *,
        base_url: str = API_BASE,
        timeout: float = 20.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not merchant_id or not secret:
            raise ValueError("merchant_id и secret обязательны")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-MerchantId": merchant_id,
            "X-Secret": secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "PlategaClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str,
                       json_body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        response = await self._client.request(
            method, f"{self._base_url}{path}", headers=self._headers, json=json_body
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if response.status_code in (401, 403):
            raise PlategaAuthError(
                _error_text(payload) or "Platega отклонила merchantId/secret",
                status_code=response.status_code,
                payload=payload,
            )
        if response.status_code >= 400:
            raise PlategaError(
                _error_text(payload) or f"Platega вернула HTTP {response.status_code}",
                status_code=response.status_code,
                payload=payload,
            )
        if not isinstance(payload, dict):
            raise PlategaError("Platega вернула неожиданный формат ответа",
                               status_code=response.status_code, payload=payload)
        return payload

    async def create_payment(
        self,
        amount: Decimal | float | int,
        description: str,
        *,
        currency: str = "RUB",
        payment_method: PaymentMethod = PaymentMethod.SBP_QR,
        payload: Optional[str] = None,
        return_url: Optional[str] = None,
        failed_url: Optional[str] = None,
    ) -> CreatedPayment:
        """Выставить счёт. ``description`` обязателен — без него шлюз отдаёт 400.

        ``payload`` возвращается в статусе счёта без изменений: удобно
        прокидывать туда идентификатор пользователя или заказа.
        """
        if not description:
            raise ValueError("description обязателен для Platega")

        body: dict[str, Any] = {
            "paymentMethod": int(payment_method),
            "paymentDetails": {"amount": float(amount), "currency": currency},
            "description": description,
        }
        if payload is not None:
            body["payload"] = payload
        if return_url is not None:
            body["return"] = return_url
        if failed_url is not None:
            body["failedUrl"] = failed_url

        data = await self._request("POST", "/transaction/process", body)

        transaction_id = data.get("transactionId")
        redirect_url = data.get("redirect")
        if not transaction_id or not redirect_url:
            raise PlategaError("в ответе Platega нет transactionId/redirect", payload=data)

        amount_with_fee, response_currency = _parse_payment_details(
            data.get("paymentDetails"), currency
        )
        return CreatedPayment(
            transaction_id=transaction_id,
            redirect_url=redirect_url,
            status=str(data.get("status") or TransactionStatus.PENDING),
            payment_method=str(data.get("paymentMethod") or payment_method.name),
            amount_with_fee=amount_with_fee,
            currency=response_currency,
            raw=data,
        )

    async def get_status(self, transaction_id: str) -> PaymentStatus:
        data = await self._request("GET", f"/transaction/{transaction_id}")
        details = data.get("paymentDetails") or {}
        if not isinstance(details, dict):
            details = {}
        return PaymentStatus(
            transaction_id=str(data.get("id") or transaction_id),
            status=str(data.get("status") or ""),
            amount=_to_decimal(details.get("amount")),
            currency=str(details.get("currency") or ""),
            commission=_to_decimal(data.get("comission")),
            payload=data.get("payload"),
            raw=data,
        )

    async def wait_for_payment(
        self,
        transaction_id: str,
        *,
        timeout: float = 900.0,
        poll_interval: float = 5.0,
    ) -> PaymentStatus:
        """Опрашивать счёт до финального статуса или истечения ``timeout``.

        Вебхуки Platega не задокументированы и не проверялись на этом
        аккаунте, поэтому подтверждение оплаты строится на опросе.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        status = await self.get_status(transaction_id)
        while not status.is_final and loop.time() < deadline:
            await asyncio.sleep(poll_interval)
            status = await self.get_status(transaction_id)
        return status


def _error_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("message", "title", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    errors = payload.get("errors")
    if isinstance(errors, dict):
        parts = [
            f"{field_name}: {'; '.join(messages)}"
            for field_name, messages in errors.items()
            if isinstance(messages, list)
        ]
        if parts:
            return " | ".join(parts)
    return ""


def _parse_payment_details(details: Any, fallback_currency: str) -> tuple[Optional[Decimal], str]:
    """Разобрать ``paymentDetails``.

    При создании счёта шлюз отдаёт строку «10.80 RUB», при запросе статуса —
    объект ``{"amount": ..., "currency": ...}``.
    """
    if isinstance(details, dict):
        return _to_decimal(details.get("amount")), str(
            details.get("currency") or fallback_currency
        )
    if isinstance(details, str):
        parts = details.split()
        if len(parts) == 2:
            return _to_decimal(parts[0]), parts[1]
        return _to_decimal(details), fallback_currency
    return None, fallback_currency
