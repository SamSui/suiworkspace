"""Elasticsearch 客户端：原文切片存储 + 关键词召回。

职责边界（设计文档 §2.1）：存 chunk 原文与过滤字段；向量只在 Milvus。
"""

from __future__ import annotations

from typing import Any

from elasticsearch import AsyncElasticsearch

from core.config import ESSettings
from core.exceptions import StorageUnavailable
from core.logging import get_logger
from core.storage.base import BaseStore

logger = get_logger(__name__)


class ESStore(BaseStore):
    name = "elasticsearch"

    def __init__(self, settings: ESSettings) -> None:
        super().__init__()
        self._settings = settings
        self._client: AsyncElasticsearch | None = None

    # ---------- 生命周期 ----------

    async def connect(self) -> None:
        if self._connected:
            return
        s = self._settings
        self._client = AsyncElasticsearch(
            hosts=s.host_list,
            request_timeout=s.request_timeout,
            max_retries=s.max_retries,
            retry_on_timeout=True,
        )
        self._connected = True
        logger.info("es store connected", extra={"extra_fields": {"hosts": s.host_list}})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
        self._client = None
        self._connected = False
        logger.info("es store closed")

    # ---------- 探活 ----------

    async def _probe(self) -> tuple[str, dict[str, Any]]:
        client = self.client
        info = await client.info()
        health = await client.cluster.health()
        status = health.get("status")
        detail = (
            f"cluster={info.get('cluster_name')}, status={status}, "
            f"version={info.get('version', {}).get('number')}"
        )
        return detail, {
            "cluster_status": status,
            "version": info.get("version", {}).get("number"),
        }

    # ---------- 使用入口 ----------

    @property
    def client(self) -> AsyncElasticsearch:
        if self._client is None:
            raise StorageUnavailable("es 尚未连接，请先调用 connect()")
        return self._client

    # ---------- 索引管理 ----------

    async def ensure_index(self, mapping: dict[str, Any]) -> bool:
        """幂等建索引。已存在返回 False。"""
        client = self.client
        index = self._settings.index
        if await client.indices.exists(index=index):
            return False
        await client.indices.create(index=index, **mapping)
        logger.info("es index created", extra={"extra_fields": {"index": index}})
        return True

    # ---------- 检索（供 langgraph 节点调用）----------

    async def keyword_search(
        self,
        query: str,
        *,
        kb_id: str,
        top_k: int = 30,
        highlight: bool = True,
    ) -> list[dict[str, Any]]:
        """BM25 关键词召回。kb_id 作 term 过滤（检索优化，非安全边界）。"""
        client = self.client
        body: dict[str, Any] = {
            "query": {
                "bool": {
                    "must": [{"match": {"text": {"query": query}}}],
                    "filter": [{"term": {"kb_id": kb_id}}],
                }
            },
            "size": top_k,
        }
        if highlight:
            body["highlight"] = {
                "fields": {"text": {"fragment_size": 120, "number_of_fragments": 1}}
            }

        resp = await client.search(index=self._settings.index, **body)
        hits: list[dict[str, Any]] = []
        for hit in resp["hits"]["hits"]:
            source = hit.get("_source", {})
            hits.append(
                {
                    "chunk_id": hit["_id"],
                    "score": hit["_score"],
                    "doc_id": source.get("doc_id"),
                    "kb_id": source.get("kb_id"),
                    "title": source.get("title"),
                    "text": source.get("text", ""),
                    "highlight": (hit.get("highlight", {}).get("text") or [None])[0],
                }
            )
        return hits

    async def fetch_chunks(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """按 chunk_id（ES `_id`）批量取正文。generate 组装 prompt 时水合切片用。

        文本不进编排状态（设计：state 只留引用），需要原文时按引用回查本方法。
        """
        client = self.client
        if not chunk_ids:
            return []
        body: dict[str, Any] = {
            "query": {"ids": {"values": chunk_ids}},
            "size": len(chunk_ids),
            "_source": ["doc_id", "kb_id", "text", "title"],
        }
        resp = await client.search(index=self._settings.index, **body)
        hits: list[dict[str, Any]] = []
        for hit in resp["hits"]["hits"]:
            source = hit.get("_source", {})
            hits.append(
                {
                    "chunk_id": hit["_id"],
                    "doc_id": source.get("doc_id"),
                    "kb_id": source.get("kb_id"),
                    "title": source.get("title"),
                    "text": source.get("text", ""),
                }
            )
        return hits
