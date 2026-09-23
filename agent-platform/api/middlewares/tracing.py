"""trace_id 中间件：全链路埋点的起点（设计文档 §9.2）。

入口生成（或沿用上游传入的）trace_id，写入 contextvar 供日志自动注入，
并回写到响应头，方便前端/网关把一次请求串起来。
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders

from core.logging import TRACE_ID_HEADER, get_logger, set_trace_id

logger = get_logger(__name__)


class TraceMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope)
        trace_id = incoming.get(TRACE_ID_HEADER) or uuid.uuid4().hex
        set_trace_id(trace_id)
        scope.setdefault("state", {})["trace_id"] = trace_id

        async def send_with_trace(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(TRACE_ID_HEADER, trace_id)
            await send(message)

        await self.app(scope, receive, send_with_trace)
