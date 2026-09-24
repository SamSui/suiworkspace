"""限流验收单测（增量 2.6）：Redis 滑动窗口 + 中间件 429 语义。

用**真实应用** + 把窗口配额压到很小的值，验证：
- 未超配额 → 放行（200）；
- 超配额 → 429 `rate_limited` + `Retry-After` 头。
限流按 user_id 分组；每条用例用唯一用户，避免与其余测试共享计数（DB0）。
MySQL 不可达时整体 skip（应用 lifespan 需要容器）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from api.middlewares import RateLimitMiddleware
from core.config import get_settings
from core.storage import StorageContainer


@pytest.fixture(scope="module")
def mysql_ready() -> bool:
    settings = get_settings()
    container = StorageContainer(settings)
    try:
        import asyncio

        async def _probe() -> bool:
            await container.startup(bootstrap=False, fail_fast=True)
            try:
                async with container.mysql.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return True
            finally:
                await container.shutdown()

        return asyncio.run(_probe())
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def app_client(mysql_ready: bool):
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过限流集成测试")
    from fastapi.testclient import TestClient

    from api.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _token(client, name: str) -> str:
    r = client.post("/v1/users", json={"name": name})
    assert r.status_code == 201, r.text
    api_key = r.json()["api_key"]
    tok = client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


def test_rate_limit_returns_429_when_exceeded(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 压小窗口，让「60s 内 120 次」在测试里 3 次就触顶
    monkeypatch.setattr(RateLimitMiddleware, "WINDOW_SECONDS", 5)
    monkeypatch.setattr(RateLimitMiddleware, "DEFAULT_LIMIT", 3)

    token = _token(app_client, f"ratelimit_{int(time.time())}")
    hdrs = {"Authorization": f"Bearer {token}"}

    # 前 3 次在准入内 -> 200
    for _ in range(3):
        r = app_client.get("/v1/users/me", headers=hdrs)
        assert r.status_code == 200, r.text
    # 第 4 次超限 -> 429
    over = app_client.get("/v1/users/me", headers=hdrs)
    assert over.status_code == 429, over.text
    assert over.json()["error"]["code"] == "rate_limited"
    assert over.headers.get("retry-after") is not None