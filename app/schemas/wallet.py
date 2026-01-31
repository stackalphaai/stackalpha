from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.wallet import WalletStatus, WalletType
from app.schemas.common import BaseSchema, TimestampMixin


class WalletBase(BaseSchema):
    address: str = Field(..., min_length=42, max_length=42)
    wallet_type: WalletType = WalletType.MASTER


class ConnectWalletRequest(BaseModel):
    address: str = Field(..., min_length=42, max_length=42)

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v.startswith("0x"):
            raise ValueError("Invalid Ethereum address format")
        return v.lower()


class AuthorizeWalletRequest(BaseModel):
    signature: str
    message: str


class GenerateAPIWalletRequest(BaseModel):
    name: str | None = Field(None, max_length=100)


class WalletResponse(WalletBase, TimestampMixin):
    id: str
    user_id: str
    status: WalletStatus
    is_trading_enabled: bool
    is_authorized: bool
    balance_usd: float | None = None
    margin_used: float | None = None
    unrealized_pnl: float | None = None
    last_sync_at: datetime | None = None


class WalletBalanceResponse(BaseSchema):
    address: str
    balance_usd: float
    margin_used: float
    unrealized_pnl: float
    available_balance: float
    account_value: float


class WalletPositionResponse(BaseSchema):
    symbol: str
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    margin_used: float
    leverage: int
    liquidation_price: float | None = None


class WalletSyncResponse(BaseSchema):
    success: bool
    balance_usd: float
    positions_count: int
    synced_at: datetime


class EnableTradingRequest(BaseModel):
    enabled: bool = True


class APIWalletResponse(BaseSchema):
    address: str
    private_key: str
    message: str = "Store this private key securely. It will not be shown again."
