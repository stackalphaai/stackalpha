from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Trade, TradeStatus, User
from app.schemas.common import PaginationParams
from app.schemas.user import AdminUserUpdate, UserStatsResponse, UserUpdate


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_by_id(self, user_id: str) -> User | None:
        result = await self.db.execute(
            select(User)
            .options(
                selectinload(User.wallets),
                selectinload(User.subscriptions),
                selectinload(User.affiliate),
            )
            .where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def update_user(self, user: User, data: UserUpdate) -> User:
        if data.email and data.email.lower() != user.email:
            existing = await self.get_user_by_email(data.email)
            if existing:
                raise ConflictError("Email already in use")
            user.email = data.email.lower()
            user.is_verified = False

        if data.full_name is not None:
            user.full_name = data.full_name

        return user

    async def get_users(
        self,
        pagination: PaginationParams,
        search: str | None = None,
        is_active: bool | None = None,
        is_verified: bool | None = None,
    ) -> tuple[list[User], int]:
        query = select(User)

        if search:
            query = query.where(
                User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%")
            )

        if is_active is not None:
            query = query.where(User.is_active == is_active)

        if is_verified is not None:
            query = query.where(User.is_verified == is_verified)

        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar() or 0

        query = query.order_by(User.created_at.desc())
        query = query.offset(pagination.offset).limit(pagination.limit)

        result = await self.db.execute(query)
        users = list(result.scalars().all())

        return users, total

    async def admin_update_user(
        self,
        user_id: str,
        data: AdminUserUpdate,
    ) -> User:
        user = await self.get_user_by_id(user_id)
        if not user:
            raise NotFoundError("User")

        if data.is_active is not None:
            user.is_active = data.is_active

        if data.is_verified is not None:
            user.is_verified = data.is_verified

        if data.is_admin is not None:
            user.is_admin = data.is_admin

        return user

    async def delete_user(self, user_id: str) -> bool:
        user = await self.get_user_by_id(user_id)
        if not user:
            raise NotFoundError("User")

        await self.db.delete(user)
        return True

    async def get_user_stats(self, user_id: str) -> UserStatsResponse:
        result = await self.db.execute(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.status == TradeStatus.CLOSED,
            )
        )
        trades = list(result.scalars().all())

        if not trades:
            return UserStatsResponse(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl=0.0,
                average_trade_duration=None,
                best_trade_pnl=0.0,
                worst_trade_pnl=0.0,
            )

        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.realized_pnl and t.realized_pnl > 0)
        losing_trades = sum(1 for t in trades if t.realized_pnl and t.realized_pnl < 0)
        total_pnl = sum(t.realized_pnl or 0 for t in trades)

        pnls = [t.realized_pnl for t in trades if t.realized_pnl is not None]
        best_trade_pnl = max(pnls) if pnls else 0.0
        worst_trade_pnl = min(pnls) if pnls else 0.0

        durations = [t.duration_seconds for t in trades if t.duration_seconds]
        avg_duration = sum(durations) // len(durations) if durations else None

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        return UserStatsResponse(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 2),
            total_pnl=round(float(total_pnl), 2),
            average_trade_duration=avg_duration,
            best_trade_pnl=round(float(best_trade_pnl), 2),
            worst_trade_pnl=round(float(worst_trade_pnl), 2),
        )

    async def get_total_users_count(self) -> int:
        result = await self.db.execute(select(func.count(User.id)))
        return result.scalar() or 0

    async def get_active_users_count(self) -> int:
        result = await self.db.execute(select(func.count(User.id)).where(User.is_active))
        return result.scalar() or 0

    async def get_verified_users_count(self) -> int:
        result = await self.db.execute(select(func.count(User.id)).where(User.is_verified))
        return result.scalar() or 0
