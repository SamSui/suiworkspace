"""独立检索服务 HTTP 客户端（编排进程侧调用）。

对 `POST /v1/retrieval` / `POST /v1/embed` 做类型化封装，返回与进程内 `hybrid_search`
一致的结构（`list[RetrievedChunk]`），便于 `nodes/retrieve` 无差别地把两路径结果
落缓存 / 推进生成。

错误映射：存储不可用(503) → `StorageUnavailable`；其余非 2xx → `UpstreamError`。
不缓存连接：单个短命 client 由编排装配层创建；不配置服务 URL 则不实例化。
"""

from __future__ import annotations

from typing import Any

import httpx

from core.exceptions import StorageUnavailable, UpstreamError
from core.retrieval import RetrievedChunk

_TIMEOUT = 15.0


class RetrievalClient:
    """面向独立检索服务 `retrieval_service` 的异步客户端。"""

    def __init__(self, base_url: str, *, timeout: float = _TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def retrieve(
        self,
        *,
        query: str,
        kb_id: str | None,
        top_k: int = 10,
        tenant_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """调用 `POST /v1/retrieval`，返回 uniformly 结构化 TopN 命中（含 text）。"""
        body: dict[str, Any] = {"query": query, "topk": int(top_k)}
        if kb_id is not None:
            body["kb_id"] = kb_id
        else:
            # 契约要求 kb_id 必填：无 kb 视为无检索依据，返回空列表（不请求上游）
            return []
        if tenant_id is not None:
            body["tenant_id"] = tenant_id

        async with httpx.AsyncClient(timeout=self._timeout) as _c:
            resp = await _c.post(f"{self._base_url}/v1/retrieval", json=body)

        if resp.status_code == 503:
            raise StorageUnavailable(f"检索服务不可用: {resp.text}")
        if resp.status_code != 200:
            raise UpstreamError(f"检索服务返回 {resp.status_code}: {resp.text}")

        payload = resp.json()
        hits: list[RetrievedChunk] = []
        for h in payload.get("hits", []):
            hits.append(
                {
                    "chunk_id": h["chunk_id"],
                    "doc_id": h.get("doc_id", ""),
                    "kb_id": h.get("kb_id", ""),
                    "score": float(h.get("score", 0.0)),
                    "source": h.get("source", "fused"),
                    "highlight": h.get("highlight"),
                }
            )
        return hits

    async def embed(self, query: str) -> list[float]:
        """调用 `POST /v1/embed`，返回对应 query 的向量。"""
        async with httpx.AsyncClient(timeout=self._timeout) as _c:
            resp = await _c.post(f"{self._base_url}/v1/embed", json={"query": query})
        if resp.status_code != 200:
            raise UpstreamError(f"embed 服务返回 {resp.status_code}: {resp.text}")
        return list(resp.json()["vector"])


__all__ = ["RetrievalClient"]