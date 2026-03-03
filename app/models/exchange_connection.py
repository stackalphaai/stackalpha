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
    UniqueConstraint,
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


class ExchangeType(str, Enum):
    BINANCE = "binance"


class ExchangeConnectionStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class ExchangeConnection(Base):
    __tablename__ = "exchange_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "exchange_type", "is_testnet", name="uq_user_exchange_testnet"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid, index=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    exchange_type: Mapped[ExchangeType] = mapped_column(
        SQLEnum(ExchangeType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_api_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_testnet: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[ExchangeConnectionStatus] = mapped_column(
        SQLEnum(
            ExchangeConnectionStatus,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ExchangeConnectionStatus.ACTIVE,
        nullable=False,
    )

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

    user: Mapped["User"] = relationship("User", back_populates="exchange_connections")

    def __repr__(self) -> str:
        return (
            f"<ExchangeConnection(id={self.id}, type={self.exchange_type}, "
            f"testnet={self.is_testnet})>"
        )

    @property
    def is_active(self) -> bool:
        return self.status == ExchangeConnectionStatus.ACTIVE

    @property
    def can_trade(self) -> bool:
        return self.is_active and self.is_trading_enabled
