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
    total_wallet_balance: float
    total_exchange_balance: float
    total_unrealized_pnl: float
    active_wallets: int
    active_exchanges: int


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


class LLMModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    description: str
    is_active: bool = False


class UpdateModelsRequest(BaseModel):
    active_models: list[str]


# Pre-seeded catalog of available LLM models on OpenRouter
AVAILABLE_LLM_MODELS: list[dict[str, str]] = [
    # Anthropic
    {
        "id": "anthropic/claude-sonnet-4.6",
        "name": "Claude Sonnet 4.6",
        "provider": "Anthropic",
        "description": "Latest Anthropic model, strong reasoning and analysis",
    },
    {
        "id": "anthropic/claude-opus-4.6",
        "name": "Claude Opus 4.6",
        "provider": "Anthropic",
        "description": "Most capable Anthropic model, deep analysis",
    },
    {
        "id": "anthropic/claude-haiku-4.5",
        "name": "Claude Haiku 4.5",
        "provider": "Anthropic",
        "description": "Fast and cost-efficient Anthropic model",
    },
    # OpenAI
    {
        "id": "openai/gpt-5.2-chat",
        "name": "GPT-5.2 Chat",
        "provider": "OpenAI",
        "description": "Latest OpenAI flagship model",
    },
    {
        "id": "openai/o3-mini",
        "name": "O3 Mini",
        "provider": "OpenAI",
        "description": "OpenAI reasoning model, compact and fast",
    },
    {
        "id": "openai/gpt-4.1",
        "name": "GPT-4.1",
        "provider": "OpenAI",
        "description": "Strong general-purpose OpenAI model",
    },
    {
        "id": "openai/gpt-4.1-mini",
        "name": "GPT-4.1 Mini",
        "provider": "OpenAI",
        "description": "Cost-efficient OpenAI model",
    },
    # xAI
    {
        "id": "x-ai/grok-4.20-beta",
        "name": "Grok 4.20 Beta",
        "provider": "xAI",
        "description": "Latest xAI model with real-time data awareness",
    },
    {
        "id": "x-ai/grok-3-mini-beta",
        "name": "Grok 3 Mini Beta",
        "provider": "xAI",
        "description": "Compact xAI reasoning model",
    },
    # Google
    {
        "id": "google/gemini-2.5-pro-preview",
        "name": "Gemini 2.5 Pro",
        "provider": "Google",
        "description": "Google's most capable model, strong at analysis",
    },
    {
        "id": "google/gemini-2.5-flash-preview",
        "name": "Gemini 2.5 Flash",
        "provider": "Google",
        "description": "Fast and efficient Google model",
    },
    # Meta
    {
        "id": "meta-llama/llama-4-maverick",
        "name": "Llama 4 Maverick",
        "provider": "Meta",
        "description": "Meta's latest open model, strong reasoning",
    },
    {
        "id": "meta-llama/llama-4-scout",
        "name": "Llama 4 Scout",
        "provider": "Meta",
        "description": "Efficient Meta open model",
    },
    # DeepSeek
    {
        "id": "deepseek/deepseek-r1",
        "name": "DeepSeek R1",
        "provider": "DeepSeek",
        "description": "Strong reasoning model, competitive performance",
    },
    {
        "id": "deepseek/deepseek-chat-v3-0324",
        "name": "DeepSeek V3",
        "provider": "DeepSeek",
        "description": "General-purpose DeepSeek chat model",
    },
    # Mistral
    {
        "id": "mistralai/mistral-large-2411",
        "name": "Mistral Large",
        "provider": "Mistral",
        "description": "Mistral's flagship model",
    },
    {
        "id": "mistralai/mistral-small-3.1-24b-instruct",
        "name": "Mistral Small 3.1",
        "provider": "Mistral",
        "description": "Efficient Mistral model, good cost-performance ratio",
    },
    # Qwen
    {
        "id": "qwen/qwen-2.5-72b-instruct",
        "name": "Qwen 2.5 72B",
        "provider": "Qwen",
        "description": "Alibaba's large instruction-tuned model",
    },
]


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

    # Wallet & exchange balances
    from app.models.exchange_connection import ExchangeConnectionStatus
    from app.models.wallet import WalletStatus

    total_wallet_balance = (
        await db.scalar(
            select(func.sum(Wallet.balance_usd)).where(Wallet.status == WalletStatus.ACTIVE)
        )
        or 0
    )
    active_wallets = (
        await db.scalar(select(func.count(Wallet.id)).where(Wallet.status == WalletStatus.ACTIVE))
        or 0
    )

    total_exchange_balance = (
        await db.scalar(
            select(func.sum(ExchangeConnection.balance_usd)).where(
                ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE
            )
        )
        or 0
    )
    active_exchanges = (
        await db.scalar(
            select(func.count(ExchangeConnection.id)).where(
                ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE
            )
        )
        or 0
    )

    total_unrealized_pnl = 0
    wallet_pnl = await db.scalar(
        select(func.sum(Wallet.unrealized_pnl)).where(Wallet.status == WalletStatus.ACTIVE)
    )
    exchange_pnl = await db.scalar(
        select(func.sum(ExchangeConnection.unrealized_pnl)).where(
            ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE
        )
    )
    total_unrealized_pnl = float(wallet_pnl or 0) + float(exchange_pnl or 0)

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
        total_wallet_balance=float(total_wallet_balance),
        total_exchange_balance=float(total_exchange_balance),
        total_unrealized_pnl=total_unrealized_pnl,
        active_wallets=active_wallets,
        active_exchanges=active_exchanges,
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


async def load_config_overrides(db: AsyncSession) -> int:
    """Load all DB config overrides into in-memory settings at startup.
    Called from app lifespan so admin changes survive restarts."""
    result = await db.execute(select(SystemConfig))
    db_configs = {row.key: row.value for row in result.scalars().all()}

    applied = 0
    for key, raw_value in db_configs.items():
        meta = CONFIGURABLE_SETTINGS.get(key)
        if not meta:
            continue
        try:
            deserialized = _deserialize_value(raw_value, meta["type"])
            setattr(settings, key, deserialized)
            applied += 1
        except Exception as e:
            logger.warning(f"Failed to apply config override {key}: {e}")

    if applied:
        logger.info(f"Loaded {applied} config override(s) from database")
    return applied


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
# LLM Model Management
# ============================================================================


@router.get("/models", response_model=list[LLMModelInfo])
async def list_llm_models(current_user: AdminUser) -> list[LLMModelInfo]:
    """List all available LLM models with their active status."""
    active_models = settings.llm_models
    return [
        LLMModelInfo(
            id=m["id"],
            name=m["name"],
            provider=m["provider"],
            description=m["description"],
            is_active=m["id"] in active_models,
        )
        for m in AVAILABLE_LLM_MODELS
    ]


@router.put("/models")
async def update_active_models(
    body: UpdateModelsRequest,
    current_user: SuperAdminUser,
    db: DB,
) -> dict[str, Any]:
    """Set which LLM models are used for consensus analysis."""
    known_ids = {m["id"] for m in AVAILABLE_LLM_MODELS}
    # Allow custom model IDs not in the catalog (power users)
    active = body.active_models

    if len(active) < 1:
        from app.core.exceptions import ValidationError

        raise ValidationError("At least 1 model must be active")

    serialized = json.dumps(active)

    # Upsert system_config
    existing = await db.execute(select(SystemConfig).where(SystemConfig.key == "llm_models"))
    config_row = existing.scalar_one_or_none()
    if config_row:
        config_row.value = serialized
    else:
        config_row = SystemConfig(
            key="llm_models",
            value=serialized,
            description="Active LLM models for consensus analysis",
            category="signal_quality",
        )
        db.add(config_row)

    await db.commit()

    # Apply in-memory immediately
    settings.llm_models = active

    in_catalog = [m for m in active if m in known_ids]
    custom = [m for m in active if m not in known_ids]
    logger.info(
        f"Admin {current_user.email} updated active models: "
        f"{len(in_catalog)} catalog + {len(custom)} custom = {len(active)} total"
    )

    return {
        "active_models": active,
        "count": len(active),
        "message": f"Updated to {len(active)} active model(s)",
    }


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


class AdminExecuteRequest(BaseModel):
    user_email: str | None = None
    user_id: str | None = None
    leverage: int | None = None
    position_size_percent: float | None = None


@router.post("/signals/{signal_id}/execute-for-user")
async def admin_execute_signal_for_user(
    signal_id: str,
    body: AdminExecuteRequest,
    current_user: SuperAdminUser,
    db: DB,
) -> dict[str, Any]:
    """Force-execute a signal for a specific user. Bypasses risk checks."""
    from app.models.exchange_connection import (
        ExchangeConnectionStatus,
        ExchangeType,
    )
    from app.services.trading.binance_executor import BinanceTradeExecutor
    from app.services.trading.executor import TradeExecutor

    # Find signal
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Signal")

    # Find user
    if body.user_email:
        result = await db.execute(
            select(User)
            .options(
                selectinload(User.exchange_connections),
                selectinload(User.wallets),
                selectinload(User.telegram_connection),
            )
            .where(User.email == body.user_email)
        )
    elif body.user_id:
        result = await db.execute(
            select(User)
            .options(
                selectinload(User.exchange_connections),
                selectinload(User.wallets),
                selectinload(User.telegram_connection),
            )
            .where(User.id == body.user_id)
        )
    else:
        from app.core.exceptions import ValidationError

        raise ValidationError("Provide user_email or user_id")

    user = result.scalar_one_or_none()
    if not user:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("User")

    # Execute based on exchange
    if signal.exchange == "binance":
        connection = next(
            (
                c
                for c in user.exchange_connections
                if c.exchange_type == ExchangeType.BINANCE
                and c.status == ExchangeConnectionStatus.ACTIVE
                and not c.is_testnet
            ),
            None,
        )
        if not connection:
            return {
                "status": "error",
                "message": f"User {user.email} has no active Binance connection",
            }

        executor = BinanceTradeExecutor(db)
        trade = await executor.execute_signal(
            user=user,
            exchange_connection=connection,
            signal=signal,
            leverage=body.leverage,
            position_size_percent=body.position_size_percent,
        )
    else:
        wallet = next(
            (w for w in user.wallets if w.is_trading_enabled),
            None,
        )
        if not wallet:
            return {
                "status": "error",
                "message": f"User {user.email} has no trading-enabled wallet",
            }

        executor = TradeExecutor(db)
        trade = await executor.execute_signal(
            user=user,
            wallet=wallet,
            signal=signal,
            leverage=body.leverage,
            position_size_percent=body.position_size_percent,
        )

    await db.commit()

    logger.info(
        f"Admin {current_user.email} force-executed signal {signal_id} "
        f"for user {user.email}: trade {trade.id} status={trade.status.value}"
    )

    return {
        "status": "success",
        "trade_id": trade.id,
        "trade_status": trade.status.value,
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "user_email": user.email,
    }


@router.post("/signals/{signal_id}/execute")
async def admin_execute_signal_for_eligible_users(
    signal_id: str,
    current_user: SuperAdminUser,
    db: DB,
) -> dict[str, Any]:
    """Execute a signal for all eligible users (subscribed with active connections)."""
    from app.core.exceptions import RiskLimitError
    from app.models.exchange_connection import (
        ExchangeConnectionStatus,
        ExchangeType,
    )
    from app.models.wallet import WalletStatus
    from app.services.telegram_service import TelegramService
    from app.services.trading.binance_executor import BinanceTradeExecutor
    from app.services.trading.executor import TradeExecutor

    # Find signal
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Signal")

    results: list[dict[str, Any]] = []

    if signal.exchange == "binance":
        # Get all users with active Binance connections
        user_result = await db.execute(
            select(User)
            .join(ExchangeConnection, ExchangeConnection.user_id == User.id)
            .options(
                selectinload(User.exchange_connections),
                selectinload(User.telegram_connection),
            )
            .where(
                ExchangeConnection.exchange_type == ExchangeType.BINANCE,
                ExchangeConnection.is_testnet.is_(False),
                ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE,
                ExchangeConnection.is_trading_enabled.is_(True),
                User.is_subscribed.is_(True),
            )
            .distinct()
        )
        users = list(user_result.scalars().all())

        executor = BinanceTradeExecutor(db)
        telegram_service = TelegramService(db)

        for user in users:
            connection = next(
                (
                    c
                    for c in user.exchange_connections
                    if c.can_trade
                    and c.status == ExchangeConnectionStatus.ACTIVE
                    and c.exchange_type == ExchangeType.BINANCE
                    and not c.is_testnet
                ),
                None,
            )
            if not connection:
                results.append({"user": user.email, "status": "skipped", "reason": "no connection"})
                continue

            try:
                trade = await executor.execute_signal(
                    user=user, exchange_connection=connection, signal=signal
                )
                results.append(
                    {
                        "user": user.email,
                        "status": "executed",
                        "trade_id": trade.id,
                        "trade_status": trade.status.value,
                    }
                )

                if (
                    user.telegram_connection
                    and user.telegram_connection.is_verified
                    and user.telegram_connection.trade_notifications
                ):
                    try:
                        await telegram_service.send_trade_opened_notification(
                            user.telegram_connection, trade
                        )
                    except Exception as e:
                        logger.error(f"Failed to send trade notification for user {user.id}: {e}")

            except RiskLimitError as e:
                results.append({"user": user.email, "status": "rejected", "reason": e.detail})
            except Exception as e:
                results.append({"user": user.email, "status": "error", "reason": str(e)})

    else:
        # Hyperliquid — get users with active wallets
        user_result = await db.execute(
            select(User)
            .join(Wallet, Wallet.user_id == User.id)
            .options(
                selectinload(User.wallets),
                selectinload(User.telegram_connection),
            )
            .where(
                Wallet.status == WalletStatus.ACTIVE,
                Wallet.is_authorized.is_(True),
                Wallet.is_trading_enabled.is_(True),
                User.is_subscribed.is_(True),
            )
            .distinct()
        )
        users = list(user_result.scalars().all())

        executor = TradeExecutor(db)
        telegram_service = TelegramService(db)

        for user in users:
            wallet = next((w for w in user.wallets if w.can_trade), None)
            if not wallet:
                results.append({"user": user.email, "status": "skipped", "reason": "no wallet"})
                continue

            try:
                trade = await executor.execute_signal(user=user, wallet=wallet, signal=signal)
                results.append(
                    {
                        "user": user.email,
                        "status": "executed",
                        "trade_id": trade.id,
                        "trade_status": trade.status.value,
                    }
                )

                if (
                    user.telegram_connection
                    and user.telegram_connection.is_verified
                    and user.telegram_connection.trade_notifications
                ):
                    try:
                        await telegram_service.send_trade_opened_notification(
                            user.telegram_connection, trade
                        )
                    except Exception as e:
                        logger.error(f"Failed to send trade notification for user {user.id}: {e}")

            except RiskLimitError as e:
                results.append({"user": user.email, "status": "rejected", "reason": e.detail})
            except Exception as e:
                results.append({"user": user.email, "status": "error", "reason": str(e)})

    await db.commit()

    executed = [r for r in results if r["status"] == "executed"]
    rejected = [r for r in results if r["status"] == "rejected"]
    errors = [r for r in results if r["status"] == "error"]

    logger.info(
        f"Admin {current_user.email} executed signal {signal_id} for eligible users: "
        f"{len(executed)} executed, {len(rejected)} rejected, {len(errors)} errors"
    )

    return {
        "signal_id": signal_id,
        "symbol": signal.symbol,
        "exchange": signal.exchange,
        "total_eligible": len(results),
        "executed": len(executed),
        "rejected": len(rejected),
        "errors": len(errors),
        "details": results,
    }


# ============================================================================
# Trades Management
# ============================================================================


@router.get("/trades")
async def list_all_trades(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    status: TradeStatus | None = None,
    exchange: str | None = Query(None),
):
    base_query = select(Trade)
    if status:
        base_query = base_query.where(Trade.status == status)
    if exchange:
        base_query = base_query.where(Trade.exchange == exchange)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    query = select(Trade, User.email).join(User, Trade.user_id == User.id, isouter=True)
    if status:
        query = query.where(Trade.status == status)
    if exchange:
        query = query.where(Trade.exchange == exchange)
    query = (
        query.order_by(Trade.created_at.desc()).offset(pagination.offset).limit(pagination.limit)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for trade, email in rows:
        data = TradeResponse.model_validate(trade).model_dump()
        data["user_email"] = email or "—"
        items.append(data)

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
    }


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


@router.get("/subscriptions")
async def list_all_subscriptions(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    status: SubscriptionStatus | None = None,
):
    base_query = select(Subscription)
    if status:
        base_query = base_query.where(Subscription.status == status)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    query = select(Subscription, User.email).join(
        User, Subscription.user_id == User.id, isouter=True
    )
    if status:
        query = query.where(Subscription.status == status)
    query = (
        query.order_by(Subscription.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for s, email in rows:
        data = SubscriptionResponse.model_validate(s).model_dump()
        data["user_email"] = email or "—"
        items.append(data)

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
    }


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
    base_query = select(ExchangeConnection)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    query = (
        select(ExchangeConnection, User.email)
        .join(User, ExchangeConnection.user_id == User.id, isouter=True)
        .order_by(ExchangeConnection.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for conn, email in rows:
        items.append(
            {
                "id": conn.id,
                "user_id": conn.user_id,
                "user_email": email or "—",
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
    base_query = select(Wallet)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    query = (
        select(Wallet, User.email)
        .join(User, Wallet.user_id == User.id, isouter=True)
        .order_by(Wallet.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    for w, email in rows:
        items.append(
            {
                "id": w.id,
                "user_id": w.user_id,
                "user_email": email or "—",
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


# ============================================================================
# Live Logs
# ============================================================================

LOG_FILES = {
    "api": "/var/log/stackalpha/error.log",
    "celery-worker": "/var/log/stackalpha/celery-worker.log",
    "celery-beat": "/var/log/stackalpha/celery-beat.log",
}

# Noise patterns to filter out from logs by default
LOG_NOISE_PATTERNS = (
    "INFO sqlalchemy.engine.Engine",
    "sqlalchemy.engine.Engine",
    "[raw sql] ()",
    "[generated in ",
    "select pg_catalog.version()",
    "select current_schema()",
    "show standard_conforming_strings",
    "BEGIN (implicit)",
)


@router.get("/logs")
async def get_logs(
    current_user: SuperAdminUser,
    source: str = Query("celery-worker"),
    lines: int = Query(200, ge=1, le=2000),
    search: str | None = Query(None),
    filter_noise: bool = Query(True),
) -> dict[str, Any]:
    """Get recent log lines from a log file."""
    import os

    log_path = LOG_FILES.get(source)
    if not log_path:
        return {"lines": [], "error": f"Unknown log source: {source}"}

    if not os.path.exists(log_path):
        return {"lines": [], "error": f"Log file not found: {log_path}"}

    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            # Read more to compensate for filtered lines
            read_size = min(file_size, 1_024_000)
            f.seek(max(0, file_size - read_size))
            content = f.read().decode("utf-8", errors="replace")

        all_lines = content.splitlines()

        # Filter out noisy SQLAlchemy / engine lines
        if filter_noise:
            all_lines = [
                line for line in all_lines if not any(p in line for p in LOG_NOISE_PATTERNS)
            ]

        recent = all_lines[-lines:]

        if search:
            search_lower = search.lower()
            recent = [line for line in recent if search_lower in line.lower()]

        return {
            "source": source,
            "lines": recent,
            "total_lines": len(recent),
            "available_sources": list(LOG_FILES.keys()),
        }
    except Exception as e:
        return {"lines": [], "error": str(e)}


@router.get("/logs/stream")
async def stream_logs(
    current_user: SuperAdminUser,
    source: str = Query("celery-worker"),
):
    """Stream log lines via SSE (Server-Sent Events)."""
    import asyncio
    import os

    from starlette.responses import StreamingResponse

    log_path = LOG_FILES.get(source)
    if not log_path or not os.path.exists(log_path):
        return {"error": f"Log file not available: {source}"}

    async def log_generator():
        with open(log_path) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    stripped = line.rstrip()
                    if not any(p in stripped for p in LOG_NOISE_PATTERNS):
                        yield f"data: {stripped}\n\n"
                else:
                    await asyncio.sleep(0.5)

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
