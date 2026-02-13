import logging
from typing import Any

from app.services.hyperliquid.client import HyperliquidClient, get_hyperliquid_client

logger = logging.getLogger(__name__)


class HyperliquidInfoService:
    def __init__(self, client: HyperliquidClient | None = None):
        self.client = client or get_hyperliquid_client()

    async def get_meta(self) -> dict[str, Any]:
        return await self.client.info_request({"type": "meta"})

    async def get_all_mids(self) -> dict[str, str]:
        return await self.client.info_request({"type": "allMids"})

    async def get_meta_and_asset_ctxs(self) -> list[dict[str, Any]]:
        return await self.client.info_request({"type": "metaAndAssetCtxs"})

    async def get_user_state(self, address: str) -> dict[str, Any]:
        return await self.client.info_request(
            {
                "type": "clearinghouseState",
                "user": address,
            }
        )

    async def get_user_open_orders(self, address: str) -> list[dict[str, Any]]:
        return await self.client.info_request(
            {
                "type": "openOrders",
                "user": address,
            }
        )

    async def get_user_fills(
        self,
        address: str,
        start_time: int | None = None,
    ) -> list[dict[str, Any]]:
        data = {
            "type": "userFills",
            "user": address,
        }
        if start_time:
            data["startTime"] = start_time

        return await self.client.info_request(data)

    async def get_funding_history(
        self,
        coin: str,
        start_time: int,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        data = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time,
        }
        if end_time:
            data["endTime"] = end_time

        return await self.client.info_request(data)

    async def get_l2_book(self, coin: str) -> dict[str, Any]:
        return await self.client.info_request(
            {
                "type": "l2Book",
                "coin": coin,
            }
        )

    async def get_candles(
        self,
        coin: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        data = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
            },
        }
        if end_time:
            data["req"]["endTime"] = end_time

        return await self.client.info_request(data)

    async def get_market_data(self, symbol: str) -> dict[str, Any]:
        meta = await self.get_meta_and_asset_ctxs()

        if not meta or len(meta) < 2:
            return {}

        universe = meta[0].get("universe", [])
        asset_ctxs = meta[1] if len(meta) > 1 else []

        for i, asset in enumerate(universe):
            if asset.get("name") == symbol:
                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                mark_price = float(ctx.get("markPx", 0))
                price_change_percent = float(ctx.get("dayChg", 0)) if ctx.get("dayChg") else 0
                prev_price = float(ctx.get("prevDayPx", 0)) if ctx.get("prevDayPx") else 0
                price_change = (
                    mark_price - prev_price if prev_price else mark_price * price_change_percent
                )

                return {
                    "symbol": symbol,
                    "mark_price": mark_price,
                    "index_price": float(ctx.get("oraclePx", 0)),
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": float(ctx.get("openInterest", 0)),
                    "volume_24h": float(ctx.get("dayNtlVlm", 0)),
                    "price_change_24h": price_change,
                    "price_change_percent_24h": price_change_percent * 100,
                }

        return {}

    async def get_all_market_data(self) -> list[dict[str, Any]]:
        meta = await self.get_meta_and_asset_ctxs()

        if not meta or len(meta) < 2:
            return []

        universe = meta[0].get("universe", [])
        asset_ctxs = meta[1] if len(meta) > 1 else []

        markets = []
        for i, asset in enumerate(universe):
            ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
            mark_price = float(ctx.get("markPx", 0))
            price_change_percent = float(ctx.get("dayChg", 0)) if ctx.get("dayChg") else 0
            prev_price = float(ctx.get("prevDayPx", 0)) if ctx.get("prevDayPx") else 0
            price_change = (
                mark_price - prev_price if prev_price else mark_price * price_change_percent
            )

            markets.append(
                {
                    "symbol": asset.get("name"),
                    "mark_price": mark_price,
                    "index_price": float(ctx.get("oraclePx", 0)),
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": float(ctx.get("openInterest", 0)),
                    "volume_24h": float(ctx.get("dayNtlVlm", 0)),
                    "price_change_24h": price_change,
                    "price_change_percent_24h": price_change_percent * 100,
                }
            )

        return markets

    async def get_high_volume_coins(
        self,
        min_volume: float = 1_000_000,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        markets = await self.get_all_market_data()

        high_volume = [m for m in markets if m.get("volume_24h", 0) >= min_volume]

        high_volume.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)

        return high_volume[:limit]

    async def get_user_balance(self, address: str) -> dict[str, Any]:
        state = await self.get_user_state(address)

        if not state:
            return {
                "balance_usd": 0,
                "margin_used": 0,
                "unrealized_pnl": 0,
                "available_balance": 0,
                "account_value": 0,
            }

        margin_summary = state.get("marginSummary", {})
        cross_margin_summary = state.get("crossMarginSummary", {})

        account_value = float(margin_summary.get("accountValue", 0))
        margin_used = float(cross_margin_summary.get("totalMarginUsed", 0))
        unrealized_pnl = float(cross_margin_summary.get("totalNtlPos", 0))

        return {
            "balance_usd": account_value,
            "margin_used": margin_used,
            "unrealized_pnl": unrealized_pnl,
            "available_balance": account_value - margin_used,
            "account_value": account_value,
        }

    async def get_user_positions(self, address: str) -> list[dict[str, Any]]:
        state = await self.get_user_state(address)

        if not state:
            return []

        asset_positions = state.get("assetPositions", [])
        positions = []

        for pos_data in asset_positions:
            pos = pos_data.get("position", {})
            if float(pos.get("szi", 0)) != 0:
                positions.append(
                    {
                        "symbol": pos.get("coin"),
                        "size": float(pos.get("szi", 0)),
                        "entry_price": float(pos.get("entryPx", 0)),
                        "mark_price": float(pos.get("positionValue", 0)) / float(pos.get("szi", 1))
                        if float(pos.get("szi", 0)) != 0
                        else 0,
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                        "margin_used": float(pos.get("marginUsed", 0)),
                        "leverage": int(pos.get("leverage", {}).get("value", 1)),
                        "liquidation_price": float(pos.get("liquidationPx", 0))
                        if pos.get("liquidationPx")
                        else None,
                    }
                )

        return positions


_info_service_instance: HyperliquidInfoService | None = None


def get_info_service() -> HyperliquidInfoService:
    global _info_service_instance
    if _info_service_instance is None:
        _info_service_instance = HyperliquidInfoService()
    return _info_service_instance
