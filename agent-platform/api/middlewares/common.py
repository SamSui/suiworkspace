"""中间件公共工具。

所有中间件均为**纯 ASGI** 实现，而非 Starlette 的 `BaseHTTPMiddleware`——
后者会用 anyio 内存对象包装响应，对 SSE 长连接会引入缓冲与背压问题。
网关是 SSE 透传的主力，这一点必须从一开始就做对。
"""

from __future__ import annotations

import json
from typing import Any

# 免鉴权 / 免限流路径（探针、文档、认证引导）。值为对应 HTTP method 集合；None 表示任意 method。
PUBLIC_PATHS: dict[str, frozenset[str] | None] = {
    "/healthz": None,
    "/healthz/live": None,
    "/healthz/ready": None,
    "/docs": None,
    "/redoc": None,
    "/openapi.json": None,
    # 认证引导（增量 2.1）：登录换取 JWT、公开注册首个用户——仅对应 method 免鉴权
    "/v1/auth/token": frozenset({"POST"}),
    "/v1/users": frozenset({"POST"}),
}

# 带前缀的文档路径也免鉴权
_DOC_PREFIXES = ("/docs", "/redoc")


def is_public(path: str, method: str = "GET") -> bool:
    """判断该请求是否免鉴权 / 免限流。

    - 命中 `PUBLIC_PATHS`：值为 None（任意 method）或方法在值集合内 → 公开。
    - `/docs*` / `/redoc*` 前缀兜底。
    - 未命中 → 需认证 / 限流。
    """
    if any(path.startswith(p) for p in _DOC_PREFIXES):
        return True
    if path not in PUBLIC_PATHS:
        return False
    allowed = PUBLIC_PATHS[path]
    return allowed is None or method.upper() in allowed


async def send_json(
    send: Any,
    status: int,
    payload: dict[str, Any],
    *,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    """直接向 ASGI 通道写一个 JSON 响应。

    中间件自己产出错误响应（401/429）而非抛异常——中间件在异常处理器外层，
    抛出去不会被 FastAPI 的 handler 接住。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
