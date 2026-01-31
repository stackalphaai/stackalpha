import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.trade import Trade


def generate_uuid() -> str:
    return str(uuid.uuid4())


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class SignalStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXECUTED = "executed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SignalOutcome(str, Enum):
    PENDING = "pending"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    MANUAL_CLOSE = "manual_close"
    EXPIRED = "expired"


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid, index=True
    )

    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[SignalDirection] = mapped_column(SQLEnum(SignalDirection), nullable=False)
    status: Mapped[SignalStatus] = mapped_column(
        SQLEnum(SignalStatus), default=SignalStatus.PENDING, nullable=False
    )
    outcome: Mapped[SignalOutcome] = mapped_column(
        SQLEnum(SignalOutcome), default=SignalOutcome.PENDING, nullable=False
    )

    entry_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    take_profit_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    stop_loss_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)

    suggested_leverage: Mapped[int] = mapped_column(Integer, default=5)
    suggested_position_size_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=5.0)

    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    consensus_votes: Mapped[int] = mapped_column(Integer, default=0)
    total_votes: Mapped[int] = mapped_column(Integer, default=0)

    analysis_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    llm_responses: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    technical_indicators: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    market_price_at_creation: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)

    actual_exit_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    actual_pnl_percent: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    trades: Mapped[list["Trade"]] = relationship(
        "Trade", back_populates="signal", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Signal(id={self.id}, symbol={self.symbol}, direction={self.direction})>"

    @property
    def is_valid(self) -> bool:
        if self.status != SignalStatus.ACTIVE:
            return False
        if self.expires_at and datetime.now(self.expires_at.tzinfo) > self.expires_at:
            return False
        return True

    @property
    def risk_reward_ratio(self) -> float:
        if self.direction == SignalDirection.LONG:
            risk = self.entry_price - self.stop_loss_price
            reward = self.take_profit_price - self.entry_price
        else:
            risk = self.stop_loss_price - self.entry_price
            reward = self.entry_price - self.take_profit_price

        if risk == 0:
            return 0.0
        return float(reward / risk)
