"""pytest 共享夹具（增量 2 · 2.1）。

- `auth_settings`: 测试用 AppSettings（强 JWT/API-Key 密钥，避免 InsecureKeyLength 告警）。
- `mysql_store`: 连接本机 MySQL（`agent_platform` 库，compose 已拉起），供 user CRUD
  集成用例使用；库不可达时 `pytest.skip`，保证离线跑纯单测仍可通过。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from core.config import AppSettings, MySQLSettings, Settings
from core.storage import MySQLStore
from tests._constants import TEST_API_KEY_SECRET, TEST_JWT_SECRET


@pytest.fixture
def auth_settings() -> AppSettings:
    """独立、强密钥的 App 配置；`exp` 可覆写以构造过期 token。"""
    return AppSettings(jwt_secret=TEST_JWT_SECRET, api_key_secret=TEST_API_KEY_SECRET)


@pytest_asyncio.fixture
async def mysql_store() -> AsyncIterator[MySQLStore]:
    """连本机 MySQL（agent_platform 库）。连不上则跳过依赖它的用例。"""
    settings = Settings(mysql=MySQLSettings())
    store = MySQLStore(settings.mysql)
    try:
        await store.connect()
    except Exception:
        pytest.skip("MySQL 未运行或不可达，跳过 user CRUD 集成测试")
        return
    try:
        yield store
    finally:
        await store.close()