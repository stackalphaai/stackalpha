import logging

from binance import AsyncClient

from app.core.exceptions import BinanceAPIError

logger = logging.getLogger(__name__)


class BinanceClient:
    """Thin wrapper around python-binance AsyncClient with lazy initialization."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self._client: AsyncClient | None = None

    async def get_client(self) -> AsyncClient:
        # Check if the cached client's event loop is still running.
        # Celery workers call asyncio.run() per task, which closes the loop
        # after each task — any cached aiohttp session becomes stale.
        if self._client is not None:
            try:
                loop = self._client.session._loop  # type: ignore[attr-defined]
                if loop.is_closed():
                    await self.close()
            except Exception:
                await self.close()

        if self._client is None:
            try:
                self._client = await AsyncClient.create(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    testnet=self.testnet,
                )
            except Exception as e:
                logger.error(f"Failed to create Binance client: {e}")
                raise BinanceAPIError(f"Failed to connect to Binance: {e}") from e
        return self._client

    async def close(self):
        if self._client:
            await self._client.close_connection()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
