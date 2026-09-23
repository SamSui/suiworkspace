"""MySQL 客户端：SQLAlchemy 2.x 异步引擎 + sessionmaker。

职责边界（设计文档 §2.1）：只承载业务元数据，不存大文本与向量。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import MySQLSettings
from core.exceptions import StorageUnavailable
from core.logging import get_logger
from core.storage.base import BaseStore

logger = get_logger(__name__)


class MySQLStore(BaseStore):
    name = "mysql"

    def __init__(self, settings: MySQLSettings) -> None:
        super().__init__()
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    # ---------- 生命周期 ----------

    async def connect(self) -> None:
        if self._connected:
            return
        s = self._settings
        self._engine = create_async_engine(
            s.dsn,
            echo=s.echo,
            pool_size=s.pool_size,
            max_overflow=s.max_overflow,
            pool_recycle=s.pool_recycle,
            pool_timeout=s.pool_timeout,
            pool_pre_ping=True,  # 断连自愈，避免拿到已被 MySQL 关掉的连接
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )
        self._connected = True
        logger.info("mysql store connected", extra={"extra_fields": {"host": s.host, "db": s.database}})

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._sessionmaker = None
        self._connected = False
        logger.info("mysql store closed")

    # ---------- 探活 ----------

    async def _probe(self) -> tuple[str, dict[str, Any]]:
        if self._engine is None:
            raise StorageUnavailable("mysql engine 未初始化")
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT VERSION()"))
            version = result.scalar_one()
        return f"SELECT 1 ok, server={version}", {"server_version": str(version)}

    # ---------- 使用入口 ----------

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise StorageUnavailable("mysql 尚未连接，请先调用 connect()")
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """事务边界由调用方掌握：成功自动 commit，异常自动 rollback。"""
        if self._sessionmaker is None:
            raise StorageUnavailable("mysql 尚未连接，请先调用 connect()")
        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def create_all(self, metadata: Any) -> None:
        """开发/测试便利方法：按 ORM 元数据建表。

        生产建表走 `db/sql/01_schema.sql` 或后续 Alembic 迁移，不用此方法。
        """
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        logger.info("mysql schema ensured via ORM metadata")
