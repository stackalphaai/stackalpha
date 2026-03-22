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
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "https://stackalpha.xyz",
        "https://www.stackalpha.xyz",
        "https://admin.stackalpha.xyz",
    ]

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
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.2-chat",
        "x-ai/grok-4.20-beta",
    ]
    llm_consensus_threshold: float = 0.66
    llm_min_confidence: float = 0.6
    llm_min_agreeing_models: int = 2
    llm_min_risk_reward_ratio: float = 1.2
    llm_min_adx: float = 15.0
    llm_min_atr_ratio: float = 0.003
    llm_tp_min_pct: float = 0.008
    llm_tp_max_pct: float = 0.03
    llm_sl_min_pct: float = 0.004
    llm_sl_max_pct: float = 0.02

    # NOWPayments
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    nowpayments_api_url: str = "https://api.nowpayments.io/v1"
    nowpayments_ipn_callback_url: str = ""
    nowpayments_success_url: str = ""
    nowpayments_cancel_url: str = ""

    # Telegram (deprecated — users now provide their own bot tokens)
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_url: str = ""

    # Email (Zoho ZeptoMail API)
    zeptomail_api_key: str = ""
    zeptomail_api_url: str = "https://api.zeptomail.com/v1.1/email"
    email_from_name: str = "StackAlpha"
    email_from_address: str = "noreply@stackalpha.xyz"
    admin_alert_email: str = ""

    # Subscription
    subscription_monthly_price: float = 50.00
    subscription_yearly_price: float = 500.00
    subscription_grace_period_days: int = 3

    # Binance
    binance_default_leverage: int = 5
    binance_max_leverage: int = 20
    binance_min_volume_usd: float = 50_000_000
    binance_top_movers_limit: int = 15

    # Trading
    default_leverage: int = 5
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

    # Twitter/X Agent (OAuth 1.0a — all 4 keys needed to post tweets)
    twitter_consumer_key: str = ""
    twitter_consumer_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_token_secret: str = ""
    twitter_bearer_token: str = ""
    twitter_enabled: bool = False
    twitter_post_hour: int = 4  # Hour in ET (America/New_York)
    twitter_prompt: str = (
        "You are the social media voice of StackAlpha (@stackalpha_xyz), "
        "an AI-powered algorithmic trading platform. Write a single tweet (max 280 chars) "
        "about AI/algo trading. Mix professional fintech insight, casual crypto twitter energy, "
        "and educational value. Mention StackAlpha naturally. No hashtag spam (1-2 max). "
        "No emojis overload. Be authentic, not salesy."
    )

    # Server
    server_public_ip: str = ""

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
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            # Support comma-separated: "https://a.com,https://b.com"
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
