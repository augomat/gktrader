from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_OPENROUTER_FALLBACK_MODEL = "deepseek/deepseek-v4-flash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GKTRADER_", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+pysqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-3.1-flash-lite"
    openrouter_fallback_model: str = DEFAULT_OPENROUTER_FALLBACK_MODEL
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0
    internal_api_shared_secret: str = "dev-secret"
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    sec_user_agent: str = "GKTrader/0.1 (ops@example.com)"
    http_user_agent: str = "GKTrader/0.1 (+https://localhost)"
    allow_alerts_during_replay: bool = False
    enable_first_start_baseline: bool = True
    source_poll_interval_seconds: int = 60
    alert_cooldown_hours: int = 6
    internal_api_host: str = "127.0.0.1"
    internal_api_port: int = 8000
    europe_timezone: str = "Europe/Vienna"
    health_failure_threshold: int = 3
    health_critical_minutes: int = 10
    telegram_send_base_url: str = Field(
        default="https://api.telegram.org",
        description="Telegram Bot API base URL for outbound sendMessage only.",
    )
    playwright_proxy_url: str = Field(
        default="",
        description="SOCKS5/HTTP proxy for Playwright browser (e.g. socks5://host.docker.internal:1080). Empty disables Playwright fallback.",
    )
    gkfetch_url: str = Field(
        default="",
        description="Legacy default gkfetch base URL. Prefer GKTRADER_GKFETCH_CM4_URL and GKTRADER_GKFETCH_GEORG_LAPTOP_URL.",
    )
    gkfetch_secret: str = Field(
        default="",
        description="Shared secret for the CM4 gkfetch service (X-Secret header).",
    )
    gkfetch_cm4_url: str = Field(
        default="",
        description="CM4 gkfetch base URL. Falls back to GKTRADER_GKFETCH_URL when empty.",
    )
    gkfetch_cm4_secret: str = Field(
        default="",
        description="CM4 gkfetch shared secret. Falls back to GKTRADER_GKFETCH_SECRET when empty.",
    )
    gkfetch_georg_laptop_url: str = Field(
        default="",
        description="Georg Windows laptop gkfetch base URL. Required for Commerce browser fallback.",
    )
    gkfetch_georg_laptop_secret: str = Field(
        default="",
        description="Georg Windows laptop gkfetch shared secret for Commerce browser fallback.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
