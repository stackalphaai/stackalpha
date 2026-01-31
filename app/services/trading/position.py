import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db_context
from app.models import Trade, TradeCloseReason, TradeDirection, TradeStatus, Wallet
from app.services.hyperliquid import get_info_service, get_ws_manager

logger = logging.getLogger(__name__)


class PositionMonitor:
    def __init__(self):
        self.info_service = get_info_service()
        self.ws_manager = get_ws_manager()
        self._monitoring_tasks: dict[int, asyncio.Task] = {}
        self._callbacks: dict[str, list[Callable]] = {}

    async def start_monitoring(self, trade: Trade, wallet: Wallet):
        if trade.id in self._monitoring_tasks:
            return

        task = asyncio.create_task(self._monitor_trade(trade, wallet))
        self._monitoring_tasks[trade.id] = task
        logger.info(f"Started monitoring trade {trade.id}")

    async def stop_monitoring(self, trade_id: str):
        if trade_id in self._monitoring_tasks:
            self._monitoring_tasks[trade_id].cancel()
            try:
                await self._monitoring_tasks[trade_id]
            except asyncio.CancelledError:
                pass
            del self._monitoring_tasks[trade_id]
            logger.info(f"Stopped monitoring trade {trade_id}")

    async def stop_all(self):
        for trade_id in list(self._monitoring_tasks.keys()):
            await self.stop_monitoring(trade_id)

    def on_tp_hit(self, callback: Callable):
        self._callbacks.setdefault("tp_hit", []).append(callback)

    def on_sl_hit(self, callback: Callable):
        self._callbacks.setdefault("sl_hit", []).append(callback)

    def on_position_closed(self, callback: Callable):
        self._callbacks.setdefault("position_closed", []).append(callback)

    async def _monitor_trade(self, trade: Trade, wallet: Wallet):
        while True:
            try:
                await asyncio.sleep(5)

                positions = await self.info_service.get_user_positions(wallet.address)
                position = next((p for p in positions if p.get("symbol") == trade.symbol), None)

                if not position:
                    await self._handle_position_closed(trade)
                    break

                current_price = position.get("mark_price", 0)
                unrealized_pnl = position.get("unrealized_pnl", 0)

                if trade.take_profit_price and trade.stop_loss_price:
                    close_reason = self._check_tp_sl(trade, current_price)

                    if close_reason:
                        await self._handle_close_trigger(trade, close_reason, current_price)
                        break

                async with get_db_context() as db:
                    result = await db.execute(select(Trade).where(Trade.id == trade.id))
                    db_trade = result.scalar_one_or_none()
                    if db_trade:
                        db_trade.unrealized_pnl = unrealized_pnl
                        db_trade.position_data = position

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error monitoring trade {trade.id}: {e}")
                await asyncio.sleep(10)

    def _check_tp_sl(
        self,
        trade: Trade,
        current_price: float,
    ) -> TradeCloseReason | None:
        if not trade.take_profit_price or not trade.stop_loss_price:
            return None

        if trade.direction == TradeDirection.LONG:
            if current_price >= trade.take_profit_price:
                return TradeCloseReason.TP_HIT
            if current_price <= trade.stop_loss_price:
                return TradeCloseReason.SL_HIT
        else:
            if current_price <= trade.take_profit_price:
                return TradeCloseReason.TP_HIT
            if current_price >= trade.stop_loss_price:
                return TradeCloseReason.SL_HIT

        return None

    async def _handle_close_trigger(
        self,
        trade: Trade,
        reason: TradeCloseReason,
        price: float,
    ):
        logger.info(f"Trade {trade.id} triggered {reason.value} at price {price}")

        event = "tp_hit" if reason == TradeCloseReason.TP_HIT else "sl_hit"
        await self._emit_event(event, trade, price, reason)

    async def _handle_position_closed(self, trade: Trade):
        logger.info(f"Trade {trade.id} position closed externally")
        await self._emit_event("position_closed", trade, None, None)

    async def _emit_event(
        self,
        event: str,
        trade: Trade,
        price: float | None,
        reason: TradeCloseReason | None,
    ):
        callbacks = self._callbacks.get(event, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(trade, price, reason)
                else:
                    callback(trade, price, reason)
            except Exception as e:
                logger.error(f"Error in {event} callback: {e}")


class PositionSyncService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.info_service = get_info_service()

    async def sync_all_positions(self) -> int:
        result = await self.db.execute(
            select(Trade).options(selectinload(Trade.user)).where(Trade.status == TradeStatus.OPEN)
        )
        open_trades = list(result.scalars().all())

        synced_count = 0
        for trade in open_trades:
            try:
                await self.sync_trade_position(trade)
                synced_count += 1
            except Exception as e:
                logger.error(f"Error syncing trade {trade.id}: {e}")

        return synced_count

    async def sync_trade_position(self, trade: Trade) -> Trade:
        result = await self.db.execute(select(Wallet).where(Wallet.id == trade.wallet_id))
        wallet = result.scalar_one_or_none()

        if not wallet:
            return trade

        positions = await self.info_service.get_user_positions(wallet.address)
        position = next((p for p in positions if p.get("symbol") == trade.symbol), None)

        if position:
            trade.unrealized_pnl = position.get("unrealized_pnl")
            trade.position_data = position

            if not trade.entry_price:
                trade.entry_price = position.get("entry_price")
        else:
            if trade.status == TradeStatus.OPEN:
                trade.status = TradeStatus.CLOSED
                trade.closed_at = datetime.now(UTC)

                if not trade.close_reason:
                    trade.close_reason = TradeCloseReason.SYSTEM

        return trade

    async def sync_wallet_balances(self, wallet: Wallet) -> dict:
        balance = await self.info_service.get_user_balance(wallet.address)

        wallet.balance_usd = balance.get("balance_usd", 0)
        wallet.margin_used = balance.get("margin_used", 0)
        wallet.unrealized_pnl = balance.get("unrealized_pnl", 0)
        wallet.last_sync_at = datetime.now(UTC)

        return balance


_position_monitor_instance: PositionMonitor | None = None


def get_position_monitor() -> PositionMonitor:
    global _position_monitor_instance
    if _position_monitor_instance is None:
        _position_monitor_instance = PositionMonitor()
    return _position_monitor_instance
