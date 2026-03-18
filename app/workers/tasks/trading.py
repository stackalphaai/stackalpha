import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class _TaskDisabledError(Exception):
    """Raised when a task is disabled via admin config."""


@celery_app.task(bind=True)
def sync_all_positions(self):
    try:
        asyncio.run(_sync_all_positions())
    except _TaskDisabledError:
        logger.info("sync_all_positions is disabled — skipping")
    except Exception as e:
        logger.error(f"Position sync failed: {e}")
        raise


async def _sync_all_positions():
    from app.services.trading import PositionSyncService
    from app.workers.database import get_worker_db
    from app.workers.task_guard import is_task_enabled

    async with get_worker_db() as db:
        if not await is_task_enabled(db, "app.workers.tasks.trading.sync_all_positions"):
            raise _TaskDisabledError()
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

    from app.core.exceptions import RiskLimitError
    from app.models import Signal, User, Wallet
    from app.services.telegram_service import TelegramService
    from app.services.trading import TradeExecutor
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
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

        try:
            trade = await executor.execute_signal(
                user=user,
                wallet=wallet,
                signal=signal,
                position_size_percent=position_size_percent,
                leverage=leverage,
            )
        except RiskLimitError as e:
            logger.info(
                f"Trade for signal {signal_id} rejected by risk management "
                f"for user {user_id}: {e.detail}"
            )
            return

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

    from app.models import Trade, TradeCloseReason, User, Wallet
    from app.services.telegram_service import TelegramService
    from app.services.trading import TradeExecutor
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
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
            conn = trade.user.telegram_connection
            if close_reason == TradeCloseReason.TP_HIT:
                await telegram_service.send_tp_hit_notification(conn, trade)
            elif close_reason == TradeCloseReason.SL_HIT:
                await telegram_service.send_sl_hit_notification(conn, trade)
            else:
                await telegram_service.send_trade_closed_notification(conn, trade)

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

    from app.models import Trade, TradeCloseReason, TradeDirection, TradeStatus
    from app.services.hyperliquid import get_info_service
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
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


# ---------------------------------------------------------------------------
# Hyperliquid auto-execution
# ---------------------------------------------------------------------------


@celery_app.task(bind=True)
def auto_execute_hyperliquid_signal(self, signal_id: str):
    """Auto-execute a Hyperliquid signal for all subscribed users with active wallets."""
    try:
        asyncio.run(_auto_execute_hyperliquid_signal(signal_id))
    except Exception as e:
        logger.error(f"Hyperliquid auto-execute failed for signal {signal_id}: {e}")
        raise


async def _auto_execute_hyperliquid_signal(signal_id: str):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.exceptions import RiskLimitError
    from app.models import Signal, User
    from app.models.wallet import Wallet, WalletStatus
    from app.services.telegram_service import TelegramService
    from app.services.trading import TradeExecutor
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        result = await db.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one_or_none()

        if not signal:
            logger.error(f"Signal {signal_id} not found for auto-execution")
            return

        # Get all subscribed users with active Hyperliquid wallets that can trade
        result = await db.execute(
            select(User)
            .join(Wallet, Wallet.user_id == User.id)
            .options(
                selectinload(User.wallets),
                selectinload(User.telegram_connection),
            )
            .where(
                User.is_active.is_(True),
                User.is_subscribed.is_(True),
                Wallet.status == WalletStatus.ACTIVE,
                Wallet.is_authorized.is_(True),
                Wallet.is_trading_enabled.is_(True),
            )
            .distinct()
        )
        users = list(result.scalars().all())

        if not users:
            logger.info("No subscribed users with active Hyperliquid trading wallets")
            return

        logger.info(f"Auto-executing Hyperliquid signal {signal_id} for {len(users)} users")

        executor = TradeExecutor(db)
        telegram_service = TelegramService(db)

        for user in users:
            # Find the first wallet that can trade
            wallet = next(
                (w for w in user.wallets if w.can_trade),
                None,
            )
            if not wallet:
                logger.warning(
                    f"User {user.id} matched query but has no tradeable wallet — skipping"
                )
                continue

            try:
                trade = await executor.execute_signal(
                    user=user,
                    wallet=wallet,
                    signal=signal,
                )

                if (
                    user.telegram_connection
                    and user.telegram_connection.is_verified
                    and user.telegram_connection.trade_notifications
                ):
                    try:
                        await telegram_service.send_trade_opened_notification(
                            user.telegram_connection, trade
                        )
                    except Exception as e:
                        logger.error(f"Failed to send trade notification for user {user.id}: {e}")

                logger.info(f"Hyperliquid trade executed for user {user.id}: {trade.id}")

            except RiskLimitError as e:
                logger.info(
                    f"Hyperliquid signal {signal_id} rejected by risk management "
                    f"for user {user.id}: {e.detail}"
                )
                continue

            except Exception as e:
                logger.error(
                    f"Failed to auto-execute Hyperliquid signal for user {user.id}: {e}",
                    exc_info=True,
                )
                continue

        await db.commit()


# ---------------------------------------------------------------------------
# Binance-specific tasks
# ---------------------------------------------------------------------------


@celery_app.task(bind=True)
def auto_execute_binance_signal(self, signal_id: str):
    """Auto-execute a Binance signal for all subscribed users with active connections."""
    try:
        asyncio.run(_auto_execute_binance_signal(signal_id))
    except Exception as e:
        logger.error(f"Binance auto-execute failed for signal {signal_id}: {e}")
        raise


async def _auto_execute_binance_signal(signal_id: str):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.exceptions import RiskLimitError
    from app.models import Signal, User
    from app.models.exchange_connection import (
        ExchangeConnection,
        ExchangeConnectionStatus,
        ExchangeType,
    )
    from app.services.telegram_service import TelegramService
    from app.services.trading.binance_executor import BinanceTradeExecutor
    from app.workers.database import get_worker_db

    async with get_worker_db() as db:
        result = await db.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one_or_none()

        if not signal:
            logger.error(f"Signal {signal_id} not found for auto-execution")
            return

        # Get all users with active BINANCE connections that have trading enabled
        result = await db.execute(
            select(User)
            .join(ExchangeConnection, ExchangeConnection.user_id == User.id)
            .options(
                selectinload(User.exchange_connections),
                selectinload(User.telegram_connection),
            )
            .where(
                User.is_active.is_(True),
                User.is_subscribed.is_(True),
                ExchangeConnection.exchange_type == ExchangeType.BINANCE,
                ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE,
                ExchangeConnection.is_trading_enabled.is_(True),
            )
            .distinct()
        )
        users = list(result.scalars().all())

        if not users:
            logger.info("No subscribed users with active Binance trading connections")
            return

        logger.info(f"Auto-executing Binance signal {signal_id} for {len(users)} users")

        executor = BinanceTradeExecutor(db)
        telegram_service = TelegramService(db)

        for user in users:
            # Find the active Binance trading connection for this user
            connection = next(
                (
                    c
                    for c in user.exchange_connections
                    if c.can_trade
                    and c.status == ExchangeConnectionStatus.ACTIVE
                    and c.exchange_type == ExchangeType.BINANCE
                ),
                None,
            )
            if not connection:
                logger.warning(
                    f"User {user.id} matched query but has no tradeable Binance connection — skipping"
                )
                continue

            try:
                trade = await executor.execute_signal(
                    user=user,
                    exchange_connection=connection,
                    signal=signal,
                )

                if (
                    user.telegram_connection
                    and user.telegram_connection.is_verified
                    and user.telegram_connection.trade_notifications
                ):
                    try:
                        await telegram_service.send_trade_opened_notification(
                            user.telegram_connection, trade
                        )
                    except Exception as e:
                        logger.error(f"Failed to send trade notification for user {user.id}: {e}")

                logger.info(f"Binance trade executed for user {user.id}: {trade.id}")

            except RiskLimitError as e:
                # Risk rejections are expected — log as info, not error
                logger.info(
                    f"Binance signal {signal_id} rejected by risk management "
                    f"for user {user.id}: {e.detail}"
                )
                continue

            except Exception as e:
                logger.error(
                    f"Failed to auto-execute Binance signal for user {user.id}: {e}",
                    exc_info=True,
                )
                continue

        await db.commit()


@celery_app.task(bind=True)
def monitor_binance_tpsl(self):
    """Monitor Binance trades for TP/SL fills and cancel the remaining order."""
    try:
        asyncio.run(_monitor_binance_tpsl())
    except _TaskDisabledError:
        logger.info("monitor_binance_tpsl is disabled — skipping")
    except Exception as e:
        logger.error(f"Binance TP/SL monitoring failed: {e}")
        raise


async def _monitor_binance_tpsl():
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Trade, TradeCloseReason, TradeStatus, User
    from app.services.binance import create_binance_exchange_service, get_binance_info_service
    from app.services.binance.utils import to_binance_symbol
    from app.services.telegram_service import TelegramService
    from app.workers.database import get_worker_db
    from app.workers.task_guard import is_task_enabled

    async with get_worker_db() as db:
        if not await is_task_enabled(db, "app.workers.tasks.trading.monitor_binance_tpsl"):
            raise _TaskDisabledError()
        # Get all open Binance trades with TP/SL orders
        result = await db.execute(
            select(Trade)
            .options(
                selectinload(Trade.user).selectinload(User.telegram_connection),
                selectinload(Trade.exchange_connection),
            )
            .where(
                Trade.exchange == "binance",
                Trade.status == TradeStatus.OPEN,
            )
        )
        trades = list(result.scalars().all())

        if not trades:
            return

        info_service = get_binance_info_service()
        telegram_service = TelegramService(db)
        closed_count = 0

        for trade in trades:
            if not trade.exchange_connection:
                continue

            binance_symbol = to_binance_symbol(trade.symbol)

            try:
                # Check if TP or SL has been filled by querying open algo orders
                binance_exchange = await create_binance_exchange_service(trade.exchange_connection)

                try:
                    open_algos = await binance_exchange.get_algo_open_orders(binance_symbol)
                except Exception:
                    open_algos = []
                finally:
                    await binance_exchange.close()

                open_algo_ids = {str(o.get("algoId", "")) for o in open_algos}

                tp_active = trade.tp_order_id and trade.tp_order_id in open_algo_ids
                sl_active = trade.sl_order_id and trade.sl_order_id in open_algo_ids

                close_reason = None

                if trade.tp_order_id and trade.sl_order_id:
                    if not tp_active and sl_active:
                        # TP was filled, cancel SL
                        close_reason = TradeCloseReason.TP_HIT
                    elif tp_active and not sl_active:
                        # SL was filled, cancel TP
                        close_reason = TradeCloseReason.SL_HIT
                    elif not tp_active and not sl_active:
                        # Both gone — position closed externally
                        close_reason = TradeCloseReason.SYSTEM
                    # else: both still active, nothing to do
                else:
                    # No algo order IDs stored (e.g. Binance returned null orderId).
                    # TP/SL orders still work on Binance via closePosition=true, but we
                    # can't track them by ID. Fall back to checking if the actual
                    # position still exists on the exchange.
                    try:
                        binance_exchange_check = await create_binance_exchange_service(
                            trade.exchange_connection
                        )
                        try:
                            open_positions = await binance_exchange_check.get_positions()
                        finally:
                            await binance_exchange_check.close()
                        open_symbols = {p["symbol"] for p in open_positions}
                        if binance_symbol not in open_symbols:
                            # Position is gone — closed by TP/SL or externally
                            close_reason = TradeCloseReason.SYSTEM
                    except Exception as e:
                        logger.warning(f"Could not check position for trade {trade.id}: {e}")

                if close_reason:
                    # Cancel the remaining order
                    binance_exchange = await create_binance_exchange_service(
                        trade.exchange_connection
                    )
                    try:
                        if close_reason == TradeCloseReason.TP_HIT and trade.sl_order_id:
                            try:
                                await binance_exchange.cancel_algo_order(
                                    binance_symbol, int(trade.sl_order_id)
                                )
                            except Exception as e:
                                logger.warning(f"Failed to cancel SL order: {e}")

                        elif close_reason == TradeCloseReason.SL_HIT and trade.tp_order_id:
                            try:
                                await binance_exchange.cancel_algo_order(
                                    binance_symbol, int(trade.tp_order_id)
                                )
                            except Exception as e:
                                logger.warning(f"Failed to cancel TP order: {e}")
                    finally:
                        await binance_exchange.close()

                    # Get exit price — skip closing if we can't fetch it
                    try:
                        market_data = await info_service.get_market_data(binance_symbol)
                        exit_price = market_data.get("mark_price", 0)
                    except Exception as e:
                        logger.warning(
                            f"Failed to get market data for {binance_symbol}, "
                            f"skipping close for trade {trade.id}: {e}"
                        )
                        continue

                    if not exit_price:
                        logger.warning(
                            f"Got zero exit price for {binance_symbol}, "
                            f"skipping close for trade {trade.id}"
                        )
                        continue

                    trade.exit_price = exit_price
                    trade.status = TradeStatus.CLOSED
                    trade.close_reason = close_reason
                    trade.closed_at = datetime.now(UTC)

                    # Calculate PnL
                    if trade.entry_price and exit_price:
                        from app.models import TradeDirection

                        if trade.direction == TradeDirection.LONG:
                            pnl = (exit_price - float(trade.entry_price)) * float(
                                trade.position_size
                            )
                            pnl_pct = (
                                (exit_price - float(trade.entry_price))
                                / float(trade.entry_price)
                                * 100
                            )
                        else:
                            pnl = (float(trade.entry_price) - exit_price) * float(
                                trade.position_size
                            )
                            pnl_pct = (
                                (float(trade.entry_price) - exit_price)
                                / float(trade.entry_price)
                                * 100
                            )

                        trade.realized_pnl = pnl
                        trade.realized_pnl_percent = pnl_pct * trade.leverage

                    closed_count += 1

                    logger.info(
                        f"Binance trade {trade.id} closed: {close_reason.value} "
                        f"exit_price={exit_price}"
                    )

                    # Send Telegram notification
                    if (
                        trade.user
                        and trade.user.telegram_connection
                        and trade.user.telegram_connection.is_verified
                    ):
                        try:
                            conn = trade.user.telegram_connection
                            if close_reason == TradeCloseReason.TP_HIT:
                                await telegram_service.send_tp_hit_notification(conn, trade)
                            elif close_reason == TradeCloseReason.SL_HIT:
                                await telegram_service.send_sl_hit_notification(conn, trade)
                            else:
                                await telegram_service.send_trade_closed_notification(conn, trade)
                        except Exception as e:
                            logger.error(f"Failed to send close notification: {e}")

            except Exception as e:
                logger.error(f"Error monitoring Binance trade {trade.id}: {e}", exc_info=True)
                continue

        if closed_count > 0:
            await db.commit()
            logger.info(f"Binance TP/SL monitor: closed {closed_count} trades")


@celery_app.task(bind=True)
def sync_binance_positions(self):
    """Sync balance for all active Binance exchange connections."""
    try:
        asyncio.run(_sync_binance_positions())
    except _TaskDisabledError:
        logger.info("sync_binance_positions is disabled — skipping")
    except Exception as e:
        logger.error(f"Binance position sync failed: {e}")
        raise


async def _sync_binance_positions():
    from sqlalchemy import select

    from app.models.exchange_connection import ExchangeConnection, ExchangeConnectionStatus
    from app.services.exchange_connection_service import ExchangeConnectionService
    from app.workers.database import get_worker_db
    from app.workers.task_guard import is_task_enabled

    async with get_worker_db() as db:
        if not await is_task_enabled(db, "app.workers.tasks.trading.sync_binance_positions"):
            raise _TaskDisabledError()
        result = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE,
            )
        )
        connections = list(result.scalars().all())

        if not connections:
            return

        service = ExchangeConnectionService(db)
        synced = 0

        for connection in connections:
            try:
                await service.sync_balance(connection)
                synced += 1
            except Exception as e:
                logger.error(f"Failed to sync Binance connection {connection.id}: {e}")
                continue

        await db.commit()

        if synced > 0:
            logger.info(f"Synced {synced}/{len(connections)} Binance connections")
