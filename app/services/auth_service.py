import secrets
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AuthenticationError, BadRequestError, ConflictError, NotFoundError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_password_reset_token,
    generate_verification_token,
    get_password_hash,
    verify_email_token,
    verify_password,
    verify_password_reset_token,
)
from app.models import User
from app.schemas.auth import RegisterRequest, TokenResponse


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(
        self,
        data: RegisterRequest,
        ip_address: str | None = None,
    ) -> User:
        result = await self.db.execute(select(User).where(User.email == data.email.lower()))
        if result.scalar_one_or_none():
            raise ConflictError("User with this email already exists")

        user = User(
            email=data.email.lower(),
            hashed_password=get_password_hash(data.password),
            full_name=data.full_name,
            is_active=True,
            is_verified=True,  # Auto-verify for DeFi platform
            verification_token=None,
        )

        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)

        return user

    async def login(
        self,
        email: str,
        password: str,
        totp_code: str | None = None,
    ) -> tuple[User, TokenResponse]:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        if not user:
            raise AuthenticationError("Invalid email or password")

        if not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid email or password")

        if not user.is_active:
            raise AuthenticationError("Account is deactivated")

        if user.is_2fa_enabled:
            if not totp_code:
                raise AuthenticationError("2FA code required")
            if not self._verify_totp(user.totp_secret, totp_code):
                raise AuthenticationError("Invalid 2FA code")

        user.last_login = datetime.now(UTC)
        user.login_count += 1

        tokens = self._generate_tokens(user)

        return user, tokens

    async def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        payload = decode_token(refresh_token)

        if not payload:
            raise AuthenticationError("Invalid refresh token")

        if payload.get("type") != "refresh":
            raise AuthenticationError("Invalid token type")

        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationError("Invalid token payload")

        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise AuthenticationError("User not found or inactive")

        return self._generate_tokens(user)

    async def verify_email(self, token: str) -> User:
        email = verify_email_token(token)

        if not email:
            raise BadRequestError("Invalid or expired verification token")

        result = await self.db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundError("User")

        if user.is_verified:
            raise BadRequestError("Email already verified")

        user.is_verified = True
        user.verification_token = None

        return user

    async def resend_verification(self, email: str) -> str:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundError("User")

        if user.is_verified:
            raise BadRequestError("Email already verified")

        token = generate_verification_token(email.lower())
        user.verification_token = token

        return token

    async def forgot_password(self, email: str) -> str | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        if not user:
            return None

        token = generate_password_reset_token(email.lower())
        user.password_reset_token = token
        user.password_reset_expires = datetime.now(UTC) + timedelta(hours=1)

        return token

    async def reset_password(self, token: str, new_password: str) -> User:
        email = verify_password_reset_token(token)

        if not email:
            raise BadRequestError("Invalid or expired reset token")

        result = await self.db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundError("User")

        if user.password_reset_token != token:
            raise BadRequestError("Invalid reset token")

        if user.password_reset_expires and datetime.now(UTC) > user.password_reset_expires:
            raise BadRequestError("Reset token has expired")

        user.hashed_password = get_password_hash(new_password)
        user.password_reset_token = None
        user.password_reset_expires = None

        return user

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> User:
        if not verify_password(current_password, user.hashed_password):
            raise AuthenticationError("Current password is incorrect")

        user.hashed_password = get_password_hash(new_password)

        return user

    async def setup_2fa(self, user: User) -> tuple[str, str]:
        if user.is_2fa_enabled:
            raise BadRequestError("2FA is already enabled")

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)

        provisioning_uri = totp.provisioning_uri(
            name=user.email,
            issuer_name=settings.app_name,
        )

        user.totp_secret = secret

        return secret, provisioning_uri

    async def verify_2fa_setup(self, user: User, totp_code: str) -> list[str]:
        if not user.totp_secret:
            raise BadRequestError("2FA setup not initiated")

        if user.is_2fa_enabled:
            raise BadRequestError("2FA is already enabled")

        if not self._verify_totp(user.totp_secret, totp_code):
            raise AuthenticationError("Invalid 2FA code")

        user.is_2fa_enabled = True

        backup_codes = [secrets.token_hex(4) for _ in range(8)]

        return backup_codes

    async def disable_2fa(
        self,
        user: User,
        totp_code: str,
        password: str,
    ) -> User:
        if not user.is_2fa_enabled:
            raise BadRequestError("2FA is not enabled")

        if not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid password")

        if not self._verify_totp(user.totp_secret, totp_code):
            raise AuthenticationError("Invalid 2FA code")

        user.is_2fa_enabled = False
        user.totp_secret = None

        return user

    def _generate_tokens(self, user: User) -> TokenResponse:
        access_token = create_access_token(
            subject=user.id,
            additional_claims={
                "email": user.email,
                "is_admin": user.is_admin,
            },
        )

        refresh_token = create_refresh_token(subject=user.id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )

    def _verify_totp(self, secret: str, code: str) -> bool:
        if not secret:
            return False
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)
