"""user 表 CRUD + api_key 哈希轮换 集成测试（增量 2 · 2.1）。

直连本机 MySQL（agent_platform 库，compose 已拉起）验证：
- 创建（明文只返回一次、库中只存哈希、可回读）
- 查询 / 更新 / 停用
- api_key 轮换（旧明文失效、新明文可用、库中仍是哈希）
- 不存在资源 → 404（NotFound）

生成的用户用实例唯一前缀命名，`clean_session` 夹具在用例结束后统一清理，不影响既有数据。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from api.main import app as api_app
from api.services.user_service import user_service
from core.config import AppSettings, get_settings
from core.exceptions import NotFound
from core.security import create_access_token, verify_api_key
from db.models import User

if TYPE_CHECKING:
    from core.storage import MySQLStore

_NAMESPACE = f"t2_{uuid4().hex[:8]}"


def _name(tag: str) -> str:
    return f"{_NAMESPACE}_{tag}_{int(time.time() * 1000) % 100000}"


def _app() -> AppSettings:
    return get_settings().app


@pytest_asyncio.fixture
async def clean_session(
    mysql_store: MySQLStore,
) -> AsyncIterator[AsyncSession]:
    """每个用例独立事务；用例事务提交并关闭后，统一清理本命名空间创建的用户。

    清理放在用例事务退出（commit+close）之后再开新会话执行，避免对未提交行
    的 DELETE 触发 InnoDB 行锁等待（Lock wait timeout）。
    """
    async with mysql_store.session() as session:
        yield session
    # --- teardown：用例事务已提交/关闭，新会话清理本命名空间用户 ---
    async with mysql_store.session() as s:
        await s.execute(delete(User).where(User.name.like(f"{_NAMESPACE}%")))
        await s.commit()


async def test_create_stores_hash_not_plaintext(clean_session) -> None:
    user, plain = await user_service.create(clean_session, _name("create"))
    assert plain.startswith("ap_")
    assert user.api_key != plain  # 库中列不是明文
    assert verify_api_key(plain, user.api_key, _app()) is True
    reloaded = await user_service.get(clean_session, user.id)
    assert reloaded.api_key == user.api_key
    assert reloaded.status == 1


async def test_get_nonexistent_raises_404(clean_session) -> None:
    with pytest.raises(NotFound):
        await user_service.get(clean_session, 999_999_999)


async def test_update_name(clean_session) -> None:
    user, _ = await user_service.create(clean_session, _name("pre"))
    updated = await user_service.update(clean_session, user.id, _name("post"))
    assert (await user_service.get(clean_session, user.id)).name == updated.name


async def test_deactivate_sets_status_zero(clean_session) -> None:
    user, _ = await user_service.create(clean_session, _name("deact"))
    deactivated = await user_service.deactivate(clean_session, user.id)
    assert deactivated.status == 0
    assert (await user_service.get(clean_session, user.id)).status == 0


async def test_rotate_api_key_invalidates_old_and_works_new(clean_session) -> None:
    user, plain1 = await user_service.create(clean_session, _name("rotate"))
    _u2, plain2 = await user_service.rotate_key(clean_session, user.id)
    assert plain1 != plain2
    assert _u2.api_key != plain2  # 库中仍是哈希
    assert verify_api_key(plain1, _u2.api_key, _app()) is False  # 旧明文失效
    assert verify_api_key(plain2, _u2.api_key, _app()) is True  # 新明文可用


# ---------------------------------------------------------------------------
# 端到端：完整应用（AuthMiddleware → 路由 → service → MySQL）
# ---------------------------------------------------------------------------

def test_user_endpoints_end_to_end(mysql_store) -> None:
    """全栈 HTTP 验证 user 路由。

    覆盖：无 token→401；创建→201（明文出现一次）；查询不回明文；
    轮换→200 新明文；停用→status=0。
    """
    actor = None
    try:
        # 先用 service 落一个真实 actor（真实 id），再为它签发 JWT 走 HTTP 全链路
        actor = _bootstrap_actor(mysql_store)
        auth = {"Authorization": f"Bearer {create_access_token(actor, get_settings().app)}"}

        with TestClient(api_app) as client:
            # 无 token → 401
            resp = client.post("/v1/user", json={"name": _name("e2e")})
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "unauthenticated"

            # 创建 → 201，明文只此一次
            resp = client.post("/v1/user", json={"name": _name("e2e_alice")}, headers=auth)
            assert resp.status_code == 201
            body = resp.json()
            new_id = body["id"]
            assert body["api_key"].startswith("ap_")
            assert body.get("name").startswith(_NAMESPACE)

            # 查询不返回明文
            resp = client.get(f"/v1/user/{new_id}", headers=auth)
            assert resp.status_code == 200
            assert "api_key" not in resp.json()

            # 轮换返回新明文且可用
            resp = client.post(f"/v1/user/{new_id}/api-key/rotate", headers=auth)
            assert resp.status_code == 200
            assert resp.json()["api_key"].startswith("ap_")

            # 停用
            resp = client.post(f"/v1/user/{new_id}/deactivate", headers=auth)
            assert resp.status_code == 200
            assert resp.json()["status"] == 0
    finally:
        _cleanup_actor(mysql_store, actor)


def _bootstrap_actor(store) -> int:
    async def _go():
        async with store.session() as s:
            user, _ = await user_service.create(s, f"{_NAMESPACE}_actor")
            return user.id

    return asyncio.run(_go())


def _cleanup_actor(store, uid: int | None) -> None:
    if uid is None:
        return

    async def _go():
        async with store.session() as s:
            await s.execute(delete(User).where(User.id.in_([uid])))
        await s.close()

    asyncio.run(_go())