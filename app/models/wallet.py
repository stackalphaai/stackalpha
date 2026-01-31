import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


def generate_uuid() -> str:
    return str(uuid.uuid4())


class WalletType(str, Enum):
    MASTER = "master"
    API = "api"


class WalletStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    SUSPENDED = "suspended"


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    wallet_type: Mapped[WalletType] = mapped_column(
        SQLEnum(WalletType), default=WalletType.MASTER, nullable=False
    )
    status: Mapped[WalletStatus] = mapped_column(
        SQLEnum(WalletStatus), default=WalletStatus.PENDING, nullable=False
    )

    encrypted_private_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    balance_usd: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    margin_used: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="wallets")

    def __repr__(self) -> str:
        return f"<Wallet(id={self.id}, address={self.address}, type={self.wallet_type})>"

    @property
    def is_active(self) -> bool:
        return self.status == WalletStatus.ACTIVE and self.is_authorized

    @property
    def can_trade(self) -> bool:
        return self.is_active and self.is_trading_enabled
