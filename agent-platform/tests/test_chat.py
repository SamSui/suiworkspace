"""增量 2.3 对话接口（SSE）测试。

覆盖验收口径：
1. 非流式 `POST /v1/chat`：正常返回会话/消息 id；无 token → 401；
2. 流式 `POST /v1/chat/stream`：SSE **逐事件透传不缓冲**，事件字段严格符合冻结契约，
   无 token → 401；
3. `POST /v1/chat/resume`：以 thread_id 续接；无该会话/无权 → 404。

下游编排以 `tests/sse_stub.py` 的 SSE stub（符合冻结契约）替代，通过
`api.upstream.set_client_factory` 注入 `httpx.ASGITransport`，全程不依赖真实网络 /
增量 3 的编排节点。凡需 MySQL 承载「用户落库 / 会话归属」校验的断言，在库不可用时 skip
（与 `test_kb_integration` / `test_auth_integration` 同口径）。
"""

from __future__ import annotations

import os
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sse_stub import SSEStubApp, _error_event, stream_all

from api import upstream

_SECRET = os.environ.get("APP_JWT_SECRET", "unit-test-secret-0123456789abcdef-0123456789")
_ALGO = "HS256"


def _make_token(user_id: int = 909, ttl: int = 600) -> str:
    """直接签发测试 JWT（同 env 密钥/算法），供「不落库」的流式断言使用。"""
    now = int(time.time())
    return jwt.encode(
        {"sub": str(user_id), "exp": now + ttl, "iat": now}, _SECRET, algorithm=_ALGO
    )


@pytest.fixture()
def stub() -> SSEStubApp:
    from sse_stub import app

    return app


@pytest.fixture()
def gateway(stub: SSEStubApp):  # noqa: ANN001
    """网关真实 app（TestClient 会执行 lifespan）+ 指向内存编排 stub 的上游。"""
    from api.main import create_app

    stub_upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub), base_url="http://langgraph-stub"
    )
    upstream.set_client_factory(lambda: stub_upstream)

    with TestClient(create_app()) as client:
        yield client
    # 夹具用完还原默认下游，避免泄漏到其他测试
    upstream.set_client_factory(None)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- 401：无 token / 无效 token（不依赖 DB）----------


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/v1/chat", {"query": "hi"}),
        ("/v1/chat/stream", {"query": "hi"}),
        ("/v1/chat/resume", {"thread_id": "t", "value": "ok"}),
    ],
)
def test_chat_requires_auth(gateway, path: str, payload: dict) -> None:  # noqa: ANN001
    r = gateway.post(path, json=payload)
    assert r.status_code == 401, r.text


def test_chat_requires_valid_auth(gateway) -> None:  # noqa: ANN001
    r = gateway.post(
        "/v1/chat", json={"query": "hi"}, headers={"Authorization": "Bearer invalid-token"}
    )
    assert r.status_code == 401, r.text


# ---------- 流式透传：逐事件、不缓冲、契约符合（不依赖 DB）----------


def test_stream_passthrough_no_buffering(gateway) -> None:  # noqa: ANN001
    tok = _make_token()
    expected = stream_all("你好", message_id=321)

    with gateway.stream(
        "POST", "/v1/chat/stream", json={"query": "你好"}, headers=_auth(tok)
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        got = "".join(piece.decode("utf-8") for piece in resp.iter_bytes())

    # 逐事件原样透传：不重组、不丢事件、不改字段
    assert got == expected
    # 事件骨架符合冻结契约（event/seq/data）
    assert "event: token" in got
    assert "event: done" in got
    assert ": ping\n" in got  # 心跳保活注释行一并透传


def test_stream_error_event_relayed_verbatim(gateway) -> None:  # noqa: ANN001
    """编排 output 错误事件时，网关仍原样透传（code / trace_id 留在事件 body 内）。"""
    tok = _make_token()
    with gateway.stream(
        "POST",
        "/v1/chat/stream",
        json={"query": "__error__"},
        headers=_auth(tok),
    ) as resp:
        assert resp.status_code == 200
        body = "".join(piece.decode("utf-8") for piece in resp.iter_bytes())
    assert body == _error_event()
    # error 事件 data 携带机器可读 code + trace_id（契约第 2 条）
    assert "llm_timeout" in body
    assert "trace-stub-1" in body


# ---------- 非流式收敛 / resume：依赖 MySQL（无库则 skip）----------


@pytest.fixture(scope="module")
def mysql_ready() -> bool:
    import asyncio

    from sqlalchemy import text

    from core.config import get_settings
    from core.storage import StorageContainer

    settings = get_settings()
    container = StorageContainer(settings)

    async def _probe() -> bool:
        try:
            await container.startup(bootstrap=False, fail_fast=True)
            async with container.mysql.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await container.shutdown()

    return asyncio.run(_probe())


def _register_user(gateway, name: str) -> tuple[int, str]:  # noqa: ANN001
    """创建唯一用户并换取 JWT，返回 (user_id, token)。"""
    created = gateway.post("/v1/users", json={"name": name})
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    api_key = created.json()["api_key"]
    tok = gateway.post(
        "/v1/auth/token", json={"name": user["name"], "api_key": api_key}
    )
    assert tok.status_code == 200, tok.text
    return user["id"], tok.json()["access_token"]


def test_chat_once_done_fields(gateway, stub: SSEStubApp, mysql_ready: bool) -> None:  # noqa: ANN001
    """非流式 `/v1/chat`：thread/message id + usage 与 done 事件对齐（需 DB 鉴定真实用户）。"""
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过非流式收敛断言")
    stub.hits.clear()
    user_id, tok = _register_user(gateway, f"chat_u_{int(time.time())}")
    r = gateway.post("/v1/chat", json={"query": "你好"}, headers=_auth(tok))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["message_id"] == 321
    assert data["usage"] == {"prompt": 11, "completion": 7}
    assert isinstance(data["thread_id"], str) and data["thread_id"]
    # 下游 stub 收到带 user_id 的透传载荷
    sent = stub.hits[-1]
    assert sent["user_id"] == user_id


def test_chat_once_error_maps_502(gateway, mysql_ready: bool) -> None:  # noqa: ANN001
    """非流式 `/v1/chat` 遇编排 error 事件 → 502，code + trace_id 落 body（契约第 2 条）。"""
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过非流式 error 收敛断言")
    _uid, tok = _register_user(gateway, f"chat_err_{int(time.time())}")
    r = gateway.post("/v1/chat", json={"query": "__error__"}, headers=_auth(tok))
    assert r.status_code == 502, r.text
    body = r.json()["error"]
    assert body["code"] == "upstream_error"
    # 机器可读 code + trace_id 都在响应 body 内（契约第 2 条），而非只在头
    assert body["detail"]["code"] == "llm_timeout"
    assert body["detail"]["trace_id"] == "trace-stub-1"


def test_resume_404_without_owned_thread(gateway, mysql_ready: bool) -> None:  # noqa: ANN001
    """无该会话/无权 → 404（thread 归属校验须验 DB，无库则 skip）。"""
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过 resume 归属校验断言")
    _uid, tok = _register_user(gateway, f"chat_res_{int(time.time())}")
    r = gateway.post(
        "/v1/chat/resume",
        json={"thread_id": "no-such-thread", "value": "ok"},
        headers=_auth(tok),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"