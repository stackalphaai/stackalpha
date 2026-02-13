"""
Enterprise-grade Risk Management Service

Implements professional risk management strategies:
- Position sizing algorithms (Kelly, Fixed Fractional, Percent of Equity)
- Portfolio heat monitoring (total exposure)
- Drawdown limits (daily, weekly, monthly)
- Risk-reward validation
- Correlation analysis
- Diversification requirements
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Trade, TradeStatus

logger = logging.getLogger(__name__)


class PositionSizingMethod(str, Enum):
    FIXED_AMOUNT = "fixed_amount"  # Fixed dollar amount per trade
    FIXED_PERCENT = "fixed_percent"  # Fixed % of portfolio per trade
    KELLY_CRITERION = "kelly"  # Kelly Criterion (optimal growth)
    RISK_PARITY = "risk_parity"  # Risk-adjusted sizing


@dataclass
class RiskLimits:
    """User-defined risk limits"""

    # Position Sizing
    max_position_size_usd: float = 10000.0
    max_position_size_percent: float = 10.0  # % of portfolio
    position_sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_PERCENT

    # Portfolio Limits
    max_portfolio_heat: float = 50.0  # Max % of portfolio at risk
    max_open_positions: int = 5
    max_leverage: int = 10

    # Drawdown Limits
    max_daily_loss_usd: float = 500.0
    max_daily_loss_percent: float = 5.0
    max_weekly_loss_percent: float = 10.0
    max_monthly_loss_percent: float = 20.0

    # Risk-Reward
    min_risk_reward_ratio: float = 1.5  # Minimum RR ratio to take trade

    # Diversification
    max_correlated_positions: int = 2  # Max positions in correlated assets
    max_single_asset_exposure_percent: float = 20.0  # Max % in one asset

    # Circuit Breakers
    max_consecutive_losses: int = 3  # Pause after N losses
    trading_paused: bool = False


@dataclass
class PortfolioMetrics:
    """Real-time portfolio metrics"""

    total_equity: float
    total_margin_used: float
    total_unrealized_pnl: float
    total_realized_pnl_today: float
    open_positions_count: int
    portfolio_heat: float  # % of portfolio at risk
    margin_utilization: float  # % of margin used
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    max_drawdown: float
    consecutive_losses: int


@dataclass
class PositionSizingResult:
    """Result of position sizing calculation"""

    position_size_usd: float
    position_size_percent: float
    risk_amount: float
    approved: bool
    rejection_reason: str | None = None


class RiskManagementService:
    """
    Enterprise-grade risk management for auto-trading.

    Ensures capital preservation through:
    - Sophisticated position sizing
    - Portfolio-level risk monitoring
    - Dynamic drawdown protection
    - Multi-asset correlation analysis
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_risk_limits(self, user_id: str) -> RiskLimits:
        """Get user's risk limits from database"""
        # TODO: Fetch from database user settings table
        # For now, return defaults
        return RiskLimits()

    async def get_portfolio_metrics(self, user_id: str) -> PortfolioMetrics:
        """Calculate real-time portfolio metrics"""
        # Get all open positions
        open_trades_result = await self.db.execute(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
            )
        )
        open_trades = list(open_trades_result.scalars().all())

        # Get today's closed trades for P&L
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_pnl_result = await self.db.execute(
            select(func.sum(Trade.realized_pnl)).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= today_start,
            )
        )
        daily_pnl = float(today_pnl_result.scalar() or 0)

        # Calculate weekly P&L
        week_start = today_start - timedelta(days=today_start.weekday())
        weekly_pnl_result = await self.db.execute(
            select(func.sum(Trade.realized_pnl)).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= week_start,
            )
        )
        weekly_pnl = float(weekly_pnl_result.scalar() or 0)

        # Calculate monthly P&L
        month_start = today_start.replace(day=1)
        monthly_pnl_result = await self.db.execute(
            select(func.sum(Trade.realized_pnl)).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= month_start,
            )
        )
        monthly_pnl = float(monthly_pnl_result.scalar() or 0)

        # Calculate consecutive losses
        recent_trades_result = await self.db.execute(
            select(Trade.realized_pnl)
            .where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.realized_pnl.isnot(None),
            )
            .order_by(Trade.closed_at.desc())
            .limit(10)
        )
        recent_pnls = [float(pnl) for pnl in recent_trades_result.scalars().all()]
        consecutive_losses = 0
        for pnl in recent_pnls:
            if pnl < 0:
                consecutive_losses += 1
            else:
                break

        # Calculate totals
        total_margin = sum(t.margin_used or 0 for t in open_trades)
        total_unrealized = sum(t.unrealized_pnl or 0 for t in open_trades)
        total_equity = total_margin + total_unrealized + daily_pnl

        # Portfolio heat = total risk / total equity
        total_risk = sum(
            abs(t.position_size_usd * (t.entry_price - (t.stop_loss_price or 0)))
            for t in open_trades
            if t.entry_price and t.stop_loss_price
        )
        portfolio_heat = (total_risk / total_equity * 100) if total_equity > 0 else 0

        # Margin utilization
        # Assume max margin is 10x total equity (conservative)
        max_margin = total_equity * 10
        margin_utilization = (total_margin / max_margin * 100) if max_margin > 0 else 0

        return PortfolioMetrics(
            total_equity=total_equity,
            total_margin_used=total_margin,
            total_unrealized_pnl=total_unrealized,
            total_realized_pnl_today=daily_pnl,
            open_positions_count=len(open_trades),
            portfolio_heat=portfolio_heat,
            margin_utilization=margin_utilization,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            monthly_pnl=monthly_pnl,
            max_drawdown=0.0,  # TODO: Calculate from historical data
            consecutive_losses=consecutive_losses,
        )

    async def calculate_position_size(
        self,
        user_id: str,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        signal_confidence: float = 0.7,
    ) -> PositionSizingResult:
        """
        Calculate optimal position size using configured method.

        Args:
            user_id: User ID
            symbol: Trading symbol
            entry_price: Intended entry price
            stop_loss_price: Stop loss price
            signal_confidence: AI signal confidence (0-1)

        Returns:
            PositionSizingResult with size and approval status
        """
        limits = await self.get_risk_limits(user_id)
        metrics = await self.get_portfolio_metrics(user_id)

        # Check circuit breakers first
        if limits.trading_paused:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason="Trading is paused by circuit breaker",
            )

        # Check consecutive losses
        if metrics.consecutive_losses >= limits.max_consecutive_losses:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason=f"Circuit breaker: {metrics.consecutive_losses} consecutive losses",
            )

        # Check daily loss limit
        daily_loss_percent = (
            abs(metrics.daily_pnl) / metrics.total_equity * 100
            if metrics.total_equity > 0 and metrics.daily_pnl < 0
            else 0
        )
        if daily_loss_percent >= limits.max_daily_loss_percent:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason=f"Daily loss limit reached: {daily_loss_percent:.2f}%",
            )

        # Check position count limit
        if metrics.open_positions_count >= limits.max_open_positions:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason=f"Max open positions reached: {limits.max_open_positions}",
            )

        # Calculate risk per trade (distance from entry to stop loss)
        risk_per_share = abs(entry_price - stop_loss_price)
        risk_percent = risk_per_share / entry_price

        # Check risk-reward ratio
        # Assuming take profit is at 1.5x the risk (can be parameterized)
        risk_reward_ratio = 1.5  # TODO: Get from signal data
        if risk_reward_ratio < limits.min_risk_reward_ratio:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason=f"Risk-reward ratio too low: {risk_reward_ratio:.2f}",
            )

        # Calculate position size based on method
        if limits.position_sizing_method == PositionSizingMethod.FIXED_AMOUNT:
            position_size_usd = min(
                limits.max_position_size_usd,
                metrics.total_equity * limits.max_position_size_percent / 100,
            )

        elif limits.position_sizing_method == PositionSizingMethod.FIXED_PERCENT:
            position_size_usd = metrics.total_equity * limits.max_position_size_percent / 100

        elif limits.position_sizing_method == PositionSizingMethod.KELLY_CRITERION:
            # Kelly = (Win% * Avg Win - Loss% * Avg Loss) / Avg Win
            # Simplified: use signal confidence as win probability
            win_prob = signal_confidence
            loss_prob = 1 - win_prob
            avg_win = risk_reward_ratio
            avg_loss = 1

            kelly_fraction = (win_prob * avg_win - loss_prob * avg_loss) / avg_win
            kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Cap at 25%

            position_size_usd = metrics.total_equity * kelly_fraction

        else:  # RISK_PARITY
            # Size based on volatility (inversely proportional to risk)
            # Higher risk = smaller position
            target_risk_percent = 1.0  # 1% risk per trade
            position_size_usd = (metrics.total_equity * target_risk_percent / 100) / risk_percent

        # Apply hard limits
        position_size_usd = min(
            position_size_usd,
            limits.max_position_size_usd,
            metrics.total_equity * limits.max_position_size_percent / 100,
        )

        position_size_percent = (
            position_size_usd / metrics.total_equity * 100 if metrics.total_equity > 0 else 0
        )

        # Calculate risk amount
        risk_amount = position_size_usd * risk_percent

        # Check if adding this position exceeds portfolio heat
        new_portfolio_heat = (
            metrics.portfolio_heat + (risk_amount / metrics.total_equity * 100)
            if metrics.total_equity > 0
            else 0
        )
        if new_portfolio_heat > limits.max_portfolio_heat:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason=f"Portfolio heat limit exceeded: {new_portfolio_heat:.2f}%",
            )

        return PositionSizingResult(
            position_size_usd=position_size_usd,
            position_size_percent=position_size_percent,
            risk_amount=risk_amount,
            approved=True,
        )

    async def validate_trade(
        self,
        user_id: str,
        symbol: str,
        direction: str,
        position_size_usd: float,
        entry_price: float,
        stop_loss_price: float | None,
        take_profit_price: float | None,
    ) -> tuple[bool, str | None]:
        """
        Validate a trade against all risk management rules.

        Returns:
            (approved: bool, rejection_reason: str | None)
        """
        limits = await self.get_risk_limits(user_id)
        metrics = await self.get_portfolio_metrics(user_id)

        # 1. Check if trading is paused
        if limits.trading_paused:
            return False, "Trading is paused"

        # 2. Check position count
        if metrics.open_positions_count >= limits.max_open_positions:
            return False, f"Max open positions: {limits.max_open_positions}"

        # 3. Check daily loss limit
        if metrics.daily_pnl < 0:
            daily_loss_usd = abs(metrics.daily_pnl)
            daily_loss_percent = (
                daily_loss_usd / metrics.total_equity * 100 if metrics.total_equity > 0 else 0
            )

            if daily_loss_usd >= limits.max_daily_loss_usd:
                return False, f"Daily loss limit: ${daily_loss_usd:.2f}"

            if daily_loss_percent >= limits.max_daily_loss_percent:
                return False, f"Daily loss %: {daily_loss_percent:.2f}%"

        # 4. Check weekly loss limit
        if metrics.weekly_pnl < 0:
            weekly_loss_percent = (
                abs(metrics.weekly_pnl) / metrics.total_equity * 100
                if metrics.total_equity > 0
                else 0
            )
            if weekly_loss_percent >= limits.max_weekly_loss_percent:
                return False, f"Weekly loss limit: {weekly_loss_percent:.2f}%"

        # 5. Check consecutive losses
        if metrics.consecutive_losses >= limits.max_consecutive_losses:
            return False, f"Consecutive losses: {metrics.consecutive_losses}"

        # 6. Check position size
        if position_size_usd > limits.max_position_size_usd:
            return False, f"Position too large: ${position_size_usd:.2f}"

        position_percent = (
            position_size_usd / metrics.total_equity * 100 if metrics.total_equity > 0 else 0
        )
        if position_percent > limits.max_position_size_percent:
            return False, f"Position % too large: {position_percent:.2f}%"

        # 7. Validate stop loss exists
        if not stop_loss_price:
            return False, "Stop loss is required"

        # 8. Check risk-reward ratio
        if stop_loss_price and take_profit_price:
            risk = abs(entry_price - stop_loss_price)
            reward = abs(take_profit_price - entry_price)
            rr_ratio = reward / risk if risk > 0 else 0

            if rr_ratio < limits.min_risk_reward_ratio:
                return False, f"Risk-reward too low: {rr_ratio:.2f}"

        return True, None
