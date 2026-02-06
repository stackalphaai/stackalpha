import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from app.services.hyperliquid.client import get_hyperliquid_client
from app.services.hyperliquid.websocket import get_ws_manager

logger = logging.getLogger(__name__)


@dataclass
class CoinData:
    symbol: str
    mid_price: float = 0.0
    mark_price: float = 0.0
    prev_day_price: float = 0.0
    day_change_pct: float = 0.0
    volume_24h: float = 0.0
    funding_rate: float = 0.0
    open_interest: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mid_price": self.mid_price,
            "mark_price": self.mark_price,
            "prev_day_price": self.prev_day_price,
            "day_change_pct": round(self.day_change_pct, 4),
            "volume_24h": self.volume_24h,
            "funding_rate": self.funding_rate,
            "open_interest": self.open_interest,
        }


class TopGainersService:
    """
    Background service that aggregates Hyperliquid market data to produce
    real-time top gainers and losers.

    Architecture:
    - Subscribes to allMids WebSocket channel for real-time mid price updates
    - Periodically fetches metaAndAssetCtxs REST endpoint for 24h stats
    - Calculates live 24h % change using real-time mid prices vs prevDayPx
    - Broadcasts sorted top gainers/losers to connected frontend WebSocket clients
    """

    def __init__(self):
        self._coins: dict[str, CoinData] = {}
        self._connected_clients: set[WebSocket] = set()
        self._running = False
        self._broadcast_task: asyncio.Task | None = None
        self._stats_refresh_task: asyncio.Task | None = None
        self._last_broadcast: str = ""
        self._lock = asyncio.Lock()

    async def start(self):
        """Start the top gainers service."""
        if self._running:
            return

        self._running = True
        logger.info("Starting TopGainersService...")

        # Initial fetch of all market data
        await self._refresh_market_stats()

        # Subscribe to allMids for real-time price updates
        ws_manager = get_ws_manager()
        await ws_manager.connect()
        await ws_manager.subscribe_all_mids(self._on_all_mids_update)

        # Start periodic tasks
        self._stats_refresh_task = asyncio.create_task(self._stats_refresh_loop())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

        logger.info(
            f"TopGainersService started. Tracking {len(self._coins)} coins."
        )

    async def stop(self):
        """Stop the top gainers service."""
        self._running = False

        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        if self._stats_refresh_task:
            self._stats_refresh_task.cancel()
            try:
                await self._stats_refresh_task
            except asyncio.CancelledError:
                pass

        # Close all client connections
        for client in list(self._connected_clients):
            try:
                await client.close()
            except Exception:
                pass
        self._connected_clients.clear()

        logger.info("TopGainersService stopped.")

    async def register_client(self, websocket: WebSocket):
        """Register a new WebSocket client."""
        self._connected_clients.add(websocket)
        logger.info(
            f"Client connected. Total clients: {len(self._connected_clients)}"
        )

        # Send current state immediately
        data = self._build_payload()
        try:
            await websocket.send_text(data)
        except Exception:
            self._connected_clients.discard(websocket)

    def unregister_client(self, websocket: WebSocket):
        """Unregister a disconnected WebSocket client."""
        self._connected_clients.discard(websocket)
        logger.info(
            f"Client disconnected. Total clients: {len(self._connected_clients)}"
        )

    async def _refresh_market_stats(self):
        """Fetch metaAndAssetCtxs from Hyperliquid REST API to get 24h stats."""
        try:
            client = get_hyperliquid_client()
            meta = await client.info_request({"type": "metaAndAssetCtxs"})

            if not meta or len(meta) < 2:
                logger.warning("Empty metaAndAssetCtxs response")
                return

            universe = meta[0].get("universe", [])
            asset_ctxs = meta[1] if len(meta) > 1 else []

            async with self._lock:
                for i, asset in enumerate(universe):
                    symbol = asset.get("name", "")
                    if not symbol:
                        continue

                    ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                    mark_price = float(ctx.get("markPx", 0))
                    prev_day_price = float(ctx.get("prevDayPx", 0)) if ctx.get("prevDayPx") else 0
                    day_change = float(ctx.get("dayChg", 0)) if ctx.get("dayChg") else 0
                    volume = float(ctx.get("dayNtlVlm", 0)) if ctx.get("dayNtlVlm") else 0
                    funding = float(ctx.get("funding", 0)) if ctx.get("funding") else 0
                    oi = float(ctx.get("openInterest", 0)) if ctx.get("openInterest") else 0

                    if symbol in self._coins:
                        coin = self._coins[symbol]
                        coin.mark_price = mark_price
                        coin.prev_day_price = prev_day_price
                        coin.day_change_pct = day_change * 100
                        coin.volume_24h = volume
                        coin.funding_rate = funding
                        coin.open_interest = oi
                        # Only update mid_price if we haven't received a WS update
                        if coin.mid_price == 0:
                            coin.mid_price = mark_price
                    else:
                        self._coins[symbol] = CoinData(
                            symbol=symbol,
                            mid_price=mark_price,
                            mark_price=mark_price,
                            prev_day_price=prev_day_price,
                            day_change_pct=day_change * 100,
                            volume_24h=volume,
                            funding_rate=funding,
                            open_interest=oi,
                        )

            logger.debug(f"Refreshed market stats for {len(universe)} coins")

        except Exception as e:
            logger.error(f"Failed to refresh market stats: {e}")

    async def _on_all_mids_update(self, data: dict[str, Any]):
        """Handle real-time allMids WebSocket updates."""
        mids = data.get("data", {}).get("mids", {})
        if not mids:
            return

        async with self._lock:
            for symbol, mid_price_str in mids.items():
                try:
                    mid_price = float(mid_price_str)
                except (ValueError, TypeError):
                    continue

                if symbol in self._coins:
                    coin = self._coins[symbol]
                    coin.mid_price = mid_price
                    # Recalculate 24h change based on live mid price
                    if coin.prev_day_price > 0:
                        coin.day_change_pct = (
                            (mid_price - coin.prev_day_price) / coin.prev_day_price
                        ) * 100
                else:
                    self._coins[symbol] = CoinData(
                        symbol=symbol,
                        mid_price=mid_price,
                        mark_price=mid_price,
                    )

    async def _stats_refresh_loop(self):
        """Periodically refresh 24h stats from REST API."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Refresh every 30 seconds
                await self._refresh_market_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in stats refresh loop: {e}")
                await asyncio.sleep(5)

    async def _broadcast_loop(self):
        """Periodically broadcast top gainers to connected clients."""
        while self._running:
            try:
                await asyncio.sleep(2)  # Broadcast every 2 seconds

                if not self._connected_clients:
                    continue

                payload = self._build_payload()

                # Skip if nothing changed
                if payload == self._last_broadcast:
                    continue
                self._last_broadcast = payload

                # Broadcast to all connected clients
                disconnected = set()
                for client in self._connected_clients:
                    try:
                        await client.send_text(payload)
                    except Exception:
                        disconnected.add(client)

                # Clean up disconnected clients
                for client in disconnected:
                    self._connected_clients.discard(client)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcast loop: {e}")
                await asyncio.sleep(2)

    def _build_payload(self) -> str:
        """Build the JSON payload with top gainers and losers."""
        coins = list(self._coins.values())

        # Filter out coins with no price data
        coins = [c for c in coins if c.mid_price > 0 and c.prev_day_price > 0]

        # Sort by 24h change percentage descending
        sorted_by_change = sorted(
            coins, key=lambda c: c.day_change_pct, reverse=True
        )

        top_gainers = [c.to_dict() for c in sorted_by_change[:20]]
        top_losers = [c.to_dict() for c in sorted_by_change[-20:]]
        top_losers.reverse()  # Most negative first

        # Top by volume
        sorted_by_volume = sorted(
            coins, key=lambda c: c.volume_24h, reverse=True
        )
        top_volume = [c.to_dict() for c in sorted_by_volume[:20]]

        payload = {
            "type": "top_gainers_update",
            "timestamp": time.time(),
            "data": {
                "gainers": top_gainers,
                "losers": top_losers,
                "top_volume": top_volume,
                "total_coins": len(coins),
            },
        }

        return json.dumps(payload)

    @property
    def client_count(self) -> int:
        return len(self._connected_clients)

    @property
    def coin_count(self) -> int:
        return len(self._coins)


# Singleton instance
_service_instance: TopGainersService | None = None


def get_top_gainers_service() -> TopGainersService:
    global _service_instance
    if _service_instance is None:
        _service_instance = TopGainersService()
    return _service_instance


async def close_top_gainers_service():
    global _service_instance
    if _service_instance:
        await _service_instance.stop()
        _service_instance = None
