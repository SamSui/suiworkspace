"""通用响应模型：错误体与健康体。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(description="稳定的机器可读错误码")
    message: str = Field(description="面向人的错误说明")
    detail: Any | None = Field(default=None, description="结构化补充信息（可选）")


class ErrorResponse(BaseModel):
    """全站统一错误响应体。"""

    error: ErrorBody


class StoreHealth(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    detail: str | None = None
    extra: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    healthy: bool
    checked: int
    failed: list[str] = Field(default_factory=list)
    stores: list[StoreHealth] = Field(default_factory=list)
