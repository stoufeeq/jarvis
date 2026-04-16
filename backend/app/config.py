from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "Jarvis"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Database
    database_url: str = "postgresql+asyncpg://jarvis:jarvis_dev@localhost:5432/jarvis"
    database_url_sync: str = "postgresql://jarvis:jarvis_dev@localhost:5432/jarvis"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Market Data
    polygon_api_key: str = ""
    alpha_vantage_api_key: str = ""

    # IBKR
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1

    # AI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # News / External
    news_api_key: str = ""
    unusual_whales_api_key: str = ""
    quiver_quant_api_key: str = ""
    finnhub_api_key: str = ""

    # Email (SMTP)
    sendgrid_api_key: str = ""          # kept for backwards compat, unused
    alert_from_email: str = "alerts@jarvis.local"
    smtp_host: str = ""                 # e.g. smtp.gmail.com
    smtp_port: int = 587                # 587=STARTTLS, 465=SSL
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True           # STARTTLS on port 587

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    # Frontend
    frontend_url: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_url]


@lru_cache
def get_settings() -> Settings:
    return Settings()
