import logging
from typing import Any

from app.config import settings
from app.models import Signal, SignalDirection, TradeDirection

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self.max_position_size_percent = settings.max_position_size_percent
        self.max_leverage = settings.max_leverage
        self.default_leverage = settings.default_leverage
        self.max_concurrent_positions = settings.max_concurrent_positions

    def calculate_position_size(
        self,
        available_balance: float,
        signal: Signal,
        risk_percent: float | None = None,
    ) -> dict[str, float]:
        risk_pct = risk_percent or signal.suggested_position_size_percent
        risk_pct = min(risk_pct, self.max_position_size_percent)

        position_size_usd = available_balance * (risk_pct / 100)

        if signal.entry_price and signal.stop_loss_price:
            stop_distance = abs(signal.entry_price - signal.stop_loss_price)
            stop_distance_pct = stop_distance / signal.entry_price

            if stop_distance_pct > 0:
                max_loss = available_balance * (risk_pct / 100)
                risk_based_size = max_loss / stop_distance_pct
                position_size_usd = min(position_size_usd, risk_based_size)

        position_size_units = position_size_usd / signal.entry_price if signal.entry_price else 0

        return {
            "position_size_usd": round(position_size_usd, 2),
            "position_size_units": round(position_size_units, 8),
            "risk_percent": round(risk_pct, 2),
        }

    def calculate_stop_loss(
        self,
        entry_price: float,
        direction: SignalDirection | TradeDirection,
        atr: float,
        multiplier: float = 1.5,
    ) -> float:
        stop_distance = atr * multiplier

        if direction in [SignalDirection.LONG, TradeDirection.LONG]:
            return round(entry_price - stop_distance, 6)
        else:
            return round(entry_price + stop_distance, 6)

    def calculate_take_profit(
        self,
        entry_price: float,
        stop_loss: float,
        direction: SignalDirection | TradeDirection,
        risk_reward_ratio: float = 2.0,
    ) -> float:
        risk = abs(entry_price - stop_loss)
        reward = risk * risk_reward_ratio

        if direction in [SignalDirection.LONG, TradeDirection.LONG]:
            return round(entry_price + reward, 6)
        else:
            return round(entry_price - reward, 6)

    def validate_leverage(self, leverage: int) -> int:
        return max(1, min(leverage, self.max_leverage))

    def calculate_liquidation_price(
        self,
        entry_price: float,
        leverage: int,
        direction: SignalDirection | TradeDirection,
        maintenance_margin_rate: float = 0.005,
    ) -> float:
        if leverage <= 0:
            return 0.0

        margin_ratio = 1 / leverage

        if direction in [SignalDirection.LONG, TradeDirection.LONG]:
            liquidation_price = entry_price * (1 - margin_ratio + maintenance_margin_rate)
        else:
            liquidation_price = entry_price * (1 + margin_ratio - maintenance_margin_rate)

        return round(liquidation_price, 6)

    def calculate_risk_reward_ratio(
        self,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        direction: SignalDirection | TradeDirection,
    ) -> float:
        if direction in [SignalDirection.LONG, TradeDirection.LONG]:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit

        if risk <= 0:
            return 0.0

        return round(reward / risk, 2)

    def assess_trade_risk(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        leverage: int,
        direction: SignalDirection | TradeDirection,
        position_size_usd: float,
        account_balance: float,
    ) -> dict[str, Any]:
        rr_ratio = self.calculate_risk_reward_ratio(entry_price, take_profit, stop_loss, direction)

        liquidation_price = self.calculate_liquidation_price(entry_price, leverage, direction)

        if direction in [SignalDirection.LONG, TradeDirection.LONG]:
            stop_distance_pct = (entry_price - stop_loss) / entry_price * 100
        else:
            stop_distance_pct = (stop_loss - entry_price) / entry_price * 100

        max_loss_pct = stop_distance_pct * leverage
        max_loss_usd = position_size_usd * (max_loss_pct / 100)
        account_risk_pct = (max_loss_usd / account_balance * 100) if account_balance > 0 else 100

        if account_risk_pct > 20:
            risk_level = "critical"
        elif account_risk_pct > 10:
            risk_level = "high"
        elif account_risk_pct > 5:
            risk_level = "medium"
        else:
            risk_level = "low"

        warnings = []

        if rr_ratio < 1.5:
            warnings.append(f"Risk-reward ratio ({rr_ratio}:1) is below recommended 1.5:1")

        if leverage > 10:
            warnings.append(f"High leverage ({leverage}x) increases liquidation risk")

        if account_risk_pct > 5:
            warnings.append(f"Trade risks {account_risk_pct:.1f}% of account balance")

        if direction in [SignalDirection.LONG, TradeDirection.LONG]:
            if liquidation_price >= stop_loss:
                warnings.append("Liquidation price is above stop loss")
        else:
            if liquidation_price <= stop_loss:
                warnings.append("Liquidation price is below stop loss")

        return {
            "risk_reward_ratio": rr_ratio,
            "liquidation_price": liquidation_price,
            "stop_distance_percent": round(stop_distance_pct, 2),
            "max_loss_percent": round(max_loss_pct, 2),
            "max_loss_usd": round(max_loss_usd, 2),
            "account_risk_percent": round(account_risk_pct, 2),
            "risk_level": risk_level,
            "warnings": warnings,
            "is_acceptable": len(warnings) <= 1 and risk_level in ["low", "medium"],
        }


_risk_manager_instance: RiskManager | None = None


def get_risk_manager() -> RiskManager:
    global _risk_manager_instance
    if _risk_manager_instance is None:
        _risk_manager_instance = RiskManager()
    return _risk_manager_instance
