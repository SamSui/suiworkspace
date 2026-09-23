"""中间件公共工具。

所有中间件均为**纯 ASGI** 实现，而非 Starlette 的 `BaseHTTPMiddleware`——
后者会用 anyio 内存对象包装响应，对 SSE 长连接会引入缓冲与背压问题。
网关是 SSE 透传的主力，这一点必须从一开始就做对。
"""

from __future__ import annotations

import json
from typing import Any

# 免鉴权 / 免限流路径（探针与文档）
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/healthz",
        "/healthz/live",
        "/healthz/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc")


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
