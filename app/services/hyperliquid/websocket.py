import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import websockets
from websockets.client import WebSocketClientProtocol

from app.config import settings

logger = logging.getLogger(__name__)


class HyperliquidWebSocketManager:
    def __init__(self):
        self.ws_url = (
            settings.hyperliquid_ws_testnet
            if settings.hyperliquid_use_testnet
            else settings.hyperliquid_ws_mainnet
        )
        self._ws: WebSocketClientProtocol | None = None
        self._subscriptions: dict[str, Callable] = {}
        self._running = False
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60
        self._task: asyncio.Task | None = None

    async def connect(self):
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._connection_loop())

    async def disconnect(self):
        self._running = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _connection_loop(self):
        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1
                    logger.info(f"Connected to Hyperliquid WebSocket: {self.ws_url}")

                    for channel in self._subscriptions:
                        await self._send_subscription(channel)

                    await self._receive_loop()

            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay} seconds...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _receive_loop(self):
        if not self._ws:
            return

        async for message in self._ws:
            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse WebSocket message: {e}")
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")

    async def _handle_message(self, data: dict[str, Any]):
        channel = data.get("channel")
        if not channel:
            return

        callback = self._subscriptions.get(channel)
        if callback:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"Error in subscription callback for {channel}: {e}")

    async def _send_subscription(self, channel: str):
        if not self._ws:
            return

        parts = channel.split(":")
        subscription_type = parts[0]

        message = {"method": "subscribe", "subscription": {"type": subscription_type}}

        if subscription_type == "allMids":
            pass
        elif subscription_type == "trades" and len(parts) > 1:
            message["subscription"]["coin"] = parts[1]
        elif subscription_type == "l2Book" and len(parts) > 1:
            message["subscription"]["coin"] = parts[1]
        elif subscription_type == "candle" and len(parts) > 2:
            message["subscription"]["coin"] = parts[1]
            message["subscription"]["interval"] = parts[2]
        elif subscription_type == "orderUpdates" and len(parts) > 1:
            message["subscription"]["user"] = parts[1]
        elif subscription_type == "userEvents" and len(parts) > 1:
            message["subscription"]["user"] = parts[1]
        elif subscription_type == "userFills" and len(parts) > 1:
            message["subscription"]["user"] = parts[1]
        elif subscription_type == "userFundings" and len(parts) > 1:
            message["subscription"]["user"] = parts[1]

        await self._ws.send(json.dumps(message))
        logger.info(f"Subscribed to channel: {channel}")

    async def subscribe_all_mids(self, callback: Callable):
        channel = "allMids"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def subscribe_trades(self, coin: str, callback: Callable):
        channel = f"trades:{coin}"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def subscribe_l2_book(self, coin: str, callback: Callable):
        channel = f"l2Book:{coin}"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def subscribe_candles(self, coin: str, interval: str, callback: Callable):
        channel = f"candle:{coin}:{interval}"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def subscribe_order_updates(self, user: str, callback: Callable):
        channel = f"orderUpdates:{user}"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def subscribe_user_events(self, user: str, callback: Callable):
        channel = f"userEvents:{user}"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def subscribe_user_fills(self, user: str, callback: Callable):
        channel = f"userFills:{user}"
        self._subscriptions[channel] = callback
        if self._ws:
            await self._send_subscription(channel)

    async def unsubscribe(self, channel: str):
        if channel in self._subscriptions:
            del self._subscriptions[channel]

            if self._ws:
                parts = channel.split(":")
                message = {
                    "method": "unsubscribe",
                    "subscription": {"type": parts[0]},
                }
                await self._ws.send(json.dumps(message))
                logger.info(f"Unsubscribed from channel: {channel}")

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open


_ws_manager_instance: HyperliquidWebSocketManager | None = None


def get_ws_manager() -> HyperliquidWebSocketManager:
    global _ws_manager_instance
    if _ws_manager_instance is None:
        _ws_manager_instance = HyperliquidWebSocketManager()
    return _ws_manager_instance


async def close_ws_manager():
    global _ws_manager_instance
    if _ws_manager_instance:
        await _ws_manager_instance.disconnect()
        _ws_manager_instance = None
