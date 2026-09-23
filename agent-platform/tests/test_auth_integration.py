"""用户/鉴权端到端集成测试（需 live MySQL + FastAPI 全栈 up）。

走真实 `create_app()`（lifespan 会连四类存储），roundtrip：
注册 → 拿 api_key → 换 JWT → 访问 /v1/users/me → 轮换 → 旧 key 失效。
MySQL 不可达时整体 skip（单元验收口径在 test_auth / test_security 已覆盖）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

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
        pytest.skip("MySQL 不可用，跳过端到端鉴权集成测试")
    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


def test_full_auth_roundtrip(app_client) -> None:
    import time

    name = f"it_{int(time.time())}"  # 每次唯一，避免跨次运行的重复名冲突
    r = app_client.post("/v1/users", json={"name": name})
    assert r.status_code == 201, r.text
    api_key = r.json()["api_key"]

    tok_resp = app_client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    assert tok_resp.status_code == 200, tok_resp.text
    token = tok_resp.json()["access_token"]
    assert tok_resp.json()["token_type"] == "bearer"

    me = app_client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["name"] == name

    # 无 token -> 401（跟中间件口径一致）
    assert app_client.get("/v1/users/me").status_code == 401

    # 轮换：新 key 生效、旧 key 立即失效
    rot = app_client.post(
        "/v1/users/me/api-key/rotate", headers={"Authorization": f"Bearer {token}"}
    )
    assert rot.status_code == 200
    new_key = rot.json()["api_key"]
    assert new_key and new_key != api_key

    # 旧 key 换 token 应 401
    old_swap = app_client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    assert old_swap.status_code == 401

    # 新 key 换 token 应 200
    new_swap = app_client.post("/v1/auth/token", json={"name": name, "api_key": new_key})
    assert new_swap.status_code == 200