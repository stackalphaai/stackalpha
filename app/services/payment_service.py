import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BadRequestError, PaymentError, WebhookValidationError
from app.models import (
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
)
from app.schemas.subscription import NOWPaymentsIPNPayload

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.api_key = settings.nowpayments_api_key
        self.ipn_secret = settings.nowpayments_ipn_secret
        self.api_url = settings.nowpayments_api_url
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                timeout=30.0,
                headers={
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def create_subscription(
        self,
        user: User,
        plan: SubscriptionPlan,
        pay_currency: str = "USDT",
    ) -> tuple[Subscription, Payment]:
        if plan == SubscriptionPlan.MONTHLY:
            price_usd = settings.subscription_monthly_price
        else:
            price_usd = settings.subscription_yearly_price

        subscription = Subscription(
            user_id=user.id,
            plan=plan,
            status=SubscriptionStatus.PENDING,
            price_usd=price_usd,
            crypto_currency=pay_currency,
            auto_renew=True,
        )

        self.db.add(subscription)
        await self.db.flush()

        payment = await self._create_payment(subscription, pay_currency)

        return subscription, payment

    async def _create_payment(
        self,
        subscription: Subscription,
        pay_currency: str,
    ) -> Payment:
        client = await self.get_client()

        try:
            # Use /invoice endpoint to get a hosted payment page with invoice_url
            payload = {
                "price_amount": float(subscription.price_usd),
                "price_currency": "usd",
                "pay_currency": pay_currency.lower(),
                "order_id": f"sub_{subscription.id}",
                "order_description": f"StackAlpha {subscription.plan.value} subscription",
                "ipn_callback_url": settings.nowpayments_ipn_callback_url,
                "success_url": settings.nowpayments_success_url,
                "cancel_url": settings.nowpayments_cancel_url,
            }

            response = await client.post("/invoice", json=payload)
            response.raise_for_status()
            data = response.json()

            logger.info(f"NOWPayments invoice created: {data}")

            # Get invoice_url from response, or construct it from invoice id
            invoice_id = data.get("id")
            invoice_url = data.get("invoice_url")
            if not invoice_url and invoice_id:
                invoice_url = f"https://nowpayments.io/payment/?iid={invoice_id}"

            payment = Payment(
                subscription_id=subscription.id,
                nowpayments_id=str(invoice_id),
                nowpayments_order_id=f"sub_{subscription.id}",
                status=PaymentStatus.WAITING,
                amount_usd=subscription.price_usd,
                amount_crypto=data.get("pay_amount"),
                pay_currency=pay_currency,
                pay_address=data.get("pay_address", ""),
                invoice_url=invoice_url,
            )

            self.db.add(payment)
            await self.db.flush()

            return payment

        except httpx.HTTPStatusError as e:
            logger.error(f"NOWPayments API error: {e.response.text}")
            raise PaymentError(f"Failed to create payment: {e.response.text}") from e
        except Exception as e:
            logger.error(f"Payment creation error: {e}")
            raise PaymentError(f"Failed to create payment: {str(e)}") from e

    async def process_webhook(
        self,
        payload: NOWPaymentsIPNPayload,
        signature: str,
    ) -> Payment:
        if not self._verify_signature(payload.model_dump(), signature):
            raise WebhookValidationError("Invalid IPN signature")

        # Try to find payment by payment_id first, then by order_id
        result = await self.db.execute(
            select(Payment).where(Payment.nowpayments_id == str(payload.payment_id))
        )
        payment = result.scalar_one_or_none()

        # If not found by payment_id, try by order_id (for invoice-based payments)
        if not payment and payload.order_id:
            result = await self.db.execute(
                select(Payment).where(Payment.nowpayments_order_id == payload.order_id)
            )
            payment = result.scalar_one_or_none()
            # Update the nowpayments_id with the actual payment_id from webhook
            if payment:
                payment.nowpayments_id = str(payload.payment_id)

        if not payment:
            logger.warning(
                f"Payment not found: payment_id={payload.payment_id}, order_id={payload.order_id}"
            )
            raise BadRequestError("Payment not found")

        old_status = payment.status
        payment.status = PaymentStatus(payload.payment_status)
        payment.actually_paid = payload.actually_paid
        payment.payin_hash = payload.payin_hash

        if payment.status == PaymentStatus.FINISHED:
            payment.paid_at = datetime.now(UTC)
            await self._activate_subscription(payment)
        elif payment.status == PaymentStatus.FAILED:
            await self._handle_failed_payment(payment)

        logger.info(f"Payment {payment.id} status updated: {old_status} -> {payment.status}")

        return payment

    async def _activate_subscription(self, payment: Payment):
        result = await self.db.execute(
            select(Subscription).where(Subscription.id == payment.subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            return

        now = datetime.now(UTC)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.starts_at = now

        if subscription.plan == SubscriptionPlan.MONTHLY:
            subscription.expires_at = now + timedelta(days=30)
        else:
            subscription.expires_at = now + timedelta(days=365)

        subscription.grace_period_ends_at = subscription.expires_at + timedelta(
            days=settings.subscription_grace_period_days
        )

        # Update denormalized flag on user
        user_result = await self.db.execute(select(User).where(User.id == subscription.user_id))
        user = user_result.scalar_one_or_none()
        if user:
            user.is_subscribed = True

        logger.info(f"Subscription {subscription.id} activated until {subscription.expires_at}")

    async def _handle_failed_payment(self, payment: Payment):
        result = await self.db.execute(
            select(Subscription).where(Subscription.id == payment.subscription_id)
        )
        subscription = result.scalar_one_or_none()

        if subscription and subscription.status == SubscriptionStatus.PENDING:
            subscription.status = SubscriptionStatus.EXPIRED

    def _verify_signature(self, payload: dict[str, Any], signature: str) -> bool:
        if not self.ipn_secret:
            return True

        sorted_payload = dict(sorted(payload.items()))
        payload_string = ""
        for _key, value in sorted_payload.items():
            if value is not None:
                payload_string += str(value)

        expected_signature = hmac.new(
            self.ipn_secret.encode(),
            payload_string.encode(),
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(signature, expected_signature)

    async def get_payment_status(self, payment_id: str) -> dict[str, Any]:
        client = await self.get_client()

        try:
            response = await client.get(f"/payment/{payment_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get payment status: {e}")
            raise PaymentError(f"Failed to get payment status: {str(e)}") from e

    async def get_available_currencies(self) -> list[str]:
        # Only accept these currencies
        accepted_currencies = ["USDC", "USDT", "ETH", "BTC", "SOL", "BNB", "XRP", "LTC"]
        return accepted_currencies

    async def get_minimum_amount(self, currency: str) -> float:
        client = await self.get_client()

        try:
            response = await client.get(
                "/min-amount",
                params={"currency_from": currency, "currency_to": "usd"},
            )
            response.raise_for_status()
            data = response.json()
            return float(data.get("min_amount", 0))
        except Exception as e:
            logger.error(f"Failed to get minimum amount: {e}")
            return 0.0

    async def check_expired_subscriptions(self) -> int:
        now = datetime.now(UTC)

        result = await self.db.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at < now,
            )
        )
        expired = list(result.scalars().all())

        for sub in expired:
            if sub.grace_period_ends_at and now < sub.grace_period_ends_at:
                sub.status = SubscriptionStatus.GRACE_PERIOD
            else:
                sub.status = SubscriptionStatus.EXPIRED
                # Clear denormalized flag on user
                user_result = await self.db.execute(select(User).where(User.id == sub.user_id))
                user = user_result.scalar_one_or_none()
                if user:
                    user.is_subscribed = False

        # Also expire grace period subscriptions that have passed their grace window
        grace_result = await self.db.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.GRACE_PERIOD,
                Subscription.grace_period_ends_at < now,
            )
        )
        grace_expired = list(grace_result.scalars().all())

        for sub in grace_expired:
            sub.status = SubscriptionStatus.EXPIRED
            user_result = await self.db.execute(select(User).where(User.id == sub.user_id))
            user = user_result.scalar_one_or_none()
            if user:
                user.is_subscribed = False

        return len(expired) + len(grace_expired)

    async def cancel_subscription(
        self,
        subscription: Subscription,
        reason: str | None = None,
    ) -> Subscription:
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.auto_renew = False

        # Clear denormalized flag on user
        user_result = await self.db.execute(select(User).where(User.id == subscription.user_id))
        user = user_result.scalar_one_or_none()
        if user:
            user.is_subscribed = False

        return subscription
