import asyncio
import logging

import httpx

from app.services.binance.utils import to_binance_symbol

logger = logging.getLogger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"


class BinancePriceService:
    """
    Lightweight singleton that polls Binance Futures mark prices
    for symbols with active trades. Uses the public premiumIndex endpoint.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}  # BTCUSDT -> price
        self._tracked_symbols: set[str] = set()  # HL-style symbols: BTC, ETH
        self._running = False
        self._price_task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def start(self):
        if self._running:
            return
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        self._price_task = asyncio.create_task(self._price_loop())
        logger.info("BinancePriceService started")

    async def stop(self):
        self._running = False
        if self._price_task:
            self._price_task.cancel()
            try:
                await self._price_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("BinancePriceService stopped")

    def track_symbols(self, symbols: set[str]):
        """Update the set of HL-style symbols (e.g. {'BTC', 'ETH'}) to track."""
        self._tracked_symbols = symbols

    def get_prices(self) -> dict[str, float]:
        """Return {HL_symbol: mark_price} for all tracked symbols."""
        return dict(self._prices)

    def get_price(self, symbol: str) -> float | None:
        """Get price for an HL-style symbol (e.g. 'BTC')."""
        return self._prices.get(symbol)

    async def _price_loop(self):
        while self._running:
            try:
                await asyncio.sleep(2)

                if not self._tracked_symbols or not self._client:
                    continue

                # Fetch all mark prices in one call (lightweight)
                resp = await self._client.get(f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex")
                if resp.status_code != 200:
                    logger.warning(f"Binance premiumIndex returned {resp.status_code}")
                    continue

                data = resp.json()

                # Build lookup of Binance symbols we care about
                binance_to_hl: dict[str, str] = {}
                for hl_symbol in self._tracked_symbols:
                    binance_to_hl[to_binance_symbol(hl_symbol)] = hl_symbol

                async with self._lock:
                    for item in data:
                        binance_symbol = item.get("symbol", "")
                        if binance_symbol in binance_to_hl:
                            mark_price = float(item.get("markPrice", 0))
                            if mark_price > 0:
                                hl_symbol = binance_to_hl[binance_symbol]
                                self._prices[hl_symbol] = mark_price

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"BinancePriceService error: {e}")
                await asyncio.sleep(5)


_binance_price_service: BinancePriceService | None = None


def get_binance_price_service() -> BinancePriceService:
    global _binance_price_service
    if _binance_price_service is None:
        _binance_price_service = BinancePriceService()
    return _binance_price_service


async def close_binance_price_service():
    global _binance_price_service
    if _binance_price_service:
        await _binance_price_service.stop()
        _binance_price_service = None
