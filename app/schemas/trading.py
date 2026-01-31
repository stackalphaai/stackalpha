from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.signal import SignalDirection, SignalOutcome, SignalStatus
from app.models.trade import TradeCloseReason, TradeDirection, TradeStatus
from app.schemas.common import BaseSchema, TimestampMixin


class SignalBase(BaseSchema):
    symbol: str
    direction: SignalDirection
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    suggested_leverage: int = 5
    suggested_position_size_percent: float = 5.0


class SignalResponse(SignalBase, TimestampMixin):
    id: str
    status: SignalStatus
    outcome: SignalOutcome
    confidence_score: float
    consensus_votes: int
    total_votes: int
    market_price_at_creation: float
    risk_reward_ratio: float
    actual_exit_price: float | None = None
    actual_pnl_percent: float | None = None
    expires_at: datetime | None = None
    executed_at: datetime | None = None
    closed_at: datetime | None = None


class SignalDetailResponse(SignalResponse):
    analysis_data: dict[str, Any] | None = None
    technical_indicators: dict[str, Any] | None = None


class TradeBase(BaseSchema):
    symbol: str
    direction: TradeDirection
    position_size: float
    leverage: int = 5


class CreateTradeRequest(BaseModel):
    signal_id: str | None = None
    wallet_id: str
    symbol: str
    direction: TradeDirection
    position_size_usd: float = Field(..., gt=0)
    leverage: int = Field(default=5, ge=1, le=20)
    take_profit_price: float | None = None
    stop_loss_price: float | None = None


class ExecuteSignalRequest(BaseModel):
    signal_id: str
    wallet_id: str
    position_size_percent: float | None = Field(None, gt=0, le=100)
    leverage: int | None = Field(None, ge=1, le=20)


class CloseTradeRequest(BaseModel):
    reason: TradeCloseReason = TradeCloseReason.MANUAL


class TradeResponse(TradeBase, TimestampMixin):
    id: str
    user_id: str
    wallet_id: str
    signal_id: str | None = None
    status: TradeStatus
    entry_price: float | None = None
    exit_price: float | None = None
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    position_size_usd: float
    margin_used: float | None = None
    realized_pnl: float | None = None
    realized_pnl_percent: float | None = None
    unrealized_pnl: float | None = None
    fees_paid: float | None = None
    close_reason: TradeCloseReason | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None


class TradeDetailResponse(TradeResponse):
    funding_paid: float | None = None
    error_message: str | None = None
    duration_seconds: int | None = None
    hyperliquid_order_id: str | None = None


class MarketDataResponse(BaseSchema):
    symbol: str
    mark_price: float
    index_price: float
    funding_rate: float
    open_interest: float
    volume_24h: float
    high_24h: float
    low_24h: float
    price_change_24h: float
    price_change_percent_24h: float


class OrderBookResponse(BaseSchema):
    symbol: str
    bids: list[list[float]]
    asks: list[list[float]]
    timestamp: datetime


class CandleResponse(BaseSchema):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class TradingSettingsRequest(BaseModel):
    max_position_size_percent: float | None = Field(None, gt=0, le=100)
    default_leverage: int | None = Field(None, ge=1, le=20)
    max_leverage: int | None = Field(None, ge=1, le=50)
    auto_trade_signals: bool | None = None


class TradingSettingsResponse(BaseSchema):
    max_position_size_percent: float
    default_leverage: int
    max_leverage: int
    auto_trade_signals: bool
