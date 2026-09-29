"""LLM / Tool 网关统一异常（P1.3 · SUIG-33）。

网关作为独立服务，把配额拒绝 / 鉴权失败 / 白名单拒等语义表达为稳定的异常类型，
由各自 main.py 的异常处理器映射成 HTTP 状态码。
"""

from __future__ import annotations


class GatewayError(Exception):
    """网关业务异常基类。`status_code` 表达 HTTP 语义，`code` 为稳定机器标识。"""

    status_code: int = 500
    code: str = "gateway_error"

    def __init__(
        self,
        message: str = "",
        *,
        detail: object | None = None,
        retry_after: float = 0.0,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail
        self.retry_after = retry_after


class GatewayAuthError(GatewayError):
    """未认证：缺失 / 非法调用方凭证。"""

    status_code = 401
    code = "gateway_unauthenticated"


class QuotaExceeded(GatewayError):
    """触达配额上限。携带 `retry_after` 供调用方退避。"""

    status_code = 429
    code = "quota_exceeded"


class ToolUnavailable(GatewayError):
    """工具不存在或未授权调用。"""

    status_code = 404
    code = "tool_unavailable"


class ToolRejected(GatewayError):
    """工具调用被准入拒绝（未授权 / 参数非法 / 默认禁）。"""

    status_code = 403
    code = "tool_rejected"


class UpstreamCallError(GatewayError):
    """下游（LLM / 工具端点）调用失败。"""

    status_code = 502
    code = "upstream_error"


__all__ = [
    "GatewayError",
    "GatewayAuthError",
    "QuotaExceeded",
    "ToolRejected",
    "ToolUnavailable",
    "UpstreamCallError",
]