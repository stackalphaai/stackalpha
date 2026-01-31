import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.core.exceptions import HyperliquidAPIError

logger = logging.getLogger(__name__)


class HyperliquidClient:
    def __init__(self, use_testnet: bool | None = None):
        self.use_testnet = (
            use_testnet if use_testnet is not None else settings.hyperliquid_use_testnet
        )
        self.base_url = (
            settings.hyperliquid_testnet_url
            if self.use_testnet
            else settings.hyperliquid_mainnet_url
        )
        self.ws_url = (
            settings.hyperliquid_ws_testnet if self.use_testnet else settings.hyperliquid_ws_mainnet
        )
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self.get_client()

        try:
            if method.upper() == "POST":
                response = await client.post(endpoint, json=data)
            else:
                response = await client.get(endpoint, params=data)

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"Hyperliquid API error: {e.response.status_code} - {e.response.text}")
            raise HyperliquidAPIError(f"API request failed: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Hyperliquid request error: {str(e)}")
            raise HyperliquidAPIError(f"Request failed: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error in Hyperliquid client: {str(e)}")
            raise HyperliquidAPIError(f"Unexpected error: {str(e)}") from e

    async def info_request(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/info", data)

    async def exchange_request(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/exchange", data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


_client_instance: HyperliquidClient | None = None


def get_hyperliquid_client() -> HyperliquidClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = HyperliquidClient()
    return _client_instance


async def close_hyperliquid_client():
    global _client_instance
    if _client_instance:
        await _client_instance.close()
        _client_instance = None
