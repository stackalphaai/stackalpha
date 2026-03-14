import json
import logging
from typing import Any

import pandas as pd
import ta

from app.services.binance import get_binance_info_service
from app.services.binance.utils import to_binance_symbol
from app.services.llm.openrouter import OpenRouterClient, get_openrouter_client

logger = logging.getLogger(__name__)


class BinanceMarketAnalyzer:
    """Market analyzer that fetches candles from Binance Futures."""

    def __init__(self, client: OpenRouterClient | None = None):
        self.client = client or get_openrouter_client()
        self.info_service = get_binance_info_service()

    async def get_technical_indicators(
        self,
        symbol: str,
        interval: str = "4h",
        lookback_periods: int = 100,
    ) -> dict[str, Any]:
        binance_symbol = to_binance_symbol(symbol)

        candles = await self.info_service.get_klines(
            symbol=binance_symbol,
            interval=interval,
            limit=lookback_periods,
        )

        if not candles:
            return {}

        df = pd.DataFrame(candles)
        df = df.rename(
            columns={
                "t": "timestamp",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            }
        )
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df = df.astype(
            {"open": float, "high": float, "low": float, "close": float, "volume": float}
        )

        indicators = {}

        # RSI
        indicators["rsi_14"] = float(
            ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1]
        )

        # MACD
        macd = ta.trend.MACD(df["close"])
        indicators["macd"] = float(macd.macd().iloc[-1])
        indicators["macd_signal"] = float(macd.macd_signal().iloc[-1])
        indicators["macd_histogram"] = float(macd.macd_diff().iloc[-1])

        # Bollinger Bands
        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        indicators["bb_upper"] = float(bb.bollinger_hband().iloc[-1])
        indicators["bb_middle"] = float(bb.bollinger_mavg().iloc[-1])
        indicators["bb_lower"] = float(bb.bollinger_lband().iloc[-1])
        indicators["bb_width"] = float(bb.bollinger_wband().iloc[-1])

        # EMAs and SMA
        indicators["ema_9"] = float(
            ta.trend.EMAIndicator(df["close"], window=9).ema_indicator().iloc[-1]
        )
        indicators["ema_21"] = float(
            ta.trend.EMAIndicator(df["close"], window=21).ema_indicator().iloc[-1]
        )
        indicators["ema_50"] = float(
            ta.trend.EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1]
        )
        indicators["sma_200"] = float(
            ta.trend.SMAIndicator(df["close"], window=min(200, len(df))).sma_indicator().iloc[-1]
        )

        # Stochastic
        stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"])
        indicators["stoch_k"] = float(stoch.stoch().iloc[-1])
        indicators["stoch_d"] = float(stoch.stoch_signal().iloc[-1])

        # ATR
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
        indicators["atr_14"] = float(atr.average_true_range().iloc[-1])

        # ADX
        adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        indicators["adx"] = float(adx.adx().iloc[-1])
        indicators["di_plus"] = float(adx.adx_pos().iloc[-1])
        indicators["di_minus"] = float(adx.adx_neg().iloc[-1])

        # Price data
        indicators["current_price"] = float(df["close"].iloc[-1])
        indicators["price_change_pct"] = float(
            (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100
        )
        indicators["volume_avg"] = float(df["volume"].mean())
        indicators["volume_current"] = float(df["volume"].iloc[-1])

        # Sanitize NaN/Inf values — PostgreSQL JSONB rejects them
        import math

        for key, value in indicators.items():
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                indicators[key] = 0.0

        return indicators

    async def analyze_market(
        self,
        symbol: str,
        model: str,
        indicators: dict[str, Any],
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        system_prompt = """You are an expert cryptocurrency market analyst specializing in LEVERAGED perpetual futures trading on Binance Futures.
Analyze the provided technical indicators and market data, then provide a trading recommendation.

CRITICAL — LEVERAGE-AWARE TP/SL RULES:
These trades use leverage (typically 3x-20x). With leverage, small price moves create large P&L:
- At 5x leverage: a 2% price move = 10% P&L
- At 10x leverage: a 1% price move = 10% P&L
- At 15x leverage: a 0.7% price move = ~10% P&L

Therefore, your TP and SL MUST be tight and realistic:
- Take Profit: Set 1-3% from entry price (NOT 5-10%). These are short-term leveraged trades.
- Stop Loss: Set 0.5-2% from entry price. Keep it tight to protect capital with leverage.
- A move from $0.0899 to $0.0911 on DOGE (1.3%) at 10x = 13% profit. That IS the trade.
- Do NOT set TP at 5%+ away — that would require a massive move and the trade will likely get stopped out first.

Your response MUST be valid JSON with the following structure:
{
    "direction": "long" | "short" | "neutral",
    "confidence": 0.0-1.0,
    "entry_price": float,
    "take_profit_price": float,
    "stop_loss_price": float,
    "leverage": 1-20,
    "reasoning": "detailed analysis explanation",
    "key_factors": ["factor1", "factor2", "factor3"],
    "risk_level": "low" | "medium" | "high"
}

STRICT FILTERING — return "neutral" if ANY of these apply:
- ADX < 20 (no clear trend — avoid choppy/ranging markets)
- RSI between 40-60 with no clear divergence (indecisive momentum)
- MACD histogram near zero with no clear crossover forming
- Price is mid-range within Bollinger Bands with no directional pressure
- Volume is declining (current volume < average volume)
- Multiple indicators conflict (e.g., RSI says oversold but MACD is bearish)
- Funding rate is extreme and against your direction (>0.03% for longs, <-0.03% for shorts)

Guidelines for valid signals:
- Only recommend trades with confidence > 0.7 (be conservative)
- Risk-reward ratio MUST be at least 1.5:1 — if you can't find a setup with this ratio, return neutral
- TP should be 1-3% from entry, SL should be 0.5-2% from entry (leverage amplifies these moves)
- Use ATR to gauge recent volatility — if ATR/price < 1%, use tighter TP/SL
- Factor in funding rate for position costs
- Use support/resistance from Bollinger Bands for precise TP/SL placement
- Prefer trades where EMA_9 > EMA_21 (for longs) or EMA_9 < EMA_21 (for shorts)
- Higher leverage = tighter TP/SL required
- When in doubt, return "neutral" — it is better to miss a trade than to enter a bad one"""

        user_prompt = f"""Analyze {symbol} on Binance Futures for a potential trade opportunity.

Technical Indicators:
{json.dumps(indicators, indent=2)}

Market Data:
- Mark Price: ${market_data.get("mark_price", "N/A")}
- Funding Rate: {market_data.get("funding_rate", "N/A")}%
- 24h Volume: ${market_data.get("volume_24h", "N/A")}
- 24h Price Change: {market_data.get("price_change_percent_24h", "N/A")}%
- 24h High: ${market_data.get("high_24h", "N/A")}
- 24h Low: ${market_data.get("low_24h", "N/A")}

Provide your analysis and trading recommendation in JSON format."""

        try:
            response = await self.client.get_completion_text(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]

            analysis = json.loads(response)
            analysis["model"] = model
            analysis["symbol"] = symbol

            return analysis

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return {
                "model": model,
                "symbol": symbol,
                "direction": "neutral",
                "confidence": 0.0,
                "error": "Failed to parse response",
            }
        except Exception as e:
            logger.error(f"Error analyzing market with {model}: {e}")
            return {
                "model": model,
                "symbol": symbol,
                "direction": "neutral",
                "confidence": 0.0,
                "error": str(e),
            }


_binance_analyzer_instance: BinanceMarketAnalyzer | None = None


def get_binance_market_analyzer() -> BinanceMarketAnalyzer:
    global _binance_analyzer_instance
    if _binance_analyzer_instance is None:
        _binance_analyzer_instance = BinanceMarketAnalyzer()
    return _binance_analyzer_instance
