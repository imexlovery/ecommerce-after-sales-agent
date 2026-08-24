"""Safe, uniform API error translation."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from after_sales_agent.application.service import ApplicationError
from after_sales_agent.storage.repositories import (
    ConcurrentMutationError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    StorageNotFoundError,
)


def new_trace_id() -> str:
    return f"trc_{uuid4().hex}"


@dataclass(slots=True)
class ApiSurfaceError(RuntimeError):
    code: str
    message: str
    status_code: int
    retryable: bool = False
    trace_id: str = field(default_factory=new_trace_id)


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    trace_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "trace_id": trace_id or new_trace_id(),
            }
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error_handler(_: Request, exc: ApplicationError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            trace_id=exc.trace_id,
        )

    @app.exception_handler(ApiSurfaceError)
    async def surface_error_handler(_: Request, exc: ApiSurfaceError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            trace_id=exc.trace_id,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return error_response(
            status_code=422,
            code="REQUEST_VALIDATION_FAILED",
            message="请求格式不符合接口要求。",
        )

    @app.exception_handler(StorageNotFoundError)
    async def storage_not_found_handler(_: Request, __: StorageNotFoundError) -> JSONResponse:
        return error_response(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="找不到请求的虚拟资源。",
        )

    async def storage_conflict_handler(_: Request, __: Exception) -> JSONResponse:
        return error_response(
            status_code=409,
            code="STATE_CONFLICT",
            message="资源状态已经变化，请刷新后重试。",
        )

    for exception_type in (
        ConcurrentMutationError,
        InvalidStateTransitionError,
        IdempotencyConflictError,
    ):
        app.add_exception_handler(exception_type, storage_conflict_handler)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "请求无法完成。"
        return error_response(
            status_code=exc.status_code,
            code=f"HTTP_{exc.status_code}",
            message=message,
            retryable=exc.status_code >= 500,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
        return error_response(
            status_code=500,
            code="INTERNAL_ERROR",
            message="服务暂时无法完成请求。",
            retryable=True,
        )
