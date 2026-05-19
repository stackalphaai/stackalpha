import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.config import settings
from app.models import (
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
)
from app.schemas import NOWPaymentsIPNPayload
from app.services.payment_service import PaymentService


class _ScalarResult:
    def __init__(self, value: Any):
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _FakeDB:
    def __init__(self, *results: Any):
        self._results = list(results)
        self.execute_count = 0

    async def execute(self, _statement: Any) -> _ScalarResult:
        self.execute_count += 1
        return _ScalarResult(self._results.pop(0) if self._results else None)


def _signature(secret: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return hmac.new(secret.encode(), body.encode(), hashlib.sha512).hexdigest()


def test_nowpayments_signature_uses_sorted_json_body() -> None:
    service = PaymentService(db=_FakeDB())
    service.ipn_secret = "test-secret"
    payload = {
        "payment_status": "finished",
        "payment_id": 123,
        "fee": {"serviceFee": 0, "depositFee": 0.1},
    }
    raw_body = json.dumps(payload, indent=2).encode()

    assert service._verify_signature(raw_body, _signature("test-secret", payload))


def test_nowpayments_signature_missing_secret_fails_closed_in_production(monkeypatch) -> None:
    service = PaymentService(db=_FakeDB())
    service.ipn_secret = ""
    monkeypatch.setattr(settings, "app_env", "production")

    assert not service._verify_signature({"payment_id": 123}, "anything")


@pytest.mark.asyncio
async def test_finished_webhook_activates_subscription_once() -> None:
    user = User(email="user@example.com", hashed_password="hash")
    subscription = Subscription(
        id="sub-id",
        user_id="user-id",
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.PENDING,
        price_usd=50,
    )
    payment = Payment(
        id="payment-row-id",
        subscription_id="sub-id",
        nowpayments_id="invoice-id",
        nowpayments_order_id="sub_sub-id",
        status=PaymentStatus.WAITING,
        amount_usd=50,
    )
    service = PaymentService(db=_FakeDB(payment, subscription, user))
    service.ipn_secret = ""

    result = await service.process_webhook(
        payload=NOWPaymentsIPNPayload(
            payment_id="payment-id",
            payment_status="finished",
            price_amount=50,
            price_currency="usd",
            pay_amount=50,
            pay_currency="usdt",
            order_id="sub_sub-id",
        ),
        signature="",
    )

    assert result.subscription_activated
    assert payment.status == PaymentStatus.FINISHED
    assert payment.paid_at is not None
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert user.is_subscribed


@pytest.mark.asyncio
async def test_duplicate_finished_webhook_has_no_activation_side_effects() -> None:
    paid_at = datetime(2026, 1, 1, tzinfo=UTC)
    payment = Payment(
        id="payment-row-id",
        subscription_id="sub-id",
        nowpayments_id="payment-id",
        nowpayments_order_id="sub_sub-id",
        status=PaymentStatus.FINISHED,
        amount_usd=50,
        paid_at=paid_at,
    )
    db = _FakeDB(payment)
    service = PaymentService(db=db)
    service.ipn_secret = ""

    result = await service.process_webhook(
        payload=NOWPaymentsIPNPayload(
            payment_id="payment-id",
            payment_status="finished",
            price_amount=50,
            price_currency="usd",
            pay_amount=50,
            pay_currency="usdt",
            order_id="sub_sub-id",
        ),
        signature="",
    )

    assert not result.status_changed
    assert not result.subscription_activated
    assert payment.paid_at == paid_at
    assert db.execute_count == 1


@pytest.mark.asyncio
async def test_post_finished_status_does_not_downgrade_payment() -> None:
    payment = Payment(
        id="payment-row-id",
        subscription_id="sub-id",
        nowpayments_id="payment-id",
        nowpayments_order_id="sub_sub-id",
        status=PaymentStatus.FINISHED,
        amount_usd=50,
        paid_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db = _FakeDB(payment)
    service = PaymentService(db=db)
    service.ipn_secret = ""

    result = await service.process_webhook(
        payload=NOWPaymentsIPNPayload(
            payment_id="payment-id",
            payment_status="failed",
            price_amount=50,
            price_currency="usd",
            pay_amount=50,
            pay_currency="usdt",
            order_id="sub_sub-id",
        ),
        signature="",
    )

    assert not result.status_changed
    assert not result.subscription_activated
    assert payment.status == PaymentStatus.FINISHED
    assert db.execute_count == 1
