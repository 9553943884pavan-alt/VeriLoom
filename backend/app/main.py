"""FastAPI entry point for the VeriLoom backend."""

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.drafts import router as drafts_router
from app.api.v1.evidence import router as evidence_router
from app.api.v1.invitations import router as invitations_router
from app.api.v1.organisations import router as organisations_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.sources import router as sources_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.security import AUTH_RATE_LIMITER, cookie_name, csrf_matches


def _boundary_error(request_id: str, status_code: int, code: str, message: str, *, retry_after: int | None = None) -> JSONResponse:
    headers = {"X-Request-ID": request_id, "Cache-Control": "private, no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, "details": {}, "request_id": request_id}}, headers=headers)


def create_app() -> FastAPI:
    app = FastAPI(title="VeriLoom API", version="1.0.0")
    register_error_handlers(app)

    @app.middleware("http")
    async def add_response_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = uuid4().hex
        request.state.request_id = request_id
        path = request.url.path
        if path.startswith("/api/v1/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            settings = get_settings()
            if request.headers.get("origin") != settings.APP_ORIGIN:
                return _boundary_error(request_id, 403, "invalid_origin", "This request origin is not allowed.")
            if path in {"/api/v1/auth/register", "/api/v1/auth/login"}:
                client_host = request.client.host if request.client else "unknown"
                wait = AUTH_RATE_LIMITER.check(f"{path}:{client_host}", limit=10, window_seconds=60)
                if wait is not None:
                    return _boundary_error(request_id, 429, "rate_limited", "Too many attempts. Try again later.", retry_after=wait)
            else:
                token = request.cookies.get(cookie_name(settings))
                if not token:
                    return _boundary_error(request_id, 401, "unauthenticated", "Sign in to continue.")
                if not csrf_matches(settings, token, request.headers.get("X-CSRF-Token")):
                    return _boundary_error(request_id, 403, "invalid_csrf", "Refresh your session and try again.")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "private, no-store"
        return response

    app.include_router(health_router)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(organisations_router, prefix="/api/v1")
    app.include_router(invitations_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    app.include_router(drafts_router, prefix="/api/v1")
    app.include_router(evidence_router, prefix="/api/v1")
    app.include_router(reviews_router, prefix="/api/v1")
    return app


app = create_app()
