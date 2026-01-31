import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import TelegramService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    db: DB,
):
    try:
        data = await request.json()
        logger.info(f"Received Telegram webhook: {data}")

        if "message" in data:
            message = data["message"]
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "")
            user = message.get("from", {})
            telegram_user_id = user.get("id")
            telegram_username = user.get("username")

            telegram_service = TelegramService(db)

            if text.startswith("/start"):
                parts = text.split()
                if len(parts) > 1:
                    verification_code = parts[1]
                    connection = await telegram_service.verify_user(
                        verification_code=verification_code,
                        telegram_user_id=telegram_user_id,
                        telegram_username=telegram_username,
                        chat_id=chat_id,
                    )

                    if connection:
                        await db.commit()
                        await telegram_service.send_message(
                            chat_id,
                            "✅ <b>Successfully Connected!</b>\n\n"
                            "Your Telegram account is now linked to StackAlpha.\n\n"
                            "You will receive:\n"
                            "• Trading signal alerts\n"
                            "• Trade execution notifications\n"
                            "• Subscription updates\n\n"
                            "Use /help to see available commands.",
                        )
                    else:
                        await telegram_service.send_message(
                            chat_id,
                            "❌ <b>Verification Failed</b>\n\n"
                            "The verification code is invalid or expired.\n"
                            "Please generate a new code from your dashboard.",
                        )
                else:
                    await telegram_service.send_message(
                        chat_id,
                        "👋 <b>Welcome to StackAlpha Bot!</b>\n\n"
                        "To connect your account:\n"
                        "1. Go to your StackAlpha dashboard\n"
                        "2. Navigate to Settings → Telegram\n"
                        "3. Click 'Connect Telegram'\n"
                        "4. Use the verification link provided\n\n"
                        "Use /help to see available commands.",
                    )

            elif text == "/help":
                await telegram_service.send_message(
                    chat_id,
                    "📚 <b>StackAlpha Bot Commands</b>\n\n"
                    "/start - Connect your account\n"
                    "/status - Check connection status\n"
                    "/signals - Toggle signal notifications\n"
                    "/trades - Toggle trade notifications\n"
                    "/help - Show this help message\n\n"
                    "Visit https://stackalpha.io for more info.",
                )

            elif text == "/status":
                from sqlalchemy import select

                from app.models import TelegramConnection

                result = await db.execute(
                    select(TelegramConnection).where(
                        TelegramConnection.telegram_user_id == telegram_user_id
                    )
                )
                connection = result.scalar_one_or_none()

                if connection and connection.is_verified:
                    status_msg = (
                        "✅ <b>Connection Status: Active</b>\n\n"
                        f"<b>Notifications:</b>\n"
                        f"• Signals: {'✅' if connection.signal_notifications else '❌'}\n"
                        f"• Trades: {'✅' if connection.trade_notifications else '❌'}\n"
                        f"• System: {'✅' if connection.system_notifications else '❌'}"
                    )
                else:
                    status_msg = "❌ <b>Not Connected</b>\n\nUse /start to connect your account."

                await telegram_service.send_message(chat_id, status_msg)

            elif text == "/signals":
                from sqlalchemy import select

                from app.models import TelegramConnection

                result = await db.execute(
                    select(TelegramConnection).where(
                        TelegramConnection.telegram_user_id == telegram_user_id
                    )
                )
                connection = result.scalar_one_or_none()

                if connection and connection.is_verified:
                    connection.signal_notifications = not connection.signal_notifications
                    await db.commit()

                    status = "enabled" if connection.signal_notifications else "disabled"
                    await telegram_service.send_message(
                        chat_id,
                        f"✅ Signal notifications {status}.",
                    )
                else:
                    await telegram_service.send_message(
                        chat_id,
                        "❌ Please connect your account first using /start",
                    )

            elif text == "/trades":
                from sqlalchemy import select

                from app.models import TelegramConnection

                result = await db.execute(
                    select(TelegramConnection).where(
                        TelegramConnection.telegram_user_id == telegram_user_id
                    )
                )
                connection = result.scalar_one_or_none()

                if connection and connection.is_verified:
                    connection.trade_notifications = not connection.trade_notifications
                    await db.commit()

                    status = "enabled" if connection.trade_notifications else "disabled"
                    await telegram_service.send_message(
                        chat_id,
                        f"✅ Trade notifications {status}.",
                    )
                else:
                    await telegram_service.send_message(
                        chat_id,
                        "❌ Please connect your account first using /start",
                    )

    except Exception as e:
        logger.error(f"Error processing Telegram webhook: {e}")

    return {"status": "ok"}
