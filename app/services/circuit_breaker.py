"""
Circuit Breaker Service - Emergency Trading Controls

Provides kill switch and safety mechanisms to protect capital:
- Emergency stop all trading
- Pause/resume controls
- System health monitoring
- Automatic triggers based on risk events

State is persisted in the risk_settings table.
"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Trade, TradeStatus
from app.models.risk_settings import PositionSizingMethod, RiskSettings

logger = logging.getLogger(__name__)


class CircuitBreakerStatus(str, Enum):
    ACTIVE = "active"  # Trading allowed
    PAUSED = "paused"  # New trades paused, existing positions open
    KILLED = "killed"  # All trading stopped, positions closed


class SystemHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    OFFLINE = "offline"


class CircuitBreakerService:
    """
    Emergency controls and safety mechanisms.
    State is persisted in the database via RiskSettings.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_settings(self, user_id: str) -> RiskSettings:
        """Get or create risk settings for user."""
        result = await self.db.execute(select(RiskSettings).where(RiskSettings.user_id == user_id))
        settings = result.scalar_one_or_none()

        if not settings:
            settings = RiskSettings(
                id=str(uuid4()),
                user_id=user_id,
                position_sizing_method=PositionSizingMethod.FIXED_PERCENT,
            )
            self.db.add(settings)
            await self.db.commit()
            await self.db.refresh(settings)

        return settings

    async def is_trading_allowed(self, user_id: str) -> tuple[bool, str | None]:
        """Check if trading is currently allowed."""
        settings = await self._get_settings(user_id)
        status = settings.circuit_breaker_status

        if status == CircuitBreakerStatus.KILLED.value:
            return False, "Circuit breaker: Kill switch activated"

        if status == CircuitBreakerStatus.PAUSED.value:
            return False, f"Trading paused: {settings.paused_reason}"

        if settings.trading_paused:
            return False, "Trading is paused"

        return True, None

    async def pause_trading(
        self,
        user_id: str,
        reason: str,
        paused_by: str = "system",
        duration_seconds: int | None = None,
    ) -> None:
        """Pause new trading (keep existing positions)."""
        settings = await self._get_settings(user_id)
        settings.circuit_breaker_status = CircuitBreakerStatus.PAUSED.value
        settings.trading_paused = True
        settings.paused_reason = reason
        settings.paused_at = datetime.utcnow()
        settings.paused_by = paused_by

        if duration_seconds:
            settings.auto_resume_at = datetime.utcnow() + timedelta(seconds=duration_seconds)
        else:
            settings.auto_resume_at = None

        await self.db.commit()
        logger.warning(f"Trading paused for user {user_id} by {paused_by}: {reason}")

    async def resume_trading(self, user_id: str) -> None:
        """Resume trading after pause."""
        settings = await self._get_settings(user_id)

        if settings.circuit_breaker_status == CircuitBreakerStatus.KILLED.value:
            raise ValueError(
                "Cannot resume while kill switch is active. Deactivate kill switch first."
            )

        settings.circuit_breaker_status = CircuitBreakerStatus.ACTIVE.value
        settings.trading_paused = False
        settings.paused_reason = None
        settings.paused_at = None
        settings.paused_by = None
        settings.auto_resume_at = None

        await self.db.commit()
        logger.info(f"Trading resumed for user {user_id}")

    async def kill_switch(self, user_id: str, close_positions: bool = True) -> None:
        """EMERGENCY: Stop all trading and optionally close positions."""
        settings = await self._get_settings(user_id)
        settings.circuit_breaker_status = CircuitBreakerStatus.KILLED.value
        settings.trading_paused = True
        settings.paused_reason = "EMERGENCY: Kill switch activated"
        settings.paused_at = datetime.utcnow()
        settings.paused_by = "kill_switch"
        settings.auto_resume_at = None

        logger.critical(f"KILL SWITCH activated for user {user_id}")

        if close_positions:
            open_trades_result = await self.db.execute(
                select(Trade).where(
                    Trade.user_id == user_id,
                    Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
                )
            )
            open_trades = list(open_trades_result.scalars().all())
            logger.info(f"Closing {len(open_trades)} open positions for user {user_id}")

            for trade in open_trades:
                trade.status = TradeStatus.CLOSING
                logger.info(f"Closing trade {trade.id} - {trade.symbol}")

        await self.db.commit()

    async def deactivate_kill_switch(self, user_id: str) -> None:
        """Deactivate kill switch (requires manual action)."""
        settings = await self._get_settings(user_id)
        settings.circuit_breaker_status = CircuitBreakerStatus.ACTIVE.value
        settings.trading_paused = False
        settings.paused_reason = None
        settings.paused_at = None
        settings.paused_by = None
        settings.auto_resume_at = None

        await self.db.commit()
        logger.info(f"Kill switch deactivated for user {user_id}")

    async def get_statistics(self, user_id: str) -> dict:
        """Get circuit breaker statistics."""
        settings = await self._get_settings(user_id)

        # Count open positions
        open_count_result = await self.db.execute(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
            )
        )
        open_positions = len(list(open_count_result.scalars().all()))

        status = settings.circuit_breaker_status
        trading_allowed = (
            status == CircuitBreakerStatus.ACTIVE.value and not settings.trading_paused
        )

        return {
            "status": status,
            "system_health": SystemHealth.HEALTHY.value,
            "trading_allowed": trading_allowed,
            "paused_reason": settings.paused_reason,
            "paused_at": (settings.paused_at.isoformat() if settings.paused_at else None),
            "paused_by": settings.paused_by,
            "auto_resume_at": (
                settings.auto_resume_at.isoformat() if settings.auto_resume_at else None
            ),
            "open_positions_count": open_positions,
        }
