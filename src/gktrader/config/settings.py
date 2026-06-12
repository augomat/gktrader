from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GKTRADER_", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+pysqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-3.1-flash-lite"
    openrouter_fallback_model: str = "google/gemini-2.5-flash-lite"
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
        description="Base URL of the CM4 gkfetch service (e.g. http://100.88.46.68:8899). Empty disables remote-browser Playwright tier.",
    )
    gkfetch_secret: str = Field(
        default="",
        description="Shared secret for the CM4 gkfetch service (X-Secret header).",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
