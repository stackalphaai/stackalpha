"""
Celery tasks for sending notifications.

All email sending is done asynchronously through Celery to ensure
API endpoints remain responsive and email failures don't affect
user operations.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro):
    """Helper to run async functions in Celery tasks."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# Email Notification Tasks
# =============================================================================


@celery_app.task(
    bind=True,
    name="notifications.send_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_kwargs={"max_retries": 3},
)
def send_email_task(
    self,
    to_email: str,
    template: str,
    context: dict[str, Any] | None = None,
    name: str | None = None,
) -> bool:
    """
    Send an email using a template.

    This is the primary task for sending emails asynchronously.
    It supports all email templates defined in the system.

    Args:
        to_email: Recipient email address.
        template: Template name (e.g., 'welcome', 'verification').
        context: Template context variables.
        name: Recipient name (optional).

    Returns:
        True if email was sent successfully.
    """
    try:
        return run_async(_send_template_email(to_email, template, context or {}, name))
    except Exception as e:
        logger.error(f"Email task failed for {to_email} ({template}): {e}")
        raise


async def _send_template_email(
    to_email: str,
    template: str,
    context: dict[str, Any],
    name: str | None = None,
) -> bool:
    """Internal async function to send template email."""
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_template_email(
        to_email=to_email,
        template_name=template,
        context=context,
        name=name,
    )
    logger.info(f"Email sent: {template} to {to_email}")
    return True


# Convenience tasks for specific email types


@celery_app.task(bind=True, name="notifications.send_welcome_email")
def send_welcome_email_task(self, to_email: str, name: str | None = None) -> bool:
    """Send welcome email to new user."""
    try:
        return run_async(_send_welcome_email(to_email, name))
    except Exception as e:
        logger.error(f"Welcome email failed for {to_email}: {e}")
        raise


async def _send_welcome_email(to_email: str, name: str | None = None) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_welcome_email(to_email, name)
    logger.info(f"Welcome email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_verification_email")
def send_verification_email_task(
    self,
    to_email: str,
    token: str,
    name: str | None = None,
) -> bool:
    """Send email verification email."""
    try:
        return run_async(_send_verification_email(to_email, token, name))
    except Exception as e:
        logger.error(f"Verification email failed for {to_email}: {e}")
        raise


async def _send_verification_email(
    to_email: str,
    token: str,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_verification_email(to_email, token, name)
    logger.info(f"Verification email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_password_reset_email")
def send_password_reset_email_task(
    self,
    to_email: str,
    token: str,
    name: str | None = None,
) -> bool:
    """Send password reset email."""
    try:
        return run_async(_send_password_reset_email(to_email, token, name))
    except Exception as e:
        logger.error(f"Password reset email failed for {to_email}: {e}")
        raise


async def _send_password_reset_email(
    to_email: str,
    token: str,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_password_reset_email(to_email, token, name)
    logger.info(f"Password reset email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_subscription_activated_email")
def send_subscription_activated_email_task(
    self,
    to_email: str,
    plan: str,
    expires_at_iso: str,
    name: str | None = None,
) -> bool:
    """Send subscription activated email."""
    try:
        expires_at = datetime.fromisoformat(expires_at_iso)
        return run_async(_send_subscription_activated_email(to_email, plan, expires_at, name))
    except Exception as e:
        logger.error(f"Subscription activated email failed for {to_email}: {e}")
        raise


async def _send_subscription_activated_email(
    to_email: str,
    plan: str,
    expires_at: datetime,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_subscription_activated_email(to_email, plan, expires_at, name)
    logger.info(f"Subscription activated email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_subscription_expiring_email")
def send_subscription_expiring_email_task(
    self,
    to_email: str,
    days_remaining: int,
    expires_at_iso: str,
    name: str | None = None,
) -> bool:
    """Send subscription expiring reminder email."""
    try:
        expires_at = datetime.fromisoformat(expires_at_iso)
        return run_async(
            _send_subscription_expiring_email(to_email, days_remaining, expires_at, name)
        )
    except Exception as e:
        logger.error(f"Subscription expiring email failed for {to_email}: {e}")
        raise


async def _send_subscription_expiring_email(
    to_email: str,
    days_remaining: int,
    expires_at: datetime,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_subscription_expiring_email(to_email, days_remaining, expires_at, name)
    logger.info(f"Subscription expiring email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_subscription_expired_email")
def send_subscription_expired_email_task(
    self,
    to_email: str,
    grace_period_ends_iso: str,
    name: str | None = None,
) -> bool:
    """Send subscription expired email."""
    try:
        grace_period_ends = datetime.fromisoformat(grace_period_ends_iso)
        return run_async(_send_subscription_expired_email(to_email, grace_period_ends, name))
    except Exception as e:
        logger.error(f"Subscription expired email failed for {to_email}: {e}")
        raise


async def _send_subscription_expired_email(
    to_email: str,
    grace_period_ends: datetime,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_subscription_expired_email(to_email, grace_period_ends, name)
    logger.info(f"Subscription expired email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_payment_received_email")
def send_payment_received_email_task(
    self,
    to_email: str,
    amount_usd: float,
    currency: str,
    transaction_id: str,
    payment_date_iso: str,
    plan: str | None = None,
    expires_at_iso: str | None = None,
    name: str | None = None,
) -> bool:
    """Send payment received email."""
    try:
        payment_date = datetime.fromisoformat(payment_date_iso)
        expires_at = datetime.fromisoformat(expires_at_iso) if expires_at_iso else None
        return run_async(
            _send_payment_received_email(
                to_email, amount_usd, currency, transaction_id, payment_date, plan, expires_at, name
            )
        )
    except Exception as e:
        logger.error(f"Payment received email failed for {to_email}: {e}")
        raise


async def _send_payment_received_email(
    to_email: str,
    amount_usd: float,
    currency: str,
    transaction_id: str,
    payment_date: datetime,
    plan: str | None = None,
    expires_at: datetime | None = None,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_payment_received_email(
        to_email, amount_usd, currency, transaction_id, payment_date, plan, expires_at, name
    )
    logger.info(f"Payment received email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_payment_failed_email")
def send_payment_failed_email_task(
    self,
    to_email: str,
    amount_usd: float,
    error_message: str | None = None,
    name: str | None = None,
) -> bool:
    """Send payment failed email."""
    try:
        return run_async(_send_payment_failed_email(to_email, amount_usd, error_message, name))
    except Exception as e:
        logger.error(f"Payment failed email failed for {to_email}: {e}")
        raise


async def _send_payment_failed_email(
    to_email: str,
    amount_usd: float,
    error_message: str | None = None,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_payment_failed_email(to_email, amount_usd, error_message, name)
    logger.info(f"Payment failed email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_trade_opened_email")
def send_trade_opened_email_task(
    self,
    to_email: str,
    trade_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    position_size_usd: float,
    leverage: int,
    take_profit_price: float,
    stop_loss_price: float,
    signal_confidence: float | None = None,
    signal_reason: str | None = None,
    name: str | None = None,
) -> bool:
    """Send trade opened email."""
    try:
        return run_async(
            _send_trade_opened_email(
                to_email,
                trade_id,
                symbol,
                direction,
                entry_price,
                position_size_usd,
                leverage,
                take_profit_price,
                stop_loss_price,
                signal_confidence,
                signal_reason,
                name,
            )
        )
    except Exception as e:
        logger.error(f"Trade opened email failed for {to_email}: {e}")
        raise


async def _send_trade_opened_email(
    to_email: str,
    trade_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    position_size_usd: float,
    leverage: int,
    take_profit_price: float,
    stop_loss_price: float,
    signal_confidence: float | None = None,
    signal_reason: str | None = None,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_trade_opened_email(
        to_email,
        trade_id,
        symbol,
        direction,
        entry_price,
        position_size_usd,
        leverage,
        take_profit_price,
        stop_loss_price,
        signal_confidence,
        signal_reason,
        name,
    )
    logger.info(f"Trade opened email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_trade_closed_email")
def send_trade_closed_email_task(
    self,
    to_email: str,
    trade_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    position_size_usd: float,
    leverage: int,
    pnl: float,
    pnl_percent: float,
    fees_paid: float,
    close_reason: str,
    duration_seconds: int,
    name: str | None = None,
) -> bool:
    """Send trade closed email."""
    try:
        return run_async(
            _send_trade_closed_email(
                to_email,
                trade_id,
                symbol,
                direction,
                entry_price,
                exit_price,
                position_size_usd,
                leverage,
                pnl,
                pnl_percent,
                fees_paid,
                close_reason,
                duration_seconds,
                name,
            )
        )
    except Exception as e:
        logger.error(f"Trade closed email failed for {to_email}: {e}")
        raise


async def _send_trade_closed_email(
    to_email: str,
    trade_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    position_size_usd: float,
    leverage: int,
    pnl: float,
    pnl_percent: float,
    fees_paid: float,
    close_reason: str,
    duration_seconds: int,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_trade_closed_email(
        to_email,
        trade_id,
        symbol,
        direction,
        entry_price,
        exit_price,
        position_size_usd,
        leverage,
        pnl,
        pnl_percent,
        fees_paid,
        close_reason,
        duration_seconds,
        name,
    )
    logger.info(f"Trade closed email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_affiliate_commission_email")
def send_affiliate_commission_email_task(
    self,
    to_email: str,
    commission_amount: float,
    commission_rate: float,
    original_amount: float,
    referral_email: str,
    commission_type: str,
    commission_date_iso: str,
    pending_earnings: float,
    total_earnings: float,
    referral_code: str,
    initial_commission_rate: float = 20.0,
    renewal_commission_rate: float = 5.0,
    name: str | None = None,
) -> bool:
    """Send affiliate commission email."""
    try:
        commission_date = datetime.fromisoformat(commission_date_iso)
        return run_async(
            _send_affiliate_commission_email(
                to_email,
                commission_amount,
                commission_rate,
                original_amount,
                referral_email,
                commission_type,
                commission_date,
                pending_earnings,
                total_earnings,
                referral_code,
                initial_commission_rate,
                renewal_commission_rate,
                name,
            )
        )
    except Exception as e:
        logger.error(f"Affiliate commission email failed for {to_email}: {e}")
        raise


async def _send_affiliate_commission_email(
    to_email: str,
    commission_amount: float,
    commission_rate: float,
    original_amount: float,
    referral_email: str,
    commission_type: str,
    commission_date: datetime,
    pending_earnings: float,
    total_earnings: float,
    referral_code: str,
    initial_commission_rate: float = 20.0,
    renewal_commission_rate: float = 5.0,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_affiliate_commission_email(
        to_email,
        commission_amount,
        commission_rate,
        original_amount,
        referral_email,
        commission_type,
        commission_date,
        pending_earnings,
        total_earnings,
        referral_code,
        initial_commission_rate,
        renewal_commission_rate,
        name,
    )
    logger.info(f"Affiliate commission email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_affiliate_payout_email")
def send_affiliate_payout_email_task(
    self,
    to_email: str,
    amount: float,
    currency: str,
    payout_address: str,
    status: str,
    payout_date_iso: str,
    transaction_hash: str | None = None,
    error_message: str | None = None,
    name: str | None = None,
) -> bool:
    """Send affiliate payout email."""
    try:
        payout_date = datetime.fromisoformat(payout_date_iso)
        return run_async(
            _send_affiliate_payout_email(
                to_email,
                amount,
                currency,
                payout_address,
                status,
                payout_date,
                transaction_hash,
                error_message,
                name,
            )
        )
    except Exception as e:
        logger.error(f"Affiliate payout email failed for {to_email}: {e}")
        raise


async def _send_affiliate_payout_email(
    to_email: str,
    amount: float,
    currency: str,
    payout_address: str,
    status: str,
    payout_date: datetime,
    transaction_hash: str | None = None,
    error_message: str | None = None,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_affiliate_payout_email(
        to_email,
        amount,
        currency,
        payout_address,
        status,
        payout_date,
        transaction_hash,
        error_message,
        name,
    )
    logger.info(f"Affiliate payout email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_wallet_connected_email")
def send_wallet_connected_email_task(
    self,
    to_email: str,
    wallet_address: str,
    connected_at_iso: str,
    trading_enabled: bool = True,
    name: str | None = None,
) -> bool:
    """Send wallet connected email."""
    try:
        connected_at = datetime.fromisoformat(connected_at_iso)
        return run_async(
            _send_wallet_connected_email(
                to_email, wallet_address, connected_at, trading_enabled, name
            )
        )
    except Exception as e:
        logger.error(f"Wallet connected email failed for {to_email}: {e}")
        raise


async def _send_wallet_connected_email(
    to_email: str,
    wallet_address: str,
    connected_at: datetime,
    trading_enabled: bool = True,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_wallet_connected_email(
        to_email, wallet_address, connected_at, trading_enabled, name
    )
    logger.info(f"Wallet connected email sent to {to_email}")
    return True


@celery_app.task(bind=True, name="notifications.send_security_alert_email")
def send_security_alert_email_task(
    self,
    to_email: str,
    alert_type: str,
    alert_title: str,
    alert_description: str,
    alert_time_iso: str,
    ip_address: str | None = None,
    location: str | None = None,
    device: str | None = None,
    name: str | None = None,
) -> bool:
    """Send security alert email."""
    try:
        alert_time = datetime.fromisoformat(alert_time_iso)
        return run_async(
            _send_security_alert_email(
                to_email,
                alert_type,
                alert_title,
                alert_description,
                alert_time,
                ip_address,
                location,
                device,
                name,
            )
        )
    except Exception as e:
        logger.error(f"Security alert email failed for {to_email}: {e}")
        raise


async def _send_security_alert_email(
    to_email: str,
    alert_type: str,
    alert_title: str,
    alert_description: str,
    alert_time: datetime,
    ip_address: str | None = None,
    location: str | None = None,
    device: str | None = None,
    name: str | None = None,
) -> bool:
    from app.services.email_service import get_email_service

    email_service = get_email_service()
    await email_service.send_security_alert_email(
        to_email,
        alert_type,
        alert_title,
        alert_description,
        alert_time,
        ip_address,
        location,
        device,
        name,
    )
    logger.info(f"Security alert email sent to {to_email}")
    return True


# =============================================================================
# Telegram Notification Tasks
# =============================================================================


@celery_app.task(bind=True, name="notifications.send_telegram_notification")
def send_telegram_notification_task(
    self,
    user_id: str,
    notification_type: str,
    **kwargs: Any,
) -> bool:
    """Send Telegram notification to user."""
    try:
        return run_async(_send_telegram_notification(user_id, notification_type, **kwargs))
    except Exception as e:
        logger.error(f"Telegram notification failed for user {user_id}: {e}")
        raise


async def _send_telegram_notification(
    user_id: str,
    notification_type: str,
    **kwargs: Any,
) -> bool:
    from sqlalchemy import select

    from app.models import TelegramConnection
    from app.services.telegram_service import TelegramService
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        result = await db.execute(
            select(TelegramConnection).where(
                TelegramConnection.user_id == user_id,
                TelegramConnection.is_verified,
            )
        )
        connection = result.scalar_one_or_none()

        if not connection or not connection.telegram_chat_id:
            logger.debug(f"No Telegram connection for user {user_id}")
            return False

        telegram_service = TelegramService(db)

        if notification_type == "subscription":
            await telegram_service.send_subscription_notification(
                connection,
                kwargs.get("message_type", ""),
                **kwargs,
            )
        elif notification_type == "trade":
            await telegram_service.send_trade_notification(
                connection,
                kwargs.get("trade_data", {}),
            )
        elif notification_type == "signal":
            await telegram_service.send_signal_notification(
                connection,
                kwargs.get("signal_data", {}),
            )

        logger.info(f"Telegram notification sent: {notification_type} to user {user_id}")
        return True


# =============================================================================
# Scheduled Tasks
# =============================================================================


@celery_app.task(bind=True, name="notifications.send_renewal_reminders")
def send_renewal_reminders(self) -> int:
    """
    Send subscription renewal reminders to users whose subscriptions
    are expiring within 3 days.

    This task runs daily via Celery Beat.
    """
    try:
        count = run_async(_send_renewal_reminders())
        logger.info(f"Sent {count} renewal reminders")
        return count
    except Exception as e:
        logger.error(f"Renewal reminders task failed: {e}")
        raise


async def _send_renewal_reminders() -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Subscription, SubscriptionStatus
    from app.services.email_service import get_email_service
    from app.services.telegram_service import TelegramService
    from app.workers.database import get_worker_db

    now = datetime.now(UTC)
    reminder_threshold = now + timedelta(days=3)

    async with get_worker_db() as db:
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= reminder_threshold,
                Subscription.expires_at > now,
                not Subscription.renewal_reminder_sent,
            )
        )
        subscriptions = list(result.scalars().all())

        if not subscriptions:
            return 0

        email_service = get_email_service()
        telegram_service = TelegramService(db)
        sent_count = 0

        for sub in subscriptions:
            if not sub.user:
                continue

            days_remaining = (sub.expires_at - now).days if sub.expires_at else 0

            # Send email notification
            try:
                await email_service.send_subscription_expiring_email(
                    to_email=sub.user.email,
                    days_remaining=days_remaining,
                    expires_at=sub.expires_at,
                    name=sub.user.email.split("@")[0],
                )
            except Exception as e:
                logger.error(f"Failed to send renewal email to {sub.user.email}: {e}")

            # Send Telegram notification
            connection = await telegram_service.get_connection_by_user(sub.user.id)
            if connection and connection.is_verified:
                try:
                    await telegram_service.send_subscription_notification(
                        connection,
                        "expiring",
                        days=days_remaining,
                    )
                except Exception as e:
                    logger.error(f"Failed to send Telegram reminder to user {sub.user.id}: {e}")

            sub.renewal_reminder_sent = True
            sent_count += 1

        await db.commit()

    return sent_count


@celery_app.task(bind=True, name="notifications.send_expired_subscription_emails")
def send_expired_subscription_emails(self) -> int:
    """
    Send notification emails to users whose subscriptions have just expired.

    This task runs daily via Celery Beat.
    """
    try:
        count = run_async(_send_expired_subscription_emails())
        logger.info(f"Sent {count} subscription expired emails")
        return count
    except Exception as e:
        logger.error(f"Expired subscription emails task failed: {e}")
        raise


async def _send_expired_subscription_emails() -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import settings
    from app.models import Subscription, SubscriptionStatus
    from app.services.email_service import get_email_service
    from app.workers.database import get_worker_db

    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    grace_period_days = settings.subscription_grace_period_days

    async with get_worker_db() as db:
        # Find subscriptions that expired in the last 24 hours
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                Subscription.status == SubscriptionStatus.GRACE_PERIOD,
                Subscription.expires_at <= now,
                Subscription.expires_at > yesterday,
            )
        )
        subscriptions = list(result.scalars().all())

        if not subscriptions:
            return 0

        email_service = get_email_service()
        sent_count = 0

        for sub in subscriptions:
            if not sub.user:
                continue

            grace_period_ends = sub.expires_at + timedelta(days=grace_period_days)

            try:
                await email_service.send_subscription_expired_email(
                    to_email=sub.user.email,
                    grace_period_ends=grace_period_ends,
                    name=sub.user.email.split("@")[0],
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send expired email to {sub.user.email}: {e}")

    return sent_count


@celery_app.task(bind=True, name="notifications.broadcast_notification")
def broadcast_notification_task(
    self,
    message: str,
    notification_type: str = "system",
) -> int:
    """Broadcast a notification to all users with verified Telegram connections."""
    try:
        count = run_async(_broadcast_notification(message, notification_type))
        logger.info(f"Broadcast sent to {count} users")
        return count
    except Exception as e:
        logger.error(f"Broadcast task failed: {e}")
        raise


async def _broadcast_notification(message: str, notification_type: str) -> int:
    from app.services.telegram_service import TelegramService
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        telegram_service = TelegramService(db)
        sent_count = await telegram_service.broadcast_message(message)
        return sent_count


@celery_app.task(bind=True, name="notifications.cleanup_old_notifications")
def cleanup_old_notifications(self, days: int = 30) -> int:
    """
    Clean up old notification records.

    This task runs periodically to remove notification records older than
    the specified number of days.
    """
    try:
        count = run_async(_cleanup_old_notifications(days))
        logger.info(f"Cleaned up {count} old notifications")
        return count
    except Exception as e:
        logger.error(f"Notification cleanup failed: {e}")
        raise


async def _cleanup_old_notifications(days: int) -> int:
    from sqlalchemy import delete

    from app.models import Notification
    from app.workers.database import get_worker_db

    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with get_worker_db() as db:
        result = await db.execute(delete(Notification).where(Notification.created_at < cutoff))
        await db.commit()
        return result.rowcount
