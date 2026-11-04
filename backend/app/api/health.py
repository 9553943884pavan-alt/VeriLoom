"""Operator liveness and database readiness probes."""

import psycopg
from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.errors import ApiError


router = APIRouter(include_in_schema=False)


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    try:
        with psycopg.connect(
            settings.DATABASE_URL.get_secret_value(),
            connect_timeout=settings.database_connect_timeout_seconds,
        ) as connection:
            connection.execute("SELECT 1").fetchone()
    except psycopg.Error as exc:
        raise ApiError(503, "database_unavailable", "The API is not ready.") from exc
    return {"status": "ready"}
