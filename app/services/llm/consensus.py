import asyncio
import logging
from typing import Any

from app.config import settings
from app.models import SignalDirection
from app.services.hyperliquid import get_info_service
from app.services.llm.analyzer import MarketAnalyzer, get_market_analyzer

logger = logging.getLogger(__name__)


class ConsensusEngine:
    def __init__(self, analyzer: MarketAnalyzer | None = None):
        self.analyzer = analyzer or get_market_analyzer()
        self.info_service = get_info_service()
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
            for analysis in analyses:
                if isinstance(analysis, Exception):
                    logger.error(f"Analysis failed: {analysis}")
                    continue
                if analysis.get("error"):
                    continue
                if analysis.get("direction") != "neutral":
                    valid_analyses.append(analysis)

            if not valid_analyses:
                logger.info(f"No valid analyses for {symbol}")
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
            "entry_price": round(entry_price, 6),
            "take_profit_price": round(take_profit, 6),
            "stop_loss_price": round(stop_loss, 6),
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
        atr_multiplier = 2.0
        if direction == "long":
            return entry_price + (atr * atr_multiplier)
        else:
            return entry_price - (atr * atr_multiplier)

    def _calculate_sl(
        self,
        entry_price: float,
        direction: str,
        atr: float,
    ) -> float:
        atr_multiplier = 1.5
        if direction == "long":
            return entry_price - (atr * atr_multiplier)
        else:
            return entry_price + (atr * atr_multiplier)

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
