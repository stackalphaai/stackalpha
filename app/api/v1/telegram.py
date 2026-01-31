from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser
from app.schemas import SuccessResponse
from app.services import TelegramService

router = APIRouter(prefix="/telegram", tags=["Telegram"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


class TelegramConnectionResponse(BaseModel):
    is_connected: bool
    telegram_username: str | None = None
    verification_code: str | None = None
    deep_link: str | None = None
    bot_username: str | None = None
    notifications_enabled: bool = False
    signal_notifications: bool = False
    trade_notifications: bool = False
    system_notifications: bool = False


class NotificationSettingsRequest(BaseModel):
    signal_notifications: bool | None = None
    trade_notifications: bool | None = None
    system_notifications: bool | None = None


@router.get("/status", response_model=TelegramConnectionResponse)
async def get_telegram_status(
    current_user: CurrentUser,
    db: DB,
):
    telegram_service = TelegramService(db)
    connection = await telegram_service.get_connection_by_user(current_user.id)

    if not connection:
        return TelegramConnectionResponse(is_connected=False)

    return TelegramConnectionResponse(
        is_connected=connection.is_verified,
        telegram_username=connection.telegram_username,
        verification_code=connection.verification_code if not connection.is_verified else None,
        notifications_enabled=connection.notifications_enabled,
        signal_notifications=connection.signal_notifications,
        trade_notifications=connection.trade_notifications,
        system_notifications=connection.system_notifications,
    )


@router.post("/connect", response_model=TelegramConnectionResponse)
async def connect_telegram(
    current_user: CurrentUser,
    db: DB,
):
    telegram_service = TelegramService(db)
    code = await telegram_service.generate_verification_code(current_user)
    await db.commit()

    bot_username = settings.telegram_bot_username
    deep_link = f"https://t.me/{bot_username}?start={code}"

    return TelegramConnectionResponse(
        is_connected=False,
        verification_code=code,
        deep_link=deep_link,
        bot_username=bot_username,
    )


@router.patch("/settings", response_model=TelegramConnectionResponse)
async def update_notification_settings(
    data: NotificationSettingsRequest,
    current_user: CurrentUser,
    db: DB,
):
    telegram_service = TelegramService(db)
    connection = await telegram_service.get_connection_by_user(current_user.id)

    if not connection or not connection.is_verified:
        from app.core.exceptions import BadRequestError

        raise BadRequestError("Telegram not connected")

    connection = await telegram_service.update_notification_settings(
        connection,
        signal_notifications=data.signal_notifications,
        trade_notifications=data.trade_notifications,
        system_notifications=data.system_notifications,
    )
    await db.commit()

    return TelegramConnectionResponse(
        is_connected=True,
        telegram_username=connection.telegram_username,
        notifications_enabled=connection.notifications_enabled,
        signal_notifications=connection.signal_notifications,
        trade_notifications=connection.trade_notifications,
        system_notifications=connection.system_notifications,
    )


@router.post("/disconnect", response_model=SuccessResponse)
async def disconnect_telegram(
    current_user: CurrentUser,
    db: DB,
):
    telegram_service = TelegramService(db)
    connection = await telegram_service.get_connection_by_user(current_user.id)

    if not connection:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Telegram connection")

    await telegram_service.disconnect(connection)
    await db.commit()

    return SuccessResponse(message="Telegram disconnected successfully")


@router.post("/test", response_model=SuccessResponse)
async def send_test_notification(
    current_user: CurrentUser,
    db: DB,
):
    telegram_service = TelegramService(db)
    connection = await telegram_service.get_connection_by_user(current_user.id)

    if not connection or not connection.is_verified or not connection.telegram_chat_id:
        from app.core.exceptions import BadRequestError

        raise BadRequestError("Telegram not connected")

    success = await telegram_service.send_message(
        connection.telegram_chat_id,
        "🎉 <b>Test Notification</b>\n\nYour StackAlpha notifications are working!",
    )

    if not success:
        from app.core.exceptions import TelegramError

        raise TelegramError("Failed to send test notification")

    return SuccessResponse(message="Test notification sent")
