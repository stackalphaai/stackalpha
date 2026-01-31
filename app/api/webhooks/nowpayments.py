import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import NOWPaymentsIPNPayload
from app.services import PaymentService
from app.services.email_service import get_email_service
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Type aliases for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]
Signature = Annotated[str | None, Header()]


@router.post("/nowpayments")
async def nowpayments_ipn(
    request: Request,
    payload: NOWPaymentsIPNPayload,
    db: DB,
    x_nowpayments_sig: Signature = None,
):
    logger.info(
        f"Received NOWPayments IPN: payment_id={payload.payment_id}, "
        f"status={payload.payment_status}, order_id={payload.order_id}, "
        f"invoice_id={payload.invoice_id}, actually_paid={payload.actually_paid}"
    )

    payment_service = PaymentService(db)
    payment = await payment_service.process_webhook(
        payload=payload,
        signature=x_nowpayments_sig or "",
    )
    await db.commit()

    if payload.payment_status == "finished":
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.models import Subscription

        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(Subscription.id == payment.subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if subscription and subscription.user:
            email_service = get_email_service()
            try:
                await email_service.send_subscription_activated_email(
                    to_email=subscription.user.email,
                    plan=subscription.plan.value,
                    expires_at=subscription.expires_at.strftime("%Y-%m-%d")
                    if subscription.expires_at
                    else "N/A",
                )
            except Exception as e:
                logger.error(f"Failed to send activation email: {e}")

            telegram_service = TelegramService(db)
            connection = await telegram_service.get_connection_by_user(subscription.user.id)
            if connection and connection.is_verified:
                try:
                    await telegram_service.send_subscription_notification(connection, "activated")
                except Exception as e:
                    logger.error(f"Failed to send Telegram notification: {e}")

    return {"status": "ok"}
