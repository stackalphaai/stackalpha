from datetime import datetime

from pydantic import BaseModel, Field

from app.models.affiliate import PayoutStatus
from app.schemas.common import BaseSchema, TimestampMixin


class AffiliateBase(BaseSchema):
    referral_code: str
    commission_rate: float


class CreateAffiliateRequest(BaseModel):
    payout_address: str | None = Field(None, max_length=255)
    payout_currency: str = Field(default="USDT", max_length=10)


class UpdateAffiliateRequest(BaseModel):
    payout_address: str | None = Field(None, max_length=255)
    payout_currency: str | None = Field(None, max_length=10)


class AffiliateResponse(AffiliateBase, TimestampMixin):
    id: str
    user_id: str
    total_referrals: int
    active_referrals: int
    total_earnings: float
    pending_earnings: float
    paid_earnings: float
    payout_address: str | None = None
    payout_currency: str
    is_active: bool
    is_verified: bool


class AffiliateReferralResponse(BaseSchema):
    id: str
    referred_user_email: str
    referred_user_full_name: str | None = None
    referred_user_has_active_subscription: bool = False
    is_converted: bool
    converted_at: datetime | None = None
    created_at: datetime


class AffiliateCommissionResponse(BaseSchema):
    id: str
    amount: float
    commission_rate: float
    original_amount: float
    status: str
    source: str
    is_paid: bool
    paid_at: datetime | None = None
    created_at: datetime


class RequestPayoutRequest(BaseModel):
    amount: float | None = Field(None, gt=0)


class AffiliatePayoutResponse(BaseSchema, TimestampMixin):
    id: str
    affiliate_id: str
    amount: float
    currency: str
    address: str
    status: PayoutStatus
    transaction_hash: str | None = None
    error_message: str | None = None
    processed_at: datetime | None = None


class AffiliateDashboardResponse(BaseSchema):
    affiliate: AffiliateResponse
    recent_referrals: list[AffiliateReferralResponse]
    recent_commissions: list[AffiliateCommissionResponse]
    pending_payouts: list[AffiliatePayoutResponse]
    stats: "AffiliateStatsResponse"


class AffiliateStatsResponse(BaseSchema):
    total_clicks: int
    total_signups: int
    total_conversions: int
    conversion_rate: float
    earnings_this_month: float
    earnings_last_month: float
    lifetime_earnings: float


class AffiliateLeaderboardEntry(BaseSchema):
    rank: int
    referral_code: str
    total_referrals: int
    total_earnings: float
