from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import BaseSchema, TimestampMixin


class UserBase(BaseSchema):
    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=255)
    email: EmailStr | None = None


class UserResponse(UserBase, TimestampMixin):
    id: str
    is_active: bool
    is_verified: bool
    is_2fa_enabled: bool
    is_subscribed: bool = False
    has_active_subscription: bool = False
    last_login: datetime | None = None


class UserProfileResponse(UserResponse):
    login_count: int
    wallet_count: int = 0
    trade_count: int = 0
    is_affiliate: bool = False
    referral_code: str | None = None


class UserListResponse(BaseSchema):
    id: str
    email: EmailStr
    full_name: str | None
    is_active: bool
    is_verified: bool
    created_at: datetime


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    is_verified: bool | None = None
    is_admin: bool | None = None


class UserStatsResponse(BaseSchema):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    average_trade_duration: int | None = None
    best_trade_pnl: float
    worst_trade_pnl: float
