"""Redis 滑动窗口限流中间件（设计文档 §9.1）。

按 user_id 分组（未认证时退回客户端 IP）；计数与判定在 Redis 内以 Lua 原子完成。
Redis 不可用时**放行**（fail-open）并告警——限流是保护措施，不该成为可用性单点。
"""

from __future__ import annotations

from typing import Any

from starlette.datastructures import Headers

from core.config import Settings
from core.logging import get_logger
from api.middlewares.common import is_public, send_json

logger = get_logger(__name__)


class RateLimitMiddleware:
    # 默认窗口与配额：60 秒 120 次。后续按接口分级（上传/工具调用更严）在增量 2 细化。
    WINDOW_SECONDS = 60
    DEFAULT_LIMIT = 120

    def __init__(self, app: Any, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    @staticmethod
    def _identity(scope: dict[str, Any]) -> str:
        state = scope.get("state") or {}
        if user_id := state.get("user_id"):
            return f"user:{user_id}"
        headers = Headers(scope=scope)
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        client = scope.get("client")
        return f"ip:{client[0] if client else 'unknown'}"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        container = getattr(scope.get("app"), "state", None)
        container = getattr(container, "container", None)
        if container is None or not container.redis.connected:
            await self.app(scope, receive, send)
            return

        identity = self._identity(scope)
        try:
            allowed, current = await container.redis.hit_rate_limit(
                identity, limit=self.DEFAULT_LIMIT, window_seconds=self.WINDOW_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 — fail-open，限流故障不应阻断业务
            logger.warning(
                "rate limit check failed, fail-open",
                extra={"extra_fields": {"error": str(exc)}},
            )
            await self.app(scope, receive, send)
            return

        if not allowed:
            retry_after = str(self.WINDOW_SECONDS)
            await send_json(
                send,
                429,
                {
                    "error": {
                        "code": "rate_limited",
                        "message": f"请求过于频繁（{current}/{self.DEFAULT_LIMIT} per {self.WINDOW_SECONDS}s）",
                    }
                },
                extra_headers=[(b"retry-after", retry_after.encode())],
            )
            return

        await self.app(scope, receive, send)
