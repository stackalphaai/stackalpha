from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.database import get_db
from app.dependencies import CurrentUser, SubscribedUser
from app.schemas import SuccessResponse
from app.schemas.exchange import (
    ConnectExchangeRequest,
    ExchangeBalanceResponse,
    ExchangeConnectionResponse,
    ExchangeSyncResponse,
    ToggleExchangeTradingRequest,
)
from app.services.exchange_connection_service import ExchangeConnectionService

router = APIRouter(prefix="/exchanges", tags=["Exchanges"])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/setup-info")
async def get_exchange_setup_info(current_user: CurrentUser):
    """Get info needed to set up a Binance API key (server IP for whitelisting)."""
    from app.config import settings

    return {
        "server_ip": getattr(settings, "server_public_ip", ""),
        "permissions_required": ["Enable Futures", "Enable Spot & Margin Trading"],
        "permissions_forbidden": ["Enable Withdrawals"],
        "ip_restriction_recommended": True,
    }


@router.get("", response_model=list[ExchangeConnectionResponse])
async def get_exchanges(
    current_user: CurrentUser,
    db: DB,
):
    """List user's exchange connections."""
    service = ExchangeConnectionService(db)
    return await service.get_user_connections(current_user.id)


@router.post("/connect", response_model=ExchangeConnectionResponse)
async def connect_exchange(
    data: ConnectExchangeRequest,
    current_user: CurrentUser,
    db: DB,
):
    """Connect a new exchange with API credentials."""
    service = ExchangeConnectionService(db)
    connection = await service.connect_exchange(
        user=current_user,
        exchange_type=data.exchange_type,
        api_key=data.api_key,
        api_secret=data.api_secret,
        is_testnet=data.is_testnet,
        label=data.label,
    )
    await db.commit()
    return connection


@router.get("/{connection_id}", response_model=ExchangeConnectionResponse)
async def get_exchange(
    connection_id: str,
    current_user: CurrentUser,
    db: DB,
):
    """Get a specific exchange connection."""
    service = ExchangeConnectionService(db)
    connection = await service.get_connection_by_id(connection_id, current_user.id)
    if not connection:
        raise NotFoundError("Exchange connection")
    return connection


@router.get("/{connection_id}/balance", response_model=ExchangeBalanceResponse)
async def get_exchange_balance(
    connection_id: str,
    current_user: CurrentUser,
    db: DB,
):
    """Get live balance from the exchange."""
    service = ExchangeConnectionService(db)
    connection = await service.get_connection_by_id(connection_id, current_user.id)
    if not connection:
        raise NotFoundError("Exchange connection")

    from app.services.binance import create_binance_exchange_service

    binance_exchange = await create_binance_exchange_service(connection)
    try:
        balance = await binance_exchange.get_balance()
        return ExchangeBalanceResponse(**balance)
    finally:
        await binance_exchange.close()


@router.patch("/{connection_id}/trading", response_model=ExchangeConnectionResponse)
async def toggle_exchange_trading(
    connection_id: str,
    data: ToggleExchangeTradingRequest,
    current_user: SubscribedUser,
    db: DB,
):
    """Toggle auto-trading for an exchange connection. Requires active subscription."""
    service = ExchangeConnectionService(db)
    connection = await service.get_connection_by_id(connection_id, current_user.id)
    if not connection:
        raise NotFoundError("Exchange connection")

    connection = await service.toggle_trading(connection, data.enabled)
    await db.commit()
    await db.refresh(connection)
    return connection


@router.post("/{connection_id}/sync", response_model=ExchangeSyncResponse)
async def sync_exchange(
    connection_id: str,
    current_user: CurrentUser,
    db: DB,
):
    """Sync balance and positions from the exchange."""
    service = ExchangeConnectionService(db)
    connection = await service.get_connection_by_id(connection_id, current_user.id)
    if not connection:
        raise NotFoundError("Exchange connection")

    result = await service.sync_balance(connection)
    await db.commit()

    return ExchangeSyncResponse(
        success=True,
        balance_usd=result.get("balance_usd", 0),
        positions_count=result.get("positions_count", 0),
        synced_at=datetime.now(UTC),
    )


@router.delete("/{connection_id}", response_model=SuccessResponse)
async def disconnect_exchange(
    connection_id: str,
    current_user: CurrentUser,
    db: DB,
):
    """Disconnect an exchange connection."""
    service = ExchangeConnectionService(db)
    connection = await service.get_connection_by_id(connection_id, current_user.id)
    if not connection:
        raise NotFoundError("Exchange connection")

    await service.disconnect_exchange(connection)
    await db.commit()
    return SuccessResponse(message="Exchange disconnected successfully")
