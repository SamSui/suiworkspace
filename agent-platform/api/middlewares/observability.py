"""网关可观测中间件（增量 5.3 / 5.4）。

在既有 `TraceMiddleware`（负责 x-request-id == trace_id 的贯穿）之外，为每个 HTTP
请求：开启一个根 span、计入请求量/进行中数、按结束状态记错误率。它对
`TraceMiddleware` 是正交的——前者写 `trace_id`（日志），这里记 trace（耗时）与
metrics（计数）。

中间件层级（Starlette 由内向外追加，后加者在外层）：
    Observability → Trace → Auth → RateLimit → routes
"""

from __future__ import annotations

from typing import Any

from core.logging import get_logger
from observability import otel
from observability.prom import metrics

logger = get_logger(__name__)


class ObservabilityMiddleware:
    """为请求开启根 span 并打点请求量 / 在途数 / 错误率。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        route = (path or "").split("?")[0]

        metrics.requests_inflight.inc()
        metrics.request(route)

        with otel.span("http.request", method=scope.get("method", ""), route=route) as root:
            status_holder: dict[str, int] = {"status": 200}

            async def _send(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    status_holder["status"] = message.get("status", 200)
                await send(message)

            try:
                await self.app(scope, receive, _send)
            except Exception:
                metrics.error("gateway")
                raise
            finally:
                metrics.requests_inflight.dec()
                root.attributes["status"] = str(status_holder["status"])
                if int(status_holder["status"]) >= 500:
                    metrics.error("gateway")

                # span 内再记一次根耗时到依赖直方图（"gateway" 目标命名）
                metrics.observe_dependency("gateway", route, root.duration_ms / 1000.0)


def add_metrics_router(app: Any) -> None:
    """把 `GET /metrics` 注册到 FastAPI app 上（Prometheus 文本格式）。用 `add_api_route`
    直接挂单一路由，避免 `include_router` 在部分 Starlette 版本的挂载空白问题。"""
    from fastapi.responses import Response

    async def _metrics() -> Response:
        body, ctype = metrics.generate_latest()
        return Response(content=body, media_type=ctype)

    app.add_api_route("/metrics", _metrics, methods=["GET"], include_in_schema=False)


__all__ = [
    "ObservabilityMiddleware",
    "add_metrics_router",
    "metrics",
]