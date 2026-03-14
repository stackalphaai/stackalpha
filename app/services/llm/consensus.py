import asyncio
import logging
from typing import Any

from app.config import settings
from app.models import SignalDirection
from app.services.hyperliquid import get_info_service
from app.services.llm.analyzer import get_market_analyzer

logger = logging.getLogger(__name__)


class ConsensusEngine:
    def __init__(self, analyzer=None, info_service=None):
        self.analyzer = analyzer or get_market_analyzer()
        self.info_service = info_service or get_info_service()
        self.models = settings.llm_models
        self.threshold = settings.llm_consensus_threshold

    async def generate_signal(self, symbol: str) -> dict[str, Any] | None:
        try:
            indicators = await self.analyzer.get_technical_indicators(symbol)
            if not indicators:
                logger.warning(f"No technical indicators available for {symbol}")
                return None

            market_data = await self.info_service.get_market_data(symbol)
            if not market_data:
                logger.warning(f"No market data available for {symbol}")
                return None

            tasks = [
                self.analyzer.analyze_market(symbol, model, indicators, market_data)
                for model in self.models
            ]

            analyses = await asyncio.gather(*tasks, return_exceptions=True)

            valid_analyses = []
            failed_models = []
            neutral_models = []
            for i, analysis in enumerate(analyses):
                model_name = self.models[i] if i < len(self.models) else f"model-{i}"
                if isinstance(analysis, Exception):
                    logger.error(f"LLM analysis failed for {symbol} with {model_name}: {analysis}")
                    failed_models.append(model_name)
                    continue
                if analysis.get("error"):
                    logger.warning(
                        f"LLM returned error for {symbol} with {model_name}: {analysis.get('error')}"
                    )
                    failed_models.append(model_name)
                    continue
                if analysis.get("direction") == "neutral":
                    neutral_models.append(model_name)
                    continue
                valid_analyses.append(analysis)

            if failed_models:
                logger.warning(
                    f"Models failed for {symbol}: {', '.join(failed_models)} "
                    f"({len(failed_models)}/{len(self.models)})"
                )
            if neutral_models:
                logger.info(f"Models returned neutral for {symbol}: {', '.join(neutral_models)}")

            if not valid_analyses:
                logger.info(
                    f"No valid analyses for {symbol} — "
                    f"{len(failed_models)} failed, {len(neutral_models)} neutral"
                )
                return None

            signal = self._build_consensus(symbol, valid_analyses, market_data, indicators)
            return signal

        except Exception as e:
            logger.error(f"Error generating signal for {symbol}: {e}")
            return None

    def _build_consensus(
        self,
        symbol: str,
        analyses: list[dict[str, Any]],
        market_data: dict[str, Any],
        indicators: dict[str, Any],
    ) -> dict[str, Any] | None:
        total_votes = len(analyses)
        if total_votes == 0:
            return None

        direction_votes = {"long": 0, "short": 0}
        direction_confidences = {"long": [], "short": []}

        for analysis in analyses:
            direction = analysis.get("direction")
            confidence = analysis.get("confidence", 0)

            if direction in direction_votes:
                direction_votes[direction] += 1
                direction_confidences[direction].append(confidence)

        winning_direction = max(direction_votes, key=direction_votes.get)
        consensus_votes = direction_votes[winning_direction]
        consensus_ratio = consensus_votes / total_votes

        if consensus_ratio < self.threshold:
            logger.info(
                f"No consensus reached for {symbol}: {consensus_ratio:.2f} < {self.threshold}"
            )
            return None

        relevant_analyses = [a for a in analyses if a.get("direction") == winning_direction]

        avg_confidence = sum(direction_confidences[winning_direction]) / len(
            direction_confidences[winning_direction]
        )

        if avg_confidence < 0.6:
            logger.info(f"Confidence too low for {symbol}: {avg_confidence:.2f}")
            return None

        entry_prices = [a.get("entry_price") for a in relevant_analyses if a.get("entry_price")]
        tp_prices = [
            a.get("take_profit_price") for a in relevant_analyses if a.get("take_profit_price")
        ]
        sl_prices = [
            a.get("stop_loss_price") for a in relevant_analyses if a.get("stop_loss_price")
        ]
        leverages = [a.get("leverage") for a in relevant_analyses if a.get("leverage")]

        current_price = market_data.get("mark_price", indicators.get("current_price", 0))

        entry_price = sum(entry_prices) / len(entry_prices) if entry_prices else current_price
        take_profit = (
            sum(tp_prices) / len(tp_prices)
            if tp_prices
            else self._calculate_tp(entry_price, winning_direction, indicators.get("atr_14", 0))
        )
        stop_loss = (
            sum(sl_prices) / len(sl_prices)
            if sl_prices
            else self._calculate_sl(entry_price, winning_direction, indicators.get("atr_14", 0))
        )
        leverage = int(sum(leverages) / len(leverages)) if leverages else 5

        leverage = max(1, min(leverage, settings.max_leverage))

        # Clamp TP/SL to leverage-friendly ranges (1-3% TP, 0.5-2% SL)
        # This prevents LLMs from setting unrealistically wide targets
        take_profit = self._clamp_tp(entry_price, take_profit, winning_direction)
        stop_loss = self._clamp_sl(entry_price, stop_loss, winning_direction)

        all_reasoning = [a.get("reasoning", "") for a in relevant_analyses]
        all_factors = []
        for a in relevant_analyses:
            all_factors.extend(a.get("key_factors", []))

        unique_factors = list(set(all_factors))[:5]

        signal_data = {
            "symbol": symbol,
            "direction": SignalDirection.LONG
            if winning_direction == "long"
            else SignalDirection.SHORT,
            "entry_price": entry_price,
            "take_profit_price": take_profit,
            "stop_loss_price": stop_loss,
            "suggested_leverage": leverage,
            "suggested_position_size_percent": self._calculate_position_size(
                avg_confidence, indicators.get("atr_14", 0) / current_price if current_price else 0
            ),
            "confidence_score": round(avg_confidence, 4),
            "consensus_votes": consensus_votes,
            "total_votes": total_votes,
            "market_price_at_creation": current_price,
            "technical_indicators": indicators,
            "llm_responses": [
                {
                    "model": a.get("model"),
                    "direction": a.get("direction"),
                    "confidence": a.get("confidence"),
                    "reasoning": a.get("reasoning", "")[:500],
                }
                for a in relevant_analyses
            ],
            "analysis_data": {
                "key_factors": unique_factors,
                "combined_reasoning": " | ".join(r[:200] for r in all_reasoning if r),
                "market_data": market_data,
            },
        }

        return signal_data

    def _calculate_tp(
        self,
        entry_price: float,
        direction: str,
        atr: float,
    ) -> float:
        # For leveraged futures: TP should be tight (1-3% from entry).
        # Use ATR as a guide but cap it to a max of 3% of entry price.
        atr_based = atr * 1.5
        max_tp_distance = entry_price * 0.025  # 2.5% max
        min_tp_distance = entry_price * 0.01  # 1% min
        tp_distance = max(min_tp_distance, min(atr_based, max_tp_distance))

        if direction == "long":
            return entry_price + tp_distance
        else:
            return entry_price - tp_distance

    def _calculate_sl(
        self,
        entry_price: float,
        direction: str,
        atr: float,
    ) -> float:
        # For leveraged futures: SL should be tight (0.5-2% from entry).
        # Use ATR as a guide but cap it to protect capital with leverage.
        atr_based = atr * 1.0
        max_sl_distance = entry_price * 0.015  # 1.5% max
        min_sl_distance = entry_price * 0.005  # 0.5% min
        sl_distance = max(min_sl_distance, min(atr_based, max_sl_distance))

        if direction == "long":
            return entry_price - sl_distance
        else:
            return entry_price + sl_distance

    def _clamp_tp(
        self,
        entry_price: float,
        tp_price: float,
        direction: str,
    ) -> float:
        """Clamp TP to a max of 3% from entry (leverage-friendly)."""
        max_tp_pct = 0.03  # 3%
        min_tp_pct = 0.008  # 0.8%

        if direction == "long":
            tp_pct = (tp_price - entry_price) / entry_price if entry_price else 0
            if tp_pct > max_tp_pct:
                return entry_price * (1 + max_tp_pct)
            if tp_pct < min_tp_pct:
                return entry_price * (1 + min_tp_pct)
        else:
            tp_pct = (entry_price - tp_price) / entry_price if entry_price else 0
            if tp_pct > max_tp_pct:
                return entry_price * (1 - max_tp_pct)
            if tp_pct < min_tp_pct:
                return entry_price * (1 - min_tp_pct)
        return tp_price

    def _clamp_sl(
        self,
        entry_price: float,
        sl_price: float,
        direction: str,
    ) -> float:
        """Clamp SL to a max of 2% from entry (leverage-friendly)."""
        max_sl_pct = 0.02  # 2%
        min_sl_pct = 0.004  # 0.4%

        if direction == "long":
            sl_pct = (entry_price - sl_price) / entry_price if entry_price else 0
            if sl_pct > max_sl_pct:
                return entry_price * (1 - max_sl_pct)
            if sl_pct < min_sl_pct:
                return entry_price * (1 - min_sl_pct)
        else:
            sl_pct = (sl_price - entry_price) / entry_price if entry_price else 0
            if sl_pct > max_sl_pct:
                return entry_price * (1 + max_sl_pct)
            if sl_pct < min_sl_pct:
                return entry_price * (1 + min_sl_pct)
        return sl_price

    def _calculate_position_size(
        self,
        confidence: float,
        volatility_ratio: float,
    ) -> float:
        base_size = settings.max_position_size_percent

        confidence_factor = 0.5 + (confidence * 0.5)

        volatility_factor = 1.0
        if volatility_ratio > 0.05:
            volatility_factor = 0.5
        elif volatility_ratio > 0.03:
            volatility_factor = 0.75

        position_size = base_size * confidence_factor * volatility_factor
        return round(max(1.0, min(position_size, base_size)), 2)


_consensus_engine_instance: ConsensusEngine | None = None


def get_consensus_engine() -> ConsensusEngine:
    global _consensus_engine_instance
    if _consensus_engine_instance is None:
        _consensus_engine_instance = ConsensusEngine()
    return _consensus_engine_instance


_binance_consensus_engine_instance: ConsensusEngine | None = None


def get_binance_consensus_engine() -> ConsensusEngine:
    global _binance_consensus_engine_instance
    if _binance_consensus_engine_instance is None:
        from app.services.binance import get_binance_info_service
        from app.services.llm.binance_analyzer import get_binance_market_analyzer

        _binance_consensus_engine_instance = ConsensusEngine(
            analyzer=get_binance_market_analyzer(),
            info_service=get_binance_info_service(),
        )
    return _binance_consensus_engine_instance
