import logging
from datetime import UTC, datetime
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
from app.services.geolocation_service import get_geolocation_service
from app.utils.device import parse_user_agent

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
    request: Request,
    background_tasks: BackgroundTasks,
    db: DB,
):
    # Extract client info for login notification
    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    auth_service = AuthService(db)
    user, tokens, metadata = await auth_service.login(
        email=data.email,
        password=data.password,
        totp_code=data.totp_code,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    await db.commit()

    # Send login notification in background
    background_tasks.add_task(
        _send_login_notification,
        user.email,
        user.full_name,
        ip_address,
        user_agent,
        metadata.login_time,
    )

    return tokens


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP address from the request.

    Handles common proxy headers (X-Forwarded-For, X-Real-IP) to get
    the actual client IP when behind a reverse proxy or load balancer.
    """
    # Check X-Forwarded-For header (common for proxies/load balancers)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, first one is the client
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP header (used by nginx)
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # Check CF-Connecting-IP header (Cloudflare)
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    # Fall back to direct client IP
    if request.client:
        return request.client.host

    return "Unknown"


async def _send_login_notification(
    email: str,
    name: str | None,
    ip_address: str | None,
    user_agent: str | None,
    login_time: datetime | None,
) -> None:
    """
    Background task to send enterprise-grade login notification email.

    Gathers geolocation data from IP and device info from user-agent,
    then sends a comprehensive login notification email.
    """
    try:
        # Get geolocation data
        geo_service = get_geolocation_service()
        geo = await geo_service.lookup(ip_address or "")

        # Parse device info
        device = parse_user_agent(user_agent)

        # Determine if this login might be suspicious
        is_suspicious = geo.is_proxy or geo.is_vpn or geo.is_hosting

        # Send the notification email
        email_service = get_email_service()
        await email_service.send_login_notification_email(
            to_email=email,
            ip_address=ip_address or "Unknown",
            location=geo.display_location,
            device=device.display_device,
            login_time=login_time or datetime.now(UTC),
            browser=device.browser,
            os=device.os,
            timezone=geo.timezone,
            isp=geo.isp,
            is_suspicious=is_suspicious,
            is_vpn=geo.is_vpn or geo.is_proxy,
            name=name,
        )
        logger.info(f"Login notification sent to {email}")

    except Exception as e:
        logger.error(f"Failed to send login notification to {email}: {e}")


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
