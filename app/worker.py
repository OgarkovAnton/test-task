import asyncio
import logging
import random
from decimal import Decimal
from uuid import UUID

import httpx
from faststream import FastStream
from pydantic import BaseModel

from app.broker import broker, dlx, start_rabbit, payments_exchange, payments_queue
from app.db import Session
from app.models import Payment, PaymentStatus, utcnow
from app.outbox import relay

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("app.worker")

app = FastStream(broker)
_relay_task: asyncio.Task | None = None


class PaymentEvent(BaseModel):
    payment_id: UUID


@app.on_startup
async def startup() -> None:
    await start_rabbit()


@app.after_startup
async def start_relay() -> None:
    global _relay_task
    _relay_task = asyncio.create_task(relay())


@app.on_shutdown
async def shutdown() -> None:
    if _relay_task is None:
        return
    _relay_task.cancel()
    try:
        await _relay_task
    except asyncio.CancelledError:
        pass


@broker.subscriber(payments_queue, payments_exchange)
async def handle_new_payment(event: PaymentEvent) -> None:
    for attempt in range(1, 4):
        try:
            await process_payment(event.payment_id)
            return
        except Exception as exc:
            log.warning("платеж %s, попытка %s: %s", event.payment_id, attempt, exc)
            if attempt == 3:
                await broker.publish(
                    {"payment_id": str(event.payment_id), "error": str(exc)[:500]},
                    exchange=dlx,
                    routing_key="payments.failed",
                    persist=True,
                )
                log.error("платеж %s ушел в payments.dlq", event.payment_id)
                return
            # 1с после первой ошибки, 2с после второй
            await asyncio.sleep(2 ** (attempt - 1))


async def process_payment(payment_id: UUID) -> None:
    async with Session() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            log.warning("платеж %s не найден", payment_id)
            return
        status = payment.status
        webhook_url = payment.webhook_url
        amount = payment.amount
        currency = payment.currency

    if status == PaymentStatus.pending:
        ok = await charge()
        new_status = PaymentStatus.succeeded if ok else PaymentStatus.failed
        async with Session() as session:
            async with session.begin():
                payment = await session.get(Payment, payment_id, with_for_update=True)
                if payment is None:
                    return
                if payment.status == PaymentStatus.pending:
                    payment.status = new_status
                    payment.processed_at = utcnow()
                    log.info("платеж %s -> %s", payment_id, new_status.value)
                status = payment.status
                webhook_url = payment.webhook_url
                amount = payment.amount
                currency = payment.currency

    await send_webhook(
        webhook_url,
        {
            "payment_id": str(payment_id),
            "status": status,
            "amount": f"{Decimal(amount):.2f}",
            "currency": currency,
        },
    )
    log.info("webhook отправлен, платеж %s", payment_id)


async def charge() -> bool:
    # заглушка шлюза: 2-5 сек, примерно 90% успех
    await asyncio.sleep(random.uniform(2, 5))
    return random.random() < 0.9


async def send_webhook(url: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(app.run())
