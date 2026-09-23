"""Milvus 客户端：向量召回。

职责边界（设计文档 §2.1）：只存向量 + 主键 + 标量过滤字段，**绝不存超长原文**（原文在 ES）。

注意：pymilvus 为同步 SDK，所有调用经 `asyncio.to_thread` 卸载到线程池，
避免阻塞事件循环（FastAPI / LangGraph 都是 async 上下文）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.config import MilvusSettings
from core.exceptions import StorageUnavailable
from core.logging import get_logger
from core.storage.base import BaseStore

logger = get_logger(__name__)


class MilvusStore(BaseStore):
    name = "milvus"

    def __init__(self, settings: MilvusSettings) -> None:
        super().__init__()
        self._settings = settings
        self._client: Any | None = None

    # ---------- 生命周期 ----------

    async def connect(self) -> None:
        if self._connected:
            return
        from pymilvus import MilvusClient

        s = self._settings
        token = f"{s.user}:{s.password}" if s.user else None

        def _build() -> Any:
            return MilvusClient(uri=s.uri, token=token, timeout=s.timeout)

        self._client = await asyncio.to_thread(_build)
        self._connected = True
        logger.info("milvus store connected", extra={"extra_fields": {"uri": s.uri}})

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
        self._client = None
        self._connected = False
        logger.info("milvus store closed")

    # ---------- 探活 ----------

    async def _probe(self) -> tuple[str, dict[str, Any]]:
        client = self.client
        collections = await asyncio.to_thread(client.list_collections)
        target = self._settings.collection
        present = target in collections
        detail = (
            f"collections={len(collections)}, target='{target}' "
            f"{'present' if present else 'MISSING'}"
        )
        return detail, {"collection_count": len(collections), "target_present": present}

    # ---------- 使用入口 ----------

    @property
    def client(self) -> Any:
        if self._client is None:
            raise StorageUnavailable("milvus 尚未连接，请先调用 connect()")
        return self._client

    # ---------- 检索（供 langgraph 节点调用）----------

    async def search(
        self,
        query_vector: list[float],
        *,
        kb_id: str,
        top_k: int = 30,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按 kb_id 分区检索。

        kb_id 只作**检索提速**（partition），不作安全边界——权限在查询路径强制校验。
        """
        client = self.client
        collection = self._settings.collection
        fields = output_fields or ["chunk_id", "doc_id", "kb_id", "content_len"]

        def _run() -> list[list[dict[str, Any]]]:
            return client.search(
                collection_name=collection,
                data=[query_vector],
                limit=top_k,
                filter=f'kb_id == "{kb_id}"',
                output_fields=fields,
                search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            )

        results = await asyncio.to_thread(_run)
        return results[0] if results else []
