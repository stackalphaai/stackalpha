from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.wallet import WalletStatus, WalletType
from app.schemas.common import BaseSchema, TimestampMixin


class WalletBase(BaseSchema):
    address: str = Field(..., min_length=42, max_length=42)
    wallet_type: WalletType


class ConnectAgentWalletRequest(BaseModel):
    address: str = Field(..., min_length=42, max_length=42)
    private_key: str = Field(..., min_length=1)
    master_address: str = Field(..., min_length=42, max_length=42)

    @field_validator("address", "master_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v.startswith("0x"):
            raise ValueError("Invalid Ethereum address format")
        return v.lower()


class ConnectAPIWalletRequest(BaseModel):
    address: str = Field(..., min_length=42, max_length=42)
    private_key: str = Field(..., min_length=1)

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v.startswith("0x"):
            raise ValueError("Invalid Ethereum address format")
        return v.lower()


class WalletResponse(WalletBase, TimestampMixin):
    id: str
    user_id: str
    status: WalletStatus
    master_address: str | None = None
    is_trading_enabled: bool
    is_authorized: bool
    is_agent_approved: bool = False
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


class WalletTransferRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Amount in USDC to transfer")
    to_perp: bool = Field(True, description="True for Spot -> Perp, False for Perp -> Spot")


class WalletTransferResponse(BaseSchema):
    success: bool
    message: str
    amount: float
    direction: str
