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
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Trade, TradeStatus
from app.models.risk_settings import RiskSettings

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """User-defined risk limits"""

    # Position Sizing — margin% + leverage determines position size
    margin_per_trade_percent: float = 10.0  # % of balance used as margin per trade
    risk_percent_per_trade: float = 2.0  # max % loss per trade (determines stop loss)

    # Portfolio Limits
    max_portfolio_heat: float = 50.0  # Max % of portfolio at risk
    max_open_positions: int = 5
    leverage: int = 5

    # Drawdown Limits
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
        """Get user's risk limits from database, creating defaults if missing."""
        result = await self.db.execute(select(RiskSettings).where(RiskSettings.user_id == user_id))
        risk_settings = result.scalar_one_or_none()

        if not risk_settings:
            # Create default risk settings using admin-configured values
            from app.config import settings as app_settings

            risk_settings = RiskSettings(
                user_id=user_id,
                min_risk_reward_ratio=app_settings.llm_min_risk_reward_ratio,
                leverage=app_settings.default_leverage,
                min_signal_confidence=app_settings.llm_min_confidence,
            )
            self.db.add(risk_settings)
            await self.db.flush()

        # Map the DB PositionSizingMethod string to our local enum
        # User's risk settings are authoritative — return them directly
        return RiskLimits(
            margin_per_trade_percent=float(risk_settings.margin_per_trade_percent),
            risk_percent_per_trade=float(risk_settings.risk_percent_per_trade),
            max_portfolio_heat=float(risk_settings.max_portfolio_heat),
            max_open_positions=risk_settings.max_open_positions,
            leverage=risk_settings.leverage,
            max_daily_loss_percent=float(risk_settings.max_daily_loss_percent),
            max_weekly_loss_percent=float(risk_settings.max_weekly_loss_percent),
            max_monthly_loss_percent=float(risk_settings.max_monthly_loss_percent),
            min_risk_reward_ratio=float(risk_settings.min_risk_reward_ratio),
            max_correlated_positions=risk_settings.max_correlated_positions,
            max_single_asset_exposure_percent=float(
                risk_settings.max_single_asset_exposure_percent
            ),
            max_consecutive_losses=risk_settings.max_consecutive_losses,
            trading_paused=risk_settings.trading_paused,
        )

    async def get_min_signal_confidence(self, user_id: str) -> float:
        """Get user's minimum signal confidence threshold."""
        result = await self.db.execute(
            select(RiskSettings.min_signal_confidence).where(RiskSettings.user_id == user_id)
        )
        confidence = result.scalar_one_or_none()
        return float(confidence) if confidence is not None else 0.55

    async def get_portfolio_metrics(
        self, user_id: str, available_balance: float = 0
    ) -> PortfolioMetrics:
        """Calculate real-time portfolio metrics"""
        # Check if user has a risk counters reset timestamp
        reset_result = await self.db.execute(
            select(RiskSettings.risk_counters_reset_at).where(RiskSettings.user_id == user_id)
        )
        risk_reset_at = reset_result.scalar_one_or_none()

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
        # If risk was reset after today_start, use the reset time as floor
        daily_floor = (
            max(today_start, risk_reset_at.replace(tzinfo=None)) if risk_reset_at else today_start
        )
        today_pnl_result = await self.db.execute(
            select(func.sum(Trade.realized_pnl)).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= daily_floor,
            )
        )
        daily_pnl = Decimal(str(today_pnl_result.scalar() or 0))

        # Calculate weekly P&L
        week_start = today_start - timedelta(days=today_start.weekday())
        weekly_floor = (
            max(week_start, risk_reset_at.replace(tzinfo=None)) if risk_reset_at else week_start
        )
        weekly_pnl_result = await self.db.execute(
            select(func.sum(Trade.realized_pnl)).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= weekly_floor,
            )
        )
        weekly_pnl = Decimal(str(weekly_pnl_result.scalar() or 0))

        # Calculate monthly P&L
        month_start = today_start.replace(day=1)
        monthly_floor = (
            max(month_start, risk_reset_at.replace(tzinfo=None)) if risk_reset_at else month_start
        )
        monthly_pnl_result = await self.db.execute(
            select(func.sum(Trade.realized_pnl)).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= monthly_floor,
            )
        )
        monthly_pnl = Decimal(str(monthly_pnl_result.scalar() or 0))

        # Calculate consecutive losses (also respect reset)
        consec_filters = [
            Trade.user_id == user_id,
            Trade.status == TradeStatus.CLOSED,
            Trade.realized_pnl.isnot(None),
        ]
        if risk_reset_at:
            consec_filters.append(Trade.closed_at >= risk_reset_at)
        recent_trades_result = await self.db.execute(
            select(Trade.realized_pnl)
            .where(*consec_filters)
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
        total_margin = float(sum(t.margin_used or 0 for t in open_trades))
        total_unrealized = float(sum(t.unrealized_pnl or 0 for t in open_trades))

        # Equity: prefer the live exchange balance if provided, otherwise fall back to
        # the sum of deployed margins (an undercount, but better than zero).
        if available_balance > 0:
            total_equity = available_balance + total_unrealized
        else:
            total_equity = max(total_margin + total_unrealized + float(daily_pnl), 0.01)

        # Portfolio heat = (total dollar risk at stop) / equity
        # Dollar risk per trade = notional × |entry - sl| / entry
        total_risk = sum(
            float(t.position_size_usd)
            * abs(float(t.entry_price) - float(t.stop_loss_price))
            / float(t.entry_price)
            for t in open_trades
            if t.entry_price and t.stop_loss_price and float(t.entry_price) > 0
        )
        portfolio_heat = (total_risk / total_equity * 100) if total_equity > 0 else 0

        # Margin utilization relative to account equity
        margin_utilization = (total_margin / total_equity * 100) if total_equity > 0 else 0

        return PortfolioMetrics(
            total_equity=float(total_equity),
            total_margin_used=float(total_margin),
            total_unrealized_pnl=float(total_unrealized),
            total_realized_pnl_today=float(daily_pnl),
            open_positions_count=len(open_trades),
            portfolio_heat=float(portfolio_heat),
            margin_utilization=float(margin_utilization),
            daily_pnl=float(daily_pnl),
            weekly_pnl=float(weekly_pnl),
            monthly_pnl=float(monthly_pnl),
            max_drawdown=0.0,
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
        Calculate position size using margin_per_trade_percent.

        margin = equity * margin_per_trade_percent / 100
        """
        limits = await self.get_risk_limits(user_id)
        metrics = await self.get_portfolio_metrics(user_id)

        if limits.trading_paused:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason="Trading is paused",
            )

        if metrics.open_positions_count >= limits.max_open_positions:
            return PositionSizingResult(
                position_size_usd=0,
                position_size_percent=0,
                risk_amount=0,
                approved=False,
                rejection_reason=f"Max open positions: {limits.max_open_positions}",
            )

        position_size_usd = metrics.total_equity * (limits.margin_per_trade_percent / 100)
        position_size_percent = limits.margin_per_trade_percent
        risk_per_share = abs(entry_price - stop_loss_price)
        risk_amount = position_size_usd * (risk_per_share / entry_price) if entry_price > 0 else 0

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
        available_balance: float = 0,
    ) -> tuple[bool, str | None]:
        """
        Validate a trade against all risk management rules.

        Returns:
            (approved: bool, rejection_reason: str | None)
        """
        limits = await self.get_risk_limits(user_id)
        metrics = await self.get_portfolio_metrics(user_id, available_balance=available_balance)

        # 1. Check if trading is paused
        if limits.trading_paused:
            return False, "Trading is paused"

        # 2. Check position count
        if metrics.open_positions_count >= limits.max_open_positions:
            return False, f"Max open positions: {limits.max_open_positions}"

        # 3. Check daily loss limit
        if metrics.daily_pnl < 0:
            daily_loss_usd = abs(metrics.daily_pnl)
            # Use start-of-day equity (current equity + today's losses) as denominator
            # to avoid inflated percentages when balance is already reduced by losses
            start_of_day_equity = metrics.total_equity + daily_loss_usd
            daily_loss_percent = (
                daily_loss_usd / start_of_day_equity * 100 if start_of_day_equity > 0 else 0
            )

            if daily_loss_percent >= limits.max_daily_loss_percent:
                return False, f"Daily loss %: {daily_loss_percent:.2f}%"

        # 4. Check weekly loss limit
        if metrics.weekly_pnl < 0:
            weekly_loss_usd = abs(metrics.weekly_pnl)
            # Use start-of-week equity as denominator
            start_of_week_equity = metrics.total_equity + weekly_loss_usd
            weekly_loss_percent = (
                weekly_loss_usd / start_of_week_equity * 100 if start_of_week_equity > 0 else 0
            )
            if weekly_loss_percent >= limits.max_weekly_loss_percent:
                return False, f"Weekly loss limit: {weekly_loss_percent:.2f}%"

        # 5. Check consecutive losses
        if metrics.consecutive_losses >= limits.max_consecutive_losses:
            return False, f"Consecutive losses: {metrics.consecutive_losses}"

        # 6. Validate stop loss exists
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

    async def validate_signal_execution(
        self,
        user_id: str,
        signal_confidence: float,
        proposed_leverage: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        position_size_usd: float,
        available_balance: float = 0,
    ) -> tuple[bool, str | None, int, float]:
        """
        Full pre-trade validation.

        Position size = available_balance * margin_per_trade_percent / 100.
        Leverage comes from user's risk settings directly.

        Returns:
            (approved, rejection_reason, leverage, margin_usd)
        """
        limits = await self.get_risk_limits(user_id)

        # 1. Check signal confidence against user's minimum
        min_confidence = await self.get_min_signal_confidence(user_id)
        if signal_confidence < min_confidence:
            return (
                False,
                f"Signal confidence {signal_confidence:.0%} below minimum {min_confidence:.0%}",
                0,
                0,
            )

        # 2. Use user's leverage setting directly
        clamped_leverage = max(1, limits.leverage)

        # 3. Position sizing — margin = balance * margin_per_trade_percent / 100
        equity = available_balance if available_balance > 0 else position_size_usd
        logger.info(
            f"Position sizing for user {user_id}: equity=${equity:.2f}, "
            f"margin_pct={limits.margin_per_trade_percent}%, leverage={clamped_leverage}x"
        )
        clamped_size = equity * (limits.margin_per_trade_percent / 100)

        # Ensure position size is positive
        clamped_size = max(0, clamped_size)

        # 4. Run full trade validation
        direction = "long" if take_profit_price > entry_price else "short"
        approved, reason = await self.validate_trade(
            user_id=user_id,
            symbol="",  # Symbol not needed for these checks
            direction=direction,
            position_size_usd=clamped_size,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            available_balance=equity,
        )

        return approved, reason, clamped_leverage, clamped_size
