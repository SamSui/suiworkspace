"""JWT 鉴权中间件。

只做**认证**（你是谁）：校验 token 并把 user_id 放进 scope.state。
**授权**（你能不能看这个 kb）在查询路径由 `api.deps.require_kb_access` 强制——
两者分离，避免"过了中间件就以为安全"（裁决 #4）。
"""

from __future__ import annotations

from typing import Any

import jwt
from starlette.datastructures import Headers

from core.config import Settings
from core.logging import get_logger
from api.middlewares.common import is_public, send_json

logger = get_logger(__name__)


class AuthMiddleware:
    def __init__(self, app: Any, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    def _decode(self, token: str) -> dict[str, Any] | None:
        try:
            return jwt.decode(
                token,
                self.settings.app.jwt_secret,
                algorithms=[self.settings.app.jwt_algorithm],
            )
        except jwt.PyJWTError as exc:
            logger.info("jwt rejected", extra={"extra_fields": {"reason": str(exc)}})
            return None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope["type"] == "http" and is_public(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")

        if scheme.lower() != "bearer" or not token:
            await send_json(
                send,
                401,
                {"error": {"code": "unauthenticated", "message": "缺少 Bearer token"}},
            )
            return

        claims = self._decode(token)
        if claims is None:
            await send_json(
                send,
                401,
                {"error": {"code": "unauthenticated", "message": "token 无效或已过期"}},
            )
            return

        state = scope.setdefault("state", {})
        state["user_id"] = claims.get("sub")
        state["claims"] = claims
        await self.app(scope, receive, send)
