from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, SubscribedUser
from app.schemas import (
    ConnectAgentWalletRequest,
    ConnectAPIWalletRequest,
    EnableTradingRequest,
    SuccessResponse,
    WalletBalanceResponse,
    WalletPositionResponse,
    WalletResponse,
    WalletSyncResponse,
    WalletTransferRequest,
    WalletTransferResponse,
)
from app.services import WalletService
from app.services.hyperliquid import get_exchange_service, get_info_service

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


@router.post("/connect-agent", response_model=WalletResponse)
async def connect_agent_wallet(
    data: ConnectAgentWalletRequest,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.connect_agent_wallet(
        current_user, data.address, data.private_key, data.master_address
    )
    await db.commit()
    return wallet


@router.post("/connect-api", response_model=WalletResponse)
async def connect_api_wallet(
    data: ConnectAPIWalletRequest,
    current_user: CurrentUser,
    db: DB,
):
    wallet_service = WalletService(db)
    wallet = await wallet_service.connect_api_wallet(current_user, data.address, data.private_key)
    await db.commit()
    return wallet


@router.post("/{wallet_id}/verify-agent", response_model=WalletResponse)
async def verify_agent_approval(
    wallet_id: str,
    current_user: CurrentUser,
    db: DB,
):
    from app.core.exceptions import NotFoundError

    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        raise NotFoundError("Wallet")

    approved = await wallet_service.verify_agent_approval(wallet)
    if approved:
        await db.commit()

    return wallet


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
    balance = await info_service.get_user_balance(wallet.query_address)

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
    positions = await info_service.get_user_positions(wallet.query_address)

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
    balance = await info_service.get_user_balance(wallet.query_address)
    positions = await info_service.get_user_positions(wallet.query_address)

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


@router.post("/{wallet_id}/transfer", response_model=WalletTransferResponse)
async def transfer_usd(
    wallet_id: str,
    data: WalletTransferRequest,
    current_user: CurrentUser,
    db: DB,
):
    from app.core.exceptions import BadRequestError, NotFoundError

    wallet_service = WalletService(db)
    wallet = await wallet_service.get_wallet_by_id(wallet_id, current_user.id)

    if not wallet:
        raise NotFoundError("Wallet")

    # Get the decrypted private key
    private_key = wallet_service.get_private_key(wallet)
    if not private_key:
        raise BadRequestError("Wallet private key not found")

    # Execute the transfer
    exchange_service = get_exchange_service()
    try:
        await exchange_service.usd_transfer(
            private_key=private_key,
            amount=data.amount,
            to_perp=data.to_perp,
            vault_address=wallet.master_address,
        )

        direction = "Spot → Perp" if data.to_perp else "Perp → Spot"

        # Sync wallet balance after transfer
        info_service = get_info_service()
        balance = await info_service.get_user_balance(wallet.query_address)
        await wallet_service.update_wallet_balance(
            wallet,
            balance_usd=balance.get("balance_usd", 0),
            margin_used=balance.get("margin_used", 0),
            unrealized_pnl=balance.get("unrealized_pnl", 0),
        )
        await db.commit()

        return WalletTransferResponse(
            success=True,
            message=f"Successfully transferred {data.amount} USDC from {direction}",
            amount=data.amount,
            direction=direction,
        )
    except Exception as e:
        raise BadRequestError(f"Transfer failed: {str(e)}") from e


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
