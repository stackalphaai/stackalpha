from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, SubscribedUser
from app.schemas import (
    APIWalletResponse,
    AuthorizeWalletRequest,
    ConnectWalletRequest,
    EnableTradingRequest,
    SuccessResponse,
    WalletBalanceResponse,
    WalletPositionResponse,
    WalletResponse,
    WalletSyncResponse,
)
from app.services import WalletService
from app.services.hyperliquid import get_info_service

router = APIRouter(prefix="/wallets", tags=["Wallets"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[WalletResponse])
async def get_wallets(
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallets = await wallet_service.get_user_wallets(current_user.id)
    return wallets


@router.post("/connect", response_model=WalletResponse)
async def connect_wallet(
    data: ConnectWalletRequest,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.connect_wallet(current_user, data.address)
    await db.commit()
    return wallet


@router.post("/{wallet_id}/authorize", response_model=WalletResponse)
async def authorize_wallet(
    wallet_id: str,
    data: AuthorizeWalletRequest,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    wallet = await wallet_service.authorize_wallet(
        wallet,
        data.signature,
        data.message,
    )
    await db.commit()
    return wallet


@router.get("/{wallet_id}/auth-message")
async def get_authorization_message(
    wallet_id: str,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    message = wallet_service.get_authorization_message(wallet.address)
    return {"message": message}


@router.post("/generate-api-wallet", response_model=APIWalletResponse)
async def generate_api_wallet(
    current_user: SubscribedUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet, private_key = await wallet_service.generate_api_wallet(current_user)
    await db.commit()

    return APIWalletResponse(
        address=wallet.address,
        private_key=private_key,
    )


@router.get("/{wallet_id}/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance(
    wallet_id: str,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    info_service = get_info_service()
    balance = await info_service.get_user_balance(wallet.address)

    return WalletBalanceResponse(
        address=wallet.address,
        **balance,
    )


@router.get("/{wallet_id}/positions", response_model=list[WalletPositionResponse])
async def get_wallet_positions(
    wallet_id: str,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    info_service = get_info_service()
    positions = await info_service.get_user_positions(wallet.address)

    return [WalletPositionResponse(**pos) for pos in positions]


@router.post("/{wallet_id}/sync", response_model=WalletSyncResponse)
async def sync_wallet(
    wallet_id: str,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    info_service = get_info_service()
    balance = await info_service.get_user_balance(wallet.address)
    positions = await info_service.get_user_positions(wallet.address)

    wallet = await wallet_service.update_wallet_balance(
        wallet,
        balance_usd=balance.get("balance_usd", 0),
        margin_used=balance.get("margin_used", 0),
        unrealized_pnl=balance.get("unrealized_pnl", 0),
    )
    await db.commit()

    return WalletSyncResponse(
        success=True,
        balance_usd=balance.get("balance_usd", 0),
        positions_count=len(positions),
        synced_at=datetime.now(UTC),
    )


@router.patch("/{wallet_id}/trading", response_model=WalletResponse)
async def toggle_trading(
    wallet_id: str,
    data: EnableTradingRequest,
    current_user: SubscribedUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    wallet = await wallet_service.enable_trading(wallet, data.enabled)
    await db.commit()
    return wallet


@router.delete("/{wallet_id}", response_model=SuccessResponse)
async def disconnect_wallet(
    wallet_id: str,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Wallet")

    await wallet_service.disconnect_wallet(wallet)
    await db.commit()
    return SuccessResponse(message="Wallet disconnected successfully")
