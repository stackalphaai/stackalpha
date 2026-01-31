from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "StackAlpha"
    app_env: str = "development"
    debug: bool = False
    secret_key: str
    api_version: str = "v1"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    cors_origins: list[str] = ["http://localhost:3000"]

    # Database
    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Encryption
    encryption_key: str

    # Hyperliquid
    hyperliquid_mainnet_url: str = "https://api.hyperliquid.xyz"
    hyperliquid_testnet_url: str = "https://api.hyperliquid-testnet.xyz"
    hyperliquid_ws_mainnet: str = "wss://api.hyperliquid.xyz/ws"
    hyperliquid_ws_testnet: str = "wss://api.hyperliquid-testnet.xyz/ws"
    hyperliquid_use_testnet: bool = True

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_models: list[str] = [
        "anthropic/claude-3-sonnet",
        "openai/gpt-4-turbo",
        "google/gemini-pro",
    ]
    llm_consensus_threshold: float = 0.66

    # NOWPayments
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    nowpayments_api_url: str = "https://api.nowpayments.io/v1"
    nowpayments_ipn_callback_url: str = ""
    nowpayments_success_url: str = ""
    nowpayments_cancel_url: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""

    # Email (Zoho ZeptoMail API)
    zeptomail_api_key: str = ""
    zeptomail_api_url: str = "https://api.zeptomail.com/v1.1/email"
    email_from_name: str = "StackAlpha"
    email_from_address: str = "noreply@stackalpha.xyz"

    # Subscription
    subscription_monthly_price: float = 50.00
    subscription_yearly_price: float = 500.00
    subscription_grace_period_days: int = 3

    # Trading
    max_position_size_percent: float = 10.0
    default_leverage: int = 5
    max_leverage: int = 20
    analysis_interval_hours: int = 4
    max_concurrent_positions: int = 5

    # Affiliate
    affiliate_initial_commission_percent: float = 20.0
    affiliate_renewal_commission_percent: float = 5.0
    affiliate_payout_minimum: float = 50.0
    affiliate_cookie_days: int = 30

    # Rate Limiting
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    @property
    def hyperliquid_api_url(self) -> str:
        return (
            self.hyperliquid_testnet_url
            if self.hyperliquid_use_testnet
            else self.hyperliquid_mainnet_url
        )

    @property
    def hyperliquid_ws_url(self) -> str:
        return (
            self.hyperliquid_ws_testnet
            if self.hyperliquid_use_testnet
            else self.hyperliquid_ws_mainnet
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @field_validator("cors_origins", "allowed_hosts", "llm_models", mode="before")
    @classmethod
    def parse_list(cls, v):
        if isinstance(v, str):
            import json

            return json.loads(v)
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
