import time
import uuid
import logging
from contextvars import ContextVar
from typing import Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.utils.exceptions import AppException

logger = logging.getLogger("learnmesh.middleware")

# Context variable to hold the correlation ID per request context
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

def get_request_id() -> str:
    """Retrieve current request correlation ID from context."""
    val = _request_id_ctx.get()
    return val if val else "req-system"

class RequestContextMiddleware:
    """
    Middleware attaching correlation ID, timing headers, and request context.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        # 1. Extract or generate Request ID
        req_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or f"req-{uuid.uuid4().hex[:12]}"
        )
        token = _request_id_ctx.set(req_id)
        scope["state"] = scope.get("state", {})
        scope["state"]["request_id"] = req_id

        start_time = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", req_id.encode("utf-8")))
                headers.append((b"x-response-time-ms", str(duration_ms).encode("utf-8")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_id_ctx.reset(token)

def register_exception_handlers(app: FastAPI):
    """Register global exception handlers ensuring consistent error envelope across all endpoints."""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        req_id = getattr(request.state, "request_id", get_request_id())
        logger.warning(f"[{req_id}] AppException ({exc.code}): {exc.message} - {exc.details}")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": req_id
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        req_id = getattr(request.state, "request_id", get_request_id())
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(p) for p in err.get("loc", []))
            errors.append({
                "field": loc,
                "message": err.get("msg", ""),
                "type": err.get("type", "")
            })
        logger.info(f"[{req_id}] Validation error: {errors}")
        return JSONResponse(
            status_code=422,
            content={
                "code": "VALIDATION_ERROR",
                "message": "Request payload failed schema validation.",
                "details": {"validation_errors": errors},
                "request_id": req_id
            }
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        req_id = getattr(request.state, "request_id", get_request_id())
        # If detail is already a dict, keep it as details
        details = {}
        message = str(exc.detail)
        if isinstance(exc.detail, dict):
            details = exc.detail
            message = details.get("message", "HTTP error")

        code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            429: "TOO_MANY_REQUESTS",
            500: "INTERNAL_SERVER_ERROR",
            503: "SERVICE_UNAVAILABLE"
        }
        code = code_map.get(exc.status_code, "HTTP_ERROR")

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code,
                "message": message,
                "details": details,
                "request_id": req_id
            }
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", get_request_id())
        logger.exception(f"[{req_id}] Unhandled server exception: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected server error occurred. Please contact system support.",
                "details": {"error_type": type(exc).__name__},
                "request_id": req_id
            }
        )
