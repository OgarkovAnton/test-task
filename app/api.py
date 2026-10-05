import secrets
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Outbox, Payment
from app.schemas import PaymentCreate, PaymentCreated, PaymentRead


router = APIRouter(prefix="/api/v1")


async def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="неверный ключ")


def _created(payment: Payment) -> PaymentCreated:
    return PaymentCreated(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


def _read(payment: Payment) -> PaymentRead:
    return PaymentRead(
        id=payment.id,
        amount=payment.amount,
        currency=payment.currency,
        description=payment.description,
        metadata=payment.metadata_ or {},
        status=payment.status,
        idempotency_key=payment.idempotency_key,
        webhook_url=payment.webhook_url,
        created_at=payment.created_at,
        processed_at=payment.processed_at,
    )


@router.post(
    "/payments",
    status_code=202,
    response_model=PaymentCreated,
    dependencies=[Depends(require_api_key)],
)
async def create_payment(
    data: PaymentCreate,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
    session: AsyncSession = Depends(get_db),
) -> PaymentCreated:
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="пустой Idempotency-Key")

    existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key уже использован",
        )

    payment = Payment(
        id=uuid4(),
        amount=data.amount,
        currency=data.currency,
        description=data.description,
        metadata_=data.metadata,
        idempotency_key=key,
        webhook_url=str(data.webhook_url),
    )
    # платеж и событие пишутся вместе, иначе можно потерять публикацию
    session.add(payment)
    session.add(
        Outbox(
            event_type="payments.new",
            payload={"payment_id": str(payment.id)},
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key уже использован",
            )
        raise


    return _created(payment)


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentRead,
    dependencies=[Depends(require_api_key)],
)
async def get_payment(payment_id: UUID, session: AsyncSession = Depends(get_db)) -> PaymentRead:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="платеж не найден")
    return _read(payment)
