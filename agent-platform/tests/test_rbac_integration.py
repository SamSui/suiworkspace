"""RBAC 多租户集成测试（SUIG-34 · P2 验收口径）。

覆盖三条验收口径：
  ① 租户隔离：A 租户看不到 B 租户任何对象（跨租户访问 KB → 404）
  ② RBAC 角色权限：无 `kb:read` 权限的角色访问 KB 读取 → 403
  ③ 既有单用户不回归：注册用户即获默认租户 + admin 角色，可正常使用 KB/Agent
     （另以 SQL 断言迁移把既有 user 映射到一人一默认租户）

依赖 live MySQL（与 test_kb_integration 相同基础设施，不可达时整体 skip）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import func, select, text


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
def container(mysql_ready: bool):
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过 RBAC 集成测试")
    from core.config import get_settings
    from core.storage import StorageContainer

    c = StorageContainer(get_settings())
    import asyncio

    asyncio.run(c.startup(bootstrap=False, fail_fast=True))
    yield c
    import asyncio

    asyncio.run(c.shutdown())


@pytest.fixture(scope="module")
def app_client(mysql_ready: bool):
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过 RBAC 集成测试")
    from fastapi.testclient import TestClient

    from api.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _register_and_token(client, name: str) -> tuple[str, int]:
    """注册唯一用户并换取 JWT；返回 (token, user_id)。"""
    r = client.post("/v1/users", json={"name": name})
    assert r.status_code == 201, r.text
    body = r.json()
    api_key = body["api_key"]
    tok = client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"], body["user"]["id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rbac_tenant_isolation(app_client) -> None:
    """验收①：A 租户看不到 B 租户的对象（KB 越权即 404）。"""
    ts = int(time.time())
    tok_a, _ = _register_and_token(app_client, f"rbac_a_{ts}")
    tok_b, _ = _register_and_token(app_client, f"rbac_b_{ts}")

    created = app_client.post("/v1/kb", json={"name": "tenant-a-only"}, headers=_auth(tok_a))
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    # B 访问 A 的 kb -> 404（跨租户，与不存在同语义）
    for method, url in [
        ("get", f"/v1/kb/{kb_id}"),
        ("patch", f"/v1/kb/{kb_id}"),
        ("delete", f"/v1/kb/{kb_id}"),
    ]:
        r = app_client.request(method.upper(), url, headers=_auth(tok_b), json={"name": "x"})
        assert r.status_code == 404, f"{method} 跨租户应为 404: {r.text}"
        assert r.json()["error"]["code"] == "not_found"

    # B 列出的库不含 A 的
    lst_b = app_client.get("/v1/kb", headers=_auth(tok_b))
    assert all(x["id"] != kb_id for x in lst_b.json())

    # A 自己可读
    got = app_client.get(f"/v1/kb/{kb_id}", headers=_auth(tok_a))
    assert got.status_code == 200
    assert got.json()["id"] == kb_id


def test_rbac_role_permission_gate(app_client, container) -> None:
    """②：角色无 `kb:read` 权限 → 读取 KB 403；有权限则放行。"""
    import asyncio

    from db.models import Permission, Role, RolePermission, User

    ts = int(time.time())
    tok, user_id = _register_and_token(app_client, f"rbac_reader_{ts}")

    created = app_client.post("/v1/kb", json={"name": "perm-gate"}, headers=_auth(tok))
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    # 建一个「只读之外」的受限角色：授予 kb:list 但 NOT kb:read
    async def _mk_restricted() -> None:
        async with container.mysql.session() as session:  # type: ignore[attr-defined]
            user = (
                await session.execute(select(User).where(User.id == int(user_id)))
            ).scalar_one()
            restricted = Role(
                tenant_id=user.tenant_id, name="只读_no_read", code=f"no_read_{ts}", status=1
            )
            session.add(restricted)
            await session.flush()
            # 仅挂 kb:list，不挂 kb:read
            perm = (
                await session.execute(
                    select(Permission).where(Permission.code == "kb:list")
                )
            ).scalar_one()
            session.add(RolePermission(role_id=int(restricted.id), permission_id=int(perm.id)))
            user.role_id = int(restricted.id)

    asyncio.run(_mk_restricted())

    # list 仍可（有 kb:list），但 get 因缺 kb:read -> 403
    assert app_client.get("/v1/kb", headers=_auth(tok)).status_code == 200
    r = app_client.get(f"/v1/kb/{kb_id}", headers=_auth(tok))
    assert r.status_code == 403, f"无 kb:read 应 403: {r.text}"
    assert r.json()["error"]["code"] == "permission_denied"


def test_rbac_legacy_default_tenant(app_client, container) -> None:
    """③：既有单用户映射到一人一默认租户，且新注册用户默认可用（不回归）。"""
    import asyncio

    from db.models import Role, Tenant, User, UserRole

    ts = int(time.time())
    # 新注册用户：默认租户 + admin 角色 + 可用 KB 功能
    tok, uid = _register_and_token(app_client, f"rbac_legacy_{ts}")
    r = app_client.post("/v1/kb", json={"name": "legacy-ok"}, headers=_auth(tok))
    assert r.status_code == 201, r.text

    async def _assert_seeded() -> None:
        async with container.mysql.session() as s:  # type: ignore[attr-defined]
            u = (await s.execute(select(User).where(User.id == int(uid)))).scalar_one()
            assert u.tenant_id is not None, "新用户应有默认租户"
            assert u.role_id is not None, "新用户应有默认角色"
            t = (await s.execute(select(Tenant).where(Tenant.id == int(u.tenant_id)))).scalar_one()
            assert t.name is not None
            role = (await s.execute(select(Role).where(Role.id == int(u.role_id)))).scalar_one()
            assert role.code == "admin", "默认角色应为 admin"
            # 用户-角色绑定存在
            n = (
                await s.execute(
                    select(func.count()).select_from(UserRole).where(UserRole.user_id == int(uid))
                )
            ).scalar_one()
            assert n == 1

    asyncio.run(_assert_seeded())


def test_rbac_cross_tenant_agent_isolation(app_client) -> None:
    """①延伸：A 租户的 agent 不受 B 租户影响；B 按 A 的 kb 建 agent -> 404。"""
    ts = int(time.time())
    tok_a, _ = _register_and_token(app_client, f"rbac_ag_a_{ts}")
    tok_b, _ = _register_and_token(app_client, f"rbac_ag_b_{ts}")

    kb = app_client.post("/v1/kb", json={"name": "a-kb"}, headers=_auth(tok_a))
    assert kb.status_code == 201
    kb_id = kb.json()["id"]

    # B 用 A 的 kb 建 agent -> require_kb_access 跨租户 -> 404
    r = app_client.post(
        "/v1/agent",
        json={"kb_id": kb_id, "name": "leak", "graph_type": "rag_graph"},
        headers=_auth(tok_b),
    )
    assert r.status_code == 404, f"跨租户建 agent 应 404: {r.text}"

    # A 自己可建
    ok = app_client.post(
        "/v1/agent",
        json={"kb_id": kb_id, "name": "own", "graph_type": "rag_graph"},
        headers=_auth(tok_a),
    )
    assert ok.status_code == 201