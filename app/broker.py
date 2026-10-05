import asyncio
import logging

import aio_pika
from faststream.rabbit import QueueType, RabbitBroker, RabbitExchange, RabbitQueue

from app.config import settings

log = logging.getLogger(__name__)

broker = RabbitBroker(settings.rabbitmq_url)

payments_exchange = RabbitExchange("payments", durable=True)
# если процесс умрет посередине обработки, кролик сам уведет сообщение в dlq
payments_queue = RabbitQueue(
    "payments.new",
    queue_type=QueueType.QUORUM,
    durable=True,
    routing_key="payments.new",
    arguments={
        "x-delivery-limit": 3,
        "x-dead-letter-exchange": "payments.dlx",
        "x-dead-letter-routing-key": "payments.failed",
    },
)
dlx = RabbitExchange("payments.dlx", durable=True)


async def start_rabbit() -> None:
    last_error: Exception | None = None
    for _ in range(10):
        try:
            await _declare()
            return
        except Exception as exc:
            last_error = exc
            log.warning("rabbit еще не готов: %s", exc)
            await asyncio.sleep(2)
    assert last_error is not None
    raise last_error


async def _declare() -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    try:
        channel = await connection.channel()
        dead_exchange = await channel.declare_exchange(
            "payments.dlx",
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        dead_queue = await channel.declare_queue("payments.dlq", durable=True)
        await dead_queue.bind(dead_exchange, routing_key="payments.failed")

        exchange = await channel.declare_exchange(
            "payments",
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        queue = await channel.declare_queue(
            "payments.new",
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-delivery-limit": 3,
                "x-dead-letter-exchange": "payments.dlx",
                "x-dead-letter-routing-key": "payments.failed",
            },
        )
        await queue.bind(exchange, routing_key="payments.new")
    finally:
        await connection.close()
