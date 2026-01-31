from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser
from app.models import Payment, Subscription, SubscriptionPlan, SubscriptionStatus
from app.schemas import (
    CancelSubscriptionRequest,
    CreatePaymentResponse,
    CreateSubscriptionRequest,
    PaymentResponse,
    SubscriptionPlanInfo,
    SubscriptionResponse,
    SuccessResponse,
)
from app.services import PaymentService

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/plans", response_model=list[SubscriptionPlanInfo])
async def get_subscription_plans():
    return [
        SubscriptionPlanInfo(
            plan=SubscriptionPlan.MONTHLY,
            price_usd=settings.subscription_monthly_price,
            features=[
                "AI-powered trading signals",
                "Automated trade execution",
                "Real-time notifications",
                "Up to 5 concurrent positions",
                "Email & Telegram alerts",
            ],
            recommended=False,
        ),
        SubscriptionPlanInfo(
            plan=SubscriptionPlan.YEARLY,
            price_usd=settings.subscription_yearly_price,
            features=[
                "All monthly features",
                "2 months free",
                "Priority support",
                "Early access to new features",
                "Custom risk settings",
            ],
            recommended=True,
        ),
    ]


@router.get("", response_model=list[SubscriptionResponse])
async def get_subscriptions(
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
    )
    subscriptions = list(result.scalars().all())
    return subscriptions


@router.get("/current", response_model=SubscriptionResponse)
async def get_current_subscription(
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status.in_(
                [
                    SubscriptionStatus.ACTIVE,
                    SubscriptionStatus.GRACE_PERIOD,
                ]
            ),
        )
        .order_by(Subscription.expires_at.desc())
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Active subscription")

    return subscription


@router.post("", response_model=CreatePaymentResponse)
async def create_subscription(
    data: CreateSubscriptionRequest,
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == current_user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
        )
    )
    if result.scalar_one_or_none():
        from app.core.exceptions import BadRequestError

        raise BadRequestError("You already have an active subscription")

    payment_service = PaymentService(db)
    subscription, payment = await payment_service.create_subscription(
        user=current_user,
        plan=data.plan,
        pay_currency=data.pay_currency,
    )

    subscription.auto_renew = data.auto_renew
    await db.commit()

    return CreatePaymentResponse(
        payment_id=payment.id,
        nowpayments_id=payment.nowpayments_id or "",
        pay_address=payment.pay_address or "",
        pay_amount=payment.amount_crypto or 0,
        pay_currency=payment.pay_currency or data.pay_currency,
        invoice_url=payment.invoice_url or "",
        expires_at=payment.created_at,
    )


@router.post("/{subscription_id}/cancel", response_model=SuccessResponse)
async def cancel_subscription(
    subscription_id: str,
    data: CancelSubscriptionRequest,
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == current_user.id,
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Subscription")

    payment_service = PaymentService(db)
    await payment_service.cancel_subscription(subscription, data.reason)
    await db.commit()

    return SuccessResponse(message="Subscription cancelled. Access continues until expiry.")


@router.get("/{subscription_id}/payments", response_model=list[PaymentResponse])
async def get_subscription_payments(
    subscription_id: str,
    current_user: CurrentUser,
    db: DB,
):
    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == current_user.id,
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Subscription")

    result = await db.execute(
        select(Payment)
        .where(Payment.subscription_id == subscription_id)
        .order_by(Payment.created_at.desc())
    )
    payments = list(result.scalars().all())

    return payments


@router.get("/currencies")
async def get_available_currencies(
    db: DB,
):
    payment_service = PaymentService(db)
    currencies = await payment_service.get_available_currencies()
    return {"currencies": currencies}
