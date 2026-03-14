import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import AdminUser, Pagination, SuperAdminUser
from app.models import (
    AffiliatePayout,
    ExchangeConnection,
    Payment,
    PaymentStatus,
    PayoutStatus,
    Signal,
    SignalStatus,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    SystemConfig,
    Trade,
    TradeCloseReason,
    TradeStatus,
    User,
    Wallet,
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

DB = Annotated[AsyncSession, Depends(get_db)]


# ============================================================================
# Schemas
# ============================================================================


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
    binance: str
    openrouter: str
    timestamp: datetime


class ConfigItem(BaseModel):
    key: str
    value: Any
    description: str | None = None
    category: str = "general"


class ConfigUpdateRequest(BaseModel):
    configs: list[ConfigItem]


class UserActionRequest(BaseModel):
    reason: str | None = None


class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_verified: bool | None = None
    is_subscribed: bool | None = None
    is_admin: bool | None = None
    is_superadmin: bool | None = None


class GrantSubscriptionRequest(BaseModel):
    plan: str = "monthly"
    duration_days: int = 30


class TriggerTaskRequest(BaseModel):
    task_name: str
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class ForceCloseTradeRequest(BaseModel):
    close_reason: str = "system"


class BroadcastRequest(BaseModel):
    message: str
    channel: str = "telegram"


class UserDetailResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    is_active: bool
    is_verified: bool
    is_subscribed: bool
    is_admin: bool
    is_superadmin: bool
    is_2fa_enabled: bool
    login_count: int
    last_login: datetime | None
    created_at: datetime
    wallet_count: int = 0
    trade_count: int = 0
    exchange_connection_count: int = 0
    has_telegram: bool = False


class CeleryTaskInfo(BaseModel):
    name: str
    schedule: str
    description: str


# ============================================================================
# Dashboard & Health
# ============================================================================


@router.get("/dashboard", response_model=DashboardStats)
async def get_admin_dashboard(current_user: AdminUser, db: DB):
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
async def check_system_health(current_user: AdminUser, db: DB):
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

    binance_status = "healthy"
    try:
        from app.services.binance import get_binance_info_service

        binance_info = get_binance_info_service()
        await binance_info.get_top_gainers(limit=1)
    except Exception:
        binance_status = "unhealthy"

    openrouter_status = "healthy"
    try:
        if not settings.openrouter_api_key:
            openrouter_status = "not_configured"
    except Exception:
        openrouter_status = "unhealthy"

    statuses = [db_status, redis_status, hl_status, binance_status]
    overall = "healthy" if all(s == "healthy" for s in statuses) else "degraded"

    return SystemHealthResponse(
        status=overall,
        database=db_status,
        redis=redis_status,
        hyperliquid=hl_status,
        binance=binance_status,
        openrouter=openrouter_status,
        timestamp=datetime.now(UTC),
    )


# ============================================================================
# Runtime Configuration
# ============================================================================

# Defines which settings keys are admin-configurable, with metadata
CONFIGURABLE_SETTINGS: dict[str, dict[str, Any]] = {
    # LLM / Signal Quality
    "llm_models": {
        "category": "signal_quality",
        "description": "LLM models used for consensus analysis (JSON array)",
        "type": "json",
    },
    "llm_consensus_threshold": {
        "category": "signal_quality",
        "description": "Min ratio of agreeing models (0.0-1.0)",
        "type": "float",
    },
    "llm_min_confidence": {
        "category": "signal_quality",
        "description": "Min average confidence score to generate signal",
        "type": "float",
    },
    "llm_min_agreeing_models": {
        "category": "signal_quality",
        "description": "Min number of models that must agree on direction",
        "type": "int",
    },
    "llm_min_risk_reward_ratio": {
        "category": "signal_quality",
        "description": "Min risk-reward ratio (e.g. 1.5 = 1.5:1)",
        "type": "float",
    },
    "llm_min_adx": {
        "category": "signal_quality",
        "description": "Min ADX for trend strength filter",
        "type": "float",
    },
    "llm_min_atr_ratio": {
        "category": "signal_quality",
        "description": "Min ATR/price ratio for volatility filter",
        "type": "float",
    },
    "llm_tp_min_pct": {
        "category": "signal_quality",
        "description": "Min take-profit % from entry (e.g. 0.008 = 0.8%)",
        "type": "float",
    },
    "llm_tp_max_pct": {
        "category": "signal_quality",
        "description": "Max take-profit % from entry (e.g. 0.03 = 3%)",
        "type": "float",
    },
    "llm_sl_min_pct": {
        "category": "signal_quality",
        "description": "Min stop-loss % from entry (e.g. 0.004 = 0.4%)",
        "type": "float",
    },
    "llm_sl_max_pct": {
        "category": "signal_quality",
        "description": "Max stop-loss % from entry (e.g. 0.02 = 2%)",
        "type": "float",
    },
    # Trading
    "max_position_size_percent": {
        "category": "trading",
        "description": "Max position size as % of portfolio",
        "type": "float",
    },
    "default_leverage": {
        "category": "trading",
        "description": "Default leverage for new trades",
        "type": "int",
    },
    "max_leverage": {
        "category": "trading",
        "description": "Maximum allowed leverage",
        "type": "int",
    },
    "max_concurrent_positions": {
        "category": "trading",
        "description": "Max number of open positions per user",
        "type": "int",
    },
    "analysis_interval_hours": {
        "category": "trading",
        "description": "Hours between market analysis runs",
        "type": "int",
    },
    # Binance
    "binance_min_volume_usd": {
        "category": "binance",
        "description": "Min 24h volume for Binance signal candidates",
        "type": "float",
    },
    "binance_top_movers_limit": {
        "category": "binance",
        "description": "Number of top gainers to analyze",
        "type": "int",
    },
    "binance_default_leverage": {
        "category": "binance",
        "description": "Default leverage for Binance trades",
        "type": "int",
    },
    "binance_max_leverage": {
        "category": "binance",
        "description": "Max leverage for Binance trades",
        "type": "int",
    },
    # Hyperliquid
    "hyperliquid_use_testnet": {
        "category": "hyperliquid",
        "description": "Use Hyperliquid testnet instead of mainnet",
        "type": "bool",
    },
    # Subscription
    "subscription_monthly_price": {
        "category": "subscription",
        "description": "Monthly subscription price in USD",
        "type": "float",
    },
    "subscription_yearly_price": {
        "category": "subscription",
        "description": "Yearly subscription price in USD",
        "type": "float",
    },
    "subscription_grace_period_days": {
        "category": "subscription",
        "description": "Grace period days after subscription expires",
        "type": "int",
    },
    # Affiliate
    "affiliate_initial_commission_percent": {
        "category": "affiliate",
        "description": "Commission % for first-time referral conversions",
        "type": "float",
    },
    "affiliate_renewal_commission_percent": {
        "category": "affiliate",
        "description": "Commission % for renewal conversions",
        "type": "float",
    },
    "affiliate_payout_minimum": {
        "category": "affiliate",
        "description": "Minimum payout amount in USD",
        "type": "float",
    },
    "affiliate_cookie_days": {
        "category": "affiliate",
        "description": "Referral cookie duration in days",
        "type": "int",
    },
    # Rate Limiting
    "rate_limit_requests": {
        "category": "rate_limiting",
        "description": "Max API requests per window",
        "type": "int",
    },
    "rate_limit_window_seconds": {
        "category": "rate_limiting",
        "description": "Rate limit window in seconds",
        "type": "int",
    },
    # OpenRouter
    "openrouter_base_url": {
        "category": "openrouter",
        "description": "OpenRouter API base URL",
        "type": "str",
    },
}


def _serialize_value(value: Any) -> str:
    """Serialize a config value to string for DB storage."""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def _deserialize_value(raw: str, type_hint: str) -> Any:
    """Deserialize a config value from DB string."""
    if type_hint == "json":
        return json.loads(raw)
    elif type_hint == "float":
        return float(raw)
    elif type_hint == "int":
        return int(raw)
    elif type_hint == "bool":
        return raw.lower() in ("true", "1", "yes")
    return raw


@router.get("/config")
async def get_all_config(current_user: AdminUser, db: DB) -> dict[str, Any]:
    """Get all configurable settings with current values."""
    # Load DB overrides
    result = await db.execute(select(SystemConfig))
    db_configs = {row.key: row.value for row in result.scalars().all()}

    configs = {}
    for key, meta in CONFIGURABLE_SETTINGS.items():
        # DB override takes priority, else use current settings value
        if key in db_configs:
            value = _deserialize_value(db_configs[key], meta["type"])
        else:
            value = getattr(settings, key, None)

        configs[key] = {
            "value": value,
            "description": meta["description"],
            "category": meta["category"],
            "type": meta["type"],
            "has_override": key in db_configs,
        }

    return {"configs": configs}


@router.put("/config")
async def update_config(
    body: ConfigUpdateRequest,
    current_user: SuperAdminUser,
    db: DB,
) -> dict[str, Any]:
    """Update runtime configuration. Changes take effect immediately for the API
    process. Celery workers pick up changes on next task execution after restart."""
    updated = []

    for item in body.configs:
        if item.key not in CONFIGURABLE_SETTINGS:
            continue

        meta = CONFIGURABLE_SETTINGS[item.key]
        serialized = _serialize_value(item.value)

        # Upsert into system_config table
        existing = await db.execute(select(SystemConfig).where(SystemConfig.key == item.key))
        config_row = existing.scalar_one_or_none()

        if config_row:
            config_row.value = serialized
            config_row.description = item.description or meta["description"]
            config_row.category = meta["category"]
        else:
            config_row = SystemConfig(
                key=item.key,
                value=serialized,
                description=item.description or meta["description"],
                category=meta["category"],
            )
            db.add(config_row)

        # Apply to in-memory settings immediately
        deserialized = _deserialize_value(serialized, meta["type"])
        try:
            setattr(settings, item.key, deserialized)
            updated.append(item.key)
        except Exception as e:
            logger.warning(f"Could not apply config {item.key} in-memory: {e}")

    await db.commit()

    logger.info(f"Admin {current_user.email} updated config: {updated}")
    return {"updated": updated, "message": f"Updated {len(updated)} settings"}


@router.delete("/config/{key}")
async def reset_config(
    key: str,
    current_user: SuperAdminUser,
    db: DB,
) -> dict[str, str]:
    """Reset a config key to its default (.env) value by removing the DB override."""
    await db.execute(delete(SystemConfig).where(SystemConfig.key == key))
    await db.commit()

    # Reload from env - we can't easily reset a single value, but we note it
    return {"message": f"Override for '{key}' removed. Restart services to apply default."}


# ============================================================================
# User Management
# ============================================================================


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
        pagination=pagination, search=search, is_active=is_active
    )
    return PaginatedResponse.create(
        items=[UserListResponse.model_validate(u) for u in users],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/users/{user_id}")
async def get_user_detail(user_id: str, current_user: AdminUser, db: DB) -> UserDetailResponse:
    """Get detailed user info including counts."""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.wallets),
            selectinload(User.trades),
            selectinload(User.exchange_connections),
            selectinload(User.telegram_connection),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    return UserDetailResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        is_subscribed=user.is_subscribed,
        is_admin=user.is_admin,
        is_superadmin=user.is_superadmin,
        is_2fa_enabled=user.is_2fa_enabled,
        login_count=user.login_count,
        last_login=user.last_login,
        created_at=user.created_at,
        wallet_count=len(user.wallets) if user.wallets else 0,
        trade_count=len(user.trades) if user.trades else 0,
        exchange_connection_count=len(user.exchange_connections)
        if user.exchange_connections
        else 0,
        has_telegram=user.telegram_connection is not None and user.telegram_connection.is_verified,
    )


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    current_user: SuperAdminUser,
    db: DB,
) -> UserDetailResponse:
    """Update user fields directly."""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.wallets),
            selectinload(User.trades),
            selectinload(User.exchange_connections),
            selectinload(User.telegram_connection),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    # Prevent modifying own superadmin status
    if user.id == current_user.id and body.is_superadmin is False:
        from app.core.exceptions import AuthorizationError

        raise AuthorizationError("Cannot remove your own superadmin status")

    changes = []
    for field in [
        "full_name",
        "is_active",
        "is_verified",
        "is_subscribed",
        "is_admin",
        "is_superadmin",
    ]:
        new_val = getattr(body, field, None)
        if new_val is not None:
            old_val = getattr(user, field)
            if old_val != new_val:
                setattr(user, field, new_val)
                changes.append(f"{field}: {old_val} -> {new_val}")

    await db.commit()

    if changes:
        logger.info(f"Admin {current_user.email} updated user {user.email}: {', '.join(changes)}")

    return UserDetailResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        is_subscribed=user.is_subscribed,
        is_admin=user.is_admin,
        is_superadmin=user.is_superadmin,
        is_2fa_enabled=user.is_2fa_enabled,
        login_count=user.login_count,
        last_login=user.last_login,
        created_at=user.created_at,
        wallet_count=len(user.wallets) if user.wallets else 0,
        trade_count=len(user.trades) if user.trades else 0,
        exchange_connection_count=len(user.exchange_connections)
        if user.exchange_connections
        else 0,
        has_telegram=user.telegram_connection is not None and user.telegram_connection.is_verified,
    )


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(user_id: str, current_user: SuperAdminUser, db: DB) -> SuccessResponse:
    """Ban or unban a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    user.is_active = not user.is_active
    await db.commit()

    action = "activated" if user.is_active else "deactivated"
    logger.info(f"Admin {current_user.email} {action} user {user.email}")
    return SuccessResponse(message=f"User {action}")


@router.post("/users/{user_id}/toggle-admin")
async def toggle_user_admin(user_id: str, current_user: SuperAdminUser, db: DB) -> SuccessResponse:
    """Grant or revoke admin access."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    if user.is_superadmin:
        from app.core.exceptions import AuthorizationError

        raise AuthorizationError("Cannot modify superadmin status")

    user.is_admin = not user.is_admin
    await db.commit()

    action = "granted admin" if user.is_admin else "revoked admin"
    logger.info(f"Admin {current_user.email} {action} for {user.email}")
    return SuccessResponse(message=f"Admin access {action}")


@router.post("/users/{user_id}/grant-subscription")
async def grant_subscription(
    user_id: str,
    body: GrantSubscriptionRequest,
    current_user: SuperAdminUser,
    db: DB,
) -> SuccessResponse:
    """Grant a free subscription to a user."""
    from datetime import timedelta

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    now = datetime.now(UTC)
    plan = SubscriptionPlan.MONTHLY if body.plan == "monthly" else SubscriptionPlan.YEARLY

    sub = Subscription(
        user_id=user.id,
        plan=plan,
        status=SubscriptionStatus.ACTIVE,
        price_usd=0,
        starts_at=now,
        expires_at=now + timedelta(days=body.duration_days),
    )
    db.add(sub)
    user.is_subscribed = True
    await db.commit()

    logger.info(
        f"Admin {current_user.email} granted {body.duration_days}d subscription to {user.email}"
    )
    return SuccessResponse(message=f"Granted {body.duration_days}-day subscription")


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: str, current_user: SuperAdminUser, db: DB
) -> dict[str, str]:
    """Generate a password reset link for a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    from app.core.security import create_token

    reset_token = create_token({"sub": user.id, "type": "password_reset"}, expires_minutes=60)
    user.password_reset_token = reset_token
    user.password_reset_expires = datetime.now(UTC)
    await db.commit()

    logger.info(f"Admin {current_user.email} initiated password reset for {user.email}")
    return {"message": "Password reset token generated", "reset_token": reset_token}


# ============================================================================
# Signals Management
# ============================================================================


@router.get("/signals", response_model=PaginatedResponse[SignalResponse])
async def list_all_signals(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    exchange: str | None = Query(None),
    status: str | None = Query(None),
):
    query = select(Signal)
    if exchange:
        query = query.where(Signal.exchange == exchange)
    if status:
        query = query.where(Signal.status == status)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(Signal.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    signals = list(result.scalars().all())

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
    exchange: str = Query("hyperliquid"),
):
    signal_service = SignalService(db)
    signal = await signal_service.generate_signal(symbol, exchange=exchange)
    await db.commit()

    if signal:
        return {"status": "success", "signal_id": signal.id}
    return {"status": "no_signal", "message": "No consensus reached"}


@router.post("/signals/{signal_id}/invalidate")
async def invalidate_signal(signal_id: str, current_user: AdminUser, db: DB) -> SuccessResponse:
    """Manually invalidate/expire a signal."""
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Signal")

    signal.status = SignalStatus.EXPIRED
    signal.closed_at = datetime.now(UTC)
    await db.commit()

    logger.info(f"Admin {current_user.email} invalidated signal {signal_id}")
    return SuccessResponse(message="Signal invalidated")


# ============================================================================
# Trades Management
# ============================================================================


@router.get("/trades", response_model=PaginatedResponse[TradeResponse])
async def list_all_trades(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    status: TradeStatus | None = None,
    exchange: str | None = Query(None),
):
    query = select(Trade)
    if status:
        query = query.where(Trade.status == status)
    if exchange:
        query = query.where(Trade.exchange == exchange)

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


@router.post("/trades/{trade_id}/force-close")
async def force_close_trade(
    trade_id: str,
    body: ForceCloseTradeRequest,
    current_user: SuperAdminUser,
    db: DB,
) -> SuccessResponse:
    """Force close an open trade."""
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Trade")

    if trade.status not in (TradeStatus.OPEN, TradeStatus.OPENING):
        return SuccessResponse(message=f"Trade already in status: {trade.status.value}")

    trade.status = TradeStatus.CLOSED
    trade.close_reason = TradeCloseReason.SYSTEM
    trade.closed_at = datetime.now(UTC)
    trade.error_message = f"Force closed by admin {current_user.email}: {body.close_reason}"
    await db.commit()

    logger.info(f"Admin {current_user.email} force-closed trade {trade_id}")
    return SuccessResponse(message="Trade force-closed")


# ============================================================================
# Subscriptions
# ============================================================================


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


@router.post("/subscriptions/{sub_id}/cancel")
async def cancel_subscription(sub_id: str, current_user: SuperAdminUser, db: DB) -> SuccessResponse:
    """Admin-cancel a subscription."""
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Subscription")

    sub.status = SubscriptionStatus.CANCELLED
    # Also update user's is_subscribed flag
    await db.execute(update(User).where(User.id == sub.user_id).values(is_subscribed=False))
    await db.commit()

    logger.info(f"Admin {current_user.email} cancelled subscription {sub_id}")
    return SuccessResponse(message="Subscription cancelled")


# ============================================================================
# Payouts
# ============================================================================


@router.get("/payouts/pending", response_model=PaginatedResponse[AffiliatePayoutResponse])
async def list_pending_payouts(pagination: Pagination, current_user: AdminUser, db: DB):
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
        payout, transaction_hash=transaction_hash, error_message=error_message
    )
    await db.commit()
    return payout


# ============================================================================
# Celery Task Management
# ============================================================================

AVAILABLE_TASKS = [
    CeleryTaskInfo(
        name="app.workers.tasks.analysis.analyze_all_markets",
        schedule="Every 2 hours",
        description="Analyze Hyperliquid markets and generate signals",
    ),
    CeleryTaskInfo(
        name="app.workers.tasks.analysis.analyze_binance_markets",
        schedule="Every 2 hours",
        description="Analyze Binance Futures top gainers and generate signals",
    ),
    CeleryTaskInfo(
        name="app.workers.tasks.trading.sync_all_positions",
        schedule="Every 1 minute",
        description="Sync Hyperliquid wallet positions",
    ),
    CeleryTaskInfo(
        name="app.workers.tasks.trading.sync_binance_positions",
        schedule="Every 1 minute",
        description="Sync Binance exchange connection balances",
    ),
    CeleryTaskInfo(
        name="app.workers.tasks.trading.monitor_binance_tpsl",
        schedule="Every 30 seconds",
        description="Monitor Binance trades for TP/SL hits",
    ),
    CeleryTaskInfo(
        name="app.workers.tasks.maintenance.check_subscriptions",
        schedule="Daily",
        description="Check and expire ended subscriptions",
    ),
    CeleryTaskInfo(
        name="app.workers.tasks.maintenance.expire_old_signals",
        schedule="Hourly",
        description="Expire signals older than their TTL",
    ),
]


@router.get("/tasks")
async def list_available_tasks(current_user: AdminUser) -> list[CeleryTaskInfo]:
    """List all available Celery tasks."""
    return AVAILABLE_TASKS


@router.post("/tasks/trigger")
async def trigger_task(
    body: TriggerTaskRequest,
    current_user: SuperAdminUser,
) -> dict[str, str]:
    """Manually trigger a Celery task."""
    allowed_tasks = {t.name for t in AVAILABLE_TASKS}
    if body.task_name not in allowed_tasks:
        from app.core.exceptions import ValidationError

        raise ValidationError(f"Task '{body.task_name}' is not in the allowed list")

    from app.workers.celery_app import celery_app

    result = celery_app.send_task(
        body.task_name,
        args=body.args or [],
        kwargs=body.kwargs or {},
    )

    logger.info(f"Admin {current_user.email} triggered task {body.task_name} (id={result.id})")
    return {"task_id": result.id, "task_name": body.task_name, "status": "queued"}


# ============================================================================
# Broadcast
# ============================================================================


@router.post("/broadcast", response_model=SuccessResponse)
async def broadcast_message(
    body: BroadcastRequest,
    current_user: SuperAdminUser,
    db: DB,
):
    """Broadcast a message to all connected users."""
    from app.services import TelegramService

    telegram_service = TelegramService(db)
    sent_count = await telegram_service.broadcast_message(body.message)

    logger.info(f"Admin {current_user.email} broadcast message to {sent_count} users")
    return SuccessResponse(message=f"Message sent to {sent_count} users")


# ============================================================================
# Exchange Connections (admin view)
# ============================================================================


@router.get("/exchange-connections")
async def list_exchange_connections(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
):
    """List all exchange connections across all users."""
    query = select(ExchangeConnection)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(ExchangeConnection.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    connections = list(result.scalars().all())

    items = []
    for conn in connections:
        items.append(
            {
                "id": conn.id,
                "user_id": conn.user_id,
                "exchange_type": conn.exchange_type,
                "label": conn.label,
                "is_testnet": conn.is_testnet,
                "is_trading_enabled": conn.is_trading_enabled,
                "status": conn.status,
                "balance_usd": float(conn.balance_usd) if conn.balance_usd else None,
                "last_sync_at": conn.last_sync_at,
                "created_at": conn.created_at,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
    }


# ============================================================================
# Wallets (admin view)
# ============================================================================


@router.get("/wallets")
async def list_wallets(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
):
    """List all wallets across all users."""
    query = select(Wallet)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    query = query.order_by(Wallet.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)

    result = await db.execute(query)
    wallets = list(result.scalars().all())

    items = []
    for w in wallets:
        items.append(
            {
                "id": w.id,
                "user_id": w.user_id,
                "address": w.address,
                "wallet_type": w.wallet_type,
                "status": w.status,
                "is_trading_enabled": w.is_trading_enabled,
                "balance_usd": float(w.balance_usd) if w.balance_usd else None,
                "margin_used": float(w.margin_used) if w.margin_used else None,
                "last_sync_at": w.last_sync_at,
                "created_at": w.created_at,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
    }
