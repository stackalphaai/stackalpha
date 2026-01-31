import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import (
    BadRequestError,
    HyperliquidAPIError,
    InsufficientBalanceError,
    PositionLimitError,
    TradingDisabledError,
)
from app.models import (
    Signal,
    Trade,
    TradeCloseReason,
    TradeDirection,
    TradeStatus,
    User,
    Wallet,
)
from app.services.hyperliquid import get_exchange_service, get_info_service
from app.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.exchange_service = get_exchange_service()
        self.info_service = get_info_service()

    async def execute_signal(
        self,
        user: User,
        wallet: Wallet,
        signal: Signal,
        position_size_percent: float | None = None,
        leverage: int | None = None,
    ) -> Trade:
        if not wallet.can_trade:
            raise TradingDisabledError("Trading is not enabled for this wallet")

        open_trades = await self._count_open_trades(user.id)
        if open_trades >= settings.max_concurrent_positions:
            raise PositionLimitError(
                f"Maximum {settings.max_concurrent_positions} concurrent positions allowed"
            )

        balance = await self.info_service.get_user_balance(wallet.address)
        available_balance = balance.get("available_balance", 0)

        if available_balance <= 0:
            raise InsufficientBalanceError()

        position_pct = position_size_percent or signal.suggested_position_size_percent
        leverage_val = leverage or signal.suggested_leverage
        leverage_val = max(1, min(leverage_val, settings.max_leverage))

        position_size_usd = available_balance * (position_pct / 100)

        market_data = await self.info_service.get_market_data(signal.symbol)
        current_price = market_data.get("mark_price", signal.entry_price)

        position_size = position_size_usd / current_price

        trade = Trade(
            user_id=user.id,
            wallet_id=wallet.id,
            signal_id=signal.id,
            symbol=signal.symbol,
            direction=TradeDirection(signal.direction.value),
            status=TradeStatus.PENDING,
            position_size=position_size,
            position_size_usd=position_size_usd,
            leverage=leverage_val,
            take_profit_price=signal.take_profit_price,
            stop_loss_price=signal.stop_loss_price,
        )

        self.db.add(trade)
        await self.db.flush()

        try:
            trade = await self._open_position(trade, wallet)
        except Exception as e:
            trade.status = TradeStatus.FAILED
            trade.error_message = str(e)
            logger.error(f"Failed to execute trade: {e}")

        await self.db.refresh(trade)
        return trade

    async def open_trade(
        self,
        user: User,
        wallet: Wallet,
        symbol: str,
        direction: TradeDirection,
        position_size_usd: float,
        leverage: int,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
    ) -> Trade:
        if not wallet.can_trade:
            raise TradingDisabledError("Trading is not enabled for this wallet")

        open_trades = await self._count_open_trades(user.id)
        if open_trades >= settings.max_concurrent_positions:
            raise PositionLimitError()

        balance = await self.info_service.get_user_balance(wallet.address)
        if balance.get("available_balance", 0) < position_size_usd / leverage:
            raise InsufficientBalanceError()

        market_data = await self.info_service.get_market_data(symbol)
        current_price = market_data.get("mark_price", 0)

        if current_price <= 0:
            raise BadRequestError(f"Could not fetch price for {symbol}")

        position_size = position_size_usd / current_price

        trade = Trade(
            user_id=user.id,
            wallet_id=wallet.id,
            symbol=symbol,
            direction=direction,
            status=TradeStatus.PENDING,
            position_size=position_size,
            position_size_usd=position_size_usd,
            leverage=leverage,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
        )

        self.db.add(trade)
        await self.db.flush()

        try:
            trade = await self._open_position(trade, wallet)
        except Exception as e:
            trade.status = TradeStatus.FAILED
            trade.error_message = str(e)
            logger.error(f"Failed to open trade: {e}")

        await self.db.refresh(trade)
        return trade

    async def close_trade(
        self,
        trade: Trade,
        wallet: Wallet,
        reason: TradeCloseReason = TradeCloseReason.MANUAL,
    ) -> Trade:
        if trade.status != TradeStatus.OPEN:
            raise BadRequestError("Trade is not open")

        trade.status = TradeStatus.CLOSING

        try:
            wallet_service = WalletService(self.db)
            private_key = wallet_service.get_private_key(wallet)

            if not private_key:
                raise BadRequestError("Cannot close trade: wallet key not available")

            result = await self.exchange_service.close_position(
                private_key=private_key,
                coin=trade.symbol,
            )

            market_data = await self.info_service.get_market_data(trade.symbol)
            exit_price = market_data.get("mark_price", 0)

            trade.exit_price = exit_price
            trade.status = TradeStatus.CLOSED
            trade.close_reason = reason
            trade.closed_at = datetime.now(UTC)
            trade.order_response = result

            if trade.entry_price and exit_price:
                if trade.direction == TradeDirection.LONG:
                    pnl = (exit_price - trade.entry_price) * trade.position_size
                    pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
                else:
                    pnl = (trade.entry_price - exit_price) * trade.position_size
                    pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100

                trade.realized_pnl = pnl * trade.leverage
                trade.realized_pnl_percent = pnl_pct * trade.leverage

        except Exception as e:
            trade.status = TradeStatus.OPEN
            trade.error_message = str(e)
            logger.error(f"Failed to close trade: {e}")
            raise HyperliquidAPIError(f"Failed to close position: {e}") from e

        return trade

    async def _open_position(self, trade: Trade, wallet: Wallet) -> Trade:
        trade.status = TradeStatus.OPENING

        wallet_service = WalletService(self.db)
        private_key = wallet_service.get_private_key(wallet)

        if not private_key:
            raise BadRequestError("Cannot trade: wallet key not available")

        await self.exchange_service.update_leverage(
            private_key=private_key,
            coin=trade.symbol,
            leverage=trade.leverage,
        )

        is_buy = trade.direction == TradeDirection.LONG

        result = await self.exchange_service.place_market_order(
            private_key=private_key,
            coin=trade.symbol,
            is_buy=is_buy,
            size=trade.position_size,
        )

        trade.order_response = result
        trade.hyperliquid_order_id = str(
            result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("oid", "")
        )

        positions = await self.info_service.get_user_positions(wallet.address)
        position = next((p for p in positions if p.get("symbol") == trade.symbol), None)

        if position:
            trade.entry_price = position.get("entry_price")
            trade.margin_used = position.get("margin_used")
            trade.position_data = position
            trade.status = TradeStatus.OPEN
            trade.opened_at = datetime.now(UTC)
        else:
            trade.status = TradeStatus.FAILED
            trade.error_message = "Position not found after order execution"

        return trade

    async def _count_open_trades(self, user_id: str) -> int:
        result = await self.db.execute(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.OPENING]),
            )
        )
        return len(result.scalars().all())

    async def sync_trade_positions(self, trade: Trade, wallet: Wallet) -> Trade:
        if trade.status != TradeStatus.OPEN:
            return trade

        positions = await self.info_service.get_user_positions(wallet.address)
        position = next((p for p in positions if p.get("symbol") == trade.symbol), None)

        if position:
            trade.unrealized_pnl = position.get("unrealized_pnl")
            trade.position_data = position
        else:
            trade.status = TradeStatus.CLOSED
            trade.closed_at = datetime.now(UTC)

            if not trade.close_reason:
                market_data = await self.info_service.get_market_data(trade.symbol)
                current_price = market_data.get("mark_price", 0)

                if trade.take_profit_price and trade.stop_loss_price and trade.entry_price:
                    if trade.direction == TradeDirection.LONG:
                        if current_price >= trade.take_profit_price:
                            trade.close_reason = TradeCloseReason.TP_HIT
                        elif current_price <= trade.stop_loss_price:
                            trade.close_reason = TradeCloseReason.SL_HIT
                    else:
                        if current_price <= trade.take_profit_price:
                            trade.close_reason = TradeCloseReason.TP_HIT
                        elif current_price >= trade.stop_loss_price:
                            trade.close_reason = TradeCloseReason.SL_HIT

                if not trade.close_reason:
                    trade.close_reason = TradeCloseReason.SYSTEM

        return trade
