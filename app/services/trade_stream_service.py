import asyncio
import json
import logging
import time

from fastapi import WebSocket
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Trade, TradeDirection, TradeStatus
from app.services.binance.price_service import get_binance_price_service
from app.services.top_gainers_service import get_top_gainers_service

logger = logging.getLogger(__name__)


class TradeStreamService:
    """
    Real-time trade streaming service.

    For each connected user, fetches their open trades from the DB,
    combines with live prices from TopGainersService (Hyperliquid)
    and BinancePriceService, calculates unrealized PnL, and pushes
    updates via WebSocket every 2 seconds.
    """

    def __init__(self):
        self._user_clients: dict[str, set[WebSocket]] = {}
        self._running = False
        self._broadcast_task: asyncio.Task | None = None
        self._last_payloads: dict[str, str] = {}

    async def start(self):
        if self._running:
            return
        self._running = True
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        logger.info("TradeStreamService started")

    async def stop(self):
        self._running = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        for clients in self._user_clients.values():
            for client in list(clients):
                try:
                    await client.close()
                except Exception:
                    pass
        self._user_clients.clear()
        logger.info("TradeStreamService stopped")

    async def register_client(self, user_id: str, websocket: WebSocket):
        if user_id not in self._user_clients:
            self._user_clients[user_id] = set()
        self._user_clients[user_id].add(websocket)
        logger.info(
            f"Trade stream client registered for user {user_id}. "
            f"Total users: {len(self._user_clients)}"
        )

        # Send initial data immediately
        try:
            payload = await self._build_user_payload(user_id)
            if payload:
                await websocket.send_text(payload)
        except Exception:
            self._user_clients.get(user_id, set()).discard(websocket)

    def unregister_client(self, user_id: str, websocket: WebSocket):
        if user_id in self._user_clients:
            self._user_clients[user_id].discard(websocket)
            if not self._user_clients[user_id]:
                del self._user_clients[user_id]
                self._last_payloads.pop(user_id, None)
        logger.info(
            f"Trade stream client disconnected for user {user_id}. "
            f"Total users: {len(self._user_clients)}"
        )

    async def _broadcast_loop(self):
        while self._running:
            try:
                await asyncio.sleep(2)

                if not self._user_clients:
                    continue

                # Update Binance tracked symbols based on all connected users' trades
                await self._update_binance_tracked_symbols()

                # Broadcast to each connected user
                for user_id in list(self._user_clients.keys()):
                    clients = self._user_clients.get(user_id, set())
                    if not clients:
                        continue

                    try:
                        payload = await self._build_user_payload(user_id)
                        if not payload:
                            continue

                        # Skip if nothing changed
                        if payload == self._last_payloads.get(user_id):
                            continue
                        self._last_payloads[user_id] = payload

                        disconnected = set()
                        for client in clients:
                            try:
                                await client.send_text(payload)
                            except Exception:
                                disconnected.add(client)

                        for client in disconnected:
                            clients.discard(client)
                        if not clients:
                            self._user_clients.pop(user_id, None)
                            self._last_payloads.pop(user_id, None)

                    except Exception as e:
                        logger.error(f"Error broadcasting to user {user_id}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TradeStreamService broadcast error: {e}")
                await asyncio.sleep(2)

    async def _build_user_payload(self, user_id: str) -> str | None:
        """Build the JSON payload for a specific user's open trades."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Trade).where(
                        Trade.user_id == user_id,
                        Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
                    )
                )
                trades = list(result.scalars().all())

            if not trades:
                return json.dumps(
                    {
                        "type": "trades_update",
                        "timestamp": time.time(),
                        "data": {
                            "trades": [],
                            "summary": {
                                "total_open": 0,
                                "total_unrealized_pnl": 0,
                                "total_margin_used": 0,
                            },
                        },
                    }
                )

            top_gainers_svc = get_top_gainers_service()
            hl_prices = top_gainers_svc.get_mid_prices()

            binance_price_svc = get_binance_price_service()
            binance_prices = binance_price_svc.get_prices()

            trade_data = []
            total_unrealized_pnl = 0.0
            total_margin_used = 0.0

            for trade in trades:
                exchange = trade.exchange or "hyperliquid"
                symbol = trade.symbol

                # Get current price from the appropriate source
                if exchange == "binance":
                    current_price = binance_prices.get(symbol, 0)
                else:
                    current_price = hl_prices.get(symbol, 0)

                entry_price = float(trade.entry_price) if trade.entry_price else 0
                position_size = float(trade.position_size) if trade.position_size else 0
                leverage = trade.leverage or 1
                tp_price = float(trade.take_profit_price) if trade.take_profit_price else None
                sl_price = float(trade.stop_loss_price) if trade.stop_loss_price else None

                # Calculate unrealized PnL
                unrealized_pnl = 0.0
                unrealized_pnl_pct = 0.0
                if current_price > 0 and entry_price > 0 and position_size > 0:
                    if trade.direction == TradeDirection.LONG:
                        raw_pnl = (current_price - entry_price) * position_size
                    else:
                        raw_pnl = (entry_price - current_price) * position_size

                    unrealized_pnl = raw_pnl * leverage
                    cost_basis = entry_price * position_size
                    if cost_basis > 0:
                        unrealized_pnl_pct = (raw_pnl / cost_basis) * 100 * leverage

                # TP/SL distance percentages
                tp_distance_pct = None
                sl_distance_pct = None
                if current_price > 0:
                    if tp_price:
                        if trade.direction == TradeDirection.LONG:
                            tp_distance_pct = round(
                                (tp_price - current_price) / current_price * 100, 2
                            )
                        else:
                            tp_distance_pct = round(
                                (current_price - tp_price) / current_price * 100, 2
                            )
                    if sl_price:
                        if trade.direction == TradeDirection.LONG:
                            sl_distance_pct = round(
                                (current_price - sl_price) / current_price * 100, 2
                            )
                        else:
                            sl_distance_pct = round(
                                (sl_price - current_price) / current_price * 100, 2
                            )

                margin = float(trade.margin_used) if trade.margin_used else None
                total_unrealized_pnl += unrealized_pnl
                total_margin_used += margin or 0

                trade_data.append(
                    {
                        "id": str(trade.id),
                        "symbol": symbol,
                        "exchange": exchange,
                        "direction": trade.direction.value if trade.direction else "long",
                        "status": trade.status.value if trade.status else "open",
                        "entry_price": round(entry_price, 6) if entry_price else None,
                        "current_price": round(current_price, 6) if current_price else None,
                        "take_profit_price": round(tp_price, 6) if tp_price else None,
                        "stop_loss_price": round(sl_price, 6) if sl_price else None,
                        "position_size": round(position_size, 6),
                        "position_size_usd": round(
                            float(trade.position_size_usd) if trade.position_size_usd else 0, 2
                        ),
                        "leverage": leverage,
                        "unrealized_pnl": round(unrealized_pnl, 2),
                        "unrealized_pnl_percent": round(unrealized_pnl_pct, 2),
                        "tp_distance_pct": tp_distance_pct,
                        "sl_distance_pct": sl_distance_pct,
                        "margin_used": round(margin, 2) if margin else None,
                        "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                    }
                )

            payload = {
                "type": "trades_update",
                "timestamp": time.time(),
                "data": {
                    "trades": trade_data,
                    "summary": {
                        "total_open": len(trade_data),
                        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                        "total_margin_used": round(total_margin_used, 2),
                    },
                },
            }

            return json.dumps(payload)

        except Exception as e:
            logger.error(f"Error building trade payload for user {user_id}: {e}")
            return None

    async def _update_binance_tracked_symbols(self):
        """Update BinancePriceService with symbols from all connected users' Binance trades."""
        try:
            binance_symbols: set[str] = set()
            async with AsyncSessionLocal() as db:
                user_ids = list(self._user_clients.keys())
                if not user_ids:
                    return

                result = await db.execute(
                    select(Trade.symbol)
                    .where(
                        Trade.user_id.in_(user_ids),
                        Trade.exchange == "binance",
                        Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
                    )
                    .distinct()
                )
                binance_symbols = {row[0] for row in result.all()}

            if binance_symbols:
                binance_price_svc = get_binance_price_service()
                binance_price_svc.track_symbols(binance_symbols)

        except Exception as e:
            logger.error(f"Error updating Binance tracked symbols: {e}")


_trade_stream_service: TradeStreamService | None = None


def get_trade_stream_service() -> TradeStreamService:
    global _trade_stream_service
    if _trade_stream_service is None:
        _trade_stream_service = TradeStreamService()
    return _trade_stream_service


async def close_trade_stream_service():
    global _trade_stream_service
    if _trade_stream_service:
        await _trade_stream_service.stop()
        _trade_stream_service = None
