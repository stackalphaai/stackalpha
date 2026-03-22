import asyncio
import json
import logging
import math
import re
from typing import Any

import pandas as pd
import ta

from app.services.binance import get_binance_info_service
from app.services.binance.utils import to_binance_symbol
from app.services.llm.openrouter import OpenRouterClient, get_openrouter_client

logger = logging.getLogger(__name__)


class BinanceMarketAnalyzer:
    """Market analyzer with 4-timeframe entry system for Binance Futures.

    Timeframe roles:
      4h  — Trend direction (higher highs = BUY, lower lows = SELL)
      1h  — Trend confirmation + TP zone identification
      15m — Entry zone (price at support/resistance)
      5m  — Entry trigger (last 3 candles show pattern) + ATR for SL buffer
    """

    def __init__(self, client: OpenRouterClient | None = None):
        self.client = client or get_openrouter_client()
        self.info_service = get_binance_info_service()

    # ------------------------------------------------------------------
    # DataFrame helpers
    # ------------------------------------------------------------------

    def _candles_to_dataframe(self, candles: list[dict]) -> pd.DataFrame:
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
        return df

    def _find_swing_highs(self, highs: pd.Series, order: int = 2) -> list[float]:
        """Find pivot swing highs (a high flanked by `order` lower highs on each side)."""
        swings = []
        values = highs.values
        for i in range(order, len(values) - order):
            if all(values[i] > values[i - j] for j in range(1, order + 1)) and all(
                values[i] > values[i + j] for j in range(1, order + 1)
            ):
                swings.append(float(values[i]))
        return swings

    def _find_swing_lows(self, lows: pd.Series, order: int = 2) -> list[float]:
        """Find pivot swing lows."""
        swings = []
        values = lows.values
        for i in range(order, len(values) - order):
            if all(values[i] < values[i - j] for j in range(1, order + 1)) and all(
                values[i] < values[i + j] for j in range(1, order + 1)
            ):
                swings.append(float(values[i]))
        return swings

    def _sanitize(self, indicators: dict[str, Any]) -> dict[str, Any]:
        """Replace NaN/Inf with 0.0 for JSON serialization."""
        for key, value in indicators.items():
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                indicators[key] = 0.0
        return indicators

    # ------------------------------------------------------------------
    # 4-Timeframe analysis methods
    # ------------------------------------------------------------------

    def _analyze_4h_trend(self, df: pd.DataFrame) -> dict[str, Any]:
        """Determine trend direction from 4h candles.

        Higher highs + higher lows → BUY bias
        Lower highs + lower lows → SELL bias
        Otherwise → NEUTRAL
        """
        swing_highs = self._find_swing_highs(df["high"], order=2)
        swing_lows = self._find_swing_lows(df["low"], order=2)

        # Need at least 2 of each to compare
        hh = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
        hl = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]
        lh = len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]
        ll = len(swing_lows) >= 2 and swing_lows[-1] < swing_lows[-2]

        # EMA confirmation
        ema_9 = ta.trend.EMAIndicator(df["close"], window=9).ema_indicator().iloc[-1]
        ema_21 = ta.trend.EMAIndicator(df["close"], window=21).ema_indicator().iloc[-1]
        ema_trend = "bullish" if ema_9 > ema_21 else ("bearish" if ema_9 < ema_21 else "flat")

        # ADX for trend strength
        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        adx = float(adx_ind.adx().iloc[-1])

        if (hh and hl) and ema_trend == "bullish" and adx >= 15:
            bias = "BUY"
        elif (lh and ll) and ema_trend == "bearish" and adx >= 15:
            bias = "SELL"
        # Weaker condition: EMA trend + reasonable ADX even without perfect swings
        elif ema_trend == "bullish" and adx >= 20 and hl:
            bias = "BUY"
        elif ema_trend == "bearish" and adx >= 20 and ll:
            bias = "SELL"
        else:
            bias = "NEUTRAL"

        return {
            "bias": bias,
            "ema_trend": ema_trend,
            "adx": adx,
            "swing_highs": swing_highs[-3:] if swing_highs else [],
            "swing_lows": swing_lows[-3:] if swing_lows else [],
        }

    def _analyze_1h_confirmation(self, df: pd.DataFrame, bias_4h: str) -> dict[str, Any]:
        """Confirm 4h trend on 1h and identify key TP zones."""
        ema_9 = ta.trend.EMAIndicator(df["close"], window=9).ema_indicator().iloc[-1]
        ema_21 = ta.trend.EMAIndicator(df["close"], window=21).ema_indicator().iloc[-1]
        rsi = ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1]

        ema_aligned = (bias_4h == "BUY" and ema_9 > ema_21) or (
            bias_4h == "SELL" and ema_9 < ema_21
        )

        # RSI should not be diverging from bias
        rsi_ok = True
        if bias_4h == "BUY" and rsi > 80:
            rsi_ok = False  # overbought on 1h, don't confirm buy
        if bias_4h == "SELL" and rsi < 20:
            rsi_ok = False  # oversold on 1h, don't confirm sell

        confirmed = ema_aligned and rsi_ok

        # TP zones: recent swing highs for BUY, swing lows for SELL
        swing_highs = self._find_swing_highs(df["high"], order=2)
        swing_lows = self._find_swing_lows(df["low"], order=2)

        current_price = float(df["close"].iloc[-1])
        if bias_4h == "BUY":
            # TP at recent swing highs above current price
            tp_zones = sorted([h for h in swing_highs if h > current_price])[:3]
        else:
            # TP at recent swing lows below current price
            tp_zones = sorted([lv for lv in swing_lows if lv < current_price], reverse=True)[:3]

        # Key S/R from recent structure
        key_resistance = max(swing_highs[-3:]) if swing_highs else current_price * 1.02
        key_support = min(swing_lows[-3:]) if swing_lows else current_price * 0.98

        return {
            "confirmed": confirmed,
            "ema_aligned": ema_aligned,
            "rsi_1h": float(rsi),
            "tp_zones": tp_zones,
            "key_support": float(key_support),
            "key_resistance": float(key_resistance),
        }

    def _analyze_15m_entry_zone(self, df: pd.DataFrame, bias_4h: str) -> dict[str, Any]:
        """Check if price is at a valid 15m entry zone."""
        current_price = float(df["close"].iloc[-1])

        swing_highs = self._find_swing_highs(df["high"], order=2)
        swing_lows = self._find_swing_lows(df["low"], order=2)

        ema_21 = float(ta.trend.EMAIndicator(df["close"], window=21).ema_indicator().iloc[-1])

        proximity_pct = 0.004  # 0.4% proximity to be "at the zone"

        at_zone = False
        structure_level = 0.0

        if bias_4h == "BUY":
            # Price should be near 15m support (swing low or EMA_21)
            support_levels = swing_lows[-5:] + [ema_21]
            for level in support_levels:
                if level > 0 and abs(current_price - level) / current_price <= proximity_pct:
                    at_zone = True
                    structure_level = level
                    break
            # Also accept if price is within 0.4% above the EMA_21 (pullback to EMA)
            if not at_zone and ema_21 > 0:
                if 0 <= (current_price - ema_21) / current_price <= proximity_pct:
                    at_zone = True
                    structure_level = ema_21
        else:
            # Price should be near 15m resistance (swing high or EMA_21)
            resistance_levels = swing_highs[-5:] + [ema_21]
            for level in resistance_levels:
                if level > 0 and abs(current_price - level) / current_price <= proximity_pct:
                    at_zone = True
                    structure_level = level
                    break
            if not at_zone and ema_21 > 0:
                if 0 <= (ema_21 - current_price) / current_price <= proximity_pct:
                    at_zone = True
                    structure_level = ema_21

        # RSI on 15m as extra context
        rsi_15m = float(ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1])

        return {
            "at_zone": at_zone,
            "zone_price": current_price,
            "structure_level": structure_level if structure_level > 0 else current_price,
            "rsi_15m": rsi_15m,
            "ema_21_15m": ema_21,
        }

    def _analyze_5m_trigger(self, df: pd.DataFrame, bias_4h: str) -> dict[str, Any]:
        """Check if last 3 candles show an entry pattern on 5m."""
        if len(df) < 5:
            return {"triggered": False, "pattern": "insufficient_data", "atr_5m": 0.0}

        last3 = df.tail(3)
        closes = last3["close"].values
        opens = last3["open"].values
        highs = last3["high"].values
        lows = last3["low"].values

        # ATR on 5m for SL buffer
        atr_5m = float(
            ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
            .average_true_range()
            .iloc[-1]
        )

        # RSI on 5m
        rsi_5m = float(ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1])

        triggered = False
        pattern = "none"

        if bias_4h == "BUY":
            # Bullish patterns:
            # 1. Three consecutive higher closes
            if closes[2] > closes[1] > closes[0]:
                triggered = True
                pattern = "three_higher_closes"
            # 2. Bullish engulfing on last candle
            elif (
                closes[2] > opens[2]
                and opens[2] <= closes[1]
                and closes[2] >= opens[1]
                and closes[1] < opens[1]
            ):
                triggered = True
                pattern = "bullish_engulfing"
            # 3. Hammer at support (small body, long lower wick)
            elif closes[2] > opens[2]:
                body = closes[2] - opens[2]
                lower_wick = opens[2] - lows[2]
                if lower_wick > body * 2 and body > 0:
                    triggered = True
                    pattern = "hammer"
            # 4. Two of three candles bullish with last candle bullish
            elif closes[2] > opens[2] and sum(1 for i in range(3) if closes[i] > opens[i]) >= 2:
                triggered = True
                pattern = "bullish_majority"

            # RSI should not be extremely overbought on 5m for a buy
            if triggered and rsi_5m > 85:
                triggered = False
                pattern = "rsi_overbought_5m"
        else:
            # Bearish patterns:
            if closes[2] < closes[1] < closes[0]:
                triggered = True
                pattern = "three_lower_closes"
            elif (
                closes[2] < opens[2]
                and opens[2] >= closes[1]
                and closes[2] <= opens[1]
                and closes[1] > opens[1]
            ):
                triggered = True
                pattern = "bearish_engulfing"
            elif closes[2] < opens[2]:
                body = opens[2] - closes[2]
                upper_wick = highs[2] - opens[2]
                if upper_wick > body * 2 and body > 0:
                    triggered = True
                    pattern = "shooting_star"
            elif closes[2] < opens[2] and sum(1 for i in range(3) if closes[i] < opens[i]) >= 2:
                triggered = True
                pattern = "bearish_majority"

            if triggered and rsi_5m < 15:
                triggered = False
                pattern = "rsi_oversold_5m"

        return {
            "triggered": triggered,
            "pattern": pattern,
            "atr_5m": atr_5m,
            "rsi_5m": rsi_5m,
        }

    def _calculate_mtf_sl(
        self,
        structure_level: float,
        atr_5m: float,
        bias: str,
        current_price: float,
    ) -> float | None:
        """Compute SL: behind 15m structure + 1x 5m ATR, capped at 3%."""
        if bias == "BUY":
            raw_sl = structure_level - atr_5m
            max_sl = current_price * 0.97  # 3% cap
            sl = max(raw_sl, max_sl)
        else:
            raw_sl = structure_level + atr_5m
            max_sl = current_price * 1.03
            sl = min(raw_sl, max_sl)

        # Validate SL is at least 0.3% away (avoid getting stopped by noise)
        sl_pct = abs(current_price - sl) / current_price
        if sl_pct < 0.003:
            return None  # Too tight — setup not precise enough
        if sl_pct > 0.03:
            return None  # Exceeds 3% cap — entry zone not good enough

        return sl

    # ------------------------------------------------------------------
    # Main MTF orchestrator
    # ------------------------------------------------------------------

    async def get_multi_timeframe_analysis(self, symbol: str) -> dict[str, Any] | None:
        """Run the full 4-timeframe alignment check.

        Returns structured data if all 4 timeframes align, or None (HOLD).
        This runs BEFORE calling LLMs — saves API costs by rejecting early.
        """
        binance_symbol = to_binance_symbol(symbol)

        try:
            # Fetch all 4 timeframes in parallel
            candles_4h, candles_1h, candles_15m, candles_5m = await asyncio.gather(
                self.info_service.get_klines(binance_symbol, "4h", 100),
                self.info_service.get_klines(binance_symbol, "1h", 100),
                self.info_service.get_klines(binance_symbol, "15m", 50),
                self.info_service.get_klines(binance_symbol, "5m", 36),
            )
        except Exception as e:
            logger.warning(f"MTF: Failed to fetch candles for {symbol}: {e}")
            return None

        if not all([candles_4h, candles_1h, candles_15m, candles_5m]):
            logger.warning(f"MTF: Missing candle data for {symbol}")
            return None

        df_4h = self._candles_to_dataframe(candles_4h)
        df_1h = self._candles_to_dataframe(candles_1h)
        df_15m = self._candles_to_dataframe(candles_15m)
        df_5m = self._candles_to_dataframe(candles_5m)

        # Minimum data requirements
        if len(df_4h) < 20 or len(df_1h) < 20 or len(df_15m) < 15 or len(df_5m) < 10:
            logger.info(f"MTF: {symbol} — insufficient candle history")
            return None

        # Step 1: 4h trend direction
        trend = self._analyze_4h_trend(df_4h)
        if trend["bias"] == "NEUTRAL":
            logger.info(f"MTF: {symbol} — 4h trend NEUTRAL (ADX={trend['adx']:.1f}), skip")
            return None

        # Step 2: 1h trend confirmation
        confirmation = self._analyze_1h_confirmation(df_1h, trend["bias"])
        if not confirmation["confirmed"]:
            logger.info(
                f"MTF: {symbol} — 1h does not confirm {trend['bias']} "
                f"(EMA aligned={confirmation['ema_aligned']}, RSI={confirmation['rsi_1h']:.1f})"
            )
            return None

        # Step 3: 15m entry zone
        entry_zone = self._analyze_15m_entry_zone(df_15m, trend["bias"])
        if not entry_zone["at_zone"]:
            logger.info(f"MTF: {symbol} — not at 15m entry zone, skip")
            return None

        # Step 4: 5m entry trigger
        trigger = self._analyze_5m_trigger(df_5m, trend["bias"])
        if not trigger["triggered"]:
            logger.info(f"MTF: {symbol} — no 5m trigger (pattern={trigger['pattern']}), skip")
            return None

        # All 4 aligned — compute SL
        current_price = float(df_5m["close"].iloc[-1])
        sl = self._calculate_mtf_sl(
            entry_zone["structure_level"], trigger["atr_5m"], trend["bias"], current_price
        )
        if sl is None:
            sl_pct = abs(current_price - entry_zone["structure_level"]) / current_price
            logger.info(f"MTF: {symbol} — SL rejected (structure_pct={sl_pct:.3%}), skip")
            return None

        logger.info(
            f"MTF ALIGNED: {symbol} {trend['bias']} | 4h={trend['ema_trend']} ADX={trend['adx']:.1f} | "
            f"1h RSI={confirmation['rsi_1h']:.1f} | 15m zone={entry_zone['structure_level']:.6f} | "
            f"5m pattern={trigger['pattern']} | SL={sl:.6f} ({abs(current_price - sl) / current_price:.2%})"
        )

        return {
            "bias": trend["bias"],
            "current_price": current_price,
            "stop_loss": sl,
            "tp_zones": confirmation["tp_zones"],
            "structure_level": entry_zone["structure_level"],
            "trigger_pattern": trigger["pattern"],
            "atr_5m": trigger["atr_5m"],
            "timeframe_details": {
                "4h": trend,
                "1h": confirmation,
                "15m": entry_zone,
                "5m": trigger,
            },
        }

    # ------------------------------------------------------------------
    # Standard single-timeframe indicators (kept for LLM consumption)
    # ------------------------------------------------------------------

    async def get_technical_indicators(
        self,
        symbol: str,
        interval: str = "4h",
        lookback_periods: int = 100,
    ) -> dict[str, Any]:
        binance_symbol = to_binance_symbol(symbol)

        candles = await self.info_service.get_klines(
            symbol=binance_symbol, interval=interval, limit=lookback_periods
        )
        if not candles:
            return {}

        df = self._candles_to_dataframe(candles)

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
        indicators["atr_14"] = float(
            ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
            .average_true_range()
            .iloc[-1]
        )

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

        return self._sanitize(indicators)

    # ------------------------------------------------------------------
    # LLM prompt and analysis
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self, exchange_name: str = "Binance Futures", mtf_mode: bool = False
    ) -> str:
        from app.config import settings

        tp_min = settings.llm_tp_min_pct * 100
        tp_max = settings.llm_tp_max_pct * 100
        sl_min = settings.llm_sl_min_pct * 100
        sl_max = settings.llm_sl_max_pct * 100
        min_conf = settings.llm_min_confidence
        min_rr = settings.llm_min_risk_reward_ratio
        min_adx = settings.llm_min_adx

        base = f"""You are an expert cryptocurrency market analyst specializing in LEVERAGED perpetual futures trading on {exchange_name}.
Analyze the provided technical indicators and market data, then provide a trading recommendation.

CRITICAL — LEVERAGE-AWARE TP/SL RULES:
These trades use leverage (typically 3x-20x). With leverage, small price moves create large P&L:
- At 5x leverage: a 2% price move = 10% P&L
- At 10x leverage: a 1% price move = 10% P&L
- At 15x leverage: a 0.7% price move = ~10% P&L

Therefore, your TP and SL MUST be tight and realistic:
- Take Profit: Set {tp_min}-{tp_max}% from entry price (NOT 5-10%). These are short-term leveraged trades.
- Stop Loss: Set {sl_min}-{sl_max}% from entry price. Keep it tight to protect capital with leverage.
- Do NOT set TP at 5%+ away — that would require a massive move and the trade will likely get stopped out first.

Your response MUST be valid JSON with the following structure:
{{
    "direction": "long" | "short" | "neutral",
    "confidence": 0.0-1.0,
    "entry_price": float,
    "take_profit_price": float,
    "stop_loss_price": float,
    "leverage": 1-20,
    "reasoning": "detailed analysis explanation",
    "key_factors": ["factor1", "factor2", "factor3"],
    "risk_level": "low" | "medium" | "high"
}}"""

        if mtf_mode:
            base += """

MULTI-TIMEFRAME PRE-FILTER PASSED:
This symbol has already passed a strict 4-timeframe alignment check:
- 4h: Trend direction confirmed (higher highs/lows or lower highs/lows + EMA alignment)
- 1h: Trend confirmation + key TP zones identified
- 15m: Price is AT a valid entry zone (support for BUY, resistance for SELL)
- 5m: Entry trigger candle pattern detected

The indicators include MTF context:
- mtf_bias: The directional bias (BUY/SELL) — your direction SHOULD align with this bias
- mtf_stop_loss: Structure-based SL (behind 15m structure + 1x 5m ATR) — use this as your SL
- mtf_tp_zones: Key 1h zones for take profit — pick the nearest that gives good R:R

Since all 4 timeframes align, you should have HIGHER confidence (0.70+) unless momentum indicators
strongly disagree. Focus on confirming the setup from a momentum/volume perspective.
Do NOT return "neutral" just because you would normally be cautious — the 4-TF filter already did the filtering."""
        else:
            base += f"""

EXTREME RSI SHORT SETUPS (RSI >= 99):
When RSI >= 99, the coin has pumped hard and is extremely overbought. This is a SHORT opportunity:
- Recommend "short" with confidence 0.65-0.80 if RSI >= 99 AND volume is high AND ADX is strong
- Use a TIGHT stop loss: {sl_min}-{sl_max}% above current price
- Take profit: {tp_min}-{tp_max}% below current price
- Do NOT recommend "long" when RSI >= 99
- Do NOT return "neutral" just because RSI is at extreme — extreme RSI IS the signal

STRICT FILTERING — return "neutral" if ANY of these apply:
- ADX < {min_adx} (no clear trend — avoid choppy/ranging markets)
- RSI between 40-60 with no clear divergence (indecisive momentum)
- MACD histogram near zero with no clear crossover forming
- Price is mid-range within Bollinger Bands with no directional pressure
- Volume is declining (current volume < average volume)
- Multiple indicators conflict (e.g., RSI says oversold but MACD is bearish)
- Funding rate is extreme and against your direction (>0.03% for longs, <-0.03% for shorts)"""

        base += f"""

Guidelines for valid signals:
- Only recommend trades with confidence > {min_conf} (be conservative)
- Risk-reward ratio MUST be at least {min_rr}:1 — if you can't find a setup with this ratio, return neutral
- TP should be {tp_min}-{tp_max}% from entry, SL should be {sl_min}-{sl_max}% from entry
- Use ATR to gauge recent volatility — if ATR/price < 1%, use tighter TP/SL
- Factor in funding rate for position costs
- Prefer trades where EMA_9 > EMA_21 (for longs) or EMA_9 < EMA_21 (for shorts)
- Higher leverage = tighter TP/SL required
- When in doubt, return "neutral" — it is better to miss a trade than to enter a bad one"""

        return base

    async def analyze_market(
        self,
        symbol: str,
        model: str,
        indicators: dict[str, Any],
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        mtf_mode = "mtf_bias" in indicators
        system_prompt = self._build_system_prompt("Binance Futures", mtf_mode=mtf_mode)

        # Build user prompt with MTF context if available
        mtf_section = ""
        if mtf_mode:
            mtf_section = f"""
Multi-Timeframe Context:
- Directional Bias: {indicators.get("mtf_bias")}
- Structure-based Stop Loss: {indicators.get("mtf_stop_loss")}
- 1h TP Zones: {indicators.get("mtf_tp_zones")}
- 5m Trigger Pattern: {indicators.get("mtf_trigger_pattern")}
- 15m Structure Level: {indicators.get("mtf_structure_level")}
"""

        user_prompt = f"""Analyze {symbol} on Binance Futures for a potential trade opportunity.
{mtf_section}
Technical Indicators (4h):
{json.dumps({k: v for k, v in indicators.items() if not k.startswith("mtf_")}, indent=2)}

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
            response = response.strip()

            try:
                analysis = json.loads(response)
            except json.JSONDecodeError:
                match = re.search(r"\{[\s\S]*\}", response)
                if not match:
                    raise
                cleaned = match.group()
                cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
                analysis = json.loads(cleaned)
            analysis["model"] = model
            analysis["symbol"] = symbol

            logger.info(
                f"[{model}] {symbol}: direction={analysis.get('direction')}, "
                f"confidence={analysis.get('confidence')}"
            )
            return analysis

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse LLM response as JSON: {e}\n"
                f"Raw response (first 300 chars): {response[:300]}"
            )
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
