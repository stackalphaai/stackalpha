"""Binance symbol format conversion utilities."""


def to_binance_symbol(symbol: str) -> str:
    """Convert internal symbol format to Binance Futures format.

    'BTC' -> 'BTCUSDT'
    'BTCUSDT' -> 'BTCUSDT' (already in format)
    """
    if symbol.endswith("USDT"):
        return symbol
    return f"{symbol}USDT"


def from_binance_symbol(symbol: str) -> str:
    """Convert Binance Futures symbol to internal format.

    'BTCUSDT' -> 'BTC'
    'BTC' -> 'BTC' (already in format)
    """
    if symbol.endswith("USDT"):
        return symbol[: -len("USDT")]
    return symbol
