import hashlib
import json
import logging
import time
from typing import Any

from eth_account import Account

from app.config import settings
from app.core.exceptions import HyperliquidAPIError
from app.services.hyperliquid.client import HyperliquidClient, get_hyperliquid_client

logger = logging.getLogger(__name__)


class HyperliquidExchangeService:
    def __init__(self, client: HyperliquidClient | None = None):
        self.client = client or get_hyperliquid_client()
        self.is_mainnet = not settings.hyperliquid_use_testnet

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _sign_l1_action(
        self,
        private_key: str,
        action: dict[str, Any],
        nonce: int,
        vault_address: str | None = None,
    ) -> str:
        connection_id = hashlib.sha256(
            json.dumps(action, separators=(",", ":"), sort_keys=True).encode()
        ).digest()[:20]

        phantom_agent = {
            "source": "a" if self.is_mainnet else "b",
            "connectionId": connection_id.hex(),
        }

        data = {
            "domain": {
                "name": "Exchange",
                "version": "1",
                "chainId": 1 if self.is_mainnet else 421614,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Agent": [
                    {"name": "source", "type": "string"},
                    {"name": "connectionId", "type": "bytes20"},
                ],
            },
            "primaryType": "Agent",
            "message": phantom_agent,
        }

        account = Account.from_key(private_key)
        signed = account.sign_typed_data(
            domain_data=data["domain"],
            message_types={"Agent": data["types"]["Agent"]},
            message_data=phantom_agent,
        )

        return signed.signature.hex()

    async def place_order(
        self,
        private_key: str,
        coin: str,
        is_buy: bool,
        size: float,
        price: float,
        order_type: str = "limit",
        reduce_only: bool = False,
        time_in_force: str = "Gtc",
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        Account.from_key(private_key)

        nonce = self._get_timestamp()

        order = {
            "a": self._get_asset_index(coin),
            "b": is_buy,
            "p": str(price),
            "s": str(size),
            "r": reduce_only,
            "t": {
                "limit": {"tif": time_in_force},
            }
            if order_type == "limit"
            else {"trigger": {"isMarket": True, "triggerPx": str(price), "tpsl": "tp"}},
        }

        if client_order_id:
            order["c"] = client_order_id

        action = {
            "type": "order",
            "orders": [order],
            "grouping": "na",
        }

        signature = self._sign_l1_action(private_key, action, nonce)

        payload = {
            "action": action,
            "nonce": nonce,
            "signature": {
                "r": signature[:66],
                "s": "0x" + signature[66:130],
                "v": int(signature[130:], 16),
            },
        }

        try:
            return await self.client.exchange_request(payload)
        except Exception as e:
            logger.error(f"Error placing order: {str(e)}")
            raise HyperliquidAPIError(f"Failed to place order: {str(e)}") from e

    async def place_market_order(
        self,
        private_key: str,
        coin: str,
        is_buy: bool,
        size: float,
        slippage: float = 0.01,
    ) -> dict[str, Any]:
        from app.services.hyperliquid.info import get_info_service

        info_service = get_info_service()
        market_data = await info_service.get_market_data(coin)

        if not market_data:
            raise HyperliquidAPIError(f"Could not fetch market data for {coin}")

        mark_price = market_data.get("mark_price", 0)
        if is_buy:
            price = mark_price * (1 + slippage)
        else:
            price = mark_price * (1 - slippage)

        return await self.place_order(
            private_key=private_key,
            coin=coin,
            is_buy=is_buy,
            size=size,
            price=round(price, 6),
            order_type="limit",
            time_in_force="Ioc",
        )

    async def cancel_order(
        self,
        private_key: str,
        coin: str,
        order_id: int,
    ) -> dict[str, Any]:
        nonce = self._get_timestamp()

        action = {
            "type": "cancel",
            "cancels": [{"a": self._get_asset_index(coin), "o": order_id}],
        }

        signature = self._sign_l1_action(private_key, action, nonce)

        payload = {
            "action": action,
            "nonce": nonce,
            "signature": {
                "r": signature[:66],
                "s": "0x" + signature[66:130],
                "v": int(signature[130:], 16),
            },
        }

        try:
            return await self.client.exchange_request(payload)
        except Exception as e:
            logger.error(f"Error canceling order: {str(e)}")
            raise HyperliquidAPIError(f"Failed to cancel order: {str(e)}") from e

    async def cancel_all_orders(
        self,
        private_key: str,
        coin: str | None = None,
    ) -> dict[str, Any]:
        from app.services.hyperliquid.info import get_info_service

        account = Account.from_key(private_key)
        info_service = get_info_service()

        open_orders = await info_service.get_user_open_orders(account.address)

        if coin:
            open_orders = [o for o in open_orders if o.get("coin") == coin]

        if not open_orders:
            return {"status": "ok", "message": "No orders to cancel"}

        cancels = []
        for order in open_orders:
            cancels.append(
                {
                    "a": self._get_asset_index(order.get("coin")),
                    "o": order.get("oid"),
                }
            )

        nonce = self._get_timestamp()
        action = {"type": "cancel", "cancels": cancels}

        signature = self._sign_l1_action(private_key, action, nonce)

        payload = {
            "action": action,
            "nonce": nonce,
            "signature": {
                "r": signature[:66],
                "s": "0x" + signature[66:130],
                "v": int(signature[130:], 16),
            },
        }

        try:
            return await self.client.exchange_request(payload)
        except Exception as e:
            logger.error(f"Error canceling orders: {str(e)}")
            raise HyperliquidAPIError(f"Failed to cancel orders: {str(e)}") from e

    async def update_leverage(
        self,
        private_key: str,
        coin: str,
        leverage: int,
        is_cross: bool = True,
    ) -> dict[str, Any]:
        nonce = self._get_timestamp()

        action = {
            "type": "updateLeverage",
            "asset": self._get_asset_index(coin),
            "isCross": is_cross,
            "leverage": leverage,
        }

        signature = self._sign_l1_action(private_key, action, nonce)

        payload = {
            "action": action,
            "nonce": nonce,
            "signature": {
                "r": signature[:66],
                "s": "0x" + signature[66:130],
                "v": int(signature[130:], 16),
            },
        }

        try:
            return await self.client.exchange_request(payload)
        except Exception as e:
            logger.error(f"Error updating leverage: {str(e)}")
            raise HyperliquidAPIError(f"Failed to update leverage: {str(e)}") from e

    async def close_position(
        self,
        private_key: str,
        coin: str,
        slippage: float = 0.01,
    ) -> dict[str, Any]:
        from app.services.hyperliquid.info import get_info_service

        account = Account.from_key(private_key)
        info_service = get_info_service()

        positions = await info_service.get_user_positions(account.address)
        position = next((p for p in positions if p.get("symbol") == coin), None)

        if not position:
            return {"status": "ok", "message": "No position to close"}

        size = abs(position.get("size", 0))
        is_buy = position.get("size", 0) < 0

        return await self.place_market_order(
            private_key=private_key,
            coin=coin,
            is_buy=is_buy,
            size=size,
            slippage=slippage,
        )

    async def usd_transfer(
        self,
        private_key: str,
        amount: float,
        to_perp: bool = True,
    ) -> dict[str, Any]:
        """
        Transfer USDC between Spot and Perp wallets.

        Args:
            private_key: Wallet private key for signing
            amount: Amount in USDC (will be converted to micro-USDC)
            to_perp: True for Spot -> Perp, False for Perp -> Spot

        Returns:
            API response from Hyperliquid
        """
        nonce = self._get_timestamp()

        # Convert USDC to micro-USDC (1 USDC = 1,000,000 micro-USDC)
        amount_micro = int(amount * 1_000_000)

        action = {
            "type": "spotUser",
            "classTransfer": {
                "usdc": amount_micro,
                "toPerp": to_perp,
            },
        }

        signature = self._sign_l1_action(private_key, action, nonce)

        payload = {
            "action": action,
            "nonce": nonce,
            "signature": {
                "r": signature[:66],
                "s": "0x" + signature[66:130],
                "v": int(signature[130:], 16),
            },
        }

        try:
            result = await self.client.exchange_request(payload)
            logger.info(f"USD transfer {'to Perp' if to_perp else 'to Spot'}: {amount} USDC")
            return result
        except Exception as e:
            logger.error(f"Error transferring USD: {str(e)}")
            raise HyperliquidAPIError(f"Failed to transfer USD: {str(e)}") from e

    def _get_asset_index(self, coin: str) -> int:
        coin_index_map = {
            "BTC": 0,
            "ETH": 1,
            "ATOM": 2,
            "MATIC": 3,
            "DYDX": 4,
            "SOL": 5,
            "AVAX": 6,
            "BNB": 7,
            "APE": 8,
            "OP": 9,
            "LTC": 10,
            "ARB": 11,
            "DOGE": 12,
            "INJ": 13,
            "SUI": 14,
            "kPEPE": 15,
            "CRV": 16,
            "LDO": 17,
            "LINK": 18,
            "STX": 19,
            "RNDR": 20,
            "CFX": 21,
            "FTM": 22,
            "GMX": 23,
            "SNX": 24,
        }
        return coin_index_map.get(coin, 0)


_exchange_service_instance: HyperliquidExchangeService | None = None


def get_exchange_service() -> HyperliquidExchangeService:
    global _exchange_service_instance
    if _exchange_service_instance is None:
        _exchange_service_instance = HyperliquidExchangeService()
    return _exchange_service_instance
