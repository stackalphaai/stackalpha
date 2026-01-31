from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthenticationError, AuthorizationError, SubscriptionRequiredError
from app.core.security import decode_token
from app.database import get_db
from app.models import User, Wallet
from app.schemas.common import PaginationParams

security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise AuthenticationError("Invalid or expired token")

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.subscriptions),
            selectinload(User.wallets),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise AuthenticationError("User not found")

    if not user.is_active:
        raise AuthenticationError("User account is deactivated")

    return user


async def get_current_verified_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_verified:
        raise AuthorizationError("Email verification required")
    return current_user


async def get_current_subscribed_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.has_active_subscription:
        raise SubscriptionRequiredError()
    return current_user


async def get_current_admin_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_admin and not current_user.is_superadmin:
        raise AuthorizationError("Admin access required")
    return current_user


async def get_current_superadmin_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_superadmin:
        raise AuthorizationError("Superadmin access required")
    return current_user


async def get_optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    if not credentials:
        return None

    try:
        payload = decode_token(credentials.credentials)
        if not payload or payload.get("type") != "access":
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
    except Exception:
        return None


def get_pagination_params(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


async def get_user_wallet(
    wallet_id: str,
    current_user: Annotated[User, Depends(get_current_subscribed_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Wallet:
    result = await db.execute(
        select(Wallet).where(
            Wallet.id == wallet_id,
            Wallet.user_id == current_user.id,
        )
    )
    wallet = result.scalar_one_or_none()

    if not wallet:
        raise AuthorizationError("Wallet not found or access denied")

    return wallet


CurrentUser = Annotated[User, Depends(get_current_user)]
VerifiedUser = Annotated[User, Depends(get_current_verified_user)]
SubscribedUser = Annotated[User, Depends(get_current_subscribed_user)]
AdminUser = Annotated[User, Depends(get_current_admin_user)]
SuperAdminUser = Annotated[User, Depends(get_current_superadmin_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
Pagination = Annotated[PaginationParams, Depends(get_pagination_params)]
DB = Annotated[AsyncSession, Depends(get_db)]
