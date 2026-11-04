"""Typed, server-side settings shared by the API and worker."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Read deployment environment first, then the local ignored ``.env``."""

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    DATABASE_URL: SecretStr
    SESSION_SECRET: SecretStr
    APP_ORIGIN: str = "http://localhost:5173"

    R2_ACCOUNT_ID: str = ""
    R2_BUCKET: str = ""
    R2_ACCESS_KEY_ID: SecretStr = SecretStr("")
    R2_SECRET_ACCESS_KEY: SecretStr = SecretStr("")

    SARVAM_API_KEY: SecretStr = SecretStr("")
    SARVAM_MODEL: str = "sarvam-105b"
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    RERANKER_ENABLED: bool = False
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"

    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=10)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
