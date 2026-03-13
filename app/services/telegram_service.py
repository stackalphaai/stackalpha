import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.core.security import decrypt_data, encrypt_data
from app.models import Signal, TelegramConnection, Trade, User

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, db: AsyncSession | None = None):
        self.db = db

    def _get_bot(self, connection: TelegramConnection) -> Bot:
        """Create a Bot instance from the connection's encrypted bot token."""
        if not connection.encrypted_bot_token:
            raise ValueError("No bot token configured for this connection")
        token = decrypt_data(connection.encrypted_bot_token)
        return Bot(token=token)

    async def connect_user(
        self,
        user: User,
        bot_token: str,
        chat_id: int,
    ) -> TelegramConnection:
        """
        Connect a user's Telegram by validating their bot_token + chat_id
        and storing the encrypted credentials.
        """
        if not self.db:
            raise ValueError("Database session required")

        # Validate by sending a test message
        try:
            bot = Bot(token=bot_token)
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "✅ <b>Connected to StackAlpha!</b>\n\n"
                    "You will now receive:\n"
                    "• Trading signal alerts\n"
                    "• Trade execution notifications\n"
                    "• TP/SL hit notifications\n"
                    "• Subscription updates"
                ),
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            raise ValueError(
                f"Failed to send message. Please check your bot token and chat ID. Error: {e}"
            ) from e

        encrypted_token = encrypt_data(bot_token)

        # Create or update connection
        result = await self.db.execute(
            select(TelegramConnection).where(TelegramConnection.user_id == user.id)
        )
        connection = result.scalar_one_or_none()

        if connection:
            connection.encrypted_bot_token = encrypted_token
            connection.telegram_chat_id = chat_id
            connection.is_verified = True
            connection.is_active = True
        else:
            connection = TelegramConnection(
                user_id=user.id,
                encrypted_bot_token=encrypted_token,
                telegram_chat_id=chat_id,
                is_verified=True,
                is_active=True,
            )
            self.db.add(connection)

        await self.db.flush()
        return connection

    async def send_message(
        self,
        connection: TelegramConnection,
        text: str,
        parse_mode: str = ParseMode.HTML,
    ) -> bool:
        """Send a message using the connection's own bot token."""
        try:
            bot = self._get_bot(connection)
            await bot.send_message(
                chat_id=connection.telegram_chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except (TelegramError, ValueError) as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_signal_notification(
        self,
        connection: TelegramConnection,
        signal: Signal,
    ) -> bool:
        if not connection.is_verified or not connection.telegram_chat_id:
            return False

        if not connection.signal_notifications:
            return False

        emoji = "📈" if signal.direction.value == "long" else "📉"
        direction = signal.direction.value.upper()

        message = f"""
{emoji} <b>New Trading Signal</b>

<b>Symbol:</b> {signal.symbol}
<b>Direction:</b> {direction}
<b>Confidence:</b> {signal.confidence_score:.1%}

<b>Entry:</b> ${signal.entry_price:,.4f}
<b>Take Profit:</b> ${signal.take_profit_price:,.4f}
<b>Stop Loss:</b> ${signal.stop_loss_price:,.4f}

<b>Suggested Leverage:</b> {signal.suggested_leverage}x
<b>Position Size:</b> {signal.suggested_position_size_percent}%

<i>Consensus: {signal.consensus_votes}/{signal.total_votes} models agree</i>
"""

        return await self.send_message(connection, message)

    async def send_trade_opened_notification(
        self,
        connection: TelegramConnection,
        trade: Trade,
    ) -> bool:
        if not connection.is_verified or not connection.telegram_chat_id:
            return False

        if not connection.trade_notifications:
            return False

        emoji = "🟢" if trade.direction.value == "long" else "🔴"
        direction = trade.direction.value.upper()

        message = f"""
{emoji} <b>Trade Opened</b>

<b>Symbol:</b> {trade.symbol}
<b>Direction:</b> {direction}
<b>Entry Price:</b> ${trade.entry_price:,.4f}

<b>Position Size:</b> ${trade.position_size_usd:,.2f}
<b>Leverage:</b> {trade.leverage}x

<b>Take Profit:</b> ${trade.take_profit_price:,.4f}
<b>Stop Loss:</b> ${trade.stop_loss_price:,.4f}
"""

        return await self.send_message(connection, message)

    async def send_trade_closed_notification(
        self,
        connection: TelegramConnection,
        trade: Trade,
    ) -> bool:
        if not connection.is_verified or not connection.telegram_chat_id:
            return False

        if not connection.trade_notifications:
            return False

        if trade.realized_pnl and trade.realized_pnl > 0:
            emoji = "✅"
            pnl_text = f"+${trade.realized_pnl:,.2f}"
        else:
            emoji = "❌"
            pnl_text = f"-${abs(trade.realized_pnl or 0):,.2f}"

        reason = (
            trade.close_reason.value.replace("_", " ").title() if trade.close_reason else "Unknown"
        )

        message = f"""
{emoji} <b>Trade Closed</b>

<b>Symbol:</b> {trade.symbol}
<b>Direction:</b> {trade.direction.value.upper()}

<b>Entry:</b> ${trade.entry_price:,.4f}
<b>Exit:</b> ${trade.exit_price:,.4f}

<b>P&L:</b> {pnl_text} ({trade.realized_pnl_percent:+.2f}%)
<b>Close Reason:</b> {reason}
"""

        return await self.send_message(connection, message)

    async def send_tp_hit_notification(
        self,
        connection: TelegramConnection,
        trade: Trade,
    ) -> bool:
        """Send a notification when take profit is hit."""
        if not connection.is_verified or not connection.telegram_chat_id:
            return False

        if not connection.trade_notifications:
            return False

        direction = trade.direction.value.upper()
        pnl = trade.realized_pnl or 0
        pnl_pct = trade.realized_pnl_percent or 0

        message = f"""
🎯 <b>Take Profit Hit!</b>

<b>Symbol:</b> {trade.symbol}
<b>Direction:</b> {direction}
<b>Leverage:</b> {trade.leverage}x

<b>Entry:</b> ${trade.entry_price:,.4f}
<b>TP Price:</b> ${trade.take_profit_price:,.4f}
<b>Exit:</b> ${trade.exit_price:,.4f}

<b>P&L:</b> +${abs(pnl):,.2f} ({pnl_pct:+.2f}%)

Great trade! 🚀
"""

        return await self.send_message(connection, message)

    async def send_sl_hit_notification(
        self,
        connection: TelegramConnection,
        trade: Trade,
    ) -> bool:
        """Send a notification when stop loss is hit."""
        if not connection.is_verified or not connection.telegram_chat_id:
            return False

        if not connection.trade_notifications:
            return False

        direction = trade.direction.value.upper()
        pnl = trade.realized_pnl or 0
        pnl_pct = trade.realized_pnl_percent or 0

        message = f"""
🛑 <b>Stop Loss Hit</b>

<b>Symbol:</b> {trade.symbol}
<b>Direction:</b> {direction}
<b>Leverage:</b> {trade.leverage}x

<b>Entry:</b> ${trade.entry_price:,.4f}
<b>SL Price:</b> ${trade.stop_loss_price:,.4f}
<b>Exit:</b> ${trade.exit_price:,.4f}

<b>P&L:</b> -${abs(pnl):,.2f} ({pnl_pct:+.2f}%)

Risk was managed. On to the next one. 💪
"""

        return await self.send_message(connection, message)

    async def send_subscription_notification(
        self,
        connection: TelegramConnection,
        message_type: str,
        **kwargs,
    ) -> bool:
        if not connection.is_verified or not connection.telegram_chat_id:
            return False

        if not connection.system_notifications:
            return False

        if message_type == "activated":
            message = """
🎉 <b>Subscription Activated!</b>

Your StackAlpha subscription is now active.
You now have access to:
• AI-powered trading signals
• Automated trade execution
• Real-time notifications

Happy trading! 🚀
"""
        elif message_type == "expiring":
            days = kwargs.get("days", 3)
            message = f"""
⚠️ <b>Subscription Expiring Soon</b>

Your subscription will expire in <b>{days} days</b>.

Renew now to continue receiving AI trading signals and automated execution.
"""
        elif message_type == "expired":
            message = """
❌ <b>Subscription Expired</b>

Your StackAlpha subscription has expired.
Renew to continue using premium features.
"""
        else:
            return False

        return await self.send_message(connection, message)

    async def broadcast_message(
        self,
        text: str,
        active_only: bool = True,
    ) -> int:
        if not self.db:
            raise ValueError("Database session required")

        query = select(TelegramConnection).where(
            TelegramConnection.is_verified,
            TelegramConnection.encrypted_bot_token.isnot(None),
        )

        if active_only:
            query = query.where(TelegramConnection.is_active)

        result = await self.db.execute(query)
        connections = list(result.scalars().all())

        sent_count = 0
        for conn in connections:
            if conn.telegram_chat_id:
                if await self.send_message(conn, text):
                    sent_count += 1

        return sent_count

    async def get_connection_by_user(self, user_id: str) -> TelegramConnection | None:
        if not self.db:
            return None

        result = await self.db.execute(
            select(TelegramConnection).where(TelegramConnection.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def update_notification_settings(
        self,
        connection: TelegramConnection,
        signal_notifications: bool | None = None,
        trade_notifications: bool | None = None,
        system_notifications: bool | None = None,
    ) -> TelegramConnection:
        if signal_notifications is not None:
            connection.signal_notifications = signal_notifications
        if trade_notifications is not None:
            connection.trade_notifications = trade_notifications
        if system_notifications is not None:
            connection.system_notifications = system_notifications

        return connection

    async def disconnect(self, connection: TelegramConnection) -> TelegramConnection:
        connection.is_active = False
        connection.telegram_chat_id = None
        connection.encrypted_bot_token = None
        connection.is_verified = False

        return connection
