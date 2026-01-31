import re


def validate_ethereum_address(address: str) -> bool:
    if not address:
        return False

    if not address.startswith("0x"):
        return False

    if len(address) != 42:
        return False

    try:
        int(address, 16)
    except ValueError:
        return False

    return True


def validate_signature(signature: str) -> bool:
    if not signature:
        return False

    if not signature.startswith("0x"):
        return False

    if len(signature) != 132:
        return False

    try:
        int(signature, 16)
    except ValueError:
        return False

    return True


def validate_trading_symbol(symbol: str) -> bool:
    if not symbol:
        return False

    pattern = r"^[A-Z]{2,10}$"
    return bool(re.match(pattern, symbol.upper()))


def validate_leverage(leverage: int, max_leverage: int = 20) -> bool:
    return 1 <= leverage <= max_leverage


def validate_position_size_percent(percent: float, max_percent: float = 100.0) -> bool:
    return 0 < percent <= max_percent


def sanitize_string(value: str, max_length: int = 255) -> str:
    if not value:
        return ""

    sanitized = re.sub(r"[<>\"']", "", value)

    return sanitized[:max_length].strip()
