import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.schemas import (
    ChangePasswordRequest,
    Disable2FARequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    Setup2FAResponse,
    SuccessResponse,
    TokenResponse,
    UserResponse,
    Verify2FARequest,
    VerifyEmailRequest,
)
from app.services import AuthService
from app.services.affiliate_service import AffiliateService
from app.services.email_service import get_email_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Type alias for dependency injection
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/register", response_model=UserResponse)
async def register(
    data: RegisterRequest,
    request: Request,
    db: DB,
):
    auth_service = AuthService(db)
    user = await auth_service.register(
        data,
        ip_address=request.client.host if request.client else None,
    )

    if data.referral_code:
        affiliate_service = AffiliateService(db)
        affiliate = await affiliate_service.get_affiliate_by_code(data.referral_code)
        if affiliate:
            await affiliate_service.track_referral(
                affiliate,
                user,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )

    await db.commit()
    await db.refresh(user)

    # Users are auto-verified for DeFi platform - no email verification needed

    return user


async def _send_verification_email(email: str, token: str) -> None:
    """Background task to send verification email."""
    try:
        email_service = get_email_service()
        await email_service.send_verification_email(email, token)
    except Exception as e:
        logger.error(f"Failed to send verification email to {email}: {e}")


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    db: DB,
):
    auth_service = AuthService(db)
    user, tokens = await auth_service.login(
        email=data.email,
        password=data.password,
        totp_code=data.totp_code,
    )
    await db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    data: RefreshTokenRequest,
    db: DB,
):
    auth_service = AuthService(db)
    tokens = await auth_service.refresh_tokens(data.refresh_token)
    return tokens


@router.post("/verify-email", response_model=SuccessResponse)
async def verify_email(
    data: VerifyEmailRequest,
    db: DB,
):
    auth_service = AuthService(db)
    await auth_service.verify_email(data.token)
    await db.commit()
    return SuccessResponse(message="Email verified successfully")


@router.post("/resend-verification", response_model=SuccessResponse)
async def resend_verification(
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: DB,
):
    auth_service = AuthService(db)
    token = await auth_service.resend_verification(data.email)
    await db.commit()

    # Send verification email in background
    background_tasks.add_task(_send_verification_email, data.email, token)

    return SuccessResponse(message="Verification email sent")


@router.post("/forgot-password", response_model=SuccessResponse)
async def forgot_password(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: DB,
):
    auth_service = AuthService(db)
    token = await auth_service.forgot_password(data.email)
    await db.commit()

    if token:
        # Send password reset email in background
        background_tasks.add_task(_send_password_reset_email, data.email, token)

    return SuccessResponse(message="If the email exists, a reset link has been sent")


async def _send_password_reset_email(email: str, token: str) -> None:
    """Background task to send password reset email."""
    try:
        email_service = get_email_service()
        await email_service.send_password_reset_email(email, token)
    except Exception as e:
        logger.error(f"Failed to send password reset email to {email}: {e}")


@router.post("/reset-password", response_model=SuccessResponse)
async def reset_password(
    data: ResetPasswordRequest,
    db: DB,
):
    auth_service = AuthService(db)
    await auth_service.reset_password(data.token, data.new_password)
    await db.commit()
    return SuccessResponse(message="Password reset successfully")


@router.post("/change-password", response_model=SuccessResponse)
async def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser,
    db: DB,
):
    auth_service = AuthService(db)
    await auth_service.change_password(
        current_user,
        data.current_password,
        data.new_password,
    )
    await db.commit()
    return SuccessResponse(message="Password changed successfully")


@router.post("/2fa/setup", response_model=Setup2FAResponse)
async def setup_2fa(
    current_user: CurrentUser,
    db: DB,
):
    auth_service = AuthService(db)
    secret, qr_uri = await auth_service.setup_2fa(current_user)
    await db.commit()

    return Setup2FAResponse(
        secret=secret,
        qr_code_uri=qr_uri,
        backup_codes=[],
    )


@router.post("/2fa/verify", response_model=Setup2FAResponse)
async def verify_2fa_setup(
    data: Verify2FARequest,
    current_user: CurrentUser,
    db: DB,
):
    auth_service = AuthService(db)
    backup_codes = await auth_service.verify_2fa_setup(current_user, data.totp_code)
    await db.commit()

    return Setup2FAResponse(
        secret="",
        qr_code_uri="",
        backup_codes=backup_codes,
    )


@router.post("/2fa/disable", response_model=SuccessResponse)
async def disable_2fa(
    data: Disable2FARequest,
    current_user: CurrentUser,
    db: DB,
):
    auth_service = AuthService(db)
    await auth_service.disable_2fa(current_user, data.totp_code, data.password)
    await db.commit()
    return SuccessResponse(message="2FA disabled successfully")
