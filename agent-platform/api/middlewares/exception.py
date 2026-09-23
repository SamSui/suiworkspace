"""全局异常映射：把 AppError 层次翻译成稳定的 HTTP 响应。

响应体统一为 `{"error": {"code", "message", "detail?}}`——
`code` 稳定可编程，`message` 面向人，内部细节不外泄。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.exceptions import AppError
from core.logging import get_logger

logger = get_logger(__name__)


def _payload(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return body


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error(
            "app error",
            extra={"extra_fields": {"code": exc.code, "path": request.url.path}},
            exc_info=exc,
        )
    else:
        logger.info(
            "app error",
            extra={"extra_fields": {"code": exc.code, "path": request.url.path}},
        )
    return JSONResponse(status_code=exc.status_code, content=_payload(exc.code, exc.message, exc.detail))


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_payload("invalid_argument", "请求参数校验失败", exc.errors()),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error",
        extra={"extra_fields": {"path": request.url.path}},
        exc_info=exc,
    )
    # 不把异常细节回给调用方
    return JSONResponse(status_code=500, content=_payload("internal_error", "服务内部错误"))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
