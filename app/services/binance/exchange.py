import logging
from typing import Any

from app.core.exceptions import BinanceAPIError
from app.services.binance.client import BinanceClient

logger = logging.getLogger(__name__)


class BinanceExchangeService:
    """Authenticated Binance Futures trading operations.

    Instantiated per-user since each user has their own API credentials.
    """

    def __init__(self, client: BinanceClient):
        self.client = client

    async def get_account_info(self) -> dict[str, Any]:
        """Get futures account information including balance and positions."""
        try:
            c = await self.client.get_client()
            return await c.futures_account()
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            raise BinanceAPIError(f"Failed to get account info: {e}") from e

    async def get_balance(self) -> dict[str, Any]:
        """Get USDT balance summary."""
        account = await self.get_account_info()

        usdt_balance = None
        for asset in account.get("assets", []):
            if asset.get("asset") == "USDT":
                usdt_balance = asset
                break

        if not usdt_balance:
            return {
                "available_balance": 0.0,
                "total_balance": 0.0,
                "margin_used": 0.0,
                "unrealized_pnl": 0.0,
            }

        return {
            "available_balance": float(usdt_balance.get("availableBalance", 0)),
            "total_balance": float(usdt_balance.get("walletBalance", 0)),
            "margin_used": float(usdt_balance.get("initialMargin", 0)),
            "unrealized_pnl": float(usdt_balance.get("unrealizedProfit", 0)),
        }

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get open positions (non-zero) with mark prices."""
        try:
            c = await self.client.get_client()
            # futures_position_information includes markPrice, unlike futures_account positions
            all_positions = await c.futures_position_information()
        except Exception as e:
            logger.error(f"Failed to get position information: {e}")
            raise BinanceAPIError(f"Failed to get positions: {e}") from e

        positions = []
        for pos in all_positions:
            size = float(pos.get("positionAmt", 0))
            if size == 0:
                continue
            positions.append(
                {
                    "symbol": pos.get("symbol"),
                    "size": size,
                    "entry_price": float(pos.get("entryPrice", 0)),
                    "mark_price": float(pos.get("markPrice", 0)),
                    "unrealized_pnl": float(pos.get("unRealizedProfit", 0)),
                    "leverage": int(pos.get("leverage", 1)),
                    "margin_type": pos.get("marginType", "cross"),
                    "notional": float(pos.get("notional", 0)),
                }
            )
        return positions

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Set leverage for a symbol."""
        try:
            c = await self.client.get_client()
            return await c.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            logger.error(f"Failed to set leverage for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to set leverage: {e}") from e

    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> dict[str, Any]:
        """Set margin type (CROSSED or ISOLATED)."""
        try:
            c = await self.client.get_client()
            return await c.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except Exception as e:
            # Ignore "No need to change margin type" error (-4046)
            if "-4046" in str(e):
                return {"msg": "Already set"}
            logger.error(f"Failed to set margin type for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to set margin type: {e}") from e

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
    ) -> dict[str, Any]:
        """Place a market order on Binance Futures."""
        try:
            c = await self.client.get_client()
            result = await c.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                newOrderRespType="RESULT",
            )
            logger.info(
                f"Market order placed: {side} {quantity} {symbol}, orderId={result.get('orderId')}"
            )
            return result
        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            raise BinanceAPIError(f"Failed to place market order: {e}") from e

    async def place_tp_algo_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
    ) -> dict[str, Any]:
        """Place a Take Profit algo order using /fapi/v1/algoOrder.

        CRITICAL: Since Dec 2025, all conditional orders must use the algoOrder endpoint.
        """
        try:
            c = await self.client.get_client()
            result = await c.futures_create_order(
                symbol=symbol,
                side=side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=stop_price,
                closePosition="true",
                workingType="MARK_PRICE",
            )
            logger.info(
                f"TP order placed: {side} {symbol} @ {stop_price}, orderId={result.get('orderId')}"
            )
            return result
        except Exception as e:
            error_str = str(e)
            # If the standard endpoint fails with -4120, try algo order endpoint
            if "-4120" in error_str:
                return await self._place_algo_conditional_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    stop_price=stop_price,
                    order_type="TAKE_PROFIT_MARKET",
                )
            logger.error(f"Failed to place TP order: {e}")
            raise BinanceAPIError(f"Failed to place TP order: {e}") from e

    async def place_sl_algo_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
    ) -> dict[str, Any]:
        """Place a Stop Loss algo order using /fapi/v1/algoOrder.

        CRITICAL: Since Dec 2025, all conditional orders must use the algoOrder endpoint.
        """
        try:
            c = await self.client.get_client()
            result = await c.futures_create_order(
                symbol=symbol,
                side=side,
                type="STOP_MARKET",
                stopPrice=stop_price,
                closePosition="true",
                workingType="MARK_PRICE",
            )
            logger.info(
                f"SL order placed: {side} {symbol} @ {stop_price}, orderId={result.get('orderId')}"
            )
            return result
        except Exception as e:
            error_str = str(e)
            if "-4120" in error_str:
                return await self._place_algo_conditional_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    stop_price=stop_price,
                    order_type="STOP_MARKET",
                )
            logger.error(f"Failed to place SL order: {e}")
            raise BinanceAPIError(f"Failed to place SL order: {e}") from e

    def _get_futures_base_url(self) -> str:
        """Get the correct base URL for futures API based on testnet setting."""
        if self.client.testnet:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"

    async def _place_algo_conditional_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        order_type: str,
    ) -> dict[str, Any]:
        """Fallback: Place conditional order via /fapi/v1/algoOrder endpoint."""
        try:
            c = await self.client.get_client()
            base_url = self._get_futures_base_url()
            # Use the algo order endpoint — note: uses triggerPrice, not stopPrice
            params = {
                "symbol": symbol,
                "side": side,
                "positionSide": "BOTH",
                "type": order_type,
                "quantity": str(quantity),
                "triggerPrice": str(stop_price),
                "workingType": "MARK_PRICE",
                "algoType": "CONDITIONAL",
            }
            # Make signed request to algo order endpoint
            result = await c._request(
                "post",
                f"{base_url}/fapi/v1/algoOrder",
                signed=True,
                data=params,
            )
            logger.info(
                f"Algo {order_type} order placed: {side} {symbol} @ {stop_price}, "
                f"algoId={result.get('algoId')}"
            )
            return result
        except Exception as e:
            logger.error(f"Failed to place algo conditional order: {e}")
            raise BinanceAPIError(f"Failed to place {order_type} algo order: {e}") from e

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel a regular futures order."""
        try:
            c = await self.client.get_client()
            return await c.futures_cancel_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise BinanceAPIError(f"Failed to cancel order: {e}") from e

    async def cancel_algo_order(self, symbol: str, algo_order_id: int) -> dict[str, Any]:
        """Cancel an algo conditional order."""
        try:
            c = await self.client.get_client()
            # Try cancelling as regular order first (for standard TP/SL)
            return await c.futures_cancel_order(symbol=symbol, orderId=algo_order_id)
        except Exception:
            # If regular cancel fails, try algo cancel endpoint
            try:
                base_url = self._get_futures_base_url()
                result = await c._request(
                    "delete",
                    f"{base_url}/fapi/v1/algoOrder",
                    signed=True,
                    data={"symbol": symbol, "algoId": algo_order_id},
                )
                return result
            except Exception as e2:
                logger.error(f"Failed to cancel algo order {algo_order_id}: {e2}")
                raise BinanceAPIError(f"Failed to cancel algo order: {e2}") from e2

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get standard open orders for a symbol (includes STOP_MARKET, TAKE_PROFIT_MARKET, etc.)."""
        try:
            c = await self.client.get_client()
            if symbol:
                return await c.futures_get_open_orders(symbol=symbol)
            return await c.futures_get_open_orders()
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            raise BinanceAPIError(f"Failed to get open orders: {e}") from e

    async def get_position_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        """Get the open position for a specific symbol, or None if no position."""
        try:
            c = await self.client.get_client()
            all_positions = await c.futures_position_information(symbol=symbol)
            for pos in all_positions:
                if abs(float(pos.get("positionAmt", 0))) > 0:
                    return {
                        "symbol": pos.get("symbol"),
                        "size": float(pos.get("positionAmt", 0)),
                        "entry_price": float(pos.get("entryPrice", 0)),
                        "mark_price": float(pos.get("markPrice", 0)),
                        "unrealized_pnl": float(pos.get("unRealizedProfit", 0)),
                        "leverage": int(pos.get("leverage", 1)),
                        "notional": abs(float(pos.get("notional", 0))),
                        "initial_margin": float(pos.get("initialMargin", 0)),
                    }
            return None
        except Exception as e:
            logger.error(f"Failed to get position for {symbol}: {e}")
            return None

    async def close_position(self, symbol: str) -> dict[str, Any]:
        """Close an entire position by placing an opposite market order."""
        positions = await self.get_positions()
        position = next((p for p in positions if p["symbol"] == symbol), None)

        if not position:
            return {"status": "ok", "message": "No position to close"}

        size = abs(position["size"])
        # If position is long (positive size), sell to close; if short, buy to close
        side = "SELL" if position["size"] > 0 else "BUY"

        return await self.place_market_order(
            symbol=symbol,
            side=side,
            quantity=size,
        )

    async def close(self):
        """Clean up the client connection."""
        await self.client.close()
