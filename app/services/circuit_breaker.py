"""
Circuit Breaker Service - Emergency Trading Controls

Provides kill switch and safety mechanisms to protect capital:
- Emergency stop all trading
- Pause/resume controls
- System health monitoring
- Automatic triggers based on risk events
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Trade, TradeStatus

logger = logging.getLogger(__name__)


class CircuitBreakerStatus(str, Enum):
    ACTIVE = "active"  # Trading allowed
    PAUSED = "paused"  # New trades paused, existing positions open
    KILLED = "killed"  # All trading stopped, positions closed


class SystemHealth(str, Enum):
    HEALTHY = "healthy"  # All systems operational
    DEGRADED = "degraded"  # Some issues, reduced capacity
    CRITICAL = "critical"  # Major issues, trading should stop
    OFFLINE = "offline"  # No connection


@dataclass
class CircuitBreakerState:
    """Current state of the circuit breaker"""

    status: CircuitBreakerStatus
    paused_reason: str | None = None
    paused_at: datetime | None = None
    paused_by: str | None = None  # "system" or user_id
    auto_resume_at: datetime | None = None
    system_health: SystemHealth = SystemHealth.HEALTHY


class CircuitBreakerService:
    """
    Emergency controls and safety mechanisms.

    Features:
    - Kill Switch: Emergency close all positions
    - Pause Trading: Stop new trades, keep existing
    - Auto-Triggers: Pause on risk events
    - System Health: Monitor API/exchange status
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        # In production, this would be stored in Redis/database
        # For now, using in-memory state
        self._state: dict[str, CircuitBreakerState] = {}

    async def get_state(self, user_id: str) -> CircuitBreakerState:
        """Get current circuit breaker state for user"""
        if user_id not in self._state:
            self._state[user_id] = CircuitBreakerState(status=CircuitBreakerStatus.ACTIVE)
        return self._state[user_id]

    async def is_trading_allowed(self, user_id: str) -> tuple[bool, str | None]:
        """
        Check if trading is currently allowed.

        Returns:
            (allowed: bool, reason: str | None)
        """
        state = await self.get_state(user_id)

        if state.status == CircuitBreakerStatus.KILLED:
            return False, "Circuit breaker: Kill switch activated"

        if state.status == CircuitBreakerStatus.PAUSED:
            return False, f"Trading paused: {state.paused_reason}"

        if state.system_health == SystemHealth.CRITICAL:
            return False, "System health: Critical"

        if state.system_health == SystemHealth.OFFLINE:
            return False, "System offline"

        return True, None

    async def pause_trading(
        self,
        user_id: str,
        reason: str,
        paused_by: str = "system",
        duration_seconds: int | None = None,
    ) -> CircuitBreakerState:
        """
        Pause new trading (keep existing positions).

        Args:
            user_id: User to pause trading for
            reason: Reason for pause (e.g., "Daily loss limit")
            paused_by: "system" or specific user_id
            duration_seconds: Auto-resume after N seconds (optional)
        """
        state = await self.get_state(user_id)
        state.status = CircuitBreakerStatus.PAUSED
        state.paused_reason = reason
        state.paused_at = datetime.utcnow()
        state.paused_by = paused_by

        if duration_seconds:
            from datetime import timedelta

            state.auto_resume_at = datetime.utcnow() + timedelta(seconds=duration_seconds)
        else:
            state.auto_resume_at = None

        logger.warning(f"Trading paused for user {user_id} by {paused_by}: {reason}")

        # TODO: Send Telegram notification
        # await self._notify_user(user_id, f"⚠️ Trading Paused: {reason}")

        return state

    async def resume_trading(self, user_id: str) -> CircuitBreakerState:
        """Resume trading after pause"""
        state = await self.get_state(user_id)

        if state.status == CircuitBreakerStatus.KILLED:
            logger.warning(f"Cannot resume - kill switch active for user {user_id}")
            raise ValueError("Cannot resume while kill switch is active")

        state.status = CircuitBreakerStatus.ACTIVE
        state.paused_reason = None
        state.paused_at = None
        state.paused_by = None
        state.auto_resume_at = None

        logger.info(f"Trading resumed for user {user_id}")

        # TODO: Send Telegram notification
        # await self._notify_user(user_id, "✅ Trading Resumed")

        return state

    async def kill_switch(self, user_id: str, close_positions: bool = True) -> CircuitBreakerState:
        """
        EMERGENCY: Stop all trading and optionally close positions.

        Args:
            user_id: User to activate kill switch for
            close_positions: If True, close all open positions immediately
        """
        state = await self.get_state(user_id)
        state.status = CircuitBreakerStatus.KILLED
        state.paused_reason = "EMERGENCY: Kill switch activated"
        state.paused_at = datetime.utcnow()
        state.paused_by = "kill_switch"

        logger.critical(f"🚨 KILL SWITCH activated for user {user_id}")

        if close_positions:
            # Close all open positions
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
                # In production, this would call the exchange to close
                # For now, just mark as closing
                logger.info(f"Closing trade {trade.id} - {trade.symbol}")

            await self.db.commit()

        # TODO: Send urgent Telegram notification
        # await self._notify_user(user_id, "🚨 EMERGENCY: Kill switch activated! All trading stopped.")

        return state

    async def deactivate_kill_switch(self, user_id: str) -> CircuitBreakerState:
        """Deactivate kill switch (requires manual action)"""
        state = await self.get_state(user_id)
        state.status = CircuitBreakerStatus.ACTIVE
        state.paused_reason = None
        state.paused_at = None
        state.paused_by = None

        logger.info(f"Kill switch deactivated for user {user_id}")
        return state

    async def check_auto_triggers(self, user_id: str) -> CircuitBreakerState:
        """
        Check if any automatic circuit breaker triggers should fire.

        Should be called:
        - After each trade execution
        - Periodically (every minute)
        - On risk limit breaches

        Auto-triggers:
        - Daily loss limit exceeded
        - Consecutive losses threshold
        - System health degraded
        """
        state = await self.get_state(user_id)

        # If already paused/killed, skip checks
        if state.status != CircuitBreakerStatus.ACTIVE:
            return state

        # Check auto-resume
        if (
            state.status == CircuitBreakerStatus.PAUSED
            and state.auto_resume_at
            and datetime.utcnow() >= state.auto_resume_at
        ):
            logger.info(f"Auto-resuming trading for user {user_id}")
            return await self.resume_trading(user_id)

        # Check risk limits (would integrate with RiskManagementService)
        # For now, placeholder for integration
        # from app.services.trading.risk_management import RiskManagementService
        # risk_service = RiskManagementService(self.db)
        # metrics = await risk_service.get_portfolio_metrics(user_id)
        # limits = await risk_service.get_risk_limits(user_id)

        # if metrics.daily_pnl < -limits.max_daily_loss_usd:
        #     await self.pause_trading(
        #         user_id,
        #         f"Daily loss limit: ${abs(metrics.daily_pnl):.2f}",
        #         duration_seconds=86400  # 24 hours
        #     )

        return state

    async def update_system_health(self, health: SystemHealth, reason: str | None = None) -> None:
        """
        Update global system health status.

        Called by monitoring systems when:
        - Exchange API is down
        - WebSocket connection lost
        - Database errors
        - High latency detected
        """
        logger.warning(f"System health updated: {health.value} - {reason}")

        # Update for all users
        for user_id in self._state:
            self._state[user_id].system_health = health

            # Auto-pause on critical
            if (
                health == SystemHealth.CRITICAL
                and self._state[user_id].status == CircuitBreakerStatus.ACTIVE
            ):
                await self.pause_trading(user_id, f"System health critical: {reason}")

    async def get_statistics(self, user_id: str) -> dict:
        """Get circuit breaker statistics"""
        state = await self.get_state(user_id)

        # Count open positions
        open_count_result = await self.db.execute(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
            )
        )
        open_positions = len(list(open_count_result.scalars().all()))

        return {
            "status": state.status.value,
            "system_health": state.system_health.value,
            "trading_allowed": (await self.is_trading_allowed(user_id))[0],
            "paused_reason": state.paused_reason,
            "paused_at": (state.paused_at.isoformat() if state.paused_at else None),
            "paused_by": state.paused_by,
            "auto_resume_at": (state.auto_resume_at.isoformat() if state.auto_resume_at else None),
            "open_positions_count": open_positions,
        }
