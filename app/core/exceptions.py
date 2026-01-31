from fastapi import HTTPException, status


class HyperTradeException(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: str,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)


class AuthenticationError(HyperTradeException):
    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthorizationError(HyperTradeException):
    def __init__(self, detail: str = "Not authorized"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class NotFoundError(HyperTradeException):
    def __init__(self, resource: str = "Resource"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource} not found",
        )


class BadRequestError(HyperTradeException):
    def __init__(self, detail: str = "Bad request"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class ConflictError(HyperTradeException):
    def __init__(self, detail: str = "Resource already exists"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class RateLimitError(HyperTradeException):
    def __init__(self, detail: str = "Rate limit exceeded"):
        super().__init__(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


class ValidationError(HyperTradeException):
    def __init__(self, detail: str = "Validation error"):
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


class InternalError(HyperTradeException):
    def __init__(self, detail: str = "Internal server error"):
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


class ServiceUnavailableError(HyperTradeException):
    def __init__(self, detail: str = "Service temporarily unavailable"):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


# Trading specific exceptions
class InsufficientBalanceError(BadRequestError):
    def __init__(self, detail: str = "Insufficient balance for this operation"):
        super().__init__(detail=detail)


class InvalidWalletError(BadRequestError):
    def __init__(self, detail: str = "Invalid wallet address or signature"):
        super().__init__(detail=detail)


class TradingDisabledError(BadRequestError):
    def __init__(self, detail: str = "Trading is currently disabled"):
        super().__init__(detail=detail)


class PositionLimitError(BadRequestError):
    def __init__(self, detail: str = "Maximum position limit reached"):
        super().__init__(detail=detail)


class SubscriptionRequiredError(AuthorizationError):
    def __init__(self, detail: str = "Active subscription required"):
        super().__init__(detail=detail)


# Payment exceptions
class PaymentError(BadRequestError):
    def __init__(self, detail: str = "Payment processing failed"):
        super().__init__(detail=detail)


class WebhookValidationError(BadRequestError):
    def __init__(self, detail: str = "Webhook signature validation failed"):
        super().__init__(detail=detail)


# External service exceptions
class HyperliquidAPIError(ServiceUnavailableError):
    def __init__(self, detail: str = "Hyperliquid API error"):
        super().__init__(detail=detail)


class LLMServiceError(ServiceUnavailableError):
    def __init__(self, detail: str = "LLM service error"):
        super().__init__(detail=detail)


class TelegramError(ServiceUnavailableError):
    def __init__(self, detail: str = "Telegram service error"):
        super().__init__(detail=detail)


class EmailError(ServiceUnavailableError):
    def __init__(self, detail: str = "Email service error"):
        super().__init__(detail=detail)
