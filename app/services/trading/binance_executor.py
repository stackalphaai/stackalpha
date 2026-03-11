import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import (
    BadRequestError,
    BinanceAPIError,
    InsufficientBalanceError,
    RiskLimitError,
    TradingDisabledError,
)
from app.models import Signal, Trade, TradeCloseReason, TradeDirection, TradeStatus, User
from app.models.exchange_connection import ExchangeConnection
from app.services.binance import (
    create_binance_exchange_service,
    get_binance_info_service,
    to_binance_symbol,
)
from app.services.trading.risk_management import RiskManagementService

logger = logging.getLogger(__name__)


class BinanceTradeExecutor:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.info_service = get_binance_info_service()

    async def execute_signal(
        self,
        user: User,
        exchange_connection: ExchangeConnection,
        signal: Signal,
        position_size_percent: float | None = None,
        leverage: int | None = None,
    ) -> Trade:
        """Execute a Binance Futures trade from a signal."""
        if not exchange_connection.can_trade:
            raise TradingDisabledError("Trading is not enabled for this exchange connection")

        # Create per-user exchange service
        binance_exchange = await create_binance_exchange_service(exchange_connection)

        try:
            # Get balance
            balance = await binance_exchange.get_balance()
            available_balance = balance.get("available_balance", 0)

            if available_balance <= 0:
                raise InsufficientBalanceError()

            # Calculate position size
            position_pct = position_size_percent or float(signal.suggested_position_size_percent)
            leverage_val = leverage or signal.suggested_leverage
            leverage_val = max(1, min(leverage_val, settings.binance_max_leverage))

            position_size_usd = available_balance * (position_pct / 100)

            # --- Risk Management Validation ---
            risk_service = RiskManagementService(self.db)
            (
                approved,
                reason,
                leverage_val,
                position_size_usd,
            ) = await risk_service.validate_signal_execution(
                user_id=user.id,
                signal_confidence=float(signal.confidence_score),
                proposed_leverage=leverage_val,
                entry_price=float(signal.entry_price),
                stop_loss_price=float(signal.stop_loss_price),
                take_profit_price=float(signal.take_profit_price),
                position_size_usd=position_size_usd,
                available_balance=available_balance,
            )
            if not approved:
                raise RiskLimitError(f"Risk check failed: {reason}")

            # Get symbol precision
            binance_symbol = to_binance_symbol(signal.symbol)
            precision = await self.info_service.get_symbol_precision(binance_symbol)

            # Get current price
            market_data = await self.info_service.get_market_data(binance_symbol)
            current_price = market_data.get("mark_price", float(signal.entry_price))

            # Calculate quantity
            position_size = round(
                position_size_usd / current_price,
                precision["quantity_precision"],
            )

            # Create trade record
            trade = Trade(
                user_id=user.id,
                exchange_connection_id=exchange_connection.id,
                signal_id=signal.id,
                symbol=signal.symbol,
                exchange="binance",
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
                trade = await self._open_binance_position(trade, binance_exchange, precision)
            except Exception as e:
                trade.status = TradeStatus.FAILED
                trade.error_message = str(e)
                logger.error(f"Failed to execute Binance trade: {e}")

            return trade
        finally:
            await binance_exchange.close()

    async def close_trade(
        self,
        trade: Trade,
        exchange_connection: ExchangeConnection,
        reason: TradeCloseReason = TradeCloseReason.MANUAL,
    ) -> Trade:
        """Close a Binance trade: cancel TP/SL orders, close position."""
        if trade.status != TradeStatus.OPEN:
            raise BadRequestError("Trade is not open")

        trade.status = TradeStatus.CLOSING
        binance_exchange = await create_binance_exchange_service(exchange_connection)
        binance_symbol = to_binance_symbol(trade.symbol)

        try:
            # Cancel TP order
            if trade.tp_order_id:
                try:
                    await binance_exchange.cancel_algo_order(binance_symbol, int(trade.tp_order_id))
                except Exception as e:
                    logger.warning(f"Failed to cancel TP order {trade.tp_order_id}: {e}")

            # Cancel SL order
            if trade.sl_order_id:
                try:
                    await binance_exchange.cancel_algo_order(binance_symbol, int(trade.sl_order_id))
                except Exception as e:
                    logger.warning(f"Failed to cancel SL order {trade.sl_order_id}: {e}")

            # Close position
            result = await binance_exchange.close_position(binance_symbol)

            # Get exit price
            market_data = await self.info_service.get_market_data(binance_symbol)
            exit_price = market_data.get("mark_price", 0)

            trade.exit_price = exit_price
            trade.status = TradeStatus.CLOSED
            trade.close_reason = reason
            trade.closed_at = datetime.now(UTC)
            trade.order_response = result

            # Calculate PnL
            if trade.entry_price and exit_price:
                if trade.direction == TradeDirection.LONG:
                    pnl = (exit_price - float(trade.entry_price)) * float(trade.position_size)
                    pnl_pct = (
                        (exit_price - float(trade.entry_price)) / float(trade.entry_price) * 100
                    )
                else:
                    pnl = (float(trade.entry_price) - exit_price) * float(trade.position_size)
                    pnl_pct = (
                        (float(trade.entry_price) - exit_price) / float(trade.entry_price) * 100
                    )

                trade.realized_pnl = pnl * trade.leverage
                trade.realized_pnl_percent = pnl_pct * trade.leverage

        except Exception as e:
            trade.status = TradeStatus.OPEN
            trade.error_message = str(e)
            logger.error(f"Failed to close Binance trade: {e}")
            raise BinanceAPIError(f"Failed to close position: {e}") from e
        finally:
            await binance_exchange.close()

        return trade

    async def _open_binance_position(
        self,
        trade: Trade,
        binance_exchange,
        precision: dict,
    ) -> Trade:
        """Open a Binance Futures position with TP/SL."""
        trade.status = TradeStatus.OPENING
        binance_symbol = to_binance_symbol(trade.symbol)

        # Step 1: Set leverage
        await binance_exchange.set_leverage(binance_symbol, trade.leverage)

        # Step 2: Set margin type to CROSSED
        try:
            await binance_exchange.set_margin_type(binance_symbol, "CROSSED")
        except Exception:
            pass  # Already set

        # Step 3: Place market entry order
        side = "BUY" if trade.direction == TradeDirection.LONG else "SELL"
        entry_result = await binance_exchange.place_market_order(
            symbol=binance_symbol,
            side=side,
            quantity=float(trade.position_size),
        )

        trade.exchange_order_id = str(entry_result.get("orderId", ""))
        trade.order_response = entry_result

        # Get fill price
        fill_price = float(entry_result.get("avgPrice", 0))
        if fill_price == 0:
            # Fallback: get from cumulative quote / executed quantity
            cum_quote = float(entry_result.get("cumQuote", 0))
            executed_qty = float(entry_result.get("executedQty", 0))
            if executed_qty > 0:
                fill_price = cum_quote / executed_qty

        trade.entry_price = fill_price
        trade.opened_at = datetime.now(UTC)

        # Step 4: Place TP algo order
        close_side = "SELL" if trade.direction == TradeDirection.LONG else "BUY"
        tp_price = round(float(trade.take_profit_price), precision["price_precision"])

        try:
            tp_result = await binance_exchange.place_tp_algo_order(
                symbol=binance_symbol,
                side=close_side,
                quantity=float(trade.position_size),
                stop_price=tp_price,
            )
            trade.tp_order_id = str(tp_result.get("algoId", tp_result.get("orderId", "")))
        except Exception as e:
            logger.error(f"Failed to place TP order for {binance_symbol}: {e}")
            # Don't fail the whole trade if TP placement fails

        # Step 5: Place SL algo order
        sl_price = round(float(trade.stop_loss_price), precision["price_precision"])

        try:
            sl_result = await binance_exchange.place_sl_algo_order(
                symbol=binance_symbol,
                side=close_side,
                quantity=float(trade.position_size),
                stop_price=sl_price,
            )
            trade.sl_order_id = str(sl_result.get("algoId", sl_result.get("orderId", "")))
        except Exception as e:
            logger.error(f"Failed to place SL order for {binance_symbol}: {e}")

        trade.status = TradeStatus.OPEN
        logger.info(
            f"Binance position opened: {side} {trade.position_size} {binance_symbol} "
            f"@ {fill_price}, TP={tp_price}, SL={sl_price}"
        )

        return trade
