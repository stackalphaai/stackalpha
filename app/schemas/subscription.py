from datetime import datetime

from pydantic import BaseModel, Field

from app.models.subscription import PaymentStatus, SubscriptionPlan, SubscriptionStatus
from app.schemas.common import BaseSchema, TimestampMixin


class SubscriptionPlanInfo(BaseSchema):
    plan: SubscriptionPlan
    price_usd: float
    features: list[str]
    recommended: bool = False


class CreateSubscriptionRequest(BaseModel):
    plan: SubscriptionPlan
    pay_currency: str = Field(default="USDT", max_length=10)
    auto_renew: bool = True


class SubscriptionResponse(BaseSchema, TimestampMixin):
    id: str
    user_id: str
    plan: SubscriptionPlan
    status: SubscriptionStatus
    price_usd: float
    price_crypto: float | None = None
    crypto_currency: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    grace_period_ends_at: datetime | None = None
    auto_renew: bool
    is_active: bool


class PaymentResponse(BaseSchema, TimestampMixin):
    id: str
    subscription_id: str
    nowpayments_id: str | None = None
    status: PaymentStatus
    amount_usd: float
    amount_crypto: float | None = None
    actually_paid: float | None = None
    pay_currency: str | None = None
    pay_address: str | None = None
    invoice_url: str | None = None
    paid_at: datetime | None = None


class CreatePaymentResponse(BaseSchema):
    payment_id: str
    nowpayments_id: str
    pay_address: str
    pay_amount: float
    pay_currency: str
    invoice_url: str
    expires_at: datetime


class CancelSubscriptionRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class NOWPaymentsIPNPayload(BaseModel):
    payment_id: int | str
    payment_status: str
    pay_address: str | None = None
    price_amount: float
    price_currency: str
    pay_amount: float
    pay_currency: str
    actually_paid: float | None = None
    order_id: str | None = None
    order_description: str | None = None
    payin_hash: str | None = None
    outcome_amount: float | None = None
    outcome_currency: str | None = None
    invoice_id: int | str | None = None
    purchase_id: str | None = None


class SubscriptionStatsResponse(BaseSchema):
    total_subscribers: int
    active_subscribers: int
    monthly_subscribers: int
    yearly_subscribers: int
    monthly_revenue: float
    total_revenue: float
