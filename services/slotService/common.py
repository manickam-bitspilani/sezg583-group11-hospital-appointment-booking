"""
Cross-cutting concerns: JSON logging, correlation IDs and the uniform error
format. Every service carries its own identical copy of this file so that each
one stays independently buildable and deployable (no shared library).
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException

CORRELATION_HEADER = "X-Correlation-ID"

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")

_DEFAULT_ERROR_CODES = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}

# Attributes present on every LogRecord; anything else came from `extra=`.
_STANDARD_LOG_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


# -------------------- ERROR MODEL --------------------

class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# Documented on every route of every service's OpenAPI spec.
ERROR_RESPONSES = {
    status_code: {"model": ErrorResponse}
    for status_code in (404, 409, 422, 500, 503)
}


class ApiError(Exception):
    """Raise from anywhere in a service to return the uniform error body."""

    def __init__(self, status_code: int, code: str, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_body(code: str, message: str, details: list[dict] | None = None) -> dict:
    error = {"code": code, "message": message, "correlation_id": correlation_id_var.get()}
    if details:
        error["details"] = details
    return {"error": error}


def error_response(status_code: int, code: str, message: str, details: list[dict] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message, details))


# -------------------- HEALTH --------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    database: str


HEALTH_RESPONSES = {503: {"model": HealthResponse, "description": "The service's database is unreachable"}}


def health_response(service: str, engine: Engine) -> JSONResponse:
    """
    GET /health: healthy only if this service's own database answers. A
    service that cannot reach its database cannot serve requests, so it
    reports 503 and a load balancer / readiness probe stops routing to it.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:
        logging.getLogger("health").warning(
            "Health check failed: database unreachable",
            extra={"error": type(error).__name__},
        )
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "service": service, "database": "down"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "healthy", "service": service, "database": "up"},
    )


# -------------------- LOGGING --------------------

class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "correlation_id": correlation_id_var.get(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update(
            {key: value for key, value in vars(record).items() if key not in _STANDARD_LOG_ATTRS}
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Route uvicorn's own logs through the JSON handler; the access log is
    # replaced by the request log in the middleware below (it has the
    # correlation ID, uvicorn's does not).
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


# -------------------- APP WIRING --------------------

def install_common_handlers(app: FastAPI) -> None:
    logger = logging.getLogger("http")

    @app.middleware("http")
    async def correlation_and_request_log(request: Request, call_next):
        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("Unhandled error", extra={"path": request.url.path})
                response = error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")

            response.headers[CORRELATION_HEADER] = correlation_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            return response
        finally:
            correlation_id_var.reset(token)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = _DEFAULT_ERROR_CODES.get(exc.status_code, "ERROR")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details)
