"""Tool 网关统一异常（P1.3 · SUIG-33）。

复用 LLM 网关的 `GatewayError` 族，语义一致；工具侧新增拒绝/不存在分支。
"""

from __future__ import annotations

from llm_gateway.errors import (
    GatewayAuthError,
    GatewayError,
    QuotaExceeded,
    UpstreamCallError,
)

__all__ = ["GatewayAuthError", "GatewayError", "QuotaExceeded", "UpstreamCallError"]


class ToolUnavailable(GatewayError):
    """工具不存在。"""

    status_code = 404
    code = "tool_unavailable"


class ToolRejected(GatewayError):
    """工具调用被准入拒绝（未授权 / 参数非法 / 默认禁 / 隔离不符）。"""

    status_code = 403
    code = "tool_rejected"


class GatewayNotExist(GatewayError):
    """网关未就绪。"""

    status_code = 503
    code = "gateway_unavailable"