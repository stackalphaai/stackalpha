import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.exceptions import BadRequestError
from app.models import (
    Affiliate,
    AffiliateCommission,
    AffiliatePayout,
    AffiliateReferral,
    Payment,
    PayoutStatus,
    User,
)
from app.schemas.affiliate import AffiliateStatsResponse
from app.schemas.common import PaginationParams

logger = logging.getLogger(__name__)


class AffiliateService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.initial_commission_rate = settings.affiliate_initial_commission_percent
        self.renewal_commission_rate = settings.affiliate_renewal_commission_percent
        self.payout_minimum = settings.affiliate_payout_minimum

    async def create_affiliate(self, user: User) -> Affiliate:
        result = await self.db.execute(select(Affiliate).where(Affiliate.user_id == user.id))
        existing = result.scalar_one_or_none()

        if existing:
            raise BadRequestError("User is already an affiliate")

        referral_code = await self._generate_unique_code()

        affiliate = Affiliate(
            user_id=user.id,
            referral_code=referral_code,
            commission_rate=self.initial_commission_rate,
            is_active=True,
            is_verified=False,
        )

        self.db.add(affiliate)
        await self.db.flush()
        await self.db.refresh(affiliate)

        return affiliate

    async def get_affiliate_by_user(self, user_id: str) -> Affiliate | None:
        result = await self.db.execute(
            select(Affiliate)
            .options(
                selectinload(Affiliate.referrals),
                selectinload(Affiliate.commissions),
            )
            .where(Affiliate.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_affiliate_by_code(self, code: str) -> Affiliate | None:
        result = await self.db.execute(
            select(Affiliate).where(Affiliate.referral_code == code.upper())
        )
        return result.scalar_one_or_none()

    async def track_referral(
        self,
        affiliate: Affiliate,
        referred_user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AffiliateReferral:
        result = await self.db.execute(
            select(AffiliateReferral).where(AffiliateReferral.referred_user_id == referred_user.id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            return existing

        referral = AffiliateReferral(
            affiliate_id=affiliate.id,
            referred_user_id=referred_user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            is_converted=False,
        )

        self.db.add(referral)
        affiliate.total_referrals += 1

        await self.db.flush()
        return referral

    async def process_commission(
        self,
        payment: Payment,
    ) -> AffiliateCommission | None:
        result = await self.db.execute(
            select(AffiliateReferral)
            .options(selectinload(AffiliateReferral.affiliate))
            .join(User, AffiliateReferral.referred_user_id == User.id)
            .join(
                Payment.__table__.join(
                    AffiliateReferral.__table__,
                    Payment.subscription.has(user_id=AffiliateReferral.referred_user_id),
                )
            )
            .where(Payment.id == payment.id)
        )

        result = await self.db.execute(
            select(AffiliateReferral)
            .options(selectinload(AffiliateReferral.affiliate))
            .where(AffiliateReferral.referred_user_id == payment.subscription.user_id)
        )
        referral = result.scalar_one_or_none()

        if not referral:
            return None

        affiliate = referral.affiliate

        if not affiliate or not affiliate.is_active:
            return None

        # Determine commission rate: 20% for initial, 5% for renewals
        is_initial_payment = not referral.is_converted
        commission_rate = (
            self.initial_commission_rate if is_initial_payment else self.renewal_commission_rate
        )

        commission_amount = float(payment.amount_usd) * (commission_rate / 100)

        commission = AffiliateCommission(
            affiliate_id=affiliate.id,
            referral_id=referral.id,
            payment_id=payment.id,
            amount=commission_amount,
            commission_rate=commission_rate,
            original_amount=float(payment.amount_usd),
            is_paid=False,
        )

        self.db.add(commission)

        affiliate.total_earnings = float(affiliate.total_earnings) + commission_amount
        affiliate.pending_earnings = float(affiliate.pending_earnings) + commission_amount

        if is_initial_payment:
            referral.is_converted = True
            referral.converted_at = datetime.now(UTC)
            affiliate.active_referrals += 1

        await self.db.flush()

        commission_type = "initial" if is_initial_payment else "renewal"
        logger.info(
            f"Commission created ({commission_type}): ${commission_amount:.2f} "
            f"({commission_rate}%) for affiliate {affiliate.referral_code}"
        )

        return commission

    async def request_payout(
        self,
        affiliate: Affiliate,
        amount: float | None = None,
    ) -> AffiliatePayout:
        if not affiliate.payout_address:
            raise BadRequestError("Payout address not configured")

        payout_amount = amount or float(affiliate.pending_earnings)

        if payout_amount < self.payout_minimum:
            raise BadRequestError(f"Minimum payout amount is ${self.payout_minimum}")

        if payout_amount > float(affiliate.pending_earnings):
            raise BadRequestError("Insufficient pending earnings")

        payout = AffiliatePayout(
            affiliate_id=affiliate.id,
            amount=payout_amount,
            currency=affiliate.payout_currency,
            address=affiliate.payout_address,
            status=PayoutStatus.PENDING,
        )

        self.db.add(payout)

        affiliate.pending_earnings = float(affiliate.pending_earnings) - payout_amount

        await self.db.flush()
        return payout

    async def process_payout(
        self,
        payout: AffiliatePayout,
        transaction_hash: str | None = None,
        error_message: str | None = None,
    ) -> AffiliatePayout:
        if payout.status != PayoutStatus.PENDING:
            raise BadRequestError("Payout is not pending")

        if transaction_hash:
            payout.status = PayoutStatus.COMPLETED
            payout.transaction_hash = transaction_hash
            payout.processed_at = datetime.now(UTC)

            result = await self.db.execute(
                select(Affiliate).where(Affiliate.id == payout.affiliate_id)
            )
            affiliate = result.scalar_one_or_none()
            if affiliate:
                affiliate.paid_earnings = float(affiliate.paid_earnings) + float(payout.amount)
        else:
            payout.status = PayoutStatus.FAILED
            payout.error_message = error_message

            result = await self.db.execute(
                select(Affiliate).where(Affiliate.id == payout.affiliate_id)
            )
            affiliate = result.scalar_one_or_none()
            if affiliate:
                affiliate.pending_earnings = float(affiliate.pending_earnings) + float(
                    payout.amount
                )

        return payout

    async def get_affiliate_stats(self, affiliate: Affiliate) -> AffiliateStatsResponse:
        now = datetime.now(UTC)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_of_last_month = (start_of_month - timedelta(days=1)).replace(day=1)

        result = await self.db.execute(
            select(func.sum(AffiliateCommission.amount)).where(
                AffiliateCommission.affiliate_id == affiliate.id,
                AffiliateCommission.created_at >= start_of_month,
            )
        )
        this_month = result.scalar() or 0

        result = await self.db.execute(
            select(func.sum(AffiliateCommission.amount)).where(
                AffiliateCommission.affiliate_id == affiliate.id,
                AffiliateCommission.created_at >= start_of_last_month,
                AffiliateCommission.created_at < start_of_month,
            )
        )
        last_month = result.scalar() or 0

        conversion_rate = (
            (affiliate.active_referrals / affiliate.total_referrals * 100)
            if affiliate.total_referrals > 0
            else 0
        )

        return AffiliateStatsResponse(
            total_clicks=0,
            total_signups=affiliate.total_referrals,
            total_conversions=affiliate.active_referrals,
            conversion_rate=round(conversion_rate, 2),
            earnings_this_month=round(float(this_month), 2),
            earnings_last_month=round(float(last_month), 2),
            lifetime_earnings=round(float(affiliate.total_earnings), 2),
        )

    async def get_referrals(
        self,
        affiliate: Affiliate,
        pagination: PaginationParams,
    ) -> tuple[list[AffiliateReferral], int]:
        query = (
            select(AffiliateReferral)
            .options(
                selectinload(AffiliateReferral.referred_user).selectinload(User.subscriptions)
            )
            .where(AffiliateReferral.affiliate_id == affiliate.id)
        )

        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar() or 0

        query = query.order_by(AffiliateReferral.created_at.desc())
        query = query.offset(pagination.offset).limit(pagination.limit)

        result = await self.db.execute(query)
        referrals = list(result.scalars().all())

        return referrals, total

    async def get_commissions(
        self,
        affiliate: Affiliate,
        pagination: PaginationParams,
    ) -> tuple[list[AffiliateCommission], int]:
        query = select(AffiliateCommission).where(AffiliateCommission.affiliate_id == affiliate.id)

        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar() or 0

        query = query.order_by(AffiliateCommission.created_at.desc())
        query = query.offset(pagination.offset).limit(pagination.limit)

        result = await self.db.execute(query)
        commissions = list(result.scalars().all())

        return commissions, total

    async def get_payouts(
        self,
        affiliate: Affiliate,
        pagination: PaginationParams,
    ) -> tuple[list[AffiliatePayout], int]:
        query = select(AffiliatePayout).where(AffiliatePayout.affiliate_id == affiliate.id)

        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar() or 0

        query = query.order_by(AffiliatePayout.created_at.desc())
        query = query.offset(pagination.offset).limit(pagination.limit)

        result = await self.db.execute(query)
        payouts = list(result.scalars().all())

        return payouts, total

    async def update_affiliate(
        self,
        affiliate: Affiliate,
        payout_address: str | None = None,
        payout_currency: str | None = None,
    ) -> Affiliate:
        if payout_address is not None:
            affiliate.payout_address = payout_address
        if payout_currency is not None:
            affiliate.payout_currency = payout_currency

        return affiliate

    async def _generate_unique_code(self) -> str:
        while True:
            code = secrets.token_hex(4).upper()
            result = await self.db.execute(select(Affiliate).where(Affiliate.referral_code == code))
            if not result.scalar_one_or_none():
                return code
