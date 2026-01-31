import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import instance_state

from app.database import Base

if TYPE_CHECKING:
    from app.models.affiliate import Affiliate, AffiliateReferral
    from app.models.notification import TelegramConnection
    from app.models.subscription import Subscription
    from app.models.trade import Trade
    from app.models.wallet import Wallet


def generate_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)  # Auto-verify for DeFi
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)

    totp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    verification_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    password_reset_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    password_reset_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    login_count: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    wallets: Mapped[list["Wallet"]] = relationship(
        "Wallet", back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    trades: Mapped[list["Trade"]] = relationship(
        "Trade", back_populates="user", cascade="all, delete-orphan"
    )
    telegram_connection: Mapped[Optional["TelegramConnection"]] = relationship(
        "TelegramConnection", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    affiliate: Mapped[Optional["Affiliate"]] = relationship(
        "Affiliate", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    referred_by: Mapped[Optional["AffiliateReferral"]] = relationship(
        "AffiliateReferral",
        foreign_keys="AffiliateReferral.referred_user_id",
        back_populates="referred_user",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email})>"

    @property
    def has_active_subscription(self) -> bool:
        # Check if subscriptions relationship is loaded to avoid lazy loading in async context
        state = instance_state(self)
        if "subscriptions" not in state.dict:
            return False
        if not self.subscriptions:
            return False
        for sub in self.subscriptions:
            if sub.is_active:
                return True
        return False
