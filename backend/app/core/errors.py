"""Stable public API error responses without leaking internal details."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "private, no-store"},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(
            request, exc.status_code, exc.code, exc.message, exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's raw error can include rejected inputs; expose locations only.
        fields = [
            {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
            for error in exc.errors()
        ]
        return _error_response(
            request, 422, "validation_error", "The request is invalid.", {"fields": fields}
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        messages = {
            401: ("unauthenticated", "Sign in to continue."),
            403: ("forbidden", "You do not have access to this action."),
            404: ("not_found", "The requested resource was not found."),
            405: ("method_not_allowed", "This method is not allowed."),
        }
        code, message = messages.get(
            exc.status_code, ("http_error", "The request could not be completed.")
        )
        return _error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Logging belongs to the structured logging layer; do not echo exceptions.
        return _error_response(
            request, 500, "internal_error", "The request could not be completed."
        )
