import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.signal import Signal
    from app.models.user import User


def generate_uuid() -> str:
    return str(uuid.uuid4())


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class TradeStatus(str, Enum):
    PENDING = "pending"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TradeCloseReason(str, Enum):
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    MANUAL = "manual"
    LIQUIDATION = "liquidation"
    SIGNAL_EXPIRED = "signal_expired"
    SYSTEM = "system"


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wallet_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("signals.id", ondelete="SET NULL"), nullable=True, index=True
    )

    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[TradeDirection] = mapped_column(SQLEnum(TradeDirection), nullable=False)
    status: Mapped[TradeStatus] = mapped_column(
        SQLEnum(TradeStatus), default=TradeStatus.PENDING, nullable=False, index=True
    )

    entry_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    take_profit_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_loss_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    position_size: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    position_size_usd: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, default=1)

    margin_used: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    realized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    realized_pnl_percent: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    fees_paid: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    funding_paid: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    close_reason: Mapped[TradeCloseReason | None] = mapped_column(
        SQLEnum(TradeCloseReason), nullable=True
    )

    hyperliquid_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hyperliquid_position_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    order_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    position_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="trades")
    signal: Mapped[Optional["Signal"]] = relationship("Signal", back_populates="trades")

    def __repr__(self) -> str:
        return f"<Trade(id={self.id}, symbol={self.symbol}, status={self.status})>"

    @property
    def is_open(self) -> bool:
        return self.status in [TradeStatus.OPEN, TradeStatus.OPENING, TradeStatus.CLOSING]

    @property
    def duration_seconds(self) -> int | None:
        if not self.opened_at:
            return None
        end_time = self.closed_at or datetime.now(self.opened_at.tzinfo)
        return int((end_time - self.opened_at).total_seconds())
