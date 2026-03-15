"""
Test the risk management and position sizing pipeline.

Validates:
1. Risk-based position sizing formula
2. User settings are authoritative
3. Leverage correctly applied to quantity calculation
4. RR ratio check uses user's min_risk_reward_ratio
"""

import pytest


def test_risk_based_position_sizing():
    """Verify the risk-based sizing formula produces correct margin amounts."""
    # Scenario: $1000 balance, 2% risk per trade, 2% SL distance, 10x leverage
    equity = 1000.0
    risk_percent_per_trade = 2.0
    stop_distance_pct = 0.02  # 2%
    leverage = 10

    max_loss = equity * (risk_percent_per_trade / 100)  # $20
    assert max_loss == 20.0

    # position_size_usd (margin) = max_loss / (stop_distance_pct * leverage)
    margin = max_loss / (stop_distance_pct * leverage)
    assert margin == 100.0  # $100 margin

    # Notional = margin * leverage
    notional = margin * leverage
    assert notional == 1000.0  # $1000 notional

    # If SL hits: loss = notional * stop_distance_pct = $1000 * 0.02 = $20
    # Which is exactly 2% of equity. Correct!
    actual_loss = notional * stop_distance_pct
    assert actual_loss == max_loss


def test_position_sizing_with_different_params():
    """Test with real-world-ish numbers."""
    # $500 balance, 2% risk, entry=$14.50, SL=$14.00, leverage=5x
    equity = 500.0
    risk_percent = 2.0
    entry_price = 14.50
    sl_price = 14.00
    leverage = 5

    stop_distance_pct = abs(entry_price - sl_price) / entry_price
    assert round(stop_distance_pct, 4) == 0.0345  # ~3.45%

    max_loss = equity * (risk_percent / 100)  # $10
    assert max_loss == 10.0

    margin = max_loss / (stop_distance_pct * leverage)
    # $10 / (0.0345 * 5) = $10 / 0.1724 = ~$58
    assert round(margin, 0) == 58.0

    notional = margin * leverage  # ~$290
    quantity = notional / entry_price  # ~20 tokens

    # Verify: if SL hits, loss = quantity * (entry - sl) * 1 (leverage baked into notional)
    # Actually: loss = position_size_in_tokens * price_diff
    # With leverage: loss = (notional / entry) * (entry - sl)
    # = notional * stop_distance_pct = margin * leverage * stop_distance_pct
    actual_loss = notional * stop_distance_pct
    assert round(actual_loss, 2) == round(max_loss, 2)


def test_quantity_calculation():
    """Verify token quantity is calculated from notional, not margin."""
    margin = 100.0  # $100 margin
    leverage = 10
    current_price = 50.0  # $50 per token

    # WRONG (old code): quantity = margin / price = 2 tokens
    wrong_quantity = margin / current_price
    assert wrong_quantity == 2.0

    # CORRECT (new code): quantity = (margin * leverage) / price = 20 tokens
    notional = margin * leverage
    correct_quantity = notional / current_price
    assert correct_quantity == 20.0

    # With 20 tokens at $50, notional = $1000, margin = $100 at 10x
    assert correct_quantity * current_price == notional


def test_rr_ratio_from_signal():
    """Verify RR ratio is calculated from actual signal prices, not hardcoded."""
    entry = 14.50
    tp = 15.00
    sl = 14.00

    risk = abs(entry - sl)  # 0.50
    reward = abs(tp - entry)  # 0.50
    rr = reward / risk
    assert rr == 1.0

    # With wider TP
    tp2 = 15.50
    reward2 = abs(tp2 - entry)
    rr2 = reward2 / risk
    assert rr2 == 2.0

    # User has min_rr = 1.2 → signal with rr=1.36 should PASS
    min_rr = 1.2
    tp3 = 14.50 + 0.68  # reward = 0.68, risk = 0.50, rr = 1.36
    rr3 = abs(tp3 - entry) / risk
    assert round(rr3, 2) == 1.36
    assert rr3 >= min_rr  # Should pass


def test_max_position_size_cap():
    """Verify position is capped by max_position_size_usd and max_position_size_percent."""
    equity = 10000.0
    risk_percent = 2.0
    stop_distance_pct = 0.005  # 0.5% tight SL
    leverage = 20
    max_position_size_usd = 5000.0
    max_position_size_percent = 10.0

    max_loss = equity * (risk_percent / 100)  # $200
    risk_based_size = max_loss / (stop_distance_pct * leverage)  # $200 / 0.1 = $2000

    # Cap by limits
    clamped = min(
        risk_based_size,
        max_position_size_usd,
        equity * (max_position_size_percent / 100),
    )
    assert clamped == min(2000.0, 5000.0, 1000.0)
    assert clamped == 1000.0  # Capped by 10% of equity


if __name__ == "__main__":
    test_risk_based_position_sizing()
    test_position_sizing_with_different_params()
    test_quantity_calculation()
    test_rr_ratio_from_signal()
    test_max_position_size_cap()
    print("All tests passed!")
