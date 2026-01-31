from datetime import UTC, datetime
from typing import TypeVar

T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_price(price: float, decimals: int = 2) -> str:
    return f"${price:,.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def format_pnl(pnl: float, decimals: int = 2) -> str:
    sign = "+" if pnl > 0 else ""
    return f"{sign}${pnl:,.{decimals}f}"


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def round_to_precision(value: float, precision: int) -> float:
    factor = 10**precision
    return round(value * factor) / factor


def clamp(value: T, min_value: T, max_value: T) -> T:
    return max(min_value, min(value, max_value))


def calculate_pnl_percent(
    entry_price: float,
    exit_price: float,
    is_long: bool,
    leverage: int = 1,
) -> float:
    if entry_price == 0:
        return 0.0

    if is_long:
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100

    return pnl_pct * leverage


def calculate_position_value(
    size: float,
    price: float,
    leverage: int = 1,
) -> float:
    return size * price


def calculate_margin_required(
    position_value: float,
    leverage: int,
) -> float:
    if leverage == 0:
        return position_value
    return position_value / leverage


def truncate_address(address: str, chars: int = 4) -> str:
    if len(address) <= chars * 2 + 3:
        return address
    return f"{address[: chars + 2]}...{address[-chars:]}"
