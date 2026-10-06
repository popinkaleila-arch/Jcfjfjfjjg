"""Живая проверка клиента Platega: создание счёта, статус, отказ авторизации."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations.platega import (  # noqa: E402
    PlategaAuthError,
    PlategaClient,
    PlategaError,
    TransactionStatus,
)

MERCHANT_ID = os.environ["PLATEGA_MERCHANT_ID"]
SECRET = os.environ["PLATEGA_SECRET"]


async def main() -> int:
    failures = []

    async with PlategaClient(MERCHANT_ID, SECRET) as client:
        payment = await client.create_payment(
            amount=10,
            description="Проверка интеграции Platega",
            payload="smoke-test",
            return_url="https://t.me/PaskodVPN_bot",
        )
        print("создан счёт:")
        print(f"  transaction_id  = {payment.transaction_id}")
        print(f"  redirect_url    = {payment.redirect_url}")
        print(f"  status          = {payment.status}")
        print(f"  payment_method  = {payment.payment_method}")
        print(f"  к оплате        = {payment.amount_with_fee} {payment.currency}")

        if payment.status != TransactionStatus.PENDING:
            failures.append(f"ожидался PENDING, получен {payment.status}")
        if not payment.redirect_url.startswith("https://pay.platega.io/"):
            failures.append(f"неожидаемый redirect: {payment.redirect_url}")
        if str(payment.amount_with_fee) != "10.80":
            failures.append(f"ожидалось 10.80 с комиссией, получено {payment.amount_with_fee}")

        status = await client.get_status(payment.transaction_id)
        print("статус счёта:")
        print(f"  status     = {status.status}")
        print(f"  amount     = {status.amount} {status.currency}")
        print(f"  commission = {status.commission}")
        print(f"  payload    = {status.payload}")
        print(f"  is_paid    = {status.is_paid}  is_final = {status.is_final}")

        if status.transaction_id != payment.transaction_id:
            failures.append("get_status вернул другой transaction_id")
        if status.status != TransactionStatus.PENDING:
            failures.append(f"статус неоплаченного счёта = {status.status}")
        if status.payload != "smoke-test":
            failures.append(f"payload не вернулся: {status.payload!r}")
        if status.is_paid or status.is_final:
            failures.append("неоплаченный счёт помечен как оплаченный/финальный")

        try:
            await client.get_status("11111111-2222-4333-8444-555555555555")
        except PlategaError as exc:
            print(f"несуществующий счёт → {type(exc).__name__} (HTTP {exc.status_code})")
            if isinstance(exc, PlategaAuthError):
                failures.append("несуществующий счёт принят за ошибку авторизации")
        else:
            failures.append("несуществующий счёт не вызвал ошибку")

        try:
            await client.create_payment(amount=10, description="")
        except ValueError:
            print("пустой description → ValueError, как и задумано")
        else:
            failures.append("пустой description не отклонён")

    async with PlategaClient(
        "00000000-0000-4000-8000-000000000000", "a" * 100
    ) as bogus:
        try:
            await bogus.get_status("11111111-2222-4333-8444-555555555555")
        except PlategaAuthError as exc:
            print(f"подставные данные → PlategaAuthError (HTTP {exc.status_code})")
        except PlategaError as exc:
            failures.append(f"подставные данные дали {type(exc).__name__}, ждали PlategaAuthError")
        else:
            failures.append("подставные данные не отклонены")

    print()
    if failures:
        print("ПРОВАЛЕНО:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
