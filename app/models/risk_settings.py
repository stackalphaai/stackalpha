"""Risk Management Settings Model"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PositionSizingMethod(str, PyEnum):
    FIXED_AMOUNT = "fixed_amount"
    FIXED_PERCENT = "fixed_percent"
    KELLY_CRITERION = "kelly"
    RISK_PARITY = "risk_parity"


class RiskSettings(Base):
    """User risk management settings for auto-trading"""

    __tablename__ = "risk_settings"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True
    )

    # Position Sizing
    position_sizing_method: Mapped[str] = mapped_column(
        Enum(PositionSizingMethod), nullable=False, default=PositionSizingMethod.FIXED_PERCENT
    )
    max_position_size_usd: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=10000.0
    )
    max_position_size_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=10.0
    )

    # Portfolio Limits
    max_portfolio_heat: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=50.0)
    max_open_positions: Mapped[int] = mapped_column(nullable=False, default=5)
    max_leverage: Mapped[int] = mapped_column(nullable=False, default=10)

    # Drawdown Limits
    max_daily_loss_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=500.0)
    max_daily_loss_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=5.0
    )
    max_weekly_loss_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=10.0
    )
    max_monthly_loss_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=20.0
    )

    # Risk-Reward
    min_risk_reward_ratio: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=1.5)

    # Diversification
    max_correlated_positions: Mapped[int] = mapped_column(nullable=False, default=2)
    max_single_asset_exposure_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=20.0
    )

    # Circuit Breakers
    max_consecutive_losses: Mapped[int] = mapped_column(nullable=False, default=3)
    trading_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Auto-Trading Features
    enable_trailing_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trailing_stop_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=1.5)
    enable_scale_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enable_pyramiding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_signal_confidence: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, default=0.7)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )

    # Relationships
    user = relationship("User", back_populates="risk_settings")
