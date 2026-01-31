from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import AdminUser, CurrentUser, Pagination
from app.schemas import (
    AdminUserUpdate,
    PaginatedResponse,
    SuccessResponse,
    UserListResponse,
    UserProfileResponse,
    UserResponse,
    UserStatsResponse,
    UserUpdate,
)
from app.services import UserService

router = APIRouter(prefix="/users", tags=["Users"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: CurrentUser,
    db: DB,
):
    user_service = UserService(db)
    user = await user_service.get_user_by_id(current_user.id)

    wallet_count = len(user.wallets) if user.wallets else 0

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        is_2fa_enabled=user.is_2fa_enabled,
        has_active_subscription=user.has_active_subscription,
        last_login=user.last_login,
        login_count=user.login_count,
        wallet_count=wallet_count,
        trade_count=0,
        is_affiliate=user.affiliate is not None,
        referral_code=user.affiliate.referral_code if user.affiliate else None,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    data: UserUpdate,
    current_user: CurrentUser,
    db: DB,
):
    user_service = UserService(db)
    user = await user_service.update_user(current_user, data)
    await db.commit()
    return user


@router.get("/me/stats", response_model=UserStatsResponse)
async def get_user_stats(
    current_user: CurrentUser,
    db: DB,
):
    user_service = UserService(db)
    stats = await user_service.get_user_stats(current_user.id)
    return stats


@router.get("", response_model=PaginatedResponse[UserListResponse])
async def list_users(
    pagination: Pagination,
    current_user: AdminUser,
    db: DB,
    search: str | None = Query(None, max_length=100),
    is_active: bool | None = None,
    is_verified: bool | None = None,
):
    user_service = UserService(db)
    users, total = await user_service.get_users(
        pagination=pagination,
        search=search,
        is_active=is_active,
        is_verified=is_verified,
    )

    return PaginatedResponse.create(
        items=[UserListResponse.model_validate(u) for u in users],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: AdminUser,
    db: DB,
):
    user_service = UserService(db)
    user = await user_service.get_user_by_id(user_id)
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def admin_update_user(
    user_id: str,
    data: AdminUserUpdate,
    current_user: AdminUser,
    db: DB,
):
    user_service = UserService(db)
    user = await user_service.admin_update_user(user_id, data)
    await db.commit()
    return user


@router.delete("/{user_id}", response_model=SuccessResponse)
async def delete_user(
    user_id: str,
    current_user: AdminUser,
    db: DB,
):
    user_service = UserService(db)
    await user_service.delete_user(user_id)
    await db.commit()
    return SuccessResponse(message="User deleted successfully")
