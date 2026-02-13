from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, Pagination
from app.schemas import (
    AffiliateCommissionResponse,
    AffiliateDashboardResponse,
    AffiliatePayoutResponse,
    AffiliateReferralResponse,
    AffiliateResponse,
    AffiliateStatsResponse,
    CreateAffiliateRequest,
    PaginatedResponse,
    RequestPayoutRequest,
    UpdateAffiliateRequest,
)
from app.services import AffiliateService

router = APIRouter(prefix="/affiliate", tags=["Affiliate"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=AffiliateResponse)
async def get_affiliate_status(
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    return affiliate


@router.post("", response_model=AffiliateResponse)
async def become_affiliate(
    data: CreateAffiliateRequest,
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.create_affiliate(current_user)

    if data.payout_address:
        affiliate = await affiliate_service.update_affiliate(
            affiliate,
            payout_address=data.payout_address,
            payout_currency=data.payout_currency,
        )

    await db.commit()
    return affiliate


@router.get("/dashboard", response_model=AffiliateDashboardResponse)
async def get_affiliate_dashboard(
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    from app.schemas.common import PaginationParams

    pagination = PaginationParams(page=1, page_size=5)

    referrals, _ = await affiliate_service.get_referrals(affiliate, pagination)
    commissions, _ = await affiliate_service.get_commissions(affiliate, pagination)
    payouts, _ = await affiliate_service.get_payouts(affiliate, pagination)
    stats = await affiliate_service.get_affiliate_stats(affiliate)

    return AffiliateDashboardResponse(
        affiliate=AffiliateResponse.model_validate(affiliate),
        recent_referrals=[
            AffiliateReferralResponse(
                id=r.id,
                referred_user_email=r.referred_user.email if r.referred_user else "Unknown",
                referred_user_full_name=r.referred_user.full_name if r.referred_user else None,
                referred_user_has_active_subscription=r.referred_user.has_active_subscription if r.referred_user else False,
                is_converted=r.is_converted,
                converted_at=r.converted_at,
                created_at=r.created_at,
            )
            for r in referrals
        ],
        recent_commissions=[
            AffiliateCommissionResponse(
                id=c.id,
                amount=float(c.amount),
                commission_rate=float(c.commission_rate),
                original_amount=float(c.original_amount),
                status="paid" if c.is_paid else "pending",
                source="Initial referral" if float(c.commission_rate) >= 20 else "Renewal",
                is_paid=c.is_paid,
                paid_at=c.paid_at,
                created_at=c.created_at,
            )
            for c in commissions
        ],
        pending_payouts=[AffiliatePayoutResponse.model_validate(p) for p in payouts],
        stats=stats,
    )


@router.patch("", response_model=AffiliateResponse)
async def update_affiliate(
    data: UpdateAffiliateRequest,
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    affiliate = await affiliate_service.update_affiliate(
        affiliate,
        payout_address=data.payout_address,
        payout_currency=data.payout_currency,
    )
    await db.commit()
    return affiliate


@router.get("/stats", response_model=AffiliateStatsResponse)
async def get_affiliate_stats(
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    stats = await affiliate_service.get_affiliate_stats(affiliate)
    return stats


@router.get("/referrals", response_model=PaginatedResponse[AffiliateReferralResponse])
async def get_referrals(
    pagination: Pagination,
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    referrals, total = await affiliate_service.get_referrals(affiliate, pagination)

    return PaginatedResponse.create(
        items=[
            AffiliateReferralResponse(
                id=r.id,
                referred_user_email=r.referred_user.email if r.referred_user else "Unknown",
                referred_user_full_name=r.referred_user.full_name if r.referred_user else None,
                referred_user_has_active_subscription=r.referred_user.has_active_subscription if r.referred_user else False,
                is_converted=r.is_converted,
                converted_at=r.converted_at,
                created_at=r.created_at,
            )
            for r in referrals
        ],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/commissions", response_model=PaginatedResponse[AffiliateCommissionResponse])
async def get_commissions(
    pagination: Pagination,
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    commissions, total = await affiliate_service.get_commissions(affiliate, pagination)

    return PaginatedResponse.create(
        items=[
            AffiliateCommissionResponse(
                id=c.id,
                amount=float(c.amount),
                commission_rate=float(c.commission_rate),
                original_amount=float(c.original_amount),
                status="paid" if c.is_paid else "pending",
                source="Initial referral" if float(c.commission_rate) >= 20 else "Renewal",
                is_paid=c.is_paid,
                paid_at=c.paid_at,
                created_at=c.created_at,
            )
            for c in commissions
        ],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/payouts", response_model=PaginatedResponse[AffiliatePayoutResponse])
async def get_payouts(
    pagination: Pagination,
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    payouts, total = await affiliate_service.get_payouts(affiliate, pagination)

    return PaginatedResponse.create(
        items=[AffiliatePayoutResponse.model_validate(p) for p in payouts],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/payout", response_model=AffiliatePayoutResponse)
async def request_payout(
    data: RequestPayoutRequest,
    current_user: CurrentUser,
    db: DB,
):
    affiliate_service = AffiliateService(db)
    affiliate = await affiliate_service.get_affiliate_by_user(current_user.id)

    if not affiliate:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Affiliate account")

    payout = await affiliate_service.request_payout(affiliate, data.amount)
    await db.commit()
    return payout
