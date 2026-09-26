"""retrieve 节点（增量 3.4 落地）。

落地设计文档 §4 的混合检索三段式：
1. 并发召回：Milvus 向量（按 kb_id 过滤）+ ES 关键词（各 top30）；
2. 融合去重（RRF + chunk_id 去重）；
3. Rerank（取 top-K，桩实现，耗时纳入埋点）；
4. 命中 Redis 检索缓存则直接返回（跳过 1–3）。

依赖注入：容器（redis/milvus/es）与全部设置经 `config["configurable"]["deps"]`
由编排装配层传入（节点不持有全局状态；见模块头注释）。

注意：本模块**不使用** `from __future__ import annotations`——langgraph 1.2 靠运行时
注解识别节点的 `config` 形参（字符串注解识别不到，会导致 deps 注入失效，实测 config=None）。
"""

import asyncio
import time
from typing import Any

from langgraph.types import RunnableConfig

from core.logging import get_logger
from langgraph_service.graph.state import AgentState
from langgraph_service.metrics import bump
from langgraph_service.metrics import record as record_metric
from langgraph_service.retrieval import (
    embed_query,
    fuse_dedup,
    rerank_chunks,
    retrieval_cache_key,
)
from observability import otel
from observability.prom import metrics as prom_metrics

logger = get_logger(__name__)


def _cache_key(settings: Any, query: str, kb_id: str | None) -> str:
    base = retrieval_cache_key(query, kb_id)
    return f"{settings.redis.prefix_retrieval_cache}{base}"


async def retrieve_node(
    state: AgentState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """执行混合检索。返回 `retrieved` 切片引用列表（只带引用，正文由 generate 水合）。"""
    deps = (config or {}).get("configurable", {}).get("deps") or {}
    container = deps.get("container")
    settings = deps.get("settings")
    redis_store = getattr(container, "redis", None) if container else None
    milvus = getattr(container, "milvus", None) if container else None
    es = getattr(container, "es", None) if container else None

    query = state.get("query", "")
    kb_id = state.get("kb_id")
    start = time.perf_counter()

    with otel.span("node.retrieve", kb_id=str(kb_id or ""), query=query[:80]) as rt_span:
        # 1) 缓存命中 → 直接返回（跳过召回/融合/重排）
        cached = None
        if redis_store is not None:
            cached = await redis_store.get_json(_cache_key(settings, query, kb_id))
        if cached:
            bump("retrieval.cache_hit")
            prom_metrics.cache_hit()
            rt_span.attributes["cache"] = "hit"
            record_metric("retrieval.total_ms", (time.perf_counter() - start) * 1000)
            prom_metrics.observe_retrieval(time.perf_counter() - start)
            logger.info("retrieval cache hit", extra={"extra_fields": {"query": query}})
            return {"retrieved": cached}

        bump("retrieval.cache_miss")
        prom_metrics.cache_miss()
        rt_span.attributes["cache"] = "miss"

        # 2) 并发召回：向量 + 关键词
        vector_hits: list[dict[str, Any]] = []
        keyword_hits: list[dict[str, Any]] = []

        async def _vector() -> None:
            nonlocal vector_hits
            if milvus is None or not kb_id:
                return
            with rt_span.child("dependency.milvus.search"):
                vec = await embed_query(
                    query, settings.retrieval.embed_dim, settings.retrieval.embed_provider
                )
                rows = await milvus.search(
                    vec,
                    kb_id=kb_id,
                    top_k=settings.retrieval.vector_top_k,
                    output_fields=["chunk_id", "doc_id", "kb_id"],
                )
            vector_hits = [
                {
                    "chunk_id": r.get("chunk_id") or r.get("id"),
                    "doc_id": r.get("doc_id"),
                    "kb_id": r.get("kb_id"),
                    # pymilvus 返回 `distance`；现贴 score 以统一融合层入参
                    "score": float(r.get("distance") or r.get("score") or 0.0),
                }
                for r in rows
                if (r.get("chunk_id") or r.get("id"))
            ]

        async def _keyword() -> None:
            nonlocal keyword_hits
            if es is None or not kb_id:
                return
            with rt_span.child("dependency.es"):
                keyword_hits = await es.keyword_search(
                    query, kb_id=kb_id, top_k=settings.retrieval.keyword_top_k
                )

        await asyncio.gather(_vector(), _keyword())

        # 3) 融合去重 + 重排（top-K）
        with rt_span.child("rerank"):
            fused = await fuse_dedup(
                vector_hits, keyword_hits, top_k=settings.retrieval.keyword_top_k
            )
            reranked = await rerank_chunks(
                query, fused, settings.retrieval.rerank_top_k, settings.retrieval.rerank_provider
            )

        record_metric("retrieval.total_ms", (time.perf_counter() - start) * 1000)
        prom_metrics.observe_retrieval(time.perf_counter() - start)

        # 4) 写缓存（桩环境也写，保证同 query 二访命中——单测据此断言「缓存命中跳过检索」）
        if redis_store is not None:
            await redis_store.set_json(
                _cache_key(settings, query, kb_id),
                reranked,
                ttl_seconds=settings.retrieval.cache_ttl_seconds,
            )

        return {"retrieved": reranked}


__all__ = ["retrieve_node"]