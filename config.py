# =============================================================================
# config.py - All settings loaded from environment variables (.env)
# =============================================================================

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Server
    # -------------------------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    CORS_ORIGINS: List[str] = ["*"]   # tighten in production

    # -------------------------------------------------------------------------
    # Admin
    # -------------------------------------------------------------------------
    ADMIN_SECRET: str = "change-me-in-production"

    # -------------------------------------------------------------------------
    # Ollama
    # -------------------------------------------------------------------------
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_DEFAULT_MODEL: str = "qwen2.5:7b"
    OLLAMA_TIMEOUT: int = 120          # seconds

    # -------------------------------------------------------------------------
    # Supabase  (free tier — https://supabase.com)
    # -------------------------------------------------------------------------
    SUPABASE_URL: str = ""             # e.g. https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY: str = ""     # service_role key (keep secret)

    # -------------------------------------------------------------------------
    # Rate-limiting defaults (per-key overrides stored in DB)
    # -------------------------------------------------------------------------
    DEFAULT_RATE_LIMIT_PER_MIN: int = 20
    DEFAULT_MONTHLY_TOKEN_LIMIT: int = 1_000_000   # 1M tokens/month free

    # -------------------------------------------------------------------------
    # API key prefix
    # -------------------------------------------------------------------------
    API_KEY_PREFIX: str = "llm"


# Singleton
settings = Settings()
