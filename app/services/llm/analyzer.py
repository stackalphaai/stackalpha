import json
import logging
from typing import Any

import pandas as pd
import ta

from app.services.hyperliquid import get_info_service
from app.services.llm.openrouter import OpenRouterClient, get_openrouter_client

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    def __init__(self, client: OpenRouterClient | None = None):
        self.client = client or get_openrouter_client()
        self.info_service = get_info_service()

    async def get_technical_indicators(
        self,
        symbol: str,
        interval: str = "4h",
        lookback_periods: int = 100,
    ) -> dict[str, Any]:
        import time

        end_time = int(time.time() * 1000)
        start_time = end_time - (lookback_periods * self._interval_to_ms(interval))

        candles = await self.info_service.get_candles(
            coin=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
        )

        if not candles:
            return {}

        df = pd.DataFrame(candles)
        df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
        df = df.astype(
            {"open": float, "high": float, "low": float, "close": float, "volume": float}
        )

        indicators = {}

        indicators["rsi_14"] = float(
            ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1]
        )

        macd = ta.trend.MACD(df["close"])
        indicators["macd"] = float(macd.macd().iloc[-1])
        indicators["macd_signal"] = float(macd.macd_signal().iloc[-1])
        indicators["macd_histogram"] = float(macd.macd_diff().iloc[-1])

        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        indicators["bb_upper"] = float(bb.bollinger_hband().iloc[-1])
        indicators["bb_middle"] = float(bb.bollinger_mavg().iloc[-1])
        indicators["bb_lower"] = float(bb.bollinger_lband().iloc[-1])
        indicators["bb_width"] = float(bb.bollinger_wband().iloc[-1])

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

        stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"])
        indicators["stoch_k"] = float(stoch.stoch().iloc[-1])
        indicators["stoch_d"] = float(stoch.stoch_signal().iloc[-1])

        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
        indicators["atr_14"] = float(atr.average_true_range().iloc[-1])

        adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        indicators["adx"] = float(adx.adx().iloc[-1])
        indicators["di_plus"] = float(adx.adx_pos().iloc[-1])
        indicators["di_minus"] = float(adx.adx_neg().iloc[-1])

        indicators["current_price"] = float(df["close"].iloc[-1])
        indicators["price_change_pct"] = float(
            (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100
        )
        indicators["volume_avg"] = float(df["volume"].mean())
        indicators["volume_current"] = float(df["volume"].iloc[-1])

        return indicators

    async def analyze_market(
        self,
        symbol: str,
        model: str,
        indicators: dict[str, Any],
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        system_prompt = """You are an expert cryptocurrency market analyst specializing in perpetual futures trading on Hyperliquid.
Analyze the provided technical indicators and market data, then provide a trading recommendation.

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

Guidelines:
- Only recommend trades with confidence > 0.6
- Risk-reward ratio should be at least 1.5:1
- Consider volatility (ATR) for stop-loss placement
- Factor in funding rate for position costs
- Use support/resistance from Bollinger Bands
- Consider trend strength from ADX"""

        user_prompt = f"""Analyze {symbol} for a potential trade opportunity.

Technical Indicators:
{json.dumps(indicators, indent=2)}

Market Data:
- Mark Price: ${market_data.get("mark_price", "N/A")}
- Index Price: ${market_data.get("index_price", "N/A")}
- Funding Rate: {market_data.get("funding_rate", "N/A")}%
- Open Interest: ${market_data.get("open_interest", "N/A")}
- 24h Volume: ${market_data.get("volume_24h", "N/A")}

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

    def _interval_to_ms(self, interval: str) -> int:
        mapping = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "30m": 1_800_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }
        return mapping.get(interval, 14_400_000)


_analyzer_instance: MarketAnalyzer | None = None


def get_market_analyzer() -> MarketAnalyzer:
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = MarketAnalyzer()
    return _analyzer_instance
