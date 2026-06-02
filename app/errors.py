"""Shared API error responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.context import trace_id_var

logger = logging.getLogger("store_intelligence")


def error_body(
    error: str,
    message: str,
    *,
    status_code: int,
    details: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": error,
        "message": message,
        "trace_id": trace_id_var.get(),
        "status_code": status_code,
    }
    if details is not None:
        body["details"] = details
    return body


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(
            "http_error",
            str(exc.detail),
            status_code=exc.status_code,
        ),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(
            "validation_error",
            "Request validation failed",
            status_code=422,
            details=exc.errors(),
        ),
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.exception(
        "Unhandled exception on %s",
        request.url.path,
        extra={"trace_id": trace_id_var.get()},
    )
    return JSONResponse(
        status_code=503,
        content=error_body(
            "service_unavailable",
            "An internal error occurred",
            status_code=503,
        ),
    )
