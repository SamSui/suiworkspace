"""存储容器：按配置装配四类客户端，统一启停与探活。

两个进程组都从同一容器取依赖，保证「同一套连接配置、同一套探活语义」：

    container = StorageContainer(get_settings())
    await container.startup()      # 应用 lifespan 内调用
    ...
    await container.shutdown()
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.config import Settings
from core.logging import get_logger
from core.storage.base import BaseStore, HealthResult
from core.storage.es import ESStore
from core.storage.milvus import MilvusStore
from core.storage.mysql import MySQLStore
from core.storage.redis import RedisStore

logger = get_logger(__name__)


class StorageContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mysql = MySQLStore(settings.mysql)
        self.redis = RedisStore(settings.redis)
        self.milvus = MilvusStore(settings.milvus)
        self.es = ESStore(settings.es)

    def all(self) -> list[BaseStore]:
        return [self.mysql, self.redis, self.milvus, self.es]

    # ---------- 生命周期 ----------

    async def startup(self, *, bootstrap: bool = True, fail_fast: bool = False) -> None:
        """并发连接全部存储。

        `fail_fast=False`（默认）：单点连接失败只记警告，服务照常起来——便于开发时
        按需只拉部分容器；真正是否可服务由 /healthz 暴露。
        `fail_fast=True`：任一存储连不上直接抛错，适合生产就绪探针。
        """
        stores = self.all()
        results = await asyncio.gather(
            *(self._connect_one(store) for store in stores), return_exceptions=True
        )

        failures: list[tuple[str, BaseException]] = []
        for store, result in zip(stores, results, strict=True):
            if isinstance(result, BaseException):
                failures.append((store.name, result))
                logger.warning(
                    "storage connect failed",
                    extra={"extra_fields": {"store": store.name, "error": str(result)}},
                )

        if failures and fail_fast:
            detail = "; ".join(f"{name}: {err}" for name, err in failures)
            from core.exceptions import StorageUnavailable

            raise StorageUnavailable(f"存储连接失败: {detail}")

        if bootstrap:
            await self.bootstrap_schema()

    async def _connect_one(self, store: BaseStore) -> None:
        await store.connect()

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self._close_one(store) for store in self.all()), return_exceptions=True
        )

    async def _close_one(self, store: BaseStore) -> None:
        try:
            await store.close()
        except Exception as exc:  # noqa: BLE001 — 关闭阶段不应再抛
            logger.warning(
                "storage close failed",
                extra={"extra_fields": {"store": store.name, "error": str(exc)}},
            )

    # ---------- Schema 引导 ----------

    async def bootstrap_schema(self) -> None:
        """幂等地把 ES 索引与 Milvus collection 建好。

        开发栈首次 `docker compose up` 后即可直接跑，无需手工建索引。
        任一环节失败只告警，不阻断启动（由 /healthz 暴露真实状态）。
        """
        from db.mappings_es import build_index_body

        if self.es.connected:
            try:
                created = await self.es.ensure_index(build_index_body(self.settings.es.index))
                if created:
                    logger.info("es index bootstrapped")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "es bootstrap failed", extra={"extra_fields": {"error": str(exc)}}
                )

        if self.milvus.connected:
            try:
                await asyncio.to_thread(self._bootstrap_milvus)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "milvus bootstrap failed", extra={"extra_fields": {"error": str(exc)}}
                )

    def _bootstrap_milvus(self) -> None:
        from pymilvus import DataType

        from db.schemas_milvus import build_collection_schema, build_index_params

        client = self.milvus.client
        collection = self.settings.milvus.collection
        if client.has_collection(collection):
            return

        schema = build_collection_schema(DataType, dim=self.settings.milvus.dim)
        index_params = client.prepare_index_params()
        build_index_params(index_params)
        client.create_collection(
            collection_name=collection,
            schema=schema,
            index_params=index_params,
        )
        logger.info("milvus collection bootstrapped")

    # ---------- 健康 ----------

    async def health(self) -> tuple[bool, list[HealthResult], dict[str, Any]]:
        from core.storage.health import check_all

        return await check_all(self)
