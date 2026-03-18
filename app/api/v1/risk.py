"""Risk Management and Circuit Breaker API Endpoints"""

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.risk_settings import RiskSettings
from app.schemas.risk import (
    CircuitBreakerStatusResponse,
    PauseTradingRequest,
    PortfolioMetricsResponse,
    PositionSizeRequest,
    PositionSizeResponse,
    RiskSettingsResponse,
    UpdateRiskSettingsRequest,
)
from app.services.circuit_breaker import CircuitBreakerService
from app.services.trading.risk_management import RiskManagementService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/risk", tags=["Risk Management"])

DB = Annotated[AsyncSession, Depends(get_db)]


def _serialize_risk_settings(settings: RiskSettings) -> RiskSettingsResponse:
    """Explicitly serialize RiskSettings ORM object to response schema."""
    return RiskSettingsResponse(
        margin_per_trade_percent=float(settings.margin_per_trade_percent),
        risk_percent_per_trade=float(settings.risk_percent_per_trade),
        max_portfolio_heat=float(settings.max_portfolio_heat),
        max_open_positions=settings.max_open_positions,
        leverage=settings.leverage,
        max_daily_loss_percent=float(settings.max_daily_loss_percent),
        max_weekly_loss_percent=float(settings.max_weekly_loss_percent),
        max_monthly_loss_percent=float(settings.max_monthly_loss_percent),
        min_risk_reward_ratio=float(settings.min_risk_reward_ratio),
        max_correlated_positions=settings.max_correlated_positions,
        max_single_asset_exposure_percent=float(settings.max_single_asset_exposure_percent),
        max_consecutive_losses=settings.max_consecutive_losses,
        trading_paused=settings.trading_paused,
        enable_trailing_stop=settings.enable_trailing_stop,
        trailing_stop_percent=float(settings.trailing_stop_percent),
        enable_scale_out=settings.enable_scale_out,
        enable_pyramiding=settings.enable_pyramiding,
        min_signal_confidence=float(settings.min_signal_confidence),
    )


@router.get("/settings", response_model=RiskSettingsResponse)
async def get_risk_settings(
    current_user: CurrentUser,
    db: DB,
):
    """Get user's risk management settings"""
    result = await db.execute(select(RiskSettings).where(RiskSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings
        settings = RiskSettings(
            id=str(uuid4()),
            user_id=current_user.id,
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return _serialize_risk_settings(settings)


@router.patch("/settings", response_model=RiskSettingsResponse)
async def update_risk_settings(
    data: UpdateRiskSettingsRequest,
    current_user: CurrentUser,
    db: DB,
):
    """Update risk management settings"""
    result = await db.execute(select(RiskSettings).where(RiskSettings.user_id == current_user.id))
    settings = result.scalar_one_or_none()

    if not settings:
        # Create if doesn't exist
        settings = RiskSettings(
            id=str(uuid4()),
            user_id=current_user.id,
        )
        db.add(settings)

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)

    await db.commit()
    await db.refresh(settings)

    logger.info(f"Risk settings updated for user {current_user.id}")
    return _serialize_risk_settings(settings)


@router.get("/portfolio-metrics", response_model=PortfolioMetricsResponse)
async def get_portfolio_metrics(
    current_user: CurrentUser,
    db: DB,
):
    """Get real-time portfolio metrics"""
    # Try to fetch live balance from all the user's exchange connections and wallets
    # so that total_equity reflects the real account value, not just deployed margins.
    available_balance = 0.0
    try:
        from app.services.binance import create_binance_exchange_service
        from app.services.exchange_connection_service import ExchangeConnectionService
        from app.services.hyperliquid import get_info_service
        from app.services.wallet_service import WalletService

        exchange_service = ExchangeConnectionService(db)
        connections = await exchange_service.get_user_connections(str(current_user.id))
        for conn in connections:
            if conn.is_verified:
                try:
                    binance = await create_binance_exchange_service(conn)
                    bal = await binance.get_balance()
                    available_balance += bal.get("total_balance", 0)
                    await binance.close()
                except Exception:
                    pass

        wallet_service = WalletService(db)
        wallets = await wallet_service.get_user_wallets(str(current_user.id))
        hl_info = get_info_service()
        for wallet in wallets:
            if wallet.query_address:
                try:
                    bal = await hl_info.get_user_balance(wallet.query_address)
                    available_balance += bal.get("account_value", bal.get("available_balance", 0))
                except Exception:
                    pass
    except Exception:
        pass

    service = RiskManagementService(db)
    metrics = await service.get_portfolio_metrics(
        str(current_user.id), available_balance=available_balance
    )
    return metrics


@router.post("/calculate-position-size", response_model=PositionSizeResponse)
async def calculate_position_size(
    data: PositionSizeRequest,
    current_user: CurrentUser,
    db: DB,
):
    """Calculate optimal position size for a trade"""
    service = RiskManagementService(db)
    result = await service.calculate_position_size(
        user_id=str(current_user.id),
        symbol=data.symbol,
        entry_price=data.entry_price,
        stop_loss_price=data.stop_loss_price,
        signal_confidence=data.signal_confidence,
    )
    return result


# Circuit Breaker Endpoints
circuit_breaker_router = APIRouter(prefix="/circuit-breaker", tags=["Circuit Breaker"])


@circuit_breaker_router.get("/status", response_model=CircuitBreakerStatusResponse)
async def get_circuit_breaker_status(
    current_user: CurrentUser,
    db: DB,
):
    """Get circuit breaker status"""
    service = CircuitBreakerService(db)
    stats = await service.get_statistics(str(current_user.id))
    return stats


@circuit_breaker_router.post("/pause", response_model=CircuitBreakerStatusResponse)
async def pause_trading(
    data: PauseTradingRequest,
    current_user: CurrentUser,
    db: DB,
):
    """Pause trading (manual)"""
    service = CircuitBreakerService(db)
    await service.pause_trading(
        user_id=str(current_user.id),
        reason=data.reason,
        paused_by=str(current_user.id),
        duration_seconds=data.duration_seconds,
    )

    stats = await service.get_statistics(str(current_user.id))
    return stats


@circuit_breaker_router.post("/resume", response_model=CircuitBreakerStatusResponse)
async def resume_trading(
    current_user: CurrentUser,
    db: DB,
):
    """Resume trading"""
    service = CircuitBreakerService(db)
    try:
        await service.resume_trading(str(current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    stats = await service.get_statistics(str(current_user.id))
    return stats


@circuit_breaker_router.post("/kill-switch", response_model=CircuitBreakerStatusResponse)
async def activate_kill_switch(
    current_user: CurrentUser,
    db: DB,
    close_positions: bool = True,
):
    """🚨 EMERGENCY: Activate kill switch"""
    service = CircuitBreakerService(db)
    await service.kill_switch(user_id=str(current_user.id), close_positions=close_positions)

    stats = await service.get_statistics(str(current_user.id))
    return stats


@circuit_breaker_router.post("/deactivate-kill-switch", response_model=CircuitBreakerStatusResponse)
async def deactivate_kill_switch(
    current_user: CurrentUser,
    db: DB,
):
    """Deactivate kill switch (requires manual confirmation)"""
    service = CircuitBreakerService(db)
    await service.deactivate_kill_switch(str(current_user.id))

    stats = await service.get_statistics(str(current_user.id))
    return stats


# Include circuit breaker router
router.include_router(circuit_breaker_router)
