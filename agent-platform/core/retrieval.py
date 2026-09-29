"""混合检索共享流水线（P1.2 独立检索服务的业务核心）。

从 `langgraph_service/retrieval.py` 迁出并沉淀到 `core`：独立检索服务 / 编排进程
两组都能引用同一套纯检索逻辑，保证「独立服务返回的 TopN = 编排内嵌路径的 TopN」
（语义一致，见 SUIG-32 验收口径 #1）。

本模块**只依赖 core / 标准库，不 import langgraph_service**——独立服务进程因此可
单独 `uvicorn retrieval_service.app:app` 起进程、独立水平扩展，不与编排进程耦合。

对外提供（供 retrieval-service 的 `hybrid_search` 与编排节点复用）：
- `_stub_embed` / `embed_query` —— 确定性伪向量（同 query 同向量，缓存可命中）。
- `fuse_dedup` —— 多路召回 RRF 融合 + 按 chunk_id 去重。
- `rerank_chunks` —— 重排取 top_k（桩按 score 倒序），耗时记入埋点。
- `text_hydrate` —— 按 chunk_id 从 ES 回填正文（文本不进编排 state）。
- `retrieval_cache_key` —— kb 隔离 + query 稳定哈希的缓存键。
- `hybrid_search` —— 串联「embed → 双路召回 → 融合 → 重排 → 缓存」的完整主线。

错误语义：存储不可用收敛为 `StorageUnavailable`（服务层映射为 503），其余异常
收敛为 `UpstreamError`（500）。调用接口与编排节点解耦，便于单测注入假存储。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from core.logging import get_logger

logger = get_logger(__name__)

_RRF_K = 60


# ---------- 检索结果类型 ----------

class RetrievedChunk(TypedDict):
    """一条召回结果：只带引用 + 分数，正文按需从 ES 回填（文本不进编排 state）。"""

    chunk_id: str
    doc_id: str
    kb_id: str
    score: float
    source: Literal["vector", "keyword", "fused"]
    highlight: str | None


# ---------- Embedding（桩：确定性可复现；真实模型在 P0/P1.3 替换同契约） ----------

async def _stub_embed(query: str, dim: int) -> list[float]:
    """确定性伪向量：以 query 哈希播种，保证同 query 同向量（供检索缓存命中验证）。"""
    digest = hashlib.sha256(query.encode("utf-8")).digest()
    vec = [float((digest[i % len(digest)] / 255.0) * 2 - 1) for i in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


EmbedFn = Callable[[str, int], Any]


async def embed_query(query: str, dim: int, provider: str = "echo") -> list[float]:
    """按配置选嵌入；桩默认确定性，供检索缓存 key 复用。真实接入在 P4 联动替换。"""
    return await _stub_embed(query, dim)  # noqa: A501 — 桩实现占位，接口稳定


# ---------- 融合去重 ----------

async def fuse_dedup(
    vector_hits: list[dict[str, Any]],
    keyword_hits: list[dict[str, Any]],
    top_k: int = 30,
) -> list[RetrievedChunk]:
    """两路召回 → RRF 融合 + 按 chunk_id 去重，返回统一 chunk 结构（只带引用）。

    每路 hits 需含 `chunk_id` 与 `score`；向量路还提供 `doc_id` / `kb_id`。
    """
    acc: dict[str, dict[str, Any]] = {}

    def _add(hit: dict[str, Any], source: str, rank: int) -> None:
        cid = str(hit.get("chunk_id") or hit.get("id"))
        if not cid:
            return
        entry = acc.setdefault(
            cid,
            {
                "chunk_id": cid,
                "doc_id": str(hit.get("doc_id") or ""),
                "kb_id": str(hit.get("kb_id") or ""),
                "sum": 0.0,
                "best": float(hit.get("score") or 0.0),
                "sources": set(),
                "highlight": hit.get("highlight"),
            },
        )
        entry["sum"] += 1.0 / (_RRF_K + rank)
        entry["sources"].add(source)
        if float(hit.get("score") or 0.0) > entry["best"]:
            entry["best"] = float(hit.get("score") or 0.0)

    for rank, hit in enumerate(vector_hits, start=1):
        _add(hit, "vector", rank)
    for rank, hit in enumerate(keyword_hits, start=1):
        _add(hit, "keyword", rank)

    ordered = sorted(acc.values(), key=lambda e: (e["sum"], e["best"]), reverse=True)
    out: list[RetrievedChunk] = []
    for e in ordered[:top_k]:
        srcs = sorted(e["sources"])
        out.append(
            {
                "chunk_id": e["chunk_id"],
                "doc_id": e["doc_id"],
                "kb_id": e["kb_id"],
                "score": e["best"],
                "source": srcs[0] if len(srcs) == 1 else "fused",
                "highlight": e["highlight"],
            }
        )
    return out


# ---------- 重排 ----------

RerankFn = Callable[[str, list[RetrievedChunk], int], Any]

async def rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int,
    provider: str = "echo",
) -> list[RetrievedChunk]:
    """重排取 top_k（桩按现有 score 倒序；真实接 BGE-Reranker 时替换 `_rerank_impl`）。

    注：独立服务 / 编排均经本函数重排，耗时埋点设在调用侧（进程内 metrics / 观测）。
    """
    return await _rerank_impl(provider, query, chunks, top_k)


async def _rerank_impl(
    provider: str, query: str, chunks: list[RetrievedChunk], top_k: int
) -> list[RetrievedChunk]:
    ordered = sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)
    return ordered[:top_k]


# ---------- 正文水合（generate 用；文本不进 state） ----------

async def text_hydrate(
    es: Any,
    chunks: list[RetrievedChunk],
) -> dict[str, tuple[str, str]]:
    """按 chunk_id 从 ES 取 (text, highlight)。取不到则回落 highlight / 空串。"""
    ids = [c["chunk_id"] for c in chunks]
    if not ids or es is None:
        return {}
    try:
        rows = await es.fetch_chunks(ids)
    except Exception as exc:  # noqa: BLE001 — 水合失败不阻断生成
        logger.warning("chunk hydrate failed", extra={"extra_fields": {"error": str(exc)}})
        rows = []
    by_id = {r["chunk_id"]: r for r in rows}
    out: dict[str, tuple[str, str]] = {}
    for c in chunks:
        r = by_id.get(c["chunk_id"])
        text = (r.get("text") if r else None) or c.get("highlight") or ""
        out[c["chunk_id"]] = (text, str(c.get("highlight") or ""))
    return out


# ---------- 检索缓存键 ----------

def retrieval_cache_key(query: str, kb_id: str | None) -> str:
    """缓存键：kb 隔离 + query 稳定哈希（桩向量确定性 → 同 query 同键，可命中）。"""
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:24]
    return f"{kb_id or '_global'}:{h}"


# ---------- 完整混合检索（独立服务 / 编排进程共用同一实现） ----------

async def hybrid_search(
    *,
    es: Any,
    milvus: Any,
    redis_store: Any,
    query: str,
    kb_id: str | None,
    tenant_id: str | None = None,
    cache_prefix: str = "",
    vector_top_k: int = 30,
    keyword_top_k: int = 30,
    rerank_top_k: int = 5,
    embed_dim: int = 768,
    embed_provider: str = "echo",
    rerank_provider: str = "echo",
    cache_ttl_seconds: int = 300,
    vector_takes_tenant: bool = False,
    keyword_takes_tenant: bool = False,
) -> list[RetrievedChunk]:
    """执行完整混合检索：缓存命中直接返回，否则并发召回→融合→重排→写缓存。

    参数全量注入（不依赖任何全局状态），方便单测用假存储与可控参量：
    `tenant_id` 非空且存储支持标量过滤时并入过滤条件（隔离生效）。
    """
    import asyncio

    # 1) 缓存命中 → 直接返回（跳过召回/融合/重排）
    base_key = retrieval_cache_key(query, kb_id)
    cache_key = f"{cache_prefix}{base_key}"
    if redis_store is not None:
        cached = await redis_store.get_json(cache_key)
        if cached:
            return list(cached)

    vector_hits: list[dict[str, Any]] = []
    keyword_hits: list[dict[str, Any]] = []

    async def _vector() -> None:
        nonlocal vector_hits
        if milvus is None or not kb_id:
            return
        vec = await embed_query(query, embed_dim, embed_provider)
        kw: dict[str, Any] = {"kb_id": kb_id, "top_k": vector_top_k,
                             "output_fields": ["chunk_id", "doc_id", "kb_id"]}
        if vector_takes_tenant and tenant_id:
            kw["tenant_id"] = tenant_id
        rows = await milvus.search(vec, **kw)
        vector_hits = [
            {
                "chunk_id": r.get("chunk_id") or r.get("id"),
                "doc_id": r.get("doc_id"),
                "kb_id": r.get("kb_id"),
                "score": float(r.get("distance") or r.get("score") or 0.0),
            }
            for r in rows
            if (r.get("chunk_id") or r.get("id"))
        ]

    async def _keyword() -> None:
        nonlocal keyword_hits
        if es is None or not kb_id:
            return
        kw: dict[str, Any] = {"kb_id": kb_id, "top_k": keyword_top_k}
        if keyword_takes_tenant and tenant_id:
            kw["tenant_id"] = tenant_id
        keyword_hits = await es.keyword_search(query, **kw)

    await asyncio.gather(_vector(), _keyword())

    fused = await fuse_dedup(vector_hits, keyword_hits, top_k=keyword_top_k + vector_top_k)
    reranked = await rerank_chunks(query, fused, rerank_top_k, rerank_provider)

    if redis_store is not None:
        await redis_store.set_json(cache_key, reranked, ttl_seconds=cache_ttl_seconds)

    return reranked


__all__ = [
    "RetrievedChunk",
    "embed_query",
    "rerank_chunks",
    "fuse_dedup",
    "text_hydrate",
    "retrieval_cache_key",
    "hybrid_search",
]