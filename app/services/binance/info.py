import logging
from typing import Any

from app.core.exceptions import BinanceAPIError
from app.services.binance.client import BinanceClient

logger = logging.getLogger(__name__)


class BinanceInfoService:
    """Read-only Binance Futures market data. Uses unauthenticated client for public endpoints."""

    def __init__(self, client: BinanceClient | None = None):
        self._client = client or BinanceClient()

    async def get_futures_tickers(self) -> list[dict[str, Any]]:
        """Get all 24h futures ticker data."""
        try:
            client = await self._client.get_client()
            return await client.futures_ticker()
        except Exception as e:
            logger.error(f"Failed to get futures tickers: {e}")
            raise BinanceAPIError(f"Failed to get futures tickers: {e}") from e

    async def get_top_gainers(
        self, min_volume: float = 10_000_000, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get top gaining futures pairs sorted by 24h price change percent."""
        tickers = await self.get_futures_tickers()

        # Filter: USDT pairs only, minimum volume, exclude stablecoins
        stablecoins = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT", "FDUSDUSDT"}
        filtered = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in stablecoins:
                continue
            volume_usd = float(t.get("quoteVolume", 0))
            if volume_usd < min_volume:
                continue
            filtered.append(
                {
                    "symbol": symbol,
                    "price": float(t.get("lastPrice", 0)),
                    "price_change_percent_24h": float(t.get("priceChangePercent", 0)),
                    "volume_24h": volume_usd,
                    "high_24h": float(t.get("highPrice", 0)),
                    "low_24h": float(t.get("lowPrice", 0)),
                }
            )

        # Sort by price change percent descending (top gainers)
        filtered.sort(key=lambda x: x["price_change_percent_24h"], reverse=True)
        return filtered[:limit]

    async def get_top_losers(
        self, min_volume: float = 10_000_000, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get top losing futures pairs sorted by 24h price change percent ascending."""
        tickers = await self.get_futures_tickers()

        stablecoins = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT", "FDUSDUSDT"}
        filtered = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in stablecoins:
                continue
            volume_usd = float(t.get("quoteVolume", 0))
            if volume_usd < min_volume:
                continue
            filtered.append(
                {
                    "symbol": symbol,
                    "price": float(t.get("lastPrice", 0)),
                    "price_change_percent_24h": float(t.get("priceChangePercent", 0)),
                    "volume_24h": volume_usd,
                    "high_24h": float(t.get("highPrice", 0)),
                    "low_24h": float(t.get("lowPrice", 0)),
                }
            )

        # Sort by price change percent ascending (top losers)
        filtered.sort(key=lambda x: x["price_change_percent_24h"])
        return filtered[:limit]

    async def get_market_data(self, symbol: str) -> dict[str, Any]:
        """Get comprehensive market data for a single symbol."""
        try:
            client = await self._client.get_client()

            ticker = await client.futures_symbol_ticker(symbol=symbol)
            ticker_24h = await client.futures_ticker(symbol=symbol)

            result = {
                "symbol": symbol,
                "mark_price": float(ticker.get("price", 0)),
                "price_change_24h": float(ticker_24h.get("priceChange", 0)),
                "price_change_percent_24h": float(ticker_24h.get("priceChangePercent", 0)),
                "volume_24h": float(ticker_24h.get("quoteVolume", 0)),
                "high_24h": float(ticker_24h.get("highPrice", 0)),
                "low_24h": float(ticker_24h.get("lowPrice", 0)),
            }

            # Try to get funding rate
            try:
                funding = await client.futures_funding_rate(symbol=symbol, limit=1)
                if funding:
                    result["funding_rate"] = float(funding[-1].get("fundingRate", 0))
            except Exception:
                result["funding_rate"] = 0.0

            return result
        except Exception as e:
            logger.error(f"Failed to get market data for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to get market data: {e}") from e

    async def get_klines(
        self,
        symbol: str,
        interval: str = "4h",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get candlestick/kline data for technical analysis."""
        try:
            client = await self._client.get_client()
            klines = await client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit,
            )

            result = []
            for k in klines:
                result.append(
                    {
                        "t": k[0],  # open time
                        "o": k[1],  # open
                        "h": k[2],  # high
                        "l": k[3],  # low
                        "c": k[4],  # close
                        "v": k[5],  # volume
                    }
                )
            return result
        except Exception as e:
            logger.error(f"Failed to get klines for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to get klines: {e}") from e

    async def get_symbol_precision(self, symbol: str) -> dict[str, int]:
        """Get quantity and price precision for a symbol from exchange info."""
        try:
            client = await self._client.get_client()
            info = await client.futures_exchange_info()

            for s in info.get("symbols", []):
                if s["symbol"] == symbol:
                    return {
                        "quantity_precision": s.get("quantityPrecision", 3),
                        "price_precision": s.get("pricePrecision", 2),
                        "min_qty": float(
                            next(
                                (
                                    f["minQty"]
                                    for f in s.get("filters", [])
                                    if f["filterType"] == "LOT_SIZE"
                                ),
                                "0.001",
                            )
                        ),
                        "min_notional": float(
                            next(
                                (
                                    f["notional"]
                                    for f in s.get("filters", [])
                                    if f["filterType"] == "MIN_NOTIONAL"
                                ),
                                "5",
                            )
                        ),
                    }

            raise BinanceAPIError(f"Symbol {symbol} not found in exchange info")
        except BinanceAPIError:
            raise
        except Exception as e:
            logger.error(f"Failed to get symbol precision for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to get symbol info: {e}") from e

    async def close(self):
        await self._client.close()
