"""Risk Management Schemas"""

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class RiskSettingsResponse(BaseSchema):
    # Position Sizing
    position_sizing_method: str
    max_position_size_usd: float
    max_position_size_percent: float

    # Portfolio Limits
    max_portfolio_heat: float
    max_open_positions: int
    max_leverage: int

    # Drawdown Limits
    max_daily_loss_usd: float
    max_daily_loss_percent: float
    max_weekly_loss_percent: float
    max_monthly_loss_percent: float

    # Risk-Reward
    min_risk_reward_ratio: float

    # Diversification
    max_correlated_positions: int
    max_single_asset_exposure_percent: float

    # Circuit Breakers
    max_consecutive_losses: int
    trading_paused: bool

    # Auto-Trading Features
    enable_trailing_stop: bool
    trailing_stop_percent: float
    enable_scale_out: bool
    enable_pyramiding: bool
    min_signal_confidence: float


class UpdateRiskSettingsRequest(BaseModel):
    # Position Sizing
    position_sizing_method: str | None = None
    max_position_size_usd: float | None = Field(None, gt=0)
    max_position_size_percent: float | None = Field(None, gt=0, le=100)

    # Portfolio Limits
    max_portfolio_heat: float | None = Field(None, gt=0, le=100)
    max_open_positions: int | None = Field(None, gt=0, le=20)
    max_leverage: int | None = Field(None, gt=0, le=100)

    # Drawdown Limits
    max_daily_loss_usd: float | None = Field(None, gt=0)
    max_daily_loss_percent: float | None = Field(None, gt=0, le=100)
    max_weekly_loss_percent: float | None = Field(None, gt=0, le=100)
    max_monthly_loss_percent: float | None = Field(None, gt=0, le=100)

    # Risk-Reward
    min_risk_reward_ratio: float | None = Field(None, gt=0)

    # Diversification
    max_correlated_positions: int | None = Field(None, gt=0)
    max_single_asset_exposure_percent: float | None = Field(None, gt=0, le=100)

    # Circuit Breakers
    max_consecutive_losses: int | None = Field(None, gt=0)
    trading_paused: bool | None = None

    # Auto-Trading Features
    enable_trailing_stop: bool | None = None
    trailing_stop_percent: float | None = Field(None, gt=0, le=100)
    enable_scale_out: bool | None = None
    enable_pyramiding: bool | None = None
    min_signal_confidence: float | None = Field(None, ge=0, le=1)


class PortfolioMetricsResponse(BaseSchema):
    total_equity: float
    total_margin_used: float
    total_unrealized_pnl: float
    total_realized_pnl_today: float
    open_positions_count: int
    portfolio_heat: float
    margin_utilization: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    max_drawdown: float
    consecutive_losses: int


class PositionSizeRequest(BaseModel):
    symbol: str
    entry_price: float = Field(gt=0)
    stop_loss_price: float = Field(gt=0)
    signal_confidence: float = Field(default=0.7, ge=0, le=1)


class PositionSizeResponse(BaseSchema):
    position_size_usd: float
    position_size_percent: float
    risk_amount: float
    approved: bool
    rejection_reason: str | None = None


class CircuitBreakerStatusResponse(BaseSchema):
    status: str  # "active", "paused", "killed"
    system_health: str  # "healthy", "degraded", "critical", "offline"
    trading_allowed: bool
    paused_reason: str | None = None
    paused_at: str | None = None
    paused_by: str | None = None
    auto_resume_at: str | None = None
    open_positions_count: int


class PauseTradingRequest(BaseModel):
    reason: str
    duration_seconds: int | None = Field(None, gt=0, le=86400 * 7)  # Max 1 week
