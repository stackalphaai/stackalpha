from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.schemas import SuccessResponse
from app.services import TelegramService

router = APIRouter(prefix="/telegram", tags=["Telegram"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


class TelegramConnectRequest(BaseModel):
    bot_token: str
    chat_id: int


class TelegramConnectionResponse(BaseModel):
    is_connected: bool
    chat_id: int | None = None
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
        chat_id=connection.telegram_chat_id,
        notifications_enabled=connection.notifications_enabled,
        signal_notifications=connection.signal_notifications,
        trade_notifications=connection.trade_notifications,
        system_notifications=connection.system_notifications,
    )


@router.post("/connect", response_model=TelegramConnectionResponse)
async def connect_telegram(
    data: TelegramConnectRequest,
    current_user: CurrentUser,
    db: DB,
):
    telegram_service = TelegramService(db)

    try:
        connection = await telegram_service.connect_user(current_user, data.bot_token, data.chat_id)
    except ValueError as e:
        from app.core.exceptions import BadRequestError

        raise BadRequestError(str(e)) from e

    await db.commit()

    return TelegramConnectionResponse(
        is_connected=True,
        chat_id=connection.telegram_chat_id,
        notifications_enabled=connection.notifications_enabled,
        signal_notifications=connection.signal_notifications,
        trade_notifications=connection.trade_notifications,
        system_notifications=connection.system_notifications,
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
        chat_id=connection.telegram_chat_id,
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
        connection,
        "🎉 <b>Test Notification</b>\n\nYour StackAlpha notifications are working!",
    )

    if not success:
        from app.core.exceptions import TelegramError

        raise TelegramError("Failed to send test notification")

    return SuccessResponse(message="Test notification sent")
