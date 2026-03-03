from app.services.binance.client import BinanceClient
from app.services.binance.exchange import BinanceExchangeService
from app.services.binance.info import BinanceInfoService
from app.services.binance.utils import from_binance_symbol, to_binance_symbol

# Singleton for public market data (no auth needed)
_info_service_instance: BinanceInfoService | None = None


def get_binance_info_service() -> BinanceInfoService:
    global _info_service_instance
    if _info_service_instance is None:
        _info_service_instance = BinanceInfoService()
    return _info_service_instance


async def close_binance_info_service():
    global _info_service_instance
    if _info_service_instance:
        await _info_service_instance.close()
        _info_service_instance = None


async def create_binance_exchange_service(
    exchange_connection,
) -> BinanceExchangeService:
    """Create a per-user BinanceExchangeService from an ExchangeConnection."""
    from app.core.security import decrypt_data

    api_key = decrypt_data(exchange_connection.encrypted_api_key)
    api_secret = decrypt_data(exchange_connection.encrypted_api_secret)
    client = BinanceClient(
        api_key=api_key,
        api_secret=api_secret,
        testnet=exchange_connection.is_testnet,
    )
    return BinanceExchangeService(client)


__all__ = [
    "BinanceClient",
    "BinanceInfoService",
    "BinanceExchangeService",
    "get_binance_info_service",
    "close_binance_info_service",
    "create_binance_exchange_service",
    "to_binance_symbol",
    "from_binance_symbol",
]
