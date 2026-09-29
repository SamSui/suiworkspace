"""retrieve 节点（增量 3.4 落地；P1.2 改调独立检索服务）。

P1.2 变化：混合检索主线已收敛到 `core.retrieval.hybrid_search`。编排进程优先经
独立检索服务（`retrieval_service`）走 HTTP 调用（可独立水平扩展）；当服务未配置或
不可达时，回落到进程内同一 `hybrid_search` 实现（语义一致，**不回归 SSE 契约**）。

沿用增量 3.4 三段式行为（缓存 → 并发召回 → 融合重排 → 写缓存），唯一差异是检索
执行体来自 `core.retrieval.hybrid_search` 而非本节点手写。

依赖注入：容器（redis/milvus/es）与设置经 `config["configurable"]["deps"]` 由编排
装配层传入；`deps["retrieval_client"]` 可选注入独立检索服务 HTTP 客户端（未配置则
回落进程内 hybrid_search，单测用假容器直接命中该回退路径）。

注意：本模块**不使用** `from __future__ import annotations`——langgraph 1.2 靠运行时
注解识别节点的 `config` 形参（字符串注解识别不到，会导致 deps 注入失效，实测 config=None）。
"""

import time
from typing import Any

from langgraph.types import RunnableConfig

from core.logging import get_logger
from core.retrieval import hybrid_search
from langgraph_service.graph.state import AgentState
from langgraph_service.metrics import bump
from langgraph_service.metrics import record as record_metric
from observability import otel
from observability.prom import metrics as prom_metrics

logger = get_logger(__name__)


def _cache_key(settings: Any, query: str, kb_id: str | None) -> str:
    from core.retrieval import retrieval_cache_key

    base = retrieval_cache_key(query, kb_id)
    return f"{settings.redis.prefix_retrieval_cache}{base}"


async def retrieve_node(
    state: AgentState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """执行混合检索。返回 `retrieved` 切片引用列表（只带引用，正文由 generate 水合）。"""
    deps = (config or {}).get("configurable", {}).get("deps") or {}
    container = deps.get("container")
    settings = deps.get("settings")
    # 独立检索服务客户端（可选注入）：配置了 RETRIEVAL_SERVICE_URL 时由编排注入。
    http_client = deps.get("retrieval_client")

    query = state.get("query", "")
    kb_id = state.get("kb_id")
    tenant_id = state.get("tenant_id")
    start = time.perf_counter()

    with otel.span("node.retrieve", kb_id=str(kb_id or ""), query=query[:80]) as rt_span:
        # 1) 缓存命中 → 直接返回（拼装/召回/重排全跳过）
        redis_store = getattr(container, "redis", None) if container else None
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

        # 2) 执行混合检索：优先独立服务（HTTP），否则进程内同一实现
        reranked = await _do_hybrid(
            http_client=http_client,
            container=container,
            settings=settings,
            query=query,
            kb_id=kb_id,
            tenant_id=tenant_id,
        )

        # 3) 写缓存（桩环境也写，保证同 query 二访命中——单测据此断言「缓存命中跳过检索」）
        if redis_store is not None:
            await redis_store.set_json(
                _cache_key(settings, query, kb_id),
                reranked,
                ttl_seconds=settings.retrieval.cache_ttl_seconds,
            )

        record_metric("retrieval.total_ms", (time.perf_counter() - start) * 1000)
        prom_metrics.observe_retrieval(time.perf_counter() - start)
        return {"retrieved": reranked}


async def _do_hybrid(
    *,
    http_client: Any,
    container: Any,
    settings: Any,
    query: str,
    kb_id: str | None,
    tenant_id: str | None,
) -> list[dict[str, Any]]:
    """执行检索主线；http_client 可用则走独立服务，否则进程内 hybrid_search。"""
    if http_client is not None:
        # 独立检索服务路径：HTTP 返回语义与 hybrid_search 一致的 RetrievedChunk 列表
        top_k = (settings.retrieval.rerank_top_k if settings else 5) or 5
        return await http_client.retrieve(query=query, kb_id=kb_id, top_k=top_k,
                                 tenant_id=tenant_id)

    # 进程内回退：与独立服务共用 core.retrieval.hybrid_search（语义一致）
    es = getattr(container, "es", None) if container else None
    milvus = getattr(container, "milvus", None) if container else None
    redis_store = getattr(container, "redis", None) if container else None
    cfg = settings.retrieval if settings is not None else None
    prefix = settings.redis.prefix_retrieval_cache if settings is not None else ""
    return await hybrid_search(
        es=es,
        milvus=milvus,
        redis_store=redis_store,
        cache_prefix=prefix,
        query=query,
        kb_id=kb_id,
        tenant_id=tenant_id,
        vector_top_k=cfg.vector_top_k if cfg else 30,
        keyword_top_k=cfg.keyword_top_k if cfg else 30,
        rerank_top_k=cfg.rerank_top_k if cfg else 5,
        embed_dim=cfg.embed_dim if cfg else 768,
        embed_provider=cfg.embed_provider if cfg else "echo",
        rerank_provider=cfg.rerank_provider if cfg else "echo",
        cache_ttl_seconds=cfg.cache_ttl_seconds if cfg else 300,
        vector_takes_tenant=True,
        keyword_takes_tenant=True,
    )


__all__ = ["retrieve_node"]