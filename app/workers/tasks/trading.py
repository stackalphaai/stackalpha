import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def sync_all_positions(self):
    try:
        asyncio.run(_sync_all_positions())
    except Exception as e:
        logger.error(f"Position sync failed: {e}")
        raise


async def _sync_all_positions():
    from app.database import get_db_context
    from app.services.trading import PositionSyncService

    async with get_db_context() as db:
        sync_service = PositionSyncService(db)
        synced_count = await sync_service.sync_all_positions()
        await db.commit()

        logger.info(f"Synced {synced_count} positions")


@celery_app.task(bind=True)
def execute_trade_task(
    self,
    user_id: str,
    wallet_id: str,
    signal_id: str,
    position_size_percent: float = None,
    leverage: int = None,
):
    try:
        asyncio.run(_execute_trade(user_id, wallet_id, signal_id, position_size_percent, leverage))
    except Exception as e:
        logger.error(f"Trade execution failed: {e}")
        raise


async def _execute_trade(
    user_id: str,
    wallet_id: str,
    signal_id: str,
    position_size_percent: float,
    leverage: int,
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import get_db_context
    from app.models import Signal, User, Wallet
    from app.services.telegram_service import TelegramService
    from app.services.trading import TradeExecutor

    async with get_db_context() as db:
        result = await db.execute(
            select(User).options(selectinload(User.telegram_connection)).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
        wallet = result.scalar_one_or_none()

        result = await db.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one_or_none()

        if not user or not wallet or not signal:
            logger.error("Trade execution failed: missing user, wallet, or signal")
            return

        executor = TradeExecutor(db)
        trade = await executor.execute_signal(
            user=user,
            wallet=wallet,
            signal=signal,
            position_size_percent=position_size_percent,
            leverage=leverage,
        )

        await db.commit()

        if user.telegram_connection and user.telegram_connection.is_verified:
            telegram_service = TelegramService(db)
            await telegram_service.send_trade_opened_notification(user.telegram_connection, trade)

        logger.info(f"Trade executed: {trade.id} for signal {signal_id}")


@celery_app.task(bind=True)
def close_trade_task(self, trade_id: str, reason: str = "manual"):
    try:
        asyncio.run(_close_trade(trade_id, reason))
    except Exception as e:
        logger.error(f"Trade close failed: {e}")
        raise


async def _close_trade(trade_id: str, reason: str):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import get_db_context
    from app.models import Trade, TradeCloseReason, User, Wallet
    from app.services.telegram_service import TelegramService
    from app.services.trading import TradeExecutor

    async with get_db_context() as db:
        result = await db.execute(
            select(Trade)
            .options(selectinload(Trade.user).selectinload(User.telegram_connection))
            .where(Trade.id == trade_id)
        )
        trade = result.scalar_one_or_none()

        if not trade:
            logger.error(f"Trade {trade_id} not found")
            return

        result = await db.execute(select(Wallet).where(Wallet.id == trade.wallet_id))
        wallet = result.scalar_one_or_none()

        if not wallet:
            logger.error(f"Wallet not found for trade {trade_id}")
            return

        close_reason = TradeCloseReason(reason)
        executor = TradeExecutor(db)
        trade = await executor.close_trade(trade, wallet, close_reason)

        await db.commit()

        if trade.user.telegram_connection and trade.user.telegram_connection.is_verified:
            telegram_service = TelegramService(db)
            await telegram_service.send_trade_closed_notification(
                trade.user.telegram_connection, trade
            )

        logger.info(f"Trade {trade_id} closed with reason: {reason}")


@celery_app.task(bind=True)
def monitor_tp_sl(self, trade_id: str):
    try:
        asyncio.run(_monitor_tp_sl(trade_id))
    except Exception as e:
        logger.error(f"TP/SL monitoring failed for trade {trade_id}: {e}")
        raise


async def _monitor_tp_sl(trade_id: str):
    from sqlalchemy import select

    from app.database import get_db_context
    from app.models import Trade, TradeCloseReason, TradeDirection, TradeStatus
    from app.services.hyperliquid import get_info_service

    async with get_db_context() as db:
        result = await db.execute(select(Trade).where(Trade.id == trade_id))
        trade = result.scalar_one_or_none()

        if not trade or trade.status != TradeStatus.OPEN:
            return

        info_service = get_info_service()
        market_data = await info_service.get_market_data(trade.symbol)
        current_price = market_data.get("mark_price", 0)

        should_close = False
        close_reason = None

        if trade.take_profit_price and trade.stop_loss_price:
            if trade.direction == TradeDirection.LONG:
                if current_price >= trade.take_profit_price:
                    should_close = True
                    close_reason = TradeCloseReason.TP_HIT
                elif current_price <= trade.stop_loss_price:
                    should_close = True
                    close_reason = TradeCloseReason.SL_HIT
            else:
                if current_price <= trade.take_profit_price:
                    should_close = True
                    close_reason = TradeCloseReason.TP_HIT
                elif current_price >= trade.stop_loss_price:
                    should_close = True
                    close_reason = TradeCloseReason.SL_HIT

        if should_close and close_reason:
            close_trade_task.delay(trade_id, close_reason.value)
            logger.info(f"Trade {trade_id} triggered {close_reason.value} at {current_price}")
