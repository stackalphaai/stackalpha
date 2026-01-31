from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import AdminUser, Pagination, SuperAdminUser
from app.models import (
    AffiliatePayout,
    Payment,
    PaymentStatus,
    PayoutStatus,
    Signal,
    Subscription,
    SubscriptionStatus,
    Trade,
    TradeStatus,
    User,
)
from app.schemas import (
    AffiliatePayoutResponse,
    PaginatedResponse,
    SignalResponse,
    SubscriptionResponse,
    SuccessResponse,
    TradeResponse,
    UserListResponse,
)
from app.services import UserService
from app.services.affiliate_service import AffiliateService
from app.services.trading import SignalService

router = APIRouter(prefix="/admin", tags=["Admin"])

# Type aliases for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    total_subscribers: int
    active_subscribers: int
    total_trades: int
    open_trades: int
    total_signals: int
    active_signals: int
    total_revenue: float
    pending_payouts: float


class SystemHealthResponse(BaseModel):
    status: str
    database: str
    redis: str
    hyperliquid: str
    timestamp: datetime


@router.get("/dashboard", response_model=DashboardStats)
async def get_admin_dashboard(
    current_user: AdminUser,
    db: DB,
):
    total_users = await db.scalar(select(func.count(User.id)))
    active_users = await db.scalar(select(func.count(User.id)).where(User.is_active.is_(True)))

    total_subs = await db.scalar(select(func.count(Subscription.id)))
    active_subs = await db.scalar(
        select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE)
    )

    total_trades = await db.scalar(select(func.count(Trade.id)))
    open_trades = await db.scalar(
        select(func.count(Trade.id)).where(Trade.status == TradeStatus.OPEN)
    )

    total_signals = await db.scalar(select(func.count(Signal.id)))
    active_signals = await db.scalar(
        select(func.count(Signal.id)).where(Signal.status.in_(["pending", "active"]))
    )

    total_revenue = (
        await db.scalar(
            select(func.sum(Payment.amount_usd)).where(Payment.status == PaymentStatus.FINISHED)
        )
        or 0
    )

    pending_payouts = (
        await db.scalar(
            select(func.sum(AffiliatePayout.amount)).where(
                AffiliatePayout.status == PayoutStatus.PENDING
            )
        )
        or 0
    )

    return DashboardStats(
        total_users=total_users or 0,
        active_users=active_users or 0,
        total_subscribers=total_subs or 0,
        active_subscribers=active_subs or 0,
        total_trades=total_trades or 0,
        open_trades=open_trades or 0,
        total_signals=total_signals or 0,
        active_signals=active_signals or 0,
        total_revenue=float(total_revenue),
        pending_payouts=float(pending_payouts),
    )


@router.get("/health", response_model=SystemHealthResponse)
async def check_system_health(
    current_user: AdminUser,
    db: DB,
):
    db_status = "healthy"
    try:
        await db.execute(select(1))
    except Exception:
        db_status = "unhealthy"

    redis_status = "healthy"
    try:
        from redis import asyncio as aioredis

        redis = await aioredis.from_url(settings.redis_url)
        await redis.ping()
        await redis.close()
    except Exception:
        redis_status = "unhealthy"

    hl_status = "healthy"
    try:
        from app.services.hyperliquid import get_info_service

        info = get_info_service()
        await info.get_meta()
    except Exception:
        hl_status = "unhealthy"

    overall = (
        "healthy"
        if all(s == "healthy" for s in [db_status, redis_status, hl_status])
        else "degraded"
    )

    return SystemHealthResponse(
        status=overall,
        database=db_status,
        redis=redis_status,
        hyperliquid=hl_status,
        timestamp=datetime.now(UTC),
    )


@router.get("/users", response_model=PaginatedResponse[UserListResponse])
async def list_all_users(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    search: str | None = Query(None),
    is_active: bool | None = None,
):
    user_service = UserService(db)
    users, total = await user_service.get_users(
        pagination=pagination,
        search=search,
        is_active=is_active,
    )

    return PaginatedResponse.create(
        items=[UserListResponse.model_validate(u) for u in users],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/trades", response_model=PaginatedResponse[TradeResponse])
async def list_all_trades(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    status: TradeStatus | None = None,
):
    query = select(Trade)
    if status:
        query = query.where(Trade.status == status)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(Trade.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    trades = list(result.scalars().all())

    return PaginatedResponse.create(
        items=[TradeResponse.model_validate(t) for t in trades],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/signals", response_model=PaginatedResponse[SignalResponse])
async def list_all_signals(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
):
    signal_service = SignalService(db)
    signals, total = await signal_service.get_signals(pagination)

    return PaginatedResponse.create(
        items=[SignalResponse.model_validate(s) for s in signals],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/signals/generate")
async def trigger_signal_generation(
    symbol: str,
    current_user: AdminUser,
    db: DB,
):
    signal_service = SignalService(db)
    signal = await signal_service.generate_signal(symbol)
    await db.commit()

    if signal:
        return {"status": "success", "signal_id": signal.id}
    return {"status": "no_signal", "message": "No consensus reached"}


@router.get("/subscriptions", response_model=PaginatedResponse[SubscriptionResponse])
async def list_all_subscriptions(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    status: SubscriptionStatus | None = None,
):
    query = select(Subscription)
    if status:
        query = query.where(Subscription.status == status)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(Subscription.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    subscriptions = list(result.scalars().all())

    return PaginatedResponse.create(
        items=[SubscriptionResponse.model_validate(s) for s in subscriptions],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/payouts/pending", response_model=PaginatedResponse[AffiliatePayoutResponse])
async def list_pending_payouts(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
):
    query = select(AffiliatePayout).where(AffiliatePayout.status == PayoutStatus.PENDING)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(AffiliatePayout.created_at.asc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    payouts = list(result.scalars().all())

    return PaginatedResponse.create(
        items=[AffiliatePayoutResponse.model_validate(p) for p in payouts],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/payouts/{payout_id}/process", response_model=AffiliatePayoutResponse)
async def process_payout(
    payout_id: str,
    current_user: SuperAdminUser,
    db: DB,
    transaction_hash: str | None = None,
    error_message: str | None = None,
):
    result = await db.execute(select(AffiliatePayout).where(AffiliatePayout.id == payout_id))
    payout = result.scalar_one_or_none()

    if not payout:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Payout")

    affiliate_service = AffiliateService(db)
    payout = await affiliate_service.process_payout(
        payout,
        transaction_hash=transaction_hash,
        error_message=error_message,
    )
    await db.commit()

    return payout


@router.post("/broadcast", response_model=SuccessResponse)
async def broadcast_message(
    message: str,
    current_user: SuperAdminUser,
    db: DB,
):
    from app.services import TelegramService

    telegram_service = TelegramService(db)
    sent_count = await telegram_service.broadcast_message(message)

    return SuccessResponse(message=f"Message sent to {sent_count} users")
