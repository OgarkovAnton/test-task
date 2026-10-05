import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.broker import broker, payments_exchange
from app.db import Session
from app.models import Outbox

log = logging.getLogger(__name__)


async def publish_pending() -> int:
    async with Session() as session:
        rows = list(
            await session.scalars(
                select(Outbox)
                .where(Outbox.published_at.is_(None))
                .order_by(Outbox.created_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        if not rows:
            return 0

        now = datetime.now(timezone.utc)
        for row in rows:
            await broker.publish(
                row.payload,
                exchange=payments_exchange,
                routing_key=row.event_type,
                persist=True,
            )
            row.published_at = now
        await session.commit()
        return len(rows)


async def relay() -> None:
    while True:
        try:
            published = await publish_pending()
            if published:
                log.info("опубликовано из outbox: %s", published)
        except Exception as exc:
            log.warning("outbox: %s", exc)
        await asyncio.sleep(1)
