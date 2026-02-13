import asyncio
import logging
from datetime import UTC

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def check_subscriptions(self):
    try:
        asyncio.run(_check_subscriptions())
    except Exception as e:
        logger.error(f"Subscription check failed: {e}")
        raise


async def _check_subscriptions():
    from app.services import PaymentService
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        payment_service = PaymentService(db)
        expired_count = await payment_service.check_expired_subscriptions()
        await db.commit()

        logger.info(f"Checked subscriptions: {expired_count} expired/in grace period")


@celery_app.task(bind=True)
def expire_old_signals(self):
    try:
        asyncio.run(_expire_old_signals())
    except Exception as e:
        logger.error(f"Signal expiration failed: {e}")
        raise


async def _expire_old_signals():
    from app.services.trading import SignalService
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        signal_service = SignalService(db)
        expired_count = await signal_service.expire_old_signals()
        await db.commit()

        logger.info(f"Expired {expired_count} old signals")


@celery_app.task(bind=True)
def cleanup_old_notifications(self):
    try:
        asyncio.run(_cleanup_old_notifications())
    except Exception as e:
        logger.error(f"Notification cleanup failed: {e}")
        raise


async def _cleanup_old_notifications():
    from datetime import datetime, timedelta

    from sqlalchemy import delete

    from app.models import Notification
    from app.workers.database import get_worker_db

    cutoff = datetime.now(UTC) - timedelta(days=30)

    async with get_worker_db() as db:
        result = await db.execute(
            delete(Notification).where(
                Notification.created_at < cutoff,
                Notification.is_read,
            )
        )
        await db.commit()

        deleted_count = result.rowcount
        logger.info(f"Cleaned up {deleted_count} old notifications")


@celery_app.task(bind=True)
def sync_wallet_balances(self):
    try:
        asyncio.run(_sync_wallet_balances())
    except Exception as e:
        logger.error(f"Wallet balance sync failed: {e}")
        raise


async def _sync_wallet_balances():
    from sqlalchemy import select

    from app.models import Wallet, WalletStatus
    from app.services.trading import PositionSyncService
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        result = await db.execute(
            select(Wallet).where(
                Wallet.status == WalletStatus.ACTIVE,
                Wallet.is_authorized,
            )
        )
        wallets = list(result.scalars().all())

        sync_service = PositionSyncService(db)

        for wallet in wallets:
            try:
                await sync_service.sync_wallet_balances(wallet)
            except Exception as e:
                logger.error(f"Failed to sync wallet {wallet.id}: {e}")

        await db.commit()

        logger.info(f"Synced balances for {len(wallets)} wallets")


@celery_app.task(bind=True)
def generate_daily_report(self):
    try:
        asyncio.run(_generate_daily_report())
    except Exception as e:
        logger.error(f"Daily report generation failed: {e}")
        raise


async def _generate_daily_report():
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from app.models import Payment, PaymentStatus, Signal, Trade, TradeStatus, User
    from app.workers.database import get_worker_db

    yesterday = datetime.now(UTC) - timedelta(days=1)

    async with get_worker_db() as db:
        new_users = await db.scalar(select(func.count(User.id)).where(User.created_at >= yesterday))

        total_trades = await db.scalar(
            select(func.count(Trade.id)).where(Trade.created_at >= yesterday)
        )

        closed_trades = await db.scalar(
            select(func.count(Trade.id)).where(
                Trade.closed_at >= yesterday,
                Trade.status == TradeStatus.CLOSED,
            )
        )

        total_pnl = (
            await db.scalar(
                select(func.sum(Trade.realized_pnl)).where(
                    Trade.closed_at >= yesterday,
                    Trade.status == TradeStatus.CLOSED,
                )
            )
            or 0
        )

        new_signals = await db.scalar(
            select(func.count(Signal.id)).where(Signal.created_at >= yesterday)
        )

        revenue = (
            await db.scalar(
                select(func.sum(Payment.amount_usd)).where(
                    Payment.paid_at >= yesterday,
                    Payment.status == PaymentStatus.FINISHED,
                )
            )
            or 0
        )

        report = {
            "date": yesterday.strftime("%Y-%m-%d"),
            "new_users": new_users,
            "total_trades": total_trades,
            "closed_trades": closed_trades,
            "total_pnl": float(total_pnl),
            "new_signals": new_signals,
            "revenue_usd": float(revenue),
        }

        logger.info(f"Daily report: {report}")

        return report
