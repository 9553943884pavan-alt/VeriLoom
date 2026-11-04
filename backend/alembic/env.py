"""Alembic entry point. Run from backend/; never auto-migrate on app startup."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for Alembic")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


if context.is_offline_mode():
    context.configure(
        url=database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
