import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.core.exceptions import BadRequestError, ConflictError
from app.core.security import decrypt_data, encrypt_data
from app.models import User, Wallet, WalletStatus, WalletType

logger = logging.getLogger(__name__)


class WalletService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.w3 = Web3()

    async def _check_address_conflict(self, address: str, user_id: str) -> Wallet | None:
        """Check if address is already connected to another user, return existing if same user."""
        result = await self.db.execute(
            select(Wallet).where(
                Wallet.address == address.lower(),
                Wallet.user_id != user_id,
            )
        )
        if result.scalar_one_or_none():
            raise ConflictError("Wallet is already connected to another account")

        result = await self.db.execute(
            select(Wallet).where(
                Wallet.address == address.lower(),
                Wallet.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def connect_agent_wallet(
        self,
        user: User,
        address: str,
        private_key: str,
        master_address: str,
    ) -> Wallet:
        address = Web3.to_checksum_address(address.lower()).lower()
        master_address = Web3.to_checksum_address(master_address.lower()).lower()

        existing = await self._check_address_conflict(address, user.id)
        if existing:
            return existing

        encrypted_key = encrypt_data(private_key)

        wallet = Wallet(
            user_id=user.id,
            address=address,
            wallet_type=WalletType.AGENT,
            status=WalletStatus.ACTIVE,
            encrypted_private_key=encrypted_key,
            master_address=master_address,
            is_trading_enabled=False,
            is_authorized=True,
            is_agent_approved=False,
        )

        self.db.add(wallet)
        await self.db.flush()
        await self.db.refresh(wallet)

        return wallet

    async def connect_api_wallet(
        self,
        user: User,
        address: str,
        private_key: str,
    ) -> Wallet:
        address = Web3.to_checksum_address(address.lower()).lower()

        existing = await self._check_address_conflict(address, user.id)
        if existing:
            return existing

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

        return wallet

    async def verify_agent_approval(self, wallet: Wallet) -> bool:
        if wallet.wallet_type != WalletType.AGENT:
            raise BadRequestError("Only agent wallets need agent approval")

        if not wallet.master_address:
            raise BadRequestError("Wallet has no master address configured")

        if wallet.is_agent_approved:
            return True

        from app.services.hyperliquid import get_info_service

        info_service = get_info_service()

        try:
            result = await info_service.client.info_request(
                {"type": "extraAgents", "user": wallet.master_address}
            )

            agent_address = wallet.address.lower()
            if isinstance(result, list):
                for agent in result:
                    addr = agent.get("address", "").lower()
                    if addr == agent_address:
                        wallet.is_agent_approved = True
                        logger.info(
                            f"Agent {wallet.address} approved for master {wallet.master_address}"
                        )
                        return True

            logger.info(
                f"Agent {wallet.address} not yet approved for master {wallet.master_address}"
            )
            return False

        except Exception as e:
            logger.error(f"Failed to verify agent approval: {e}")
            raise BadRequestError(f"Failed to verify agent approval: {e}") from e

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

    async def delete_wallet(self, wallet: Wallet) -> bool:
        await self.db.delete(wallet)
        return True
