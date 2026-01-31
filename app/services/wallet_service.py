from datetime import UTC, datetime

from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.core.exceptions import BadRequestError, ConflictError, InvalidWalletError
from app.core.security import decrypt_data, encrypt_data
from app.models import User, Wallet, WalletStatus, WalletType


class WalletService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.w3 = Web3()

    async def connect_wallet(
        self,
        user: User,
        address: str,
    ) -> Wallet:
        address = Web3.to_checksum_address(address.lower())

        result = await self.db.execute(
            select(Wallet).where(
                Wallet.address == address.lower(),
                Wallet.user_id != user.id,
            )
        )
        if result.scalar_one_or_none():
            raise ConflictError("Wallet is already connected to another account")

        result = await self.db.execute(
            select(Wallet).where(
                Wallet.address == address.lower(),
                Wallet.user_id == user.id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        wallet = Wallet(
            user_id=user.id,
            address=address.lower(),
            wallet_type=WalletType.MASTER,
            status=WalletStatus.PENDING,
            is_trading_enabled=False,
            is_authorized=False,
        )

        self.db.add(wallet)
        await self.db.flush()
        await self.db.refresh(wallet)

        return wallet

    async def authorize_wallet(
        self,
        wallet: Wallet,
        signature: str,
        message: str,
    ) -> Wallet:
        if wallet.is_authorized:
            raise BadRequestError("Wallet is already authorized")

        expected_message = self._get_authorization_message(wallet.address)
        if message != expected_message:
            raise InvalidWalletError("Invalid authorization message")

        try:
            message_hash = encode_defunct(text=message)
            recovered_address = Account.recover_message(message_hash, signature=signature)

            if recovered_address.lower() != wallet.address.lower():
                raise InvalidWalletError("Signature does not match wallet address")

        except Exception as e:
            raise InvalidWalletError(f"Invalid signature: {str(e)}") from e

        wallet.is_authorized = True
        wallet.authorization_signature = signature
        wallet.status = WalletStatus.ACTIVE

        return wallet

    async def generate_api_wallet(self, user: User) -> tuple[Wallet, str]:
        account = Account.create()
        address = account.address.lower()
        private_key = account.key.hex()

        encrypted_key = encrypt_data(private_key)

        wallet = Wallet(
            user_id=user.id,
            address=address,
            wallet_type=WalletType.API,
            status=WalletStatus.ACTIVE,
            encrypted_private_key=encrypted_key,
            is_trading_enabled=True,
            is_authorized=True,
        )

        self.db.add(wallet)
        await self.db.flush()
        await self.db.refresh(wallet)

        return wallet, private_key

    async def get_wallet_by_id(
        self,
        wallet_id: str,
        user_id: str | None = None,
    ) -> Wallet | None:
        query = select(Wallet).where(Wallet.id == wallet_id)
        if user_id:
            query = query.where(Wallet.user_id == user_id)

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_user_wallets(self, user_id: str) -> list[Wallet]:
        result = await self.db.execute(
            select(Wallet).where(Wallet.user_id == user_id).order_by(Wallet.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_trading_wallets(self, user_id: str) -> list[Wallet]:
        result = await self.db.execute(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.status == WalletStatus.ACTIVE,
                Wallet.is_trading_enabled,
                Wallet.is_authorized,
            )
        )
        return list(result.scalars().all())

    async def enable_trading(
        self,
        wallet: Wallet,
        enabled: bool = True,
    ) -> Wallet:
        if not wallet.is_authorized:
            raise BadRequestError("Wallet must be authorized before enabling trading")

        if wallet.status != WalletStatus.ACTIVE:
            raise BadRequestError("Wallet is not active")

        wallet.is_trading_enabled = enabled

        return wallet

    async def disconnect_wallet(self, wallet: Wallet) -> Wallet:
        wallet.status = WalletStatus.DISCONNECTED
        wallet.is_trading_enabled = False

        return wallet

    async def update_wallet_balance(
        self,
        wallet: Wallet,
        balance_usd: float,
        margin_used: float = 0.0,
        unrealized_pnl: float = 0.0,
    ) -> Wallet:
        wallet.balance_usd = balance_usd
        wallet.margin_used = margin_used
        wallet.unrealized_pnl = unrealized_pnl
        wallet.last_sync_at = datetime.now(UTC)

        return wallet

    def get_private_key(self, wallet: Wallet) -> str | None:
        if not wallet.encrypted_private_key:
            return None
        return decrypt_data(wallet.encrypted_private_key)

    def _get_authorization_message(self, address: str) -> str:
        return (
            f"Sign this message to authorize StackAlpha to manage trades on your behalf.\n\n"
            f"Wallet: {address}\n"
            f"This will NOT give access to transfer your funds."
        )

    def get_authorization_message(self, address: str) -> str:
        return self._get_authorization_message(address)

    async def delete_wallet(self, wallet: Wallet) -> bool:
        await self.db.delete(wallet)
        return True
