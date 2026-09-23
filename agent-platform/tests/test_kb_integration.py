"""知识库 CRUD + 权限端到端集成测试（需 live MySQL + FastAPI 全栈 up）。

覆盖增量 2.2 验收口径：**越权访问他人 kb → 404（不泄漏存在性）**，以及 owner 自身的
list / create / get / update / delete 全生命周期。
MySQL 不可达时整体 skip。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text


@pytest.fixture(scope="module")
def mysql_ready() -> bool:
    import asyncio

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


@pytest.fixture(scope="module")
def app_client(mysql_ready: bool):
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过知识库权限集成测试")
    from fastapi.testclient import TestClient

    from api.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _register_and_token(client, name: str) -> str:
    """注册唯一用户并换取 JWT。"""
    r = client.post("/v1/users", json={"name": name})
    assert r.status_code == 201, r.text
    api_key = r.json()["api_key"]
    tok = client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_kb_permission_matrix(app_client) -> None:
    ts = int(time.time())
    token_a = _register_and_token(app_client, f"kb_owner_{ts}")
    token_b = _register_and_token(app_client, f"kb_intruder_{ts}")

    # A 创建知识库
    created = app_client.post("/v1/kb", json={"name": "shared-ish"}, headers=_auth(token_a))
    assert created.status_code == 201, created.text
    kb = created.json()
    kb_id = kb["id"]

    # 越权访问他人 kb -> 404（与不存在同语义，不泄漏存在性）
    for method, url in [
        ("get", f"/v1/kb/{kb_id}"),
        ("patch", f"/v1/kb/{kb_id}"),
        ("delete", f"/v1/kb/{kb_id}"),
    ]:
        r = app_client.request(method.upper(), url, headers=_auth(token_b), json={"name": "hijack"})
        assert r.status_code == 404, f"{method} 越权应为 404: {r.text}"
        # 统一错误体
        assert r.json()["error"]["code"] == "not_found"

    # owner 自己可读
    got = app_client.get(f"/v1/kb/{kb_id}", headers=_auth(token_a))
    assert got.status_code == 200
    assert got.json()["name"] == "shared-ish"
    assert got.json()["owner_id"] == kb["owner_id"]

    # owner 可改
    upd = app_client.patch(f"/v1/kb/{kb_id}", json={"name": "renamed"}, headers=_auth(token_a))
    assert upd.status_code == 200
    assert upd.json()["name"] == "renamed"

    # owner 列出的库只含自己的
    lst_a = app_client.get("/v1/kb", headers=_auth(token_a))
    assert lst_a.status_code == 200
    assert all(x["owner_id"] == kb["owner_id"] for x in lst_a.json())
    assert any(x["id"] == kb_id for x in lst_a.json())

    # B 列出的库不含 A 的
    lst_b = app_client.get("/v1/kb", headers=_auth(token_b))
    assert all(x["id"] != kb_id for x in lst_b.json())

    # 访问不存在的 kb -> 404（与越权同语义）
    miss = app_client.get("/v1/kb/999999", headers=_auth(token_a))
    assert miss.status_code == 404

    # owner 软删 -> 之后对任何路径（含 owner）都不可见
    de = app_client.delete(f"/v1/kb/{kb_id}", headers=_auth(token_a))
    assert de.status_code == 204
    after = app_client.get(f"/v1/kb/{kb_id}", headers=_auth(token_a))
    assert after.status_code == 404
    lst_after = app_client.get("/v1/kb", headers=_auth(token_a))
    assert all(x["id"] != kb_id for x in lst_after.json())

    # 无认证访问 /v1/kb -> 401
    assert app_client.get("/v1/kb").status_code == 401


def test_kb_no_token_required(app_client) -> None:
    """公开性检查：/v1/kb 全部受鉴权保护，无 token 一律 401。"""
    assert app_client.get("/v1/kb").status_code == 401
    assert app_client.post("/v1/kb", json={"name": "x"}).status_code == 401