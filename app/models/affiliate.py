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


class PayoutStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Affiliate(Base):
    __tablename__ = "affiliates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid, index=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    referral_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)

    commission_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=20.0)

    total_referrals: Mapped[int] = mapped_column(default=0)
    active_referrals: Mapped[int] = mapped_column(default=0)

    total_earnings: Mapped[float] = mapped_column(Numeric(20, 8), default=0.0)
    pending_earnings: Mapped[float] = mapped_column(Numeric(20, 8), default=0.0)
    paid_earnings: Mapped[float] = mapped_column(Numeric(20, 8), default=0.0)

    payout_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payout_currency: Mapped[str] = mapped_column(String(10), default="USDT")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="affiliate")
    referrals: Mapped[list["AffiliateReferral"]] = relationship(
        "AffiliateReferral", back_populates="affiliate", cascade="all, delete-orphan"
    )
    commissions: Mapped[list["AffiliateCommission"]] = relationship(
        "AffiliateCommission", back_populates="affiliate", cascade="all, delete-orphan"
    )
    payouts: Mapped[list["AffiliatePayout"]] = relationship(
        "AffiliatePayout", back_populates="affiliate", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Affiliate(id={self.id}, code={self.referral_code})>"


class AffiliateReferral(Base):
    __tablename__ = "affiliate_referrals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid, index=True)
    affiliate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    referred_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    is_converted: Mapped[bool] = mapped_column(Boolean, default=False)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    affiliate: Mapped["Affiliate"] = relationship("Affiliate", back_populates="referrals")
    referred_user: Mapped["User"] = relationship(
        "User", foreign_keys=[referred_user_id], back_populates="referred_by"
    )

    def __repr__(self) -> str:
        return f"<AffiliateReferral(id={self.id}, affiliate_id={self.affiliate_id})>"


class AffiliateCommission(Base):
    __tablename__ = "affiliate_commissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid, index=True)
    affiliate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    referral_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("affiliate_referrals.id", ondelete="CASCADE"), nullable=False
    )
    payment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )

    amount: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    commission_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    original_amount: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)

    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    affiliate: Mapped["Affiliate"] = relationship("Affiliate", back_populates="commissions")

    def __repr__(self) -> str:
        return f"<AffiliateCommission(id={self.id}, amount={self.amount})>"


class AffiliatePayout(Base):
    __tablename__ = "affiliate_payouts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid, index=True)
    affiliate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    amount: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[PayoutStatus] = mapped_column(
        SQLEnum(PayoutStatus), default=PayoutStatus.PENDING, nullable=False
    )

    transaction_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    affiliate: Mapped["Affiliate"] = relationship("Affiliate", back_populates="payouts")

    def __repr__(self) -> str:
        return f"<AffiliatePayout(id={self.id}, amount={self.amount}, status={self.status})>"
