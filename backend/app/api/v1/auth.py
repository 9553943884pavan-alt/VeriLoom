"""Registration, opaque session, and CSRF token endpoints."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import SESSION_LIFETIME, cookie_name, csrf_token, secure_cookie
from app.db.repositories.identity import list_active_memberships
from app.db.session import get_session
from app.schemas.auth import LoginRequest, LoginResponse, LogoutRequest, MeResponse, RegisterRequest, RegisterResponse
from app.services import authentication


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_session)) -> dict:
    return authentication.register(db, payload)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    body, token = authentication.login(db, settings, payload)
    response.set_cookie(key=cookie_name(settings), value=token, max_age=int(SESSION_LIFETIME.total_seconds()), secure=secure_cookie(settings), httponly=True, samesite="lax", path="/")
    return body


@router.get("/me", response_model=MeResponse)
def me(request: Request, db: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    token = request.cookies.get(cookie_name(settings))
    user = authentication.current_user(db, settings, token)
    return {"user_id": user["id"], "email": user["email"], "display_name": user["display_name"], "memberships": list_active_memberships(db, user["id"]), "csrf_token": csrf_token(settings, token)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, request: Request, response: Response, db: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> None:
    token = request.cookies.get(cookie_name(settings))
    authentication.current_user(db, settings, token)
    authentication.logout(db, settings, token)
    response.delete_cookie(cookie_name(settings), path="/")
