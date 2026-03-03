from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema, TimestampMixin


class ConnectExchangeRequest(BaseModel):
    exchange_type: str = Field(..., pattern="^binance$")
    api_key: str = Field(..., min_length=10)
    api_secret: str = Field(..., min_length=10)
    is_testnet: bool = False
    label: str | None = Field(None, max_length=100)


class ToggleExchangeTradingRequest(BaseModel):
    enabled: bool


class ExchangeConnectionResponse(BaseSchema, TimestampMixin):
    id: str
    user_id: str
    exchange_type: str
    label: str | None = None
    is_testnet: bool
    is_trading_enabled: bool
    status: str
    balance_usd: float | None = None
    margin_used: float | None = None
    unrealized_pnl: float | None = None
    last_sync_at: datetime | None = None


class ExchangeBalanceResponse(BaseSchema):
    available_balance: float
    total_balance: float
    margin_used: float
    unrealized_pnl: float


class ExchangeSyncResponse(BaseSchema):
    success: bool
    balance_usd: float
    positions_count: int
    synced_at: datetime
