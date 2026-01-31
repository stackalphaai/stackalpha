from app.services.affiliate_service import AffiliateService
from app.services.auth_service import AuthService
from app.services.email_service import EmailService, get_email_service
from app.services.payment_service import PaymentService
from app.services.telegram_service import TelegramService
from app.services.user_service import UserService
from app.services.wallet_service import WalletService

__all__ = [
    "AuthService",
    "UserService",
    "WalletService",
    "PaymentService",
    "TelegramService",
    "EmailService",
    "get_email_service",
    "AffiliateService",
]
