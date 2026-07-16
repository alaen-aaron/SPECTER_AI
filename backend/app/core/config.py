"""
Application configuration.

Single source of truth for all environment-derived settings. Loaded once
per process via ``get_settings()`` (LRU-cached), so every consumer shares
the same immutable ``Settings`` instance instead of re-reading the
environment on every access.

Env var names intentionally match SRS §12.2 exactly, so the frozen SRS
remains the canonical reference for what each variable means.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(str, Enum):
    """Deployment environment. Controls logging format and debug behavior."""

    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LLMProvider(str, Enum):
    """Which LLM backend the AI Engine talks to (SRS §8.3)."""

    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class Settings(BaseSettings):
    """
    Process-wide configuration, populated from environment variables
    (or a `.env` file in local development).

    This class has no business logic — it is a typed, validated view
    over the environment and nothing else.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- General -----------------------------------------------------
    APP_NAME: str = "SPECTER_AI"
    APP_ENV: AppEnvironment = AppEnvironment.LOCAL
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # --- CORS ----------------------------------------------------------
    CORS_ALLOW_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Database --------------------------------------------------------
    DATABASE_URL: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://specter:specter@postgres:5432/specter"),
    )

    # --- Redis / Celery --------------------------------------------------
    REDIS_URL: RedisDsn = Field(default=RedisDsn("redis://redis:6379/0"))

    # --- Auth (values are read now; auth logic itself lands in a later
    # milestone per the frozen SRS — Milestone 1 does not implement auth) --
    JWT_SECRET: str = Field(default="changeme-in-production")
    JWT_ACCESS_TTL_MIN: int = Field(default=15)

    # --- Object storage (MinIO / S3-compatible) ---------------------------
    OBJECT_STORAGE_ENDPOINT: str = Field(default="http://minio:9000")
    OBJECT_STORAGE_ACCESS_KEY: str = Field(default="specter")
    OBJECT_STORAGE_SECRET_KEY: str = Field(default="specter-secret")
    OBJECT_STORAGE_BUCKET: str = Field(default="specter-evidence")

    # --- AI provider (config only in Milestone 1; no AI logic yet) --------
    LLM_PROVIDER: LLMProvider = Field(default=LLMProvider.OLLAMA)
    LLM_BASE_URL: str = Field(default="http://ollama:11434")
    LLM_MODEL: str = Field(default="qwen2.5:14b")

    # --- Scope Guard (SRS §16.3) -------------------------------------------
    SCOPE_GUARD_STRICT: bool = Field(default=True)

    @property
    def is_local(self) -> bool:
        return self.APP_ENV == AppEnvironment.LOCAL

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == AppEnvironment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the process-wide ``Settings`` singleton.

    Cached so repeated calls (e.g. once per request via ``Depends``)
    don't re-parse the environment. Tests can bypass the cache with
    ``get_settings.cache_clear()`` after monkey-patching ``os.environ``.
    """
    return Settings()
