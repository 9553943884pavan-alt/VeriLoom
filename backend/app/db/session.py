"""Small synchronous PostgreSQL pool; transactions belong to caller services."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def sqlalchemy_url(url: str) -> str:
    """Accept Render's usual postgres/postgresql URLs with psycopg 3."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgresql+psycopg://"):
        return url
    raise ValueError("DATABASE_URL must be a PostgreSQL URL")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        sqlalchemy_url(settings.DATABASE_URL.get_secret_value()),
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def get_session() -> Iterator[Session]:
    """Yield a session without committing implicit writes."""
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with factory() as session:
        yield session
