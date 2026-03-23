import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError
from app.core.security import decrypt_data, encrypt_data
from app.models.exchange_connection import (
    ExchangeConnection,
    ExchangeConnectionStatus,
    ExchangeType,
)
from app.models.user import User

logger = logging.getLogger(__name__)


class ExchangeConnectionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def connect_exchange(
        self,
        user: User,
        exchange_type: str,
        api_key: str,
        api_secret: str,
        is_testnet: bool = False,
        label: str | None = None,
    ) -> ExchangeConnection:
        """Connect a new exchange by validating credentials and storing encrypted keys."""
        # Validate exchange type
        try:
            ex_type = ExchangeType(exchange_type)
        except ValueError as e:
            raise BadRequestError(f"Unsupported exchange type: {exchange_type}") from e

        # Check for existing connection
        existing = await self.db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user.id,
                ExchangeConnection.exchange_type == ex_type,
                ExchangeConnection.is_testnet == is_testnet,
                ExchangeConnection.status == ExchangeConnectionStatus.ACTIVE,
            )
        )
        if existing.scalar_one_or_none():
            net_label = "testnet" if is_testnet else "mainnet"
            raise ConflictError(
                f"You already have an active {exchange_type} {net_label} connection"
            )

        # Validate credentials by making a test API call
        await self._validate_binance_credentials(api_key, api_secret, is_testnet)

        # Encrypt and store
        connection = ExchangeConnection(
            user_id=user.id,
            exchange_type=ex_type,
            label=label,
            encrypted_api_key=encrypt_data(api_key),
            encrypted_api_secret=encrypt_data(api_secret),
            is_testnet=is_testnet,
            is_trading_enabled=False,
            status=ExchangeConnectionStatus.ACTIVE,
        )
        self.db.add(connection)
        await self.db.flush()
        await self.db.refresh(connection)

        logger.info(
            f"Exchange connected: {exchange_type} ({'testnet' if is_testnet else 'mainnet'}) "
            f"for user {user.id}"
        )
        return connection

    async def disconnect_exchange(self, connection: ExchangeConnection) -> None:
        """Disconnect an exchange connection."""
        connection.status = ExchangeConnectionStatus.INACTIVE
        connection.is_trading_enabled = False
        connection.encrypted_api_key = None
        connection.encrypted_api_secret = None

    async def get_user_connections(self, user_id: str) -> list[ExchangeConnection]:
        """Get all exchange connections for a user."""
        result = await self.db.execute(
            select(ExchangeConnection)
            .where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.status != ExchangeConnectionStatus.INACTIVE,
            )
            .order_by(ExchangeConnection.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_connection_by_id(
        self, connection_id: str, user_id: str
    ) -> ExchangeConnection | None:
        """Get a specific exchange connection."""
        result = await self.db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.id == connection_id,
                ExchangeConnection.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def toggle_trading(
        self, connection: ExchangeConnection, enabled: bool
    ) -> ExchangeConnection:
        """Enable or disable auto-trading for a connection."""
        connection.is_trading_enabled = enabled
        return connection

    async def sync_balance(self, connection: ExchangeConnection) -> dict:
        """Fetch latest balance from the exchange and update cached fields."""
        from app.services.binance import create_binance_exchange_service

        binance_exchange = await create_binance_exchange_service(connection)
        try:
            balance = await binance_exchange.get_balance()
            positions = await binance_exchange.get_positions()

            connection.balance_usd = balance["total_balance"]
            connection.margin_used = balance["margin_used"]
            connection.unrealized_pnl = balance["unrealized_pnl"]
            connection.last_sync_at = datetime.now(UTC)

            return {
                "balance_usd": balance["total_balance"],
                "positions_count": len(positions),
                **balance,
            }
        finally:
            await binance_exchange.close()

    def get_credentials(self, connection: ExchangeConnection) -> tuple[str, str]:
        """Decrypt and return (api_key, api_secret)."""
        api_key = decrypt_data(connection.encrypted_api_key)
        api_secret = decrypt_data(connection.encrypted_api_secret)
        return api_key, api_secret

    async def _validate_binance_credentials(
        self, api_key: str, api_secret: str, is_testnet: bool
    ) -> None:
        """Validate Binance API credentials by making a test call."""
        from app.services.binance.client import BinanceClient
        from app.services.binance.exchange import BinanceExchangeService

        client = BinanceClient(api_key=api_key, api_secret=api_secret, testnet=is_testnet)
        exchange = BinanceExchangeService(client)
        try:
            await exchange.get_account_info()
        except Exception as e:
            error_str = str(e)
            if "-2015" in error_str:
                raise BadRequestError(
                    "Binance rejected the API key. This usually means: "
                    "(1) IP restriction is enabled on your API key but StackAlpha's "
                    "server IP is not whitelisted — go to Binance API Management, "
                    "edit your key, and add the server IP shown in setup instructions, OR "
                    "(2) Futures trading permission is not enabled on the key, OR "
                    "(3) The API key or secret is incorrect. "
                    "Please check and try again."
                ) from e
            elif "-2014" in error_str or "API-key format invalid" in error_str:
                raise BadRequestError(
                    "Invalid API key format. Please double-check you copied "
                    "the full API key from Binance."
                ) from e
            elif "-1022" in error_str or "Signature" in error_str:
                raise BadRequestError(
                    "Invalid API secret. The key looks valid but the secret "
                    "doesn't match. Please re-copy your API secret from Binance."
                ) from e
            else:
                raise BadRequestError(
                    f"Failed to connect to Binance: {e}. "
                    "Please check your API credentials and try again."
                ) from e
        finally:
            await exchange.close()
