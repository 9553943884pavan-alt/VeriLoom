"""Identity creation and session lifecycle."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import SESSION_LIFETIME, hash_password, new_session_token, session_token_hash, verify_password
from app.db.repositories import identity
from app.schemas.auth import LoginRequest, RegisterRequest


def register(db: Session, payload: RegisterRequest) -> dict[str, Any]:
    try:
        user = identity.insert_user(db, email=payload.email, display_name=payload.display_name, password_hash=hash_password(payload.password))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "email_already_registered", "This email is already registered.") from exc
    return {"user_id": user["id"], "email": user["email"], "display_name": user["display_name"]}


def login(db: Session, settings: Settings, payload: LoginRequest) -> tuple[dict[str, Any], str]:
    user = identity.find_user_by_email(db, payload.email)
    if user is None or user["disabled_at"] is not None or not verify_password(user["password_hash"], payload.password):
        raise ApiError(401, "invalid_credentials", "The email or password is incorrect.")
    token = new_session_token()
    identity.insert_session(db, user_id=user["id"], token_hash=session_token_hash(settings, token), expires_at=datetime.now(timezone.utc) + SESSION_LIFETIME)
    memberships = identity.list_active_memberships(db, user["id"])
    db.commit()
    return {"user_id": user["id"], "memberships": memberships}, token


def current_user(db: Session, settings: Settings, token: str | None) -> dict[str, Any]:
    if not token:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    user = identity.find_session_user(db, session_token_hash(settings, token))
    if user is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    return user


def logout(db: Session, settings: Settings, token: str) -> None:
    identity.revoke_session(db, session_token_hash(settings, token))
    db.commit()
