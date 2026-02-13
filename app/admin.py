"""
SQLAdmin configuration for StackAlpha Backend.
Provides a full admin interface with CRUD operations for all models.
"""

from pathlib import Path

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from app.config import settings
from app.core.security import verify_password
from app.database import AsyncSessionLocal, engine
from app.models import (
    Affiliate,
    AffiliateCommission,
    AffiliatePayout,
    AffiliateReferral,
    EmailTemplate,
    Notification,
    Payment,
    Signal,
    Subscription,
    TelegramConnection,
    Trade,
    User,
    Wallet,
)


class AdminAuth(AuthenticationBackend):
    """Authentication backend for SQLAdmin using existing User model."""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = form.get("username")
        password = form.get("password")

        if not email or not password:
            return False

        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

            if user and verify_password(str(password), user.hashed_password):
                if user.is_admin or user.is_superadmin:
                    request.session.update(
                        {
                            "admin_user_id": user.id,
                            "admin_email": user.email,
                            "is_superadmin": user.is_superadmin,
                        }
                    )
                    return True

        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> RedirectResponse | bool:
        admin_user_id = request.session.get("admin_user_id")

        if not admin_user_id:
            return RedirectResponse(request.url_for("admin:login"), status_code=302)

        return True


# ============================================================================
# User Management Views
# ============================================================================


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    category = "User Management"

    column_list = [
        User.id,
        User.email,
        User.full_name,
        User.is_active,
        User.is_verified,
        User.is_admin,
        User.is_superadmin,
        User.is_2fa_enabled,
        User.login_count,
        User.last_login,
        User.created_at,
    ]

    column_searchable_list = [User.email, User.full_name]
    column_sortable_list = [
        User.id,
        User.email,
        User.is_active,
        User.is_admin,
        User.login_count,
        User.created_at,
    ]
    column_default_sort = [(User.created_at, True)]

    form_excluded_columns = [
        User.hashed_password,
        User.totp_secret,
        User.verification_token,
        User.password_reset_token,
        User.password_reset_expires,
        User.wallets,
        User.subscriptions,
        User.trades,
        User.telegram_connection,
        User.affiliate,
        User.referred_by,
    ]

    column_details_exclude_list = [
        User.hashed_password,
        User.totp_secret,
        User.verification_token,
        User.password_reset_token,
    ]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class WalletAdmin(ModelView, model=Wallet):
    name = "Wallet"
    name_plural = "Wallets"
    icon = "fa-solid fa-wallet"
    category = "User Management"

    column_list = [
        Wallet.id,
        Wallet.user_id,
        Wallet.address,
        Wallet.wallet_type,
        Wallet.status,
        Wallet.is_trading_enabled,
        Wallet.is_authorized,
        Wallet.balance_usd,
        Wallet.margin_used,
        Wallet.unrealized_pnl,
        Wallet.last_sync_at,
        Wallet.created_at,
    ]

    column_searchable_list = [Wallet.address]
    column_sortable_list = [
        Wallet.id,
        Wallet.user_id,
        Wallet.wallet_type,
        Wallet.status,
        Wallet.balance_usd,
        Wallet.created_at,
    ]
    column_default_sort = [(Wallet.created_at, True)]

    form_excluded_columns = [
        Wallet.encrypted_private_key,
        Wallet.authorization_signature,
        Wallet.user,
    ]

    column_details_exclude_list = [
        Wallet.encrypted_private_key,
        Wallet.authorization_signature,
    ]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class TelegramConnectionAdmin(ModelView, model=TelegramConnection):
    name = "Telegram Connection"
    name_plural = "Telegram Connections"
    icon = "fa-brands fa-telegram"
    category = "User Management"

    column_list = [
        TelegramConnection.id,
        TelegramConnection.user_id,
        TelegramConnection.telegram_user_id,
        TelegramConnection.telegram_username,
        TelegramConnection.is_verified,
        TelegramConnection.is_active,
        TelegramConnection.notifications_enabled,
        TelegramConnection.signal_notifications,
        TelegramConnection.trade_notifications,
        TelegramConnection.created_at,
    ]

    column_searchable_list = [TelegramConnection.telegram_username]
    column_sortable_list = [
        TelegramConnection.id,
        TelegramConnection.user_id,
        TelegramConnection.is_verified,
        TelegramConnection.created_at,
    ]
    column_default_sort = [(TelegramConnection.created_at, True)]

    form_excluded_columns = [
        TelegramConnection.verification_code,
        TelegramConnection.verification_expires_at,
        TelegramConnection.user,
    ]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


# ============================================================================
# Subscription & Payment Views
# ============================================================================


class SubscriptionAdmin(ModelView, model=Subscription):
    name = "Subscription"
    name_plural = "Subscriptions"
    icon = "fa-solid fa-credit-card"
    category = "Subscriptions"

    column_list = [
        Subscription.id,
        Subscription.user_id,
        Subscription.plan,
        Subscription.status,
        Subscription.price_usd,
        Subscription.price_crypto,
        Subscription.crypto_currency,
        Subscription.starts_at,
        Subscription.expires_at,
        Subscription.auto_renew,
        Subscription.created_at,
    ]

    column_searchable_list = [Subscription.crypto_currency]
    column_sortable_list = [
        Subscription.id,
        Subscription.user_id,
        Subscription.plan,
        Subscription.status,
        Subscription.price_usd,
        Subscription.expires_at,
        Subscription.created_at,
    ]
    column_default_sort = [(Subscription.created_at, True)]

    form_excluded_columns = [Subscription.user, Subscription.payments]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class PaymentAdmin(ModelView, model=Payment):
    name = "Payment"
    name_plural = "Payments"
    icon = "fa-solid fa-money-bill-wave"
    category = "Subscriptions"

    column_list = [
        Payment.id,
        Payment.subscription_id,
        Payment.nowpayments_id,
        Payment.status,
        Payment.amount_usd,
        Payment.amount_crypto,
        Payment.actually_paid,
        Payment.pay_currency,
        Payment.paid_at,
        Payment.created_at,
    ]

    column_searchable_list = [Payment.nowpayments_id, Payment.nowpayments_order_id]
    column_sortable_list = [
        Payment.id,
        Payment.subscription_id,
        Payment.status,
        Payment.amount_usd,
        Payment.paid_at,
        Payment.created_at,
    ]
    column_default_sort = [(Payment.created_at, True)]

    form_excluded_columns = [Payment.subscription]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


# ============================================================================
# Trading Views
# ============================================================================


class SignalAdmin(ModelView, model=Signal):
    name = "Signal"
    name_plural = "Signals"
    icon = "fa-solid fa-chart-line"
    category = "Trading"

    column_list = [
        Signal.id,
        Signal.symbol,
        Signal.direction,
        Signal.status,
        Signal.outcome,
        Signal.entry_price,
        Signal.take_profit_price,
        Signal.stop_loss_price,
        Signal.suggested_leverage,
        Signal.confidence_score,
        Signal.consensus_votes,
        Signal.total_votes,
        Signal.actual_pnl_percent,
        Signal.created_at,
    ]

    column_searchable_list = [Signal.symbol]
    column_sortable_list = [
        Signal.id,
        Signal.symbol,
        Signal.direction,
        Signal.status,
        Signal.outcome,
        Signal.confidence_score,
        Signal.created_at,
    ]
    column_default_sort = [(Signal.created_at, True)]

    form_excluded_columns = [Signal.trades]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class TradeAdmin(ModelView, model=Trade):
    name = "Trade"
    name_plural = "Trades"
    icon = "fa-solid fa-exchange-alt"
    category = "Trading"

    column_list = [
        Trade.id,
        Trade.user_id,
        Trade.wallet_id,
        Trade.signal_id,
        Trade.symbol,
        Trade.direction,
        Trade.status,
        Trade.entry_price,
        Trade.exit_price,
        Trade.position_size_usd,
        Trade.leverage,
        Trade.realized_pnl,
        Trade.realized_pnl_percent,
        Trade.close_reason,
        Trade.opened_at,
        Trade.closed_at,
        Trade.created_at,
    ]

    column_searchable_list = [Trade.symbol, Trade.hyperliquid_order_id]
    column_sortable_list = [
        Trade.id,
        Trade.user_id,
        Trade.symbol,
        Trade.direction,
        Trade.status,
        Trade.realized_pnl,
        Trade.created_at,
    ]
    column_default_sort = [(Trade.created_at, True)]

    form_excluded_columns = [Trade.user, Trade.signal]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


# ============================================================================
# Affiliate Views
# ============================================================================


class AffiliateAdmin(ModelView, model=Affiliate):
    name = "Affiliate"
    name_plural = "Affiliates"
    icon = "fa-solid fa-handshake"
    category = "Affiliate Program"

    column_list = [
        Affiliate.id,
        Affiliate.user_id,
        Affiliate.referral_code,
        Affiliate.commission_rate,
        Affiliate.total_referrals,
        Affiliate.active_referrals,
        Affiliate.total_earnings,
        Affiliate.pending_earnings,
        Affiliate.paid_earnings,
        Affiliate.payout_currency,
        Affiliate.is_active,
        Affiliate.is_verified,
        Affiliate.created_at,
    ]

    column_searchable_list = [Affiliate.referral_code, Affiliate.payout_address]
    column_sortable_list = [
        Affiliate.id,
        Affiliate.user_id,
        Affiliate.total_referrals,
        Affiliate.total_earnings,
        Affiliate.pending_earnings,
        Affiliate.created_at,
    ]
    column_default_sort = [(Affiliate.created_at, True)]

    form_excluded_columns = [
        Affiliate.user,
        Affiliate.referrals,
        Affiliate.commissions,
        Affiliate.payouts,
    ]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class AffiliateReferralAdmin(ModelView, model=AffiliateReferral):
    name = "Affiliate Referral"
    name_plural = "Affiliate Referrals"
    icon = "fa-solid fa-user-plus"
    category = "Affiliate Program"

    column_list = [
        AffiliateReferral.id,
        AffiliateReferral.affiliate_id,
        AffiliateReferral.referred_user_id,
        AffiliateReferral.is_converted,
        AffiliateReferral.converted_at,
        AffiliateReferral.ip_address,
        AffiliateReferral.created_at,
    ]

    column_searchable_list = [AffiliateReferral.ip_address]
    column_sortable_list = [
        AffiliateReferral.id,
        AffiliateReferral.affiliate_id,
        AffiliateReferral.is_converted,
        AffiliateReferral.created_at,
    ]
    column_default_sort = [(AffiliateReferral.created_at, True)]

    form_excluded_columns = [AffiliateReferral.affiliate, AffiliateReferral.referred_user]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class AffiliateCommissionAdmin(ModelView, model=AffiliateCommission):
    name = "Affiliate Commission"
    name_plural = "Affiliate Commissions"
    icon = "fa-solid fa-coins"
    category = "Affiliate Program"

    column_list = [
        AffiliateCommission.id,
        AffiliateCommission.affiliate_id,
        AffiliateCommission.referral_id,
        AffiliateCommission.payment_id,
        AffiliateCommission.amount,
        AffiliateCommission.commission_rate,
        AffiliateCommission.original_amount,
        AffiliateCommission.is_paid,
        AffiliateCommission.paid_at,
        AffiliateCommission.created_at,
    ]

    column_sortable_list = [
        AffiliateCommission.id,
        AffiliateCommission.affiliate_id,
        AffiliateCommission.amount,
        AffiliateCommission.is_paid,
        AffiliateCommission.created_at,
    ]
    column_default_sort = [(AffiliateCommission.created_at, True)]

    form_excluded_columns = [AffiliateCommission.affiliate]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class AffiliatePayoutAdmin(ModelView, model=AffiliatePayout):
    name = "Affiliate Payout"
    name_plural = "Affiliate Payouts"
    icon = "fa-solid fa-money-check-alt"
    category = "Affiliate Program"

    column_list = [
        AffiliatePayout.id,
        AffiliatePayout.affiliate_id,
        AffiliatePayout.amount,
        AffiliatePayout.currency,
        AffiliatePayout.address,
        AffiliatePayout.status,
        AffiliatePayout.transaction_hash,
        AffiliatePayout.processed_at,
        AffiliatePayout.created_at,
    ]

    column_searchable_list = [AffiliatePayout.address, AffiliatePayout.transaction_hash]
    column_sortable_list = [
        AffiliatePayout.id,
        AffiliatePayout.affiliate_id,
        AffiliatePayout.amount,
        AffiliatePayout.status,
        AffiliatePayout.created_at,
    ]
    column_default_sort = [(AffiliatePayout.created_at, True)]

    form_excluded_columns = [AffiliatePayout.affiliate]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


# ============================================================================
# Notification Views
# ============================================================================


class NotificationAdmin(ModelView, model=Notification):
    name = "Notification"
    name_plural = "Notifications"
    icon = "fa-solid fa-bell"
    category = "Notifications"

    column_list = [
        Notification.id,
        Notification.user_id,
        Notification.type,
        Notification.channel,
        Notification.title,
        Notification.is_read,
        Notification.is_sent,
        Notification.email_sent,
        Notification.telegram_sent,
        Notification.sent_at,
        Notification.created_at,
    ]

    column_searchable_list = [Notification.title]
    column_sortable_list = [
        Notification.id,
        Notification.user_id,
        Notification.type,
        Notification.channel,
        Notification.is_sent,
        Notification.created_at,
    ]
    column_default_sort = [(Notification.created_at, True)]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


class EmailTemplateAdmin(ModelView, model=EmailTemplate):
    name = "Email Template"
    name_plural = "Email Templates"
    icon = "fa-solid fa-envelope"
    category = "Notifications"

    column_list = [
        EmailTemplate.id,
        EmailTemplate.name,
        EmailTemplate.subject,
        EmailTemplate.is_active,
        EmailTemplate.created_at,
        EmailTemplate.updated_at,
    ]

    column_searchable_list = [EmailTemplate.name, EmailTemplate.subject]
    column_sortable_list = [
        EmailTemplate.id,
        EmailTemplate.name,
        EmailTemplate.is_active,
        EmailTemplate.created_at,
    ]
    column_default_sort = [(EmailTemplate.name, False)]

    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True


def setup_admin(app):
    """Setup SQLAdmin with all model views."""
    # Get paths for custom templates and static files
    base_path = Path(__file__).parent
    templates_dir = base_path / "templates" / "admin"
    static_dir = base_path / "static"

    # Mount static files for custom CSS
    if static_dir.exists():
        app.mount("/static/custom", StaticFiles(directory=str(static_dir)), name="custom_static")

    authentication_backend = AdminAuth(secret_key=settings.secret_key)

    admin = Admin(
        app,
        engine,
        authentication_backend=authentication_backend,
        title="StackAlpha Admin",
        templates_dir=str(templates_dir) if templates_dir.exists() else None,
    )

    # User Management
    admin.add_view(UserAdmin)
    admin.add_view(WalletAdmin)
    admin.add_view(TelegramConnectionAdmin)

    # Subscriptions
    admin.add_view(SubscriptionAdmin)
    admin.add_view(PaymentAdmin)

    # Trading
    admin.add_view(SignalAdmin)
    admin.add_view(TradeAdmin)

    # Affiliate Program
    admin.add_view(AffiliateAdmin)
    admin.add_view(AffiliateReferralAdmin)
    admin.add_view(AffiliateCommissionAdmin)
    admin.add_view(AffiliatePayoutAdmin)

    # Notifications
    admin.add_view(NotificationAdmin)
    admin.add_view(EmailTemplateAdmin)

    return admin
